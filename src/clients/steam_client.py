from __future__ import annotations

import asyncio
from email.utils import parsedate_to_datetime
import hashlib
import html
import json
import logging
import re
from datetime import datetime, timezone
from typing import Protocol
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse, urlunparse
import xml.etree.ElementTree as ET

import aiohttp

from ..core.config import AppConfig
from ..core.models import NewsPost


LOGGER = logging.getLogger(__name__)
STEAM_NEWS_BASE_URL = "https://store.steampowered.com/news/app"
STEAM_NEWS_FEED_BASE_URL = "https://store.steampowered.com/feeds/news/app"
STEAM_REQUEST_TIMEOUT_SECONDS = 20
STEAM_EVENT_MAX_ATTEMPTS = 3
STEAM_EVENT_RETRY_BACKOFF_SECONDS = 2.0
STEAM_CLAN_IMAGE_BASE_URL = "https://clan.fastly.steamstatic.com/images"
STEAM_LANGUAGE_INDEXES = {
    "english": 0,
    "german": 1,
    "french": 2,
    "italian": 3,
    "koreana": 4,
    "spanish": 5,
    "schinese": 6,
    "tchinese": 7,
    "russian": 8,
    "thai": 9,
    "japanese": 10,
    "portuguese": 11,
    "polish": 12,
    "danish": 13,
    "dutch": 14,
    "finnish": 15,
    "norwegian": 16,
    "swedish": 17,
    "hungarian": 18,
    "czech": 19,
    "romanian": 20,
    "turkish": 21,
    "arabic": 25,
    "brazilian": 26,
    "bulgarian": 27,
    "greek": 28,
    "ukrainian": 29,
    "latam": 30,
    "vietnamese": 31,
}
ACCEPT_LANGUAGE_HEADERS = {
    "koreana": "ko-KR,ko;q=0.9,en;q=0.7",
    "english": "en-US,en;q=0.9",
    "japanese": "ja-JP,ja;q=0.9,en;q=0.7",
}


class NewsSource(Protocol):
    async def fetch_recent_posts(
        self, language: str | None = None, limit: int | None = None
    ) -> list[NewsPost]:
        ...


