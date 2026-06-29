from __future__ import annotations

import csv
import ctypes
import io
import logging
import os
import re
import socket
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import aiohttp
import cv2
import discord
import numpy as np
from PIL import Image, UnidentifiedImageError
from discord import app_commands

from .bot_constants import (
    AIOHTTP_KEEPALIVE_TIMEOUT_SECONDS,
    BOOLEAN_TRUE,
    BROADCAST_SOURCE_BOTH,
    BROADCAST_SOURCE_CHZZK,
    BROADCAST_SOURCE_YOUTUBE,
    CHZZK_LIVE_ANNOUNCE_MAX_AGE,
    CHZZK_LIVE_END_ANNOUNCE_MAX_AGE,
    EGO_GIFT_CSV_PATH,
    EMBED_DESCRIPTION_LIMIT,
    ES_CONTINUOUS,
    ES_SYSTEM_REQUIRED,
    HAMPANG_SOURCE_BOTH,
    HAMPANG_SOURCE_X,
    HAMPANG_SOURCE_YOUTUBE,
    HAMPANG_YOUTUBE_TITLE_MARKER,
    IMAGE_DELIVERY_FILES,
    IMAGE_ONLY_EMBEDS_PER_MESSAGE,
    KST,
    LANGUAGE_LABELS,
    LEGACY_STEAM_CARD_THUMBNAIL_FRAGMENTS,
    MAINTENANCE_START_HOUR,
    MAINTENANCE_UPDATE_HOUR,
    MAINTENANCE_WEEKDAY,
    MAX_INLINE_GALLERY_IMAGES,
    MAX_TWITTER_EMBED_IMAGES,
    NEWS_BANNER_ATTACHMENT_NAME,
    NEWS_BANNER_DIR,
    NEWS_BANNER_DISABLED_LABEL,
    NEWS_BANNER_EXTENSIONS,
    NEWS_SOURCE_STEAM,
    NEWS_SOURCE_TWITTER,
    NEWS_UI_TEXT,
    SYNC_LANGUAGES,
    TCP_KEEPALIVE_IDLE_SECONDS,
    TCP_KEEPALIVE_INTERVAL_SECONDS,
    TCP_KEEPALIVE_PROBES,
    WINDOWS_KEEPALIVE_INTERVAL_MS,
    WINDOWS_KEEPALIVE_TIME_MS,
    YOUTUBE_LIVE_ANNOUNCE_MAX_AGE,
    YOUTUBE_PLACEHOLDER_IMAGE_FRAGMENT,
)
from .clients.chzzk_client import (
    ChzzkLive,
    PROJECT_MOON_CHZZK_LIVE_URL,
)
from .clients.youtube_client import (
    PROJECT_MOON_YOUTUBE_STREAMS_URL,
    PROJECT_MOON_YOUTUBE_VIDEOS_URL,
    YoutubeLive,
    YoutubeUpload,
)
from .core.models import (
    GuildChzzkTarget,
    GuildHampangTarget,
    GuildNewsTarget,
    GuildYoutubeTarget,
    GuildYoutubeUploadTarget,
    NewsPost,
    TwitterPost,
)
from .core.storage import (
    DEFAULT_NOTIFICATION_BANNER,
    DISABLED_NOTIFICATION_BANNER,
    NEWS_UPDATE_MAX_AGE_SECONDS,
)

LOGGER = logging.getLogger(__name__)

@dataclass(frozen=True)
class EgoGift:
    name: str
    grade: str
    keyword: str
    category: str
    related: str
    first_seen: str
    upgradeable: str
    sale_price: str
    purchasable: str
    synthesis: str
    hard_only: str
    extreme_only: str
    theme_pack_only: str
    recipe: str
    effect: str
    image_url: str


def _normalize_search_text(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


@lru_cache(maxsize=1)
def _load_ego_gifts() -> tuple[EgoGift, ...]:
    path = _resource_path(EGO_GIFT_CSV_PATH)
    if not path.exists():
        LOGGER.warning("에고 기프트 CSV 파일을 찾지 못했습니다: %s", path)
        return ()

    gifts: list[EgoGift] = []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            name, grade = _split_ego_gift_name_and_grade(
                (row.get("이름") or "").strip(),
                (row.get("등급") or "").strip(),
            )
            if not name:
                continue
            gifts.append(
                EgoGift(
                    name=name,
                    grade=grade,
                    keyword=(row.get("키워드") or "").strip(),
                    category=(row.get("카테고리") or "").strip(),
                    related=(row.get("연관") or "").strip(),
                    first_seen=(row.get("첫_등장") or "").strip(),
                    upgradeable=(row.get("강화_가능") or "").strip(),
                    sale_price=(row.get("판매_가격") or "").strip(),
                    purchasable=(row.get("구매_가능") or "").strip(),
                    synthesis=(row.get("합성_기프트") or "").strip(),
                    hard_only=(row.get("하드_한정") or "").strip(),
                    extreme_only=(row.get("익스트림_한정") or "").strip(),
                    theme_pack_only=(row.get("테마팩_한정") or "").strip(),
                    recipe=(row.get("조합식") or "").strip(),
                    effect=(row.get("효과") or "").strip(),
                    image_url=(row.get("이미지_URL") or "").strip(),
                )
            )
    return tuple(sorted(gifts, key=_ego_gift_sort_key))


def _find_ego_gifts(query: str) -> list[EgoGift]:
    return _filter_ego_gifts(query)


def _split_ego_gift_name_and_grade(name: str, grade: str) -> tuple[str, str]:
    if grade:
        return name, grade

    match = re.match(r"^EX\s+(.+)$", name, flags=re.IGNORECASE)
    if match is None:
        return name, grade
    return match.group(1).strip(), "EX"


def _ego_gift_sort_key(gift: EgoGift) -> tuple[int, str, str]:
    return _ego_gift_grade_value(gift.grade), _ego_gift_keyword(gift), gift.name


def _ego_gift_keyword(gift: EgoGift) -> str:
    if gift.grade.strip().upper() == "EX":
        return "EX"
    return gift.keyword


def _ego_gift_grade_value(grade: str) -> int:
    normalized = grade.strip().upper()
    roman_grades = {
        "Ⅰ": 1,
        "I": 1,
        "Ⅱ": 2,
        "II": 2,
        "Ⅲ": 3,
        "III": 3,
        "Ⅳ": 4,
        "IV": 4,
        "Ⅴ": 5,
        "V": 5,
        "EX": 6,
    }
    if normalized in roman_grades:
        return roman_grades[normalized]
    try:
        return int(normalized)
    except ValueError:
        return 999


def _filter_ego_gifts(query: str, *, keyword: str | None = None) -> list[EgoGift]:
    normalized_query = _normalize_search_text(query)
    gifts = [
        gift
        for gift in _load_ego_gifts()
        if keyword is None or _ego_gift_keyword(gift) == keyword
    ]
    if not normalized_query:
        return gifts

    grade_matches = [
        gift
        for gift in gifts
        if _normalize_search_text(gift.grade) == normalized_query
    ]
    if grade_matches:
        return grade_matches

    exact = [
        gift
        for gift in gifts
        if normalized_query in _ego_gift_search_values(gift)
    ]
    if exact:
        return exact

    startswith = [
        gift
        for gift in gifts
        if any(value.startswith(normalized_query) for value in _ego_gift_search_values(gift))
    ]
    contains = [
        gift
        for gift in gifts
        if any(normalized_query in value for value in _ego_gift_search_values(gift))
        and gift not in startswith
    ]
    return [*startswith, *contains]


def _ego_gift_search_values(gift: EgoGift) -> tuple[str, ...]:
    name = _normalize_search_text(gift.name)
    grade_and_name = _normalize_search_text(f"{gift.grade} {gift.name}")
    return name, grade_and_name


@lru_cache(maxsize=1)
def _ego_gift_keyword_counts() -> tuple[tuple[str, int], ...]:
    counts: dict[str, int] = {}
    for gift in _load_ego_gifts():
        keyword = _ego_gift_keyword(gift)
        if not keyword:
            continue
        counts[keyword] = counts.get(keyword, 0) + 1
    return tuple(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _ego_gift_component_markdown(gift: EgoGift, *, status_text: str) -> str:
    detail_lines = [
        ("등급", _ego_gift_grade_label(gift.grade), False),
        ("강화 여부", gift.upgradeable, False),
        ("판매 가격", gift.sale_price, False),
        ("구매 가능", _format_ego_gift_flag(gift.purchasable), False),
        ("합성 기프트", _format_ego_gift_flag(gift.synthesis), False),
        ("하드 한정", _format_ego_gift_flag(gift.hard_only), False),
        ("익스트림 한정", _format_ego_gift_flag(gift.extreme_only), True),
        ("테마팩 한정", gift.theme_pack_only, True),
    ]
    if gift.recipe:
        detail_lines.append(("조합식", gift.recipe, True))

    quoted_details = "\n".join(
        f"> {'-# ' if muted else ''}{label}: **{value or '-'}**"
        for label, value, muted in detail_lines
    )
    return (
        f"## **{gift.name}**\n"
        f"-# 분류: **{_ego_gift_keyword(gift) or '-'}**\n\n"
        "## **기프트 상세 정보**\n"
        f"{quoted_details}\n\n"
        "## **효과**\n"
        f"{_format_ego_gift_effect_markdown(gift.effect)}\n\n"
        f"-# {status_text}"
    )


def _format_ego_gift_flag(value: str) -> str:
    return value.strip() or "-"


def _ego_gift_grade_label(grade: str) -> str:
    value = grade.strip()
    if value.upper() == "EX":
        return "EX"
    return f"{value}등급" if value else "-"


def _format_ego_gift_effect_markdown(effect: str) -> str:
    normalized_effect = effect.replace("\r\n", "\n").replace("\r", "\n")
    if "|" in normalized_effect:
        raw_parts = normalized_effect.split("|")
    else:
        raw_parts = normalized_effect.split("\n")
    parts = [part.strip() for part in raw_parts if part.strip()]
    if not parts:
        return "-"

    heading_labels = {
        "기본 효과": "기본 효과",
        "+": "+ (1강)",
        "++": "++ (2강)",
    }
    lines: list[str] = []
    for part in parts:
        heading = heading_labels.get(part)
        if heading is not None:
            lines.append(f"### **• {heading}**")
        else:
            lines.append(part)
    return "\n".join(lines)


def _filter_image_urls(urls: list[str]) -> list[str]:
    return [
        url
        for url in urls
        if url and YOUTUBE_PLACEHOLDER_IMAGE_FRAGMENT not in url
    ]


def _normalize_image_url(url: str) -> str:
    value = url.strip()
    if value.startswith("//"):
        return f"https:{value}"
    return value


def _image_request_headers(url: str) -> dict[str, str]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0 Safari/537.36 LimpiBot/1.0"
        )
    }
    hostname = urlparse(url).hostname or ""
    if hostname.endswith("namu.wiki"):
        headers["Referer"] = "https://namu.wiki/"
    return headers


def _is_namu_wiki_image_url(url: str) -> bool:
    hostname = (urlparse(url).hostname or "").casefold()
    return hostname == "namu.wiki" or hostname.endswith(".namu.wiki")


def _resource_path(relative_path: Path) -> Path:
    base_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base_path / relative_path


