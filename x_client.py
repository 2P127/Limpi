from __future__ import annotations

import asyncio
import html
import json
import re
from dataclasses import replace
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlunparse
from xml.etree import ElementTree

from config import AppConfig
from models import TwitterPost

_VIDEO_THUMBNAIL_URL_FRAGMENTS = (
    "/ext_tw_video_thumb/",
    "/amplify_video_thumb/",
    "/tweet_video_thumb/",
)


class XClientError(RuntimeError):
    pass


class LimbusXClient:
    def __init__(self, config: AppConfig, session: Any) -> None:
        self.config = config
        self.session = session
        self._playwright = None
        self._browser = None

    async def _get_browser(self):
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise XClientError(
                "playwright가 설치되어 있지 않아요. "
                "`pip install playwright && playwright install chromium` 를 실행해주세요."
            ) from exc

        if self._playwright is None:
            self._playwright = await async_playwright().start()
        if self._browser is None or not self._browser.is_connected():
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
            )
        return self._browser

    async def fetch_recent_posts(self, *, limit: int = 20) -> list[TwitterPost]:
        rss_posts = await self._fetch_recent_posts_from_rss(limit=limit)
        if rss_posts:
            return await self._enrich_posts_with_graphql_media(rss_posts, limit=limit)
        browser = await self._get_browser()
        return await _playwright_fetch(browser, self.config.x_account_username, limit)

    async def _fetch_recent_posts_from_rss(self, *, limit: int) -> list[TwitterPost]:
        if self.session is None:
            return []
        url = f"https://nitter.net/{self.config.x_account_username}/rss"
        try:
            async with self.session.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 LimpiBot/1.0"},
                timeout=30,
            ) as response:
                if response.status >= 400:
                    return []
                text = await response.text()
        except Exception:
            return []
        return _parse_nitter_rss(text, self.config.x_account_username, limit)

    async def _enrich_posts_with_graphql_media(
        self, posts: list[TwitterPost], *, limit: int
    ) -> list[TwitterPost]:
        posts = await self._enrich_posts_with_fx_media(posts)
        try:
            browser = await self._get_browser()
            media_posts = await _playwright_fetch(
                browser,
                self.config.x_account_username,
                max(limit, 100),
            )
        except Exception:
            return posts

        media_by_id = {post.post_id: post for post in media_posts}
        enriched: list[TwitterPost] = []
        for post in posts:
            media_post = media_by_id.get(post.post_id)
            if media_post is None:
                enriched.append(post)
                continue

            raw = dict(post.raw)
            for key in ("video_urls", "video_variant_groups", "youtube_urls"):
                value = media_post.raw.get(key)
                if value and not raw.get(key):
                    raw[key] = value
            if media_post.raw.get("video_urls") and not raw.get("video_urls"):
                raw["video_fallback_url"] = str(media_post.raw["video_urls"][0])

            enriched.append(replace(post, raw=raw))
        return enriched

    async def _enrich_posts_with_fx_media(
        self, posts: list[TwitterPost]
    ) -> list[TwitterPost]:
        if self.session is None:
            return posts

        enriched: list[TwitterPost] = []
        for post in posts:
            if not post.raw.get("video_fallback_url"):
                enriched.append(post)
                continue

            groups = await self._fetch_fx_video_variant_groups(post)
            if not groups:
                enriched.append(post)
                continue

            raw = dict(post.raw)
            raw["video_variant_groups"] = groups
            raw["video_urls"] = [group[0] for group in groups if group]
            raw["video_fallback_url"] = raw["video_urls"][0]
            enriched.append(replace(post, raw=raw))
        return enriched

    async def _fetch_fx_video_variant_groups(
        self, post: TwitterPost
    ) -> list[list[str]]:
        tweet_id = str(post.raw.get("tweet_id") or post.post_id.removeprefix("x:"))
        url = f"https://api.fxtwitter.com/{post.author_username}/status/{tweet_id}"
        try:
            async with self.session.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 LimpiBot/1.0"},
                timeout=30,
            ) as response:
                if response.status >= 400:
                    return []
                payload = await response.json(content_type=None)
        except Exception:
            return []
        return _fx_video_variant_groups(payload)