class SteamNewsSource:
    def __init__(self, config: AppConfig, session: aiohttp.ClientSession) -> None:
        self.config = config
        self.session = session

    async def fetch_recent_posts(
        self, language: str | None = None, limit: int | None = None
    ) -> list[NewsPost]:
        language = language or self.config.steam_language
        limit = limit or self.config.max_posts_per_poll
        try:
            posts = await self._fetch_event_posts_with_retry(language)
        except (
            aiohttp.ClientError,
            asyncio.TimeoutError,
            TimeoutError,
            RuntimeError,
        ) as exc:
            posts = await self._fetch_rss_posts(language, limit)
            if posts:
                LOGGER.info(
                    "Steam event data unavailable; using RSS fallback "
                    "(language=%s, reason=%s).",
                    language,
                    _short_exception(exc),
                )
            else:
                raise RuntimeError("Steam RSS feed returned no usable posts.") from exc
        posts = _dedupe_posts_by_id(posts)
        return _sort_newest_first(posts)[:limit]

    async def _fetch_event_posts_with_retry(self, language: str) -> list[NewsPost]:
        last_error: Exception | None = None
        for attempt in range(1, STEAM_EVENT_MAX_ATTEMPTS + 1):
            try:
                posts = await self._fetch_event_posts(language)
            except (
                aiohttp.ClientError,
                asyncio.TimeoutError,
                TimeoutError,
                RuntimeError,
            ) as exc:
                last_error = exc
                LOGGER.debug(
                    "Steam event data request failed (attempt %s/%s, language=%s): %s",
                    attempt,
                    STEAM_EVENT_MAX_ATTEMPTS,
                    language,
                    _short_exception(exc),
                )
            else:
                if posts:
                    return posts
                last_error = RuntimeError("Steam 이벤트 데이터가 비어 있습니다.")
                LOGGER.debug(
                    "Steam event data was empty (attempt %s/%s, language=%s).",
                    attempt,
                    STEAM_EVENT_MAX_ATTEMPTS,
                    language,
                )

            if attempt < STEAM_EVENT_MAX_ATTEMPTS:
                await asyncio.sleep(STEAM_EVENT_RETRY_BACKOFF_SECONDS * attempt)

        assert last_error is not None
        raise last_error

    async def _fetch_event_posts(self, language: str) -> list[NewsPost]:
        url = self._app_news_url(language)
        headers = {
            "Accept": "text/html,application/xhtml+xml;q=0.9",
            "Accept-Language": _accept_language_header(language),
            "User-Agent": "Limpi Discord Bot (Steam news poller)",
        }
        async with self.session.get(
            url,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=STEAM_REQUEST_TIMEOUT_SECONDS),
        ) as response:
            if response.status >= 400:
                body = await response.text()
                _raise_response_error(response, body)
            body = await response.text()

        data = _extract_initial_events(body)
        if not data:
            return []

        posts: list[NewsPost] = []
        for event in data.get("events", []):
            if not isinstance(event, dict):
                continue
            if int(event.get("published") or 0) != 1 or int(event.get("hidden") or 0) != 0:
                continue
            post = self._event_to_post(event, language)
            if post is not None:
                posts.append(post)

        return posts

    async def _fetch_rss_posts(self, language: str, limit: int) -> list[NewsPost]:
        url = self._rss_feed_url(language)
        headers = {
            "Accept": "application/rss+xml,application/xml;q=0.9,text/xml;q=0.8",
            "Accept-Language": _accept_language_header(language),
            "User-Agent": "Limpi Discord Bot (Steam news RSS fallback)",
        }
        async with self.session.get(
            url,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=STEAM_REQUEST_TIMEOUT_SECONDS),
        ) as response:
            if response.status >= 400:
                body = await response.text()
                _raise_response_error(response, body)
            body = await response.text()

        try:
            root = ET.fromstring(body)
        except ET.ParseError as exc:
            raise RuntimeError(f"Steam RSS feed parse failed: {exc}") from exc

        channel = root.find("channel")
        if channel is None:
            return []

        posts: list[NewsPost] = []
        for item in channel.findall("item"):
            post = self._rss_item_to_post(item, language, url)
            if post is None:
                continue
            posts.append(post)
            if len(posts) >= limit:
                break
        return posts

    def _rss_item_to_post(
        self,
        item: ET.Element,
        language: str,
        source_url: str,
    ) -> NewsPost | None:
        title = _xml_child_text(item, "title")
        description = _xml_child_text(item, "description")
        url = _xml_child_text(item, "link") or _xml_child_text(item, "guid")
        guid = _xml_child_text(item, "guid")
        if not url:
            return None

        post_key = _steam_news_post_id_from_url(url) or _steam_news_post_id_from_url(guid)
        if post_key is None:
            post_key = _stable_post_key(url or guid or title)

        image_urls = _dedupe_urls([
            *_extract_html_image_urls(description),
            *_rss_enclosure_urls(item),
        ])
        youtube_urls = _extract_youtube_urls(description)
        text = format_steam_news_for_discord(description)
        if not text:
            text = title or url

        raw = {
            "source": "steam_rss",
            "source_url": source_url,
            "language": language,
            "guid": guid,
            "link": url,
            "youtube_urls": youtube_urls,
            "thumbnail_url": image_urls[0] if image_urls else None,
        }
        return NewsPost(
            post_id=_language_post_id(language, post_key),
            source_user="Limbus Company Steam News",
            url=url,
            text=text,
            title=_title_from_text(title, fallback=text or post_key),
            created_at=_datetime_from_rfc2822(_xml_child_text(item, "pubDate")),
            image_urls=image_urls,
            raw=raw,
        )

    def _event_to_post(self, event: dict, language: str) -> NewsPost | None:
        gid = str(event.get("gid") or "")
        if not gid:
            return None

        announcement = event.get("announcement_body") or {}
        if not isinstance(announcement, dict):
            announcement = {}

        json_data = _parse_json_object(event.get("jsondata"))
        title = str(
            announcement.get("headline")
            or event.get("event_name")
            or f"Steam event {gid}"
        ).strip()
        body = str(announcement.get("body") or "").strip()
        clan_id = str(announcement.get("clanid") or "")
        clan_steam_id = str(event.get("clan_steamid") or "")
        post_url = self._event_url(gid, clan_steam_id, language)
        youtube_urls = _extract_youtube_urls(body)
        image_urls = _extract_steam_bbcode_image_urls(body, clan_id)
        thumbnail_url = _localized_event_image_url(
            json_data,
            clan_id,
            language,
            "localized_capsule_image",
        )
        is_video_only = bool(youtube_urls) and not image_urls
        image_urls = _dedupe_urls(image_urls)

        text = format_steam_news_for_discord(body)
        if not text:
            text = title

        raw = {
            "source": "steam_initial_events",
            "source_url": self._app_news_url(language),
            "language": language,
            "event_gid": gid,
            "event_type": event.get("event_type"),
            "starts_at": event.get("rtime32_start_time"),
            "ends_at": event.get("rtime32_end_time"),
            "youtube_urls": youtube_urls,
            "is_video_only": is_video_only,
            "thumbnail_url": thumbnail_url,
        }
        return NewsPost(
            post_id=_language_post_id(language, gid),
            source_user="Limbus Company Steam News",
            url=post_url,
            text=text,
            title=_title_from_text(title, fallback=text or gid),
            created_at=_datetime_from_unix(
                announcement.get("posttime") or event.get("rtime32_start_time")
            ),
            image_urls=image_urls,
            raw=raw,
        )

    def _app_news_url(self, language: str | None = None) -> str:
        language = language or self.config.steam_language
        if self.config.steam_news_url:
            return _url_with_query_values(
                self.config.steam_news_url,
                {"l": language, "cc": self.config.steam_country},
            )
        return (
            f"{STEAM_NEWS_BASE_URL}/{self.config.steam_app_id}/?"
            f"{urlencode({'l': language, 'cc': self.config.steam_country})}"
        )

    def _rss_feed_url(self, language: str) -> str:
        query = {"cc": self.config.steam_country, "l": language}
        return f"{STEAM_NEWS_FEED_BASE_URL}/{self.config.steam_app_id}/?{urlencode(query)}"

    def _event_url(self, gid: str, clan_steam_id: str, language: str) -> str:
        query = {"l": language}
        if clan_steam_id:
            query["emclan"] = clan_steam_id
        query["emgid"] = gid
        return f"{STEAM_NEWS_BASE_URL}/{self.config.steam_app_id}?{urlencode(query)}"