def _banner_files() -> list[Path]:
    directory = _resource_path(NEWS_BANNER_DIR)
    if not directory.exists():
        return []
    return sorted(
        (
            path
            for path in directory.iterdir()
            if (
                path.is_file()
                and path.suffix.lower() in NEWS_BANNER_EXTENSIONS
                and "배너" in path.stem
            )
        ),
        key=lambda path: path.stem.casefold(),
    )


def _resolve_banner_filename(value: str | None) -> str | None:
    if value is None:
        return None
    selected = value.strip()
    if not selected:
        return None
    if selected.casefold() in {
        DISABLED_NOTIFICATION_BANNER.casefold(),
        NEWS_BANNER_DISABLED_LABEL.casefold(),
        "없음",
        "off",
        "disable",
        "disabled",
    }:
        return DISABLED_NOTIFICATION_BANNER
    for path in _banner_files():
        if selected.casefold() in {path.name.casefold(), path.stem.casefold()}:
            return path.name
    return None


def _banner_display_name(filename: str | None) -> str:
    resolved = _resolve_banner_filename(filename or DEFAULT_NOTIFICATION_BANNER)
    if resolved == DISABLED_NOTIFICATION_BANNER:
        return NEWS_BANNER_DISABLED_LABEL
    if resolved is None:
        return "없음"
    return Path(resolved).stem


def _banner_autocomplete_choices(current: str) -> list[app_commands.Choice[str]]:
    query = current.strip().casefold()
    choices: list[app_commands.Choice[str]] = []
    disabled_aliases = (
        NEWS_BANNER_DISABLED_LABEL.casefold(),
        DISABLED_NOTIFICATION_BANNER.casefold(),
        "없음",
    )
    if not query or any(query in alias for alias in disabled_aliases):
        choices.append(
            app_commands.Choice(
                name=NEWS_BANNER_DISABLED_LABEL,
                value=DISABLED_NOTIFICATION_BANNER,
            )
        )
    for path in _banner_files():
        if query and query not in path.stem.casefold() and query not in path.name.casefold():
            continue
        choices.append(app_commands.Choice(name=path.stem[:100], value=path.name[:100]))
        if len(choices) >= 25:
            break
    return choices


def _news_banner_file(filename: str | None) -> discord.File | None:
    resolved = _resolve_banner_filename(filename or DEFAULT_NOTIFICATION_BANNER)
    if resolved == DISABLED_NOTIFICATION_BANNER:
        return None
    if resolved is None:
        return None
    path = _resource_path(NEWS_BANNER_DIR / resolved)
    if not path.exists():
        return None
    return discord.File(path, filename=NEWS_BANNER_ATTACHMENT_NAME)


def _content_image_urls(post: NewsPost) -> list[str]:
    thumbnail_url = _thumbnail_url_for_post(post)
    return [
        url
        for url in _filter_image_urls(post.image_urls)
        if not thumbnail_url or url != thumbnail_url
    ]


def _downloadable_image_urls(post: NewsPost) -> list[str]:
    if _is_twitter_news_post(post):
        return _filter_image_urls(post.image_urls)
    return _content_image_urls(post)


def _brightenable_image_urls(post: NewsPost) -> list[str]:
    if not _is_twitter_news_post(post):
        return []
    return _filter_image_urls(post.image_urls)


def _thumbnail_url_for_post(post: NewsPost) -> str | None:
    raw_thumbnail = post.raw.get("thumbnail_url")
    if isinstance(raw_thumbnail, str) and raw_thumbnail:
        return raw_thumbnail

    image_urls = _filter_image_urls(post.image_urls)
    if _is_twitter_news_post(post) and len(image_urls) == 1:
        return image_urls[0]

    for url in image_urls:
        if _is_steam_card_thumbnail_url(url):
            return url
    return None


def _is_steam_card_thumbnail_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.netloc not in {"clan.fastly.steamstatic.com", "cdn.fastly.steamstatic.com"}:
        return False
    if any(fragment in url for fragment in LEGACY_STEAM_CARD_THUMBNAIL_FRAGMENTS):
        return True
    return bool(re.search(r"_(?:400x225|600x338)\.[A-Za-z0-9]+$", parsed.path))


def _standalone_image_urls(post: NewsPost, *, attach_images: bool) -> list[str]:
    urls = _content_image_urls(post)
    if not attach_images or not urls:
        return []
    return list(urls)


def _image_embed_batches_from_urls(
    image_urls: list[str], post: NewsPost
) -> list[list[discord.Embed]]:
    embeds: list[discord.Embed] = []
    for image_url in image_urls:
        embed = discord.Embed(url=post.url, color=_post_embed_color(post))
        embed.set_image(url=image_url)
        embeds.append(embed)

    return [
        embeds[index : index + IMAGE_ONLY_EMBEDS_PER_MESSAGE]
        for index in range(0, len(embeds), IMAGE_ONLY_EMBEDS_PER_MESSAGE)
    ]


def _split_message_content(text: str, limit: int) -> list[str]:
    chunks: list[str] = []
    current = ""
    for line in text.splitlines():
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= limit:
            current = candidate
            continue

        if current:
            chunks.append(current)
            current = ""

        while len(line) > limit:
            chunks.append(line[:limit])
            line = line[limit:]
        current = line

    if current:
        chunks.append(current)

    return chunks


def _description_for_post(post: NewsPost) -> str:
    body_text, tag_block = _display_body_and_trailing_tags(post)
    chunks = _split_message_content(
        body_text,
        EMBED_DESCRIPTION_LIMIT,
    )
    description = chunks[0] if chunks else post.url
    is_twitter = _is_twitter_news_post(post)
    date_block = (
        f"**작성일**\n{_format_kst(post.created_at)}\n\n"
        if post.created_at and not is_twitter
        else ""
    )
    if date_block and len(date_block) + len(description) <= EMBED_DESCRIPTION_LIMIT:
        description = f"{date_block}{description}"
    if is_twitter and tag_block:
        tag_block = f"{tag_block}\n\n"
        if len(tag_block) + len(description) <= EMBED_DESCRIPTION_LIMIT:
            description = f"{tag_block}{description}"
    source_block = "" if is_twitter else f"\n\n**출처**\n{_post_source_label(post)}"
    if len(description) + len(source_block) <= EMBED_DESCRIPTION_LIMIT:
        description = f"{description}{source_block}"
    return description


def _embed_groups_for_post(post: NewsPost) -> list[list[discord.Embed]]:
    fallback = discord.Embed(
        title=_display_title_for_post(post)[:256],
        description=_description_for_post(post),
        url=post.url,
        color=_post_embed_color(post),
    )
    if _is_twitter_news_post(post):
        footer = "출처: X(트위터)"
        if post.created_at is not None:
            footer = f"{footer} · 작성일: {_format_kst(post.created_at)}"
        fallback.set_footer(text=footer)
    return [[fallback]]


def _embeds_for_post(post: NewsPost) -> list[discord.Embed]:
    groups = _embed_groups_for_post(post)
    return groups[0] if groups else []

def _twitter_video_urls(post: TwitterPost) -> list[str]:
    value = post.raw.get("video_urls")
    return [str(u) for u in value] if isinstance(value, list) else []


def _twitter_video_url_groups(post: TwitterPost) -> list[list[str]]:
    groups = _twitter_video_url_groups_from_raw(post.raw)
    if groups:
        return groups
    return [[url] for url in _twitter_video_urls(post)]


def _twitter_video_url_groups_from_raw(raw: dict[str, object]) -> list[list[str]]:
    value = raw.get("video_variant_groups")
    if isinstance(value, list):
        groups = [
            [str(url) for url in group if url]
            for group in value
            if isinstance(group, list)
        ]
        groups = [group for group in groups if group]
        if groups:
            return groups
    value = raw.get("video_urls")
    urls = [str(u) for u in value] if isinstance(value, list) else []
    return [[url] for url in urls]


def _twitter_video_fallback_url(post: TwitterPost) -> str | None:
    return _twitter_video_fallback_url_from_raw(post.raw)


def _twitter_video_fallback_url_from_raw(raw: dict[str, object]) -> str | None:
    value = raw.get("video_fallback_url")
    return str(value) if value else None


def _select_twitter_video_url(urls: list[str]) -> str | None:
    if not urls:
        return None
    parsed = [(_twitter_video_resolution(url), url) for url in urls]
    for resolution, url in parsed:
        if resolution == (1920, 1080):
            return url
    with_resolution = [(resolution, url) for resolution, url in parsed if resolution is not None]
    if with_resolution:
        below_1080 = [
            (resolution, url)
            for resolution, url in with_resolution
            if resolution[1] <= 1080
        ]
        candidates = below_1080 or with_resolution
        return max(candidates, key=lambda item: item[0][0] * item[0][1])[1]
    return urls[0]


def _twitter_video_resolution(url: str) -> tuple[int, int] | None:
    match = re.search(r"/(\d{3,4})x(\d{3,4})/", url)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _is_payload_too_large(exc: discord.HTTPException) -> bool:
    code = getattr(exc, "code", None)
    status = getattr(exc, "status", None)
    return code == 40005 or status == 413


def _twitter_image_urls(post: TwitterPost) -> list[str]:
    return [
        url
        for url in post.image_urls
        if not _is_twitter_video_thumbnail_url(url)
    ]


def _twitter_original_image_url(url: str) -> str:
    parsed = urlparse(url)
    if not (parsed.hostname or "").endswith("twimg.com"):
        return url
    query = parse_qs(parsed.query, keep_blank_values=True)
    query["name"] = ["orig"]
    return parsed._replace(
        query="&".join(
            f"{quote(key)}={quote(value)}"
            for key, values in query.items()
            for value in values
        )
    ).geturl()


def _steam_original_image_url(url: str) -> str:
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    if hostname not in {
        "clan.fastly.steamstatic.com",
        "cdn.fastly.steamstatic.com",
        "steamcdn-a.akamaihd.net",
    }:
        return url

    path = re.sub(
        r"_(?:\d{2,5})x(?:\d{2,5})(?=\.[A-Za-z0-9]+$)",
        "",
        parsed.path,
    )
    query = parse_qs(parsed.query, keep_blank_values=True)
    for key in ("imw", "imh", "impolicy", "letterbox", "crop"):
        query.pop(key, None)
    return parsed._replace(
        path=path,
        query="&".join(
            f"{quote(key)}={quote(value)}"
            for key, values in query.items()
            for value in values
        ),
    ).geturl()


def _original_image_download_candidates(url: str) -> list[str]:
    if (urlparse(url).hostname or "").endswith("twimg.com"):
        original = _twitter_original_image_url(url)
    else:
        original = _steam_original_image_url(url)
    return list(dict.fromkeys([original, url]))


def _is_twitter_video_thumbnail_url(url: str) -> bool:
    lowered = url.lower()
    return any(
        fragment in lowered
        for fragment in (
            "/ext_tw_video_thumb/",
            "/amplify_video_thumb/",
            "/tweet_video_thumb/",
        )
    )


def _twitter_youtube_urls(post: TwitterPost) -> list[str]:
    value = post.raw.get("youtube_urls")
    return [str(u) for u in value] if isinstance(value, list) else []


