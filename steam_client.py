from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Protocol
from urllib.parse import urlencode
from xml.etree import ElementTree

import aiohttp

from config import AppConfig
from models import NewsPost


LOGGER = logging.getLogger(__name__)
STEAM_NEWS_BASE_URL = "https://store.steampowered.com/news/app"
STEAM_RSS_BASE_URL = "https://store.steampowered.com/feeds/news/app"
STEAM_REQUEST_TIMEOUT_SECONDS = 20
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
            event_posts = await self._fetch_event_posts(language)
        except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError, RuntimeError) as exc:
            LOGGER.warning("Steam initial event data request failed. Falling back to RSS: %s", exc)
            event_posts = []
        if event_posts:
            return _sort_newest_first(event_posts)[:limit]

        LOGGER.warning("Steam initial event data was unavailable. Falling back to RSS.")
        return await self._fetch_rss_posts(language, limit)

    async def _fetch_rss_posts(self, language: str, limit: int) -> list[NewsPost]:
        url = self._feed_url(language)
        headers = {
            "Accept": "application/rss+xml, application/xml;q=0.9, text/xml;q=0.8",
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
                raise RuntimeError(f"Steam news RSS request failed ({response.status}): {body[:500]}")
            body = await response.text()

        root = ElementTree.fromstring(body)
        posts = self._parse_rss(root, language)
        return _sort_newest_first(posts)[:limit]

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
                raise RuntimeError(f"Steam news hub request failed ({response.status}): {body[:500]}")
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

    def _feed_url(self, language: str) -> str:
        if self.config.steam_news_url:
            return self.config.steam_news_url

        query = urlencode({"l": language, "cc": self.config.steam_country})
        return f"{STEAM_RSS_BASE_URL}/{self.config.steam_app_id}/?{query}"

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

    def _parse_rss(self, root: ElementTree.Element, language: str | None = None) -> list[NewsPost]:
        language = language or self.config.steam_language
        posts: list[NewsPost] = []
        for item in root.findall("./channel/item"):
            title = _find_text(item, "title") or "Steam news"
            link = _find_text(item, "link")
            guid = _find_text(item, "guid") or link
            description_html = _find_text(item, "description") or ""
            pub_date = _find_text(item, "pubDate")
            image_urls = _extract_image_urls(item, description_html)
            youtube_urls = _extract_youtube_urls(description_html)
            is_video_only = bool(youtube_urls) and not image_urls
            image_urls = _dedupe_urls(image_urls)
            text = format_steam_news_for_discord(description_html)
            if not text:
                text = title

            post_url = link or guid or self._app_news_url(language)
            raw_post_id = _post_id_from_url(post_url) or _stable_hash(guid or post_url or title)
            posts.append(
                NewsPost(
                    post_id=_language_post_id(language, raw_post_id),
                    source_user="Limbus Company Steam News",
                    url=post_url,
                    text=text,
                    title=_title_from_text(title, fallback=text or raw_post_id),
                    created_at=_parse_rss_datetime(pub_date),
                    image_urls=image_urls,
                    raw={
                        "title": title,
                        "link": link,
                        "guid": guid,
                        "description": description_html,
                        "source_url": self._app_news_url(language),
                        "language": language,
                        "youtube_urls": youtube_urls,
                        "is_video_only": is_video_only,
                    },
                )
            )

        return posts

    def _app_news_url(self, language: str | None = None) -> str:
        language = language or self.config.steam_language
        return f"{STEAM_NEWS_BASE_URL}/{self.config.steam_app_id}/?l={language}"

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


def _accept_language_header(language: str) -> str:
    return ACCEPT_LANGUAGE_HEADERS.get(language, ACCEPT_LANGUAGE_HEADERS["koreana"])


def _language_post_id(language: str, post_id: str) -> str:
    return f"steam:{language}:{post_id}"


def _title_from_text(text: str, fallback: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line[:256]
    return fallback[:256]


def _parse_rss_datetime(value: str | None) -> datetime | None:
    if not value:
        return None

    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


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


def _find_text(element: ElementTree.Element, name: str) -> str | None:
    child = element.find(name)
    if child is None or child.text is None:
        return None
    return child.text.strip()


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


def _extract_image_urls(element: ElementTree.Element, html_text: str | None) -> list[str]:
    urls: list[str] = []
    for child in element.iter():
        tag_name = child.tag.rsplit("}", 1)[-1].lower()
        url = child.attrib.get("url")
        content_type = child.attrib.get("type", "")
        if url and (tag_name == "enclosure" or content_type.startswith("image/")):
            urls.append(url)

    if html_text:
        html_text = html.unescape(html_text)
        urls.extend(re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', html_text, flags=re.IGNORECASE))

    return _dedupe_urls(urls)


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


def _post_id_from_url(value: str | None) -> str | None:
    if not value:
        return None

    match = re.search(r"/view/(\d+)", value)
    return match.group(1) if match else None


def _stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


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