def build_news_source(config: AppConfig, session: aiohttp.ClientSession) -> NewsSource:
    LOGGER.info(
        "Using Steam news hub events for app %s with language=%s.",
        config.steam_app_id,
        config.steam_language,
    )
    return SteamNewsSource(config, session)


def _sort_newest_first(posts: list[NewsPost]) -> list[NewsPost]:
    return sorted(
        posts,
        key=lambda post: (
            post.created_at or datetime.min.replace(tzinfo=timezone.utc),
            _numeric_id(post.post_id),
        ),
        reverse=True,
    )


def _dedupe_posts_by_id(posts: list[NewsPost]) -> list[NewsPost]:
    deduped: list[NewsPost] = []
    seen: set[str] = set()
    for post in posts:
        if post.post_id in seen:
            continue
        seen.add(post.post_id)
        deduped.append(post)
    return deduped


def _accept_language_header(language: str) -> str:
    return ACCEPT_LANGUAGE_HEADERS.get(language, ACCEPT_LANGUAGE_HEADERS["koreana"])


def _language_post_id(language: str, post_id: str) -> str:
    return f"steam:{language}:{post_id}"


def _steam_news_post_id_from_url(url: str) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    if (
        len(parts) >= 5
        and parts[0].lower() == "news"
        and parts[1].lower() == "app"
        and parts[3].lower() == "view"
        and parts[4]
    ):
        return parts[4]
    emgid = parse_qs(parsed.query).get("emgid")
    if emgid:
        return emgid[0]
    return None


def _stable_post_key(value: str) -> str:
    value = value.strip()
    if not value:
        return "unknown"
    digest = hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()
    return f"rss:{digest[:20]}"


def _url_with_query_values(url: str, values: dict[str, str]) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    for key, value in values.items():
        if value:
            query[key] = value
    return urlunparse(parsed._replace(query=urlencode(query)))


def _short_exception(exc: BaseException) -> str:
    if isinstance(exc, aiohttp.ClientResponseError):
        detail = f"HTTP {exc.status}"
        if exc.message:
            detail = f"{detail} {exc.message}"
        return detail.strip()

    text = str(exc).replace("\r", " ").replace("\n", " ").strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) > 160:
        text = f"{text[:157]}..."
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def _raise_response_error(response: aiohttp.ClientResponse, body: str) -> None:
    message = response.reason or "HTTP error"
    title = _html_title(body)
    if title and title.lower() not in message.lower():
        message = f"{message}: {title}"
    raise aiohttp.ClientResponseError(
        response.request_info,
        response.history,
        status=response.status,
        message=message,
        headers=response.headers,
    )