def _twitter_link_urls(post: TwitterPost) -> list[str]:
    value = post.raw.get("link_urls")
    urls = [str(u) for u in value] if isinstance(value, list) else []
    urls = [url for url in urls if not _is_steam_news_url(url)]
    if not urls:
        urls = _twitter_youtube_urls(post)
    return list(dict.fromkeys(urls))


def _twitter_post_needs_refresh(post: TwitterPost) -> bool:
    if _looks_truncated_post_text(post.text) or _looks_truncated_post_text(post.title):
        return True
    if re.match(r"^RT @[^:\s]+:", post.text or "") and not post.raw.get("retweeted_tweet_id"):
        return True
    return False


def _looks_truncated_post_text(text: str) -> bool:
    cleaned = (text or "").rstrip()
    return cleaned.endswith("+...") or cleaned.endswith("…") or cleaned.endswith("...")


def _is_steam_news_url(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host != "store.steampowered.com":
        return False
    return parsed.path.lower().startswith("/news/app/")


def _steam_news_url_key(url: str) -> str | None:
    if not _is_steam_news_url(url):
        return None
    post_id = _steam_news_post_id_from_url(url)
    if post_id is not None:
        return f"steam-news:{post_id}"
    return urlparse(url).path.lower().rstrip("/")


def _embed_for_twitter_post(
    post: TwitterPost,
    *,
    image_url: str | None = None,
) -> discord.Embed:
    description, tag_block = _split_trailing_hashtag_block((post.text or post.url).strip())
    description = _strip_twitter_post_context_prefix(post, description)
    context_line = _twitter_post_context_line(post)
    if context_line:
        description = f"{context_line}\n\n{description or post.url}"
    description = _link_twitter_hashtags(description)
    tag_block = _link_twitter_hashtags(tag_block)
    meta_lines: list[str] = []
    if post.created_at is not None:
        meta_lines.append(f"**작성일**\n{_format_kst(post.created_at)}")
    if tag_block:
        meta_lines.append(tag_block)
    if meta_lines:
        meta_block = "\n".join(meta_lines)
        description = f"{meta_block}\n\n{description or post.url}"
    embed = discord.Embed(
        title=_display_title_for_twitter_post(post)[:256],
        description=_truncate_component_text(description or post.url, EMBED_DESCRIPTION_LIMIT),
        url=post.url,
        color=discord.Color.from_rgb(29, 155, 240),
    )
    if post.created_at is not None:
        embed.timestamp = post.created_at
        embed.set_footer(text="출처: X(트위터)")
    else:
        embed.set_footer(text="출처: X(트위터)")
    embed.set_author(name=f"@{post.author_username}", url=f"https://x.com/{post.author_username}")
    if image_url:
        embed.set_image(url=image_url)
    embed.add_field(name="원문", value=f"[X에서 보기]({post.url})", inline=False)
    return embed


def _embeds_for_twitter_post(
    post: TwitterPost,
    *,
    image_urls: list[str],
) -> list[discord.Embed]:
    inline_image_urls = image_urls[:MAX_TWITTER_EMBED_IMAGES]
    embeds = [
        _embed_for_twitter_post(
            post,
            image_url=inline_image_urls[0] if inline_image_urls else None,
        )
    ]
    for image_url in inline_image_urls[1:]:
        embed = discord.Embed(url=post.url, color=discord.Color.from_rgb(29, 155, 240))
        embed.set_image(url=image_url)
        embeds.append(embed)
    return embeds


def _display_title_for_twitter_post(post: TwitterPost) -> str:
    retweeted_username = str(post.raw.get("retweeted_username") or "").strip()
    if retweeted_username:
        return f"RT @{retweeted_username}"
    match = re.match(r"^RT @([^:\s]+):", post.title or post.text or "")
    if match:
        return f"RT @{match.group(1)}"
    return post.title.strip() or post.post_id


def _twitter_post_context_line(post: TwitterPost) -> str:
    language = str(post.raw.get("language") or "koreana")
    retweeted_username = str(post.raw.get("retweeted_username") or "").strip()
    if retweeted_username:
        return _news_ui_text(language, "retweet_context").format(username=retweeted_username)
    reply_username = str(post.raw.get("in_reply_to_screen_name") or "").strip()
    if reply_username:
        return _news_ui_text(language, "reply_context").format(username=reply_username)
    return ""


def _strip_twitter_post_context_prefix(post: TwitterPost, text: str) -> str:
    retweeted_username = str(post.raw.get("retweeted_username") or "").strip()
    if retweeted_username:
        pattern = rf"^\s*RT @{re.escape(retweeted_username)}:\s*"
        return re.sub(pattern, "", text, count=1).strip()
    return text


def _embed_for_chzzk_live(live: ChzzkLive) -> discord.Embed:
    embed = discord.Embed(
        title=live.title[:256],
        description=(
            f"{live.channel_name} 방송이 시작되었습니다.\n"
            "유튜브에서도 볼수 있어요!"
        ),
        url=PROJECT_MOON_CHZZK_LIVE_URL,
        color=discord.Color.from_rgb(0, 232, 149),
    )
    if live.category:
        embed.add_field(name="카테고리", value=live.category[:1024], inline=True)
    if live.image_url:
        embed.set_image(url=live.image_url)
    if live.open_date is not None:
        embed.timestamp = live.open_date.replace(tzinfo=KST)
        embed.set_footer(text=f"출처: CHZZK · 시작: {_format_kst(embed.timestamp)}")
    else:
        embed.set_footer(text="출처: CHZZK")
    embed.set_author(
        name=live.channel_name,
        url=PROJECT_MOON_CHZZK_LIVE_URL,
        icon_url=live.channel_image_url,
    )
    return embed


def _embed_for_chzzk_live_end() -> discord.Embed:
    embed = discord.Embed(
        title="ProjectMoon Official 방송이 종료되었습니다.",
        description="치지직 라이브가 종료되었습니다.\n다음 방송이 시작되면 다시 알려드릴게요.",
        url=PROJECT_MOON_CHZZK_LIVE_URL,
        color=discord.Color.dark_gray(),
    )
    embed.set_author(name="ProjectMoon Official", url=PROJECT_MOON_CHZZK_LIVE_URL)
    embed.set_footer(text="출처: CHZZK")
    return embed


def _embed_for_chzzk_offline(previous: ChzzkBroadcast | None = None) -> discord.Embed:
    embed = discord.Embed(
        title="ProjectMoon Official은 현재 오프라인 상태입니다.",
        description="현재 치지직 채널에 진행 중인 방송이 없어요.",
        url=PROJECT_MOON_CHZZK_LIVE_URL,
        color=discord.Color.dark_gray(),
    )
    if previous is not None:
        lines = [f"[{previous.title}]({PROJECT_MOON_CHZZK_LIVE_URL})"]
        if previous.open_date is not None:
            lines.append(f"시작: {_format_kst(previous.open_date.replace(tzinfo=KST))}")
        if previous.close_date is not None:
            lines.append(f"종료: {_format_kst(previous.close_date.replace(tzinfo=KST))}")
        embed.add_field(
            name="전에 하였던 방송",
            value="\n".join(lines)[:1024],
            inline=False,
        )
        if previous.image_url:
            embed.set_image(url=previous.image_url)
    embed.set_author(name="ProjectMoon Official", url=PROJECT_MOON_CHZZK_LIVE_URL)
    embed.set_footer(text="출처: CHZZK")
    return embed


def _embed_for_youtube_live(live: YoutubeLive) -> discord.Embed:
    embed = discord.Embed(
        title=live.title[:256],
        description="ProjectMoon Official 유튜브 라이브가 시작되었습니다.",
        url=live.url,
        color=discord.Color.from_rgb(255, 0, 0),
    )
    if live.thumbnail_url:
        embed.set_image(url=live.thumbnail_url)
    if live.start_time is not None:
        embed.timestamp = live.start_time
        embed.set_footer(text=f"출처: YouTube · 시작: {_format_kst(live.start_time)}")
    else:
        embed.set_footer(text="출처: YouTube")
    embed.set_author(name="ProjectMoon Official", url=PROJECT_MOON_YOUTUBE_STREAMS_URL)
    return embed


def _embed_for_youtube_upload(upload: YoutubeUpload) -> discord.Embed:
    embed = discord.Embed(
        title=upload.title[:256],
        description="ProjectMoon Official 유튜브 채널에 새 영상이 업로드되었습니다.",
        url=upload.url,
        color=discord.Color.from_rgb(255, 0, 0),
    )
    if upload.thumbnail_url:
        embed.set_image(url=upload.thumbnail_url)
    if upload.published_at is not None:
        embed.timestamp = upload.published_at
        embed.set_footer(text=f"출처: YouTube · 업로드: {_format_kst(upload.published_at)}")
    else:
        embed.set_footer(text="출처: YouTube")
    embed.set_author(name="ProjectMoon Official", url=PROJECT_MOON_YOUTUBE_VIDEOS_URL)
    return embed


def _embed_for_hampang_youtube_upload(upload: YoutubeUpload) -> discord.Embed:
    embed = _embed_for_youtube_upload(upload)
    embed.description = "ProjectMoon Official YouTube 채널에 햄햄팡팡 관련 영상이 업로드되었습니다."
    return embed


def _embed_for_youtube_offline(previous: YoutubeStream | None = None) -> discord.Embed:
    embed = discord.Embed(
        title="ProjectMoon Official 유튜브는 현재 오프라인 상태입니다.",
        description="현재 유튜브 채널에 진행 중인 라이브가 없어요.",
        url=PROJECT_MOON_YOUTUBE_STREAMS_URL,
        color=discord.Color.dark_gray(),
    )
    if previous is not None:
        embed.add_field(
            name="전에 하였던 방송",
            value=f"[{previous.title}]({previous.url})"[:1024],
            inline=False,
        )
        if previous.thumbnail_url:
            embed.set_image(url=previous.thumbnail_url)
    embed.set_author(name="ProjectMoon Official", url=PROJECT_MOON_YOUTUBE_STREAMS_URL)
    embed.set_footer(text="출처: YouTube")
    return embed


def _chzzk_live_view(
    youtube_url: str | None = None,
    *,
    include_youtube: bool = True,
) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(
        discord.ui.Button(
            label="CHZZK 바로가기",
            style=discord.ButtonStyle.link,
            url=PROJECT_MOON_CHZZK_LIVE_URL,
        )
    )
    if include_youtube and youtube_url:
        view.add_item(
            discord.ui.Button(
                label="YouTube 바로가기",
                style=discord.ButtonStyle.link,
                url=youtube_url,
            )
        )
    return view


def _youtube_live_view(
    youtube_url: str,
    *,
    include_chzzk: bool = False,
) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(
        discord.ui.Button(
            label="YouTube 바로가기",
            style=discord.ButtonStyle.link,
            url=youtube_url,
        )
    )
    if include_chzzk:
        view.add_item(
            discord.ui.Button(
                label="CHZZK 바로가기",
                style=discord.ButtonStyle.link,
                url=PROJECT_MOON_CHZZK_LIVE_URL,
            )
        )
    return view


def _youtube_upload_view(youtube_url: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(
        discord.ui.Button(
            label="영상 보러가기",
            style=discord.ButtonStyle.link,
            url=youtube_url,
        )
    )
    return view


def _build_layout_view_for_post(
    post: NewsPost,
    *,
    include_zip_button: bool,
    include_banner: bool,
    leading_text: str | None = None,
    is_update: bool = False,
    include_content_images: bool = True,
) -> discord.ui.LayoutView:
    from .bot_views import BrightenSpoilerButton, ZipDownloadButton

    view = discord.ui.LayoutView(timeout=None)
    container = discord.ui.Container(accent_color=_post_embed_color(post))
    language = _post_language(post)

    if is_update:
        container.add_item(discord.ui.TextDisplay(_news_ui_text(language, "updated")))
    if leading_text:
        container.add_item(discord.ui.TextDisplay(leading_text))

    if include_banner:
        banner_gallery = discord.ui.MediaGallery()
        banner_gallery.add_item(media=f"attachment://{NEWS_BANNER_ATTACHMENT_NAME}")
        container.add_item(banner_gallery)

    update_badge = _news_ui_text(language, "updated")
    overhead = (
        (len(update_badge) if is_update else 0)
        + (len(leading_text) if leading_text else 0)
    )
    body_limit = max(100, 4000 - overhead)

    body_text, tag_block = _display_body_and_trailing_tags(post)
    meta_block = _post_meta_block(post, tag_block=tag_block)
    container.add_item(
        discord.ui.TextDisplay(
            _truncate_component_text(
                f"## {_display_title_for_post(post).strip() or post.url}\n{meta_block}\n\n{body_text}",
                body_limit,
            )
        )
    )

    thumbnail_url = _thumbnail_url_for_post(post)
    if thumbnail_url:
        container.add_item(discord.ui.Separator())
        thumbnail_gallery = discord.ui.MediaGallery()
        thumbnail_gallery.add_item(media=thumbnail_url)
        container.add_item(thumbnail_gallery)

    if include_content_images:
        content_image_urls = _content_image_urls(post)[:MAX_INLINE_GALLERY_IMAGES]
        if content_image_urls:
            container.add_item(discord.ui.Separator())
            content_gallery = discord.ui.MediaGallery()
            for image_url in content_image_urls:
                content_gallery.add_item(media=image_url)
            container.add_item(content_gallery)

    container.add_item(discord.ui.Separator())
    container.add_item(discord.ui.TextDisplay(f"**출처**\n{_post_source_label(post)}"))

    action_row = discord.ui.ActionRow()
    if post.url:
        action_row.add_item(
            discord.ui.Button(
                label=_news_ui_text(language, "original"),
                style=discord.ButtonStyle.link,
                url=post.url,
            )
        )
    if include_zip_button and _downloadable_image_urls(post):
        action_row.add_item(ZipDownloadButton(post.post_id, language=language))
    if _brightenable_image_urls(post):
        action_row.add_item(
            BrightenSpoilerButton(post.post_id, image_index=0, language=language)
        )
    if action_row.children:
        container.add_item(discord.ui.Separator())
        container.add_item(action_row)

    view.add_item(container)
    return view


def _post_embed_color(post: NewsPost) -> discord.Color:
    if _is_twitter_news_post(post):
        return discord.Color.from_rgb(29, 155, 240)
    return discord.Color.from_rgb(179, 28, 28)


def _success_embed_color() -> discord.Color:
    return discord.Color.green()


def _news_update_notice_embed() -> discord.Embed:
    return discord.Embed(
        title="소식이 수정되었습니다!",
        description="소식 내용이 수정되었으니 다시 한번 내용을 확인해보세요!",
        color=discord.Color.orange(),
    )


def _truncate_component_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]
    return f"{text[: limit - 1]}…"


def _build_view_for_post(
    post: NewsPost,
    *,
    include_zip_button: bool,
) -> discord.ui.View | None:
    from .bot_views import BrightenSpoilerButton, ZipDownloadButton

    view = discord.ui.View(timeout=None)
    language = _post_language(post)
    if post.url:
        view.add_item(
            discord.ui.Button(
                label=_news_ui_text(language, "original"),
                style=discord.ButtonStyle.link,
                url=post.url,
            )
        )
    if include_zip_button and _downloadable_image_urls(post):
        view.add_item(ZipDownloadButton(post.post_id, language=language))
    if _brightenable_image_urls(post):
        view.add_item(
            BrightenSpoilerButton(post.post_id, image_index=0, language=language)
        )
    return view if view.children else None


def _current_maintenance_notice() -> tuple[str | None, str | None]:
    local_now = datetime.now(KST)
    if local_now.weekday() != MAINTENANCE_WEEKDAY:
        return None, None

    if local_now.hour == MAINTENANCE_START_HOUR:
        return "start", local_now.strftime("%Y-%m-%d:start")
    if local_now.hour == MAINTENANCE_UPDATE_HOUR:
        return "update", local_now.strftime("%Y-%m-%d:update")
    return None, None


def _maintenance_embed(
    title: str,
    description: str,
    *,
    color: discord.Color,
) -> discord.Embed:
    return discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.now(timezone.utc),
    )