async def _playwright_fetch(browser: Any, username: str, limit: int) -> list[TwitterPost]:
    context = await browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 800},
        locale="en-US",
    )
    page = await context.new_page()
    payloads: list[dict] = []

    async def on_response(response: Any) -> None:
        if _is_user_tweets_response(response.url):
            try:
                data = await response.json()
                payloads.append(data)
            except Exception:
                pass

    page.on("response", on_response)

    try:
        await page.goto(
            f"https://x.com/{username}",
            wait_until="domcontentloaded",
            timeout=30_000,
        )
        loop = asyncio.get_event_loop()
        deadline = loop.time() + 15
        first_posts_at: float | None = None
        while loop.time() < deadline:
            if payloads and _extract_twitter_posts_from_payloads(payloads, username):
                if first_posts_at is None:
                    first_posts_at = loop.time()
                if loop.time() - first_posts_at >= 3:
                    break
            await asyncio.sleep(0.5)

        if not payloads:
            raise XClientError(
                "X 타임라인 데이터를 받지 못했어요. "
                "X가 차단했거나 X_ACCOUNT_USERNAME 계정명을 확인해야 해요."
            )

        posts = _extract_twitter_posts_from_payloads(payloads, username)
        posts.sort(
            key=lambda p: p.created_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        return posts[:limit]
    finally:
        await page.close()
        await context.close()


def _is_user_tweets_response(url: str) -> bool:
    return "/UserTweets" in url or "UserTweets?" in url


def _extract_twitter_posts_from_payloads(
    payloads: list[dict[str, Any]], username: str
) -> list[TwitterPost]:
    posts: list[TwitterPost] = []
    seen: set[str] = set()
    for payload in payloads:
        for post in _extract_twitter_posts(payload, username):
            if post.post_id in seen:
                continue
            seen.add(post.post_id)
            posts.append(post)
    return posts


def _extract_twitter_posts(payload: dict[str, Any], username: str) -> list[TwitterPost]:
    posts: list[TwitterPost] = []
    seen: set[str] = set()
    results = _timeline_tweet_results(payload)
    if not results:
        results = _fallback_tweet_results(payload)
    for result in results:
        tweet = _unwrap_tweet_result(result)
        if tweet is None:
            continue
        post = _tweet_to_post(tweet, username)
        if post is not None and post.post_id not in seen:
            seen.add(post.post_id)
            posts.append(post)
    return posts


def _timeline_tweet_results(payload: dict[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for instruction in _walk(payload):
        if not isinstance(instruction, dict) or "entries" not in instruction:
            continue
        if str(instruction.get("type") or "") == "TimelinePinEntry":
            continue
        entries = instruction.get("entries")
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            entry_id = str(entry.get("entryId") or entry.get("entry_id") or "")
            if not entry_id.startswith("tweet-"):
                continue
            result = _entry_tweet_result(entry)
            if isinstance(result, dict):
                results.append(result)
    return results


def _entry_tweet_result(entry: dict[str, Any]) -> dict[str, Any] | None:
    content = entry.get("content")
    if not isinstance(content, dict):
        return None
    item_content = content.get("itemContent")
    if not isinstance(item_content, dict):
        return None
    tweet_results = item_content.get("tweet_results")
    if not isinstance(tweet_results, dict):
        return None
    result = tweet_results.get("result")
    return result if isinstance(result, dict) else None


def _fallback_tweet_results(payload: dict[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in _walk(payload):
        if not isinstance(item, dict):
            continue
        tweet_results = item.get("tweet_results")
        if not isinstance(tweet_results, dict):
            continue
        result = tweet_results.get("result")
        if isinstance(result, dict):
            results.append(result)
    return results


def _walk(value: Any) -> list[Any]:
    items = [value]
    if isinstance(value, dict):
        for nested in value.values():
            items.extend(_walk(nested))
    elif isinstance(value, list):
        for nested in value:
            items.extend(_walk(nested))
    return items


def _unwrap_tweet_result(result: Any) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    typename = str(result.get("__typename") or "")
    if typename in {"Tweet", "TweetWithVisibilityResults"}:
        tweet = result.get("tweet") if typename == "TweetWithVisibilityResults" else result
        return tweet if isinstance(tweet, dict) else None
    return None


def _tweet_to_post(tweet: dict[str, Any], username: str) -> TwitterPost | None:
    legacy = tweet.get("legacy")
    if not isinstance(legacy, dict):
        return None
    if not _tweet_author_matches(tweet, username):
        return None
    tweet_id = str(tweet.get("rest_id") or legacy.get("id_str") or "").strip()
    if not tweet_id:
        return None
    text = str(legacy.get("full_text") or legacy.get("text") or "")
    link_urls = _external_link_urls(legacy, username, tweet_id)
    text = _strip_tco_links(text, legacy)
    text = _clean_tweet_text(text)
    if text in {"메인에 올림", "Pinned", "Pinned Tweet"}:
        return None
    title = _title_from_text(text) or f"X 게시물 {tweet_id}"
    created_at = _parse_twitter_datetime(legacy.get("created_at"))
    video_variant_groups = _video_variant_groups(tweet)
    video_urls = [group[0] for group in video_variant_groups if group]
    image_urls = _photo_urls(tweet)
    if video_urls and len(image_urls) == 1:
        image_urls = []
    youtube_urls = _youtube_urls(legacy)
    raw: dict[str, Any] = {
        "source": "x",
        "language": "koreana",
        "tweet_id": tweet_id,
        "username": username,
        "created_at": legacy.get("created_at"),
    }
    if video_urls:
        raw["video_urls"] = video_urls
        raw["video_variant_groups"] = video_variant_groups
    if youtube_urls:
        raw["youtube_urls"] = youtube_urls
    if link_urls:
        raw["link_urls"] = link_urls
    return TwitterPost(
        post_id=f"x:{tweet_id}",
        author_username=username,
        url=f"https://x.com/{username}/status/{tweet_id}",
        text=text or f"https://x.com/{username}/status/{tweet_id}",
        title=title,
        created_at=created_at,
        image_urls=image_urls,
        raw=raw,
    )


def _strip_tco_links(text: str, legacy: dict[str, Any]) -> str:
    urls: set[str] = set()
    for media in legacy.get("extended_entities", {}).get("media", []) or []:
        if isinstance(media, dict) and media.get("url"):
            urls.add(str(media["url"]))
    for media in legacy.get("entities", {}).get("media", []) or []:
        if isinstance(media, dict) and media.get("url"):
            urls.add(str(media["url"]))
    for url_entity in legacy.get("entities", {}).get("urls", []) or []:
        if isinstance(url_entity, dict) and url_entity.get("url"):
            urls.add(str(url_entity["url"]))
    for url in urls:
        text = text.replace(url, "")
    return text


def _clean_tweet_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t\u3000]+", " ", line).strip() for line in text.split("\n")]
    cleaned = "\n".join(lines).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned


def _title_from_text(text: str) -> str:
    for line in text.splitlines():
        cleaned = line.strip()
        if cleaned:
            return cleaned[:80]
    return ""


def _parse_twitter_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _tweet_author_matches(tweet: dict[str, Any], username: str) -> bool:
    user_result = tweet.get("core", {}).get("user_results", {}).get("result")
    if not isinstance(user_result, dict):
        return True
    legacy = user_result.get("legacy")
    if not isinstance(legacy, dict):
        return True
    screen_name = str(legacy.get("screen_name") or "").strip()
    if not screen_name:
        return True
    return screen_name.lower() == username.lower()


def _photo_urls(tweet: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for media in _iter_media_entities(tweet):
        media_type = str(media.get("type") or "").lower()
        if (
            media_type == "photo"
            and not media.get("video_info")
            and media.get("media_url_https")
        ):
            url = str(media["media_url_https"])
            if _is_video_thumbnail_url(url):
                continue
            urls.append(_highest_quality_photo_url(url))
    return list(dict.fromkeys(urls))


def _highest_quality_photo_url(url: str) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["name"] = "orig"
    if parsed.netloc.endswith("twimg.com") and "format" not in query:
        suffix = parsed.path.rsplit(".", 1)
        if len(suffix) == 2 and suffix[1]:
            query["format"] = suffix[1].lower()
    return urlunparse(parsed._replace(query=urlencode(query)))


def _is_video_thumbnail_url(url: str) -> bool:
    lowered = url.lower()
    return any(fragment in lowered for fragment in _VIDEO_THUMBNAIL_URL_FRAGMENTS)


def _fx_video_variant_groups(payload: dict[str, Any]) -> list[list[str]]:
    tweet = payload.get("tweet")
    if not isinstance(tweet, dict):
        return []

    media_root = tweet.get("media")
    if not isinstance(media_root, dict):
        return []

    media_items: list[dict[str, Any]] = []
    for key in ("all", "videos"):
        value = media_root.get(key)
        if isinstance(value, list):
            media_items.extend(item for item in value if isinstance(item, dict))

    groups: list[list[str]] = []
    for media in media_items:
        variants: list[dict[str, Any]] = []
        for key in ("variants", "formats"):
            value = media.get(key)
            if isinstance(value, list):
                variants.extend(item for item in value if isinstance(item, dict))

        mp4_variants = [
            variant
            for variant in variants
            if _fx_variant_url(variant)
            and (
                variant.get("content_type") == "video/mp4"
                or variant.get("container") == "mp4"
                or ".mp4" in str(_fx_variant_url(variant)).lower()
            )
        ]
        mp4_variants.sort(
            key=lambda variant: int(variant.get("bitrate") or 0),
            reverse=True,
        )

        group = list(dict.fromkeys(str(_fx_variant_url(variant)) for variant in mp4_variants))
        direct_url = str(media.get("url") or "")
        if not group and ".mp4" in direct_url.lower():
            group = [direct_url]
        if group:
            groups.append(group)

    return _dedupe_video_variant_groups(groups)


def _fx_variant_url(variant: dict[str, Any]) -> str | None:
    url = variant.get("url")
    if not url:
        url = variant.get("href")
    return str(url) if url else None


def _video_variant_groups(tweet: dict[str, Any]) -> list[list[str]]:
    groups: list[list[str]] = []
    for media in _iter_media_entities(tweet):
        video_info = media.get("video_info")
        if not isinstance(video_info, dict):
            continue
        variants = video_info.get("variants") or []
        mp4_variants = [
            v
            for v in variants
            if isinstance(v, dict) and v.get("content_type") == "video/mp4" and v.get("url")
        ]
        if not mp4_variants:
            continue
        mp4_variants.sort(key=lambda v: int(v.get("bitrate") or 0), reverse=True)
        group = list(dict.fromkeys(str(variant["url"]) for variant in mp4_variants))
        if group:
            groups.append(group)
    return _dedupe_video_variant_groups(groups)


def _video_urls(tweet: dict[str, Any]) -> list[str]:
    return [group[0] for group in _video_variant_groups(tweet) if group]


def _dedupe_video_variant_groups(groups: list[list[str]]) -> list[list[str]]:
    deduped: list[list[str]] = []
    seen_first_urls: set[str] = set()
    for group in groups:
        if not group or group[0] in seen_first_urls:
            continue
        seen_first_urls.add(group[0])
        deduped.append(group)
    return deduped


def _iter_media_entities(tweet: dict[str, Any]) -> list[dict[str, Any]]:
    media: list[dict[str, Any]] = []
    legacy = tweet.get("legacy")
    if isinstance(legacy, dict):
        media.extend(_media_list(legacy.get("extended_entities", {}).get("media")))
        media.extend(_media_list(legacy.get("entities", {}).get("media")))

    card = tweet.get("card")
    if isinstance(card, dict):
        media.extend(_iter_card_media_entities(card))

    media.extend(
        item
        for item in _walk(tweet)
        if isinstance(item, dict) and isinstance(item.get("video_info"), dict)
    )

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in media:
        key = str(
            item.get("media_key")
            or item.get("id_str")
            or item.get("media_url_https")
            or item.get("url")
            or id(item)
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _media_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _iter_card_media_entities(card: dict[str, Any]) -> list[dict[str, Any]]:
    media: list[dict[str, Any]] = []
    for binding in card.get("legacy", {}).get("binding_values", []) or []:
        if not isinstance(binding, dict):
            continue
        value = binding.get("value")
        if not isinstance(value, dict):
            continue
        if isinstance(value.get("image_value"), dict):
            media.append(value["image_value"])
        string_value = value.get("string_value")
        if isinstance(string_value, str) and string_value.strip().startswith("{"):
            try:
                decoded = json.loads(string_value)
            except json.JSONDecodeError:
                continue
            media.extend(
                item
                for item in _walk(decoded)
                if isinstance(item, dict)
                and (
                    isinstance(item.get("video_info"), dict)
                    or item.get("media_url_https")
                    or item.get("media_url")
                )
            )
    return media


def _youtube_urls(legacy: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for url_entity in legacy.get("entities", {}).get("urls", []) or []:
        if not isinstance(url_entity, dict):
            continue
        expanded = str(url_entity.get("expanded_url") or "")
        if "youtube.com/watch" in expanded or "youtu.be/" in expanded:
            urls.append(expanded)
    return list(dict.fromkeys(urls))


def _external_link_urls(
    legacy: dict[str, Any],
    username: str,
    tweet_id: str,
) -> list[str]:
    urls: list[str] = []
    for url_entity in legacy.get("entities", {}).get("urls", []) or []:
        if not isinstance(url_entity, dict):
            continue
        expanded = str(url_entity.get("expanded_url") or url_entity.get("url") or "").strip()
        if not expanded or _is_tweet_self_url(expanded, username, tweet_id):
            continue
        urls.append(expanded)
    return list(dict.fromkeys(urls))


def _is_tweet_self_url(url: str, username: str, tweet_id: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host not in {"x.com", "twitter.com", "mobile.twitter.com"}:
        return False
    parts = [part for part in parsed.path.split("/") if part]
    return (
        len(parts) >= 3
        and parts[0].lower() == username.lower()
        and parts[1] == "status"
        and parts[2] == tweet_id
    )


def _parse_nitter_rss(xml_text: str, username: str, limit: int) -> list[TwitterPost]:
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return []

    posts: list[TwitterPost] = []
    for item in root.findall("./channel/item"):
        creator = item.findtext("{http://purl.org/dc/elements/1.1/}creator") or ""
        if creator.strip().lower() != f"@{username.lower()}":
            continue
        tweet_id = (item.findtext("guid") or "").strip()
        if not tweet_id.isdigit():
            continue

        title = html.unescape(item.findtext("title") or "").strip()
        title = _strip_rss_reply_prefix(title, username)
        if title in {"메인에 올림", "Pinned", "Pinned Tweet"}:
            continue
        description = item.findtext("description") or ""
        created_at = _parse_twitter_datetime(item.findtext("pubDate"))
        post_url = f"https://x.com/{username}/status/{tweet_id}"
        image_urls = _rss_image_urls(description)
        link_urls = _rss_link_urls(description, username, tweet_id)
        raw: dict[str, Any] = {
            "source": "x-rss",
            "language": "koreana",
            "tweet_id": tweet_id,
            "username": username,
            "created_at": item.findtext("pubDate"),
        }
        if link_urls:
            raw["link_urls"] = link_urls
        if _rss_has_video(description):
            raw["video_fallback_url"] = post_url

        posts.append(
            TwitterPost(
                post_id=f"x:{tweet_id}",
                author_username=username,
                url=post_url,
                text=_clean_tweet_text(title) or post_url,
                title=_title_from_text(title) or f"X 게시물 {tweet_id}",
                created_at=created_at,
                image_urls=image_urls,
                raw=raw,
            )
        )
        if len(posts) >= limit:
            break
    return posts


def _strip_rss_reply_prefix(text: str, username: str) -> str:
    prefix = f"R to @{username}:"
    if text.startswith(prefix):
        return text[len(prefix) :].strip()
    return text


def _rss_has_video(description: str) -> bool:
    return re.search(r">\s*Video\s*<", description, flags=re.IGNORECASE) is not None


def _rss_image_urls(description: str) -> list[str]:
    urls: list[str] = []
    for match in re.finditer(r'<img\s+[^>]*src=["\']([^"\']+)["\']', description, flags=re.IGNORECASE):
        url = html.unescape(match.group(1))
        normalized = _normalize_nitter_image_url(url)
        if _is_video_thumbnail_url(normalized):
            continue
        urls.append(_highest_quality_photo_url(normalized))
    return list(dict.fromkeys(urls))


def _normalize_nitter_image_url(url: str) -> str:
    parsed = urlparse(url)
    if "nitter.net" not in parsed.netloc or not parsed.path.startswith("/pic/"):
        return url
    decoded = unquote(parsed.path.removeprefix("/pic/"))
    if decoded.startswith("https://") or decoded.startswith("http://"):
        return decoded
    if decoded.startswith(("media/", "card_img/", "amplify_video_thumb/", "ext_tw_video_thumb/")):
        return f"https://pbs.twimg.com/{decoded}"
    return url


def _rss_link_urls(description: str, username: str, tweet_id: str) -> list[str]:
    urls: list[str] = []
    for match in re.finditer(r'<a\s+[^>]*href=["\']([^"\']+)["\']', description, flags=re.IGNORECASE):
        url = html.unescape(match.group(1)).strip()
        if not url or _is_tweet_self_url(url, username, tweet_id):
            continue
        parsed = urlparse(url)
        if parsed.netloc.endswith("nitter.net"):
            continue
        urls.append(url)
    return list(dict.fromkeys(urls))