def _html_title(value: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", value, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    title = html.unescape(_strip_html_tags(match.group(1))).strip()
    title = re.sub(r"\s+", " ", title)
    return title[:120]


def _title_from_text(text: str, fallback: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line[:256]
    return fallback[:256]


def _datetime_from_rfc2822(value: str) -> datetime | None:
    if not value:
        return None
    try:
        moment = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _datetime_from_unix(value: object) -> datetime | None:
    if value in (None, ""):
        return None

    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return None

    if timestamp <= 0:
        return None

    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


def _numeric_id(value: str) -> int:
    match = re.search(r"(\d+)$", value)
    if match:
        return int(match.group(1))

    try:
        return int(value)
    except ValueError:
        return 0


def format_steam_news_for_discord(value: str | None) -> str:
    if not value:
        return ""

    value = html.unescape(value)
    value = _html_to_discord_markdown(value)
    value = _steam_bbcode_to_discord_markdown(value)
    value = html.unescape(value)
    return _normalize_discord_markdown(value)


def _html_to_discord_markdown(value: str) -> str:
    value = re.sub(
        r"<(script|style|iframe)\b[^>]*>.*?</\1>",
        "",
        value,
        flags=re.IGNORECASE | re.DOTALL,
    )
    value = re.sub(r"<img\b[^>]*>", "", value, flags=re.IGNORECASE)
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"</(?:p|div|section|article|blockquote)\s*>", "\n\n", value, flags=re.IGNORECASE)
    value = re.sub(r"<(?:p|div|section|article|blockquote)\b[^>]*>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(
        r"<h[1-6]\b[^>]*>(.*?)</h[1-6]\s*>",
        lambda match: f"\n\n**{_strip_html_tags(match.group(1)).strip()}**\n\n",
        value,
        flags=re.IGNORECASE | re.DOTALL,
    )
    value = re.sub(
        r"<(?:strong|b)\b[^>]*>(.*?)</(?:strong|b)\s*>",
        lambda match: f"**{_strip_html_tags(match.group(1)).strip()}**",
        value,
        flags=re.IGNORECASE | re.DOTALL,
    )
    value = re.sub(
        r"<(?:em|i)\b[^>]*>(.*?)</(?:em|i)\s*>",
        lambda match: f"*{_strip_html_tags(match.group(1)).strip()}*",
        value,
        flags=re.IGNORECASE | re.DOTALL,
    )
    value = re.sub(r"<li\b[^>]*>", "\n- ", value, flags=re.IGNORECASE)
    value = re.sub(r"</li\s*>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"</?(?:ul|ol)\b[^>]*>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(
        r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a\s*>",
        lambda match: f"{_strip_html_tags(match.group(2)).strip()} ({match.group(1).strip()})",
        value,
        flags=re.IGNORECASE | re.DOTALL,
    )
    value = re.sub(r"<[^>]+>", "", value)
    return value


def _strip_html_tags(value: str) -> str:
    return re.sub(r"<[^>]+>", "", html.unescape(value))


def _extract_html_image_urls(value: str | None) -> list[str]:
    if not value:
        return []
    urls: list[str] = []
    decoded = html.unescape(value)
    for match in re.finditer(r"<img\b[^>]*\bsrc=[\"']([^\"']+)[\"']", decoded, re.IGNORECASE):
        url = match.group(1).strip()
        if not url:
            continue
        urls.append(_normalize_steam_image_url(url, ""))
    return urls


def _rss_enclosure_urls(item: ET.Element) -> list[str]:
    urls: list[str] = []
    for enclosure in item.findall("enclosure"):
        url = str(enclosure.attrib.get("url") or "").strip()
        if not url:
            continue
        media_type = str(enclosure.attrib.get("type") or "").lower()
        if media_type and not media_type.startswith("image/"):
            continue
        urls.append(_normalize_steam_image_url(url, ""))
    return urls


def _xml_child_text(item: ET.Element, tag: str) -> str:
    child = item.find(tag)
    if child is None or child.text is None:
        return ""
    return child.text.strip()


def _steam_bbcode_to_discord_markdown(value: str) -> str:
    value = re.sub(r"\[img\].*?\[/img\]", "", value, flags=re.IGNORECASE | re.DOTALL)
    value = re.sub(
        r"\[previewyoutube=([^;\]]+)[^\]]*\]\[/previewyoutube\]",
        "",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"\[url=([^\]]+)\](.*?)\[/url\]",
        r"\2 (\1)",
        value,
        flags=re.IGNORECASE | re.DOTALL,
    )
    value = re.sub(
        r"\[b\](.*?)\[/b\]",
        r"**\1**",
        value,
        flags=re.IGNORECASE | re.DOTALL,
    )
    value = re.sub(
        r"\[h[1-6]\](.*?)\[/h[1-6]\]",
        lambda match: f"\n\n**{match.group(1).strip()}**\n\n",
        value,
        flags=re.IGNORECASE | re.DOTALL,
    )
    value = re.sub(
        r"\[i\](.*?)\[/i\]",
        r"*\1*",
        value,
        flags=re.IGNORECASE | re.DOTALL,
    )
    value = re.sub(
        r"\[/?(?:list|olist|quote|table)(?:=[^\]]*)?\]",
        "\n",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"\[/?(?:tr|th|td)\]", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"\[hr\]", "\n\n", value, flags=re.IGNORECASE)
    value = re.sub(r"\[\*\]", "\n- ", value)
    value = re.sub(
        r"\[/?(?:u|code|strike|spoiler|noparse|center|left|right|indent|url)(?:=[^\]]*)?\]",
        "",
        value,
        flags=re.IGNORECASE,
    )
    return value


def _normalize_discord_markdown(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = value.replace("\xa0", " ")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"[ \t]*\n[ \t]*", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    value = re.sub(r"\n\n(?=- )", "\n", value)

    lines: list[str] = []
    for line in value.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            stripped = "- " + stripped[2:].strip()
        lines.append(stripped)

    return "\n".join(lines).strip()


def _extract_steam_bbcode_image_urls(value: str, clan_id: str) -> list[str]:
    urls: list[str] = []
    for raw_url in re.findall(r"\[img\](.*?)\[/img\]", value, flags=re.IGNORECASE | re.DOTALL):
        url = raw_url.strip()
        if not url:
            continue
        urls.append(_normalize_steam_image_url(url, clan_id))
    return urls


def _localized_event_image_url(
    json_data: dict,
    clan_id: str,
    language: str,
    key: str,
) -> str | None:
    filename = _localized_value(json_data.get(key), language)
    if filename and clan_id:
        return f"{STEAM_CLAN_IMAGE_BASE_URL}/{clan_id}/{filename}"
    return None


def _localized_value(values: object, language: str) -> str | None:
    if not isinstance(values, list):
        return None

    language_index = STEAM_LANGUAGE_INDEXES.get(language)
    if language_index is not None and language_index < len(values):
        value = values[language_index]
        if isinstance(value, str) and value:
            return value

    for value in values:
        if isinstance(value, str) and value:
            return value

    return None


def _extract_youtube_urls(value: str | None) -> list[str]:
    if not value:
        return []

    value = html.unescape(value)
    video_ids = []
    video_ids.extend(
        re.findall(
            r"\[previewyoutube=([^;\]\s]+)[^\]]*\]\[/previewyoutube\]",
            value,
            flags=re.IGNORECASE,
        )
    )
    video_ids.extend(re.findall(r'data-youtube=["\']([^"\']+)["\']', value, flags=re.IGNORECASE))
    video_ids.extend(re.findall(r"youtube(?:-nocookie)?\.com/embed/([A-Za-z0-9_-]+)", value))
    video_ids.extend(re.findall(r"youtu\.be/([A-Za-z0-9_-]+)", value))
    video_ids.extend(re.findall(r"[?&]v=([A-Za-z0-9_-]+)", value))

    return [f"https://www.youtube.com/watch?v={video_id}" for video_id in dict.fromkeys(video_ids)]


def _normalize_steam_image_url(value: str, clan_id: str) -> str:
    value = value.replace("{STEAM_CLAN_IMAGE}", STEAM_CLAN_IMAGE_BASE_URL)
    if value.startswith("/"):
        return f"https://store.steampowered.com{value}"
    if value.startswith("http://"):
        return "https://" + value.removeprefix("http://")
    if value.startswith("https://"):
        return value
    if clan_id:
        return f"{STEAM_CLAN_IMAGE_BASE_URL}/{clan_id}/{value}"
    return value


def _extract_initial_events(page_html: str) -> dict | None:
    match = re.search(r'data-initial[Ee]vents="(.*?)"', page_html, flags=re.DOTALL)
    if not match:
        return None

    return _parse_json_object(html.unescape(match.group(1)))


def _parse_json_object(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value:
        return {}

    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}

    return parsed if isinstance(parsed, dict) else {}


def _dedupe_urls(urls: list[str]) -> list[str]:
    skipped_fragments = (
        "youtube_16x9_placeholder.gif",
        "1dc5775f3444c32d11acb9d57c03232157739877",
    )
    clean_urls = [
        url
        for url in urls
        if url and not any(fragment in url for fragment in skipped_fragments)
    ]
    return list(dict.fromkeys(clean_urls))