def _language_label(language: str) -> str:
    return LANGUAGE_LABELS.get(language, language)


def _news_ui_text(language: str, key: str) -> str:
    language_text = (language or "koreana").strip()
    texts = NEWS_UI_TEXT.get(language_text) or NEWS_UI_TEXT["koreana"]
    return texts.get(key) or NEWS_UI_TEXT["koreana"][key]


def _format_news_targets(targets: list[GuildNewsTarget]) -> str:
    if not targets:
        return "미설정"

    lines: list[str] = []
    for language in SYNC_LANGUAGES:
        channels = [
            f"<#{target.channel_id}>"
            for target in targets
            if target.language == language
        ]
        if channels:
            lines.append(f"{_language_label(language)}: {', '.join(channels)}")

    extra_languages = sorted(
        {
            target.language
            for target in targets
            if target.language not in SYNC_LANGUAGES
        }
    )
    for language in extra_languages:
        channels = [
            f"<#{target.channel_id}>"
            for target in targets
            if target.language == language
        ]
        lines.append(f"{_language_label(language)}: {', '.join(channels)}")

    return "\n".join(lines) if lines else "미설정"


def _format_chzzk_target(target: GuildChzzkTarget | None, role_id: int | None) -> str:
    role_text = f"<@&{role_id}>" if role_id else "없음"
    if target is None:
        return (
            "상태: 미설정\n"
            "채널: 미설정\n"
            f"역할 핑: 시작 알림만 {role_text}\n"
            "최근 라이브 기준선: 없음"
        )

    return (
        f"상태: {'켜짐' if target.enabled else '꺼짐'}\n"
        f"채널: <#{target.channel_id}>\n"
        f"역할 핑: 시작 알림만 {role_text}\n"
        f"현재 방송 상태: {'방송중' if target.is_live else '방송 없음 / 오프라인'}\n"
        f"최근 라이브 기준선: {target.last_live_id or '없음'}"
    )


def _format_youtube_target(target: GuildYoutubeTarget | None, role_id: int | None) -> str:
    role_text = f"<@&{role_id}>" if role_id else "없음"
    if target is None:
        return (
            "상태: 미설정\n"
            "채널: 미설정\n"
            f"역할 핑: 시작 알림만 {role_text}\n"
            "최근 라이브 기준선: 없음"
        )

    return (
        f"상태: {'켜짐' if target.enabled else '꺼짐'}\n"
        f"채널: <#{target.channel_id}>\n"
        f"역할 핑: 시작 알림만 {role_text}\n"
        f"현재 방송 상태: {'방송중' if target.is_live else '방송 없음 / 오프라인'}\n"
        f"최근 라이브 기준선: {target.last_live_id or '없음'}"
    )


def _format_youtube_upload_target(
    target: GuildYoutubeUploadTarget | None,
    role_id: int | None,
) -> str:
    role_text = f"<@&{role_id}>" if role_id else "없음"
    if target is None:
        return (
            "업로드 알림 상태: 미설정\n"
            "채널: 미설정\n"
            f"역할 핑: {role_text}\n"
            "최근 일반 영상 기준선: 없음"
        )

    return (
        f"업로드 알림 상태: {'켜짐' if target.enabled else '꺼짐'}\n"
        f"채널: <#{target.channel_id}>\n"
        f"역할 핑: {role_text}\n"
        f"최근 일반 영상 기준선: {target.last_video_id or '없음'}"
    )


def _format_hampang_target(target: GuildHampangTarget | None, role_id: int | None) -> str:
    role_text = f"<@&{role_id}>" if role_id else "없음"
    if target is None:
        return (
            "자동 알림: 미설정\n"
            "채널: 미설정\n"
            f"역할 핑: {role_text}\n"
            "최근 X 기준선: 없음\n"
            "최근 YouTube 기준선: 없음"
        )

    return (
        f"자동 알림: {_bool_label(target.enabled)}\n"
        f"채널: <#{target.channel_id}>\n"
        f"역할 핑: {role_text}\n"
        f"최근 X 기준선: {target.last_x_post_id or '없음'}\n"
        f"최근 YouTube 기준선: {target.last_youtube_video_id or '없음'}"
    )


def _is_hampang_youtube_upload(upload: YoutubeUpload) -> bool:
    normalized_title = re.sub(r"[^a-z0-9]+", "", upload.title.casefold())
    return HAMPANG_YOUTUBE_TITLE_MARKER in normalized_title


def _regular_youtube_uploads(uploads: list[YoutubeUpload]) -> list[YoutubeUpload]:
    return [upload for upload in uploads if not _is_hampang_youtube_upload(upload)]


def _sort_twitter_posts_newest_first(posts: list[TwitterPost]) -> list[TwitterPost]:
    minimum = datetime.min.replace(tzinfo=timezone.utc)
    return sorted(
        posts,
        key=lambda post: _as_utc_datetime(post.created_at) or minimum,
        reverse=True,
    )


def _sort_youtube_uploads_newest_first(
    uploads: list[YoutubeUpload],
) -> list[YoutubeUpload]:
    minimum = datetime.min.replace(tzinfo=timezone.utc)
    return sorted(
        uploads,
        key=lambda upload: _as_utc_datetime(upload.published_at) or minimum,
        reverse=True,
    )


def _hampang_news_items(
    x_posts: list[TwitterPost],
    youtube_uploads: list[YoutubeUpload],
    *,
    newest_first: bool = True,
) -> list[tuple[str, TwitterPost | YoutubeUpload]]:
    items: list[tuple[str, TwitterPost | YoutubeUpload]] = [
        *(("x", post) for post in x_posts),
        *(("youtube", upload) for upload in youtube_uploads),
    ]
    minimum = datetime.min.replace(tzinfo=timezone.utc)

    def item_time(item: tuple[str, TwitterPost | YoutubeUpload]) -> datetime:
        source, value = item
        moment = value.created_at if source == "x" else value.published_at
        return _as_utc_datetime(moment) or minimum

    return sorted(items, key=item_time, reverse=newest_first)


def _hampang_news_items_for_source(
    x_posts: list[TwitterPost],
    youtube_uploads: list[YoutubeUpload],
    source: str,
) -> list[tuple[str, TwitterPost | YoutubeUpload]]:
    selected_x_posts = x_posts if source in {HAMPANG_SOURCE_BOTH, HAMPANG_SOURCE_X} else []
    selected_youtube_uploads = (
        youtube_uploads
        if source in {HAMPANG_SOURCE_BOTH, HAMPANG_SOURCE_YOUTUBE}
        else []
    )
    return _hampang_news_items(selected_x_posts, selected_youtube_uploads)


