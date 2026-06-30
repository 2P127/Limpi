from __future__ import annotations

import asyncio
import ctypes
import gc
import io
import json
import logging
import logging.handlers
import os
import queue
import signal
import socket
import sys
import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
from time import perf_counter
from typing import Iterable, Mapping
from urllib.parse import parse_qs, quote, urlparse

import aiohttp
import cv2
import discord
import numpy as np
from PIL import Image, UnidentifiedImageError
from discord import app_commands
from discord.ext import commands, tasks

from .clients.chzzk_client import (
    ChzzkBroadcast,
    ChzzkClient,
    ChzzkLive,
    PROJECT_MOON_CHZZK_LIVE_URL,
)
from .core.config import AppConfig, BOT_VERSION
from .core.models import (
    GuildChzzkTarget,
    GuildHampangTarget,
    GuildNewsTarget,
    GuildSettings,
    GuildTwitterTarget,
    GuildYoutubeTarget,
    GuildYoutubeUploadTarget,
    NewsPost,
    TwitterPost,
)
from .core.storage import (
    DEFAULT_NOTIFICATION_BANNER,
    DEFAULT_NEWS_SOURCE_MODE,
    DISABLED_NOTIFICATION_BANNER,
    MAX_CLEANUP_DAYS,
    MIN_CLEANUP_DAYS,
    NEWS_UPDATE_MAX_AGE_SECONDS,
    SQLiteStorage,
)
from .clients.steam_client import NewsSource, build_news_source
from .clients.x_client import LimbusXClient, XClientError
from .clients.youtube_client import (
    PROJECT_MOON_YOUTUBE_STREAMS_URL,
    PROJECT_MOON_YOUTUBE_VIDEOS_URL,
    YoutubeClient,
    YoutubeLive,
    YoutubeStream,
    YoutubeUpload,
)


from .bot_constants import (
    BOOLEAN_CHOICES,
    BRIGHTEN_CACHE_MAX_BYTES,
    BRIGHTEN_CACHE_MAX_ITEM_BYTES,
    BRIGHTEN_CACHE_MAX_ITEMS,
    BRIGHTEN_PROCESS_CONCURRENCY,
    BROADCAST_SOURCE_BOTH,
    BROADCAST_SOURCE_CHOICES,
    BROADCAST_SOURCE_CHZZK,
    BROADCAST_SOURCE_YOUTUBE,
    CHZZK_LIVE_ANNOUNCE_MAX_AGE,
    CHZZK_LIVE_END_ANNOUNCE_MAX_AGE,
    CHZZK_POLL_INTERVAL_SECONDS,
    COMMAND_GUIDE_IMAGE_NAME,
    DISCORD_HEARTBEAT_TIMEOUT_SECONDS,
    EGO_GIFT_FALLBACK_IMAGE_BASE_URL,
    EGO_GIFT_FALLBACK_IMAGE_INDEX_URL,
    EGO_GIFT_IMAGE_CACHE_MAX_BYTES,
    EGO_GIFT_IMAGE_CACHE_MAX_ITEMS,
    EGO_GIFT_IMAGE_PROCESS_CONCURRENCY,
    EGO_GIFT_IMAGE_WARMUP_CONCURRENCY,
    EGO_GIFT_IMAGE_WARMUP_LIMIT,
    EGO_GIFT_STORE_PATH,
    EGO_GIFT_UPDATE_HOUR_KST,
    EGO_GIFT_UPDATE_WEEKDAY,
    HAMPANG_AUTO_POLL_INTERVAL_SECONDS,
    HAMPANG_SOURCE_BOTH,
    HAMPANG_SOURCE_CHOICES,
    HAMPANG_SOURCE_X,
    HAMPANG_SOURCE_YOUTUBE,
    HAMPANG_X_USERNAME,
    IMAGE_CACHE_MAX_BYTES,
    IMAGE_CACHE_MAX_ITEM_BYTES,
    IMAGE_CACHE_MAX_ITEMS,
    IMAGE_CACHE_WARM_POST_LIMIT,
    IMAGE_DELIVERY_CHOICES,
    IMAGE_DELIVERY_EMBEDS,
    IMAGE_DELIVERY_FILES,
    IMAGE_DOWNLOAD_ATTEMPTS,
    IMAGE_DOWNLOAD_TIMEOUT_SECONDS,
    IMAGE_FAILED_URL_CACHE_MAX_ITEMS,
    IMAGE_FILES_PER_MESSAGE,
    IMAGE_PROCESS_CONCURRENCY,
    KST,
    LANGUAGE_CHOICES,
    MAINTENANCE_START_DESCRIPTION,
    MAINTENANCE_START_TITLE,
    MAINTENANCE_UPDATE_DESCRIPTION,
    MAINTENANCE_UPDATE_TITLE,
    MAX_TWITTER_EMBED_IMAGES,
    NEWS_BANNER_DIR,
    NEWS_LOOKUP_SOURCE_CHOICES,
    NEWS_POLL_TICK_SECONDS,
    NEWS_POST_LIMIT,
    NEWS_ROLE_MENTION_COOLDOWN_SECONDS,
    NEWS_SELECT_POST_LIMIT,
    NEWS_SOURCE_CHOICES,
    NEWS_SOURCE_STEAM,
    NEWS_SOURCE_TWITTER,
    NEWS_TARGET_SEND_CONCURRENCY,
    NEWS_UPDATE_NOTICE_COOLDOWN,
    POST_FORMAT_RICH,
    SYNC_LANGUAGES,
    TWITTER_NEWS_DEFAULT_MAX_AGE_SECONDS,
    TWITTER_POLL_TICK_SECONDS,
    TWITTER_POST_LIMIT,
    TWITTER_PRIORITY_POLL_INTERVAL_SECONDS,
    TWITTER_PRIORITY_POLL_PREP_SECONDS,
    TWITTER_PRIORITY_POLL_TIMES_KST,
    TWITTER_PRIORITY_POLL_WINDOW_SECONDS,
    TWITTER_STEAM_PREFERENCE_GRACE_SECONDS,
    USER_COMMAND_COOLDOWN_SECONDS,
    YOUTUBE_LIVE_ANNOUNCE_MAX_AGE,
    YOUTUBE_UPLOAD_POLL_INTERVAL_SECONDS,
    ZIP_CACHE_MAX_ITEMS,
    ZIP_IMAGE_CONCURRENCY,
    ZIP_UPLOAD_HEADROOM_BYTES,
    ZIP_UPLOAD_SAFE_BYTES,
)
from .bot_helpers import (
    EgoGift,
    _as_utc_datetime,
    _banner_autocomplete_choices,
    _banner_display_name,
    _bool_label,
    _broadcast_target_choice_name,
    _broadcast_source_allows_chzzk,
    _broadcast_source_allows_youtube,
    _broadcast_source_label,
    _broadcast_source_value,
    _brightenable_image_urls,
    _brighten_image_bytes,
    _build_aiohttp_connector,
    _build_layout_view_for_post,
    clear_ego_gift_cache,
    _chzzk_live_view,
    _choice_bool,
    _content_image_urls,
    _current_maintenance_notice,
    _dedupe_posts_by_id,
    _downloadable_image_urls,
    _embed_for_chzzk_live,
    _embed_for_chzzk_live_end,
    _embed_for_chzzk_offline,
    _embed_for_hampang_youtube_upload,
    _embed_for_youtube_live,
    _embed_for_youtube_offline,
    _embed_for_youtube_upload,
    _embeds_for_twitter_post,
    _format_chzzk_target,
    _format_hampang_target,
    _format_news_targets,
    _format_windows_label,
    _format_youtube_target,
    _format_youtube_upload_target,
    _hampang_news_items,
    _hampang_news_items_for_source,
    _image_bytes_as_png,
    _image_delivery_label,
    _image_embed_batches_from_urls,
    _image_request_headers,
    _is_chzzk_live_recently_closed,
    _is_chzzk_live_too_old,
    _is_hampang_youtube_upload,
    _is_namu_wiki_image_url,
    _is_news_update_recent,
    _is_payload_too_large,
    _is_twitter_news_post,
    _is_twitter_news_post_recent,
    _is_twitter_post_recent,
    _is_youtube_live_too_old,
    _language_label,
    _log_level_from_env,
    _matching_steam_posts_for_twitter,
    _maintenance_embed,
    _minute_in_window,
    _news_banner_file,
    _news_source_mode_label,
    _news_target_choice_value,
    _news_update_notice_embed,
    _news_ui_text,
    _normalize_image_url,
    _original_image_download_candidates,
    _parse_news_target_choice,
    _post_delay_seconds,
    _post_language,
    _post_language_independent_id,
    _prevent_windows_sleep,
    _process_ego_gift_image_bytes,
    _recent_auto_posts,
    _regular_youtube_uploads,
    _resolve_banner_filename,
    _resource_path,
    _restore_windows_sleep,
    _safe_zip_filename,
    set_ego_gift_store_path,
    _sort_posts_newest_first,
    _sort_twitter_posts_newest_first,
    _sort_youtube_uploads_newest_first,
    _standalone_image_urls,
    _steam_news_link_keys_for_news_post,
    _steam_news_link_keys_for_twitter,
    _steam_news_post_ids_for_twitter_posts,
    _steam_posts_without_fast_twitter_duplicates,
    _success_embed_color,
    _twitter_image_urls,
    _twitter_link_urls,
    _twitter_news_prefers_available_steam,
    _twitter_news_without_duplicate_steam_links,
    _twitter_post_delay_seconds,
    _twitter_post_needs_refresh,
    _twitter_posts_as_news_posts,
    _twitter_video_fallback_url,
    _twitter_video_fallback_url_from_raw,
    _twitter_video_url_groups,
    _twitter_video_url_groups_from_raw,
    _select_twitter_video_url,
    _unique_zip_name,
    _youtube_links_content,
    _youtube_live_view,
    _youtube_upload_view,
)
from .bot_runtime import (
    _install_asyncio_exception_handler,
    _install_windows_selector_event_loop_policy,
    _is_internet_exception,
    _log_internet_exception,
)
from .bot_views import (
    BrightenSpoilerButton,
    BrightenSpoilerVisibilityView,
    EgoGiftSelectView,
    ExternalNewsSendConfirmView,
    HampangNewsSelectView,
    NewsPostSelectView,
    ZipDownloadButton,
)

LOGGER = logging.getLogger(__name__)

_UNKNOWN_LOG_VALUE = "알 수 없음"
_TWITTER_AUTO_CHECK_FAILURE = "X 게시물 자동 확인 실패"
_TWITTER_NEWS_POST_ID_PREFIX = "twitter:"
_NEWS_SEND_SENT = "sent"
_NEWS_SEND_BASELINE = "baseline"
_NEWS_SEND_RETRY = "retry"
_IMAGE_DOWNLOAD_RETRY = object()
_NEWS_AUTO_ANNOUNCE_MAX_AGE_SECONDS = 24 * 60 * 60


def _maintenance_notice_embed(notice_type: str) -> discord.Embed:
    if notice_type == "start":
        return _maintenance_embed(
            MAINTENANCE_START_TITLE,
            MAINTENANCE_START_DESCRIPTION,
            color=discord.Color.dark_gray(),
        )
    return _maintenance_embed(
        MAINTENANCE_UPDATE_TITLE,
        MAINTENANCE_UPDATE_DESCRIPTION,
        color=discord.Color.yellow(),
    )


def _news_delay_label(post: NewsPost) -> str:
    delay = _post_delay_seconds(post)
    if not isinstance(delay, int):
        return str(delay)
    if delay < 60:
        return f"{delay}초"
    minutes, seconds = divmod(delay, 60)
    if minutes < 60:
        return f"{minutes}분 {seconds}초"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}시간 {minutes}분"
    days, hours = divmod(hours, 24)
    return f"{days}일 {hours}시간"


@dataclass
class ManualNewsSendResult:
    sent_channel_ids: list[int]
    failed_channel_ids: list[int | None]
    missing_languages: set[str]


@dataclass
class HampangPollContext:
    x_posts: list[TwitterPost]
    youtube_uploads: list[YoutubeUpload]
    latest_x_post_id: str | None
    latest_youtube_video_id: str | None
    x_ids: list[str]
    youtube_ids: list[str]
    x_baseline_only: bool
    youtube_baseline_only: bool
    window_started_at: datetime | None
    max_age_seconds: int


@dataclass
class HampangTargetPlan:
    baseline_x_id: str | None
    baseline_youtube_id: str | None
    new_x_posts: list[TwitterPost]
    new_youtube_uploads: list[YoutubeUpload]


@dataclass
class HampangConfigBaseline:
    latest_x_post: TwitterPost | None
    latest_youtube_upload: YoutubeUpload | None
    x_failed: bool = False
    youtube_failed: bool = False


def _log_text(value: object | None, fallback: str = "-") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    if not text:
        return fallback
    return re.sub(r"\s+", " ", text)


def _log_value(value: object | None, fallback: str = "-", max_length: int = 160) -> str:
    text = _log_text(value, fallback)
    if len(text) <= max_length:
        return text
    return f"{text[: max_length - 3]}..."


class _ScriptContentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.scripts: list[tuple[dict[str, str], str]] = []
        self._script_attrs: dict[str, str] | None = None
        self._script_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "script":
            return
        self._script_attrs = {
            name.casefold(): value or ""
            for name, value in attrs
        }
        self._script_chunks = []

    def handle_data(self, data: str) -> None:
        if self._script_attrs is not None:
            self._script_chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() != "script" or self._script_attrs is None:
            return
        self.scripts.append((self._script_attrs, "".join(self._script_chunks)))
        self._script_attrs = None
        self._script_chunks = []


def _json_object_from_text(
    text: str,
    *,
    allow_prefix: bool = False,
) -> dict[str, object] | None:
    value = text.strip()
    if allow_prefix:
        start = value.find("{")
        if start < 0:
            return None
        value = value[start:]
    elif not value.startswith("{"):
        return None

    try:
        payload, _ = json.JSONDecoder().raw_decode(value)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict):
        return payload
    return None


def _ego_gift_fallback_payload(text: str) -> dict[str, object] | None:
    payload = _json_object_from_text(text)
    if payload is not None and isinstance(payload.get("egoGifts"), list):
        return payload

    parser = _ScriptContentParser()
    parser.feed(text)
    candidate_scripts = [
        content
        for attrs, content in parser.scripts
        if attrs.get("id") == "data" or "egoGifts" in content
    ]
    for content in candidate_scripts:
        payload = _json_object_from_text(content, allow_prefix=True)
        if payload is not None and isinstance(payload.get("egoGifts"), list):
            return payload
    return None


def _ego_gift_fallback_key(name: str) -> str:
    return re.sub(r"\s+", "", name).casefold()


def _ego_gift_store_path(database_path: Path) -> Path:
    return database_path.with_name(EGO_GIFT_STORE_PATH.name)


def _is_ego_gift_update_window(now: datetime) -> bool:
    return (
        now.weekday() == EGO_GIFT_UPDATE_WEEKDAY
        and now.hour == EGO_GIFT_UPDATE_HOUR_KST
    )


def _seconds_since_midnight(now: datetime) -> int:
    local_now = now.astimezone(KST)
    return local_now.hour * 3600 + local_now.minute * 60 + local_now.second


def _twitter_priority_start_seconds() -> tuple[int, ...]:
    return tuple(
        hour * 3600 + minute * 60
        for hour, minute in TWITTER_PRIORITY_POLL_TIMES_KST
    )


def _is_twitter_priority_poll_window(now: datetime) -> bool:
    current_second = _seconds_since_midnight(now)
    return any(
        0 <= current_second - start < TWITTER_PRIORITY_POLL_WINDOW_SECONDS
        for start in _twitter_priority_start_seconds()
    )


def _seconds_until_twitter_priority_poll(now: datetime) -> int:
    current_second = _seconds_since_midnight(now)
    day_seconds = 24 * 60 * 60
    return min(
        (start - current_second) % day_seconds
        for start in _twitter_priority_start_seconds()
    )


def _is_twitter_priority_prep_window(now: datetime) -> bool:
    if _is_twitter_priority_poll_window(now):
        return False
    seconds_until = _seconds_until_twitter_priority_poll(now)
    return 0 < seconds_until <= TWITTER_PRIORITY_POLL_PREP_SECONDS


def _is_subcommand_option(option: dict[str, object]) -> bool:
    return option.get("type") in {1, 2, "1", "2"}


def _app_command_path(data: dict[str, object]) -> str:
    names = [_log_value(data.get("name"), "unknown", 80)]
    options = data.get("options")
    while isinstance(options, list):
        command_option = next(
            (
                option
                for option in options
                if isinstance(option, dict) and _is_subcommand_option(option)
            ),
            None,
        )
        if command_option is None:
            break
        names.append(_log_value(command_option.get("name"), "unknown", 80))
        options = command_option.get("options")
    return " ".join(name for name in names if name)


def _format_app_command_options(data: dict[str, object]) -> str:
    parts: list[str] = []

    def walk(options: object) -> None:
        if not isinstance(options, list):
            return
        for option in options:
            if not isinstance(option, dict):
                continue
            nested_options = option.get("options")
            if _is_subcommand_option(option):
                walk(nested_options)
                continue
            if "value" in option:
                name = _log_value(option.get("name"), "unknown", 80)
                value = _log_value(option.get("value"), "none")
                parts.append(f"{name}={value}")
            else:
                walk(nested_options)

    walk(data.get("options"))
    return " ".join(parts)


def _format_command_invocation(command_name: str, data: dict[str, object]) -> str:
    command = _log_value(command_name, "unknown", 120)
    options = _format_app_command_options(data)
    return f"/{command} {options}" if options else f"/{command}"


def _format_user_for_log(user: object) -> str:
    return (
        f"닉네임={_log_value(getattr(user, 'nick', None))} | "
        f"유저명={_log_value(getattr(user, 'name', None))} | "
        f"글로벌명={_log_value(getattr(user, 'global_name', None))} | "
        f"표시명={_log_value(getattr(user, 'display_name', None))} | "
        f"유저태그={_log_value(user)} | "
        f"유저ID={_log_value(getattr(user, 'id', None))}"
    )


def _format_guild_for_log(
    guild: discord.Guild | discord.Object | None,
    guild_id: int | str | None = None,
) -> str:
    resolved_id = guild_id or getattr(guild, "id", None)
    if guild is None and resolved_id is None:
        return "서버=DM (ID: DM, 멤버 수: -, 소유자 ID: -)"

    guild_name = _log_value(getattr(guild, "name", None), _UNKNOWN_LOG_VALUE, 120)
    member_count = _log_value(getattr(guild, "member_count", None))
    owner_id = _log_value(getattr(guild, "owner_id", None))
    suffix = ""
    if bool(getattr(guild, "unavailable", False)):
        suffix = ", 상태: unavailable"
    return (
        f"서버={guild_name} "
        f"(ID: {_log_value(resolved_id, 'unknown')}, 멤버 수: {member_count}, 소유자 ID: {owner_id}{suffix})"
    )


def _format_channel_for_log(channel: object | None, channel_id: int | str | None) -> str:
    resolved_id = channel_id or getattr(channel, "id", None)
    if channel is None and resolved_id is None:
        return "채널=DM (ID: DM)"
    channel_name = _log_value(
        getattr(channel, "name", None) or (str(channel) if channel is not None else None),
        _UNKNOWN_LOG_VALUE,
        120,
    )
    return f"채널={channel_name} (ID: {_log_value(resolved_id, 'unknown')})"


async def _resolve_interaction_guild_for_log(
    interaction: discord.Interaction,
) -> discord.Guild | discord.Object | None:
    guild = interaction.guild
    if _log_text(getattr(guild, "name", None), ""):
        return guild

    guild_id = interaction.guild_id
    if guild_id is None:
        return guild

    client = getattr(interaction, "client", None)
    get_guild = getattr(client, "get_guild", None)
    if callable(get_guild):
        cached_guild = get_guild(guild_id)
        if cached_guild is not None:
            guild = cached_guild
            if _log_text(getattr(cached_guild, "name", None), ""):
                return cached_guild

    fetch_guild = getattr(client, "fetch_guild", None)
    if callable(fetch_guild):
        try:
            fetched_guild = await fetch_guild(guild_id)
        except Exception:
            return guild
        if fetched_guild is not None:
            return fetched_guild
    return guild


async def _log_app_command_usage(
    interaction: discord.Interaction,
    data: dict[str, object],
    command_name: str,
    status: str,
    elapsed: float,
) -> None:
    guild = await _resolve_interaction_guild_for_log(interaction)
    LOGGER.info(
        "명령어 사용: %s | %s | %s | %s | 결과=%s | 소요시간=%.3f초",
        _format_command_invocation(command_name, data),
        _format_user_for_log(interaction.user),
        _format_guild_for_log(guild, interaction.guild_id),
        _format_channel_for_log(interaction.channel, getattr(interaction, "channel_id", None)),
        status,
        elapsed,
    )


class LoggingCommandTree(app_commands.CommandTree):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._user_command_cooldowns: dict[int, float] = {}

    async def _call(self, interaction: discord.Interaction) -> None:
        if interaction.type is not discord.InteractionType.application_command:
            await super()._call(interaction)
            return

        started_at = perf_counter()
        data = interaction.data if isinstance(interaction.data, dict) else {}
        command_name = _app_command_path(data)
        status = "completed"
        now = perf_counter()
        user_id = interaction.user.id
        last_used_at = self._user_command_cooldowns.get(user_id)
        if last_used_at is not None:
            remaining = USER_COMMAND_COOLDOWN_SECONDS - (now - last_used_at)
            if remaining > 0:
                status = "cooldown"
                await interaction.response.send_message(
                    f"명령어는 3초에 한 번만 사용할 수 있어요. {remaining:.1f}초 뒤 다시 시도해주세요.",
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                await _log_app_command_usage(
                    interaction,
                    data,
                    command_name,
                    status,
                    perf_counter() - started_at,
                )
                return

        self._user_command_cooldowns[user_id] = now
        self._prune_user_command_cooldowns(now)

        try:
            await super()._call(interaction)
            if interaction.command is not None:
                command_name = interaction.command.qualified_name
            if interaction.command_failed:
                status = "failed"
        except Exception:
            status = "errored"
            raise
        finally:
            elapsed = perf_counter() - started_at
            await _log_app_command_usage(interaction, data, command_name, status, elapsed)

    def _prune_user_command_cooldowns(self, now: float) -> None:
        if len(self._user_command_cooldowns) < 1000:
            return

        stale_before = now - (USER_COMMAND_COOLDOWN_SECONDS * 10)
        stale_user_ids = [
            user_id
            for user_id, last_used_at in self._user_command_cooldowns.items()
            if last_used_at < stale_before
        ]
        for user_id in stale_user_ids:
            self._user_command_cooldowns.pop(user_id, None)

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        if isinstance(error, app_commands.TransformerError):
            LOGGER.warning(
                "명령어 인자 변환 실패 (command=%s, guild_id=%s, value=%r). "
                "봇이 해당 서버에 없거나 채널을 볼 수 없어 변환에 실패했을 수 있습니다.",
                getattr(interaction.command, "qualified_name", "unknown"),
                interaction.guild_id,
                getattr(error, "value", None),
            )
            message = (
                "선택한 채널을 확인하지 못했어요. 림피가 이 서버에 초대되어 있는지, "
                "그리고 채널을 다시 선택했는지 확인한 뒤 다시 시도해주세요."
            )
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(
                        message,
                        ephemeral=True,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                else:
                    await interaction.response.send_message(
                        message,
                        ephemeral=True,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
            except discord.HTTPException:
                pass
            return

        await super().on_error(interaction, error)


class LimpiBot(commands.Bot):
    def __init__(self, config: AppConfig) -> None:
        intents = discord.Intents.default()
        super().__init__(
            command_prefix="!",
            intents=intents,
            tree_cls=LoggingCommandTree,
            heartbeat_timeout=DISCORD_HEARTBEAT_TIMEOUT_SECONDS,
        )
        self.config = config
        self._synced_connected_guilds = False
        self._cleared_global_commands = False
        self._cleared_connected_guild_commands = False
        self._logged_startup_summary = False
        self._pruned_disconnected_guild_data = False

    async def update_presence_status(self, text: str | None = None) -> None:
        if self.is_closed() or not self.is_ready():
            return
        try:
            await self.change_presence(
                activity=discord.Game(
                    name=text or f"림피가 {len(self.guilds)}개의 서버에서 활동중이에요!"
                )
            )
        except (aiohttp.ClientConnectionError, ConnectionError, discord.ConnectionClosed) as exc:
            LOGGER.debug(
                "Discord 상태 갱신을 건너뜁니다: 연결 재수립 중입니다.",
                exc_info=(type(exc), exc, exc.__traceback__),
            )

    async def setup_hook(self) -> None:
        self.add_dynamic_items(ZipDownloadButton)
        self.add_dynamic_items(BrightenSpoilerButton)
        synced = await self.tree.sync()
        LOGGER.info(
            "글로벌 명령어 %s개 동기화 완료 (서버 및 유저 앱 설치).",
            len(synced),
        )

        if self.config.command_guild_id:
            guild = discord.Object(id=self.config.command_guild_id)
            self.tree.clear_commands(guild=guild)
            guild_synced = await self.tree.sync(guild=guild)
            LOGGER.info(
                "서버 %s의 서버 범위 명령어 %s개 초기화 완료. 글로벌 명령어는 유지됩니다.",
                len(guild_synced),
                guild.id,
            )

    async def clear_global_command_duplicates(self) -> None:
        if self._cleared_global_commands:
            return

        global_commands = list(self.tree.get_commands(guild=None))
        self.tree.clear_commands(guild=None)
        try:
            await self.tree.sync()
            self._cleared_global_commands = True
            LOGGER.info(
                "슬래시 명령어 중복 방지를 위해 글로벌 명령어를 초기화했습니다."
            )
        except discord.HTTPException:
            LOGGER.exception("글로벌 명령어 초기화 실패.")
        finally:
            for command in global_commands:
                try:
                    self.tree.add_command(command)
                except app_commands.CommandAlreadyRegistered:
                    continue

    async def clear_connected_guild_command_duplicates(self) -> None:
        if self._cleared_connected_guild_commands:
            return

        if not self.guilds:
            LOGGER.warning("연결된 서버가 없어 서버 명령어 정리를 건너뜁니다.")
            return

        for guild in self.guilds:
            guild_object = discord.Object(id=guild.id)
            try:
                self.tree.clear_commands(guild=guild_object)
                synced = await self.tree.sync(guild=guild_object)
                LOGGER.info(
                    "서버 범위 명령어 %s개 초기화 완료 — 서버: %s (%s) (글로벌 중복 방지).",
                    len(synced),
                    guild.name,
                    guild.id,
                )
            except discord.HTTPException:
                LOGGER.exception(
                    "서버 %s (%s)의 명령어 초기화 실패.",
                    guild.name,
                    guild.id,
                )

        self._cleared_connected_guild_commands = True

    async def sync_connected_guild_commands(self) -> None:
        await self.clear_connected_guild_command_duplicates()
        self._synced_connected_guilds = True




class NewsCog(commands.Cog):
    def __init__(
        self,
        bot: LimpiBot,
        config: AppConfig,
        storage: SQLiteStorage,
        news_source: NewsSource | None,
        x_source: LimbusXClient,
        session: aiohttp.ClientSession,
        *,
        test_mode: bool = False,
    ) -> None:
        self.bot = bot
        self.config = config
        self.storage = storage
        self.news_source = news_source
        self.x_source = x_source
        self.hampang_x_source = LimbusXClient(
            config,
            session,
            account_username=HAMPANG_X_USERNAME,
        )
        self.session = session
        self.test_mode = test_mode
        self._x_probe_active = test_mode and config.x_news_probe
        self._ego_gift_store_path = _ego_gift_store_path(config.database_path)
        set_ego_gift_store_path(self._ego_gift_store_path)
        self.chzzk_client = ChzzkClient(session)
        self.youtube_client = YoutubeClient(session)
        self._poll_lock = asyncio.Lock()
        self._twitter_poll_lock = asyncio.Lock()
        self._chzzk_poll_lock = asyncio.Lock()
        self._youtube_poll_lock = asyncio.Lock()
        self._youtube_upload_poll_lock = asyncio.Lock()
        self._news_role_mention_times: dict[int, float] = {}
        self._twitter_steam_grace_started_at: dict[str, float] = {}
        self._twitter_steam_defer_logged_post_ids: set[str] = set()
        self._twitter_steam_retry_count: dict[str, int] = {}
        self._zip_cache: dict[str, tuple[list[bytes], int, int, int]] = {}
        self._image_cache: dict[str, tuple[bytes, str | None]] = {}
        self._image_cache_bytes: int = 0
        self._image_download_tasks: dict[str, asyncio.Task[tuple[bytes, str | None] | None]] = {}
        self._image_process_semaphore = asyncio.Semaphore(IMAGE_PROCESS_CONCURRENCY)
        self._failed_image_urls: dict[str, None] = {}
        self._brighten_cache: dict[str, bytes] = {}
        self._brighten_cache_bytes: int = 0
        self._brighten_tasks: dict[str, asyncio.Task[bytes | None]] = {}
        self._brighten_semaphore = asyncio.Semaphore(BRIGHTEN_PROCESS_CONCURRENCY)
        self._ego_gift_image_fallbacks: dict[str, str] | None = None
        self._ego_gift_image_fallbacks_task: asyncio.Task[dict[str, str]] | None = None
        # 처리 완료(PNG 변환 + 150px 리사이즈)된 첨부 바이트를 기프트 이름으로 캐싱.
        # 연속 전환/다중 사용자 조회 시 재다운로드·재인코딩을 막는다.
        self._ego_gift_image_cache: dict[str, bytes] = {}
        self._ego_gift_image_cache_bytes: int = 0
        # 동일 기프트를 동시에 조회할 때 빌드를 한 번만 수행하도록 in-flight 태스크 공유.
        self._ego_gift_image_tasks: dict[str, asyncio.Task[bytes | None]] = {}
        self._ego_gift_image_semaphore = asyncio.Semaphore(EGO_GIFT_IMAGE_PROCESS_CONCURRENCY)
        self._ego_gift_update_lock = asyncio.Lock()
        self._ego_gift_startup_task: asyncio.Task[None] | None = None
        self._last_ego_gift_update_check_date: date | None = None
        self._last_poll_at: datetime | None = None
        self._last_twitter_poll_at: datetime | None = None
        self._last_hampang_poll_at: datetime | None = None
        self._startup_synced = False
        self._news_recovery_baseline_pending = False
        self._twitter_recovery_baseline_pending = False
        self._chzzk_recovery_baseline_pending = False
        self._youtube_recovery_baseline_pending = False
        self._youtube_upload_recovery_baseline_pending = False
        self._hampang_x_recovery_baseline_pending = False
        self._hampang_youtube_recovery_baseline_pending = False
        self._in_high_frequency_window: bool = False
        self._in_twitter_tracking_window: bool = False
        self._presence_show_servers: bool = True

    async def _wait_until_ready(self) -> None:
        while not self.bot.is_ready():
            await asyncio.sleep(0.5)

    async def cog_load(self) -> None:
        self.presence_status.start()
        self.maintenance_notifications.start()
        self.cleanup_messages.start()
        self.poll_twitter_posts.start()
        self.poll_chzzk_live.start()
        self.poll_youtube_live.start()
        self.poll_youtube_uploads.start()
        self.refresh_ego_gifts.start()
        self._ego_gift_startup_task = asyncio.create_task(self._ensure_ego_gift_data())
        self._ego_gift_startup_task.add_done_callback(self._log_background_task_result)

        if self.news_source is None and self.x_source is None:
            LOGGER.warning("뉴스 소스가 설정되지 않아 뉴스 폴링을 비활성화합니다.")
            return

        self.poll_news.start()

    async def cog_unload(self) -> None:
        if self.presence_status.is_running():
            self.presence_status.cancel()
        if self.poll_news.is_running():
            self.poll_news.cancel()
        if self.poll_twitter_posts.is_running():
            self.poll_twitter_posts.cancel()
        if self.poll_chzzk_live.is_running():
            self.poll_chzzk_live.cancel()
        if self.poll_youtube_live.is_running():
            self.poll_youtube_live.cancel()
        if self.poll_youtube_uploads.is_running():
            self.poll_youtube_uploads.cancel()
        if self.refresh_ego_gifts.is_running():
            self.refresh_ego_gifts.cancel()
        if self._ego_gift_startup_task is not None:
            self._ego_gift_startup_task.cancel()
        self.maintenance_notifications.cancel()
        self.cleanup_messages.cancel()
        for task in self._brighten_tasks.values():
            task.cancel()

    @tasks.loop(minutes=1)
    async def presence_status(self) -> None:
        await self.update_presence_status()

    @presence_status.before_loop
    async def before_presence_status(self) -> None:
        await self._wait_until_ready()

    async def update_presence_status(self, *, show_servers: bool | None = None) -> None:
        use_server_count = self._presence_show_servers if show_servers is None else show_servers
        if use_server_count:
            text = f"림피가 {len(self.bot.guilds)}개의 서버에서 활동중이에요!"
        else:
            text = f"림피 앱을 {self.storage.count_user_settings()}명이 사용중이에요!"
        await self.bot.update_presence_status(text)
        if show_servers is None:
            self._presence_show_servers = not self._presence_show_servers

    @staticmethod
    def _notification_settings_for_summary(
        settings_list: list[GuildSettings],
        news_targets: list[GuildNewsTarget],
    ) -> list[GuildSettings]:
        target_guild_ids = {target.guild_id for target in news_targets}
        return [
            settings
            for settings in settings_list
            if (
                (settings.enabled and settings.guild_id in target_guild_ids)
                or (settings.channel_id and settings.maintenance_notifications_enabled)
            )
        ]

    @staticmethod
    def _log_connected_guild_summary(connected_guilds: list[discord.Guild]) -> None:
        LOGGER.info("참여 서버 수: %s", len(connected_guilds))
        for guild in connected_guilds:
            LOGGER.info("참여 서버: %s", _format_guild_for_log(guild, guild.id))
        LOGGER.info(
            "연결된 서버 요약: count=%s guilds=%s",
            len(connected_guilds),
            ", ".join(
                f"{_log_value(getattr(guild, 'name', None), _UNKNOWN_LOG_VALUE, 120)} ({guild.id})"
                for guild in connected_guilds
            )
            or "none",
        )

    def _log_notification_settings_summary(
        self,
        notification_settings: list[GuildSettings],
        connected_guild_ids: set[int],
    ) -> None:
        for settings in notification_settings:
            guild = self.bot.get_guild(settings.guild_id)
            guild_name = _log_value(getattr(guild, "name", None), "연결 안 됨", 120)
            channel = self.bot.get_channel(settings.channel_id) if settings.channel_id else None
            channel_name = _log_value(getattr(channel, "name", None), _UNKNOWN_LOG_VALUE, 120)
            role_name = "none"
            if guild is not None and settings.role_id is not None:
                role = guild.get_role(settings.role_id)
                role_name = _log_value(getattr(role, "name", None), _UNKNOWN_LOG_VALUE, 120)

            LOGGER.info(
                "알림 대상: guild=%s (%s), connected=%s, "
                "news_enabled=%s, maintenance_enabled=%s, "
                "channel=%s (%s), role=%s (%s), language=%s, image_delivery=%s, notification_banner=%s",
                guild_name,
                settings.guild_id,
                settings.guild_id in connected_guild_ids,
                settings.enabled,
                settings.maintenance_notifications_enabled,
                channel_name,
                settings.channel_id,
                role_name,
                settings.role_id or "none",
                settings.language,
                settings.image_delivery,
                settings.notification_banner or "none",
            )

    def _log_news_target_summary(
        self,
        news_targets: list[GuildNewsTarget],
        connected_guild_ids: set[int],
    ) -> None:
        for target in news_targets:
            if target.guild_id not in connected_guild_ids:
                continue
            guild = self.bot.get_guild(target.guild_id)
            channel = self.bot.get_channel(target.channel_id)
            LOGGER.info(
                "뉴스 언어별 대상: guild=%s (%s), channel=%s (%s), language=%s",
                _log_value(getattr(guild, "name", None), "연결 안 됨", 120),
                target.guild_id,
                _log_value(getattr(channel, "name", None), _UNKNOWN_LOG_VALUE, 120),
                target.channel_id,
                target.language,
            )

    @staticmethod
    def _log_orphan_settings(
        settings_list: list[GuildSettings],
        connected_guild_ids: set[int],
    ) -> None:
        orphan_settings = [
            settings
            for settings in settings_list
            if settings.guild_id not in connected_guild_ids
        ]
        if orphan_settings:
            LOGGER.warning(
                "봇이 연결되지 않은 서버에 저장된 설정이 있습니다: %s",
                ", ".join(str(settings.guild_id) for settings in orphan_settings),
            )

    def prune_disconnected_guild_data(self) -> int:
        connected_guild_ids = {guild.id for guild in self.bot.guilds}
        orphan_guild_ids = [
            settings.guild_id
            for settings in self.storage.list_settings()
            if settings.guild_id not in connected_guild_ids
        ]
        for guild_id in orphan_guild_ids:
            self.storage.delete_guild_data(guild_id)
        if orphan_guild_ids:
            LOGGER.info(
                "봇이 더 이상 연결되어 있지 않은 서버의 DB 데이터를 정리했습니다: %s",
                ", ".join(str(guild_id) for guild_id in orphan_guild_ids),
            )
        return len(orphan_guild_ids)

    def log_startup_summary(self) -> None:
        connected_guilds = sorted(self.bot.guilds, key=lambda item: item.id)
        connected_guild_ids = {guild.id for guild in connected_guilds}
        settings_list = self.storage.list_settings()
        news_targets = self.storage.list_all_news_targets()
        notification_settings = self._notification_settings_for_summary(
            settings_list,
            news_targets,
        )

        self._log_connected_guild_summary(connected_guilds)
        LOGGER.info(
            "알림 설정 요약: configured_guilds=%s active_targets=%s news_targets=%s",
            len(settings_list),
            len(notification_settings),
            len(news_targets),
        )
        self._log_notification_settings_summary(notification_settings, connected_guild_ids)
        self._log_news_target_summary(news_targets, connected_guild_ids)
        self._log_orphan_settings(settings_list, connected_guild_ids)

    @tasks.loop(seconds=NEWS_POLL_TICK_SECONDS)
    async def poll_news(self) -> None:
        async with self._poll_lock:
            try:
                now = datetime.now(timezone.utc)
                currently_in_window = self._is_high_frequency_window(now)
                if currently_in_window and not self._in_high_frequency_window:
                    LOGGER.info(
                        "뉴스 고빈도 추적 시작 (KST %s시~%s시, 요일 필터: %s).",
                        self.config.high_frequency_start_hour,
                        self.config.high_frequency_end_hour,
                        self.config.high_frequency_weekdays,
                    )
                elif not currently_in_window and self._in_high_frequency_window:
                    LOGGER.info("뉴스 고빈도 추적 종료.")
                self._in_high_frequency_window = currently_in_window

                if not self.bot.is_ready():
                    self._news_recovery_baseline_pending = True
                    return
                if not self._should_poll_now(now):
                    return
                self._last_poll_at = now
                await self._poll_once()
                self._startup_synced = True
            except Exception as exc:
                if _is_internet_exception(exc):
                    self._news_recovery_baseline_pending = True
                    _log_internet_exception("뉴스 폴링 실패", exc)
                else:
                    LOGGER.exception("뉴스 폴링 실패.")

    @poll_news.before_loop
    async def before_poll_news(self) -> None:
        await self._wait_until_ready()

    def _log_twitter_tracking_window_transition(
        self,
        now: datetime,
        currently_in_window: bool,
    ) -> None:
        if currently_in_window and not self._in_twitter_tracking_window:
            LOGGER.info(
                "X 게시물 추적 시작 (KST %s, 확인 간격: %s초).",
                _format_windows_label(self.config.twitter_tracking_windows_kst),
                self._current_twitter_poll_interval_seconds(now),
            )
        elif not currently_in_window and self._in_twitter_tracking_window:
            LOGGER.info("X 게시물 추적 일시 중지: 추적 시간대가 아닙니다.")
        self._in_twitter_tracking_window = currently_in_window

    def _handle_hampang_poll_exception(self, exc: Exception) -> None:
        self._hampang_x_recovery_baseline_pending = True
        self._hampang_youtube_recovery_baseline_pending = True
        if _is_internet_exception(exc):
            _log_internet_exception("햄햄팡팡 소식 자동 확인 실패", exc)
        else:
            LOGGER.exception(
                "햄햄팡팡 소식 자동 확인 실패.",
                exc_info=(type(exc), exc, exc.__traceback__),
            )

    def _handle_twitter_poll_exception(self, exc: Exception) -> None:
        self._twitter_recovery_baseline_pending = True
        if _is_internet_exception(exc):
            _log_internet_exception(_TWITTER_AUTO_CHECK_FAILURE, exc)
        elif isinstance(exc, XClientError):
            LOGGER.warning("%s: %s", _TWITTER_AUTO_CHECK_FAILURE, exc)
        else:
            LOGGER.exception("%s.", _TWITTER_AUTO_CHECK_FAILURE)

    @tasks.loop(seconds=TWITTER_POLL_TICK_SECONDS)
    async def poll_twitter_posts(self) -> None:
        async with self._twitter_poll_lock:
            try:
                now = datetime.now(timezone.utc)
                currently_in_window = self._is_twitter_tracking_window(now)
                self._log_twitter_tracking_window_transition(now, currently_in_window)

                if not self.bot.is_ready():
                    self._twitter_recovery_baseline_pending = True
                    return
                should_poll_twitter = self._should_poll_twitter_now(now)
                should_poll_hampang = self._should_poll_hampang_now(now)
                if not should_poll_twitter and not should_poll_hampang:
                    return

                poll_tasks: dict[str, asyncio.Task[int]] = {}
                if should_poll_twitter:
                    self._last_twitter_poll_at = now
                    poll_tasks["twitter"] = asyncio.create_task(self._poll_twitter_once())
                if should_poll_hampang:
                    self._last_hampang_poll_at = now
                    poll_tasks["hampang"] = asyncio.create_task(self._poll_hampang_news_once())

                results = await asyncio.gather(
                    *poll_tasks.values(),
                    return_exceptions=True,
                )
                result_by_name = dict(zip(poll_tasks, results))
                twitter_result = result_by_name.get("twitter")
                hampang_result = result_by_name.get("hampang")
                if isinstance(twitter_result, Exception):
                    raise twitter_result
                if isinstance(hampang_result, Exception):
                    self._handle_hampang_poll_exception(hampang_result)
            except Exception as exc:
                self._handle_twitter_poll_exception(exc)

    @poll_twitter_posts.before_loop
    async def before_poll_twitter_posts(self) -> None:
        await self._wait_until_ready()

    @tasks.loop(seconds=CHZZK_POLL_INTERVAL_SECONDS)
    async def poll_chzzk_live(self) -> None:
        async with self._chzzk_poll_lock:
            try:
                if not self.bot.is_ready():
                    self._chzzk_recovery_baseline_pending = True
                    return
                await self._poll_chzzk_once()
                self._chzzk_recovery_baseline_pending = False
            except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError) as exc:
                self._chzzk_recovery_baseline_pending = True
                _log_internet_exception("치지직 라이브 자동 확인 실패", exc)
            except Exception as exc:
                self._chzzk_recovery_baseline_pending = True
                if _is_internet_exception(exc):
                    _log_internet_exception("치지직 라이브 자동 확인 실패", exc)
                else:
                    LOGGER.exception("치지직 라이브 자동 확인 실패.")

    @poll_chzzk_live.before_loop
    async def before_poll_chzzk_live(self) -> None:
        await self._wait_until_ready()

    @tasks.loop(seconds=CHZZK_POLL_INTERVAL_SECONDS)
    async def poll_youtube_live(self) -> None:
        async with self._youtube_poll_lock:
            try:
                if not self.bot.is_ready():
                    self._youtube_recovery_baseline_pending = True
                    return
                await self._poll_youtube_once()
                self._youtube_recovery_baseline_pending = False
            except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError) as exc:
                self._youtube_recovery_baseline_pending = True
                _log_internet_exception("유튜브 라이브 자동 확인 실패", exc)
            except Exception as exc:
                self._youtube_recovery_baseline_pending = True
                if _is_internet_exception(exc):
                    _log_internet_exception("유튜브 라이브 자동 확인 실패", exc)
                else:
                    LOGGER.exception("유튜브 라이브 자동 확인 실패.")

    @poll_youtube_live.before_loop
    async def before_poll_youtube_live(self) -> None:
        await self._wait_until_ready()

    @tasks.loop(seconds=YOUTUBE_UPLOAD_POLL_INTERVAL_SECONDS)
    async def poll_youtube_uploads(self) -> None:
        async with self._youtube_upload_poll_lock:
            try:
                if not self.bot.is_ready():
                    self._youtube_upload_recovery_baseline_pending = True
                    return
                await self._poll_youtube_uploads_once()
                self._youtube_upload_recovery_baseline_pending = False
            except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError) as exc:
                self._youtube_upload_recovery_baseline_pending = True
                _log_internet_exception("유튜브 업로드 자동 확인 실패", exc)
            except Exception as exc:
                self._youtube_upload_recovery_baseline_pending = True
                if _is_internet_exception(exc):
                    _log_internet_exception("유튜브 업로드 자동 확인 실패", exc)
                else:
                    LOGGER.exception("유튜브 업로드 자동 확인 실패.")

    @poll_youtube_uploads.before_loop
    async def before_poll_youtube_uploads(self) -> None:
        await self._wait_until_ready()

    @tasks.loop(minutes=1)
    async def refresh_ego_gifts(self) -> None:
        try:
            now = datetime.now(KST)
            if not _is_ego_gift_update_window(now):
                return
            if self._last_ego_gift_update_check_date == now.date():
                return
            await self._refresh_ego_gift_data(source="schedule")
            self._last_ego_gift_update_check_date = now.date()
        except Exception:
            LOGGER.exception("에고 기프트 자동 갱신 실패.")

    @refresh_ego_gifts.before_loop
    async def before_refresh_ego_gifts(self) -> None:
        await self._wait_until_ready()

    async def _ensure_ego_gift_data(self) -> None:
        await self._wait_until_ready()
        try:
            if self._current_ego_gift_store_hash() is not None:
                return
            await self._refresh_ego_gift_data(source="startup")
        except Exception:
            LOGGER.exception("에고 기프트 초기 데이터 준비 실패.")

    def _current_ego_gift_store_hash(self) -> str | None:
        try:
            with self._ego_gift_store_path.open("r", encoding="utf-8") as file:
                payload = json.load(file)
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        value = payload.get("content_hash")
        return value if isinstance(value, str) and value else None

    async def _refresh_ego_gift_data(self, *, source: str) -> None:
        async with self._ego_gift_update_lock:
            try:
                from ego import crawl_ego_gifts, ego_gift_rows_hash, write_ego_gift_store
            except ImportError:
                LOGGER.warning("에고 기프트 자동 갱신을 건너뜁니다: Playwright 의존성을 찾지 못했습니다.")
                return

            previous_hash = self._current_ego_gift_store_hash()
            rows = await crawl_ego_gifts(verbose=False)
            if not rows:
                LOGGER.warning("에고 기프트 자동 갱신 결과가 비어 있어 기존 데이터를 유지합니다.")
                return

            content_hash = ego_gift_rows_hash(rows)
            if content_hash == previous_hash:
                LOGGER.info("에고 기프트 자동 확인 완료: 변경 없음 (%s개).", len(rows))
                return

            payload = write_ego_gift_store(self._ego_gift_store_path, rows)
            clear_ego_gift_cache()
            self._ego_gift_image_cache.clear()
            self._ego_gift_image_cache_bytes = 0
            LOGGER.info(
                "에고 기프트 데이터 갱신 완료: source=%s count=%s hash=%s",
                source,
                payload.get("count"),
                payload.get("content_hash"),
            )

    @tasks.loop(seconds=60)
    async def maintenance_notifications(self) -> None:
        try:
            await self._process_maintenance_notifications(source="loop")
        except Exception:
            LOGGER.exception("점검 알림 처리 실패.")

    @maintenance_notifications.before_loop
    async def before_maintenance_notifications(self) -> None:
        await self._wait_until_ready()

    @tasks.loop(minutes=15)
    async def cleanup_messages(self) -> None:
        try:
            await self._cleanup_expired_messages()
        except Exception:
            LOGGER.exception("추적 메시지 정리 실패.")
        collected = gc.collect()
        if collected:
            LOGGER.debug("gc.collect 정리 객체 수: %s", collected)

    @cleanup_messages.before_loop
    async def before_cleanup_messages(self) -> None:
        await self._wait_until_ready()

    async def _cleanup_expired_messages(self) -> None:
        now = datetime.now(timezone.utc)
        tracked = self.storage.list_tracked_messages()
        for entry in tracked:
            settings = self.storage.get_settings(entry.guild_id)
            if not settings.auto_cleanup_enabled:
                self.storage.delete_tracked_message(
                    entry.guild_id, entry.channel_id, entry.message_id
                )
                continue

            sent_at = entry.sent_at
            if sent_at.tzinfo is None:
                sent_at = sent_at.replace(tzinfo=timezone.utc)
            age = now - sent_at
            if age < timedelta(days=settings.auto_cleanup_days):
                continue

            await self._delete_tracked_message(entry.guild_id, entry.channel_id, entry.message_id)

    async def _delete_tracked_message(
        self, guild_id: int, channel_id: int, message_id: int
    ) -> None:
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden):
                channel = None

        if isinstance(channel, discord.abc.Messageable):
            try:
                message = await channel.fetch_message(message_id)
                await message.delete()
            except (discord.NotFound, discord.Forbidden):
                pass
            except discord.HTTPException:
                LOGGER.exception(
                    "채널 %s의 추적 메시지 %s 삭제 실패.", message_id, channel_id
                )

        self.storage.delete_tracked_message(guild_id, channel_id, message_id)

    async def _process_maintenance_notifications(self, *, source: str) -> int:
        notice_type, notice_key = _current_maintenance_notice()
        if notice_type is None or notice_key is None:
            LOGGER.debug("점검 알림 확인: 보낼 시간이 아닙니다 (source=%s).", source)
            return 0

        sent_count = 0
        skipped_count = 0
        target_count = 0
        embed = _maintenance_notice_embed(notice_type)
        for settings in self.storage.list_settings():
            if not self._is_maintenance_notice_target(settings):
                continue

            target_count += 1
            if self._maintenance_notice_already_sent(settings, notice_type, notice_key):
                skipped_count += 1
                continue

            sent = await self._send_maintenance_notice(settings, embed, notice_type)
            if sent:
                self.storage.mark_maintenance_notice_sent(
                    settings.guild_id,
                    notice_type=notice_type,
                    notice_key=notice_key,
                )
                sent_count += 1

        LOGGER.info(
            "점검 알림 확인 완료: source=%s, notice_type=%s, notice_key=%s, targets=%s, sent=%s, skipped=%s.",
            source,
            notice_type,
            notice_key,
            target_count,
            sent_count,
            skipped_count,
        )
        return sent_count

    def _is_maintenance_notice_target(self, settings: GuildSettings) -> bool:
        return (
            settings.maintenance_notifications_enabled
            and settings.channel_id is not None
            and self.bot.get_guild(settings.guild_id) is not None
        )

    def _maintenance_notice_already_sent(
        self,
        settings: GuildSettings,
        notice_type: str,
        notice_key: str,
    ) -> bool:
        if notice_type == "start":
            return settings.last_maintenance_start_notice == notice_key
        return settings.last_maintenance_update_notice == notice_key

    async def _send_maintenance_notice(
        self,
        settings: GuildSettings,
        embed: discord.Embed,
        notice_type: str,
    ) -> bool:
        assert settings.channel_id is not None
        channel = self.bot.get_channel(settings.channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(settings.channel_id)
            except discord.Forbidden as exc:
                LOGGER.warning(
                    "점검 알림 건너뜀: 설정된 채널 접근 불가 "
                    "(guild_id=%s, channel_id=%s, notice_type=%s, discord_code=%s).",
                    settings.guild_id,
                    settings.channel_id,
                    notice_type,
                    getattr(exc, "code", None),
                )
                return False
            except discord.NotFound as exc:
                LOGGER.warning(
                    "점검 알림 건너뜀: 설정된 채널을 찾을 수 없음 "
                    "(guild_id=%s, channel_id=%s, notice_type=%s, discord_code=%s).",
                    settings.guild_id,
                    settings.channel_id,
                    notice_type,
                    getattr(exc, "code", None),
                )
                return False
            except discord.HTTPException as exc:
                LOGGER.warning(
                    "점검 알림 건너뜀: 설정된 채널 조회 실패 "
                    "(guild_id=%s, channel_id=%s, notice_type=%s, discord_status=%s, discord_code=%s).",
                    settings.guild_id,
                    settings.channel_id,
                    notice_type,
                    exc.status,
                    getattr(exc, "code", None),
                )
                return False

        if not isinstance(channel, discord.abc.Messageable):
            LOGGER.warning(
                "점검 알림 건너뜀: 설정된 채널에 메시지를 보낼 수 없음 "
                "(guild_id=%s, channel_id=%s, notice_type=%s, channel_type=%s).",
                settings.guild_id,
                settings.channel_id,
                notice_type,
                type(channel).__name__,
            )
            return False

        guild_log, channel_log = self._destination_logs(
            settings.guild_id,
            channel,
            settings.channel_id,
        )
        try:
            role = discord.Object(id=settings.role_id) if settings.role_id else None
            await channel.send(
                content=f"<@&{settings.role_id}>" if settings.role_id else None,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(
                    everyone=False,
                    users=False,
                    roles=[role] if role else [],
                ),
            )
        except (discord.Forbidden, discord.NotFound):
            LOGGER.warning(
                "점검 알림 전송 실패: Discord가 메시지 전송을 거부했습니다 "
                "(guild_id=%s, channel_id=%s, notice_type=%s).",
                settings.guild_id,
                settings.channel_id,
                notice_type,
            )
            return False
        except discord.HTTPException as exc:
            LOGGER.warning(
                "점검 알림 전송 실패: Discord HTTP 오류 "
                "(guild_id=%s, channel_id=%s, notice_type=%s, discord_status=%s, discord_code=%s).",
                settings.guild_id,
                settings.channel_id,
                notice_type,
                exc.status,
                getattr(exc, "code", None),
            )
            return False

        LOGGER.info(
            "점검 알림 전송 완료 | %s | %s | notice_type=%s.",
            guild_log,
            channel_log,
            notice_type,
        )
        return True

    async def _poll_once(self) -> int:
        if self.news_source is None and self.x_source is None:
            return 0

        targets_by_language = self._news_targets_by_language()
        if not targets_by_language:
            return 0

        posts_by_language, changed_post_ids, had_upstream_failure = await self._combined_posts_by_language()
        if had_upstream_failure:
            self._news_recovery_baseline_pending = True
            LOGGER.info(
                "뉴스 업스트림 확인 실패가 있어 이번 자동 전송은 건너뜁니다. "
                "다음 정상 확인에서 기준선을 갱신합니다."
            )
            return 0

        if self._news_recovery_baseline_pending:
            updated = self._mark_news_targets_recovery_baseline(
                targets_by_language,
                posts_by_language,
            )
            self._news_recovery_baseline_pending = False
            LOGGER.info(
                "네트워크 복구 후 뉴스 기준선을 갱신했습니다. 누적 소식 자동 전송은 건너뜁니다 "
                "(targets=%s).",
                updated,
            )
            return 0

        send_semaphore = asyncio.Semaphore(NEWS_TARGET_SEND_CONCURRENCY)
        target_tasks = self._news_target_tasks_for_poll(
            targets_by_language,
            posts_by_language,
            send_semaphore,
        )

        if target_tasks:
            results = await asyncio.gather(*target_tasks, return_exceptions=True)
            announced_count = self._sum_news_target_results(results)
        else:
            announced_count = 0

        await self._broadcast_post_updates(changed_post_ids)
        return announced_count

    def _cached_posts_after_steam_sync_failure(
        self,
        language: str,
        exc: Exception,
    ) -> list[NewsPost]:
        message = f"Steam 뉴스 자동 확인 실패: language={language}. 저장된 소식을 사용합니다"
        if _is_internet_exception(exc):
            _log_internet_exception(message, exc)
        else:
            LOGGER.warning(
                "%s.",
                message,
                exc_info=(type(exc), exc, exc.__traceback__),
            )
        return self.storage.search_posts("", limit=NEWS_POST_LIMIT, language=language)

    def _track_successful_steam_sync_result(
        self,
        language: str,
        posts: list[NewsPost],
        fetched_posts: list[NewsPost],
        newest_new_posts: list[tuple[str, NewsPost]],
    ) -> None:
        if posts and self.storage.get_post(posts[0].post_id) is None:
            newest_new_posts.append((language, posts[0]))
        fetched_posts.extend(posts[:NEWS_POST_LIMIT])

    async def _sync_global_news_cache(self) -> tuple[dict[str, list[NewsPost]], list[str], bool]:
        if self.news_source is None:
            return {}, [], False

        posts_by_language: dict[str, list[NewsPost]] = {}
        fetched_posts: list[NewsPost] = []
        newest_new_posts: list[tuple[str, NewsPost]] = []
        changed: list[str] = []
        had_fetch_failure = False
        results = await asyncio.gather(
            *(
                self.news_source.fetch_recent_posts(language, limit=NEWS_POST_LIMIT)
                for language in SYNC_LANGUAGES
            ),
            return_exceptions=True,
        )
        for language, result in zip(SYNC_LANGUAGES, results):
            if isinstance(result, Exception):
                had_fetch_failure = True
                posts = self._cached_posts_after_steam_sync_failure(language, result)
            else:
                posts = result
                self._track_successful_steam_sync_result(
                    language,
                    posts,
                    fetched_posts,
                    newest_new_posts,
                )
            posts_by_language[language] = posts[:NEWS_POST_LIMIT]

        if fetched_posts:
            _, changed = self.storage.save_posts(fetched_posts)
            for language, post in newest_new_posts:
                LOGGER.info(
                    "Steam 새 소식 감지: language=%s, post_id=%s, title=%r, url=%s",
                    language,
                    post.post_id,
                    post.title,
                    post.url,
                )
            self._schedule_image_cache_warmup(fetched_posts)
        return posts_by_language, changed, had_fetch_failure

    def _handle_combined_steam_result(
        self,
        result: tuple[dict[str, list[NewsPost]], list[str], bool] | BaseException,
    ) -> tuple[dict[str, list[NewsPost]], list[str], bool]:
        if not isinstance(result, Exception):
            posts_by_language, changed, steam_had_failure = result
            return posts_by_language, changed, steam_had_failure

        if _is_internet_exception(result):
            _log_internet_exception(
                "Steam 뉴스 자동 확인 실패. 저장된 Steam 소식으로 X 링크 비교를 계속합니다",
                result,
            )
        else:
            LOGGER.warning(
                "Steam 뉴스 자동 확인 실패. 저장된 Steam 소식으로 X 링크 비교를 계속합니다.",
                exc_info=(type(result), result, result.__traceback__),
            )
        return self._cached_posts_by_language(), [], True

    def _handle_combined_twitter_result(
        self,
        result: tuple[int, list[TwitterPost]] | BaseException,
    ) -> tuple[list[TwitterPost], bool]:
        if not isinstance(result, Exception):
            _, twitter_posts = result
            return twitter_posts, False

        if isinstance(result, XClientError):
            if _is_internet_exception(result):
                _log_internet_exception(_TWITTER_AUTO_CHECK_FAILURE, result)
            else:
                LOGGER.warning("%s: %s", _TWITTER_AUTO_CHECK_FAILURE, result)
        elif _is_internet_exception(result):
            _log_internet_exception(
                f"{_TWITTER_AUTO_CHECK_FAILURE}. Steam 자동 알림은 계속 처리합니다",
                result,
            )
        else:
            LOGGER.warning(
                "%s. Steam 자동 알림은 계속 처리합니다.",
                _TWITTER_AUTO_CHECK_FAILURE,
                exc_info=(type(result), result, result.__traceback__),
            )
        return [], True

    def _combine_steam_and_twitter_news(
        self,
        posts_by_language: dict[str, list[NewsPost]],
        twitter_posts: list[TwitterPost],
    ) -> dict[str, list[NewsPost]]:
        cached_linked_steam_posts = self._cached_steam_posts_for_twitter_links(twitter_posts)
        steam_posts = _dedupe_posts_by_id([
            post
            for posts in posts_by_language.values()
            for post in posts
        ] + cached_linked_steam_posts)
        twitter_news = _twitter_news_without_duplicate_steam_links(
            _twitter_posts_as_news_posts(twitter_posts, steam_posts)
        )
        if not twitter_news and not cached_linked_steam_posts:
            return posts_by_language

        for language in SYNC_LANGUAGES:
            steam_posts_for_language = _dedupe_posts_by_id(
                [
                    *posts_by_language.get(language, []),
                    *(
                        post
                        for post in cached_linked_steam_posts
                        if _post_language(post) == language
                    ),
                ]
            )
            steam_language_posts = _steam_posts_without_fast_twitter_duplicates(
                steam_posts_for_language,
                twitter_news,
            )
            combined = [*steam_language_posts, *twitter_news]
            posts_by_language[language] = _sort_posts_newest_first(combined)[:NEWS_POST_LIMIT]
        return posts_by_language

    async def _combined_posts_by_language(self) -> tuple[dict[str, list[NewsPost]], list[str], bool]:
        now = datetime.now(timezone.utc)
        news_task = asyncio.create_task(self._sync_global_news_cache())
        if self._is_twitter_tracking_window(now):
            twitter_task = asyncio.create_task(
                self._sync_twitter_posts(cache_ttl=self._twitter_fetch_cache_ttl(now))
            )
            news_result, twitter_result = await asyncio.gather(
                news_task,
                twitter_task,
                return_exceptions=True,
            )
        else:
            news_result = await news_task
            twitter_result = (
                0,
                self.storage.search_twitter_posts("", limit=TWITTER_POST_LIMIT),
            )
        posts_by_language, changed, steam_had_failure = self._handle_combined_steam_result(
            news_result
        )
        twitter_posts, twitter_had_failure = self._handle_combined_twitter_result(twitter_result)
        had_upstream_failure = steam_had_failure or twitter_had_failure
        posts_by_language = self._combine_steam_and_twitter_news(
            posts_by_language,
            twitter_posts,
        )
        return posts_by_language, changed, had_upstream_failure

    def _mark_news_targets_recovery_baseline(
        self,
        targets_by_language: dict[str, list[GuildNewsTarget]],
        posts_by_language: dict[str, list[NewsPost]],
    ) -> int:
        updated = 0
        for language, target_list in targets_by_language.items():
            posts = posts_by_language.get(language, [])
            if not posts:
                continue
            for target in target_list:
                settings = self.storage.get_settings(target.guild_id)
                target_posts = self._posts_for_source_mode(posts, settings)[:NEWS_POST_LIMIT]
                if not target_posts:
                    continue
                post_ids = [post.post_id for post in target_posts]
                self.storage.mark_news_target_posts_seen(target.target_id, post_ids)
                self.storage.mark_posts_seen(target.guild_id, post_ids)
                self.storage.set_last_seen_post_id(target.guild_id, target_posts[0].post_id)
                updated += 1
        return updated

    def _news_target_tasks_for_poll(
        self,
        targets_by_language: dict[str, list[GuildNewsTarget]],
        posts_by_language: dict[str, list[NewsPost]],
        send_semaphore: asyncio.Semaphore,
    ) -> list[asyncio.Task[int]]:
        async def process_target(
            settings: GuildSettings,
            target: GuildNewsTarget,
            guild_posts: list[NewsPost],
        ) -> int:
            async with send_semaphore:
                return await self._process_news_target(settings, target, guild_posts)

        target_tasks: list[asyncio.Task[int]] = []
        for language, target_list in targets_by_language.items():
            posts = posts_by_language.get(language, [])
            if not posts:
                continue
            for target in target_list:
                settings = self.storage.get_settings(target.guild_id)
                guild_posts = self._posts_for_source_mode(
                    posts,
                    settings,
                    defer_linked_twitter=True,
                )[:NEWS_POST_LIMIT]
                if not guild_posts:
                    continue
                if not settings.enabled:
                    self._mark_news_target_posts_seen_for_poll(target, guild_posts)
                    continue
                target_tasks.append(
                    asyncio.create_task(process_target(settings, target, guild_posts))
                )
        return target_tasks

    @staticmethod
    def _sum_news_target_results(results: list[int | BaseException]) -> int:
        announced_count = 0
        for result in results:
            if isinstance(result, Exception):
                if _is_internet_exception(result):
                    _log_internet_exception(
                        "뉴스 자동 전송 대상 처리 실패",
                        result,
                        level=logging.ERROR,
                    )
                else:
                    LOGGER.error(
                        "뉴스 자동 전송 대상 처리 실패.",
                        exc_info=(type(result), result, result.__traceback__),
                    )
                continue
            announced_count += result
        return announced_count

    def _cached_posts_by_language(self) -> dict[str, list[NewsPost]]:
        return {
            language: self.storage.search_posts("", limit=NEWS_POST_LIMIT, language=language)
            for language in SYNC_LANGUAGES
        }

    def _cached_steam_posts_for_twitter_links(
        self,
        twitter_posts: list[TwitterPost],
    ) -> list[NewsPost]:
        post_ids = [
            f"steam:{language}:{post_key}"
            for post_key in _steam_news_post_ids_for_twitter_posts(twitter_posts)
            for language in SYNC_LANGUAGES
        ]
        cached_posts: list[NewsPost] = []
        for post_id in dict.fromkeys(post_ids):
            cached = self.storage.get_post(post_id)
            if cached is not None:
                cached_posts.append(cached)
        return cached_posts

    def _posts_for_source_mode(
        self,
        posts: list[NewsPost],
        settings: GuildSettings | None = None,
        source_mode: str | None = None,
        *,
        defer_linked_twitter: bool = False,
    ) -> list[NewsPost]:
        mode = (
            source_mode
            or (settings.news_source_mode if settings else DEFAULT_NEWS_SOURCE_MODE)
            or DEFAULT_NEWS_SOURCE_MODE
        )
        if mode == NEWS_SOURCE_STEAM:
            return [post for post in posts if not _is_twitter_news_post(post)]
        if mode == NEWS_SOURCE_TWITTER:
            return [post for post in posts if _is_twitter_news_post(post)]
        selected: list[NewsPost] = []
        for post in posts:
            if _twitter_news_prefers_available_steam(post, posts):
                self._twitter_steam_grace_started_at.pop(post.post_id, None)
                self._twitter_steam_defer_logged_post_ids.discard(post.post_id)
                self._twitter_steam_retry_count.pop(post.post_id, None)
                continue
            if defer_linked_twitter and self._defer_linked_twitter_for_steam(post):
                continue
            selected.append(post)
        return selected

    def _defer_linked_twitter_for_steam(self, post: NewsPost) -> bool:
        if not _is_twitter_news_post(post) or not _steam_news_link_keys_for_news_post(post):
            return False
        if _is_twitter_priority_poll_window(datetime.now(timezone.utc)):
            return False

        if self._twitter_steam_retry_count.get(post.post_id, 0) >= 2:
            return False

        now = perf_counter()
        started_at = self._twitter_steam_grace_started_at.setdefault(post.post_id, now)
        elapsed = now - started_at
        if elapsed >= TWITTER_STEAM_PREFERENCE_GRACE_SECONDS:
            self._twitter_steam_retry_count[post.post_id] = self._twitter_steam_retry_count.get(post.post_id, 0) + 1
            self._twitter_steam_grace_started_at[post.post_id] = now
            if post.post_id not in self._twitter_steam_defer_logged_post_ids:
                self._twitter_steam_defer_logged_post_ids.add(post.post_id)
        if post.post_id not in self._twitter_steam_defer_logged_post_ids:
            self._twitter_steam_defer_logged_post_ids.add(post.post_id)
            LOGGER.info(
                "Steam 링크가 있는 X 소식을 잠시 보류합니다. 다른 소식 전송은 계속 진행합니다 "
                "(post_id=%s, grace=%s초).",
                post.post_id,
                TWITTER_STEAM_PREFERENCE_GRACE_SECONDS,
            )
        return True

    def _combined_cached_posts(
        self,
        query: str = "",
        *,
        limit: int = 25,
        language: str | None = None,
        settings: GuildSettings | None = None,
        source_mode: str | None = None,
    ) -> list[NewsPost]:
        steam_posts = self.storage.search_posts(query, limit=limit, language=language)
        twitter_posts = self.storage.search_twitter_posts(query, limit=limit)
        posts = [*steam_posts, *_twitter_posts_as_news_posts(twitter_posts, steam_posts)]
        return self._posts_for_source_mode(
            _sort_posts_newest_first(posts),
            settings,
            source_mode=source_mode,
        )[:limit]

    async def _get_combined_post(
        self,
        value: str,
        *,
        language: str | None = None,
        settings: GuildSettings | None = None,
        source_mode: str | None = None,
    ) -> NewsPost | None:
        steam_post = self.storage.get_post_by_id_or_title(value, language=language)
        if steam_post is None:
            steam_post = self.storage.get_post_by_id_or_title(value)
        twitter_post = None
        if value.startswith(_TWITTER_NEWS_POST_ID_PREFIX):
            twitter_post = self.storage.get_twitter_post(
                value.removeprefix(_TWITTER_NEWS_POST_ID_PREFIX)
            )
        if twitter_post is None:
            twitter_post = self.storage.get_twitter_post_by_id_or_title(value)
        if twitter_post is not None:
            twitter_post = await self._refresh_stale_twitter_post(twitter_post)

        steam_posts = [steam_post] if steam_post is not None else []
        candidates = [*steam_posts]
        if twitter_post is not None:
            candidates.extend(_twitter_posts_as_news_posts([twitter_post], steam_posts))
        filtered = self._posts_for_source_mode(candidates, settings, source_mode=source_mode)
        return filtered[0] if filtered else None

    async def _selectable_news_posts(
        self,
        *,
        language: str | None = None,
        settings: GuildSettings | None = None,
        source_mode: str | None = None,
    ) -> list[NewsPost]:
        return await asyncio.to_thread(
            self._combined_cached_posts,
            "",
            limit=NEWS_SELECT_POST_LIMIT,
            language=language,
            settings=settings,
            source_mode=source_mode,
        )

    async def _send_news_post_select_menu(
        self,
        interaction: discord.Interaction,
        posts: list[NewsPost],
        *,
        mode: str,
        source_mode: str,
        language: str,
        private: bool = True,
        attach_photos: bool = True,
        channel_id: int | None = None,
        role_id: int | None = None,
    ) -> None:
        if not posts:
            message = "선택할 수 있는 게시물이 없어요. 림피가 자동 동기화한 뒤 다시 시도해 주세요."
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
            return

        view = NewsPostSelectView(
            self,
            interaction.user.id,
            posts,
            mode=mode,
            source_mode=source_mode,
            language=language,
            private=private,
            attach_photos=attach_photos,
            channel_id=channel_id,
            role_id=role_id,
        )
        if interaction.response.is_done():
            await interaction.followup.send(
                embed=view.build_embed(),
                view=view,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        else:
            await interaction.response.send_message(
                embed=view.build_embed(),
                view=view,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

    async def _show_previous_news_by_selected_post(
        self,
        interaction: discord.Interaction,
        title: str,
        *,
        source_mode: str,
        language: str,
        private: bool,
        attach_photos: bool,
        settings: GuildSettings | None = None,
    ) -> None:
        resolved_settings = settings
        if resolved_settings is None and interaction.guild_id:
            resolved_settings = self.storage.get_settings(interaction.guild_id)
        post = await self._get_combined_post(
            title,
            language=language,
            settings=resolved_settings,
            source_mode=source_mode,
        )
        if post is None:
            if interaction.response.is_done():
                await interaction.followup.send(
                    "아직 저장된 게시물을 찾지 못했어요. 림피가 자동 동기화한 뒤 다시 선택해 주세요.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "아직 저장된 게시물을 찾지 못했어요. 림피가 자동 동기화한 뒤 다시 선택해 주세요.",
                    ephemeral=True,
                )
            return

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=private, thinking=True)
        sent_messages = await self._send_news_post_followups(
            interaction,
            post,
            private=private,
            attach_photos=attach_photos,
        )
        if not private:
            for message in sent_messages:
                await self._track_manual_message(
                    interaction.guild_id, interaction.channel_id, message
                )

    async def _manual_news_delivery_targets(
        self,
        interaction: discord.Interaction,
        post: NewsPost,
        language: str,
        channel_id: int | None,
        configured_targets: list[GuildNewsTarget],
    ) -> list[tuple[discord.abc.Messageable, str]] | None:
        if channel_id is not None:
            resolved_channel = await self._resolve_target_channel(None, channel_id)
            if resolved_channel is None:
                await interaction.followup.send(
                    "보낼 채널을 찾지 못했어요. 채널 권한을 확인해주세요.",
                    ephemeral=True,
                )
                return None
            channel_languages = [
                target.language
                for target in configured_targets
                if target.channel_id == channel_id
            ]
            if not channel_languages:
                channel_languages = [_post_language(post) or language]
            return [(resolved_channel, target_language) for target_language in channel_languages]

        delivery_targets: list[tuple[discord.abc.Messageable, str]] = []
        for news_target in configured_targets:
            resolved = await self._resolve_target_channel(None, news_target.channel_id)
            if resolved is not None:
                delivery_targets.append((resolved, news_target.language))
        if delivery_targets:
            return delivery_targets

        await interaction.followup.send(
            "보낼 채널이 없어요. 채널 옵션을 지정하거나 /소식채널설정으로 언어별 채널을 설정해주세요.",
            ephemeral=True,
        )
        return None

    async def _send_manual_news_to_targets(
        self,
        post: NewsPost,
        settings: GuildSettings,
        role_to_send: int | None,
        delivery_targets: list[tuple[discord.abc.Messageable, str]],
    ) -> ManualNewsSendResult:
        result = ManualNewsSendResult([], [], set())
        mentioned_channel_ids: set[int] = set()

        for target, target_language in delivery_targets:
            resolved_channel_id = getattr(target, "id", None)
            target_post = self._post_variant_for_language(post, target_language)
            if target_post is None:
                result.missing_languages.add(target_language)
                result.failed_channel_ids.append(resolved_channel_id)
                continue

            mention_role = not (
                isinstance(resolved_channel_id, int)
                and resolved_channel_id in mentioned_channel_ids
            )
            try:
                await self._broadcast_post(
                    target,
                    target_post,
                    role_to_send if mention_role else None,
                    banner_filename=settings.notification_banner,
                    image_delivery=settings.image_delivery,
                )
            except discord.Forbidden:
                result.failed_channel_ids.append(resolved_channel_id)
                continue
            except discord.HTTPException:
                LOGGER.exception("수동 뉴스 전송 실패.")
                result.failed_channel_ids.append(resolved_channel_id)
                continue

            if isinstance(resolved_channel_id, int):
                result.sent_channel_ids.append(resolved_channel_id)
                mentioned_channel_ids.add(resolved_channel_id)
        return result

    @staticmethod
    def _manual_news_send_message(result: ManualNewsSendResult) -> str:
        sent_text = ", ".join(
            f"<#{sent_channel_id}>" for sent_channel_id in result.sent_channel_ids
        )
        message = f"{sent_text}에 소식을 보냈어요."
        if result.failed_channel_ids:
            failed_text = ", ".join(
                f"<#{failed_channel_id}>" if failed_channel_id else "지정한 채널"
                for failed_channel_id in result.failed_channel_ids
            )
            message = f"{message}\n전송 실패: {failed_text}"
        if result.missing_languages:
            missing_text = ", ".join(
                _language_label(missing_language)
                for missing_language in sorted(result.missing_languages)
            )
            message = f"{message}\n같은 소식의 {missing_text} 게시물을 아직 찾지 못했어요."
        return message

    async def _send_news_by_selected_post(
        self,
        interaction: discord.Interaction,
        title: str,
        *,
        source_mode: str,
        channel_id: int | None = None,
        role_id: int | None = None,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.followup.send("서버 안에서만 사용할 수 있어요.", ephemeral=True)
            return

        language = self._interaction_language(interaction)
        settings = self.storage.get_settings(interaction.guild_id)
        post = await self._get_combined_post(
            title,
            language=language,
            settings=settings,
            source_mode=source_mode,
        )
        if post is None:
            await interaction.followup.send("해당 게시물을 찾지 못했어요.", ephemeral=True)
            return

        configured_targets = self.storage.list_news_targets(interaction.guild_id)
        delivery_targets = await self._manual_news_delivery_targets(
            interaction,
            post,
            language,
            channel_id,
            configured_targets,
        )
        if delivery_targets is None:
            return

        role_to_send = role_id if role_id is not None else settings.role_id
        result = await self._send_manual_news_to_targets(
            post,
            settings,
            role_to_send,
            delivery_targets,
        )

        if not result.sent_channel_ids:
            await interaction.followup.send(
                "소식을 보낼 수 있는 채널이 없어요. 채널 권한을 확인해주세요.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            self._manual_news_send_message(result),
            ephemeral=True,
        )

    async def _latest_combined_post(
        self,
        *,
        language: str | None = None,
        settings: GuildSettings | None = None,
    ) -> NewsPost | None:
        steam_post = self.storage.get_latest_post(language)
        twitter_post = self.storage.get_latest_twitter_post()
        if twitter_post is not None:
            twitter_post = await self._refresh_stale_twitter_post(twitter_post)
        steam_posts = [steam_post] if steam_post is not None else []
        candidates = [*steam_posts]
        if twitter_post is not None:
            candidates.extend(_twitter_posts_as_news_posts([twitter_post], steam_posts))
        filtered = self._posts_for_source_mode(_sort_posts_newest_first(candidates), settings)
        return filtered[0] if filtered else None

    async def _refresh_stale_twitter_post(self, post: TwitterPost) -> TwitterPost:
        if self.x_source is None or not _twitter_post_needs_refresh(post):
            return post
        try:
            refreshed = await self.x_source.refresh_post(post)
        except Exception:
            LOGGER.exception("저장된 X 게시물 보강 실패 (post_id=%s).", post.post_id)
            return post
        if refreshed != post:
            self.storage.save_twitter_posts([refreshed])
        return refreshed

    def _settings_by_language(self) -> dict[str, list[GuildSettings]]:
        settings_by_language: dict[str, list[GuildSettings]] = {}
        for settings in self.storage.list_settings():
            if self.bot.get_guild(settings.guild_id) is None:
                LOGGER.debug(
                    "봇이 설정된 서버에 연결되어 있지 않아 자동 전송 설정을 건너뜁니다 "
                    "(guild_id=%s, channel_id=%s).",
                    settings.guild_id,
                    settings.channel_id or "none",
                )
                continue
            language = settings.language or self.config.steam_language
            settings_by_language.setdefault(language, []).append(settings)
        return settings_by_language

    def _news_targets_by_language(self) -> dict[str, list[GuildNewsTarget]]:
        targets_by_language: dict[str, list[GuildNewsTarget]] = {}
        for target in self.storage.list_all_news_targets():
            if self.bot.get_guild(target.guild_id) is None:
                LOGGER.debug(
                    "봇이 설정된 서버에 연결되어 있지 않아 자동 전송 대상을 건너뜁니다 "
                    "(guild_id=%s, channel_id=%s, language=%s).",
                    target.guild_id,
                    target.channel_id,
                    target.language,
                )
                continue
            targets_by_language.setdefault(target.language, []).append(target)
        return targets_by_language

    def _interaction_uses_user_install(self, interaction: discord.Interaction) -> bool:
        owners = getattr(interaction, "_integration_owners", {}) or {}
        return 1 in owners

    def _bot_is_missing_from_interaction_guild(
        self, interaction: discord.Interaction
    ) -> bool:
        return interaction.guild_id is not None and self.bot.get_guild(interaction.guild_id) is None

    def _requires_external_news_send_confirmation(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        return interaction.guild_id is None or self._bot_is_missing_from_interaction_guild(interaction)

    async def _confirm_external_news_send(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        if not self._requires_external_news_send_confirmation(interaction):
            return True

        embed = discord.Embed(
            title="정말 봇이 없는 서버(DM)에서 이 소식을 보내시겠습니까?",
            description="당사자가 불편해 할 수도 있으니 생각하고 결정해주세요!",
            color=discord.Color.from_rgb(179, 28, 28),
        )
        view = ExternalNewsSendConfirmView(interaction.user.id)
        await interaction.response.send_message(
            embed=embed,
            view=view,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

        await view.wait()
        if view.confirmed:
            await interaction.edit_original_response(
                content="소식을 보낼게요.",
                embed=None,
                view=None,
            )
            return True

        message = (
            "소식 보내기를 취소했어요."
            if view.confirmed is False
            else "시간이 지나 소식 보내기를 취소했어요."
        )
        await interaction.edit_original_response(
            content=message,
            embed=None,
            view=None,
        )
        return False

    async def _allow_public_news_send(
        self,
        interaction: discord.Interaction,
        *,
        private: bool,
    ) -> bool:
        if private or interaction.guild_id is None:
            return True

        settings = self.storage.get_settings(interaction.guild_id)
        if settings.public_news_lookup_allowed:
            return True

        message = "이 서버는 /서버설정에서 공개 소식 전송을 막아두었어요."
        if interaction.response.is_done():
            await interaction.followup.send(
                message,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        else:
            await interaction.response.send_message(
                message,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        return False

    def _interaction_user_values(
        self, interaction: discord.Interaction
    ) -> tuple[int, str, str | None]:
        user = interaction.user
        user_id = int(user.id)
        username = getattr(user, "name", None) or str(user)
        nickname = getattr(user, "nick", None)
        if not nickname:
            nickname = getattr(user, "global_name", None) or getattr(user, "display_name", None)
        if nickname == username:
            nickname = None
        return user_id, username, nickname

    def _remember_interaction_user(self, interaction: discord.Interaction) -> None:
        user_id, username, nickname = self._interaction_user_values(interaction)
        self.storage.upsert_user_settings(
            user_id,
            username=username,
            nickname=nickname,
        )

    def _interaction_language(self, interaction: discord.Interaction) -> str:
        if interaction.guild_id is None or self._interaction_uses_user_install(interaction):
            self._remember_interaction_user(interaction)
            return self.storage.get_user_settings(interaction.user.id).language
        return self.storage.get_settings(interaction.guild_id).language

    def _interaction_image_delivery(self, interaction: discord.Interaction) -> str:
        if interaction.guild_id is None or self._interaction_uses_user_install(interaction):
            return IMAGE_DELIVERY_EMBEDS
        return self.storage.get_settings(interaction.guild_id).image_delivery

    def _interaction_banner_filename(
        self,
        interaction: discord.Interaction,
        *,
        private: bool,
    ) -> str | None:
        if (
            private
            or interaction.guild_id is None
            or self._interaction_uses_user_install(interaction)
        ):
            self._remember_interaction_user(interaction)
            return self.storage.get_user_settings(interaction.user.id).news_banner
        return self.storage.get_settings(interaction.guild_id).notification_banner

    async def _ego_gift_image_file(self, gift: EgoGift) -> discord.File | None:
        data = await self._get_ego_gift_image_bytes(gift)
        if data is None:
            return None
        # discord.File은 1회용(스트림 소비)이라 캐시 바이트로 매번 새 인스턴스를 만든다.
        return discord.File(io.BytesIO(data), filename="ego_gift_image.png")

    async def _get_ego_gift_image_bytes(self, gift: EgoGift) -> bytes | None:
        cache_key = gift.name
        cached = self._ego_gift_image_cache.get(cache_key)
        if cached is not None:
            # LRU: 최근 사용 항목을 뒤로 보낸다.
            self._ego_gift_image_cache[cache_key] = self._ego_gift_image_cache.pop(cache_key)
            return cached

        # 동일 기프트를 동시에 조회하면 진행 중인 빌드 태스크를 공유해 중복 다운로드를 막는다.
        task = self._ego_gift_image_tasks.get(cache_key)
        if task is None:
            task = asyncio.create_task(self._build_ego_gift_image_bytes(gift))
            self._ego_gift_image_tasks[cache_key] = task
            task.add_done_callback(
                lambda _t, key=cache_key: self._ego_gift_image_tasks.pop(key, None)
            )

        data = await asyncio.shield(task)
        if data is not None:
            self._cache_ego_gift_image(cache_key, data)
        return data

    async def _build_ego_gift_image_bytes(self, gift: EgoGift) -> bytes | None:
        image_url = _normalize_image_url(gift.image_url)
        if not image_url:
            return None

        fallback_url = await self._ego_gift_fallback_image_url(gift.name)
        downloaded = None
        # i.namu.wiki 이미지 주소는 브라우저에서는 열려도 봇 요청에는 자주 403을
        # 반환한다. 해당 주소는 안정적인 대체 CDN을 먼저 사용해 응답 지연을 줄인다.
        if fallback_url and _is_namu_wiki_image_url(image_url):
            downloaded = await self._download_image(fallback_url)
            if downloaded is not None:
                image_url = fallback_url
        if downloaded is None:
            downloaded = await self._download_image(image_url)
        if downloaded is None and fallback_url and fallback_url != image_url:
            downloaded = await self._download_image(fallback_url)
            if downloaded is not None:
                image_url = fallback_url
        if downloaded is None:
            LOGGER.warning("에고 기프트 이미지 첨부 변환 실패: %s (%s)", gift.name, image_url)
            return None

        data, content_type = downloaded
        # PIL 디코드/리사이즈는 CPU 작업이라 스레드로 빼서 이벤트 루프를 막지 않는다.
        async with self._ego_gift_image_semaphore:
            return await asyncio.to_thread(
                _process_ego_gift_image_bytes, data, content_type
            )

    def _cache_ego_gift_image(self, cache_key: str, data: bytes) -> None:
        prev = self._ego_gift_image_cache.pop(cache_key, None)
        if prev is not None:
            self._ego_gift_image_cache_bytes -= len(prev)
        self._ego_gift_image_cache[cache_key] = data
        self._ego_gift_image_cache_bytes += len(data)
        while self._ego_gift_image_cache and (
            len(self._ego_gift_image_cache) > EGO_GIFT_IMAGE_CACHE_MAX_ITEMS
            or self._ego_gift_image_cache_bytes > EGO_GIFT_IMAGE_CACHE_MAX_BYTES
        ):
            oldest_key, oldest_data = next(iter(self._ego_gift_image_cache.items()))
            self._ego_gift_image_cache.pop(oldest_key, None)
            self._ego_gift_image_cache_bytes -= len(oldest_data)

    def _schedule_ego_gift_image_warmup(self, gifts: list[EgoGift]) -> None:
        warm_gifts: list[EgoGift] = []
        seen_names: set[str] = set()
        for gift in gifts:
            if len(warm_gifts) >= EGO_GIFT_IMAGE_WARMUP_LIMIT:
                break
            if not gift.image_url or gift.name in seen_names:
                continue
            seen_names.add(gift.name)
            if (
                gift.name in self._ego_gift_image_cache
                or gift.name in self._ego_gift_image_tasks
            ):
                continue
            warm_gifts.append(gift)

        if not warm_gifts:
            return

        task = asyncio.create_task(self._warm_ego_gift_images(warm_gifts))
        task.add_done_callback(self._log_background_task_result)

    async def _warm_ego_gift_images(self, gifts: list[EgoGift]) -> None:
        semaphore = asyncio.Semaphore(EGO_GIFT_IMAGE_WARMUP_CONCURRENCY)

        async def warm_one(gift: EgoGift) -> None:
            async with semaphore:
                await self._get_ego_gift_image_bytes(gift)

        await asyncio.gather(*(warm_one(gift) for gift in gifts))

    async def _ego_gift_fallback_image_url(self, name: str) -> str | None:
        if self._ego_gift_image_fallbacks is None:
            if self._ego_gift_image_fallbacks_task is None:
                self._ego_gift_image_fallbacks_task = asyncio.create_task(
                    self._load_ego_gift_image_fallbacks()
                )
            try:
                self._ego_gift_image_fallbacks = await self._ego_gift_image_fallbacks_task
            finally:
                self._ego_gift_image_fallbacks_task = None
        fallbacks = self._ego_gift_image_fallbacks
        if not fallbacks:
            return None
        return fallbacks.get(name) or fallbacks.get(_ego_gift_fallback_key(name))

    async def _load_ego_gift_image_fallbacks(self) -> dict[str, str]:
        timeout = aiohttp.ClientTimeout(total=IMAGE_DOWNLOAD_TIMEOUT_SECONDS)
        try:
            async with self.session.get(
                EGO_GIFT_FALLBACK_IMAGE_INDEX_URL,
                timeout=timeout,
                headers=_image_request_headers(EGO_GIFT_FALLBACK_IMAGE_INDEX_URL),
            ) as response:
                if response.status >= 400:
                    LOGGER.warning(
                        "에고 기프트 대체 이미지 목록 요청 실패 (HTTP %s).",
                        response.status,
                    )
                    return {}
                html = await response.text()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            _log_internet_exception("에고 기프트 대체 이미지 목록 요청 실패", exc)
            return {}

        payload = _ego_gift_fallback_payload(html)
        if payload is None:
            LOGGER.warning("에고 기프트 대체 이미지 목록을 찾지 못했습니다.")
            return {}

        fallbacks: dict[str, str] = {}
        for item in payload.get("egoGifts", []):
            if not isinstance(item, dict):
                continue
            gift_name = item.get("name")
            gift_id = item.get("ID")
            if not gift_name or gift_id is None:
                continue
            gift_name_text = str(gift_name)
            fallback_url = f"{EGO_GIFT_FALLBACK_IMAGE_BASE_URL}/{gift_id}.png"
            fallbacks[gift_name_text] = fallback_url
            fallbacks.setdefault(_ego_gift_fallback_key(gift_name_text), fallback_url)
        return fallbacks

    def _post_variant_for_language(
        self,
        post: NewsPost,
        language: str,
    ) -> NewsPost | None:
        if _is_twitter_news_post(post):
            return post
        if _post_language(post) == language:
            return post

        post_key = _post_language_independent_id(post)
        if post_key is None:
            return post if not _post_language(post) else None

        return self.storage.get_post(f"steam:{language}:{post_key}")

    def _should_poll_now(self, now: datetime) -> bool:
        if self._last_poll_at is None:
            return True

        interval = self._current_poll_interval_seconds(now)
        elapsed = (now - self._last_poll_at).total_seconds()
        return elapsed >= interval

    def _should_poll_twitter_now(self, now: datetime) -> bool:
        if not self._is_twitter_tracking_window(now):
            return False
        if self._last_twitter_poll_at is None:
            return True
        interval = self._current_twitter_poll_interval_seconds(now)
        elapsed = (now - self._last_twitter_poll_at).total_seconds()
        return elapsed >= interval

    def _should_poll_hampang_now(self, now: datetime) -> bool:
        if not self._is_twitter_tracking_window(now):
            return False
        if _is_twitter_priority_prep_window(now) or _is_twitter_priority_poll_window(now):
            return False
        if self._last_hampang_poll_at is None:
            return True
        elapsed = (now - self._last_hampang_poll_at).total_seconds()
        return elapsed >= HAMPANG_AUTO_POLL_INTERVAL_SECONDS

    def _current_twitter_poll_interval_seconds(self, now: datetime) -> int:
        if _is_twitter_priority_poll_window(now):
            return TWITTER_PRIORITY_POLL_INTERVAL_SECONDS
        return max(
            self.config.twitter_poll_interval_seconds,
            self.config.twitter_min_poll_interval_seconds,
        )

    def _is_twitter_tracking_window(self, now: datetime) -> bool:
        local_now = now.astimezone(KST)
        current_minute = local_now.hour * 60 + local_now.minute
        return any(
            _minute_in_window(current_minute, start, end)
            for start, end in self.config.twitter_tracking_windows_kst
        )

    def _current_twitter_tracking_window_started_at(self, now: datetime) -> datetime | None:
        local_now = now.astimezone(KST)
        current_minute = local_now.hour * 60 + local_now.minute
        starts: list[datetime] = []
        for start, end in self.config.twitter_tracking_windows_kst:
            if not _minute_in_window(current_minute, start, end):
                continue
            start_day = local_now
            if start > end and current_minute < end:
                start_day = local_now - timedelta(days=1)
            start_local = start_day.replace(hour=0, minute=0, second=0, microsecond=0)
            starts.append(start_local + timedelta(minutes=start))
        if not starts:
            return None
        return max(starts).astimezone(timezone.utc)

    def _hampang_auto_created_after(
        self,
        target: GuildHampangTarget,
        window_started_at: datetime | None,
    ) -> datetime | None:
        moments = [
            moment
            for moment in (
                _as_utc_datetime(target.created_at),
                _as_utc_datetime(window_started_at),
            )
            if moment is not None
        ]
        return max(moments) if moments else None

    def _auto_sendable_hampang_x_posts(
        self,
        posts: list[TwitterPost],
        *,
        target: GuildHampangTarget,
        window_started_at: datetime | None,
        max_age_seconds: int,
    ) -> list[TwitterPost]:
        created_after = self._hampang_auto_created_after(target, window_started_at)
        filtered: list[TwitterPost] = []
        for post in posts:
            created = _as_utc_datetime(post.created_at)
            if created is None:
                continue
            if created_after is not None and created <= created_after:
                continue
            if max_age_seconds > 0 and not _is_twitter_post_recent(post, max_age_seconds):
                continue
            filtered.append(post)
        return filtered

    def _auto_sendable_hampang_youtube_uploads(
        self,
        uploads: list[YoutubeUpload],
        *,
        target: GuildHampangTarget,
        window_started_at: datetime | None,
    ) -> list[YoutubeUpload]:
        created_after = self._hampang_auto_created_after(target, window_started_at)
        filtered: list[YoutubeUpload] = []
        for upload in uploads:
            published_at = _as_utc_datetime(upload.published_at)
            if published_at is None:
                continue
            if created_after is not None and published_at <= created_after:
                continue
            filtered.append(upload)
        return filtered

    def _current_poll_interval_seconds(self, now: datetime) -> int:
        if _is_twitter_priority_poll_window(now):
            return TWITTER_PRIORITY_POLL_INTERVAL_SECONDS
        if self._is_high_frequency_window(now):
            return self.config.high_frequency_poll_interval_seconds
        return self.config.poll_interval_seconds

    def _is_high_frequency_window(self, now: datetime) -> bool:
        local_now = now.astimezone(KST)
        if local_now.weekday() not in self.config.high_frequency_weekdays:
            return False

        start = self.config.high_frequency_start_hour
        end = self.config.high_frequency_end_hour
        current_hour = local_now.hour
        if start < end:
            return start <= current_hour < end
        return current_hour >= start or current_hour < end

    def _mark_news_target_posts_seen_for_poll(
        self,
        target: GuildNewsTarget,
        posts: list[NewsPost],
        *,
        exclude_post_ids: set[str] | None = None,
    ) -> None:
        excluded = exclude_post_ids or set()
        post_ids = [post.post_id for post in posts if post.post_id not in excluded]
        if not post_ids:
            return
        self.storage.mark_news_target_posts_seen(target.target_id, post_ids)
        self.storage.mark_posts_seen(target.guild_id, post_ids)
        self.storage.set_last_seen_post_id(target.guild_id, post_ids[0])

    def _mark_guild_posts_seen_for_poll(
        self,
        settings: GuildSettings,
        posts: list[NewsPost],
        *,
        exclude_post_ids: set[str] | None = None,
    ) -> None:
        excluded = exclude_post_ids or set()
        post_ids = [post.post_id for post in posts if post.post_id not in excluded]
        if not post_ids:
            return
        self.storage.mark_posts_seen(settings.guild_id, post_ids)
        self.storage.set_last_seen_post_id(settings.guild_id, post_ids[0])

    async def _process_news_target(
        self,
        settings: GuildSettings,
        target: GuildNewsTarget,
        posts: list[NewsPost],
    ) -> int:
        if not settings.enabled:
            return 0
        if self.bot.get_guild(target.guild_id) is None:
            LOGGER.debug(
                "봇이 서버에 연결되어 있지 않아 뉴스 자동 전송을 건너뜁니다 "
                "(guild_id=%s, channel_id=%s, language=%s).",
                target.guild_id,
                target.channel_id,
                target.language,
            )
            return 0

        channel = await self._resolve_automatic_news_channel(target)
        if channel is None:
            self._mark_news_target_posts_seen_for_poll(target, posts)
            return 0

        new_posts = self._auto_sendable_news_posts(
            self._new_posts_for_news_target(settings, target, posts),
            target=target,
        )
        if not new_posts:
            self._mark_news_target_posts_seen_for_poll(target, posts)
            return 0

        guild_log, channel_log = self._destination_logs(
            target.guild_id,
            channel,
            target.channel_id,
        )
        announced = 0
        retry_post_ids: set[str] = set()
        for post in new_posts:
            send_result = await self._send_news_post_to_target(
                channel,
                settings,
                target,
                post,
                mention_role=announced == 0,
            )
            if send_result == _NEWS_SEND_RETRY:
                retry_post_ids.add(post.post_id)
                LOGGER.warning(
                    "뉴스 자동 전송 일시 실패: 다음 자동 폴링에서 재시도합니다 "
                    "(guild_id=%s, channel_id=%s, language=%s, post_id=%s, title=%r).",
                    target.guild_id,
                    target.channel_id,
                    target.language,
                    post.post_id,
                    post.title,
                )
                continue
            if send_result == _NEWS_SEND_BASELINE:
                continue
            self.storage.mark_news_target_posts_seen(
                target.target_id,
                [post.post_id],
                announced=True,
            )
            self.storage.mark_posts_seen(target.guild_id, [post.post_id], announced=True)
            LOGGER.info(
                "새 뉴스 공지 | %s | %s | language=%s | delay=%s | 제목=%s",
                guild_log,
                channel_log,
                target.language,
                _news_delay_label(post),
                post.title,
            )
            announced += 1

        if retry_post_ids:
            LOGGER.warning(
                "일시 실패한 게시물은 해당 대상에서 본 것으로 처리하지 않아 이후 자동 폴링에서 재시도합니다 "
                "(guild_id=%s, channel_id=%s, post_ids=%s).",
                target.guild_id,
                target.channel_id,
                ", ".join(sorted(retry_post_ids)),
            )
        self._mark_news_target_posts_seen_for_poll(
            target,
            posts,
            exclude_post_ids=retry_post_ids,
        )
        return announced

    def _auto_sendable_news_posts(
        self,
        posts: list[NewsPost],
        *,
        target: GuildNewsTarget | None = None,
    ) -> list[NewsPost]:
        if not posts:
            return []

        created_after = _as_utc_datetime(target.created_at) if target is not None else None
        max_twitter_age = (
            self.config.twitter_announce_max_age_seconds
            or TWITTER_NEWS_DEFAULT_MAX_AGE_SECONDS
        )
        filtered: list[NewsPost] = []
        for post in posts:
            created = _as_utc_datetime(post.created_at)
            if created is None:
                continue
            if created_after is not None and created <= created_after:
                continue
            age = (datetime.now(timezone.utc) - created).total_seconds()
            if age > _NEWS_AUTO_ANNOUNCE_MAX_AGE_SECONDS:
                LOGGER.info(
                    "뉴스 자동 전송 후보 제외: 게시물이 너무 오래되었습니다 "
                    "(post_id=%s, title=%r, age_seconds=%s, max_age_seconds=%s).",
                    post.post_id,
                    post.title,
                    int(age),
                    _NEWS_AUTO_ANNOUNCE_MAX_AGE_SECONDS,
                )
                continue
            if (
                _is_twitter_news_post(post)
                and max_twitter_age > 0
                and not _is_twitter_news_post_recent(post, max_twitter_age)
            ):
                continue
            filtered.append(post)
        return filtered

    async def _resolve_automatic_news_channel(
        self, target: GuildNewsTarget
    ) -> discord.abc.Messageable | None:
        channel = self.bot.get_channel(target.channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(target.channel_id)
            except discord.Forbidden as exc:
                LOGGER.warning(
                    "뉴스 자동 전송 건너뜀: 설정된 채널 접근 불가 "
                    "(guild_id=%s, channel_id=%s, language=%s, discord_code=%s). "
                    "채널/카테고리의 채널 보기 및 메시지 보내기 권한을 확인하세요.",
                    target.guild_id,
                    target.channel_id,
                    target.language,
                    getattr(exc, "code", None),
                )
                return None
            except discord.NotFound as exc:
                LOGGER.warning(
                    "뉴스 자동 전송 건너뜀: 설정된 채널을 찾을 수 없음 "
                    "(guild_id=%s, channel_id=%s, language=%s, discord_code=%s). /서버설정을 다시 실행하세요.",
                    target.guild_id,
                    target.channel_id,
                    target.language,
                    getattr(exc, "code", None),
                )
                return None
            except discord.HTTPException as exc:
                LOGGER.exception(
                    "뉴스 자동 전송 건너뜀: 설정된 채널 조회 실패 "
                    "(guild_id=%s, channel_id=%s, language=%s, discord_status=%s, discord_code=%s).",
                    target.guild_id,
                    target.channel_id,
                    target.language,
                    getattr(exc, "status", None),
                    getattr(exc, "code", None),
                )
                return None

        if not isinstance(channel, discord.abc.Messageable):
            LOGGER.warning(
                "뉴스 자동 전송 건너뜀: 설정된 채널에 메시지를 보낼 수 없음 "
                "(guild_id=%s, channel_id=%s, language=%s, channel_type=%s).",
                target.guild_id,
                target.channel_id,
                target.language,
                type(channel).__name__,
            )
            return None

        return channel

    async def _process_guild(self, settings: GuildSettings, posts: list[NewsPost]) -> int:
        if not settings.channel_id or not settings.enabled:
            return 0
        if self.bot.get_guild(settings.guild_id) is None:
            LOGGER.debug(
                "봇이 서버에 연결되어 있지 않아 뉴스 자동 전송을 건너뜁니다 "
                "(guild_id=%s, channel_id=%s).",
                settings.guild_id,
                settings.channel_id,
            )
            return 0

        channel = await self._resolve_guild_news_channel_for_poll(settings, posts)
        if channel is None:
            return 0

        new_posts = self._auto_sendable_news_posts(
            self._new_posts_for_guild(settings, posts),
        )
        if not new_posts:
            self._mark_guild_posts_seen_for_poll(settings, posts)
            return 0

        return await self._send_guild_news_posts(settings, posts, channel, new_posts)

    async def _resolve_guild_news_channel_for_poll(
        self,
        settings: GuildSettings,
        posts: list[NewsPost],
    ) -> discord.abc.Messageable | None:
        assert settings.channel_id is not None
        channel = self.bot.get_channel(settings.channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(settings.channel_id)
            except discord.Forbidden as exc:
                LOGGER.warning(
                    "뉴스 자동 전송 건너뜀: 설정된 채널 접근 불가 "
                    "(guild_id=%s, channel_id=%s, discord_code=%s). "
                    "채널/카테고리의 채널 보기 및 메시지 보내기 권한을 확인하세요.",
                    settings.guild_id,
                    settings.channel_id,
                    getattr(exc, "code", None),
                )
                self._mark_guild_posts_seen_for_poll(settings, posts)
                return None
            except discord.NotFound as exc:
                LOGGER.warning(
                    "뉴스 자동 전송 건너뜀: 설정된 채널을 찾을 수 없음 "
                    "(guild_id=%s, channel_id=%s, discord_code=%s). /서버설정을 다시 실행하세요.",
                    settings.guild_id,
                    settings.channel_id,
                    getattr(exc, "code", None),
                )
                self._mark_guild_posts_seen_for_poll(settings, posts)
                return None
            except discord.HTTPException as exc:
                LOGGER.exception(
                    "뉴스 자동 전송 건너뜀: 설정된 채널 조회 실패 "
                    "(guild_id=%s, channel_id=%s, discord_status=%s, discord_code=%s).",
                    settings.guild_id,
                    settings.channel_id,
                    getattr(exc, "status", None),
                    getattr(exc, "code", None),
                )
                self._mark_guild_posts_seen_for_poll(settings, posts)
                return None

        if not isinstance(channel, discord.abc.Messageable):
            LOGGER.warning(
                "뉴스 자동 전송 건너뜀: 설정된 채널에 메시지를 보낼 수 없음 "
                "(guild_id=%s, channel_id=%s, channel_type=%s).",
                settings.guild_id,
                settings.channel_id,
                type(channel).__name__,
            )
            self._mark_guild_posts_seen_for_poll(settings, posts)
            return None

        return channel

    async def _send_guild_news_posts(
        self,
        settings: GuildSettings,
        posts: list[NewsPost],
        channel: discord.abc.Messageable,
        new_posts: list[NewsPost],
    ) -> int:
        assert settings.channel_id is not None
        guild_log, channel_log = self._destination_logs(
            settings.guild_id,
            channel,
            settings.channel_id,
        )
        announced = 0
        retry_post_ids: set[str] = set()
        for post in new_posts:
            send_result = await self._send_news_post(
                channel,
                settings,
                post,
                mention_role=announced == 0,
            )
            if send_result == _NEWS_SEND_RETRY:
                retry_post_ids.add(post.post_id)
                LOGGER.warning(
                    "뉴스 자동 전송 일시 실패: 다음 자동 폴링에서 재시도합니다 "
                    "(guild_id=%s, channel_id=%s, post_id=%s, title=%r).",
                    settings.guild_id,
                    settings.channel_id,
                    post.post_id,
                    post.title,
                )
                continue
            if send_result == _NEWS_SEND_BASELINE:
                continue
            self.storage.mark_posts_seen(settings.guild_id, [post.post_id], announced=True)
            LOGGER.info(
                "새 뉴스 공지 | %s | %s | delay=%s | 제목=%s",
                guild_log,
                channel_log,
                _news_delay_label(post),
                post.title,
            )
            announced += 1

        if retry_post_ids:
            LOGGER.warning(
                "일시 실패한 게시물은 본 것으로 처리하지 않아 이후 자동 폴링에서 재시도합니다 "
                "(guild_id=%s, channel_id=%s, post_ids=%s).",
                settings.guild_id,
                settings.channel_id,
                ", ".join(sorted(retry_post_ids)),
            )
        self._mark_guild_posts_seen_for_poll(
            settings,
            posts,
            exclude_post_ids=retry_post_ids,
        )
        return announced

    def _new_posts_for_guild(
        self, settings: GuildSettings, posts_newest_first: list[NewsPost]
    ) -> list[NewsPost]:
        fetched_post_ids = [post.post_id for post in posts_newest_first]
        has_seen_baseline = self.storage.has_seen_posts(settings.guild_id)
        seen_post_statuses = self.storage.get_seen_post_statuses(
            settings.guild_id,
            fetched_post_ids,
        )
        seen_post_ids = set(seen_post_statuses)
        if seen_post_ids:
            return _recent_auto_posts([
                post
                for post in reversed(posts_newest_first)
                if post.post_id not in seen_post_ids
            ])

        if not has_seen_baseline and not self.config.announce_existing_on_first_run:
            LOGGER.info(
                "서버 %s의 뉴스 기준선을 초기화했습니다 (게시물 %s개). 이전 게시물은 공지되지 않습니다.",
                settings.guild_id,
                len(fetched_post_ids),
            )
            return []

        if settings.last_seen_post_id is None:
            if self.config.announce_existing_on_first_run:
                return _recent_auto_posts(list(reversed(posts_newest_first)))
            return []

        ids = [post.post_id for post in posts_newest_first]
        if settings.last_seen_post_id in ids:
            index = ids.index(settings.last_seen_post_id)
            return _recent_auto_posts(list(reversed(posts_newest_first[:index])))

        last_seen_post = self.storage.get_post(settings.last_seen_post_id)
        if last_seen_post and last_seen_post.created_at:
            return _recent_auto_posts([
                post
                for post in reversed(posts_newest_first)
                if post.created_at and post.created_at > last_seen_post.created_at
            ])

        if settings.last_seen_post_id.isdigit():
            last_seen_id = int(settings.last_seen_post_id)
            return _recent_auto_posts([
                post
                for post in reversed(posts_newest_first)
                if post.post_id.isdigit() and int(post.post_id) > last_seen_id
            ])

        return []

    def _new_posts_for_news_target(
        self,
        settings: GuildSettings,
        target: GuildNewsTarget,
        posts_newest_first: list[NewsPost],
    ) -> list[NewsPost]:
        fetched_post_ids = [post.post_id for post in posts_newest_first]
        has_seen_baseline = self.storage.news_target_has_seen_posts(target.target_id)
        seen_post_statuses = self.storage.get_news_target_seen_post_statuses(
            target.target_id,
            fetched_post_ids,
        )
        seen_post_ids = set(seen_post_statuses)
        if seen_post_ids:
            created_after = _as_utc_datetime(target.created_at)
            return _recent_auto_posts([
                post
                for post in reversed(posts_newest_first)
                if post.post_id not in seen_post_ids
                and (
                    created_after is None
                    or (
                        post.created_at is not None
                        and _as_utc_datetime(post.created_at) > created_after
                    )
                )
            ])

        if not has_seen_baseline and not self.config.announce_existing_on_first_run:
            LOGGER.info(
                "서버 %s 채널 %s의 뉴스 기준선을 초기화했습니다 (언어=%s, 게시물 %s개). 이전 게시물은 공지되지 않습니다.",
                target.guild_id,
                target.channel_id,
                target.language,
                len(fetched_post_ids),
            )
            return []

        if settings.last_seen_post_id is None:
            if self.config.announce_existing_on_first_run:
                return _recent_auto_posts(list(reversed(posts_newest_first)))
            return []

        ids = [post.post_id for post in posts_newest_first]
        if settings.last_seen_post_id in ids:
            index = ids.index(settings.last_seen_post_id)
            return _recent_auto_posts(list(reversed(posts_newest_first[:index])))

        last_seen_post = self.storage.get_post(settings.last_seen_post_id)
        if last_seen_post and last_seen_post.created_at:
            return _recent_auto_posts([
                post
                for post in reversed(posts_newest_first)
                if post.created_at and post.created_at > last_seen_post.created_at
            ])

        return []

    async def _send_news_post(
        self,
        channel: discord.abc.Messageable,
        settings: GuildSettings,
        post: NewsPost,
        *,
        batch_tasks: list[asyncio.Task[list[discord.File]]] | None = None,
        mention_role: bool = True,
    ) -> str:
        channel_id = getattr(channel, "id", settings.channel_id)
        role_id = self._automatic_news_role_mention_id(
            channel_id,
            settings.role_id,
            requested=mention_role,
        )
        try:
            await self._broadcast_post(
                channel,
                post,
                role_id,
                banner_filename=settings.notification_banner,
                batch_tasks=batch_tasks,
                image_delivery=settings.image_delivery,
            )
            self._record_automatic_news_role_mention(role_id)
            return _NEWS_SEND_SENT
        except discord.Forbidden as exc:
            LOGGER.warning(
                "뉴스 자동 전송 권한 없음: Discord가 메시지 전송을 거부했습니다 "
                "(guild_id=%s, channel_id=%s, role_id=%s, post_id=%s, title=%r, "
                "discord_code=%s). 봇 역할의 채널/카테고리 권한 재정의를 확인하세요.",
                settings.guild_id,
                channel_id,
                settings.role_id,
                post.post_id,
                post.title,
                getattr(exc, "code", None),
            )
            return _NEWS_SEND_BASELINE
        except discord.NotFound as exc:
            LOGGER.warning(
                "뉴스 자동 전송 대상이 사라졌습니다 "
                "(guild_id=%s, channel_id=%s, post_id=%s, title=%r, discord_code=%s). "
                "Re-run server settings.",
                settings.guild_id,
                channel_id,
                post.post_id,
                post.title,
                getattr(exc, "code", None),
            )
            return _NEWS_SEND_BASELINE
        except discord.HTTPException as exc:
            LOGGER.exception(
                "뉴스 자동 전송 HTTP 오류 "
                "(guild_id=%s, channel_id=%s, role_id=%s, post_id=%s, title=%r, "
                "discord_status=%s, discord_code=%s).",
                settings.guild_id,
                channel_id,
                settings.role_id,
                post.post_id,
                post.title,
                getattr(exc, "status", None),
                getattr(exc, "code", None),
            )
            return _NEWS_SEND_RETRY
        except Exception:
            LOGGER.exception(
                "뉴스 자동 전송 중 예상치 못한 오류 "
                "(guild_id=%s, channel_id=%s, role_id=%s, post_id=%s, title=%r).",
                settings.guild_id,
                channel_id,
                settings.role_id,
                post.post_id,
                post.title,
            )
            return _NEWS_SEND_RETRY

    async def _send_news_post_to_target(
        self,
        channel: discord.abc.Messageable,
        settings: GuildSettings,
        target: GuildNewsTarget,
        post: NewsPost,
        *,
        batch_tasks: list[asyncio.Task[list[discord.File]]] | None = None,
        mention_role: bool = True,
    ) -> str:
        channel_id = getattr(channel, "id", target.channel_id)
        role_id = self._automatic_news_role_mention_id(
            channel_id,
            settings.role_id,
            requested=mention_role,
        )
        try:
            sent_message = await self._broadcast_post(
                channel,
                post,
                role_id,
                banner_filename=settings.notification_banner,
                batch_tasks=batch_tasks,
                image_delivery=settings.image_delivery,
                news_target_id=target.target_id,
            )
        except discord.Forbidden as exc:
            LOGGER.warning(
                "뉴스 자동 전송 권한 없음: Discord가 메시지 전송을 거부했습니다 "
                "(guild_id=%s, channel_id=%s, role_id=%s, language=%s, post_id=%s, title=%r, "
                "discord_code=%s). 봇 역할의 채널/카테고리 권한 재정의를 확인하세요.",
                target.guild_id,
                target.channel_id,
                settings.role_id,
                target.language,
                post.post_id,
                post.title,
                getattr(exc, "code", None),
            )
            return _NEWS_SEND_BASELINE
        except discord.NotFound as exc:
            LOGGER.warning(
                "뉴스 자동 전송 대상이 사라졌습니다 "
                "(guild_id=%s, channel_id=%s, language=%s, post_id=%s, title=%r, discord_code=%s). "
                "Re-run server settings.",
                target.guild_id,
                target.channel_id,
                target.language,
                post.post_id,
                post.title,
                getattr(exc, "code", None),
            )
            return _NEWS_SEND_BASELINE
        except discord.HTTPException as exc:
            LOGGER.exception(
                "뉴스 자동 전송 HTTP 오류 "
                "(guild_id=%s, channel_id=%s, role_id=%s, language=%s, post_id=%s, title=%r, "
                "discord_status=%s, discord_code=%s).",
                target.guild_id,
                target.channel_id,
                settings.role_id,
                target.language,
                post.post_id,
                post.title,
                getattr(exc, "status", None),
                getattr(exc, "code", None),
            )
            return _NEWS_SEND_RETRY
        except Exception:
            LOGGER.exception(
                "뉴스 자동 전송 중 예상치 못한 오류 "
                "(guild_id=%s, channel_id=%s, role_id=%s, language=%s, post_id=%s, title=%r).",
                target.guild_id,
                target.channel_id,
                settings.role_id,
                target.language,
                post.post_id,
                post.title,
            )
            return _NEWS_SEND_RETRY

        self._record_automatic_news_role_mention(role_id)
        if sent_message is not None:
            try:
                self.storage.record_news_post_message(
                    target.target_id,
                    post.post_id,
                    getattr(channel, "id", target.channel_id),
                    sent_message.id,
                )
            except Exception:
                LOGGER.exception(
                    "뉴스 메시지 기록 실패: 메시지는 전송되었으므로 공지 완료로 처리합니다 "
                    "(guild_id=%s, channel_id=%s, language=%s, post_id=%s, message_id=%s).",
                    target.guild_id,
                    target.channel_id,
                    target.language,
                    post.post_id,
                    sent_message.id,
                )
        return _NEWS_SEND_SENT

    def _automatic_news_role_mention_id(
        self,
        channel_id: int | None,
        role_id: int | None,
        *,
        requested: bool,
    ) -> int | None:
        if not requested or role_id is None:
            return role_id if requested else None

        now = perf_counter()
        last_mentioned_at = self._news_role_mention_times.get(role_id)
        if (
            last_mentioned_at is not None
            and now - last_mentioned_at < NEWS_ROLE_MENTION_COOLDOWN_SECONDS
        ):
            LOGGER.info(
                "뉴스 역할 멘션 생략: 150초 중복 방지 적용 "
                "(channel_id=%s, role_id=%s, remaining=%.1f초).",
                channel_id,
                role_id,
                NEWS_ROLE_MENTION_COOLDOWN_SECONDS - (now - last_mentioned_at),
            )
            return None

        return role_id

    def _record_automatic_news_role_mention(self, role_id: int | None) -> None:
        if role_id is None:
            return
        self._news_role_mention_times[role_id] = perf_counter()

    def _pending_news_update_targets_by_post(self) -> dict[str, list[GuildNewsTarget]]:
        targets_by_post_id: dict[str, list[GuildNewsTarget]] = {}
        for pending_post_id, target in self.storage.get_pending_news_update_targets():
            targets_by_post_id.setdefault(pending_post_id, []).append(target)
        return targets_by_post_id

    def _mark_old_news_update_targets_sent(
        self,
        post_id: str,
        post: NewsPost,
        targets: list[GuildNewsTarget],
    ) -> None:
        for target in targets:
            self.storage.mark_news_update_sent(target.target_id, post_id)
        LOGGER.info(
            "오래된 글이라 수정 재전송을 건너뛰고 큐를 정리합니다 "
            "(post_id=%s, title=%r, targets=%s).",
            post_id,
            post.title,
            len(targets),
        )

    async def _broadcast_news_update_to_target(
        self,
        post_id: str,
        post: NewsPost,
        target: GuildNewsTarget,
    ) -> None:
        settings = self.storage.get_settings(target.guild_id)
        if not settings.enabled:
            self.storage.mark_news_update_sent(target.target_id, post_id)
            return
        try:
            await self._apply_news_post_update(settings, target, post)
            self.storage.mark_news_update_sent(target.target_id, post_id)
        except Exception:
            LOGGER.exception(
                "뉴스 수정 반영 실패 (guild_id=%s, channel_id=%s, post_id=%s).",
                target.guild_id,
                target.channel_id,
                post_id,
            )

    async def _broadcast_post_updates(self, post_ids: list[str]) -> None:
        targets_by_post_id = self._pending_news_update_targets_by_post()
        pending_post_ids = list(dict.fromkeys([*post_ids, *targets_by_post_id.keys()]))
        for post_id in pending_post_ids:
            post = self.storage.get_post(post_id)
            if post is None:
                continue
            targets = targets_by_post_id.get(post_id, [])
            if not targets:
                continue
            if not _is_news_update_recent(post):
                self._mark_old_news_update_targets_sent(post_id, post, targets)
                continue
            LOGGER.info(
                "뉴스 수정 감지 — 원본 메시지 수정 (post_id=%s, title=%r, targets=%s).",
                post_id,
                post.title,
                len(targets),
            )
            for target in targets:
                await self._broadcast_news_update_to_target(post_id, post, target)

    async def _apply_news_post_update(
        self,
        settings: GuildSettings,
        target: GuildNewsTarget,
        post: NewsPost,
    ) -> None:
        recorded = self.storage.get_news_post_message(target.target_id, post.post_id)
        if recorded is None:
            LOGGER.info(
                "원본 메시지 기록이 없어 수정 반영을 건너뜁니다 "
                "(guild_id=%s, channel_id=%s, post_id=%s).",
                target.guild_id,
                target.channel_id,
                post.post_id,
            )
            return

        channel_id, message_id, last_notified_at = recorded
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except (discord.Forbidden, discord.NotFound):
                return
        if not isinstance(channel, discord.abc.Messageable):
            return

        banner_file = _news_banner_file(settings.notification_banner)
        updated_view = _build_layout_view_for_post(
            post,
            include_zip_button=True,
            include_banner=banner_file is not None,
            include_content_images=settings.image_delivery == IMAGE_DELIVERY_EMBEDS,
        )
        try:
            message = await channel.fetch_message(message_id)
        except discord.NotFound:
            LOGGER.info(
                "원본 메시지가 삭제되어 수정 반영을 건너뜁니다 "
                "(channel_id=%s, message_id=%s, post_id=%s).",
                channel_id,
                message_id,
                post.post_id,
            )
            return

        await message.edit(view=updated_view)
        if settings.image_delivery == IMAGE_DELIVERY_FILES:
            await self._replace_news_post_image_messages(channel, target, post)

        last_notified: datetime | None = None
        if last_notified_at:
            try:
                last_notified = _as_utc_datetime(datetime.fromisoformat(last_notified_at))
            except ValueError:
                last_notified = None
        if last_notified is not None and (
            datetime.now(timezone.utc) - last_notified < NEWS_UPDATE_NOTICE_COOLDOWN
        ):
            LOGGER.info(
                "최근에 수정 안내를 보냈으므로 내용만 갱신하고 답장은 생략합니다 "
                "(channel_id=%s, message_id=%s, post_id=%s).",
                channel_id,
                message_id,
                post.post_id,
            )
            return
        try:
            await message.reply(
                embed=_news_update_notice_embed(),
                allowed_mentions=discord.AllowedMentions.none(),
                mention_author=False,
            )
            self.storage.mark_news_post_message_notified(target.target_id, post.post_id)
        except discord.HTTPException:
            LOGGER.exception(
                "수정 안내 답장 전송 실패 (channel_id=%s, message_id=%s, post_id=%s).",
                channel_id,
                message_id,
                post.post_id,
            )

    async def _replace_news_post_image_messages(
        self,
        channel: discord.abc.Messageable,
        target: GuildNewsTarget,
        post: NewsPost,
    ) -> None:
        urls = _standalone_image_urls(post, attach_images=True)
        self._invalidate_image_cache(urls)
        file_batches = await self._image_file_batches_for_post(post, urls=urls)
        existing = self.storage.get_news_post_image_messages(target.target_id, post.post_id)
        message_ids: list[int] = []

        for index, file_batch in enumerate(file_batches):
            message: discord.Message | None = None
            if index < len(existing):
                _, message_id, _ = existing[index]
                try:
                    message = await channel.fetch_message(message_id)
                except discord.NotFound:
                    message = None
            if message is None:
                message = await channel.send(
                    files=file_batch,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            else:
                await message.edit(attachments=file_batch)
            message_ids.append(message.id)

        for channel_id, message_id, _ in existing[len(file_batches) :]:
            try:
                message = await channel.fetch_message(message_id)
                await message.delete()
            except discord.NotFound:
                pass
            self.storage.delete_tracked_message(target.guild_id, channel_id, message_id)

        self.storage.replace_news_post_image_messages(
            target.target_id,
            post.post_id,
            target.channel_id,
            message_ids,
        )
        LOGGER.info(
            "수정된 뉴스 첨부 이미지 반영 완료 "
            "(guild_id=%s, channel_id=%s, post_id=%s, image_messages=%s).",
            target.guild_id,
            target.channel_id,
            post.post_id,
            len(message_ids),
        )

    def _invalidate_image_cache(self, urls: list[str]) -> None:
        for url in urls:
            for candidate in _original_image_download_candidates(url):
                cached = self._image_cache.pop(candidate, None)
                if cached is not None:
                    self._image_cache_bytes -= len(cached[0])

    @staticmethod
    def _news_post_send_kwargs(
        news_view: discord.ui.View,
        mention: str | None,
        role_id: int | None,
        banner_file: discord.File | None,
    ) -> dict[str, object]:
        allowed_mentions = discord.AllowedMentions(
            everyone=False,
            users=False,
            roles=[discord.Object(id=role_id)] if role_id else False,
        )
        send_kwargs: dict[str, object] = {
            "view": news_view,
            "allowed_mentions": allowed_mentions if mention else discord.AllowedMentions.none(),
        }
        if banner_file is not None:
            send_kwargs["file"] = banner_file
        return send_kwargs

    def _news_followup_tasks(
        self,
        channel: discord.abc.Messageable,
        post: NewsPost,
    ) -> list[asyncio.Task[object]]:
        followup_tasks: list[asyncio.Task[object]] = []
        youtube_content = _youtube_links_content(post)
        if youtube_content:
            followup_tasks.append(
                asyncio.create_task(
                    channel.send(
                        content=youtube_content,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                )
            )
        if not _is_twitter_news_post(post):
            return followup_tasks

        video_url_groups = _twitter_video_url_groups_from_raw(post.raw)
        video_fallback_url = _twitter_video_fallback_url_from_raw(post.raw)
        if video_url_groups:
            followup_tasks.append(
                asyncio.create_task(
                    self._send_twitter_video_to_channel(
                        channel, video_url_groups, video_fallback_url or post.url
                    )
                )
            )
        elif video_fallback_url:
            followup_tasks.append(
                asyncio.create_task(
                    channel.send(
                        content=video_fallback_url,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                )
            )
        return followup_tasks

    @staticmethod
    async def _log_news_followup_results(
        post: NewsPost,
        followup_tasks: list[asyncio.Task[object]],
    ) -> None:
        if not followup_tasks:
            return
        results = await asyncio.gather(*followup_tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                LOGGER.error(
                    "뉴스 후속 메시지 전송 실패 (post_id=%s, title=%r).",
                    post.post_id,
                    post.title,
                    exc_info=(type(result), result, result.__traceback__),
                )

    async def _broadcast_post(
        self,
        channel: discord.abc.Messageable,
        post: NewsPost,
        role_id: int | None,
        *,
        banner_filename: str | None = None,
        is_update: bool = False,
        batch_tasks: list[asyncio.Task[list[discord.File]]] | None = None,
        image_delivery: str = IMAGE_DELIVERY_EMBEDS,
        news_target_id: int | None = None,
    ) -> discord.Message | None:
        mention = f"<@&{role_id}>" if role_id else None

        banner_file = _news_banner_file(banner_filename)
        standalone_urls = _standalone_image_urls(
            post,
            attach_images=image_delivery == IMAGE_DELIVERY_FILES,
        )
        if image_delivery == IMAGE_DELIVERY_FILES and batch_tasks is None:
            batch_tasks = self._start_image_batch_tasks(standalone_urls)
        news_view = _build_layout_view_for_post(
            post,
            include_zip_button=True,
            include_banner=banner_file is not None,
            leading_text=mention,
            is_update=is_update,
            include_content_images=image_delivery == IMAGE_DELIVERY_EMBEDS,
        )

        send_kwargs = self._news_post_send_kwargs(news_view, mention, role_id, banner_file)
        sent_message = await channel.send(**send_kwargs)

        if image_delivery == IMAGE_DELIVERY_FILES:
            self._schedule_channel_image_messages(
                channel,
                post,
                batch_tasks=batch_tasks,
                image_urls=standalone_urls,
                news_target_id=news_target_id,
            )
        await self._log_news_followup_results(post, self._news_followup_tasks(channel, post))
        return sent_message

    async def _send_news_post_followups(
        self,
        interaction: discord.Interaction,
        post: NewsPost,
        *,
        private: bool,
        attach_photos: bool = True,
    ) -> list[discord.Message | None]:
        sent_messages: list[discord.Message | None] = []
        image_delivery = self._interaction_image_delivery(interaction)
        standalone_urls = _standalone_image_urls(post, attach_images=attach_photos)
        banner_file = _news_banner_file(
            self._interaction_banner_filename(interaction, private=private)
        )
        use_image_embeds = self._bot_is_missing_from_interaction_guild(interaction)
        file_batch_tasks = (
            self._start_image_batch_tasks(standalone_urls)
            if attach_photos and image_delivery == IMAGE_DELIVERY_FILES and not use_image_embeds
            else None
        )
        inline_content_images = (
            attach_photos
            and image_delivery == IMAGE_DELIVERY_EMBEDS
            and not use_image_embeds
        )
        news_view = _build_layout_view_for_post(
            post,
            include_zip_button=attach_photos,
            include_banner=banner_file is not None,
            include_content_images=inline_content_images,
        )

        send_kwargs = {
            "ephemeral": private,
            "view": news_view,
            "allowed_mentions": discord.AllowedMentions.none(),
            "wait": True,
        }
        if banner_file is not None:
            send_kwargs["file"] = banner_file
        sent_messages.append(await interaction.followup.send(**send_kwargs))

        youtube_content = _youtube_links_content(post)
        if youtube_content:
            sent_messages.append(
                await interaction.followup.send(
                    content=youtube_content,
                    ephemeral=private,
                    allowed_mentions=discord.AllowedMentions.none(),
                    wait=True,
                )
            )
        if _is_twitter_news_post(post):
            video_url_groups = _twitter_video_url_groups_from_raw(post.raw)
            video_fallback_url = _twitter_video_fallback_url_from_raw(post.raw)
            if video_url_groups:
                task = asyncio.create_task(
                    self._send_twitter_video_followups(
                        interaction,
                        private,
                        video_url_groups,
                        video_fallback_url or post.url,
                    )
                )
                task.add_done_callback(self._log_background_task_result)
            elif video_fallback_url:
                sent_messages.append(
                    await interaction.followup.send(
                        content=video_fallback_url,
                        ephemeral=private,
                        allowed_mentions=discord.AllowedMentions.none(),
                        wait=True,
                    )
                )

        if attach_photos and use_image_embeds:
            self._schedule_interaction_image_embed_followups(
                interaction,
                post,
                private=private,
                image_urls=standalone_urls,
            )
        elif attach_photos and image_delivery == IMAGE_DELIVERY_FILES:
            self._schedule_interaction_image_followups(
                interaction,
                post,
                private=private,
                batch_tasks=file_batch_tasks,
                image_urls=standalone_urls,
            )
        return sent_messages

    async def _resolve_target_channel(
        self,
        explicit: discord.abc.Messageable | None,
        fallback_channel_id: int | None,
    ) -> discord.abc.Messageable | None:
        if explicit is not None:
            return explicit
        if not fallback_channel_id:
            return None
        cached = self.bot.get_channel(fallback_channel_id)
        if cached is None:
            try:
                cached = await self.bot.fetch_channel(fallback_channel_id)
            except (discord.Forbidden, discord.NotFound):
                return None
        return cached if isinstance(cached, discord.abc.Messageable) else None

    async def _zip_buffers_for_post(
        self,
        post: NewsPost,
        upload_limit: int,
    ) -> tuple[list[io.BytesIO], int, int]:
        cached = self._zip_cache.get(post.post_id)
        if cached is None or cached[3] != upload_limit:
            buffers, count, skipped = await self._build_image_zip_parts(
                post,
                max_part_bytes=upload_limit,
            )
            if buffers and count > 0:
                self._cache_zip(
                    post.post_id,
                    [buffer.getvalue() for buffer in buffers],
                    count,
                    skipped,
                    upload_limit,
                )
            return buffers, count, skipped

        zip_part_bytes, count, skipped, _ = cached
        return [io.BytesIO(part) for part in zip_part_bytes], count, skipped

    @staticmethod
    def _zip_empty_message(language: str, skipped: int) -> str:
        return (
            _news_ui_text(language, "zip_upload_too_large")
            if skipped
            else _news_ui_text(language, "zip_empty")
        )

    @staticmethod
    def _zip_ready_message(language: str, count: int, total_parts: int, skipped: int) -> str:
        if total_parts > 1:
            message = _news_ui_text(language, "zip_ready_split").format(
                count=count,
                parts=total_parts,
            )
        else:
            message = _news_ui_text(language, "zip_ready").format(count=count)
        if skipped:
            message = (
                f"{message}\n"
                f"{_news_ui_text(language, 'zip_oversized_skipped').format(skipped=skipped)}"
            )
        return message

    async def _send_zip_buffers(
        self,
        interaction: discord.Interaction,
        filename: str,
        buffers: list[io.BytesIO],
        message: str,
    ) -> None:
        total_parts = len(buffers)
        for index, buffer in enumerate(buffers, start=1):
            buffer.seek(0)
            part_filename = self._zip_part_filename(filename, index, total_parts)
            content = message if index == 1 else f"{index}/{total_parts}"
            await interaction.followup.send(
                content,
                file=discord.File(buffer, filename=part_filename),
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

    async def _send_zip_upload_failure(
        self,
        interaction: discord.Interaction,
        post_id: str,
        language: str,
        exc: discord.HTTPException,
        *,
        total_parts: int,
        upload_limit: int,
    ) -> None:
        if getattr(exc, "status", None) == 413 or getattr(exc, "code", None) == 40005:
            LOGGER.warning(
                "게시물 %s의 이미지 ZIP 업로드가 Discord 한도를 초과했습니다 "
                "(parts=%s, limit=%s).",
                post_id,
                total_parts,
                upload_limit,
            )
            message = _news_ui_text(language, "zip_upload_too_large")
        else:
            LOGGER.exception("게시물 %s의 이미지 ZIP 업로드 실패.", post_id)
            message = _news_ui_text(language, "zip_fetch_failed")
        await interaction.followup.send(
            message,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def handle_zip_request(
        self, interaction: discord.Interaction, post_id: str
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        post = await self._resolve_brighten_post(post_id)
        language = _post_language(post) if post is not None else "koreana"
        if post is None or not _downloadable_image_urls(post):
            await interaction.followup.send(
                _news_ui_text(language, "zip_no_images"), ephemeral=True
            )
            return

        try:
            upload_limit = self._zip_upload_limit_bytes(interaction)
            buffers, count, skipped = await self._zip_buffers_for_post(post, upload_limit)
        except Exception:
            LOGGER.exception("게시물 %s의 이미지 ZIP 생성 실패.", post_id)
            await interaction.followup.send(
                _news_ui_text(language, "zip_fetch_failed"),
                ephemeral=True,
            )
            return

        if not buffers or count == 0:
            await interaction.followup.send(
                self._zip_empty_message(language, skipped),
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        filename = _safe_zip_filename(post)
        total_parts = len(buffers)
        message = self._zip_ready_message(language, count, total_parts, skipped)

        try:
            await self._send_zip_buffers(interaction, filename, buffers, message)
        except discord.HTTPException as exc:
            await self._send_zip_upload_failure(
                interaction,
                post_id,
                language,
                exc,
                total_parts=total_parts,
                upload_limit=upload_limit,
            )

    async def handle_brighten_spoiler_request(
        self,
        interaction: discord.Interaction,
        post_id: str,
        *,
        image_index: int = 0,
        ephemeral: bool = True,
    ) -> None:
        post = await self._resolve_brighten_post(post_id)
        if post is None:
            await interaction.followup.send(
                "이미지를 찾을 수 없어요.", ephemeral=True
            )
            return

        image_urls = _brightenable_image_urls(post)
        if image_index < 0 or image_index >= len(image_urls):
            await interaction.followup.send(
                "밝게 만들 이미지가 없어요.", ephemeral=True
            )
            return

        image_url = image_urls[image_index]
        brightened = await self._get_brightened_image(image_url)
        if brightened is None:
            await interaction.followup.send(
                "이미지를 가져오는 중 문제가 생겼어요. 잠시 후 다시 시도해주세요.",
                ephemeral=True,
            )
            return

        filename = (
            f"SPOILER_limpi_brightened_{post.post_id.replace(':', '_')}_"
            f"{image_index + 1}.png"
        )
        try:
            await interaction.followup.send(
                "밝기 보정 이미지입니다. 스포일러에 주의해주세요.",
                file=discord.File(io.BytesIO(brightened), filename=filename),
                ephemeral=ephemeral,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "이 채널에 이미지를 보낼 권한이 없어요.",
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            await interaction.followup.send(
                "밝기 보정 이미지를 보내는 중 문제가 생겼어요.",
                ephemeral=True,
            )
            return

        try:
            await interaction.edit_original_response(
                content="밝기 보정 이미지를 보냈어요.",
                embed=None,
                view=None,
            )
        except discord.HTTPException:
            pass

    async def prompt_brighten_spoiler_visibility(
        self,
        interaction: discord.Interaction,
        post_id: str,
        *,
        image_index: int = 0,
    ) -> None:
        embed = discord.Embed(
            title="밝기 보정 이미지를 어디로 보낼까요?",
            description="스포일러 이미지일 수 있으니 공개 전송 전에 한 번 확인해주세요.",
            color=discord.Color.from_rgb(179, 28, 28),
        )
        view = BrightenSpoilerVisibilityView(
            interaction.user.id,
            post_id,
            image_index,
        )
        await interaction.response.send_message(
            embed=embed,
            view=view,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _get_brightened_image(self, image_url: str) -> bytes | None:
        cached = self._brighten_cache.get(image_url)
        if cached is not None:
            self._brighten_cache[image_url] = self._brighten_cache.pop(image_url)
            return cached

        task = self._brighten_tasks.get(image_url)
        if task is None:
            task = asyncio.create_task(self._build_brightened_image(image_url))
            self._brighten_tasks[image_url] = task

        try:
            return await asyncio.shield(task)
        finally:
            if task.done():
                self._brighten_tasks.pop(image_url, None)

    async def _build_brightened_image(self, image_url: str) -> bytes | None:
        downloaded = await self._download_image(image_url)
        if downloaded is None:
            return None

        data, content_type = downloaded
        async with self._brighten_semaphore:
            brightened = await asyncio.to_thread(
                _brighten_image_bytes,
                data,
                content_type,
            )
        if brightened is not None:
            self._cache_brightened_image(image_url, brightened)
        return brightened

    async def _resolve_brighten_post(self, post_id: str) -> NewsPost | None:
        post = self.storage.get_post(post_id)
        if post is not None:
            return post

        twitter_id = post_id.removeprefix(_TWITTER_NEWS_POST_ID_PREFIX)
        twitter_post = self.storage.get_twitter_post(twitter_id)
        if twitter_post is not None:
            return _twitter_posts_as_news_posts([twitter_post], [])[0]

        if self.x_source is None:
            return None

        try:
            recent_posts = await self.x_source.fetch_recent_posts(limit=TWITTER_POST_LIMIT)
        except XClientError:
            LOGGER.exception("밝기 보정용 X 게시물 재조회 실패 (post_id=%s).", post_id)
            return None

        wanted_ids = {post_id, twitter_id}
        prefixed_twitter_ids = {
            value.removeprefix(_TWITTER_NEWS_POST_ID_PREFIX)
            for value in wanted_ids
            if value.startswith(_TWITTER_NEWS_POST_ID_PREFIX)
        }
        wanted_ids.update(prefixed_twitter_ids)
        prefixed_x_ids = {
            value.removeprefix("x:")
            for value in wanted_ids
            if value.startswith("x:")
        }
        wanted_ids.update(prefixed_x_ids)
        for candidate in recent_posts:
            tweet_id = str(candidate.raw.get("tweet_id") or "").strip()
            if candidate.post_id in wanted_ids or tweet_id in wanted_ids:
                self.storage.save_twitter_posts([candidate])
                return _twitter_posts_as_news_posts([candidate], [])[0]

        return None

    def _zip_upload_limit_bytes(self, interaction: discord.Interaction) -> int:
        guild = getattr(interaction, "guild", None)
        guild_limit = getattr(guild, "filesize_limit", None)
        if not isinstance(guild_limit, int) or guild_limit <= 0:
            return ZIP_UPLOAD_SAFE_BYTES
        return max(
            1024 * 1024,
            min(ZIP_UPLOAD_SAFE_BYTES, guild_limit - ZIP_UPLOAD_HEADROOM_BYTES),
        )

    @staticmethod
    def _zip_part_filename(filename: str, index: int, total_parts: int) -> str:
        if total_parts <= 1:
            return filename
        stem = filename[:-4] if filename.lower().endswith(".zip") else filename
        return f"{stem}_part{index:02d}-of-{total_parts:02d}.zip"

    async def _build_image_zip_parts(
        self,
        post: NewsPost,
        *,
        max_part_bytes: int,
    ) -> tuple[list[io.BytesIO], int, int]:
        used_names: set[str] = set()
        urls = _downloadable_image_urls(post)
        semaphore = asyncio.Semaphore(ZIP_IMAGE_CONCURRENCY)

        tasks = [
            asyncio.create_task(
                self._prepare_zip_image(semaphore, index, url, convert_png=False)
            )
            for index, url in enumerate(urls)
        ]
        images = await asyncio.gather(*tasks)

        zip_items: list[tuple[str, bytes]] = []
        for item in images:
            if item is None:
                continue
            index, url, content_type, image_bytes = item
            name = _unique_zip_name(used_names, index, url, content_type, native=True)
            zip_items.append((name, image_bytes))

        buffers: list[io.BytesIO] = []
        current_items: list[tuple[str, bytes]] = []
        current_buffer: io.BytesIO | None = None
        count = 0
        skipped = 0

        for item in zip_items:
            candidate_items = [*current_items, item]
            candidate_buffer = self._zip_buffer_for_items(candidate_items)
            if candidate_buffer.getbuffer().nbytes <= max_part_bytes:
                current_items = candidate_items
                current_buffer = candidate_buffer
                continue

            if current_buffer is not None:
                buffers.append(current_buffer)
                count += len(current_items)

            single_buffer = self._zip_buffer_for_items([item])
            if single_buffer.getbuffer().nbytes <= max_part_bytes:
                current_items = [item]
                current_buffer = single_buffer
            else:
                current_items = []
                current_buffer = None
                skipped += 1

        if current_buffer is not None:
            buffers.append(current_buffer)
            count += len(current_items)

        return buffers, count, skipped

    @staticmethod
    def _zip_buffer_for_items(items: list[tuple[str, bytes]]) -> io.BytesIO:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_STORED) as archive:
            for name, image_bytes in items:
                archive.writestr(name, image_bytes)
        buffer.seek(0)
        return buffer

    async def _prepare_zip_image(
        self, semaphore: asyncio.Semaphore, index: int, url: str, *, convert_png: bool = True
    ) -> tuple[int, str, str | None, bytes] | None:
        async with semaphore:
            download_urls = _original_image_download_candidates(url)
            downloaded = None
            for download_url in download_urls:
                downloaded = await self._download_image(download_url)
                if downloaded is not None:
                    break
            if downloaded is None:
                return None

            data, content_type = downloaded
            if convert_png:
                async with self._image_process_semaphore:
                    data, content_type = await asyncio.to_thread(
                        _image_bytes_as_png,
                        data,
                        content_type,
                    )
                if content_type != "image/png":
                    return None
            return index, download_urls[0], content_type, data

    def _schedule_channel_image_messages(
        self,
        channel: discord.abc.Messageable,
        post: NewsPost,
        *,
        track_guild_id: int | None = None,
        track_channel_id: int | None = None,
        batch_tasks: list[asyncio.Task[list[discord.File]]] | None = None,
        image_urls: list[str] | None = None,
        news_target_id: int | None = None,
    ) -> None:
        urls = (
            image_urls
            if image_urls is not None
            else _content_image_urls(post)
        )
        resolved_tasks = batch_tasks if batch_tasks is not None else self._start_image_batch_tasks(urls)
        if not resolved_tasks:
            return

        task = asyncio.create_task(
            self._send_channel_image_messages(
                channel,
                post,
                track_guild_id=track_guild_id,
                track_channel_id=track_channel_id,
                batch_tasks=resolved_tasks,
                news_target_id=news_target_id,
            )
        )
        task.add_done_callback(self._log_background_task_result)

    def _schedule_interaction_image_followups(
        self,
        interaction: discord.Interaction,
        post: NewsPost,
        *,
        private: bool,
        batch_tasks: list[asyncio.Task[list[discord.File]]] | None = None,
        image_urls: list[str] | None = None,
    ) -> None:
        urls = (
            image_urls
            if image_urls is not None
            else _content_image_urls(post)
        )
        resolved_tasks = batch_tasks if batch_tasks is not None else self._start_image_batch_tasks(urls)
        if not resolved_tasks:
            return

        task = asyncio.create_task(
            self._send_interaction_image_followups(
                interaction,
                post,
                private=private,
                batch_tasks=resolved_tasks,
            )
        )
        task.add_done_callback(self._log_background_task_result)

    def _schedule_interaction_image_embed_followups(
        self,
        interaction: discord.Interaction,
        post: NewsPost,
        *,
        private: bool,
        image_urls: list[str] | None = None,
    ) -> None:
        urls = (
            image_urls
            if image_urls is not None
            else _content_image_urls(post)
        )
        if not urls:
            return

        task = asyncio.create_task(
            self._send_interaction_image_embed_followups(
                interaction, post, private=private, image_urls=urls
            )
        )
        task.add_done_callback(self._log_background_task_result)

    def _schedule_channel_image_embed_messages(
        self,
        channel: discord.abc.Messageable,
        post: NewsPost,
        *,
        track_guild_id: int | None = None,
        track_channel_id: int | None = None,
        image_urls: list[str] | None = None,
    ) -> None:
        urls = (
            image_urls
            if image_urls is not None
            else _content_image_urls(post)
        )
        if not urls:
            return

        task = asyncio.create_task(
            self._send_channel_image_embed_messages(
                channel,
                post,
                track_guild_id=track_guild_id,
                track_channel_id=track_channel_id,
                image_urls=urls,
            )
        )
        task.add_done_callback(self._log_background_task_result)

    async def _send_channel_image_messages(
        self,
        channel: discord.abc.Messageable,
        post: NewsPost,
        *,
        track_guild_id: int | None = None,
        track_channel_id: int | None = None,
        batch_tasks: list[asyncio.Task[list[discord.File]]] | None = None,
        news_target_id: int | None = None,
    ) -> None:
        target = await self._resolve_background_channel(channel, track_channel_id)
        if target is None:
            LOGGER.debug("대상 채널을 사용할 수 없어 이미지 첨부를 건너뜁니다.")
            return

        tasks = batch_tasks or []
        batch_index = 0
        for batch_task in asyncio.as_completed(tasks):
            file_batch = await batch_task
            if not file_batch:
                continue
            try:
                message = await target.send(
                    files=file_batch,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except (discord.Forbidden, discord.NotFound):
                LOGGER.debug(
                    "채널 %s에 더 이상 접근할 수 없어 이미지 첨부를 건너뜁니다.",
                    track_channel_id or getattr(target, "id", "unknown"),
                )
                return
            await self._track_manual_message(track_guild_id, track_channel_id, message)
            if news_target_id is not None:
                self.storage.record_news_post_image_message(
                    news_target_id,
                    post.post_id,
                    getattr(target, "id", track_channel_id),
                    message.id,
                    batch_index,
                )
            batch_index += 1

    async def _send_channel_image_embed_messages(
        self,
        channel: discord.abc.Messageable,
        post: NewsPost,
        *,
        track_guild_id: int | None = None,
        track_channel_id: int | None = None,
        image_urls: list[str] | None = None,
    ) -> None:
        target = await self._resolve_background_channel(channel, track_channel_id)
        if target is None:
            LOGGER.debug("대상 채널을 사용할 수 없어 이미지 임베드를 건너뜁니다.")
            return

        urls = (
            image_urls
            if image_urls is not None
            else _content_image_urls(post)
        )
        for embed_batch in _image_embed_batches_from_urls(urls, post):
            try:
                message = await target.send(
                    embeds=embed_batch,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except (discord.Forbidden, discord.NotFound):
                LOGGER.debug(
                    "채널 %s에 더 이상 접근할 수 없어 이미지 임베드를 건너뜁니다.",
                    track_channel_id or getattr(target, "id", "unknown"),
                )
                return
            await self._track_manual_message(track_guild_id, track_channel_id, message)

    async def _resolve_background_channel(
        self,
        channel: discord.abc.Messageable,
        channel_id: int | None,
    ) -> discord.abc.Messageable | None:
        if channel_id is None:
            return channel

        cached = self.bot.get_channel(channel_id)
        if isinstance(cached, discord.abc.Messageable):
            return cached

        try:
            fetched = await self.bot.fetch_channel(channel_id)
        except (discord.Forbidden, discord.NotFound):
            return None
        return fetched if isinstance(fetched, discord.abc.Messageable) else None

    async def _send_interaction_image_followups(
        self,
        interaction: discord.Interaction,
        post: NewsPost,
        *,
        private: bool,
        batch_tasks: list[asyncio.Task[list[discord.File]]] | None = None,
    ) -> None:
        for batch_task in asyncio.as_completed(batch_tasks or []):
            file_batch = await batch_task
            if not file_batch:
                continue
            try:
                message = await interaction.followup.send(
                    files=file_batch,
                    ephemeral=private,
                    allowed_mentions=discord.AllowedMentions.none(),
                    wait=True,
                )
            except (discord.Forbidden, discord.NotFound):
                LOGGER.debug("인터랙션에 더 이상 접근할 수 없어 이미지 팔로업을 건너뜁니다.")
                return
            if not private:
                await self._track_manual_message(
                    interaction.guild_id,
                    interaction.channel_id,
                    message,
                )

    async def _send_interaction_image_embed_followups(
        self,
        interaction: discord.Interaction,
        post: NewsPost,
        *,
        private: bool,
        image_urls: list[str] | None = None,
    ) -> None:
        urls = (
            image_urls
            if image_urls is not None
            else _content_image_urls(post)
        )
        for embed_batch in _image_embed_batches_from_urls(urls, post):
            try:
                message = await interaction.followup.send(
                    embeds=embed_batch,
                    ephemeral=private,
                    allowed_mentions=discord.AllowedMentions.none(),
                    wait=True,
                )
            except (discord.Forbidden, discord.NotFound):
                LOGGER.debug("인터랙션에 더 이상 접근할 수 없어 이미지 임베드 팔로업을 건너뜁니다.")
                return
            if not private:
                await self._track_manual_message(
                    interaction.guild_id,
                    interaction.channel_id,
                    message,
                )

    @staticmethod
    def _log_background_task_result(task: asyncio.Task[None]) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            LOGGER.exception("백그라운드 이미지 전송 실패.")

    def _start_image_batch_tasks(
        self,
        urls: list[str],
        *,
        batch_size: int = IMAGE_FILES_PER_MESSAGE,
    ) -> list[asyncio.Task[list[discord.File]]]:
        if not urls:
            return []
        semaphore = asyncio.Semaphore(ZIP_IMAGE_CONCURRENCY)
        tasks: list[asyncio.Task[list[discord.File]]] = []
        for batch_start in range(0, len(urls), batch_size):
            batch_urls = urls[batch_start : batch_start + batch_size]
            task = asyncio.create_task(
                self._download_file_batch(semaphore, batch_start, batch_urls)
            )
            task.add_done_callback(self._log_background_task_result)
            tasks.append(task)
        return tasks

    def _start_image_batch_tasks_for_posts(
        self,
        posts: list[NewsPost],
    ) -> dict[str, list[asyncio.Task[list[discord.File]]]]:
        batches: dict[str, list[asyncio.Task[list[discord.File]]]] = {}
        for post in posts:
            urls = _standalone_image_urls(post, attach_images=True)
            if urls:
                batches[post.post_id] = self._start_image_batch_tasks(urls)
        return batches

    def _start_twitter_image_batch_tasks_for_posts(
        self,
        posts: list[TwitterPost],
    ) -> dict[str, list[asyncio.Task[list[discord.File]]]]:
        batches: dict[str, list[asyncio.Task[list[discord.File]]]] = {}
        for post in posts:
            urls = _twitter_image_urls(post)
            if len(urls) > 1:
                batches[post.post_id] = self._start_image_batch_tasks(urls)
        return batches

    async def _download_file_batch(
        self,
        semaphore: asyncio.Semaphore,
        offset: int,
        urls: list[str],
    ) -> list[discord.File]:
        image_tasks = [
            asyncio.create_task(self._prepare_zip_image(semaphore, offset + i, url, convert_png=False))
            for i, url in enumerate(urls)
        ]
        images = await asyncio.gather(*image_tasks)
        used_names: set[str] = set()
        files: list[discord.File] = []
        for item in images:
            if item is None:
                continue
            index, url, content_type, data = item
            filename = _unique_zip_name(used_names, index, url, content_type, native=True)
            files.append(discord.File(io.BytesIO(data), filename=filename))
        return files

    def _schedule_image_cache_warmup(self, posts: list[NewsPost]) -> None:
        urls: list[str] = []
        seen_urls: set[str] = set()
        for post in posts[: IMAGE_CACHE_WARM_POST_LIMIT * len(SYNC_LANGUAGES)]:
            for url in _downloadable_image_urls(post):
                for candidate in _original_image_download_candidates(url):
                    if (
                        candidate in seen_urls
                        or candidate in self._image_cache
                        or candidate in self._image_download_tasks
                        or candidate in self._failed_image_urls
                    ):
                        continue
                    seen_urls.add(candidate)
                    urls.append(candidate)

        if not urls:
            return

        task = asyncio.create_task(self._warm_image_cache(urls))
        task.add_done_callback(self._log_background_task_result)

    async def _warm_image_cache(self, urls: list[str]) -> None:
        semaphore = asyncio.Semaphore(ZIP_IMAGE_CONCURRENCY)

        async def warm_one(url: str) -> None:
            async with semaphore:
                await self._download_image(url)

        await asyncio.gather(*(warm_one(url) for url in urls))

    async def _image_file_batches_for_post(
        self, post: NewsPost, *, urls: list[str] | None = None
    ) -> list[list[discord.File]]:
        resolved = urls if urls is not None else _content_image_urls(post)
        if not resolved:
            return []
        batch_tasks = self._start_image_batch_tasks(resolved)
        return [files for files in await asyncio.gather(*batch_tasks) if files]

    async def _download_image(self, url: str) -> tuple[bytes, str | None] | None:
        cached = self._image_cache.get(url)
        if cached is not None:
            self._image_cache[url] = self._image_cache.pop(url)
            return cached
        if url in self._failed_image_urls:
            self._failed_image_urls[url] = self._failed_image_urls.pop(url)
            return None

        task = self._image_download_tasks.get(url)
        if task is None:
            task = asyncio.create_task(self._fetch_image(url))
            self._image_download_tasks[url] = task

        try:
            return await asyncio.shield(task)
        finally:
            if task.done():
                self._image_download_tasks.pop(url, None)

    async def _image_download_response_result(
        self,
        response: aiohttp.ClientResponse,
        url: str,
        attempt: int,
    ) -> tuple[bytes, str | None] | object | None:
        if 400 <= response.status < 500:
            self._handle_image_download_client_error(response.status, url)
            return None
        if response.status >= 500:
            return await self._image_download_retry_or_none(
                "이미지 다운로드 최종 실패 (%s/%s, HTTP %s): %s",
                "이미지 다운로드 재시도 (%s/%s, HTTP %s): %s",
                attempt,
                url,
                response.status,
            )

        content_type = response.headers.get("Content-Type")
        data = await response.read()
        if data:
            return data, content_type
        return await self._image_download_retry_or_none(
            "이미지 다운로드 최종 실패: 빈 응답 (%s/%s): %s",
            "이미지 다운로드 재시도: 빈 응답 (%s/%s): %s",
            attempt,
            url,
        )

    def _handle_image_download_client_error(self, status: int, url: str) -> None:
        if status in {400, 401, 403, 404, 410}:
            self._remember_failed_image_url(url)
        if status == 403 and _is_namu_wiki_image_url(url):
            LOGGER.debug("나무위키 이미지 직접 다운로드 거부 (403): %s", url)
        else:
            LOGGER.warning("이미지 다운로드 실패 (%s): %s", status, url)

    async def _image_download_retry_or_none(
        self,
        final_message: str,
        retry_message: str,
        attempt: int,
        url: str,
        *extra: object,
    ) -> object | None:
        if attempt >= IMAGE_DOWNLOAD_ATTEMPTS:
            LOGGER.warning(final_message, attempt, IMAGE_DOWNLOAD_ATTEMPTS, *extra, url)
            return None
        LOGGER.info(retry_message, attempt, IMAGE_DOWNLOAD_ATTEMPTS, *extra, url)
        await asyncio.sleep(0.5 * attempt)
        return _IMAGE_DOWNLOAD_RETRY

    async def _image_download_exception_result(
        self,
        exc: aiohttp.ClientError | asyncio.TimeoutError,
        attempt: int,
        url: str,
    ) -> object | None:
        return await self._image_download_retry_or_none(
            "이미지 다운로드 최종 실패 (%s/%s, %s): %s",
            "이미지 다운로드 재시도 (%s/%s, %s): %s",
            attempt,
            url,
            type(exc).__name__,
        )

    async def _fetch_image(self, url: str) -> tuple[bytes, str | None] | None:
        cached = self._image_cache.get(url)
        if cached is not None:
            self._image_cache[url] = self._image_cache.pop(url)
            return cached
        if url in self._failed_image_urls:
            self._failed_image_urls[url] = self._failed_image_urls.pop(url)
            return None

        timeout = aiohttp.ClientTimeout(total=IMAGE_DOWNLOAD_TIMEOUT_SECONDS)
        for attempt in range(1, IMAGE_DOWNLOAD_ATTEMPTS + 1):
            try:
                async with self.session.get(
                    url,
                    timeout=timeout,
                    headers=_image_request_headers(url),
                ) as response:
                    result = await self._image_download_response_result(
                        response,
                        url,
                        attempt,
                    )
                    if result is _IMAGE_DOWNLOAD_RETRY:
                        continue
                    if result is None:
                        return None
                    data, content_type = result
                    self._cache_image(url, data, content_type)
                    return data, content_type
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if await self._image_download_exception_result(exc, attempt, url) is None:
                    return None
        return None

    def _remember_failed_image_url(self, url: str) -> None:
        self._failed_image_urls.pop(url, None)
        self._failed_image_urls[url] = None
        while len(self._failed_image_urls) > IMAGE_FAILED_URL_CACHE_MAX_ITEMS:
            oldest_url = next(iter(self._failed_image_urls))
            self._failed_image_urls.pop(oldest_url, None)

    async def _download_twitter_video(self, url: str) -> discord.File | None:
        max_bytes = 23 * 1024 * 1024
        try:
            async with self.session.get(
                url, timeout=aiohttp.ClientTimeout(total=60)
            ) as response:
                if response.status >= 400:
                    LOGGER.warning("트위터 영상 다운로드 실패 (%s): %s", response.status, url)
                    return None
                content_length = response.headers.get("Content-Length")
                if content_length and int(content_length) > max_bytes:
                    LOGGER.warning(
                        "트위터 영상이 Discord 업로드 제한보다 커서 최고화질 링크로 보냅니다 (%s bytes): %s",
                        content_length,
                        url,
                    )
                    return None
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.content.iter_chunked(65536):
                    total += len(chunk)
                    if total > max_bytes:
                        LOGGER.warning(
                            "트위터 영상이 Discord 업로드 제한보다 커서 최고화질 링크로 보냅니다: %s",
                            url,
                        )
                        return None
                    chunks.append(chunk)
                data = b"".join(chunks)
                filename = url.split("/")[-1].split("?")[0] or "video.mp4"
                if not filename.endswith(".mp4"):
                    filename = "video.mp4"
                return discord.File(io.BytesIO(data), filename=filename)
        except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError) as exc:
            _log_internet_exception(f"트위터 영상 다운로드 오류: {url}", exc)
            return None

    def _cache_image(self, url: str, data: bytes, content_type: str | None) -> None:
        size = len(data)
        if size > IMAGE_CACHE_MAX_ITEM_BYTES:
            return
        prev = self._image_cache.pop(url, None)
        if prev is not None:
            self._image_cache_bytes -= len(prev[0])
        self._image_cache[url] = (data, content_type)
        self._image_cache_bytes += size
        while self._image_cache and (
            len(self._image_cache) > IMAGE_CACHE_MAX_ITEMS
            or self._image_cache_bytes > IMAGE_CACHE_MAX_BYTES
        ):
            oldest_url, (oldest_data, _) = next(iter(self._image_cache.items()))
            self._image_cache.pop(oldest_url, None)
            self._image_cache_bytes -= len(oldest_data)

    def _cache_brightened_image(self, url: str, data: bytes) -> None:
        size = len(data)
        if size > BRIGHTEN_CACHE_MAX_ITEM_BYTES:
            return
        prev = self._brighten_cache.pop(url, None)
        if prev is not None:
            self._brighten_cache_bytes -= len(prev)
        self._brighten_cache[url] = data
        self._brighten_cache_bytes += size
        while self._brighten_cache and (
            len(self._brighten_cache) > BRIGHTEN_CACHE_MAX_ITEMS
            or self._brighten_cache_bytes > BRIGHTEN_CACHE_MAX_BYTES
        ):
            oldest_url, oldest_data = next(iter(self._brighten_cache.items()))
            self._brighten_cache.pop(oldest_url, None)
            self._brighten_cache_bytes -= len(oldest_data)

    def _cache_zip(
        self,
        post_id: str,
        zip_part_bytes: list[bytes],
        count: int,
        skipped: int,
        upload_limit: int,
    ) -> None:
        self._zip_cache[post_id] = (zip_part_bytes, count, skipped, upload_limit)
        while len(self._zip_cache) > ZIP_CACHE_MAX_ITEMS:
            oldest_post_id = next(iter(self._zip_cache))
            self._zip_cache.pop(oldest_post_id, None)

    async def run_startup_sync(self) -> None:
        if self._startup_synced:
            LOGGER.info("시작 시 동기화 생략: 이미 완료되었습니다.")
            return
        LOGGER.info("시작 시 동기화 시작: Steam 뉴스, 유튜브 기준선, X 게시물을 확인합니다.")
        await self._sync_steam_news_on_startup()
        await self._sync_youtube_startup_baseline()
        await self._sync_twitter_on_startup()
        LOGGER.info("시작 시 동기화 처리 완료.")
        await self.run_startup_news_delivery()

    async def _sync_steam_news_on_startup(self) -> None:
        if self.news_source is not None:
            try:
                posts_by_language, _, _ = await self._sync_global_news_cache()
                self._startup_synced = True
                self._log_startup_steam_sync_result(posts_by_language)
            except Exception as exc:
                if _is_internet_exception(exc):
                    _log_internet_exception("시작 시 뉴스 동기화 실패", exc)
                else:
                    LOGGER.exception("시작 시 뉴스 동기화 실패.")

    def _log_startup_steam_sync_result(
        self,
        posts_by_language: Mapping[str, list[NewsPost]],
    ) -> None:
        synced_posts = posts_by_language.get(self.config.steam_language) or next(
            iter(posts_by_language.values()),
            [],
        )
        latest = max(
            synced_posts,
            key=lambda post: post.created_at or datetime.min.replace(tzinfo=timezone.utc),
            default=None,
        )
        if latest is None:
            LOGGER.info("시작 시 Steam 뉴스 동기화 완료: 0개 등록.")
            return
        LOGGER.info(
            "시작 시 Steam 뉴스 동기화 완료: %d개 등록. 최신 소식: %s (%s)",
            len(synced_posts),
            latest.title,
            latest.url,
        )

    def _startup_twitter_sync_skip_reason(self, observed_at: datetime) -> str | None:
        if not self.storage.list_all_news_targets() and not self.storage.list_twitter_targets():
            return "no_targets"
        if not self._is_twitter_tracking_window(observed_at):
            return "outside_window"
        return None

    def _log_startup_twitter_sync_skip(self, reason: str) -> None:
        if reason == "no_targets":
            LOGGER.info("시작 시 X 게시물 동기화 생략: 설정된 X/뉴스 자동 전송 대상이 없습니다.")
            return
        LOGGER.info(
            "시작 시 X 게시물 동기화 생략: 현재 X 추적 시간대가 아닙니다 (KST %s).",
            _format_windows_label(self.config.twitter_tracking_windows_kst),
        )

    def _log_startup_twitter_sync_result(self, saved: int) -> None:
        latest = self.storage.get_latest_twitter_post()
        if latest is None:
            LOGGER.info("시작 시 X 게시물 동기화 완료: %d개 저장.", saved)
            return
        LOGGER.info(
            "시작 시 X 게시물 동기화 완료: %d개 저장. 최신 소식: %s (%s)",
            saved,
            latest.title,
            latest.url,
        )

    async def _sync_twitter_on_startup(self) -> None:
        try:
            skip_reason = self._startup_twitter_sync_skip_reason(datetime.now(timezone.utc))
            if skip_reason is not None:
                self._log_startup_twitter_sync_skip(skip_reason)
                return
            saved, _ = await self._sync_twitter_posts()
            self._log_startup_twitter_sync_result(saved)
        except XClientError as exc:
            if _is_internet_exception(exc):
                _log_internet_exception("시작 시 X 게시물 동기화 건너뜀", exc)
            else:
                LOGGER.warning("시작 시 X 게시물 동기화 건너뜀: %s", exc)
        except Exception as exc:
            if _is_internet_exception(exc):
                _log_internet_exception("시작 시 X 게시물 동기화 실패", exc)
            else:
                LOGGER.exception("시작 시 X 게시물 동기화 실패.")

    async def run_startup_news_delivery(self) -> None:
        async with self._poll_lock:
            try:
                announced = await self._poll_once()
                self._last_poll_at = datetime.now(timezone.utc)
                LOGGER.info("시작 시 새 소식 자동 전송 확인 완료: sent=%s.", announced)
            except Exception as exc:
                if _is_internet_exception(exc):
                    _log_internet_exception("시작 시 새 소식 자동 전송 확인 실패", exc)
                else:
                    LOGGER.exception("시작 시 새 소식 자동 전송 확인 실패.")

    async def _sync_youtube_startup_baseline(self) -> None:
        targets = self.storage.list_youtube_targets()
        if not targets:
            return

        try:
            latest = await self.youtube_client.fetch_latest_stream()
        except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError) as exc:
            _log_internet_exception("시작 시 유튜브 기준선 동기화 실패", exc)
            return
        except Exception as exc:
            if _is_internet_exception(exc):
                _log_internet_exception("시작 시 유튜브 기준선 동기화 실패", exc)
            else:
                LOGGER.exception("시작 시 유튜브 기준선 동기화 실패.")
            return

        if latest is None:
            LOGGER.info("시작 시 유튜브 기준선 동기화 생략: 최근 방송을 찾지 못했습니다.")
            return

        updated = 0
        for target in targets:
            if target.last_live_id == latest.video_id:
                continue
            self.storage.upsert_youtube_target(
                target.guild_id,
                channel_id=target.channel_id,
                enabled=target.enabled,
                last_live_id=latest.video_id,
                is_live=False,
            )
            updated += 1

        LOGGER.info(
            "시작 시 유튜브 기준선 동기화 완료: targets=%d updated=%d latest=%s (%s)",
            len(targets),
            updated,
            latest.title,
            latest.url,
        )

    async def _refresh_recent_news_cache(self, language: str) -> None:
        await self._refresh_recent_steam_news_cache(language)
        await self._refresh_recent_twitter_news_cache()

    async def _refresh_recent_steam_news_cache(self, language: str) -> None:
        if self.news_source is not None:
            try:
                fresh = await self.news_source.fetch_recent_posts(language, limit=NEWS_POST_LIMIT)
            except Exception as exc:
                if _is_internet_exception(exc):
                    _log_internet_exception("최신 뉴스 조회 실패. 캐시를 유지합니다", exc)
                else:
                    LOGGER.exception("최신 뉴스 조회 실패. 캐시를 유지합니다.")
            else:
                if fresh:
                    self.storage.save_posts(fresh[:NEWS_POST_LIMIT])
                    self._schedule_image_cache_warmup(fresh[:NEWS_POST_LIMIT])

    async def _refresh_recent_twitter_news_cache(self) -> None:
        if self.x_source is not None and self._is_twitter_tracking_window(datetime.now(timezone.utc)):
            try:
                await self._sync_twitter_posts()
            except XClientError as exc:
                if _is_internet_exception(exc):
                    _log_internet_exception("X 게시물 조회 실패", exc)
                else:
                    LOGGER.warning("X 게시물 조회 실패: %s", exc)

    async def _track_manual_message(
        self,
        guild_id: int | None,
        channel_id: int | None,
        message: discord.Message | None,
    ) -> None:
        if guild_id is None or channel_id is None or message is None:
            return
        self.storage.add_tracked_message(guild_id, channel_id, message.id)

    def _destination_logs(
        self,
        guild_id: int | None,
        channel: object | None,
        channel_id: int | None,
    ) -> tuple[str, str]:
        guild = getattr(channel, "guild", None)
        if guild is None and guild_id is not None:
            guild = self.bot.get_guild(guild_id)
        return (
            _format_guild_for_log(guild, guild_id),
            _format_channel_for_log(channel, channel_id),
        )

    async def _x_news_probe_observe(self, targets: list[GuildTwitterTarget]) -> None:
        observed_at = datetime.now(timezone.utc)
        run_kst = observed_at.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S KST")
        loop = asyncio.get_event_loop()
        t0 = loop.time()
        error: str | None = None
        posts: list[TwitterPost] = []
        try:
            posts = await self.x_source.fetch_recent_posts(
                limit=TWITTER_POST_LIMIT,
                ignore_rate_limit_backoff=True,
            )
        except XClientError as exc:
            error = f"{type(exc).__name__}: {exc}"
        fetch_ms = (loop.time() - t0) * 1000
        posts = posts[:TWITTER_POST_LIMIT]

        steam_by_id: dict[str, list[NewsPost]] = (
            self._steam_posts_by_twitter_post_id(posts) if posts else {}
        )
        max_age = (
            self.config.twitter_announce_max_age_seconds
            or TWITTER_NEWS_DEFAULT_MAX_AGE_SECONDS
        )

        def post_info(post: TwitterPost) -> dict:
            created = post.created_at
            age = None
            created_kst = None
            if created is not None:
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                age = (observed_at - created).total_seconds()
                created_kst = created.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S KST")
            return {
                "post_id": post.post_id,
                "created_at_kst": created_kst,
                "age_sec": round(age, 1) if age is not None else None,
                "is_reply": bool(post.raw.get("in_reply_to_status_id_str")),
                "is_retweet": post.text.startswith("RT @"),
                "has_steam_link": bool(_steam_news_link_keys_for_twitter(post)),
                "matched_steam": [s.post_id for s in steam_by_id.get(post.post_id, [])],
                "age_gate_pass": _is_twitter_post_recent(post, max_age),
            }

        targets_info = []
        for target in targets:
            would = self._new_twitter_posts_for_target(target, posts) if posts else []
            would_after_age = [p for p in would if _is_twitter_post_recent(p, max_age)]
            targets_info.append({
                "guild_id": target.guild_id,
                "channel_id": target.channel_id,
                "enabled": target.enabled,
                "last_seen_post_id": target.last_seen_post_id,
                "would_announce": [p.post_id for p in would],
                "would_announce_after_age_gate": [p.post_id for p in would_after_age],
            })

        record = {
            "probe": "x_news",
            "observed_at_kst": run_kst,
            "in_tracking_window": self._is_twitter_tracking_window(observed_at),
            "poll_interval_sec": self._current_twitter_poll_interval_seconds(observed_at),
            "tracking_windows_kst": _format_windows_label(self.config.twitter_tracking_windows_kst),
            "age_gate_max_age_sec": max_age,
            "fetch_duration_ms": round(fetch_ms, 1),
            "fetched_count": len(posts),
            "fetch_error": error,
            "posts": [post_info(p) for p in posts],
            "targets": targets_info,
        }
        try:
            log_dir = Path("logs")
            log_dir.mkdir(parents=True, exist_ok=True)
            path = log_dir / f"x_news_probe_{observed_at.astimezone(KST):%Y%m%d}.jsonl"
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            LOGGER.exception("X 관측 프로브 JSONL 기록 실패.")
        LOGGER.info(
            "[x_news_probe] window=%s fetched=%s err=%s would=%s",
            record["in_tracking_window"],
            len(posts),
            error or "none",
            sum(len(t["would_announce"]) for t in targets_info),
        )

    def _twitter_fetch_cache_ttl(self, now: datetime) -> timedelta | None:
        if _is_twitter_priority_poll_window(now):
            return timedelta(seconds=1)
        return None

    async def _sync_twitter_posts(
        self,
        *,
        cache_ttl: timedelta | None = None,
    ) -> tuple[int, list[TwitterPost]]:
        posts = await self.x_source.fetch_recent_posts(
            limit=TWITTER_POST_LIMIT,
            cache_ttl=cache_ttl,
            ignore_rate_limit_backoff=True,
        )
        saved = self.storage.save_twitter_posts(posts)
        return saved, posts

    def _mark_twitter_recovery_baseline(
        self,
        targets: list[GuildTwitterTarget],
        latest_post_id: str,
    ) -> None:
        updated = 0
        for target in targets:
            if target.enabled and target.last_seen_post_id != latest_post_id:
                self.storage.mark_twitter_target_seen(target.guild_id, latest_post_id)
                updated += 1
        self._twitter_recovery_baseline_pending = False
        LOGGER.info(
            "네트워크 복구 후 X 게시물 기준선을 갱신했습니다. 누적 X 자동 전송은 건너뜁니다 "
            "(post_id=%s, targets=%s).",
            latest_post_id,
            updated,
        )

    def _twitter_target_settings(
        self,
        targets: list[GuildTwitterTarget],
    ) -> dict[int, GuildSettings]:
        return {
            target.guild_id: self.storage.get_settings(target.guild_id)
            for target in targets
        }

    def _twitter_targets_using_steam_preference(
        self,
        targets: list[GuildTwitterTarget],
        settings_by_guild_id: Mapping[int, GuildSettings],
    ) -> list[GuildTwitterTarget]:
        return [
            target
            for target in targets
            if target.enabled
            and settings_by_guild_id[target.guild_id].news_source_mode != NEWS_SOURCE_TWITTER
        ]

    async def _steam_posts_by_twitter_id_for_targets(
        self,
        targets: list[GuildTwitterTarget],
        settings_by_guild_id: Mapping[int, GuildSettings],
        posts: list[TwitterPost],
    ) -> dict[str, list[NewsPost]]:
        targets_using_steam_preference = self._twitter_targets_using_steam_preference(
            targets,
            settings_by_guild_id,
        )
        if not targets_using_steam_preference:
            return {}
        await self._refresh_steam_cache_for_twitter_links(posts)
        return self._steam_posts_by_twitter_post_id(posts)

    def _new_recent_twitter_posts_for_target(
        self,
        target: GuildTwitterTarget,
        posts: list[TwitterPost],
    ) -> list[TwitterPost]:
        latest_post_id = posts[0].post_id
        new_posts = self._new_twitter_posts_for_target(target, posts)
        if not new_posts:
            self.storage.mark_twitter_target_seen(target.guild_id, latest_post_id)
            return []

        max_age = (
            self.config.twitter_announce_max_age_seconds
            or TWITTER_NEWS_DEFAULT_MAX_AGE_SECONDS
        )
        if max_age <= 0:
            return new_posts

        recent_posts = [post for post in new_posts if _is_twitter_post_recent(post, max_age)]
        if not recent_posts:
            self.storage.mark_twitter_target_seen(target.guild_id, latest_post_id)
        return recent_posts

    async def _process_twitter_targets(
        self,
        targets: list[GuildTwitterTarget],
        posts: list[TwitterPost],
        settings_by_guild_id: Mapping[int, GuildSettings],
        steam_posts_by_twitter_id: Mapping[str, list[NewsPost]],
    ) -> int:
        send_semaphore = asyncio.Semaphore(NEWS_TARGET_SEND_CONCURRENCY)
        results = await asyncio.gather(
            *(
                asyncio.create_task(
                    self._process_twitter_target(
                        target,
                        posts,
                        settings_by_guild_id[target.guild_id],
                        steam_posts_by_twitter_id,
                        send_semaphore,
                    )
                )
                for target in targets
            ),
            return_exceptions=True,
        )
        return self._sum_twitter_target_results(results)

    def _sum_twitter_target_results(self, results: Iterable[object]) -> int:
        announced = 0
        for result in results:
            if isinstance(result, Exception):
                LOGGER.error(
                    "X 게시물 자동 전송 대상 처리 실패.",
                    exc_info=(type(result), result, result.__traceback__),
                )
                continue
            announced += int(result)
        return announced

    async def _process_twitter_target(
        self,
        target: GuildTwitterTarget,
        posts: list[TwitterPost],
        settings: GuildSettings,
        steam_posts_by_twitter_id: Mapping[str, list[NewsPost]],
        send_semaphore: asyncio.Semaphore,
    ) -> int:
        if not target.enabled:
            return 0
        async with send_semaphore:
            channel = await self._resolve_twitter_target_channel(target)
            if channel is None:
                return 0
            new_posts = self._new_recent_twitter_posts_for_target(target, posts)
            if not new_posts:
                return 0
            prefer_steam_duplicates = settings.news_source_mode != NEWS_SOURCE_TWITTER
            return await self._send_twitter_posts_to_target(
                target,
                channel,
                new_posts,
                steam_posts_by_twitter_id,
                prefer_steam_duplicates=prefer_steam_duplicates,
            )

    async def _send_twitter_posts_to_target(
        self,
        target: GuildTwitterTarget,
        channel: discord.abc.Messageable,
        new_posts: list[TwitterPost],
        steam_posts_by_twitter_id: Mapping[str, list[NewsPost]],
        *,
        prefer_steam_duplicates: bool,
    ) -> int:
        guild_log, channel_log = self._destination_logs(
            target.guild_id,
            channel,
            target.channel_id,
        )
        announced = 0
        image_batches_by_post_id = self._start_twitter_image_batch_tasks_for_posts(new_posts)
        for post in new_posts:
            matching_steam_posts = (
                steam_posts_by_twitter_id.get(post.post_id, [])
                if prefer_steam_duplicates
                else []
            )
            if matching_steam_posts:
                self._skip_twitter_post_for_steam_duplicate(target, post, matching_steam_posts)
                continue
            sent = await self._send_twitter_post_for_target(
                target,
                channel,
                post,
                image_batches_by_post_id.get(post.post_id),
            )
            if not sent:
                continue
            self.storage.mark_twitter_target_seen(target.guild_id, post.post_id)
            LOGGER.info(
                "새 X 게시물 공지 | %s | %s | delay=%s초 | 제목=%s",
                guild_log,
                channel_log,
                _twitter_post_delay_seconds(post),
                post.title,
            )
            announced += 1
        return announced

    def _skip_twitter_post_for_steam_duplicate(
        self,
        target: GuildTwitterTarget,
        post: TwitterPost,
        matching_steam_posts: list[NewsPost],
    ) -> None:
        self.storage.mark_twitter_target_seen(target.guild_id, post.post_id)
        LOGGER.info(
            "X 게시물 자동 전송 생략: 같은 Steam 소식을 우선합니다 "
            "(guild_id=%s, channel_id=%s, twitter_post_id=%s, steam_post_ids=%s).",
            target.guild_id,
            target.channel_id,
            post.post_id,
            ", ".join(steam_post.post_id for steam_post in matching_steam_posts),
        )

    async def _send_twitter_post_for_target(
        self,
        target: GuildTwitterTarget,
        channel: discord.abc.Messageable,
        post: TwitterPost,
        batch_tasks: list[asyncio.Task[list[discord.File]]] | None,
    ) -> bool:
        try:
            await self._send_twitter_post_to_channel(
                channel,
                post,
                batch_tasks=batch_tasks,
            )
        except discord.HTTPException:
            LOGGER.exception(
                "X 게시물 자동 전송 실패 (guild_id=%s, channel_id=%s, post_id=%s).",
                target.guild_id,
                target.channel_id,
                post.post_id,
            )
            return False
        return True

    async def _poll_twitter_once(self) -> int:
        targets = self.storage.list_twitter_targets()
        if not targets:
            return 0

        if self._x_probe_active:
            await self._x_news_probe_observe(targets)
            return 0

        now = datetime.now(timezone.utc)
        _, posts = await self._sync_twitter_posts(cache_ttl=self._twitter_fetch_cache_ttl(now))
        if not posts:
            self._twitter_recovery_baseline_pending = False
            return 0

        posts = posts[:TWITTER_POST_LIMIT]
        if self._twitter_recovery_baseline_pending:
            self._mark_twitter_recovery_baseline(targets, posts[0].post_id)
            return 0

        settings_by_guild_id = self._twitter_target_settings(targets)
        steam_posts_by_twitter_id = await self._steam_posts_by_twitter_id_for_targets(
            targets,
            settings_by_guild_id,
            posts,
        )
        return await self._process_twitter_targets(
            targets,
            posts,
            settings_by_guild_id,
            steam_posts_by_twitter_id,
        )

    def _hampang_poll_context(
        self,
        x_posts: list[TwitterPost],
        youtube_uploads: list[YoutubeUpload],
        window_started_at: datetime | None,
    ) -> HampangPollContext:
        latest_x_post_id = x_posts[0].post_id if x_posts else None
        latest_youtube_video_id = youtube_uploads[0].video_id if youtube_uploads else None
        max_age = (
            self.config.twitter_announce_max_age_seconds
            or TWITTER_NEWS_DEFAULT_MAX_AGE_SECONDS
        )
        return HampangPollContext(
            x_posts=x_posts,
            youtube_uploads=youtube_uploads,
            latest_x_post_id=latest_x_post_id,
            latest_youtube_video_id=latest_youtube_video_id,
            x_ids=[post.post_id for post in x_posts],
            youtube_ids=[upload.video_id for upload in youtube_uploads],
            x_baseline_only=self._hampang_x_recovery_baseline_pending
            and latest_x_post_id is not None,
            youtube_baseline_only=self._hampang_youtube_recovery_baseline_pending
            and latest_youtube_video_id is not None,
            window_started_at=window_started_at,
            max_age_seconds=max_age,
        )

    def _hampang_x_target_plan(
        self,
        target: GuildHampangTarget,
        context: HampangPollContext,
    ) -> tuple[str | None, list[TwitterPost]]:
        if not context.x_posts or context.latest_x_post_id is None:
            return None, []
        if context.x_baseline_only:
            return context.latest_x_post_id, []
        if not target.last_x_post_id or target.last_x_post_id not in context.x_ids:
            LOGGER.info(
                "햄햄팡팡 X 기준선 설정: 기존 데이터가 없어 누적 소식 공지를 건너뜁니다 "
                "(guild_id=%s, post_id=%s).",
                target.guild_id,
                context.latest_x_post_id,
            )
            return context.latest_x_post_id, []

        last_seen_index = context.x_ids.index(target.last_x_post_id)
        candidate_posts = list(reversed(context.x_posts[:last_seen_index]))
        new_posts = self._auto_sendable_hampang_x_posts(
            candidate_posts,
            target=target,
            window_started_at=context.window_started_at,
            max_age_seconds=context.max_age_seconds,
        )
        if candidate_posts and not new_posts:
            self._log_hampang_x_baseline_skip(target, context, len(candidate_posts))
            return context.latest_x_post_id, []
        return None, new_posts

    def _hampang_youtube_target_plan(
        self,
        target: GuildHampangTarget,
        context: HampangPollContext,
    ) -> tuple[str | None, list[YoutubeUpload]]:
        if not context.youtube_uploads or context.latest_youtube_video_id is None:
            return None, []
        if context.youtube_baseline_only:
            return context.latest_youtube_video_id, []
        if (
            not target.last_youtube_video_id
            or target.last_youtube_video_id not in context.youtube_ids
        ):
            LOGGER.info(
                "햄햄팡팡 YouTube 기준선 설정: 기존 데이터가 없어 누적 소식 공지를 건너뜁니다 "
                "(guild_id=%s, video_id=%s).",
                target.guild_id,
                context.latest_youtube_video_id,
            )
            return context.latest_youtube_video_id, []

        last_seen_index = context.youtube_ids.index(target.last_youtube_video_id)
        candidate_uploads = list(reversed(context.youtube_uploads[:last_seen_index]))
        new_uploads = self._auto_sendable_hampang_youtube_uploads(
            candidate_uploads,
            target=target,
            window_started_at=context.window_started_at,
        )
        if candidate_uploads and not new_uploads:
            self._log_hampang_youtube_baseline_skip(target, context, len(candidate_uploads))
            return context.latest_youtube_video_id, []
        return None, new_uploads

    def _hampang_target_plan(
        self,
        target: GuildHampangTarget,
        context: HampangPollContext,
    ) -> HampangTargetPlan:
        baseline_x_id, new_x_posts = self._hampang_x_target_plan(target, context)
        baseline_youtube_id, new_youtube_uploads = self._hampang_youtube_target_plan(
            target,
            context,
        )
        return HampangTargetPlan(
            baseline_x_id,
            baseline_youtube_id,
            new_x_posts,
            new_youtube_uploads,
        )

    @staticmethod
    def _hampang_target_created_at_log(target: GuildHampangTarget) -> str:
        if not target.created_at:
            return "none"
        created_at = _as_utc_datetime(target.created_at)
        return created_at.isoformat() if created_at else "none"

    def _log_hampang_x_baseline_skip(
        self,
        target: GuildHampangTarget,
        context: HampangPollContext,
        candidate_count: int,
    ) -> None:
        LOGGER.info(
            "햄햄팡팡 X 자동 전송 후보를 기준선만 갱신하고 건너뜁니다 "
            "(guild_id=%s, channel_id=%s, candidates=%s, latest_post_id=%s, "
            "window_started_at=%s, target_created_at=%s).",
            target.guild_id,
            target.channel_id,
            candidate_count,
            context.latest_x_post_id,
            context.window_started_at.isoformat() if context.window_started_at else "none",
            self._hampang_target_created_at_log(target),
        )

    def _log_hampang_youtube_baseline_skip(
        self,
        target: GuildHampangTarget,
        context: HampangPollContext,
        candidate_count: int,
    ) -> None:
        LOGGER.info(
            "햄햄팡팡 YouTube 자동 전송 후보를 기준선만 갱신하고 건너뜁니다 "
            "(guild_id=%s, channel_id=%s, candidates=%s, latest_video_id=%s, "
            "window_started_at=%s, target_created_at=%s).",
            target.guild_id,
            target.channel_id,
            candidate_count,
            context.latest_youtube_video_id,
            context.window_started_at.isoformat() if context.window_started_at else "none",
            self._hampang_target_created_at_log(target),
        )

    def _mark_hampang_plan_baseline(
        self,
        target: GuildHampangTarget,
        plan: HampangTargetPlan,
    ) -> None:
        if plan.baseline_x_id is None and plan.baseline_youtube_id is None:
            return
        self.storage.mark_hampang_target_seen(
            target.guild_id,
            x_post_id=plan.baseline_x_id,
            youtube_video_id=plan.baseline_youtube_id,
        )

    async def _send_hampang_target_plan(
        self,
        target: GuildHampangTarget,
        plan: HampangTargetPlan,
        send_semaphore: asyncio.Semaphore,
    ) -> int:
        items = _hampang_news_items(
            plan.new_x_posts,
            plan.new_youtube_uploads,
            newest_first=False,
        )
        if not items:
            return 0

        async with send_semaphore:
            channel = await self._resolve_hampang_target_channel(target)
            if channel is None:
                return 0
            guild_log, channel_log = self._destination_logs(
                target.guild_id,
                channel,
                target.channel_id,
            )
            settings = self.storage.get_settings(target.guild_id)
            image_batches_by_post_id = self._start_twitter_image_batch_tasks_for_posts(
                plan.new_x_posts
            )
            return await self._send_hampang_items_to_channel(
                target,
                channel,
                settings,
                items,
                image_batches_by_post_id,
                guild_log,
                channel_log,
            )

    async def _send_hampang_items_to_channel(
        self,
        target: GuildHampangTarget,
        channel: discord.abc.Messageable,
        settings: GuildSettings,
        items: list[tuple[str, TwitterPost | YoutubeUpload]],
        image_batches_by_post_id: dict[str, list[asyncio.Task[list[discord.File]]]],
        guild_log: str,
        channel_log: str,
    ) -> int:
        announced = 0
        for source, item in items:
            role_id = settings.role_id if announced == 0 else None
            try:
                message = await self._send_hampang_item_to_channel(
                    target,
                    channel,
                    source,
                    item,
                    role_id,
                    image_batches_by_post_id,
                    guild_log,
                    channel_log,
                )
            except discord.HTTPException:
                LOGGER.exception(
                    "햄햄팡팡 소식 자동 전송 실패 "
                    "(guild_id=%s, channel_id=%s, source=%s).",
                    target.guild_id,
                    target.channel_id,
                    source,
                )
                break
            if message is None:
                continue
            await self._track_manual_message(target.guild_id, target.channel_id, message)
            announced += 1
        return announced

    async def _send_hampang_item_to_channel(
        self,
        target: GuildHampangTarget,
        channel: discord.abc.Messageable,
        source: str,
        item: TwitterPost | YoutubeUpload,
        role_id: int | None,
        image_batches_by_post_id: dict[str, list[asyncio.Task[list[discord.File]]]],
        guild_log: str,
        channel_log: str,
    ) -> discord.Message | None:
        if source == HAMPANG_SOURCE_X and isinstance(item, TwitterPost):
            message = await self._send_twitter_post_to_channel(
                channel,
                item,
                role_id=role_id,
                batch_tasks=image_batches_by_post_id.get(item.post_id),
            )
            self.storage.mark_hampang_target_seen(target.guild_id, x_post_id=item.post_id)
            LOGGER.info(
                "새 햄햄팡팡 X 소식 공지 | %s | %s | 제목=%s",
                guild_log,
                channel_log,
                item.title,
            )
            return message
        if source == HAMPANG_SOURCE_YOUTUBE and isinstance(item, YoutubeUpload):
            message = await self._send_hampang_youtube_upload_to_channel(
                channel,
                item,
                role_id=role_id,
            )
            self.storage.mark_hampang_target_seen(
                target.guild_id,
                youtube_video_id=item.video_id,
            )
            LOGGER.info(
                "새 햄햄팡팡 YouTube 소식 공지 | %s | %s | 제목=%s",
                guild_log,
                channel_log,
                item.title,
            )
            return message
        return None

    @staticmethod
    def _sum_successful_results(results: list[int | BaseException], message: str) -> int:
        announced = 0
        for result in results:
            if isinstance(result, Exception):
                LOGGER.error(message, exc_info=(type(result), result, result.__traceback__))
                continue
            announced += result
        return announced

    def _finish_hampang_recovery_baselines(
        self,
        context: HampangPollContext,
        target_count: int,
    ) -> None:
        if context.x_baseline_only:
            self._hampang_x_recovery_baseline_pending = False
            LOGGER.info(
                "네트워크 복구 후 햄햄팡팡 X 기준선을 갱신했습니다. 누적 소식 공지는 건너뜁니다 "
                "(post_id=%s, targets=%s).",
                context.latest_x_post_id,
                target_count,
            )
        if context.youtube_baseline_only:
            self._hampang_youtube_recovery_baseline_pending = False
            LOGGER.info(
                "네트워크 복구 후 햄햄팡팡 YouTube 기준선을 갱신했습니다. 누적 소식 공지는 건너뜁니다 "
                "(video_id=%s, targets=%s).",
                context.latest_youtube_video_id,
                target_count,
            )

    async def _poll_hampang_news_once(self) -> int:
        targets = self.storage.list_hampang_targets()
        if not targets:
            return 0

        poll_started_at = datetime.now(timezone.utc)
        window_started_at = self._current_twitter_tracking_window_started_at(poll_started_at)
        (
            x_posts,
            youtube_uploads,
            x_failed,
            youtube_failed,
        ) = await self._fetch_hampang_news_sources_detailed()
        if x_failed:
            self._hampang_x_recovery_baseline_pending = True
        if youtube_failed:
            self._hampang_youtube_recovery_baseline_pending = True
        if not x_posts and not youtube_uploads:
            return 0

        context = self._hampang_poll_context(
            x_posts,
            youtube_uploads,
            window_started_at,
        )
        send_semaphore = asyncio.Semaphore(NEWS_TARGET_SEND_CONCURRENCY)

        async def process_target(target: GuildHampangTarget) -> int:
            if not target.enabled:
                return 0
            plan = self._hampang_target_plan(target, context)
            self._mark_hampang_plan_baseline(target, plan)
            return await self._send_hampang_target_plan(target, plan, send_semaphore)

        results = await asyncio.gather(
            *(asyncio.create_task(process_target(target)) for target in targets),
            return_exceptions=True,
        )
        announced = self._sum_successful_results(
            results,
            "햄햄팡팡 소식 자동 전송 대상 처리 실패.",
        )
        self._finish_hampang_recovery_baselines(context, len(targets))
        return announced

    async def _refresh_steam_cache_for_twitter_links(
        self,
        twitter_posts: list[TwitterPost],
    ) -> None:
        if self.news_source is None:
            return
        post_keys = _steam_news_post_ids_for_twitter_posts(twitter_posts)
        if not post_keys:
            return
        missing_keys = [
            post_key
            for post_key in post_keys
            if not any(
                self.storage.get_post(f"steam:{language}:{post_key}") is not None
                for language in SYNC_LANGUAGES
            )
        ]
        if not missing_keys:
            return
        try:
            await self._sync_global_news_cache()
        except Exception:
            LOGGER.exception(
                "X Steam 링크 중복 확인용 Steam 뉴스 갱신 실패: post_keys=%s",
                ", ".join(missing_keys),
            )

    def _steam_posts_by_twitter_post_id(
        self,
        twitter_posts: list[TwitterPost],
    ) -> dict[str, list[NewsPost]]:
        steam_posts = _dedupe_posts_by_id([
            *self._cached_steam_posts_for_twitter_links(twitter_posts),
            *(
                post
                for language in SYNC_LANGUAGES
                for post in self.storage.search_posts("", limit=NEWS_POST_LIMIT, language=language)
            ),
        ])
        if not steam_posts:
            return {}
        return {
            post.post_id: _matching_steam_posts_for_twitter(post, steam_posts)
            for post in twitter_posts
        }

    def _new_twitter_posts_for_target(
        self,
        target: GuildTwitterTarget,
        posts_newest_first: list[TwitterPost],
    ) -> list[TwitterPost]:
        if target.last_seen_post_id is None:
            return []
        ids = [post.post_id for post in posts_newest_first]
        if target.last_seen_post_id in ids:
            index = ids.index(target.last_seen_post_id)
            return list(reversed(posts_newest_first[:index]))

        last_seen_post = self.storage.get_twitter_post(target.last_seen_post_id)
        if last_seen_post and last_seen_post.created_at:
            return [
                post
                for post in reversed(posts_newest_first)
                if post.created_at and post.created_at > last_seen_post.created_at
            ]
        return []

    async def _process_chzzk_offline_targets(
        self,
        targets: list[GuildChzzkTarget],
    ) -> int:
        live_detail = await self.chzzk_client.fetch_live_detail()
        ended = 0
        for target in targets:
            if not target.enabled or not target.is_live:
                continue
            if not _is_chzzk_live_recently_closed(live_detail, target.last_live_id):
                self._skip_chzzk_live_end_notice(target)
                continue
            ended += await self._send_chzzk_live_end_notice_to_target(target)
        return ended

    def _skip_chzzk_live_end_notice(self, target: GuildChzzkTarget) -> None:
        self.storage.mark_chzzk_target_offline(target.guild_id)
        LOGGER.info(
            "치지직 라이브 종료 공지 건너뜀: 종료 후 10분 이상 경과 또는 종료 시간 확인 불가 (guild %s, live_id=%s).",
            target.guild_id,
            target.last_live_id,
        )

    async def _send_chzzk_live_end_notice_to_target(
        self,
        target: GuildChzzkTarget,
    ) -> int:
        channel = await self._resolve_chzzk_target_channel(target)
        if channel is None:
            return 0
        guild_log, channel_log = self._destination_logs(
            target.guild_id,
            channel,
            target.channel_id,
        )
        try:
            message = await self._send_chzzk_live_end_to_channel(channel)
        except discord.HTTPException:
            LOGGER.exception(
                "치지직 라이브 종료 자동 전송 실패 (guild_id=%s, channel_id=%s, live_id=%s).",
                target.guild_id,
                target.channel_id,
                target.last_live_id,
            )
            return 0
        self.storage.mark_chzzk_target_offline(target.guild_id)
        await self._track_manual_message(target.guild_id, target.channel_id, message)
        LOGGER.info(
            "치지직 라이브 종료 공지 | %s | %s | live_id=%s.",
            guild_log,
            channel_log,
            target.last_live_id,
        )
        return 1

    def _mark_chzzk_recovery_baseline(
        self,
        targets: list[GuildChzzkTarget],
        live: ChzzkLive,
    ) -> None:
        updated = 0
        for target in targets:
            if not target.enabled:
                continue
            if str(target.last_live_id) != live.live_id or not target.is_live:
                self.storage.mark_chzzk_target_seen(target.guild_id, live.live_id)
                updated += 1
        LOGGER.info(
            "네트워크 복구 후 치지직 라이브 기준선을 갱신했습니다. 누적 라이브 공지는 건너뜁니다 "
            "(live_id=%s, targets=%s).",
            live.live_id,
            updated,
        )

    def _should_skip_chzzk_live_target(
        self,
        target: GuildChzzkTarget,
        live: ChzzkLive,
    ) -> bool:
        if not target.enabled:
            return True
        if str(target.last_live_id) == live.live_id:
            if not target.is_live:
                self.storage.mark_chzzk_target_seen(target.guild_id, live.live_id)
            return True
        if _is_chzzk_live_too_old(live):
            self.storage.mark_chzzk_target_seen(target.guild_id, live.live_id)
            LOGGER.info(
                "치지직 라이브 공지 건너뜀: 시작 후 10분 이상 경과 (guild %s, live_id=%s, title=%r).",
                target.guild_id,
                live.live_id,
                live.title,
            )
            return True
        return False

    async def _send_chzzk_live_to_target(
        self,
        target: GuildChzzkTarget,
        live: ChzzkLive,
    ) -> int:
        channel = await self._resolve_chzzk_target_channel(target)
        if channel is None:
            return 0
        guild_log, channel_log = self._destination_logs(
            target.guild_id,
            channel,
            target.channel_id,
        )
        try:
            settings = self.storage.get_settings(target.guild_id)
            youtube_target = self.storage.get_youtube_target(target.guild_id)
            message = await self._send_chzzk_live_to_channel(
                channel,
                live,
                role_id=settings.role_id,
                include_youtube_button=not (
                    youtube_target is not None and youtube_target.enabled
                ),
            )
        except discord.HTTPException:
            LOGGER.exception(
                "치지직 라이브 자동 전송 실패 (guild_id=%s, channel_id=%s, live_id=%s).",
                target.guild_id,
                target.channel_id,
                live.live_id,
            )
            return 0

        self.storage.mark_chzzk_target_seen(target.guild_id, live.live_id)
        await self._track_manual_message(target.guild_id, target.channel_id, message)
        LOGGER.info(
            "새 치지직 라이브 공지 | %s | %s | 제목=%s",
            guild_log,
            channel_log,
            live.title,
        )
        return 1

    async def _send_chzzk_live_updates(
        self,
        targets: list[GuildChzzkTarget],
        live: ChzzkLive,
    ) -> int:
        announced = 0
        for target in targets:
            if self._should_skip_chzzk_live_target(target, live):
                continue
            announced += await self._send_chzzk_live_to_target(target, live)
        return announced

    async def _poll_chzzk_once(self) -> int:
        targets = self.storage.list_chzzk_targets()
        if not targets:
            return 0

        live = await self.chzzk_client.fetch_live()
        if live is None:
            return await self._process_chzzk_offline_targets(targets)

        if self._chzzk_recovery_baseline_pending:
            self._mark_chzzk_recovery_baseline(targets, live)
            return 0

        return await self._send_chzzk_live_updates(targets, live)

    def _mark_youtube_targets_offline(
        self,
        targets: list[GuildYoutubeTarget],
    ) -> None:
        for target in targets:
            if target.enabled and target.is_live:
                self.storage.mark_youtube_target_offline(target.guild_id)

    def _mark_youtube_recovery_baseline(
        self,
        targets: list[GuildYoutubeTarget],
        live: YoutubeLive,
    ) -> None:
        updated = 0
        for target in targets:
            if not target.enabled:
                continue
            if str(target.last_live_id) != live.video_id or not target.is_live:
                self.storage.mark_youtube_target_seen(target.guild_id, live.video_id)
                updated += 1
        LOGGER.info(
            "네트워크 복구 후 유튜브 라이브 기준선을 갱신했습니다. 누적 라이브 공지는 건너뜁니다 "
            "(video_id=%s, targets=%s).",
            live.video_id,
            updated,
        )

    def _should_skip_youtube_live_target(
        self,
        target: GuildYoutubeTarget,
        live: YoutubeLive,
    ) -> bool:
        if not target.enabled:
            return True
        if str(target.last_live_id) == live.video_id:
            if not target.is_live:
                self.storage.mark_youtube_target_seen(target.guild_id, live.video_id)
            return True
        if _is_youtube_live_too_old(live):
            self.storage.mark_youtube_target_seen(target.guild_id, live.video_id)
            LOGGER.info(
                "유튜브 라이브 공지 건너뜀: 시작 후 10분 이상 경과 (guild %s, video_id=%s, title=%r).",
                target.guild_id,
                live.video_id,
                live.title,
            )
            return True
        return False

    async def _send_youtube_live_to_target(
        self,
        target: GuildYoutubeTarget,
        live: YoutubeLive,
    ) -> int:
        channel = await self._resolve_youtube_target_channel(target)
        if channel is None:
            return 0
        guild_log, channel_log = self._destination_logs(
            target.guild_id,
            channel,
            target.channel_id,
        )
        try:
            settings = self.storage.get_settings(target.guild_id)
            chzzk_target = self.storage.get_chzzk_target(target.guild_id)
            message = await self._send_youtube_live_to_channel(
                channel,
                live,
                role_id=settings.role_id,
                include_chzzk_button=not (
                    chzzk_target is not None and chzzk_target.enabled
                ),
            )
        except discord.HTTPException:
            LOGGER.exception(
                "유튜브 라이브 자동 전송 실패 (guild_id=%s, channel_id=%s, video_id=%s).",
                target.guild_id,
                target.channel_id,
                live.video_id,
            )
            return 0

        self.storage.mark_youtube_target_seen(target.guild_id, live.video_id)
        await self._track_manual_message(target.guild_id, target.channel_id, message)
        LOGGER.info(
            "새 유튜브 라이브 공지 | %s | %s | 제목=%s",
            guild_log,
            channel_log,
            live.title,
        )
        return 1

    async def _send_youtube_live_updates(
        self,
        targets: list[GuildYoutubeTarget],
        live: YoutubeLive,
    ) -> int:
        announced = 0
        for target in targets:
            if self._should_skip_youtube_live_target(target, live):
                continue
            announced += await self._send_youtube_live_to_target(target, live)
        return announced

    async def _poll_youtube_once(self) -> int:
        targets = self.storage.list_youtube_targets()
        if not targets:
            return 0

        live = await self.youtube_client.fetch_live()
        if live is None:
            self._mark_youtube_targets_offline(targets)
            return 0

        if self._youtube_recovery_baseline_pending:
            self._mark_youtube_recovery_baseline(targets, live)
            return 0

        return await self._send_youtube_live_updates(targets, live)

    def _mark_youtube_upload_recovery_baseline(
        self,
        targets: list[GuildYoutubeUploadTarget],
        latest_video_id: str,
    ) -> None:
        for target in targets:
            self.storage.mark_youtube_upload_target_seen(target.guild_id, latest_video_id)
        LOGGER.info(
            "네트워크 복구 후 유튜브 업로드 기준선을 갱신했습니다. 누적 영상 공지는 건너뜁니다 "
            "(video_id=%s, targets=%s).",
            latest_video_id,
            len(targets),
        )

    def _new_youtube_uploads_for_target(
        self,
        target: GuildYoutubeUploadTarget,
        uploads: list[YoutubeUpload],
        upload_ids: list[str],
        latest_video_id: str,
    ) -> list[YoutubeUpload]:
        if not target.enabled:
            return []
        if not target.last_video_id or target.last_video_id not in upload_ids:
            self.storage.mark_youtube_upload_target_seen(target.guild_id, latest_video_id)
            LOGGER.info(
                "유튜브 업로드 기준선 설정: 기존 데이터가 없어 누적 영상 공지를 건너뜁니다 "
                "(guild_id=%s, video_id=%s).",
                target.guild_id,
                latest_video_id,
            )
            return []
        last_seen_index = upload_ids.index(target.last_video_id)
        return list(reversed(uploads[:last_seen_index]))

    async def _send_youtube_uploads_to_target(
        self,
        target: GuildYoutubeUploadTarget,
        uploads: list[YoutubeUpload],
    ) -> int:
        if not uploads:
            return 0
        channel = await self._resolve_youtube_upload_target_channel(target)
        if channel is None:
            return 0

        guild_log, channel_log = self._destination_logs(
            target.guild_id,
            channel,
            target.channel_id,
        )
        settings = self.storage.get_settings(target.guild_id)
        announced = 0
        for upload in uploads:
            try:
                message = await self._send_youtube_upload_to_channel(
                    channel,
                    upload,
                    role_id=settings.role_id if announced == 0 else None,
                )
            except discord.HTTPException:
                LOGGER.exception(
                    "유튜브 업로드 자동 전송 실패 "
                    "(guild_id=%s, channel_id=%s, video_id=%s).",
                    target.guild_id,
                    target.channel_id,
                    upload.video_id,
                )
                break

            self.storage.mark_youtube_upload_target_seen(target.guild_id, upload.video_id)
            await self._track_manual_message(target.guild_id, target.channel_id, message)
            LOGGER.info(
                "새 유튜브 업로드 공지 | %s | %s | 제목=%s",
                guild_log,
                channel_log,
                upload.title,
            )
            announced += 1
        return announced

    async def _poll_youtube_uploads_once(self) -> int:
        targets = self.storage.list_youtube_upload_targets()
        if not targets:
            return 0

        uploads = _regular_youtube_uploads(await self.youtube_client.fetch_recent_uploads())
        if not uploads:
            return 0

        latest_video_id = uploads[0].video_id
        if self._youtube_upload_recovery_baseline_pending:
            self._mark_youtube_upload_recovery_baseline(targets, latest_video_id)
            return 0

        announced = 0
        upload_ids = [upload.video_id for upload in uploads]
        for target in targets:
            new_uploads = self._new_youtube_uploads_for_target(
                target,
                uploads,
                upload_ids,
                latest_video_id,
            )
            announced += await self._send_youtube_uploads_to_target(target, new_uploads)
        return announced

    async def _fetch_hampang_news_sources(
        self,
    ) -> tuple[list[TwitterPost], list[YoutubeUpload], bool]:
        x_posts, youtube_uploads, x_failed, youtube_failed = (
            await self._fetch_hampang_news_sources_detailed()
        )
        return x_posts, youtube_uploads, x_failed or youtube_failed

    async def _fetch_hampang_news_sources_detailed(
        self,
    ) -> tuple[list[TwitterPost], list[YoutubeUpload], bool, bool]:
        x_result, youtube_result = await asyncio.gather(
            self.hampang_x_source.fetch_recent_posts(limit=TWITTER_POST_LIMIT),
            self.youtube_client.fetch_recent_uploads(),
            return_exceptions=True,
        )
        x_failed = False
        youtube_failed = False
        x_posts: list[TwitterPost] = []
        youtube_uploads: list[YoutubeUpload] = []

        if isinstance(x_result, Exception):
            x_failed = True
            if _is_internet_exception(x_result):
                _log_internet_exception("햄햄팡팡 공식 X 확인 실패", x_result)
            else:
                LOGGER.warning("햄햄팡팡 공식 X 확인 실패: %s", x_result)
        else:
            x_posts = _sort_twitter_posts_newest_first(x_result)
            x_failed = self.hampang_x_source.last_fetch_had_upstream_failure and not x_posts

        if isinstance(youtube_result, Exception):
            youtube_failed = True
            if _is_internet_exception(youtube_result):
                _log_internet_exception("햄햄팡팡 YouTube 영상 확인 실패", youtube_result)
            else:
                LOGGER.warning("햄햄팡팡 YouTube 영상 확인 실패: %s", youtube_result)
        else:
            youtube_uploads = _sort_youtube_uploads_newest_first(
                [
                    upload for upload in youtube_result
                    if _is_hampang_youtube_upload(upload)
                ]
            )
        return x_posts, youtube_uploads, x_failed, youtube_failed

    async def _resolve_chzzk_target_channel(
        self, target: GuildChzzkTarget
    ) -> discord.abc.Messageable | None:
        channel = self.bot.get_channel(target.channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(target.channel_id)
            except (discord.Forbidden, discord.NotFound):
                LOGGER.warning(
                    "치지직 라이브 자동 전송 건너뜀: 채널 접근 불가 (guild_id=%s, channel_id=%s).",
                    target.guild_id,
                    target.channel_id,
                )
                return None
            except discord.HTTPException:
                LOGGER.exception(
                    "치지직 라이브 자동 전송 채널 조회 실패 (guild_id=%s, channel_id=%s).",
                    target.guild_id,
                    target.channel_id,
                )
                return None
        return channel if isinstance(channel, discord.abc.Messageable) else None

    async def _resolve_youtube_upload_target_channel(
        self, target: GuildYoutubeUploadTarget
    ) -> discord.abc.Messageable | None:
        channel = self.bot.get_channel(target.channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(target.channel_id)
            except (discord.Forbidden, discord.NotFound):
                LOGGER.warning(
                    "유튜브 업로드 자동 전송 건너뜀: 채널 접근 불가 "
                    "(guild_id=%s, channel_id=%s).",
                    target.guild_id,
                    target.channel_id,
                )
                return None
            except discord.HTTPException:
                LOGGER.exception(
                    "유튜브 업로드 자동 전송 채널 조회 실패 "
                    "(guild_id=%s, channel_id=%s).",
                    target.guild_id,
                    target.channel_id,
                )
                return None
        return channel if isinstance(channel, discord.abc.Messageable) else None

    async def _resolve_youtube_target_channel(
        self, target: GuildYoutubeTarget
    ) -> discord.abc.Messageable | None:
        channel = self.bot.get_channel(target.channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(target.channel_id)
            except (discord.Forbidden, discord.NotFound):
                LOGGER.warning(
                    "유튜브 라이브 자동 전송 건너뜀: 채널 접근 불가 (guild_id=%s, channel_id=%s).",
                    target.guild_id,
                    target.channel_id,
                )
                return None
            except discord.HTTPException:
                LOGGER.exception(
                    "유튜브 라이브 자동 전송 채널 조회 실패 (guild_id=%s, channel_id=%s).",
                    target.guild_id,
                    target.channel_id,
                )
                return None
        return channel if isinstance(channel, discord.abc.Messageable) else None

    async def _send_chzzk_live_to_channel(
        self,
        channel: discord.abc.Messageable,
        live: ChzzkLive,
        *,
        role_id: int | None = None,
        youtube_url: str | None = None,
        include_youtube_button: bool = True,
    ) -> discord.Message:
        embed = _embed_for_chzzk_live(live)
        if include_youtube_button:
            youtube_url = youtube_url or await self._youtube_latest_live_url()
        view = _chzzk_live_view(youtube_url, include_youtube=include_youtube_button)
        if role_id:
            await channel.send(
                content=f"<@&{role_id}>",
                allowed_mentions=discord.AllowedMentions(
                    everyone=False,
                    users=False,
                    roles=[discord.Object(id=role_id)],
                ),
            )
        return await channel.send(
            embed=embed,
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _send_chzzk_live_end_to_channel(
        self,
        channel: discord.abc.Messageable,
    ) -> discord.Message:
        youtube_url = await self._youtube_latest_live_url()
        return await channel.send(
            embed=_embed_for_chzzk_live_end(),
            view=_chzzk_live_view(youtube_url),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _send_youtube_live_to_channel(
        self,
        channel: discord.abc.Messageable,
        live: YoutubeLive,
        *,
        role_id: int | None = None,
        include_chzzk_button: bool = False,
    ) -> discord.Message:
        if role_id:
            await channel.send(
                content=f"<@&{role_id}>",
                allowed_mentions=discord.AllowedMentions(
                    everyone=False,
                    users=False,
                    roles=[discord.Object(id=role_id)],
                ),
            )
        return await channel.send(
            embed=_embed_for_youtube_live(live),
            view=_youtube_live_view(live.url, include_chzzk=include_chzzk_button),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _send_youtube_upload_to_channel(
        self,
        channel: discord.abc.Messageable,
        upload: YoutubeUpload,
        *,
        role_id: int | None = None,
    ) -> discord.Message:
        if role_id:
            await channel.send(
                content=f"<@&{role_id}>",
                allowed_mentions=discord.AllowedMentions(
                    everyone=False,
                    users=False,
                    roles=[discord.Object(id=role_id)],
                ),
            )
        return await channel.send(
            embed=_embed_for_youtube_upload(upload),
            view=_youtube_upload_view(upload.url),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _send_hampang_youtube_upload_to_channel(
        self,
        channel: discord.abc.Messageable,
        upload: YoutubeUpload,
        *,
        role_id: int | None = None,
    ) -> discord.Message:
        if role_id:
            await channel.send(
                content=f"<@&{role_id}>",
                allowed_mentions=discord.AllowedMentions(
                    everyone=False,
                    users=False,
                    roles=[discord.Object(id=role_id)],
                ),
            )
        return await channel.send(
            embed=_embed_for_hampang_youtube_upload(upload),
            view=_youtube_upload_view(upload.url),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _resolve_twitter_target_channel(
        self, target: GuildTwitterTarget
    ) -> discord.abc.Messageable | None:
        channel = self.bot.get_channel(target.channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(target.channel_id)
            except (discord.Forbidden, discord.NotFound):
                LOGGER.warning(
                    "X 게시물 자동 전송 건너뜀: 채널 접근 불가 (guild_id=%s, channel_id=%s).",
                    target.guild_id,
                    target.channel_id,
                )
                return None
            except discord.HTTPException:
                LOGGER.exception(
                    "X 게시물 자동 전송 채널 조회 실패 (guild_id=%s, channel_id=%s).",
                    target.guild_id,
                    target.channel_id,
                )
                return None
        return channel if isinstance(channel, discord.abc.Messageable) else None

    async def _resolve_hampang_target_channel(
        self, target: GuildHampangTarget
    ) -> discord.abc.Messageable | None:
        channel = self.bot.get_channel(target.channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(target.channel_id)
            except (discord.Forbidden, discord.NotFound):
                LOGGER.warning(
                    "햄햄팡팡 소식 자동 전송 건너뜀: 채널 접근 불가 "
                    "(guild_id=%s, channel_id=%s).",
                    target.guild_id,
                    target.channel_id,
                )
                return None
            except discord.HTTPException:
                LOGGER.exception(
                    "햄햄팡팡 소식 자동 전송 채널 조회 실패 "
                    "(guild_id=%s, channel_id=%s).",
                    target.guild_id,
                    target.channel_id,
                )
                return None
        return channel if isinstance(channel, discord.abc.Messageable) else None

    def _twitter_image_batch_tasks(
        self,
        image_urls: list[str],
        batch_tasks: list[asyncio.Task[list[discord.File]]] | None,
    ) -> list[asyncio.Task[list[discord.File]]]:
        if batch_tasks is not None:
            return batch_tasks
        if len(image_urls) > MAX_TWITTER_EMBED_IMAGES:
            return self._start_image_batch_tasks(image_urls[MAX_TWITTER_EMBED_IMAGES:])
        return []

    def _twitter_post_followup_tasks(
        self,
        channel: discord.abc.Messageable,
        post: TwitterPost,
        image_batch_tasks: list[asyncio.Task[list[discord.File]]],
    ) -> list[asyncio.Task]:
        followup_tasks: list[asyncio.Task] = []
        if image_batch_tasks:
            followup_tasks.append(
                asyncio.create_task(
                    self._send_twitter_channel_image_messages(
                        channel,
                        batch_tasks=image_batch_tasks,
                    )
                )
            )

        link_urls = _twitter_link_urls(post)
        if link_urls:
            followup_tasks.append(
                asyncio.create_task(
                    channel.send(
                        content="\n".join(link_urls),
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                )
            )

        video_url_groups = _twitter_video_url_groups(post)
        video_fallback_url = _twitter_video_fallback_url(post)
        if video_url_groups:
            followup_tasks.append(
                asyncio.create_task(
                    self._send_twitter_video_to_channel(
                        channel,
                        video_url_groups,
                        video_fallback_url or post.url,
                    )
                )
            )
        elif video_fallback_url:
            followup_tasks.append(
                asyncio.create_task(
                    channel.send(
                        content=video_fallback_url,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                )
            )
        return followup_tasks

    async def _run_twitter_post_followups(
        self,
        post: TwitterPost,
        followup_tasks: list[asyncio.Task],
    ) -> None:
        if not followup_tasks:
            return
        results = await asyncio.gather(*followup_tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                LOGGER.error(
                    "X 게시물 후속 메시지 전송 실패 (post_id=%s, title=%r).",
                    post.post_id,
                    post.title,
                    exc_info=(type(result), result, result.__traceback__),
                )

    async def _send_twitter_post_to_channel(
        self,
        channel: discord.abc.Messageable,
        post: TwitterPost,
        *,
        attach_photos: bool = True,
        role_id: int | None = None,
        batch_tasks: list[asyncio.Task[list[discord.File]]] | None = None,
    ) -> discord.Message:
        image_urls = _twitter_image_urls(post) if attach_photos else []
        embeds = _embeds_for_twitter_post(post, image_urls=image_urls)
        image_batch_tasks = self._twitter_image_batch_tasks(image_urls, batch_tasks)
        if role_id:
            await channel.send(
                content=f"<@&{role_id}>",
                allowed_mentions=discord.AllowedMentions(
                    everyone=False,
                    users=False,
                    roles=[discord.Object(id=role_id)],
                ),
            )
        message = await channel.send(
            embeds=embeds,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        followup_tasks = self._twitter_post_followup_tasks(channel, post, image_batch_tasks)
        await self._run_twitter_post_followups(post, followup_tasks)
        return message

    async def _send_twitter_post_followups(
        self,
        interaction: discord.Interaction,
        post: TwitterPost,
        *,
        private: bool,
        attach_photos: bool = True,
    ) -> list[discord.Message | None]:
        sent_messages: list[discord.Message | None] = []
        image_urls = _twitter_image_urls(post) if attach_photos else []
        embeds = _embeds_for_twitter_post(post, image_urls=image_urls)
        sent_messages.append(
            await interaction.followup.send(
                embeds=embeds,
                ephemeral=private,
                allowed_mentions=discord.AllowedMentions.none(),
                wait=True,
            )
        )
        if len(image_urls) > MAX_TWITTER_EMBED_IMAGES:
            batch_tasks = self._start_image_batch_tasks(image_urls[MAX_TWITTER_EMBED_IMAGES:])
            task = asyncio.create_task(
                self._send_twitter_interaction_image_followups(
                    interaction,
                    private=private,
                    batch_tasks=batch_tasks,
                )
            )
            task.add_done_callback(self._log_background_task_result)
        link_urls = _twitter_link_urls(post)
        if link_urls:
            sent_messages.append(
                await interaction.followup.send(
                    content="\n".join(link_urls),
                    ephemeral=private,
                    allowed_mentions=discord.AllowedMentions.none(),
                    wait=True,
                )
            )
        video_url_groups = _twitter_video_url_groups(post)
        video_fallback_url = _twitter_video_fallback_url(post)
        if video_url_groups:
            task = asyncio.create_task(
                self._send_twitter_video_followups(
                    interaction, private, video_url_groups, video_fallback_url or post.url
                )
            )
            task.add_done_callback(self._log_background_task_result)
        elif video_fallback_url:
            sent_messages.append(
                await interaction.followup.send(
                    content=video_fallback_url,
                    ephemeral=private,
                    allowed_mentions=discord.AllowedMentions.none(),
                    wait=True,
                )
            )
        return sent_messages

    def _schedule_twitter_channel_image_messages(
        self,
        channel: discord.abc.Messageable,
        post: TwitterPost,
        *,
        batch_tasks: list[asyncio.Task[list[discord.File]]],
    ) -> None:
        if not batch_tasks:
            return
        task = asyncio.create_task(
            self._send_twitter_channel_image_messages(channel, batch_tasks=batch_tasks)
        )
        task.add_done_callback(self._log_background_task_result)

    async def _send_twitter_channel_image_messages(
        self,
        channel: discord.abc.Messageable,
        *,
        batch_tasks: list[asyncio.Task[list[discord.File]]],
    ) -> None:
        for batch_task in batch_tasks:
            file_batch = await batch_task
            if not file_batch:
                continue
            await channel.send(
                files=file_batch,
                allowed_mentions=discord.AllowedMentions.none(),
            )

    async def _send_twitter_interaction_image_followups(
        self,
        interaction: discord.Interaction,
        *,
        private: bool,
        batch_tasks: list[asyncio.Task[list[discord.File]]],
    ) -> None:
        for batch_task in batch_tasks:
            file_batch = await batch_task
            if not file_batch:
                continue
            await interaction.followup.send(
                files=file_batch,
                ephemeral=private,
                allowed_mentions=discord.AllowedMentions.none(),
                wait=True,
            )

    async def _send_twitter_video_to_channel(
        self,
        channel: discord.abc.Messageable,
        video_url_groups: list[list[str]],
        fallback_url: str,
    ) -> None:
        for urls in video_url_groups:
            best_url = _select_twitter_video_url(urls) or fallback_url
            file = await self._download_twitter_video(best_url)
            if file:
                try:
                    await channel.send(
                        files=[file],
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                    continue
                except discord.HTTPException as exc:
                    if not _is_payload_too_large(exc):
                        raise
                    LOGGER.warning("Discord 업로드 제한으로 X 1080p 영상을 링크로 보냅니다.")
            else:
                LOGGER.warning("X 1080p 영상을 첨부할 수 없어 링크로 보냅니다: %s", best_url)
            if not best_url:
                continue
            await channel.send(
                content=best_url,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        if not video_url_groups:
            await channel.send(
                content=fallback_url,
                allowed_mentions=discord.AllowedMentions.none(),
            )

    async def _send_twitter_video_followups(
        self,
        interaction: discord.Interaction,
        private: bool,
        video_url_groups: list[list[str]],
        fallback_url: str,
    ) -> None:
        for urls in video_url_groups:
            best_url = _select_twitter_video_url(urls) or fallback_url
            file = await self._download_twitter_video(best_url)
            if file:
                try:
                    await interaction.followup.send(
                        files=[file],
                        ephemeral=private,
                        allowed_mentions=discord.AllowedMentions.none(),
                        wait=True,
                    )
                    continue
                except discord.HTTPException as exc:
                    if not _is_payload_too_large(exc):
                        raise
                    LOGGER.warning("Discord 업로드 제한으로 X 1080p 영상을 링크로 보냅니다.")
            else:
                LOGGER.warning("X 1080p 영상을 첨부할 수 없어 링크로 보냅니다: %s", best_url)
            if not best_url:
                continue
            await interaction.followup.send(
                content=best_url,
                ephemeral=private,
                allowed_mentions=discord.AllowedMentions.none(),
                wait=True,
            )
        if not video_url_groups:
            await interaction.followup.send(
                content=fallback_url,
                ephemeral=private,
                allowed_mentions=discord.AllowedMentions.none(),
                wait=True,
            )

    @app_commands.command(name="서버설정", description="서버의 공통 봇 설정을 변경합니다.")
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.rename(
        role="역할",
        enabled="자동알림",
        language="언어",
        auto_cleanup="자동삭제",
        cleanup_days="자동삭제일수",
        public_news_send="공개소식전송",
        notification_banner="알림배너",
        news_source="뉴스소스",
        image_delivery="이미지전송",
    )
    @app_commands.describe(
        role="새 소식과 함께 핑할 역할입니다.",
        enabled="새 게시물 자동 알림을 켜거나 끕니다.",
        language="서버에서 기본으로 사용할 소식 언어입니다.",
        auto_cleanup="조회한 소식 메시지를 일정 시간 뒤 자동으로 지울지 여부입니다.",
        cleanup_days="자동 삭제까지의 유예 기간(일)입니다. 1~7 사이로 입력합니다.",
        public_news_send="관리자가 아닌 다른 유저가 /이전소식보기 이나 /최근소식보기 으로 서버 채널에 공개 소식을 보낼 수 있는지 설정합니다.",
        notification_banner="자동 알림과 서버에서 보내는 소식에 사용할 배너입니다. 이미지 이름 또는 사용 안 함을 고릅니다.",
        news_source="자동 알림과 소식 명령어에서 사용할 소식 출처입니다.",
        image_delivery="소식 이미지를 임베드 안에 표시할지, 별도 첨부파일 메시지로 보낼지 정합니다.",
    )
    @app_commands.choices(
        language=LANGUAGE_CHOICES,
        public_news_send=BOOLEAN_CHOICES,
        enabled=BOOLEAN_CHOICES,
        auto_cleanup=BOOLEAN_CHOICES,
        news_source=NEWS_SOURCE_CHOICES,
        image_delivery=IMAGE_DELIVERY_CHOICES,
    )
    async def configure(
        self,
        interaction: discord.Interaction,
        role: discord.Role | None = None,
        enabled: app_commands.Choice[str] | None = None,
        language: app_commands.Choice[str] | None = None,
        auto_cleanup: app_commands.Choice[str] | None = None,
        cleanup_days: int | None = None,
        public_news_send: app_commands.Choice[str] | None = None,
        notification_banner: str | None = None,
        news_source: app_commands.Choice[str] | None = None,
        image_delivery: app_commands.Choice[str] | None = None,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("서버 안에서만 설정할 수 있어요.", ephemeral=True)
            return

        if cleanup_days is not None and not MIN_CLEANUP_DAYS <= cleanup_days <= MAX_CLEANUP_DAYS:
            await interaction.response.send_message(
                f"자동 삭제 일수는 {MIN_CLEANUP_DAYS}부터 {MAX_CLEANUP_DAYS} 사이로 입력해주세요.",
                ephemeral=True,
            )
            return

        banner_filename = None
        if notification_banner is not None:
            banner_filename = _resolve_banner_filename(notification_banner)
            if banner_filename is None:
                await interaction.response.send_message(
                    "선택한 알림 배너를 찾지 못했어요. `img` 폴더의 배너 이미지 이름이나 `사용 안 함`을 다시 골라주세요.",
                    ephemeral=True,
                )
                return

        settings = self.storage.update_settings(
            interaction.guild_id,
            role_id=role.id if role else None,
            post_format=POST_FORMAT_RICH,
            enabled=_choice_bool(enabled),
            language=language.value if language else None,
            auto_cleanup_enabled=_choice_bool(auto_cleanup),
            auto_cleanup_days=cleanup_days,
            public_news_lookup_allowed=_choice_bool(public_news_send),
            notification_banner=banner_filename,
            news_source_mode=news_source.value if news_source else None,
            image_delivery=image_delivery.value if image_delivery else None,
        )
        role_text = f"<@&{settings.role_id}>" if settings.role_id else "없음"
        enabled_text = "켜짐" if settings.enabled else "꺼짐"
        language_text = _language_label(settings.language)
        cleanup_text = "켜짐" if settings.auto_cleanup_enabled else "꺼짐"
        public_news_send_text = _bool_label(settings.public_news_lookup_allowed)
        banner_text = _banner_display_name(settings.notification_banner)
        source_text = _news_source_mode_label(settings.news_source_mode)
        image_delivery_text = _image_delivery_label(settings.image_delivery)
        embed = discord.Embed(
            title="설정이 완료되었어요~!",
            description=(
                f"역할 핑: {role_text}\n"
                f"새 게시물 자동 알림: {enabled_text}\n"
                f"기본 언어: {language_text}\n"
                f"조회 메시지 자동 삭제: {cleanup_text}\n"
                f"자동 삭제 유예: {settings.auto_cleanup_days}일\n"
                f"공개 소식 전송: {public_news_send_text}\n"
                f"알림 배너: {banner_text}\n"
                f"뉴스 소스: {source_text}\n"
                f"이미지 전송: {image_delivery_text}"
            ),
            color=_success_embed_color(),
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @configure.autocomplete("notification_banner")
    async def configure_banner_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return _banner_autocomplete_choices(current)

    @app_commands.command(name="소식채널설정", description="언어별 자동 소식 채널을 설정합니다.")
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.rename(language="언어", channel="채널")
    @app_commands.describe(
        language="이 채널로 보낼 소식 언어입니다.",
        channel="소식을 보낼 채널입니다.",
    )
    @app_commands.choices(language=LANGUAGE_CHOICES)
    async def configure_news_channel(
        self,
        interaction: discord.Interaction,
        language: app_commands.Choice[str],
        channel: discord.TextChannel,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("서버 안에서만 설정할 수 있어요.", ephemeral=True)
            return

        previous_target = self.storage.get_news_target_by_channel(
            interaction.guild_id,
            channel_id=channel.id,
        )
        self.storage.upsert_news_target(
            interaction.guild_id,
            channel_id=channel.id,
            language=language.value,
        )
        targets = self.storage.list_news_targets(interaction.guild_id)
        if previous_target is None:
            result_text = f"{_language_label(language.value)} 소식을 {channel.mention}에 보낼게요."
        elif previous_target.language == language.value:
            result_text = f"{channel.mention}은 이미 {_language_label(language.value)} 소식 채널로 설정되어 있어요."
        else:
            result_text = (
                f"{channel.mention}의 소식 언어를 "
                f"{_language_label(previous_target.language)}에서 {_language_label(language.value)}로 바꿨어요."
            )
        embed = discord.Embed(
            title="소식 채널 설정이 완료되었어요",
            description=(
                f"{result_text}\n\n"
                f"언어별 소식 채널\n{_format_news_targets(targets)}"
            ),
            color=_success_embed_color(),
        )
        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="소식채널해제", description="언어별 자동 소식 채널 등록을 해제합니다.")
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.rename(target="대상")
    @app_commands.describe(target="해제할 소식 채널입니다. 현재 설정된 채널과 언어 중에서 고릅니다.")
    async def remove_news_channel(
        self,
        interaction: discord.Interaction,
        target: str,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("서버 안에서만 설정할 수 있어요.", ephemeral=True)
            return

        parsed = _parse_news_target_choice(target)
        if parsed is None:
            targets = self.storage.list_news_targets(interaction.guild_id)
            await interaction.response.send_message(
                "해제할 소식 채널을 현재 설정 목록에서 골라주세요.\n\n언어별 소식 채널\n"
                f"{_format_news_targets(targets)}",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        channel_id, language = parsed
        removed = self.storage.delete_news_target(
            interaction.guild_id,
            channel_id=channel_id,
            language=language,
        )
        targets = self.storage.list_news_targets(interaction.guild_id)
        if removed:
            message = f"<#{channel_id}>의 {_language_label(language)} 소식 자동 발송을 해제했어요."
        else:
            message = f"<#{channel_id}>에는 {_language_label(language)} 소식 채널 설정이 없어요."

        embed = discord.Embed(
            title="소식 채널 설정",
            description=f"{message}\n\n언어별 소식 채널\n{_format_news_targets(targets)}",
            color=_success_embed_color(),
        )
        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @remove_news_channel.autocomplete("target")
    async def remove_news_channel_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild_id is None:
            return []
        targets = self.storage.list_news_targets(interaction.guild_id)
        choices: list[app_commands.Choice[str]] = []
        current_lower = current.casefold()
        for target in targets:
            channel = interaction.guild.get_channel(target.channel_id) if interaction.guild else None
            channel_name = f"#{channel.name}" if isinstance(channel, discord.TextChannel) else f"채널 {target.channel_id}"
            label = f"{channel_name} · {_language_label(target.language)}"
            if current_lower and current_lower not in label.casefold():
                continue
            choices.append(
                app_commands.Choice(
                    name=label[:100],
                    value=_news_target_choice_value(target.channel_id, target.language),
                )
            )
            if len(choices) >= 25:
                break
        return choices

    @app_commands.command(name="점검알림설정", description="매주 목요일 10시 점검 시작과 12시 업데이트 알림을 설정합니다.")
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.rename(enabled="허용", channel="채널")
    @app_commands.describe(
        enabled="점검 시작/업데이트 임베드 알림을 켜거나 끕니다.",
        channel="알림을 보낼 채널입니다. 비워두면 현재 채널 또는 /서버설정 채널을 사용합니다.",
    )
    @app_commands.choices(enabled=BOOLEAN_CHOICES)
    async def configure_maintenance_notifications(
        self,
        interaction: discord.Interaction,
        enabled: app_commands.Choice[str],
        channel: discord.TextChannel | None = None,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("서버 안에서만 설정할 수 있어요.", ephemeral=True)
            return

        current = self.storage.get_settings(interaction.guild_id)
        if channel is None and current.channel_id is None:
            channel = (
                interaction.channel
                if isinstance(interaction.channel, discord.TextChannel)
                else None
            )

        settings = self.storage.update_maintenance_notifications(
            interaction.guild_id,
            enabled=_choice_bool(enabled, False),
            channel_id=channel.id if channel else None,
        )
        channel_text = f"<#{settings.channel_id}>" if settings.channel_id else "미설정"
        embed = discord.Embed(
            title="점검 알림 설정이 완료되었어요",
            description=(
                f"점검 알림: {_bool_label(settings.maintenance_notifications_enabled)}\n"
                f"채널: {channel_text}\n"
                "매주 목요일 10:00(KST)에 점검 시작 알림, 12:00(KST)에 업데이트 알림을 역할 멘션과 임베드로 보내요."
            ),
            color=_success_embed_color(),
        )
        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _fetch_broadcast_lives(
        self,
        source_value: str,
    ) -> tuple[ChzzkLive | None, YoutubeLive | None, list[str]]:
        tasks = []
        if _broadcast_source_allows_chzzk(source_value):
            tasks.append(("치지직", self.chzzk_client.fetch_live()))
        if _broadcast_source_allows_youtube(source_value):
            tasks.append(("유튜브", self.youtube_client.fetch_live()))
        if not tasks:
            return None, None, []

        results = await asyncio.gather(
            *(task for _, task in tasks),
            return_exceptions=True,
        )
        chzzk_live: ChzzkLive | None = None
        youtube_live: YoutubeLive | None = None
        errors: list[str] = []
        for (label, _), result in zip(tasks, results):
            if isinstance(result, Exception):
                if _is_internet_exception(result):
                    _log_internet_exception(f"{label} 방송 현황 확인 실패", result)
                else:
                    LOGGER.warning("%s 방송 현황 확인 실패: %s", label, result)
                errors.append(label)
                continue
            if label == "치지직":
                chzzk_live = result
            else:
                youtube_live = result
        return chzzk_live, youtube_live, errors

    def _mark_offline_broadcast_targets(
        self,
        guild_id: int | None,
        source_value: str,
        chzzk_live: ChzzkLive | None,
        youtube_live: YoutubeLive | None,
    ) -> None:
        if guild_id is None:
            return
        if _broadcast_source_allows_chzzk(source_value) and chzzk_live is None:
            target = self.storage.get_chzzk_target(guild_id)
            if target is not None and target.is_live:
                self.storage.mark_chzzk_target_offline(guild_id)
        if _broadcast_source_allows_youtube(source_value) and youtube_live is None:
            target = self.storage.get_youtube_target(guild_id)
            if target is not None and target.is_live:
                self.storage.mark_youtube_target_offline(guild_id)

    @staticmethod
    def _all_broadcast_sources_failed(source_value: str, errors: list[str]) -> bool:
        requested_count = int(_broadcast_source_allows_chzzk(source_value)) + int(
            _broadcast_source_allows_youtube(source_value)
        )
        return bool(errors) and len(errors) == requested_count

    async def _send_chzzk_broadcast_status(
        self,
        interaction: discord.Interaction,
        chzzk_live: ChzzkLive | None,
        youtube_url: str,
    ) -> None:
        if chzzk_live is None:
            latest_chzzk = await self._fetch_chzzk_latest_broadcast()
            await interaction.followup.send(
                embed=_embed_for_chzzk_offline(latest_chzzk),
                view=_chzzk_live_view(youtube_url),
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        await interaction.followup.send(
            content=PROJECT_MOON_CHZZK_LIVE_URL,
            embed=_embed_for_chzzk_live(chzzk_live),
            view=_chzzk_live_view(youtube_url),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _send_youtube_broadcast_status(
        self,
        interaction: discord.Interaction,
        youtube_live: YoutubeLive | None,
    ) -> None:
        if youtube_live is None:
            latest_youtube = await self._fetch_youtube_latest_stream()
            await interaction.followup.send(
                embed=_embed_for_youtube_offline(latest_youtube),
                view=_youtube_live_view(
                    latest_youtube.url
                    if latest_youtube
                    else PROJECT_MOON_YOUTUBE_STREAMS_URL
                ),
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        await interaction.followup.send(
            content=youtube_live.url,
            embed=_embed_for_youtube_live(youtube_live),
            view=_youtube_live_view(youtube_live.url),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _fetch_chzzk_latest_broadcast(self) -> ChzzkBroadcast | None:
        try:
            return await self.chzzk_client.fetch_latest_broadcast()
        except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError) as exc:
            _log_internet_exception("치지직 최근 방송 확인 실패", exc)
        except Exception as exc:
            if _is_internet_exception(exc):
                _log_internet_exception("치지직 최근 방송 확인 실패", exc)
            else:
                LOGGER.exception("치지직 최근 방송 확인 실패.")
        return None

    async def _fetch_youtube_latest_stream(self) -> YoutubeStream | None:
        try:
            return await self.youtube_client.fetch_latest_stream()
        except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError) as exc:
            _log_internet_exception("유튜브 최근 방송 확인 실패", exc)
        except Exception as exc:
            if _is_internet_exception(exc):
                _log_internet_exception("유튜브 최근 방송 확인 실패", exc)
            else:
                LOGGER.exception("유튜브 최근 방송 확인 실패.")
        return None

    @staticmethod
    def _as_text_channel(channel: object | None) -> discord.TextChannel | None:
        return channel if isinstance(channel, discord.TextChannel) else None

    async def _resolve_default_broadcast_channel(
        self,
        interaction: discord.Interaction,
    ) -> discord.TextChannel | None:
        settings = self.storage.get_settings(interaction.guild_id)
        if settings.channel_id is None:
            return None
        resolved = await self._resolve_target_channel(None, settings.channel_id)
        return self._as_text_channel(resolved)

    async def _resolve_source_broadcast_channel(
        self,
        guild_id: int | None,
        source_value: str,
    ) -> discord.TextChannel | None:
        if _broadcast_source_allows_chzzk(source_value):
            chzzk_target = self.storage.get_chzzk_target(guild_id)
            if chzzk_target is not None:
                resolved = await self._resolve_chzzk_target_channel(chzzk_target)
                text_channel = self._as_text_channel(resolved)
                if text_channel is not None:
                    return text_channel

        if _broadcast_source_allows_youtube(source_value):
            youtube_target = self.storage.get_youtube_target(guild_id)
            if youtube_target is not None:
                resolved = await self._resolve_youtube_target_channel(youtube_target)
                return self._as_text_channel(resolved)
        return None

    async def _resolve_broadcast_target_channel(
        self,
        interaction: discord.Interaction,
        source_value: str,
        channel: discord.TextChannel | None,
    ) -> discord.TextChannel | None:
        if channel is not None:
            return channel

        resolved = await self._resolve_default_broadcast_channel(interaction)
        if resolved is not None:
            return resolved

        resolved = await self._resolve_source_broadcast_channel(
            interaction.guild_id,
            source_value,
        )
        if resolved is not None:
            return resolved

        return self._as_text_channel(interaction.channel)

    async def _resolve_youtube_upload_config_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None,
    ) -> discord.TextChannel | None:
        if channel is not None:
            return channel

        settings = self.storage.get_settings(interaction.guild_id)
        if settings.channel_id is not None:
            resolved = await self._resolve_target_channel(None, settings.channel_id)
            if isinstance(resolved, discord.TextChannel):
                return resolved

        current = self.storage.get_youtube_upload_target(interaction.guild_id)
        if current is not None:
            resolved = await self._resolve_youtube_upload_target_channel(current)
            if isinstance(resolved, discord.TextChannel):
                return resolved

        return self._as_text_channel(interaction.channel)

    async def _resolve_youtube_upload_send_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None,
        target: GuildYoutubeUploadTarget | None,
        settings: GuildSettings,
    ) -> discord.TextChannel | None:
        if channel is not None:
            return channel
        if target is not None:
            resolved = await self._resolve_youtube_upload_target_channel(target)
            text_channel = self._as_text_channel(resolved)
            if text_channel is not None:
                return text_channel
        if settings.channel_id is not None:
            resolved = await self._resolve_target_channel(None, settings.channel_id)
            text_channel = self._as_text_channel(resolved)
            if text_channel is not None:
                return text_channel
        return self._as_text_channel(interaction.channel)

    async def _resolve_hampang_config_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None,
    ) -> discord.TextChannel | None:
        if channel is not None:
            return channel
        current = self.storage.get_hampang_target(interaction.guild_id)
        if current is not None:
            resolved = await self._resolve_hampang_target_channel(current)
            if isinstance(resolved, discord.TextChannel):
                return resolved
        settings = self.storage.get_settings(interaction.guild_id)
        if settings.channel_id is not None:
            resolved = await self._resolve_target_channel(None, settings.channel_id)
            if isinstance(resolved, discord.TextChannel):
                return resolved
        return (
            interaction.channel
            if isinstance(interaction.channel, discord.TextChannel)
            else None
        )

    async def _send_hampang_news_select_menu(
        self,
        interaction: discord.Interaction,
        items: list[tuple[str, TwitterPost | YoutubeUpload]],
        *,
        mode: str,
        private: bool = True,
        channel_id: int | None = None,
        role_id: int | None = None,
        had_failure: bool = False,
    ) -> None:
        if not items:
            message = (
                "최근 햄햄팡팡 소식을 확인하지 못했어요. 잠시 뒤 다시 시도해주세요."
                if had_failure
                else "선택할 수 있는 햄햄팡팡 소식이 없어요."
            )
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
            return

        view = HampangNewsSelectView(
            self,
            interaction.user.id,
            items,
            mode=mode,
            private=private,
            channel_id=channel_id,
            role_id=role_id,
        )
        if interaction.response.is_done():
            await interaction.followup.send(
                embed=view.build_embed(),
                view=view,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        else:
            await interaction.response.send_message(
                embed=view.build_embed(),
                view=view,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

    async def _show_selected_hampang_news(
        self,
        interaction: discord.Interaction,
        source: str,
        item: TwitterPost | YoutubeUpload,
        *,
        private: bool,
    ) -> None:
        sent_messages: list[discord.Message | None]
        if source == HAMPANG_SOURCE_X and isinstance(item, TwitterPost):
            sent_messages = await self._send_twitter_post_followups(
                interaction,
                item,
                private=private,
            )
        elif source == HAMPANG_SOURCE_YOUTUBE and isinstance(item, YoutubeUpload):
            sent_messages = [
                await interaction.followup.send(
                    embed=_embed_for_hampang_youtube_upload(item),
                    view=_youtube_upload_view(item.url),
                    ephemeral=private,
                    allowed_mentions=discord.AllowedMentions.none(),
                    wait=True,
                )
            ]
        else:
            await interaction.followup.send(
                "선택한 햄햄팡팡 소식 형식을 확인하지 못했어요.",
                ephemeral=True,
            )
            return

        if not private and interaction.guild_id is not None and interaction.channel_id is not None:
            for message in sent_messages:
                await self._track_manual_message(
                    interaction.guild_id,
                    interaction.channel_id,
                    message,
                )

    async def _show_latest_hampang_news(
        self,
        interaction: discord.Interaction,
        *,
        private: bool,
    ) -> None:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=private, thinking=True)
        x_posts, youtube_uploads, had_failure = await self._fetch_hampang_news_sources()
        items = _hampang_news_items(x_posts, youtube_uploads)
        if not items:
            await interaction.followup.send(
                "최근 햄햄팡팡 소식을 확인하지 못했어요. 잠시 뒤 다시 시도해주세요."
                if had_failure else "아직 확인된 햄햄팡팡 소식이 없어요.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        source, item = items[0]
        await self._show_selected_hampang_news(
            interaction,
            source,
            item,
            private=private,
        )

    async def _send_selected_hampang_news(
        self,
        interaction: discord.Interaction,
        source: str,
        item: TwitterPost | YoutubeUpload,
        *,
        channel_id: int | None,
        role_id: int | None,
    ) -> None:
        if interaction.guild_id is None or channel_id is None:
            await interaction.followup.send(
                "햄햄팡팡 소식을 보낼 채널을 찾지 못했어요.",
                ephemeral=True,
            )
            return
        target_channel = await self._resolve_target_channel(None, channel_id)
        if target_channel is None:
            await interaction.followup.send(
                "햄햄팡팡 소식을 보낼 채널에 접근하지 못했어요. 채널 권한을 확인해주세요.",
                ephemeral=True,
            )
            return

        settings = self.storage.get_settings(interaction.guild_id)
        role_to_send = role_id if role_id is not None else settings.role_id
        try:
            if source == HAMPANG_SOURCE_X and isinstance(item, TwitterPost):
                message = await self._send_twitter_post_to_channel(
                    target_channel,
                    item,
                    role_id=role_to_send,
                )
            elif source == HAMPANG_SOURCE_YOUTUBE and isinstance(item, YoutubeUpload):
                message = await self._send_hampang_youtube_upload_to_channel(
                    target_channel,
                    item,
                    role_id=role_to_send,
                )
            else:
                await interaction.followup.send(
                    "선택한 햄햄팡팡 소식 형식을 확인하지 못했어요.",
                    ephemeral=True,
                )
                return
        except discord.HTTPException:
            LOGGER.exception("햄햄팡팡 소식 수동 전송 실패.")
            await interaction.followup.send(
                "햄햄팡팡 소식 전송에 실패했어요. 채널 권한을 확인해주세요.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        await self._track_manual_message(interaction.guild_id, channel_id, message)
        channel_mention = getattr(target_channel, "mention", f"<#{channel_id}>")
        await interaction.followup.send(
            f"{channel_mention}에 선택한 햄햄팡팡 소식을 보냈어요.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _hampang_config_baseline(
        self,
        enabled: bool,
    ) -> HampangConfigBaseline:
        if not enabled:
            return HampangConfigBaseline(None, None)

        x_posts, youtube_uploads, x_failed, youtube_failed = (
            await self._fetch_hampang_news_sources_detailed()
        )
        baseline = HampangConfigBaseline(
            latest_x_post=x_posts[0] if x_posts else None,
            latest_youtube_upload=youtube_uploads[0] if youtube_uploads else None,
            x_failed=x_failed,
            youtube_failed=youtube_failed,
        )
        if baseline.x_failed or baseline.latest_x_post is None:
            self._hampang_x_recovery_baseline_pending = True
        if baseline.youtube_failed or baseline.latest_youtube_upload is None:
            self._hampang_youtube_recovery_baseline_pending = True
        return baseline

    def _hampang_config_response_lines(
        self,
        target: GuildHampangTarget,
        enabled: bool,
        baseline: HampangConfigBaseline,
    ) -> list[str]:
        lines = [
            f"햄햄팡팡 소식 자동 알림: {_bool_label(target.enabled)}",
            f"채널: <#{target.channel_id}>",
            f"추적 시간대: X 게시물 추적 시간대와 동일 (KST {_format_windows_label(self.config.twitter_tracking_windows_kst)})",
        ]
        lines.extend(self._hampang_x_baseline_lines(enabled, baseline))
        lines.extend(self._hampang_youtube_baseline_lines(enabled, baseline))
        return lines

    @staticmethod
    def _hampang_x_baseline_lines(
        enabled: bool,
        baseline: HampangConfigBaseline,
    ) -> list[str]:
        if baseline.latest_x_post is not None:
            return [f"현재 X 기준선: {baseline.latest_x_post.title}"]
        if enabled and baseline.x_failed:
            return ["현재 X 확인 오류로 기준선을 잡지 못했습니다. 다음 정상 확인에서 기준선만 설정합니다."]
        if enabled:
            return ["최근 X 소식을 찾지 못했습니다. 첫 정상 확인에서 기준선만 설정합니다."]
        return []

    @staticmethod
    def _hampang_youtube_baseline_lines(
        enabled: bool,
        baseline: HampangConfigBaseline,
    ) -> list[str]:
        if baseline.latest_youtube_upload is not None:
            return [f"현재 YouTube 기준선: {baseline.latest_youtube_upload.title}"]
        if enabled and baseline.youtube_failed:
            return ["현재 YouTube 확인 오류로 기준선을 잡지 못했습니다. 다음 정상 확인에서 기준선만 설정합니다."]
        if enabled:
            return ["최근 햄햄팡팡 YouTube 영상을 찾지 못했습니다. 첫 정상 확인에서 기준선만 설정합니다."]
        return []

    @app_commands.command(name="햄팡소식알림설정", description="햄햄팡팡 공식 X와 관련 YouTube 소식 자동 알림을 설정합니다.")
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.rename(enabled="허용", channel="채널")
    @app_commands.describe(
        enabled="햄햄팡팡 소식 자동 알림을 켜거나 끕니다.",
        channel="알림 채널입니다. 비워두면 기존 햄팡 알림 채널, /서버설정 채널, 현재 채널 순서로 사용합니다.",
    )
    @app_commands.choices(enabled=BOOLEAN_CHOICES)
    async def configure_hampang_news_notifications(
        self,
        interaction: discord.Interaction,
        enabled: app_commands.Choice[str],
        channel: discord.TextChannel | None = None,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("서버 안에서만 설정할 수 있어요.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        target_channel = await self._resolve_hampang_config_channel(interaction, channel)
        if target_channel is None:
            await interaction.followup.send(
                "햄햄팡팡 소식 알림을 보낼 채널을 찾지 못했어요. 채널을 직접 골라 다시 실행해주세요.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        enabled_value = _choice_bool(enabled, False)
        baseline = await self._hampang_config_baseline(enabled_value)

        target = self.storage.upsert_hampang_target(
            interaction.guild_id,
            channel_id=target_channel.id,
            enabled=bool(enabled_value),
            last_x_post_id=(
                baseline.latest_x_post.post_id
                if baseline.latest_x_post is not None
                else None
            ),
            last_youtube_video_id=(
                baseline.latest_youtube_upload.video_id
                if baseline.latest_youtube_upload is not None
                else None
            ),
        )

        await interaction.followup.send(
            embed=discord.Embed(
                title="햄햄팡팡 소식 알림 설정이 완료되었어요",
                description="\n".join(
                    self._hampang_config_response_lines(target, enabled_value, baseline)
                ),
                color=_success_embed_color(),
            ),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="햄팡소식알림해제", description="햄햄팡팡 소식 자동 알림 설정을 해제합니다.")
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def remove_hampang_news_notifications(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("서버 안에서만 설정할 수 있어요.", ephemeral=True)
            return

        removed = self.storage.delete_hampang_target(interaction.guild_id)
        await interaction.response.send_message(
            "햄햄팡팡 소식 자동 알림 설정을 해제했어요."
            if removed
            else "이 서버에는 햄햄팡팡 소식 자동 알림 설정이 없어요.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="햄팡소식알림현황보기", description="햄햄팡팡 소식 자동 알림 현황을 확인합니다.")
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.guild_only()
    async def hampang_news_notification_status(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("서버 안에서만 사용할 수 있어요.", ephemeral=True)
            return

        target = self.storage.get_hampang_target(interaction.guild_id)
        settings = self.storage.get_settings(interaction.guild_id)
        embed = discord.Embed(
            title="햄햄팡팡 소식 알림 현황",
            description=(
                _format_hampang_target(target, settings.role_id)
                + "\n\n"
                f"추적 시간대: X 게시물 추적 시간대와 동일 (KST {_format_windows_label(self.config.twitter_tracking_windows_kst)})"
            ),
            color=_success_embed_color(),
        )
        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="햄팡소식보기", description="가장 최근 햄햄팡팡 공식 X 또는 관련 YouTube 소식을 확인합니다.")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def view_hampang_news(self, interaction: discord.Interaction) -> None:
        await self._show_latest_hampang_news(interaction, private=True)

    @app_commands.command(name="햄팡최근소식보기", description="가장 최근 햄햄팡팡 소식을 즉시 확인합니다.")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.rename(private="나만보기")
    @app_commands.describe(
        private="켜면 나에게만 보이고, 끄면 채널에 메시지를 보냅니다.",
    )
    @app_commands.choices(private=BOOLEAN_CHOICES)
    async def view_latest_hampang_news(
        self,
        interaction: discord.Interaction,
        private: app_commands.Choice[str] | None = None,
    ) -> None:
        private_value = bool(_choice_bool(private, True))
        if not await self._allow_public_news_send(interaction, private=private_value):
            return
        if not await self._confirm_external_news_send(interaction):
            return
        await self._show_latest_hampang_news(interaction, private=private_value)

    @app_commands.command(name="햄팡이전소식보기", description="최근 수집한 햄햄팡팡 이전 소식을 골라서 확인합니다.")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.rename(source="소스", private="나만보기")
    @app_commands.describe(
        source="확인할 햄햄팡팡 소식의 출처입니다.",
        private="켜면 선택한 소식이 나에게만 보이고, 끄면 채널에 메시지를 보냅니다.",
    )
    @app_commands.choices(source=HAMPANG_SOURCE_CHOICES, private=BOOLEAN_CHOICES)
    async def view_previous_hampang_news(
        self,
        interaction: discord.Interaction,
        source: app_commands.Choice[str] | None = None,
        private: app_commands.Choice[str] | None = None,
    ) -> None:
        private_value = bool(_choice_bool(private, True))
        if not await self._allow_public_news_send(interaction, private=private_value):
            return
        if not await self._confirm_external_news_send(interaction):
            return
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
        x_posts, youtube_uploads, had_failure = await self._fetch_hampang_news_sources()
        items = _hampang_news_items_for_source(
            x_posts,
            youtube_uploads,
            source.value if source is not None else HAMPANG_SOURCE_BOTH,
        )
        await self._send_hampang_news_select_menu(
            interaction,
            items,
            mode="previous",
            private=private_value,
            had_failure=had_failure,
        )

    @app_commands.command(name="햄팡소식보내기", description="햄햄팡팡 소식을 골라서 지정 채널에 보냅니다.")
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.rename(source="소스", channel="채널", role="역할")
    @app_commands.describe(
        source="보낼 햄햄팡팡 소식의 출처입니다.",
        channel="보낼 채널입니다. 비워두면 설정 채널이나 현재 채널을 사용합니다.",
        role="함께 핑할 역할입니다. 비워두면 /서버설정에서 지정한 역할을 사용합니다.",
    )
    @app_commands.choices(source=HAMPANG_SOURCE_CHOICES)
    async def send_hampang_news(
        self,
        interaction: discord.Interaction,
        source: app_commands.Choice[str] | None = None,
        channel: discord.TextChannel | None = None,
        role: discord.Role | None = None,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("서버 안에서만 사용할 수 있어요.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        target_channel = await self._resolve_hampang_config_channel(interaction, channel)
        if target_channel is None:
            await interaction.followup.send(
                "햄햄팡팡 소식을 보낼 채널을 찾지 못했어요. 채널을 직접 골라 다시 실행해주세요.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        x_posts, youtube_uploads, had_failure = await self._fetch_hampang_news_sources()
        items = _hampang_news_items_for_source(
            x_posts,
            youtube_uploads,
            source.value if source is not None else HAMPANG_SOURCE_BOTH,
        )
        await self._send_hampang_news_select_menu(
            interaction,
            items,
            mode="send",
            channel_id=target_channel.id,
            role_id=role.id if role is not None else None,
            had_failure=had_failure,
        )

    @app_commands.command(name="유튜브알림설정", description="ProjectMoon Official 일반 영상 업로드 알림을 설정합니다.")
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.rename(enabled="허용", channel="채널")
    @app_commands.describe(
        enabled="일반 영상 업로드 알림을 켜거나 끕니다.",
        channel="알림 채널입니다. 비워두면 /서버설정 채널, 기존 업로드 알림 채널, 현재 채널 순서로 사용합니다.",
    )
    @app_commands.choices(enabled=BOOLEAN_CHOICES)
    async def configure_youtube_upload_notifications(
        self,
        interaction: discord.Interaction,
        enabled: app_commands.Choice[str],
        channel: discord.TextChannel | None = None,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("서버 안에서만 설정할 수 있어요.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        target_channel = await self._resolve_youtube_upload_config_channel(interaction, channel)
        if target_channel is None:
            await interaction.followup.send(
                "유튜브 업로드 알림을 보낼 채널을 찾지 못했어요. 채널을 직접 골라 다시 실행해주세요.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        enabled_value = _choice_bool(enabled, False)
        latest_upload: YoutubeUpload | None = None
        fetch_failed = False
        if enabled_value:
            try:
                uploads = _regular_youtube_uploads(await self.youtube_client.fetch_recent_uploads())
                latest_upload = uploads[0] if uploads else None
            except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError) as exc:
                fetch_failed = True
                self._youtube_upload_recovery_baseline_pending = True
                _log_internet_exception("유튜브 업로드 알림 기준선 확인 실패", exc)
            if latest_upload is None:
                self._youtube_upload_recovery_baseline_pending = True

        target = self.storage.upsert_youtube_upload_target(
            interaction.guild_id,
            channel_id=target_channel.id,
            enabled=enabled_value,
            last_video_id=latest_upload.video_id if latest_upload is not None else None,
        )
        lines = [
            f"유튜브 업로드 알림: {_bool_label(target.enabled)}",
            f"채널: <#{target.channel_id}>",
            "라이브·프리미어 영상은 업로드 알림에서 제외됩니다.",
        ]
        if latest_upload is not None:
            lines.append(f"현재 업로드 기준선: {latest_upload.title}")
        elif enabled_value and fetch_failed:
            lines.append("현재 인터넷 오류로 기준선을 확인하지 못했습니다. 다음 정상 확인에서 기준선만 설정합니다.")
        elif enabled_value:
            lines.append("일반 영상을 찾지 못했습니다. 첫 정상 확인에서 기준선만 설정합니다.")

        await interaction.followup.send(
            embed=discord.Embed(
                title="유튜브 업로드 알림 설정이 완료되었어요",
                description="\n".join(lines),
                color=_success_embed_color(),
            ),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="유튜브알림해제", description="ProjectMoon Official 일반 영상 업로드 알림 설정을 해제합니다.")
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def remove_youtube_upload_notifications(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("서버 안에서만 설정할 수 있어요.", ephemeral=True)
            return

        removed = self.storage.delete_youtube_upload_target(interaction.guild_id)
        await interaction.response.send_message(
            "유튜브 일반 영상 업로드 알림 설정을 해제했어요."
            if removed
            else "이 서버에는 유튜브 일반 영상 업로드 알림 설정이 없어요.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="유튜브알림현황보기", description="ProjectMoon Official 일반 영상 업로드 알림 현황을 확인합니다.")
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.guild_only()
    async def youtube_upload_notification_status(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("서버 안에서만 사용할 수 있어요.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        target = self.storage.get_youtube_upload_target(interaction.guild_id)
        settings = self.storage.get_settings(interaction.guild_id)
        try:
            uploads = _regular_youtube_uploads(await self.youtube_client.fetch_recent_uploads())
        except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError) as exc:
            _log_internet_exception("유튜브 업로드 현황 확인 실패", exc)
            uploads = []

        if uploads:
            embed = _embed_for_youtube_upload(uploads[0])
            embed.description = (
                "가장 최근에 확인된 ProjectMoon Official 일반 영상입니다.\n\n"
                + _format_youtube_upload_target(target, settings.role_id)
            )
        else:
            embed = discord.Embed(
                title="유튜브 일반 영상 업로드 알림 현황",
                description=(
                    _format_youtube_upload_target(target, settings.role_id)
                    + "\n\n최근 일반 영상을 확인하지 못했습니다."
                ),
                url=PROJECT_MOON_YOUTUBE_VIDEOS_URL,
                color=discord.Color.from_rgb(255, 0, 0),
            )
        await interaction.followup.send(
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="유튜브알림보내기", description="가장 최근 ProjectMoon Official 일반 영상을 지정 채널에 보냅니다.")
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.rename(channel="채널", role="역할")
    @app_commands.describe(
        channel="영상을 보낼 채널입니다. 비워두면 업로드 알림 채널 또는 /서버설정 채널을 사용합니다.",
        role="함께 핑할 역할입니다. 비워두면 /서버설정 역할을 사용합니다.",
    )
    async def send_latest_youtube_upload(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
        role: discord.Role | None = None,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("서버 안에서만 사용할 수 있어요.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        target = self.storage.get_youtube_upload_target(interaction.guild_id)
        settings = self.storage.get_settings(interaction.guild_id)
        target_channel = await self._resolve_youtube_upload_send_channel(
            interaction,
            channel,
            target,
            settings,
        )
        if target_channel is None:
            await interaction.followup.send(
                "유튜브 영상을 보낼 채널을 찾지 못했어요. 채널을 직접 골라 다시 실행해주세요.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        try:
            uploads = _regular_youtube_uploads(await self.youtube_client.fetch_recent_uploads())
        except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError) as exc:
            _log_internet_exception("유튜브 업로드 수동 전송용 영상 확인 실패", exc)
            await interaction.followup.send(
                "최근 유튜브 일반 영상을 확인하지 못했어요. 잠시 뒤 다시 시도해주세요.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        if not uploads:
            await interaction.followup.send(
                "보낼 수 있는 일반 유튜브 영상을 찾지 못했어요.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        upload = uploads[0]
        try:
            message = await self._send_youtube_upload_to_channel(
                target_channel,
                upload,
                role_id=role.id if role is not None else settings.role_id,
            )
        except discord.HTTPException:
            LOGGER.exception("유튜브 업로드 수동 전송 실패.")
            await interaction.followup.send(
                "유튜브 영상 전송에 실패했어요. 채널 권한을 확인해주세요.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        await self._track_manual_message(interaction.guild_id, target_channel.id, message)
        if target is not None:
            self.storage.mark_youtube_upload_target_seen(interaction.guild_id, upload.video_id)
        await interaction.followup.send(
            f"{target_channel.mention}에 최신 일반 유튜브 영상을 보냈어요.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="방송알림설정", description="ProjectMoon Official 방송 시작 알림을 설정합니다.")
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.rename(source="소스", enabled="허용", channel="채널")
    @app_commands.describe(
        source="알림을 받을 방송 소스입니다.",
        enabled="선택한 방송 알림 구성을 켜거나 끕니다.",
        channel="알림을 보낼 채널입니다. 비워두면 /서버설정 채널, 기존 방송 알림 채널, 현재 채널 순서로 사용합니다.",
    )
    @app_commands.choices(source=BROADCAST_SOURCE_CHOICES, enabled=BOOLEAN_CHOICES)
    async def configure_broadcast_notifications(
        self,
        interaction: discord.Interaction,
        source: app_commands.Choice[str],
        enabled: app_commands.Choice[str],
        channel: discord.TextChannel | None = None,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("서버 안에서만 설정할 수 있어요.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        source_value = source.value
        target_channel = await self._resolve_broadcast_target_channel(
            interaction,
            source_value,
            channel,
        )
        if target_channel is None:
            await interaction.followup.send(
                "방송 알림을 보낼 채널을 찾지 못했어요. 채널을 직접 골라 다시 실행해주세요.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        enabled_value = _choice_bool(enabled, False)
        chzzk_live: ChzzkLive | None = None
        youtube_live: YoutubeLive | None = None
        errors: list[str] = []
        if enabled_value:
            chzzk_live, youtube_live, errors = await self._fetch_broadcast_lives(source_value)

        chzzk_enabled = bool(enabled_value and _broadcast_source_allows_chzzk(source_value))
        youtube_enabled = bool(enabled_value and _broadcast_source_allows_youtube(source_value))
        chzzk_target = self.storage.upsert_chzzk_target(
            interaction.guild_id,
            channel_id=target_channel.id,
            enabled=chzzk_enabled,
            last_live_id=chzzk_live.live_id if chzzk_live is not None else None,
            is_live=chzzk_live is not None if chzzk_enabled else False,
        )
        youtube_target = self.storage.upsert_youtube_target(
            interaction.guild_id,
            channel_id=target_channel.id,
            enabled=youtube_enabled,
            last_live_id=youtube_live.video_id if youtube_live is not None else None,
            is_live=youtube_live is not None if youtube_enabled else False,
        )

        lines = [
            f"방송 알림 모드: {_broadcast_source_label(source_value)}",
            f"채널: {target_channel.mention}",
            f"치지직 알림: {_bool_label(chzzk_target.enabled)}",
            f"유튜브 알림: {_bool_label(youtube_target.enabled)}",
        ]
        if chzzk_live is not None:
            lines.append(f"치지직 현재 라이브 기준선: {chzzk_live.title}")
        if youtube_live is not None:
            lines.append(f"유튜브 현재 라이브 기준선: {youtube_live.title}")
        if errors:
            lines.append("확인 실패: " + ", ".join(errors))

        embed = discord.Embed(
            title="방송 알림 설정이 완료되었어요",
            description="\n".join(lines),
            color=_success_embed_color(),
        )
        await interaction.followup.send(
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="방송알림해제", description="ProjectMoon Official 방송 시작 알림 설정을 해제합니다.")
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.rename(target="대상")
    @app_commands.describe(target="해제할 방송 알림입니다. 현재 설정된 알림 중에서 고릅니다.")
    async def remove_broadcast_notifications(
        self,
        interaction: discord.Interaction,
        target: str,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("서버 안에서만 설정할 수 있어요.", ephemeral=True)
            return

        source_value = target if target in {
            BROADCAST_SOURCE_BOTH,
            BROADCAST_SOURCE_CHZZK,
            BROADCAST_SOURCE_YOUTUBE,
        } else ""
        if not source_value:
            chzzk_target = self.storage.get_chzzk_target(interaction.guild_id)
            youtube_target = self.storage.get_youtube_target(interaction.guild_id)
            await interaction.response.send_message(
                "해제할 방송 알림을 현재 설정 목록에서 골라주세요.\n\n"
                f"치지직 알림\n{_format_chzzk_target(chzzk_target, self.storage.get_settings(interaction.guild_id).role_id)}\n\n"
                f"유튜브 알림\n{_format_youtube_target(youtube_target, self.storage.get_settings(interaction.guild_id).role_id)}",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        removed: list[str] = []
        missing: list[str] = []
        if _broadcast_source_allows_chzzk(source_value):
            if self.storage.delete_chzzk_target(interaction.guild_id):
                removed.append("치지직")
            else:
                missing.append("치지직")
        if _broadcast_source_allows_youtube(source_value):
            if self.storage.delete_youtube_target(interaction.guild_id):
                removed.append("유튜브")
            else:
                missing.append("유튜브")

        chzzk_target = self.storage.get_chzzk_target(interaction.guild_id)
        youtube_target = self.storage.get_youtube_target(interaction.guild_id)
        settings = self.storage.get_settings(interaction.guild_id)
        result = "해제한 방송 알림: " + (", ".join(removed) if removed else "없음")
        if missing:
            result += "\n이미 설정이 없던 방송 알림: " + ", ".join(missing)

        embed = discord.Embed(
            title="방송 알림 설정",
            description=result,
            color=_success_embed_color(),
        )
        embed.add_field(
            name="치지직 알림",
            value=_format_chzzk_target(chzzk_target, settings.role_id),
            inline=False,
        )
        embed.add_field(
            name="유튜브 알림",
            value=_format_youtube_target(youtube_target, settings.role_id),
            inline=False,
        )
        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @remove_broadcast_notifications.autocomplete("target")
    async def remove_broadcast_notifications_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild_id is None:
            return []
        chzzk_target = self.storage.get_chzzk_target(interaction.guild_id)
        youtube_target = self.storage.get_youtube_target(interaction.guild_id)
        choices: list[app_commands.Choice[str]] = []
        if chzzk_target is not None and youtube_target is not None:
            choices.append(
                app_commands.Choice(
                    name="치지직 & 유튜브 · 방송 알림 전체 해제",
                    value=BROADCAST_SOURCE_BOTH,
                )
            )
        if chzzk_target is not None:
            choices.append(
                app_commands.Choice(
                    name=_broadcast_target_choice_name("치지직", chzzk_target.channel_id, chzzk_target.enabled, interaction),
                    value=BROADCAST_SOURCE_CHZZK,
                )
            )
        if youtube_target is not None:
            choices.append(
                app_commands.Choice(
                    name=_broadcast_target_choice_name("유튜브", youtube_target.channel_id, youtube_target.enabled, interaction),
                    value=BROADCAST_SOURCE_YOUTUBE,
                )
            )
        if current:
            current_lower = current.casefold()
            choices = [choice for choice in choices if current_lower in choice.name.casefold()]
        return choices[:25]

    @app_commands.command(name="방송현황보기", description="ProjectMoon Official 방송 현황과 링크를 확인합니다.")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.rename(source="소스")
    @app_commands.describe(source="확인할 방송 소스입니다. 비워두면 치지직과 유튜브를 모두 확인합니다.")
    @app_commands.choices(source=BROADCAST_SOURCE_CHOICES)
    async def broadcast_live_status(
        self,
        interaction: discord.Interaction,
        source: app_commands.Choice[str] | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)

        source_value = _broadcast_source_value(source)
        chzzk_live, youtube_live, errors = await self._fetch_broadcast_lives(source_value)
        self._mark_offline_broadcast_targets(
            interaction.guild_id,
            source_value,
            chzzk_live,
            youtube_live,
        )

        if self._all_broadcast_sources_failed(source_value, errors):
            await interaction.followup.send(
                "방송 현황을 확인하지 못했어요. 잠시 뒤 다시 시도해주세요.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        youtube_url = youtube_live.url if youtube_live is not None else PROJECT_MOON_YOUTUBE_STREAMS_URL
        if _broadcast_source_allows_chzzk(source_value) and "치지직" not in errors:
            await self._send_chzzk_broadcast_status(interaction, chzzk_live, youtube_url)

        if _broadcast_source_allows_youtube(source_value) and "유튜브" not in errors:
            await self._send_youtube_broadcast_status(interaction, youtube_live)

        if errors:
            await interaction.followup.send(
                "확인 실패: " + ", ".join(errors),
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

    async def _send_manual_broadcast_error(
        self,
        interaction: discord.Interaction,
        message: str,
    ) -> None:
        await interaction.followup.send(
            message,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _process_manual_chzzk_broadcast(
        self,
        interaction: discord.Interaction,
        target_channel: discord.TextChannel,
        source_value: str,
        chzzk_live: ChzzkLive | None,
        youtube_live: YoutubeLive | None,
        role_id: int | None,
        sent: list[str],
        skipped: list[str],
        errors: list[str],
    ) -> bool:
        if not _broadcast_source_allows_chzzk(source_value) or "치지직" in errors:
            return True
        if chzzk_live is None:
            skipped.append("치지직: 방송 없음 / 오프라인")
            return True
        if _is_chzzk_live_too_old(chzzk_live):
            if self.storage.get_chzzk_target(interaction.guild_id) is not None:
                self.storage.mark_chzzk_target_seen(interaction.guild_id, chzzk_live.live_id)
            skipped.append("치지직: 방송 시작 후 10분 이상 지남")
            return True

        try:
            message = await self._send_chzzk_live_to_channel(
                target_channel,
                chzzk_live,
                role_id=role_id if not sent else None,
                youtube_url=youtube_live.url if youtube_live is not None else None,
                include_youtube_button=not _broadcast_source_allows_youtube(source_value),
            )
        except discord.Forbidden:
            await self._send_manual_broadcast_error(interaction, "지정한 채널에 방송을 보낼 권한이 없어요.")
            return False
        except discord.HTTPException:
            LOGGER.exception("치지직 라이브 수동 전송 실패.")
            await self._send_manual_broadcast_error(
                interaction,
                "방송 전송에 실패했어요. 채널 권한을 확인해주세요.",
            )
            return False

        await self._track_manual_message(interaction.guild_id, target_channel.id, message)
        if self.storage.get_chzzk_target(interaction.guild_id) is not None:
            self.storage.mark_chzzk_target_seen(interaction.guild_id, chzzk_live.live_id)
        sent.append("치지직")
        return True

    async def _process_manual_youtube_broadcast(
        self,
        interaction: discord.Interaction,
        target_channel: discord.TextChannel,
        source_value: str,
        youtube_live: YoutubeLive | None,
        role_id: int | None,
        sent: list[str],
        skipped: list[str],
        errors: list[str],
    ) -> bool:
        if not _broadcast_source_allows_youtube(source_value) or "유튜브" in errors:
            return True
        if youtube_live is None:
            skipped.append("유튜브: 방송 없음 / 오프라인")
            return True
        if _is_youtube_live_too_old(youtube_live):
            if self.storage.get_youtube_target(interaction.guild_id) is not None:
                self.storage.mark_youtube_target_seen(interaction.guild_id, youtube_live.video_id)
            skipped.append("유튜브: 방송 시작 후 10분 이상 지남")
            return True

        try:
            message = await self._send_youtube_live_to_channel(
                target_channel,
                youtube_live,
                role_id=role_id if not sent else None,
                include_chzzk_button=not _broadcast_source_allows_chzzk(source_value),
            )
        except discord.Forbidden:
            await self._send_manual_broadcast_error(interaction, "지정한 채널에 방송을 보낼 권한이 없어요.")
            return False
        except discord.HTTPException:
            LOGGER.exception("유튜브 라이브 수동 전송 실패.")
            await self._send_manual_broadcast_error(
                interaction,
                "방송 전송에 실패했어요. 채널 권한을 확인해주세요.",
            )
            return False

        await self._track_manual_message(interaction.guild_id, target_channel.id, message)
        if self.storage.get_youtube_target(interaction.guild_id) is not None:
            self.storage.mark_youtube_target_seen(interaction.guild_id, youtube_live.video_id)
        sent.append("유튜브")
        return True

    @staticmethod
    def _manual_broadcast_result_text(
        target_channel: discord.TextChannel,
        sent: list[str],
        skipped: list[str],
    ) -> str:
        if not sent:
            return "보낼 수 있는 현재 방송이 없어요.\n" + "\n".join(skipped)
        result = f"{target_channel.mention}에 {', '.join(sent)} 방송을 보냈어요."
        if skipped:
            result += "\n" + "\n".join(skipped)
        return result

    @app_commands.command(name="방송알림보내기", description="현재 ProjectMoon Official 방송을 지정 채널에 보냅니다.")
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.rename(source="소스", channel="채널", role="역할")
    @app_commands.describe(
        source="보낼 방송 소스입니다. 비워두면 치지직과 유튜브를 모두 확인합니다.",
        channel="보낼 채널입니다. 비워두면 /서버설정 채널 또는 방송 알림 채널을 사용합니다.",
        role="함께 핑할 역할입니다. 비워두면 /서버설정에서 지정한 역할을 사용합니다.",
    )
    @app_commands.choices(source=BROADCAST_SOURCE_CHOICES)
    async def send_broadcast_live(
        self,
        interaction: discord.Interaction,
        source: app_commands.Choice[str] | None = None,
        channel: discord.TextChannel | None = None,
        role: discord.Role | None = None,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("서버 안에서만 사용할 수 있어요.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        source_value = _broadcast_source_value(source)
        target_channel = await self._resolve_broadcast_target_channel(
            interaction,
            source_value,
            channel,
        )
        if target_channel is None:
            await interaction.followup.send(
                "방송을 보낼 채널을 찾지 못했어요. 채널을 직접 골라 다시 실행해주세요.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        chzzk_live, youtube_live, errors = await self._fetch_broadcast_lives(source_value)
        settings = self.storage.get_settings(interaction.guild_id)
        role_id = role.id if role else settings.role_id
        sent: list[str] = []
        skipped: list[str] = []

        if not await self._process_manual_chzzk_broadcast(
            interaction,
            target_channel,
            source_value,
            chzzk_live,
            youtube_live,
            role_id,
            sent,
            skipped,
            errors,
        ):
            return
        if not await self._process_manual_youtube_broadcast(
            interaction,
            target_channel,
            source_value,
            youtube_live,
            role_id,
            sent,
            skipped,
            errors,
        ):
            return

        for label in errors:
            skipped.append(f"{label}: 방송 현황 확인 실패")

        await interaction.followup.send(
            self._manual_broadcast_result_text(target_channel, sent, skipped),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _resolve_chzzk_config_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None,
        current: GuildChzzkTarget | None,
    ) -> discord.TextChannel | None:
        if channel is not None:
            return channel
        if current is not None and interaction.guild is not None:
            return self._as_text_channel(interaction.guild.get_channel(current.channel_id))
        return self._as_text_channel(interaction.channel)

    async def _fetch_chzzk_config_live(self, enabled: bool) -> ChzzkLive | None:
        if not enabled:
            return None
        try:
            return await self.chzzk_client.fetch_live()
        except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError) as exc:
            _log_internet_exception("치지직 현재 라이브 확인 실패", exc)
            return None

    async def _resolve_youtube_config_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None,
        current: GuildYoutubeTarget | None,
    ) -> discord.TextChannel | None:
        if channel is not None:
            return channel
        if current is not None:
            resolved = await self._resolve_youtube_target_channel(current)
            text_channel = self._as_text_channel(resolved)
            if text_channel is not None:
                return text_channel
        return self._as_text_channel(interaction.channel)

    async def _fetch_youtube_config_live(self, enabled: bool) -> YoutubeLive | None:
        if not enabled:
            return None
        try:
            return await self.youtube_client.fetch_live()
        except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError) as exc:
            _log_internet_exception("유튜브 현재 라이브 확인 실패", exc)
            return None

    async def configure_chzzk_notifications(
        self,
        interaction: discord.Interaction,
        enabled: app_commands.Choice[str],
        channel: discord.TextChannel | None = None,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("서버 안에서만 설정할 수 있어요.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        current = self.storage.get_chzzk_target(interaction.guild_id)
        channel = await self._resolve_chzzk_config_channel(interaction, channel, current)
        if channel is None:
            await interaction.followup.send(
                "치지직 알림을 보낼 채널을 찾지 못했어요. 채널을 직접 골라 다시 실행해주세요.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        enabled_value = _choice_bool(enabled, False)
        live = await self._fetch_chzzk_config_live(enabled_value)
        last_live_id = live.live_id if live is not None else None

        target = self.storage.upsert_chzzk_target(
            interaction.guild_id,
            channel_id=channel.id,
            enabled=enabled_value,
            last_live_id=last_live_id,
            is_live=live is not None if enabled_value else False,
        )
        channel_text = f"<#{target.channel_id}>"
        live_text = (
            f"\n현재 라이브 기준선: {live.title}" if live is not None else ""
        )
        await interaction.followup.send(
            (
                f"치지직 라이브 알림: {_bool_label(target.enabled)}\n"
                f"채널: {channel_text}{live_text}"
            ),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def chzzk_live_status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            live = await self.chzzk_client.fetch_live()
        except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError) as exc:
            _log_internet_exception("치지직 방송 현황 확인 실패", exc)
            await interaction.followup.send(
                "치지직 방송 현황을 확인하지 못했어요. 잠시 뒤 다시 시도해주세요.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        if live is None:
            if interaction.guild_id is not None:
                target = self.storage.get_chzzk_target(interaction.guild_id)
                if target is not None and target.is_live:
                    self.storage.mark_chzzk_target_offline(interaction.guild_id)
            youtube_url = await self._youtube_latest_live_url()
            latest_chzzk = await self._fetch_chzzk_latest_broadcast()
            await interaction.followup.send(
                embed=_embed_for_chzzk_offline(latest_chzzk),
                view=_chzzk_live_view(youtube_url),
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        youtube_url = await self._youtube_latest_live_url()
        await interaction.followup.send(
            content=PROJECT_MOON_CHZZK_LIVE_URL,
            embed=_embed_for_chzzk_live(live),
            view=_chzzk_live_view(youtube_url),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _resolve_chzzk_live_send_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None,
        settings: GuildSettings,
        chzzk_target: GuildChzzkTarget | None,
    ) -> discord.TextChannel | None:
        if channel is not None:
            return channel
        if settings.channel_id is not None:
            resolved = await self._resolve_target_channel(None, settings.channel_id)
            text_channel = self._as_text_channel(resolved)
            if text_channel is not None:
                return text_channel
        if chzzk_target is not None:
            resolved = await self._resolve_chzzk_target_channel(chzzk_target)
            text_channel = self._as_text_channel(resolved)
            if text_channel is not None:
                return text_channel
        return self._as_text_channel(interaction.channel)

    async def _send_manual_chzzk_live_message(
        self,
        interaction: discord.Interaction,
        target_channel: discord.TextChannel,
        live: ChzzkLive,
        role_id: int | None,
    ) -> discord.Message | None:
        try:
            return await self._send_chzzk_live_to_channel(
                target_channel,
                live,
                role_id=role_id,
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "지정한 채널에 치지직 방송을 보낼 권한이 없어요.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            LOGGER.exception("치지직 라이브 수동 전송 실패.")
            await interaction.followup.send(
                "치지직 방송 전송에 실패했어요. 채널 권한을 확인해주세요.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        return None

    async def send_chzzk_live(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
        role: discord.Role | None = None,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("서버 안에서만 사용할 수 있어요.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            live = await self.chzzk_client.fetch_live()
        except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError) as exc:
            _log_internet_exception("치지직 수동 전송용 방송 확인 실패", exc)
            await interaction.followup.send(
                "치지직 방송 현황을 확인하지 못했어요. 잠시 뒤 다시 시도해주세요.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        if live is None:
            await interaction.followup.send(
                "현재 ProjectMoon Official 치지직 채널은 방송이 없고 오프라인 상태예요.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        chzzk_target = self.storage.get_chzzk_target(interaction.guild_id)
        if _is_chzzk_live_too_old(live):
            if chzzk_target is not None:
                self.storage.mark_chzzk_target_seen(interaction.guild_id, live.live_id)
            await interaction.followup.send(
                "방송 시작 후 10분 이상 지나서 공지하지 않았어요.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        settings = self.storage.get_settings(interaction.guild_id)
        target_channel = await self._resolve_chzzk_live_send_channel(
            interaction,
            channel,
            settings,
            chzzk_target,
        )
        if target_channel is None:
            await interaction.followup.send(
                "치지직 방송을 보낼 채널을 찾지 못했어요. 채널을 직접 골라 다시 실행해주세요.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        role_id = role.id if role else settings.role_id
        message = await self._send_manual_chzzk_live_message(
            interaction,
            target_channel,
            live,
            role_id,
        )
        if message is None:
            return

        await self._track_manual_message(interaction.guild_id, target_channel.id, message)
        if chzzk_target is not None:
            self.storage.mark_chzzk_target_seen(interaction.guild_id, live.live_id)

        await interaction.followup.send(
            f"{target_channel.mention}에 치지직 방송을 보냈어요.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def configure_youtube_notifications(
        self,
        interaction: discord.Interaction,
        enabled: app_commands.Choice[str],
        channel: discord.TextChannel | None = None,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("서버 안에서만 설정할 수 있어요.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        current = self.storage.get_youtube_target(interaction.guild_id)
        target_channel = await self._resolve_youtube_config_channel(
            interaction,
            channel,
            current,
        )
        if target_channel is None:
            await interaction.followup.send(
                "유튜브 알림을 보낼 채널을 찾지 못했어요. 채널을 직접 골라 다시 실행해주세요.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        enabled_value = _choice_bool(enabled, False)
        live = await self._fetch_youtube_config_live(enabled_value)
        last_live_id = live.video_id if live is not None else None

        target = self.storage.upsert_youtube_target(
            interaction.guild_id,
            channel_id=target_channel.id,
            enabled=enabled_value,
            last_live_id=last_live_id,
            is_live=live is not None if enabled_value else False,
        )
        live_text = f"\n현재 라이브 기준선: {live.title}" if live is not None else ""
        await interaction.followup.send(
            (
                f"유튜브 라이브 알림: {_bool_label(target.enabled)}\n"
                f"채널: <#{target.channel_id}>{live_text}"
            ),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def youtube_live_status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            live = await self.youtube_client.fetch_live()
        except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError) as exc:
            _log_internet_exception("유튜브 방송 현황 확인 실패", exc)
            await interaction.followup.send(
                "유튜브 방송 현황을 확인하지 못했어요. 잠시 뒤 다시 시도해주세요.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        if live is None:
            if interaction.guild_id is not None:
                target = self.storage.get_youtube_target(interaction.guild_id)
                if target is not None and target.is_live:
                    self.storage.mark_youtube_target_offline(interaction.guild_id)
            latest_youtube = await self._fetch_youtube_latest_stream()
            await interaction.followup.send(
                embed=_embed_for_youtube_offline(latest_youtube),
                view=_youtube_live_view(latest_youtube.url if latest_youtube else PROJECT_MOON_YOUTUBE_STREAMS_URL),
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        await interaction.followup.send(
            content=live.url,
            embed=_embed_for_youtube_live(live),
            view=_youtube_live_view(live.url),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _resolve_youtube_live_send_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None,
        settings: GuildSettings,
        youtube_target: GuildYoutubeTarget | None,
    ) -> discord.TextChannel | None:
        if channel is not None:
            return channel
        if settings.channel_id is not None:
            resolved = await self._resolve_target_channel(None, settings.channel_id)
            text_channel = self._as_text_channel(resolved)
            if text_channel is not None:
                return text_channel
        if youtube_target is not None:
            resolved = await self._resolve_youtube_target_channel(youtube_target)
            text_channel = self._as_text_channel(resolved)
            if text_channel is not None:
                return text_channel
        return self._as_text_channel(interaction.channel)

    async def _send_manual_youtube_live_message(
        self,
        interaction: discord.Interaction,
        target_channel: discord.TextChannel,
        live: YoutubeLive,
        role_id: int | None,
    ) -> discord.Message | None:
        try:
            return await self._send_youtube_live_to_channel(
                target_channel,
                live,
                role_id=role_id,
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "지정한 채널에 유튜브 방송을 보낼 권한이 없어요.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            LOGGER.exception("유튜브 라이브 수동 전송 실패.")
            await interaction.followup.send(
                "유튜브 방송 전송에 실패했어요. 채널 권한을 확인해주세요.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        return None

    async def send_youtube_live(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
        role: discord.Role | None = None,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("서버 안에서만 사용할 수 있어요.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            live = await self.youtube_client.fetch_live()
        except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError) as exc:
            _log_internet_exception("유튜브 수동 전송용 방송 확인 실패", exc)
            await interaction.followup.send(
                "유튜브 방송 현황을 확인하지 못했어요. 잠시 뒤 다시 시도해주세요.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        if live is None:
            await interaction.followup.send(
                "현재 ProjectMoon Official 유튜브 채널은 방송이 없고 오프라인 상태예요.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        youtube_target = self.storage.get_youtube_target(interaction.guild_id)
        if _is_youtube_live_too_old(live):
            if youtube_target is not None:
                self.storage.mark_youtube_target_seen(interaction.guild_id, live.video_id)
            await interaction.followup.send(
                "방송 시작 후 10분 이상 지나서 공지하지 않았어요.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        settings = self.storage.get_settings(interaction.guild_id)
        target_channel = await self._resolve_youtube_live_send_channel(
            interaction,
            channel,
            settings,
            youtube_target,
        )
        if target_channel is None:
            await interaction.followup.send(
                "유튜브 방송을 보낼 채널을 찾지 못했어요. 채널을 직접 골라 다시 실행해주세요.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        role_id = role.id if role else settings.role_id
        message = await self._send_manual_youtube_live_message(
            interaction,
            target_channel,
            live,
            role_id,
        )
        if message is None:
            return

        await self._track_manual_message(interaction.guild_id, target_channel.id, message)
        if youtube_target is not None:
            self.storage.mark_youtube_target_seen(interaction.guild_id, live.video_id)

        await interaction.followup.send(
            f"{target_channel.mention}에 유튜브 방송을 보냈어요.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _youtube_latest_live_url(self) -> str:
        try:
            return await self.youtube_client.fetch_latest_live_url()
        except Exception as exc:
            if _is_internet_exception(exc):
                _log_internet_exception("유튜브 최신 라이브 링크 확인 실패", exc)
            else:
                LOGGER.warning("유튜브 최신 라이브 링크 확인 실패: %s", exc)
            return PROJECT_MOON_YOUTUBE_STREAMS_URL

    @app_commands.command(name="유저설정", description="앱에서 사용할 봇 개인 설정을 변경합니다.")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.rename(language="언어", news_banner="알림배너")
    @app_commands.describe(
        language="앱으로 사용하는 /최근소식보기, /이전소식보기의 표시 언어입니다.",
        news_banner="앱으로 사용하는 /최근소식보기, /이전소식보기의 배너입니다. 이미지 이름 또는 사용 안 함을 고릅니다.",
    )
    @app_commands.choices(language=LANGUAGE_CHOICES)
    async def configure_user(
        self,
        interaction: discord.Interaction,
        language: app_commands.Choice[str] | None = None,
        news_banner: str | None = None,
    ) -> None:
        if language is None and news_banner is None:
            await interaction.response.send_message(
                "바꿀 언어나 알림 배너를 하나 이상 선택해주세요.",
                ephemeral=True,
            )
            return

        banner_filename = None
        if news_banner is not None:
            banner_filename = _resolve_banner_filename(news_banner)
            if banner_filename is None:
                await interaction.response.send_message(
                    "선택한 알림 배너를 찾지 못했어요. `img` 폴더의 배너 이미지 이름이나 `사용 안 함`을 다시 골라주세요.",
                    ephemeral=True,
                )
                return

        user_id, username, nickname = self._interaction_user_values(interaction)
        if language is not None:
            settings = self.storage.update_user_language(
                user_id,
                username=username,
                nickname=nickname,
                language=language.value,
            )
        else:
            self._remember_interaction_user(interaction)
            settings = self.storage.get_user_settings(user_id)

        if news_banner is not None:
            settings = self.storage.update_user_news_banner(
                user_id,
                username=username,
                nickname=nickname,
                news_banner=banner_filename,
            )

        embed = discord.Embed(
            title="개인 설정이 완료되었어요",
            description=(
                f"개인 언어를 {_language_label(settings.language)}로 설정했어요.\n"
                f"개인 알림 배너를 {_banner_display_name(settings.news_banner)}로 설정했어요.\n"
                "앱으로 사용하는 /최근소식보기와 /이전소식보기에서 이 설정을 사용할게요."
            ),
            color=_success_embed_color(),
        )
        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @configure_user.autocomplete("news_banner")
    async def configure_user_banner_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return _banner_autocomplete_choices(current)

    @app_commands.command(name="역할핑해제", description="새 소식 알림의 역할 핑을 제거합니다.")
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def clear_role(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("서버 안에서만 설정할 수 있어요.", ephemeral=True)
            return

        self.storage.clear_role(interaction.guild_id)
        embed = discord.Embed(
            title="역할 핑을 제거했어요",
            description="새 소식 알림에 역할 핑을 붙이지 않을게요.",
            color=_success_embed_color(),
        )
        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="서버설정초기화", description="이 서버의 림피 설정을 초기 상태로 되돌립니다.")
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def reset_server_settings(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("서버 안에서만 사용할 수 있어요.", ephemeral=True)
            return

        self.storage.reset_guild_settings(interaction.guild_id)
        settings = self.storage.get_settings(interaction.guild_id)
        embed = discord.Embed(
            title="서버 설정을 초기화했어요",
            description=(
                "언어별 소식 채널: 미설정\n"
                "역할 핑: 없음\n"
                f"새 게시물 자동 알림: {'켜짐' if settings.enabled else '꺼짐'}\n"
                f"기본 언어: {_language_label(settings.language)}\n"
                f"조회 메시지 자동 삭제: {'켜짐' if settings.auto_cleanup_enabled else '꺼짐'}\n"
                f"자동 삭제 유예: {settings.auto_cleanup_days}일\n"
                f"공개 소식 전송: {_bool_label(settings.public_news_lookup_allowed)}\n"
                f"알림 배너: {_banner_display_name(settings.notification_banner)}\n"
                f"이미지 전송: {_image_delivery_label(settings.image_delivery)}\n"
                "치지직 알림: 미설정\n"
                "유튜브 알림: 미설정\n"
                "유튜브 일반 영상 업로드 알림: 미설정\n"
                "햄햄팡팡 소식 알림: 미설정\n"
                "점검 알림: 꺼짐"
            ),
            color=_success_embed_color(),
        )
        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="서버설정상태", description="현재 림피 봇의 알림 설정을 확인합니다.")
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def status(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("서버 안에서만 사용할 수 있어요.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        settings = self.storage.get_settings(interaction.guild_id)
        targets = self.storage.list_news_targets(interaction.guild_id)
        chzzk_target = self.storage.get_chzzk_target(interaction.guild_id)
        youtube_target = self.storage.get_youtube_target(interaction.guild_id)
        youtube_upload_target = self.storage.get_youtube_upload_target(interaction.guild_id)
        hampang_target = self.storage.get_hampang_target(interaction.guild_id)
        try:
            live = await self.chzzk_client.fetch_live()
        except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError) as exc:
            _log_internet_exception("서버 설정 상태용 치지직 방송 확인 실패", exc)
        else:
            if live is None and chzzk_target is not None and chzzk_target.is_live:
                self.storage.mark_chzzk_target_offline(interaction.guild_id)
                chzzk_target = self.storage.get_chzzk_target(interaction.guild_id)
        try:
            youtube_live = await self.youtube_client.fetch_live()
        except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError) as exc:
            _log_internet_exception("서버 설정 상태용 유튜브 방송 확인 실패", exc)
        else:
            if youtube_live is None and youtube_target is not None and youtube_target.is_live:
                self.storage.mark_youtube_target_offline(interaction.guild_id)
                youtube_target = self.storage.get_youtube_target(interaction.guild_id)
        target_languages = sorted({target.language for target in targets})
        if not target_languages:
            target_languages = [settings.language]
        source_status = f"{_news_source_mode_label(settings.news_source_mode)} (" + ", ".join(
            _language_label(language) for language in target_languages
        ) + ")"
        role_text = f"<@&{settings.role_id}>" if settings.role_id else "없음"
        enabled_text = "켜짐" if settings.enabled else "꺼짐"
        maintenance_text = "켜짐" if settings.maintenance_notifications_enabled else "꺼짐"
        cleanup_text = "켜짐" if settings.auto_cleanup_enabled else "꺼짐"
        public_news_send_text = _bool_label(settings.public_news_lookup_allowed)
        image_delivery_text = _image_delivery_label(settings.image_delivery)

        embed = discord.Embed(
            title="서버 설정 상태",
            color=discord.Color.from_rgb(179, 28, 28),
        )
        embed.add_field(
            name="소식 채널",
            value=_format_news_targets(targets),
            inline=False,
        )
        embed.add_field(
            name="기본 설정",
            value=(
                f"기본 언어: {_language_label(settings.language)}\n"
                f"역할 핑: {role_text}\n"
                f"새 게시물 자동 알림: {enabled_text}\n"
                f"알림 배너: {_banner_display_name(settings.notification_banner)}\n"
                f"이미지 전송: {image_delivery_text}"
            ),
            inline=False,
        )
        embed.add_field(
            name="기타",
            value=(
                f"점검 알림: {maintenance_text}\n"
                f"조회 메시지 자동 삭제: {cleanup_text}\n"
                f"자동 삭제 유예: {settings.auto_cleanup_days}일\n"
                f"공개 소식 전송: {public_news_send_text}\n"
                f"뉴스 소스: {source_status}"
            ),
            inline=False,
        )
        embed.add_field(
            name="치지직 알림",
            value=_format_chzzk_target(chzzk_target, settings.role_id),
            inline=False,
        )
        embed.add_field(
            name="유튜브 알림",
            value=_format_youtube_target(youtube_target, settings.role_id),
            inline=False,
        )
        embed.add_field(
            name="유튜브 일반 영상 업로드 알림",
            value=_format_youtube_upload_target(youtube_upload_target, settings.role_id),
            inline=False,
        )
        embed.add_field(
            name="햄햄팡팡 소식",
            value=_format_hampang_target(hampang_target, settings.role_id),
            inline=False,
        )

        await interaction.followup.send(
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="이전소식보기", description="저장된 림버스 컴퍼니 이전 소식을 다시 봅니다.")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.rename(source="소스", title="게시물", private="나만보기", attach_photos="사진첨부")
    @app_commands.describe(
        source="게시물을 가져온 소스입니다.",
        title="게시물 ID나 제목입니다. 비워두면 선택 메뉴를 엽니다.",
        private="켜면 나에게만 보이고, 끄면 채널에 메시지를 보냅니다.",
        attach_photos="켜면 소식에 포함된 이미지를 임베드로 함께 표시합니다.",
    )
    @app_commands.choices(source=NEWS_LOOKUP_SOURCE_CHOICES, private=BOOLEAN_CHOICES, attach_photos=BOOLEAN_CHOICES)
    async def previous_news(
        self,
        interaction: discord.Interaction,
        source: app_commands.Choice[str],
        title: str | None = None,
        private: app_commands.Choice[str] | None = None,
        attach_photos: app_commands.Choice[str] | None = None,
    ) -> None:
        private_value = bool(_choice_bool(private, True))
        attach_photos_value = bool(_choice_bool(attach_photos, True))
        if not await self._allow_public_news_send(interaction, private=private_value):
            return

        language = self._interaction_language(interaction)
        settings = self.storage.get_settings(interaction.guild_id) if interaction.guild_id else None
        if not title:
            if not await self._confirm_external_news_send(interaction):
                return
            posts = await self._selectable_news_posts(
                language=language,
                settings=settings,
                source_mode=source.value,
            )
            await self._send_news_post_select_menu(
                interaction,
                posts,
                mode="previous",
                source_mode=source.value,
                language=language,
                private=private_value,
                attach_photos=attach_photos_value,
            )
            return

        if not await self._confirm_external_news_send(interaction):
            return

        await self._show_previous_news_by_selected_post(
            interaction,
            title,
            source_mode=source.value,
            language=language,
            private=private_value,
            attach_photos=attach_photos_value,
            settings=settings,
        )

    @app_commands.command(name="최근소식보기", description="가장 최근 림버스 컴퍼니 소식을 즉시 확인합니다.")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.rename(private="나만보기", attach_photos="사진첨부")
    @app_commands.describe(
        private="켜면 나에게만 보이고, 끄면 채널에 메시지를 보냅니다.",
        attach_photos="켜면 소식에 포함된 이미지를 임베드로 함께 표시합니다.",
    )
    @app_commands.choices(private=BOOLEAN_CHOICES, attach_photos=BOOLEAN_CHOICES)
    async def recent_news(
        self,
        interaction: discord.Interaction,
        private: app_commands.Choice[str] | None = None,
        attach_photos: app_commands.Choice[str] | None = None,
    ) -> None:
        private_value = bool(_choice_bool(private, True))
        attach_photos_value = bool(_choice_bool(attach_photos, True))
        if not await self._allow_public_news_send(interaction, private=private_value):
            return

        language = self._interaction_language(interaction)
        settings = self.storage.get_settings(interaction.guild_id) if interaction.guild_id else None
        if not await self._confirm_external_news_send(interaction):
            return

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=private_value, thinking=True)

        post = await self._latest_combined_post(language=language, settings=settings)
        if post is None:
            await self._refresh_recent_news_cache(language)
            post = await self._latest_combined_post(language=language, settings=settings)
        else:
            task = asyncio.create_task(self._refresh_recent_news_cache(language))
            task.add_done_callback(self._log_background_task_result)

        if post is None:
            await interaction.followup.send(
                "아직 가져온 소식이 없어요. 잠시 뒤 다시 시도해주세요.",
                ephemeral=True,
            )
            return

        sent_messages = await self._send_news_post_followups(
            interaction,
            post,
            private=private_value,
            attach_photos=attach_photos_value,
        )
        if not private_value:
            for message in sent_messages:
                await self._track_manual_message(
                    interaction.guild_id, interaction.channel_id, message
                )

    @app_commands.command(name="에고기프트", description="거울 던전 에고 기프트 정보를 검색합니다.")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.rename(query="검색어", private="나만보기")
    @app_commands.describe(
        query="검색어입니다. 비워두면 전체 목록을 엽니다.",
        private="켜면 나에게만 보이고, 끄면 채널에 메시지를 보냅니다.",
    )
    @app_commands.choices(private=BOOLEAN_CHOICES)
    async def ego_gift(
        self,
        interaction: discord.Interaction,
        query: str | None = None,
        private: app_commands.Choice[str] | None = None,
    ) -> None:
        private_value = bool(_choice_bool(private, True))
        view = EgoGiftSelectView(
            self,
            interaction.user.id,
            query=query or "",
            private=private_value,
        )
        files = await view.build_response()
        await interaction.response.send_message(
            view=view,
            files=files,
            ephemeral=private_value,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(
        name="소식보내기",
        description="저장된 소식을 지정한 채널에 맨션과 함께 보냅니다.",
    )
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.rename(source="소스", title="게시물", channel="채널", role="역할")
    @app_commands.describe(
        source="게시물을 가져온 소스입니다.",
        title="게시물 ID나 제목입니다. 비워두면 선택 메뉴를 엽니다.",
        channel="보낼 채널입니다. 비워두면 /소식채널설정 채널 전체에 각 채널 언어 버전으로 보냅니다.",
        role="함께 핑할 역할입니다. 비워두면 /서버설정에서 지정한 역할을 사용합니다.",
    )
    @app_commands.choices(source=NEWS_LOOKUP_SOURCE_CHOICES)
    async def send_news(
        self,
        interaction: discord.Interaction,
        source: app_commands.Choice[str],
        title: str | None = None,
        channel: discord.TextChannel | None = None,
        role: discord.Role | None = None,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "서버 안에서만 사용할 수 있어요.", ephemeral=True
            )
            return

        language = self._interaction_language(interaction)
        settings = self.storage.get_settings(interaction.guild_id)
        if not title:
            posts = await self._selectable_news_posts(
                language=language,
                settings=settings,
                source_mode=source.value,
            )
            await self._send_news_post_select_menu(
                interaction,
                posts,
                mode="send",
                source_mode=source.value,
                language=language,
                channel_id=channel.id if channel else None,
                role_id=role.id if role else None,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        await self._send_news_by_selected_post(
            interaction,
            title,
            source_mode=source.value,
            channel_id=channel.id if channel else None,
            role_id=role.id if role else None,
        )

    @app_commands.command(name="명령어", description="림피의 모든 명령어 사용법을 봅니다.")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def list_commands(self, interaction: discord.Interaction) -> None:
        view = discord.ui.LayoutView(timeout=None)
        container = discord.ui.Container(accent_color=discord.Color.from_rgb(179, 28, 28))

        container.add_item(
            discord.ui.Section(
                discord.ui.TextDisplay(
                    "## 림피 명령어 안내\n"
                    "-# 림버스 컴퍼니 관련 소식을 확인할 필요없이 디스코드 채널에 전해주는 봇이에요!\n"
                    "-# TMI: 봇 프사랑 배너가 왜 홍루냐면요.. 단순히 제작자 최애라서..ㅎ"
                ),
                accessory=discord.ui.Thumbnail(
                    media=f"attachment://{COMMAND_GUIDE_IMAGE_NAME}"
                ),
            )
        )
        container.add_item(discord.ui.Separator())

        container.add_item(
            discord.ui.TextDisplay(
                "### 📰 **소식 보기관련 **\n"
                "> `/최근소식보기` — 설정한 언어의 최신 소식을 즉시 조회\n"
                "> `/이전소식보기` — 저장된 이전 소식 다시 보기\n"
                "> `/소식보내기` — 저장된 소식을 채널에 맨션과 함께 전송"
            )
        )
        container.add_item(discord.ui.Separator())

        container.add_item(
            discord.ui.TextDisplay(
                "### 📢 **소식 채널 · 자동 알림**\n"
                "> `/소식채널설정` — 언어별 자동 소식 채널 등록\n"
                "> `/소식채널해제` — 자동 소식 채널 등록 해제\n"
                "> `/점검알림설정` — 주간 점검 · 업데이트 알림 설정\n"
                "> `/유튜브알림설정` — 일반 영상 업로드 알림 설정\n"
                "> `/유튜브알림해제` — 일반 영상 업로드 알림 해제\n"
                "> `/유튜브알림현황보기` — 최근 일반 영상과 알림 현황 보기\n"
                "> `/유튜브알림보내기` — 최근 일반 영상을 지정 채널에 전송\n"
                "> `/햄팡소식알림설정` — 햄햄팡팡 자동 소식 알림 설정\n"
                "> `/햄팡소식알림해제` — 햄햄팡팡 자동 소식 알림 해제\n"
                "> `/햄팡소식알림현황보기` — 햄햄팡팡 자동 소식 알림 현황 보기\n"
                "> `/햄팡최근소식보기` — 최신 햄햄팡팡 소식 확인\n"
                "> `/햄팡이전소식보기` — 최근 수집한 햄햄팡팡 소식을 골라서 확인\n"
                "> `/햄팡소식보내기` — 햄햄팡팡 소식을 골라서 채널에 전송"
            )
        )
        container.add_item(discord.ui.Separator())

        container.add_item(
            discord.ui.TextDisplay(
                "### 📡 **방송 알림**\n"
                "> `/방송알림설정` — 방송 시작 알림 설정\n"
                "> `/방송알림해제` — 방송 알림 해제\n"
                "> `/방송현황보기` — 치지직 · 유튜브 방송 현황 보기\n"
                "> `/방송알림보내기` — 현재 방송을 지정 채널에 전송"
            )
        )
        container.add_item(discord.ui.Separator())

        container.add_item(
            discord.ui.TextDisplay(
                "### ⚙️ **서버 설정 · 관리**\n"
                "> `/서버설정` — 역할 · 자동 알림 · 언어 · 배너 등 종합 설정\n"
                "> `/유저설정` — 앱 사용 시 개인 언어 · 배너 설정\n"
                "> `/서버설정상태` — 현재 서버 설정 보기\n"
                "> `/서버설정초기화` — 설정 · 읽음 기준선 초기화\n"
                "> `/역할핑해제` — 새 소식 알림 역할 핑 제거\n"
                "> `/서버동기화` — DB 등록 · 명령어 준비 확인"
            )
        )
        container.add_item(discord.ui.Separator())

        container.add_item(
            discord.ui.TextDisplay(
                "### ℹ️ **기타**\n"
                "> `/에고기프트` — 거울 던전 에고 기프트 정보 검색\n"
                "> `/명령어` — 이 안내 보기\n"
                "> `/크레딧` — 림피봇 제작 크레딧 보기\n"
                "-# 에고 기프트 데이터는 나무위키를 참고하여 만들었습니다."
            )
        )
        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.TextDisplay(f"-# 한 번에 가져오는 소식 수: 최대 {NEWS_POST_LIMIT}개")
        )

        view.add_item(container)

        guide_image = discord.File(
            _resource_path(NEWS_BANNER_DIR / COMMAND_GUIDE_IMAGE_NAME),
            filename=COMMAND_GUIDE_IMAGE_NAME,
        )
        await interaction.response.send_message(
            view=view, files=[guide_image], ephemeral=True
        )

    @app_commands.command(name="크레딧", description="림피 제작 크레딧을 봅니다.")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def credits(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="크레딧",
            description=(
                "림피(Limpi) 봇 By. 2P\n"
                "알림 배너 그림 By. @gamstergd7\n"
                "에고 기프트 데이터 참고: 나무위키\n"
                "치지직 알림 구현 참고: [junah201/chzzk-discord-bot](https://github.com/junah201/chzzk-discord-bot)"
            ),
            color=discord.Color.from_rgb(179, 28, 28),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="서버동기화", description="현재 서버를 림피 DB에 등록하고 명령어 사용 준비 상태를 확인합니다.")
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def sync_news(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None or interaction.guild is None:
            await interaction.response.send_message(
                "서버 안에서만 사용할 수 있어요.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        settings, created = self.storage.ensure_guild_settings(interaction.guild_id)
        targets = self.storage.list_news_targets(interaction.guild_id)
        chzzk_target = self.storage.get_chzzk_target(interaction.guild_id)
        youtube_target = self.storage.get_youtube_target(interaction.guild_id)

        try:
            synced_commands = await self.bot.tree.sync()
        except discord.HTTPException:
            LOGGER.exception(
                "서버 명령어 준비 중 글로벌 명령어 동기화 실패 (guild_id=%s).",
                interaction.guild_id,
            )
            synced_commands = []
            command_status = "명령어 동기화 확인 실패 (콘솔 로그를 확인해주세요)"
        else:
            command_status = f"명령어 {len(synced_commands)}개 동기화 확인 완료"

        role_text = f"<@&{settings.role_id}>" if settings.role_id else "없음"
        target_text = _format_news_targets(targets)
        status_text = "새로 등록됨" if created else "이미 등록됨"
        embed = discord.Embed(
            title="서버 동기화가 완료되었어요",
            description=(
                "현재 서버를 림피 DB에 등록하고 명령어 사용 준비 상태를 확인했어요.\n"
                "이 명령어는 Steam 소식을 새로 불러오지 않아요."
            ),
            color=_success_embed_color(),
        )
        embed.add_field(
            name="서버 DB",
            value=(
                f"상태: {status_text}\n"
                f"서버: {interaction.guild.name} (`{interaction.guild_id}`)"
            ),
            inline=False,
        )
        embed.add_field(
            name="명령어 준비",
            value=command_status,
            inline=False,
        )
        embed.add_field(
            name="현재 설정",
            value=(
                f"언어별 소식 채널\n{target_text}\n"
                f"기본 언어: {_language_label(settings.language)}\n"
                f"역할 핑: {role_text}\n"
                f"새 게시물 자동 알림: {'켜짐' if settings.enabled else '꺼짐'}\n"
                f"알림 배너: {_banner_display_name(settings.notification_banner)}"
            ),
            inline=False,
        )
        embed.add_field(
            name="치지직 알림",
            value=_format_chzzk_target(chzzk_target, settings.role_id),
            inline=False,
        )
        embed.add_field(
            name="유튜브 알림",
            value=_format_youtube_target(youtube_target, settings.role_id),
            inline=False,
        )
        embed.add_field(
            name="다음 설정",
            value=(
                "자동 소식 채널은 `/소식채널설정`으로 등록해주세요.\n"
                "역할 핑, 기본 언어, 공개 소식 전송, 알림 배너는 `/서버설정`에서 바꿀 수 있어요."
            ),
            inline=False,
        )
        await interaction.followup.send(
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )




async def main() -> None:
    _base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _log_dir = os.path.join(_base, "logs")
    os.makedirs(_log_dir, exist_ok=True)
    _now = datetime.now()
    _log_file = os.path.join(
        _log_dir,
        f"limpi_{_now.strftime('%Y-%m-%d')}-{_now.hour}_{_now.strftime('%M_%S')}.log",
    )
    _debug_log_file = os.path.join(
        _log_dir,
        f"limpi_{_now.strftime('%Y-%m-%d')}-{_now.hour}_{_now.strftime('%M_%S')}-debug.log",
    )
    _fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    _log_level = _log_level_from_env()
    _file_handler = logging.FileHandler(_log_file, encoding="utf-8")
    _file_handler.setLevel(_log_level)
    _file_handler.setFormatter(_fmt)
    _debug_file_handler = logging.FileHandler(_debug_log_file, encoding="utf-8")
    _debug_file_handler.setLevel(logging.DEBUG)
    _debug_file_handler.setFormatter(_fmt)
    import io as _io
    _stdout_stream = (
        _io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
        if hasattr(sys.stdout, "buffer")
        else sys.stdout
    )
    _console_handler = logging.StreamHandler(_stdout_stream)
    _console_handler.setLevel(_log_level)
    _console_handler.setFormatter(_fmt)
    _log_queue: queue.Queue[logging.LogRecord] = queue.Queue()
    _queue_handler = logging.handlers.QueueHandler(_log_queue)
    _queue_handler.setLevel(logging.DEBUG)
    _log_listener = logging.handlers.QueueListener(
        _log_queue,
        _file_handler,
        _debug_file_handler,
        _console_handler,
        respect_handler_level=True,
    )
    _log_listener.start()
    logging.basicConfig(
        level=logging.DEBUG,
        handlers=[_queue_handler],
        force=True,
    )
    logging.captureWarnings(True)
    LOGGER.info("로그 파일: %s (level=%s)", _log_file, logging.getLevelName(_log_level))
    LOGGER.info("디버그 로그 파일: %s", _debug_log_file)

    class _DropExpiredInteraction(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            if record.exc_info:
                exc = record.exc_info[1]
                original = getattr(exc, "original", exc)
                if isinstance(original, discord.errors.NotFound) and original.code == 10062:
                    return False
            return "10062" not in record.getMessage()

    class _DropDiscordReconnectDnsError(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            if record.name != "discord.client" or "Attempting a reconnect" not in record.getMessage():
                return True
            if not record.exc_info:
                return True
            return not isinstance(record.exc_info[1], aiohttp.ClientConnectorDNSError)

    logging.getLogger("discord.app_commands.tree").addFilter(_DropExpiredInteraction())
    logging.getLogger("discord.client").addFilter(_DropExpiredInteraction())
    logging.getLogger("discord.client").addFilter(_DropDiscordReconnectDnsError())
    test_mode = "--test" in sys.argv
    if test_mode:
        LOGGER.info("테스트 모드: DISCORD_TOKEN_TEST 토큰으로 실행합니다.")
    config = AppConfig.from_env(test=test_mode)
    storage = SQLiteStorage(config.database_path)
    session: aiohttp.ClientSession | None = None
    bot: LimpiBot | None = None
    bot_task: asyncio.Task[None] | None = None
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    _install_asyncio_exception_handler(loop)

    def _request_shutdown(signum: int, _frame: object | None = None) -> None:
        LOGGER.info("종료 신호를 받았습니다 (signal=%s). Discord 상태를 오프라인으로 전환합니다.", signum)
        loop.call_soon_threadsafe(stop_event.set)

    previous_signal_handlers: dict[int, object] = {}
    for signum in (getattr(signal, "SIGBREAK", None), signal.SIGTERM, signal.SIGINT):
        if signum is None:
            continue
        try:
            previous_signal_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, _request_shutdown)
        except (ValueError, OSError):
            continue

    sleep_prevented = _prevent_windows_sleep()
    try:
        connector = _build_aiohttp_connector()
        session = aiohttp.ClientSession(connector=connector)
        news_source = build_news_source(config, session)
        x_source = LimbusXClient(config, session)
        bot = LimpiBot(config)

        cog = NewsCog(bot, config, storage, news_source, x_source, session, test_mode=test_mode)

        @bot.event
        async def on_ready() -> None:
            LOGGER.info("림피 v%s — %s (%s)로 로그인했습니다.", BOT_VERSION, bot.user, bot.user.id if bot.user else "unknown")
            await cog.update_presence_status(show_servers=True)
            if not bot._pruned_disconnected_guild_data:
                cog.prune_disconnected_guild_data()
                bot._pruned_disconnected_guild_data = True
            if not bot._logged_startup_summary:
                cog.log_startup_summary()
                bot._logged_startup_summary = True
            startup_sync_task = asyncio.create_task(cog.run_startup_sync())
            await bot.sync_connected_guild_commands()
            await startup_sync_task
            await cog._process_maintenance_notifications(source="startup")

        @bot.event
        async def on_guild_join(guild: discord.Guild) -> None:
            await cog.update_presence_status(show_servers=True)
            LOGGER.info(
                "서버 참가: name=%s, guild_id=%s, owner_id=%s, member_count=%s",
                guild.name,
                guild.id,
                guild.owner_id,
                guild.member_count,
            )

            settings, created = storage.ensure_guild_settings(guild.id)
            if created:
                LOGGER.info(
                    "참가한 서버를 DB에 등록했습니다: guild=%s (%s)",
                    guild.name,
                    guild.id,
                )
            if settings.channel_id:
                channel = bot.get_channel(settings.channel_id) if settings.channel_id else None
                channel_name = getattr(channel, "name", None) or "unknown"
                role = guild.get_role(settings.role_id) if settings.role_id else None
                LOGGER.info(
                    "참가한 서버에 저장된 설정이 있습니다: guild=%s (%s), news_enabled=%s, "
                    "maintenance_enabled=%s, channel=%s (%s), role=%s (%s), language=%s",
                    guild.name,
                    guild.id,
                    settings.enabled,
                    settings.maintenance_notifications_enabled,
                    channel_name,
                    settings.channel_id or "none",
                    role.name if role else "none",
                    settings.role_id or "none",
                    settings.language,
                )
            else:
                LOGGER.info(
                    "참가한 서버에 알림 채널이 아직 설정되지 않았습니다: guild=%s (%s)",
                    guild.name,
                    guild.id,
                )

        @bot.event
        async def on_guild_remove(guild: discord.Guild) -> None:
            await cog.update_presence_status(show_servers=True)
            LOGGER.info(
                "서버 퇴장: name=%s, guild_id=%s — DB 데이터 삭제.",
                guild.name,
                guild.id,
            )
            storage.delete_guild_data(guild.id)

        await bot.add_cog(cog)
        bot_task = asyncio.create_task(bot.start(config.discord_token))
        try:
            stop_task = asyncio.create_task(stop_event.wait())
            done, pending = await asyncio.wait(
                {bot_task, stop_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            if stop_task in done and not bot_task.done():
                LOGGER.info("봇 종료 요청 처리 중: Discord 연결을 닫습니다.")
            else:
                await bot_task
        except asyncio.CancelledError:
            LOGGER.info("종료 요청을 받아 봇을 정리합니다.")
    finally:
        if bot is not None and not bot.is_closed():
            try:
                await bot.change_presence(status=discord.Status.invisible, activity=None)
            except Exception:
                LOGGER.debug("종료 전 오프라인 상태 전환을 건너뜁니다.", exc_info=True)
            await bot.close()
        if bot_task is not None and not bot_task.done():
            try:
                await asyncio.wait_for(bot_task, timeout=5)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        if session is not None and not session.closed:
            await session.close()
        if sleep_prevented:
            _restore_windows_sleep()
        storage.close()
        for signum, handler in previous_signal_handlers.items():
            try:
                signal.signal(signum, handler)
            except (ValueError, OSError):
                pass
        _log_listener.stop()


if __name__ == "__main__":
    _install_windows_selector_event_loop_policy()
    asyncio.run(main())