def _hampang_choice_name(
    source: str,
    item: TwitterPost | YoutubeUpload,
) -> str:
    source_label = "X" if source == HAMPANG_SOURCE_X else "YouTube"
    title = item.title.strip() or (
        item.post_id if isinstance(item, TwitterPost) else item.video_id
    )
    prefix = f"[{source_label}] "
    max_title_length = max(1, 100 - len(prefix))
    if len(title) <= max_title_length:
        return f"{prefix}{title}"
    if max_title_length <= 3:
        return f"{prefix}{title[:max_title_length]}"
    return f"{prefix}{title[: max_title_length - 3]}..."


def _hampang_choice_description(
    source: str,
    item: TwitterPost | YoutubeUpload,
) -> str:
    moment = item.created_at if source == HAMPANG_SOURCE_X else item.published_at
    if moment is None:
        return "작성 시간을 확인할 수 없어요."
    return _format_kst(moment)




def _choice_bool(choice: app_commands.Choice[str] | None, default: bool | None = None) -> bool | None:
    if choice is None:
        return default
    return choice.value == BOOLEAN_TRUE


def _broadcast_source_value(choice: app_commands.Choice[str] | None) -> str:
    return choice.value if choice is not None else BROADCAST_SOURCE_BOTH


def _broadcast_source_allows_chzzk(value: str) -> bool:
    return value in {BROADCAST_SOURCE_BOTH, BROADCAST_SOURCE_CHZZK}


def _broadcast_source_allows_youtube(value: str) -> bool:
    return value in {BROADCAST_SOURCE_BOTH, BROADCAST_SOURCE_YOUTUBE}


def _broadcast_source_label(value: str) -> str:
    if value == BROADCAST_SOURCE_CHZZK:
        return "치지직"
    if value == BROADCAST_SOURCE_YOUTUBE:
        return "유튜브"
    return "치지직 & 유튜브"


def _news_target_choice_value(channel_id: int, language: str) -> str:
    return f"{channel_id}:{language}"


def _parse_news_target_choice(value: str) -> tuple[int, str] | None:
    channel_id_text, separator, language = value.partition(":")
    if not separator or not channel_id_text.isdigit() or not language:
        return None
    return int(channel_id_text), language


def _broadcast_target_choice_name(
    label: str,
    channel_id: int,
    enabled: bool,
    interaction: discord.Interaction,
) -> str:
    channel = interaction.guild.get_channel(channel_id) if interaction.guild else None
    channel_name = f"#{channel.name}" if isinstance(channel, discord.TextChannel) else f"채널 {channel_id}"
    enabled_text = "켜짐" if enabled else "꺼짐"
    return f"{label} · {channel_name} · {enabled_text}"[:100]


def _bool_label(value: bool) -> str:
    return "허용" if value else "비허용"


def _image_delivery_label(value: str | None) -> str:
    if value == IMAGE_DELIVERY_FILES:
        return "첨부파일로 따로 전송"
    return "임베드에 이미지 표시"


def _youtube_links_content(post: NewsPost) -> str | None:
    links = _youtube_urls_for_post(post)
    raw_links = post.raw.get("link_urls")
    if isinstance(raw_links, list):
        links.extend(str(url) for url in raw_links if url and not _is_steam_news_url(str(url)))
    links = list(dict.fromkeys(links))[:3]
    return "\n".join(links) if links else None


def _youtube_urls_for_post(post: NewsPost) -> list[str]:
    value = post.raw.get("youtube_urls")
    if not isinstance(value, list):
        return []

    return [str(url) for url in value if url]


def _is_twitter_news_post(post: NewsPost) -> bool:
    return str(post.raw.get("source_type") or "").lower() == NEWS_SOURCE_TWITTER


def _post_source_label(post: NewsPost) -> str:
    return "X(트위터)" if _is_twitter_news_post(post) else "Steam"


def _display_title_for_post(post: NewsPost) -> str:
    title = post.title.strip() or post.post_id
    if _is_twitter_news_post(post):
        retweeted_username = str(post.raw.get("retweeted_username") or "").strip()
        if retweeted_username:
            title = f"RT @{retweeted_username}"
        else:
            match = re.match(r"^RT @([^:\s]+):", title or post.text)
            if match:
                title = f"RT @{match.group(1)}"
    return f"[{_post_source_label(post)}] {title}"


def _display_body_and_trailing_tags(post: NewsPost) -> tuple[str, str]:
    body = (post.text or post.url).strip()
    if not _is_twitter_news_post(post):
        return body, ""
    body, tag_block = _split_trailing_hashtag_block(body)
    body = _strip_twitter_context_prefix(post, body)
    context_line = _twitter_context_line(post)
    if context_line:
        body = f"{context_line}\n\n{body or post.url}"
    return _link_twitter_hashtags(body or post.url), _link_twitter_hashtags(tag_block)


def _twitter_context_line(post: NewsPost) -> str:
    language = _post_language(post) or "koreana"
    retweeted_username = str(post.raw.get("retweeted_username") or "").strip()
    if retweeted_username:
        return _news_ui_text(language, "retweet_context").format(username=retweeted_username)
    reply_username = str(post.raw.get("in_reply_to_screen_name") or "").strip()
    if reply_username:
        return _news_ui_text(language, "reply_context").format(username=reply_username)
    return ""


def _strip_twitter_context_prefix(post: NewsPost, text: str) -> str:
    retweeted_username = str(post.raw.get("retweeted_username") or "").strip()
    if retweeted_username:
        pattern = rf"^\s*RT @{re.escape(retweeted_username)}:\s*"
        return re.sub(pattern, "", text, count=1).strip()
    return text


def _post_date_line(post: NewsPost) -> str:
    if post.created_at is None:
        return ""
    return f"-# 작성일: {_format_kst(post.created_at)}"


def _post_meta_block(post: NewsPost, *, tag_block: str = "") -> str:
    lines = []
    date_line = _post_date_line(post)
    if date_line:
        lines.append(date_line)
    if tag_block:
        lines.append(tag_block)
    return "\n".join(lines)


def _split_trailing_hashtag_block(text: str) -> tuple[str, str]:
    lines = text.rstrip().splitlines()
    tag_lines: list[str] = []
    while lines:
        line = lines[-1].strip()
        if not line:
            if tag_lines:
                lines.pop()
                continue
            break
        if not _is_hashtag_only_line(line):
            break
        tag_lines.append(line)
        lines.pop()
    if not tag_lines:
        return text.strip(), ""
    tag_lines.reverse()
    return "\n".join(lines).strip(), "\n".join(tag_lines).strip()


def _is_hashtag_only_line(line: str) -> bool:
    tokens = line.split()
    return bool(tokens) and all(re.fullmatch(r"#[^\s#]+", token) for token in tokens)


def _link_twitter_hashtags(text: str) -> str:
    if not text:
        return text

    def replace(match: re.Match[str]) -> str:
        tag = match.group(1)
        encoded = quote(tag, safe="")
        return f"[#{tag}](https://x.com/hashtag/{encoded})"

    return re.sub(r"(?<![\w/\]])#([^\s#]+)", replace, text)


def _news_source_mode_label(mode: str | None) -> str:
    if mode == NEWS_SOURCE_STEAM:
        return "Steam"
    if mode == NEWS_SOURCE_TWITTER:
        return "X(트위터)"
    return "둘 다"


def _selected_source_mode(interaction: discord.Interaction) -> str:
    source = getattr(interaction.namespace, "source", None)
    if isinstance(source, app_commands.Choice):
        return str(source.value)
    if source in {NEWS_SOURCE_STEAM, NEWS_SOURCE_TWITTER}:
        return str(source)
    data_source = _selected_source_mode_from_options(
        (interaction.data or {}).get("options") if isinstance(interaction.data, dict) else None
    )
    if data_source is not None:
        return data_source
    return NEWS_SOURCE_STEAM


def _selected_source_mode_from_options(options: object) -> str | None:
    if not isinstance(options, list):
        return None
    for option in options:
        if not isinstance(option, dict):
            continue
        name = str(option.get("name") or "")
        value = option.get("value")
        if name in {"source", "소스"} and value in {NEWS_SOURCE_STEAM, NEWS_SOURCE_TWITTER}:
            return str(value)
        nested = _selected_source_mode_from_options(option.get("options"))
        if nested is not None:
            return nested
    return None


def _sort_posts_newest_first(posts: list[NewsPost]) -> list[NewsPost]:
    return sorted(
        posts,
        key=lambda post: post.created_at or datetime.min.replace(tzinfo=timezone.utc),
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


def _recent_auto_posts(posts: list[NewsPost]) -> list[NewsPost]:
    return [post for post in posts if post.created_at is not None]


def _is_news_update_recent(post: NewsPost) -> bool:
    moment = _as_utc_datetime(post.created_at)
    if moment is None:
        return False
    age = (datetime.now(timezone.utc) - moment).total_seconds()
    return age <= NEWS_UPDATE_MAX_AGE_SECONDS


def _delay_seconds(value: datetime | None) -> int | str:
    if value is None:
        return "unknown"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - value.astimezone(timezone.utc)).total_seconds()))


def _post_delay_seconds(post: NewsPost) -> int | str:
    return _delay_seconds(post.created_at)


def _minute_in_window(minute: int, start: int, end: int) -> bool:
    if start == end:
        return False
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end


def _format_windows_label(windows: tuple[tuple[int, int], ...]) -> str:
    def hhmm(m: int) -> str:
        return f"{m // 60:02d}:{m % 60:02d}"

    return ", ".join(f"{hhmm(s)}-{hhmm(e)}" for s, e in windows) or "(없음)"


def _is_twitter_post_recent(post: TwitterPost, max_age_seconds: int) -> bool:
    if max_age_seconds <= 0:
        return True
    created = post.created_at
    if created is None:
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - created).total_seconds()
    return age <= max_age_seconds


def _is_twitter_news_post_recent(post: NewsPost, max_age_seconds: int) -> bool:
    if max_age_seconds <= 0:
        return True
    created = _as_utc_datetime(post.created_at)
    if created is None:
        return False
    age = (datetime.now(timezone.utc) - created).total_seconds()
    return age <= max_age_seconds


def _twitter_post_delay_seconds(post: TwitterPost) -> int | str:
    return _delay_seconds(post.created_at)


def _as_utc_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _matching_steam_posts_for_twitter(
    post: TwitterPost,
    steam_posts: list[NewsPost],
) -> list[NewsPost]:
    link_urls = _raw_link_urls(post.raw)
    link_keys = _steam_news_link_keys_for_twitter(post)
    twitter_candidates = _news_body_match_candidates(post.text)
    matched: list[NewsPost] = []
    seen: set[str] = set()
    for steam_post in steam_posts:
        if not _twitter_matches_steam_news(
            steam_post,
            link_urls,
            link_keys,
            twitter_candidates,
        ):
            continue
        if steam_post.post_id in seen:
            continue
        seen.add(steam_post.post_id)
        matched.append(steam_post)
    return matched


def _twitter_matches_steam_news(
    steam_post: NewsPost,
    link_urls: list[str],
    link_keys: set[str],
    twitter_candidates: set[str],
) -> bool:
    steam_key = _steam_news_url_key(steam_post.url)
    link_matches = steam_post.url in link_urls or (
        steam_key is not None and steam_key in link_keys
    )
    if steam_key is not None and steam_key in link_keys:
        return True
    if steam_post.url in link_urls:
        return True
    content_matches = _news_match_candidates_overlap(
        twitter_candidates,
        {
            *_news_body_match_candidates(steam_post.title),
            *_news_body_match_candidates(steam_post.text),
        },
    )
    return link_matches and content_matches


def _raw_link_urls(raw: dict) -> list[str]:
    link_urls = raw.get("link_urls")
    if not isinstance(link_urls, list):
        return []
    return [str(url) for url in link_urls if url]


def _news_body_match_candidates(text: str) -> set[str]:
    values: list[str] = []
    line_count = 0
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        cleaned = line.strip()
        lowered = cleaned.lower()
        if (
            not cleaned
            or cleaned.startswith("#")
            or lowered.startswith("http://")
            or lowered.startswith("https://")
        ):
            continue
        values.append(cleaned)
        values.extend(
            bracketed.strip()
            for bracketed in re.findall(r"[\[【「『](.*?)[\]】」』]", cleaned)
            if bracketed.strip()
        )
        line_count += 1
        if line_count >= 4:
            break

    candidates: set[str] = set()
    for value in values:
        normalized = _normalize_news_match_text(value)
        if len(normalized.replace(" ", "")) >= 10:
            candidates.add(normalized)
    return candidates


def _normalize_news_match_text(value: str) -> str:
    value = re.sub(r"https?://\S+", " ", value)
    value = re.sub(r"#\S+", " ", value)
    value = re.sub(r"[\[【「『](.*?)[\]】」』]", r" \1 ", value)
    value = "".join(
        character if character.isalnum() or character == "_" else " "
        for character in value.casefold()
    )
    return re.sub(r"\s+", " ", value).strip()


def _news_match_candidates_overlap(left: set[str], right: set[str]) -> bool:
    if not left or not right:
        return False
    if left & right:
        return True

    left_compact = {value.replace(" ", "") for value in left}
    right_compact = {value.replace(" ", "") for value in right}
    for left_value in left_compact:
        for right_value in right_compact:
            shorter, longer = sorted((left_value, right_value), key=len)
            if len(shorter) >= 14 and shorter in longer:
                return True
    return False


def _steam_news_link_keys_for_twitter(post: TwitterPost) -> set[str]:
    return {
        key
        for key in (_steam_news_url_key(url) for url in _raw_link_urls(post.raw))
        if key is not None
    }


def _steam_news_post_id_from_url(url: str) -> str | None:
    if not _is_steam_news_url(url):
        return None
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 5 and parts[0].lower() == "news" and parts[1].lower() == "app":
        if parts[3].lower() == "view" and parts[4]:
            return parts[4]
    emgid = parse_qs(parsed.query).get("emgid")
    if emgid:
        return emgid[0]
    return None


def _steam_news_post_ids_for_twitter_posts(posts: list[TwitterPost]) -> list[str]:
    post_ids: list[str] = []
    seen: set[str] = set()
    for post in posts:
        raw_links = post.raw.get("link_urls")
        if not isinstance(raw_links, list):
            continue
        for url in raw_links:
            post_id = _steam_news_post_id_from_url(str(url))
            if post_id is None or post_id in seen:
                continue
            seen.add(post_id)
            post_ids.append(post_id)
    return post_ids


def _steam_posts_without_fast_twitter_duplicates(
    steam_posts: list[NewsPost],
    twitter_news: list[NewsPost],
) -> list[NewsPost]:
    skip_ids: set[str] = set()
    skip_keys: set[str] = set()
    for post in twitter_news:
        prefer_ids = _raw_string_set(post.raw.get("prefer_steam_post_ids"))
        prefer_keys = _raw_string_set(post.raw.get("prefer_steam_post_keys"))
        raw_ids = post.raw.get("overlap_steam_post_ids")
        if isinstance(raw_ids, list):
            skip_ids.update(
                str(post_id)
                for post_id in raw_ids
                if post_id and str(post_id) not in prefer_ids
            )
        raw_keys = post.raw.get("overlap_steam_post_keys")
        if isinstance(raw_keys, list):
            skip_keys.update(
                str(post_key)
                for post_key in raw_keys
                if post_key and str(post_key) not in prefer_keys
            )
    if not skip_ids and not skip_keys:
        return steam_posts
    return [
        post
        for post in steam_posts
        if post.post_id not in skip_ids
        and ((_post_language_independent_id(post) or "") not in skip_keys)
    ]


def _twitter_posts_as_news_posts(
    posts: list[TwitterPost],
    steam_posts: list[NewsPost],
) -> list[NewsPost]:
    converted: list[NewsPost] = []
    for post in posts:
        raw = dict(post.raw)
        matching_steam_posts = _matching_steam_posts_for_twitter(post, steam_posts)
        if matching_steam_posts:
            raw["overlap_steam_post_ids"] = [
                steam_post.post_id for steam_post in matching_steam_posts
            ]
            raw["overlap_steam_post_keys"] = [
                post_key
                for post_key in (
                    _post_language_independent_id(steam_post)
                    for steam_post in matching_steam_posts
                )
                if post_key is not None
            ]
            raw["prefer_steam_post_ids"] = [
                steam_post.post_id for steam_post in matching_steam_posts
            ]
            raw["prefer_steam_post_keys"] = [
                post_key
                for post_key in (
                    _post_language_independent_id(steam_post)
                    for steam_post in matching_steam_posts
                )
                if post_key is not None
            ]
        raw["source_type"] = NEWS_SOURCE_TWITTER
        raw["language"] = "koreana"
        converted.append(
            NewsPost(
                post_id=f"twitter:{post.post_id}",
                source_user=post.author_username,
                url=post.url,
                text=post.text,
                title=post.title,
                created_at=post.created_at,
                image_urls=_twitter_image_urls(post),
                raw=raw,
            )
        )
    return converted


def _twitter_news_without_duplicate_steam_links(posts: list[NewsPost]) -> list[NewsPost]:
    selected_by_key: dict[str, NewsPost] = {}
    for post in posts:
        link_keys = _steam_news_link_keys_for_news_post(post)
        if not link_keys:
            continue
        for link_key in link_keys:
            selected = selected_by_key.get(link_key)
            if selected is None or _news_post_is_earlier(post, selected):
                selected_by_key[link_key] = post

    if not selected_by_key:
        return posts

    selected_ids = {post.post_id for post in selected_by_key.values()}
    deduped: list[NewsPost] = []
    seen_ids: set[str] = set()
    for post in posts:
        link_keys = _steam_news_link_keys_for_news_post(post)
        if not link_keys:
            deduped.append(post)
            continue
        if post.post_id not in selected_ids or post.post_id in seen_ids:
            continue
        seen_ids.add(post.post_id)
        deduped.append(post)
    return deduped


def _steam_news_link_keys_for_news_post(post: NewsPost) -> set[str]:
    raw_links = post.raw.get("link_urls")
    if not isinstance(raw_links, list):
        return set()
    return {
        key
        for key in (_steam_news_url_key(str(url)) for url in raw_links if url)
        if key is not None
    }


def _news_post_is_earlier(left: NewsPost, right: NewsPost) -> bool:
    left_at = _as_utc_datetime(left.created_at)
    right_at = _as_utc_datetime(right.created_at)
    if left_at is not None and right_at is not None and left_at != right_at:
        return left_at < right_at
    if left_at is not None and right_at is None:
        return True
    if left_at is None and right_at is not None:
        return False
    return left.post_id < right.post_id


def _raw_string_set(value: object) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item) for item in value if item}


def _twitter_news_prefers_available_steam(
    post: NewsPost,
    available_posts: list[NewsPost],
) -> bool:
    if not _is_twitter_news_post(post):
        return False
    prefer_ids = _raw_string_set(post.raw.get("prefer_steam_post_ids"))
    prefer_keys = _raw_string_set(post.raw.get("prefer_steam_post_keys"))
    if not prefer_ids and not prefer_keys:
        return False
    for available_post in available_posts:
        if _is_twitter_news_post(available_post):
            continue
        if available_post.post_id in prefer_ids:
            return True
        post_key = _post_language_independent_id(available_post)
        if post_key is not None and post_key in prefer_keys:
            return True
    return False


def _schedule_text_for_post(post: NewsPost) -> str | None:
    start = _datetime_from_raw_timestamp(post.raw.get("starts_at"))
    end = _datetime_from_raw_timestamp(post.raw.get("ends_at"))
    if start is None:
        return None

    text = _format_kst(start)
    if end is not None and end > start:
        text = f"{text} - {_format_kst(end)}"
    return text


def _datetime_from_raw_timestamp(value: object) -> datetime | None:
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return None

    if timestamp <= 0:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


def _format_kst(value: datetime) -> str:
    dt = value.astimezone(KST)
    ampm = "오전" if dt.hour < 12 else "오후"
    hour = dt.hour % 12 or 12
    return f"{dt.year}년 {dt.month}월 {dt.day}일 {ampm} {hour}시 {dt.minute:02d}분"

def _is_chzzk_live_too_old(live: ChzzkLive) -> bool:
    if live.open_date is None:
        return False
    opened_at = live.open_date
    if opened_at.tzinfo is None:
        opened_at = opened_at.replace(tzinfo=KST)
    return datetime.now(KST) - opened_at.astimezone(KST) >= CHZZK_LIVE_ANNOUNCE_MAX_AGE


def _is_youtube_live_too_old(live: YoutubeLive) -> bool:
    if live.start_time is None:
        return False
    return datetime.now(KST) - live.start_time.astimezone(KST) >= YOUTUBE_LIVE_ANNOUNCE_MAX_AGE


def _is_chzzk_live_recently_closed(
    live_detail: dict[str, object] | None,
    last_live_id: str | None,
) -> bool:
    if not isinstance(live_detail, dict):
        return False
    live_id = live_detail.get("liveId")
    if live_id is None or str(live_id) != str(last_live_id):
        return False
    status = str(live_detail.get("status") or "").upper()
    if status and status not in {"CLOSE", "ENDED"}:
        return False
    close_date = _parse_chzzk_datetime(live_detail.get("closeDate"))
    if close_date is None:
        return False
    return datetime.now(KST) - close_date.astimezone(KST) < CHZZK_LIVE_END_ANNOUNCE_MAX_AGE


def _parse_chzzk_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return parsed.replace(tzinfo=KST)


def _choice_name(
    post: NewsPost,
    *,
    include_language: bool = False,
    include_source: bool = True,
) -> str:
    title = _display_title_for_post(post) if include_source else (post.title.strip() or post.post_id)
    prefix = ""
    if post.created_at:
        prefix = f"[{_format_kst(post.created_at)}] "
    if include_language:
        language = _post_language(post)
        if language:
            prefix = f"{prefix}[{_language_label(language)}] "

    max_title_length = max(1, 100 - len(prefix))
    if len(title) <= max_title_length:
        return f"{prefix}{title}"

    if max_title_length <= 3:
        return f"{prefix}{title[:max_title_length]}"
    return f"{prefix}{title[: max_title_length - 3]}..."


def _twitter_choice_name(post: TwitterPost) -> str:
    title = post.title.strip() or post.post_id
    if post.created_at:
        prefix = f"[{_format_kst(post.created_at)}] "
    else:
        prefix = ""
    max_title_length = max(1, 100 - len(prefix))
    if len(title) <= max_title_length:
        return f"{prefix}{title}"
    if max_title_length <= 3:
        return f"{prefix}{title[:max_title_length]}"
    return f"{prefix}{title[: max_title_length - 3]}..."


def _post_language(post: NewsPost) -> str:
    raw_language = post.raw.get("language")
    if raw_language:
        return str(raw_language)

    parts = post.post_id.split(":", 2)
    if len(parts) == 3 and parts[0] == "steam":
        return parts[1]

    return ""


def _post_language_independent_id(post: NewsPost) -> str | None:
    parts = post.post_id.split(":", 2)
    if len(parts) == 3 and parts[0] == "steam":
        return parts[2]

    raw_id = post.raw.get("event_gid")
    if raw_id:
        return str(raw_id)

    return None


_UNSAFE_FILENAME_RE = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def _unique_zip_name(
    used_names: set[str], index: int, url: str, content_type: str | None, *, native: bool = False
) -> str:
    extension = _image_file_extension(url, content_type, native=native)
    candidate = f"소식_이미지_({index + 1}){extension}"
    counter = 2
    while candidate in used_names:
        candidate = f"소식_이미지_({index + 1}_{counter}){extension}"
        counter += 1
    used_names.add(candidate)
    return candidate


def _image_file_extension(url: str, content_type: str | None, *, native: bool = False) -> str:
    if content_type:
        normalized = content_type.split(";", 1)[0].strip().lower()
        _NATIVE_EXT = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/gif": ".gif",
            "image/webp": ".webp",
        }
        if native:
            if normalized in _NATIVE_EXT:
                return _NATIVE_EXT[normalized]
        else:
            if normalized in _NATIVE_EXT or normalized == "image/bmp":
                return ".png"

    suffix = urlparse(url).path.rsplit("/", 1)[-1].lower().rsplit(".", 1)
    if len(suffix) == 2:
        extension = f".{suffix[1]}"
        if native:
            if extension in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
                return ".jpg" if extension in {".jpg", ".jpeg"} else extension
        else:
            if extension in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}:
                return ".png"

    return ".img"


def _image_bytes_as_png(
    data: bytes, content_type: str | None
) -> tuple[bytes, str | None]:
    normalized = (content_type or "").split(";", 1)[0].strip().lower()
    if normalized == "image/png":
        return data, "image/png"

    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
            output = io.BytesIO()
            image.save(output, format="PNG", optimize=True)
            return output.getvalue(), "image/png"
    except (UnidentifiedImageError, OSError):
        LOGGER.warning("이미지를 PNG로 변환하지 못해 원본으로 보냅니다.")
        return data, content_type


EGO_GIFT_IMAGE_MAX_SIZE = 150


def _resize_image_to_fit(data: bytes, max_size: int) -> bytes:
    """비율을 유지한 채 긴 변이 max_size가 되도록 리사이즈한다.

    가로/세로 비율이 달라도 왜곡되지 않으며, max_size x max_size 박스 안에 들어간다.
    """
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
            width, height = image.size
            longest = max(width, height)
            if longest == 0:
                return data
            scale = max_size / longest
            new_size = (max(1, round(width * scale)), max(1, round(height * scale)))
            resized = image.resize(new_size, Image.LANCZOS)
            output = io.BytesIO()
            resized.save(output, format="PNG", optimize=True)
            return output.getvalue()
    except (UnidentifiedImageError, OSError):
        LOGGER.warning("에고 기프트 이미지 크기 조정에 실패해 원본 크기로 보냅니다.")
        return data


def _process_ego_gift_image_bytes(data: bytes, content_type: str | None) -> bytes:
    """에고 기프트 첨부 이미지를 PNG로 변환하고 150px 박스에 맞춰 리사이즈한다.

    CPU 바운드 작업이므로 asyncio.to_thread로 호출한다.
    """
    data, content_type = _image_bytes_as_png(data, content_type)
    return _resize_image_to_fit(data, EGO_GIFT_IMAGE_MAX_SIZE)


@dataclass(frozen=True)
class _DcRecoverParams:
    gamma: float
    red_gain: float
    green_gain: float
    blue_gain: float
    saturation: float
    contrast: float
    brightness: float
    highlight_strength: float
    red_shadow_boost: float
    clahe_clip: float
    sharpen: float
    shadow_deblock: float
    shadow_detail: float
    shadow_sharpen: float


_DC_RECOVER_PARAMS = _DcRecoverParams(
    gamma=0.48,
    red_gain=1.22,
    green_gain=1.15,
    blue_gain=1.10,
    saturation=1.10,
    contrast=1.00,
    brightness=0.0,
    highlight_strength=0.18,
    red_shadow_boost=0.020,
    clahe_clip=0.22,
    sharpen=0.00,
    shadow_deblock=0.66,
    shadow_detail=0.34,
    shadow_sharpen=0.24,
)
_DC_CLAHE_TILE_GRID = (8, 8)
_DC_SHADOW_MASK_LOW = 0.08
_DC_SHADOW_MASK_HIGH = 0.58
_DC_EDGE_PROTECT_LOW = 3.0
_DC_EDGE_PROTECT_HIGH = 18.0


def _smoothstep(edge0: float, edge1: float, value: "np.ndarray") -> "np.ndarray":
    x = np.clip((value - edge0) / (edge1 - edge0), 0.0, 1.0)
    return x * x * (3.0 - (2.0 * x))


def _dc_luminance(rgb: "np.ndarray") -> "np.ndarray":
    image = rgb.astype(np.float32) / 255.0
    return (
        0.2126 * image[..., 0]
        + 0.7152 * image[..., 1]
        + 0.0722 * image[..., 2]
    )


def _dc_shadow_weight(rgb: "np.ndarray") -> "np.ndarray":
    luminance = _dc_luminance(rgb)
    weight = 1.0 - _smoothstep(_DC_SHADOW_MASK_LOW, _DC_SHADOW_MASK_HIGH, luminance)
    return np.clip(weight, 0.0, 1.0) ** 1.35


def _dc_edge_weight(rgb: "np.ndarray") -> "np.ndarray":
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    edges = np.abs(cv2.Laplacian(gray, cv2.CV_32F, ksize=3))
    return _smoothstep(_DC_EDGE_PROTECT_LOW, _DC_EDGE_PROTECT_HIGH, edges)


def _apply_dc_gamma_and_gain(
    rgb: "np.ndarray",
    params: _DcRecoverParams,
) -> "np.ndarray":
    image = rgb.astype(np.float32) / 255.0
    image = np.power(np.clip(image, 0.0, 1.0), params.gamma)

    gains = np.array(
        [params.red_gain, params.green_gain, params.blue_gain],
        dtype=np.float32,
    ).reshape(1, 1, 3)
    image = image * 255.0 * gains
    image = (image - 127.5) * params.contrast + 127.5 + params.brightness
    return np.clip(image, 0.0, 255.0).astype(np.uint8)


def _boost_dc_red_in_shadows(
    rgb: "np.ndarray",
    strength: float,
) -> "np.ndarray":
    if strength <= 0.0:
        return rgb

    image = rgb.astype(np.float32) / 255.0
    red = image[..., 0]
    green = image[..., 1]
    blue = image[..., 2]
    luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue

    shadow_weight = np.clip(1.0 - luminance, 0.0, 1.0) ** 2
    image[..., 0] += shadow_weight * strength
    return np.clip(image * 255.0, 0.0, 255.0).astype(np.uint8)


def _boost_dc_highlights(
    rgb: "np.ndarray",
    strength: float,
) -> "np.ndarray":
    if strength <= 0.0:
        return rgb

    image = rgb.astype(np.float32) / 255.0
    value = np.max(image, axis=2)
    highlight_weight = np.clip((value - 0.65) / 0.35, 0.0, 1.0)[..., None]
    image = image + (1.0 - image) * highlight_weight * strength
    return np.clip(image * 255.0, 0.0, 255.0).astype(np.uint8)


def _deblock_dc_shadows(
    original_rgb: "np.ndarray",
    lifted_rgb: "np.ndarray",
    strength: float,
) -> "np.ndarray":
    if strength <= 0.0:
        return lifted_rgb

    shadow_weight = _dc_shadow_weight(original_rgb)
    edge_weight = _dc_edge_weight(original_rgb)
    blend_weight = shadow_weight * (1.0 - (edge_weight * 0.72)) * strength

    denoised = cv2.fastNlMeansDenoisingColored(
        lifted_rgb,
        None,
        4.2,
        4.2,
        7,
        21,
    )
    smoothed = cv2.bilateralFilter(
        denoised,
        d=5,
        sigmaColor=34,
        sigmaSpace=32,
    )
    mixed = (
        lifted_rgb.astype(np.float32) * (1.0 - blend_weight[..., np.newaxis])
        + smoothed.astype(np.float32) * blend_weight[..., np.newaxis]
    )
    return np.clip(mixed, 0.0, 255.0).astype(np.uint8)


def _change_dc_saturation(
    rgb: "np.ndarray",
    saturation: float,
) -> "np.ndarray":
    if abs(saturation - 1.0) < 1e-6:
        return rgb

    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV).astype(np.float32)
    hsv[..., 1] = np.clip(hsv[..., 1] * saturation, 0.0, 255.0)
    return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2RGB)


def _apply_dc_clahe(
    rgb: "np.ndarray",
    clip_limit: float,
) -> "np.ndarray":
    if clip_limit <= 0.0:
        return rgb

    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
    lightness, a_channel, b_channel = cv2.split(lab)
    clahe = cv2.createCLAHE(
        clipLimit=float(clip_limit),
        tileGridSize=_DC_CLAHE_TILE_GRID,
    )
    lightness = clahe.apply(lightness)
    return cv2.cvtColor(cv2.merge([lightness, a_channel, b_channel]), cv2.COLOR_LAB2RGB)


def _enhance_dc_shadow_detail(
    original_rgb: "np.ndarray",
    rgb: "np.ndarray",
    strength: float,
) -> "np.ndarray":
    if strength <= 0.0:
        return rgb

    shadow_weight = _dc_shadow_weight(original_rgb)
    edge_weight = _dc_edge_weight(original_rgb)
    detail_weight = shadow_weight * (0.35 + (0.65 * edge_weight)) * strength

    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
    lightness = lab[..., 0].astype(np.float32)
    base = cv2.bilateralFilter(
        np.clip(lightness, 0, 255).astype(np.uint8),
        d=7,
        sigmaColor=46,
        sigmaSpace=44,
    ).astype(np.float32)
    detail = lightness - base

    lightness = lightness + detail * detail_weight * 1.55
    midtone_lift = (255.0 - lightness) * shadow_weight * strength * 0.035
    lab[..., 0] = np.clip(lightness + midtone_lift, 0, 255).astype(np.uint8)
    return cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)


def _sharpen_dc_image(
    rgb: "np.ndarray",
    amount: float,
) -> "np.ndarray":
    if amount <= 0.0:
        return rgb

    blurred = cv2.GaussianBlur(rgb, (0, 0), sigmaX=1.0)
    return cv2.addWeighted(rgb, 1.0 + amount, blurred, -amount, 0.0)


def _recover_shadow_detail(rgb: "np.ndarray") -> "np.ndarray":
    params = _DC_RECOVER_PARAMS
    result = _apply_dc_gamma_and_gain(rgb, params)
    result = _deblock_dc_shadows(rgb, result, params.shadow_deblock)
    result = _boost_dc_red_in_shadows(result, params.red_shadow_boost)
    result = _boost_dc_highlights(result, params.highlight_strength)
    result = _change_dc_saturation(result, params.saturation)
    result = _apply_dc_clahe(result, params.clahe_clip)
    result = _enhance_dc_shadow_detail(rgb, result, params.shadow_detail)
    result = _sharpen_dc_image(result, params.shadow_sharpen)
    result = _sharpen_dc_image(result, params.sharpen)
    return result


def _brighten_image_bytes(data: bytes, content_type: str | None) -> bytes | None:
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            has_alpha = "A" in image.getbands()
            if image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGBA" if has_alpha else "RGB")

            alpha = (
                np.asarray(image.getchannel("A"), dtype=np.uint8)
                if image.mode == "RGBA"
                else None
            )
            rgb = np.asarray(image.convert("RGB"), dtype=np.uint8)

        recovered = _recover_shadow_detail(rgb)

        out = Image.fromarray(recovered, mode="RGB")
        if alpha is not None:
            out.putalpha(Image.fromarray(alpha, mode="L"))

        buffer = io.BytesIO()
        out.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()
    except (UnidentifiedImageError, OSError, cv2.error):
        LOGGER.warning(
            "스포일러 이미지 밝기 보정 실패 (content_type=%s).",
            content_type,
        )
        return None


def _safe_zip_filename(post: NewsPost) -> str:
    title = (post.title or "").strip()
    cleaned = _UNSAFE_FILENAME_RE.sub(" ", title).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        cleaned = post.post_id
    if len(cleaned) > 80:
        cleaned = cleaned[:80].rstrip()
    return f"림버스_소식_({cleaned}).zip"


def _log_level_from_env() -> int:
    raw = os.getenv("LIMPI_LOG_LEVEL", "INFO").strip().upper()
    if not raw:
        return logging.INFO
    if raw.isdigit():
        return int(raw)
    level = logging.getLevelName(raw)
    return level if isinstance(level, int) else logging.INFO


def _keepalive_socket_factory(addr_info: tuple) -> socket.socket:
    family, type_, proto, _, _ = addr_info
    sock = socket.socket(family, type_, proto)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    if hasattr(socket, "SIO_KEEPALIVE_VALS"):
        try:
            sock.ioctl(
                socket.SIO_KEEPALIVE_VALS,
                (1, WINDOWS_KEEPALIVE_TIME_MS, WINDOWS_KEEPALIVE_INTERVAL_MS),
            )
        except OSError:
            pass
    for option_name, value in (
        ("TCP_KEEPIDLE", TCP_KEEPALIVE_IDLE_SECONDS),
        ("TCP_KEEPALIVE", TCP_KEEPALIVE_IDLE_SECONDS),
        ("TCP_KEEPINTVL", TCP_KEEPALIVE_INTERVAL_SECONDS),
        ("TCP_KEEPCNT", TCP_KEEPALIVE_PROBES),
    ):
        option = getattr(socket, option_name, None)
        if option is None:
            continue
        try:
            sock.setsockopt(socket.IPPROTO_TCP, option, value)
        except OSError:
            pass
    return sock


def _prevent_windows_sleep() -> bool:
    if os.name != "nt":
        return False
    try:
        result = ctypes.windll.kernel32.SetThreadExecutionState(
            ES_CONTINUOUS | ES_SYSTEM_REQUIRED
        )
    except Exception:
        LOGGER.exception("Windows 절전 방지 설정 실패.")
        return False
    if not result:
        LOGGER.warning("Windows 절전 방지 설정이 적용되지 않았습니다.")
        return False
    LOGGER.info("봇 실행 중 Windows 시스템 절전을 방지합니다.")
    return True


def _restore_windows_sleep() -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
    except Exception:
        LOGGER.exception("Windows 절전 방지 해제 실패.")


def _build_aiohttp_connector() -> aiohttp.TCPConnector:
    options = {
        "keepalive_timeout": AIOHTTP_KEEPALIVE_TIMEOUT_SECONDS,
        "ttl_dns_cache": 300,
        "enable_cleanup_closed": True,
    }
    try:
        return aiohttp.TCPConnector(
            **options,
            socket_factory=_keepalive_socket_factory,
        )
    except TypeError:
        LOGGER.warning("aiohttp 버전이 TCP socket_factory를 지원하지 않아 기본 keepalive로 실행합니다.")
        return aiohttp.TCPConnector(**options)

__all__ = [
    "EgoGift",
    "_normalize_search_text",
    "_load_ego_gifts",
    "_find_ego_gifts",
    "_split_ego_gift_name_and_grade",
    "_ego_gift_sort_key",
    "_ego_gift_keyword",
    "_ego_gift_grade_value",
    "_filter_ego_gifts",
    "_ego_gift_search_values",
    "_ego_gift_keyword_counts",
    "_ego_gift_component_markdown",
    "_format_ego_gift_flag",
    "_ego_gift_grade_label",
    "_format_ego_gift_effect_markdown",
    "_filter_image_urls",
    "_normalize_image_url",
    "_image_request_headers",
    "_is_namu_wiki_image_url",
    "_resource_path",
    "_banner_files",
    "_resolve_banner_filename",
    "_banner_display_name",
    "_banner_autocomplete_choices",
    "_news_banner_file",
    "_content_image_urls",
    "_downloadable_image_urls",
    "_brightenable_image_urls",
    "_thumbnail_url_for_post",
    "_is_steam_card_thumbnail_url",
    "_standalone_image_urls",
    "_image_embed_batches_from_urls",
    "_split_message_content",
    "_description_for_post",
    "_embed_groups_for_post",
    "_embeds_for_post",
    "_twitter_video_urls",
    "_twitter_video_url_groups",
    "_twitter_video_url_groups_from_raw",
    "_twitter_video_fallback_url",
    "_twitter_video_fallback_url_from_raw",
    "_select_twitter_video_url",
    "_twitter_video_resolution",
    "_is_payload_too_large",
    "_twitter_image_urls",
    "_twitter_original_image_url",
    "_steam_original_image_url",
    "_original_image_download_candidates",
    "_is_twitter_video_thumbnail_url",
    "_twitter_youtube_urls",
    "_twitter_link_urls",
    "_twitter_post_needs_refresh",
    "_looks_truncated_post_text",
    "_is_steam_news_url",
    "_steam_news_url_key",
    "_embed_for_twitter_post",
    "_embeds_for_twitter_post",
    "_display_title_for_twitter_post",
    "_twitter_post_context_line",
    "_strip_twitter_post_context_prefix",
    "_embed_for_chzzk_live",
    "_embed_for_chzzk_live_end",
    "_embed_for_chzzk_offline",
    "_embed_for_youtube_live",
    "_embed_for_youtube_upload",
    "_embed_for_hampang_youtube_upload",
    "_embed_for_youtube_offline",
    "_chzzk_live_view",
    "_youtube_live_view",
    "_youtube_upload_view",
    "_build_layout_view_for_post",
    "_post_embed_color",
    "_success_embed_color",
    "_news_update_notice_embed",
    "_truncate_component_text",
    "_build_view_for_post",
    "_current_maintenance_notice",
    "_maintenance_embed",
    "_language_label",
    "_news_ui_text",
    "_format_news_targets",
    "_format_chzzk_target",
    "_format_youtube_target",
    "_format_youtube_upload_target",
    "_format_hampang_target",
    "_is_hampang_youtube_upload",
    "_regular_youtube_uploads",
    "_sort_twitter_posts_newest_first",
    "_sort_youtube_uploads_newest_first",
    "_hampang_news_items",
    "_hampang_news_items_for_source",
    "_hampang_choice_name",
    "_hampang_choice_description",
    "_choice_bool",
    "_broadcast_source_value",
    "_broadcast_source_allows_chzzk",
    "_broadcast_source_allows_youtube",
    "_broadcast_source_label",
    "_news_target_choice_value",
    "_parse_news_target_choice",
    "_broadcast_target_choice_name",
    "_bool_label",
    "_image_delivery_label",
    "_youtube_links_content",
    "_youtube_urls_for_post",
    "_is_twitter_news_post",
    "_post_source_label",
    "_display_title_for_post",
    "_display_body_and_trailing_tags",
    "_twitter_context_line",
    "_strip_twitter_context_prefix",
    "_post_date_line",
    "_post_meta_block",
    "_split_trailing_hashtag_block",
    "_is_hashtag_only_line",
    "_link_twitter_hashtags",
    "_news_source_mode_label",
    "_selected_source_mode",
    "_selected_source_mode_from_options",
    "_sort_posts_newest_first",
    "_dedupe_posts_by_id",
    "_recent_auto_posts",
    "_is_news_update_recent",
    "_delay_seconds",
    "_post_delay_seconds",
    "_minute_in_window",
    "_format_windows_label",
    "_is_twitter_post_recent",
    "_is_twitter_news_post_recent",
    "_twitter_post_delay_seconds",
    "_as_utc_datetime",
    "_matching_steam_posts_for_twitter",
    "_twitter_matches_steam_news",
    "_raw_link_urls",
    "_news_body_match_candidates",
    "_normalize_news_match_text",
    "_news_match_candidates_overlap",
    "_steam_news_link_keys_for_twitter",
    "_steam_news_post_id_from_url",
    "_steam_news_post_ids_for_twitter_posts",
    "_steam_posts_without_fast_twitter_duplicates",
    "_twitter_posts_as_news_posts",
    "_twitter_news_without_duplicate_steam_links",
    "_steam_news_link_keys_for_news_post",
    "_news_post_is_earlier",
    "_raw_string_set",
    "_twitter_news_prefers_available_steam",
    "_schedule_text_for_post",
    "_datetime_from_raw_timestamp",
    "_format_kst",
    "_is_chzzk_live_too_old",
    "_is_youtube_live_too_old",
    "_is_chzzk_live_recently_closed",
    "_parse_chzzk_datetime",
    "_choice_name",
    "_twitter_choice_name",
    "_post_language",
    "_post_language_independent_id",
    "_unique_zip_name",
    "_image_file_extension",
    "_image_bytes_as_png",
    "_resize_image_to_fit",
    "_process_ego_gift_image_bytes",
    "_DcRecoverParams",
    "_smoothstep",
    "_dc_luminance",
    "_dc_shadow_weight",
    "_dc_edge_weight",
    "_apply_dc_gamma_and_gain",
    "_boost_dc_red_in_shadows",
    "_boost_dc_highlights",
    "_deblock_dc_shadows",
    "_change_dc_saturation",
    "_apply_dc_clahe",
    "_enhance_dc_shadow_detail",
    "_sharpen_dc_image",
    "_recover_shadow_detail",
    "_brighten_image_bytes",
    "_safe_zip_filename",
    "_log_level_from_env",
    "_keepalive_socket_factory",
    "_prevent_windows_sleep",
    "_restore_windows_sleep",
    "_build_aiohttp_connector",
]
