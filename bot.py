from __future__ import annotations

import asyncio
import gc
import io
import logging
import logging.handlers
import os
import sys
import re
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter
from urllib.parse import urlparse

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import AppConfig
from models import GuildNewsTarget, GuildSettings, NewsPost
from storage import MAX_CLEANUP_DAYS, MIN_CLEANUP_DAYS, SQLiteStorage
from steam_client import NewsSource, build_news_source


POST_FORMAT_RICH = "rich"
LOGGER = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))
NEWS_POST_LIMIT = 30
USER_COMMAND_COOLDOWN_SECONDS = 3.0
ZIP_CUSTOM_ID_PREFIX = "limpi:zip:"
ZIP_IMAGE_CONCURRENCY = 10
ZIP_CACHE_MAX_ITEMS = 8
IMAGE_CACHE_MAX_ITEMS = 64
IMAGE_CACHE_MAX_BYTES = 64 * 1024 * 1024
IMAGE_CACHE_MAX_ITEM_BYTES = 4 * 1024 * 1024
IMAGE_CACHE_WARM_POST_LIMIT = 5
NEWS_BANNER_PATH = Path("img") / "banner.png"
NEWS_BANNER_ATTACHMENT_NAME = "limpi_news_banner.png"
YOUTUBE_PLACEHOLDER_IMAGE_FRAGMENT = "youtube_16x9_placeholder.gif"
LEGACY_STEAM_CARD_THUMBNAIL_FRAGMENTS = (
)
EMBEDS_PER_MESSAGE = 10
IMAGE_ONLY_EMBEDS_PER_MESSAGE = 4
EMBED_DESCRIPTION_LIMIT = 8096
FILES_PER_MESSAGE = 10
BOOLEAN_TRUE = "true"
BOOLEAN_FALSE = "false"
BOOLEAN_CHOICES = [
    app_commands.Choice(name="허용", value=BOOLEAN_TRUE),
    app_commands.Choice(name="비허용", value=BOOLEAN_FALSE),
]
LANGUAGE_CHOICES = [
    app_commands.Choice(name="한국어", value="koreana"),
    app_commands.Choice(name="English", value="english"),
    app_commands.Choice(name="日本語", value="japanese"),
]
IMAGE_DELIVERY_FILES = "files"
LANGUAGE_LABELS = {
    "koreana": "한국어",
    "english": "English",
    "japanese": "日本語",
}
NEWS_UI_TEXT = {
    "koreana": {
        "schedule": "일정",
        "original": "원문 보기",
        "download_images": "이미지 다운로드",
        "updated": "-# 🔄 수정된 소식입니다.",
        "zip_unavailable": "지금은 다운로드를 처리할 수 없어요.",
        "zip_no_images": "이 게시물에는 이미지가 없어요.",
        "zip_fetch_failed": "이미지를 가져오는 중 문제가 생겼어요. 잠시 후 다시 시도해주세요.",
        "zip_empty": "이미지를 다운로드하지 못했어요.",
        "zip_ready": "이미지 {count}장을 압축했어요.",
    },
    "english": {
        "schedule": "Schedule",
        "original": "View original",
        "download_images": "Download images",
        "updated": "-# 🔄 This news was updated.",
        "zip_unavailable": "Downloads are unavailable right now.",
        "zip_no_images": "This post has no images.",
        "zip_fetch_failed": "Something went wrong while fetching the images. Please try again later.",
        "zip_empty": "Could not download the images.",
        "zip_ready": "Compressed {count} images.",
    },
    "japanese": {
        "schedule": "日程",
        "original": "原文を見る",
        "download_images": "画像をダウンロード",
        "updated": "-# 🔄 このお知らせは更新されました。",
        "zip_unavailable": "現在、ダウンロードを処理できません。",
        "zip_no_images": "この投稿には画像がありません。",
        "zip_fetch_failed": "画像の取得中に問題が発生しました。しばらくしてからもう一度お試しください。",
        "zip_empty": "画像をダウンロードできませんでした。",
        "zip_ready": "画像{count}枚を圧縮しました。",
    },
}
SYNC_LANGUAGES = ("koreana", "english", "japanese")
MAINTENANCE_WEEKDAY = 3
MAINTENANCE_START_HOUR = 10
MAINTENANCE_UPDATE_HOUR = 12
MAINTENANCE_START_TITLE = "림버스 컴퍼니 점검 알림"
MAINTENANCE_START_DESCRIPTION = (
    "지금부터 림버스 컴퍼니가 점검에 들어가요! "
    "관리자 분들은 점검이 끝날때까지 기다리시면 될거에요! <3"
)
MAINTENANCE_UPDATE_TITLE = "림버스 컴퍼니 업데이트"
MAINTENANCE_UPDATE_DESCRIPTION = (
    "지금 림버스 컴퍼니가 점검이 끝나고 업데이트가 되었어요! "
    "스팀에 들어가서 림버스를 업데이트 해주세요! <3"
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
        command_name = str(data.get("name") or "unknown")
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
            user = interaction.user
            guild = interaction.guild
            LOGGER.info(
                "명령어 %s — 사용자: %s (%s), 서버: %s (%s), 결과: %s, 소요시간: %.3f초.",
                command_name,
                user,
                user.id,
                guild.name if guild else "DM",
                interaction.guild_id or "DM",
                status,
                elapsed,
            )

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


class LimpiBot(commands.Bot):
    def __init__(self, config: AppConfig) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents, tree_cls=LoggingCommandTree)
        self.config = config
        self._synced_connected_guilds = False
        self._cleared_global_commands = False
        self._cleared_connected_guild_commands = False
        self._logged_startup_summary = False

    async def setup_hook(self) -> None:
        self.add_dynamic_items(ZipDownloadButton)
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


class ZipDownloadButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"limpi:zip:(?P<post_id>.+)",
):
    def __init__(self, post_id: str, *, language: str = "koreana") -> None:
        super().__init__(
            discord.ui.Button(
                label=_news_ui_text(language, "download_images"),
                style=discord.ButtonStyle.primary,
                custom_id=f"{ZIP_CUSTOM_ID_PREFIX}{post_id}",
                emoji="🗂️",
            )
        )
        self.post_id = post_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["post_id"])

    async def callback(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("NewsCog")
        if not isinstance(cog, NewsCog):
            await interaction.response.send_message(
                _news_ui_text("koreana", "zip_unavailable"), ephemeral=True
            )
            return
        await cog.handle_zip_request(interaction, self.post_id)


class ExternalNewsSendConfirmView(discord.ui.View):
    def __init__(self, author_id: int) -> None:
        super().__init__(timeout=30)
        self.author_id = author_id
        self.confirmed: bool | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.author_id:
            return True

        await interaction.response.send_message(
            "이 확인 버튼은 명령어를 실행한 사람만 누를 수 있어요.",
            ephemeral=True,
        )
        return False

    @discord.ui.button(label="네!", style=discord.ButtonStyle.danger)
    async def confirm(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        self.confirmed = True
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="아니요...생각해볼깨요", style=discord.ButtonStyle.secondary)
    async def cancel(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        self.confirmed = False
        await interaction.response.defer()
        self.stop()


class NewsCog(commands.Cog):
    def __init__(
        self,
        bot: LimpiBot,
        config: AppConfig,
        storage: SQLiteStorage,
        news_source: NewsSource | None,
        session: aiohttp.ClientSession,
    ) -> None:
        self.bot = bot
        self.config = config
        self.storage = storage
        self.news_source = news_source
        self.session = session
        self._poll_lock = asyncio.Lock()
        self._zip_cache: dict[str, tuple[bytes, int]] = {}
        self._image_cache: dict[str, tuple[bytes, str | None]] = {}
        self._image_cache_bytes: int = 0
        self._last_poll_at: datetime | None = None
        self._startup_synced = False
        self._in_high_frequency_window: bool = False

    async def cog_load(self) -> None:
        self.maintenance_notifications.start()
        self.cleanup_messages.start()

        if self.news_source is None:
            LOGGER.warning("Steam 뉴스 소스가 설정되지 않아 뉴스 폴링을 비활성화합니다.")
            return

        self.poll_news.start()

    async def cog_unload(self) -> None:
        if self.poll_news.is_running():
            self.poll_news.cancel()
        self.maintenance_notifications.cancel()
        self.cleanup_messages.cancel()

    def log_startup_summary(self) -> None:
        connected_guild_ids = {guild.id for guild in self.bot.guilds}
        LOGGER.info(
            "연결된 서버 요약: count=%s guilds=%s",
            len(self.bot.guilds),
            ", ".join(
                f"{guild.name} ({guild.id})"
                for guild in sorted(self.bot.guilds, key=lambda item: item.id)
            )
            or "none",
        )

        settings_list = self.storage.list_settings()
        news_targets = self.storage.list_all_news_targets()
        notification_settings = [
            settings
            for settings in settings_list
            if (
                (settings.enabled and any(target.guild_id == settings.guild_id for target in news_targets))
                or (settings.channel_id and settings.maintenance_notifications_enabled)
            )
        ]
        LOGGER.info(
            "알림 설정 요약: configured_guilds=%s active_targets=%s news_targets=%s",
            len(settings_list),
            len(notification_settings),
            len(news_targets),
        )

        for settings in notification_settings:
            guild = self.bot.get_guild(settings.guild_id)
            guild_name = guild.name if guild else "not connected"
            channel = self.bot.get_channel(settings.channel_id) if settings.channel_id else None
            channel_name = getattr(channel, "name", None) or "unknown"
            role_name = "none"
            if guild is not None and settings.role_id is not None:
                role = guild.get_role(settings.role_id)
                role_name = role.name if role else "unknown"

            LOGGER.info(
                "알림 대상: guild=%s (%s), connected=%s, "
                "news_enabled=%s, missed_recovery_enabled=%s, maintenance_enabled=%s, "
                "channel=%s (%s), role=%s (%s), language=%s, image_delivery=%s",
                guild_name,
                settings.guild_id,
                settings.guild_id in connected_guild_ids,
                settings.enabled,
                settings.missed_news_recovery_enabled,
                settings.maintenance_notifications_enabled,
                channel_name,
                settings.channel_id,
                role_name,
                settings.role_id or "none",
                settings.language,
                settings.image_delivery,
            )

        for target in news_targets:
            if target.guild_id not in connected_guild_ids:
                continue
            guild = self.bot.get_guild(target.guild_id)
            channel = self.bot.get_channel(target.channel_id)
            LOGGER.info(
                "뉴스 언어별 대상: guild=%s (%s), channel=%s (%s), language=%s",
                guild.name if guild else "not connected",
                target.guild_id,
                getattr(channel, "name", None) or "unknown",
                target.channel_id,
                target.language,
            )

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

    @tasks.loop(seconds=60)
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

                if not self._should_poll_now(now):
                    return
                self._last_poll_at = now
                await self._poll_once()
                self._startup_synced = True
            except Exception:
                LOGGER.exception("뉴스 폴링 실패.")

    @poll_news.before_loop
    async def before_poll_news(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=60)
    async def maintenance_notifications(self) -> None:
        try:
            await self._process_maintenance_notifications()
        except Exception:
            LOGGER.exception("점검 알림 처리 실패.")

    @maintenance_notifications.before_loop
    async def before_maintenance_notifications(self) -> None:
        await self.bot.wait_until_ready()

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
        await self.bot.wait_until_ready()

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

    async def _process_maintenance_notifications(self) -> None:
        notice_type, notice_key = _current_maintenance_notice()
        if notice_type is None or notice_key is None:
            return

        for settings in self.storage.list_settings():
            if (
                not settings.maintenance_notifications_enabled
                or settings.channel_id is None
                or self.bot.get_guild(settings.guild_id) is None
            ):
                continue

            if notice_type == "start":
                if settings.last_maintenance_start_notice == notice_key:
                    continue
                embed = _maintenance_embed(
                    MAINTENANCE_START_TITLE,
                    MAINTENANCE_START_DESCRIPTION,
                )
            else:
                if settings.last_maintenance_update_notice == notice_key:
                    continue
                embed = _maintenance_embed(
                    MAINTENANCE_UPDATE_TITLE,
                    MAINTENANCE_UPDATE_DESCRIPTION,
                )

            sent = await self._send_maintenance_notice(settings, embed, notice_type)
            if sent:
                self.storage.mark_maintenance_notice_sent(
                    settings.guild_id,
                    notice_type=notice_type,
                    notice_key=notice_key,
                )

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

        try:
            await channel.send(
                embed=embed,
                allowed_mentions=discord.AllowedMentions.none(),
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
            "점검 알림 전송 완료 (guild_id=%s, channel_id=%s, notice_type=%s).",
            settings.guild_id,
            settings.channel_id,
            notice_type,
        )
        return True

    async def _poll_once(self) -> int:
        if self.news_source is None:
            return 0

        posts_by_language, changed_post_ids = await self._sync_global_news_cache()
        targets_by_language = self._news_targets_by_language()

        announced_count = 0
        for language, target_list in targets_by_language.items():
            posts = posts_by_language.get(language, [])
            if not posts:
                continue

            for target in target_list:
                guild_posts = posts[:NEWS_POST_LIMIT]
                if not guild_posts:
                    continue
                newest_post_id = guild_posts[0].post_id
                fetched_post_ids = [post.post_id for post in guild_posts]
                settings = self.storage.get_settings(target.guild_id)
                announced_count += await self._process_news_target(settings, target, guild_posts)
                if not settings.enabled:
                    self.storage.mark_news_target_posts_seen(target.target_id, fetched_post_ids)
                    self.storage.mark_posts_seen(target.guild_id, fetched_post_ids)
                    self.storage.set_last_seen_post_id(target.guild_id, newest_post_id)

        if changed_post_ids:
            await self._broadcast_post_updates(changed_post_ids)
        return announced_count

    async def _sync_global_news_cache(self) -> tuple[dict[str, list[NewsPost]], list[str]]:
        if self.news_source is None:
            return {}, []

        posts_by_language: dict[str, list[NewsPost]] = {}
        all_posts: list[NewsPost] = []
        for language in SYNC_LANGUAGES:
            posts = await self.news_source.fetch_recent_posts(language, limit=NEWS_POST_LIMIT)
            posts_by_language[language] = posts[:NEWS_POST_LIMIT]
            all_posts.extend(posts_by_language[language])

        if all_posts:
            _, changed = self.storage.save_posts(all_posts)
            self._schedule_image_cache_warmup(all_posts)
        return posts_by_language, changed

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
        return IMAGE_DELIVERY_FILES

    def _post_variant_for_language(
        self,
        post: NewsPost,
        language: str,
    ) -> NewsPost | None:
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

    def _current_poll_interval_seconds(self, now: datetime) -> int:
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
            return 0

        new_posts = self._new_posts_for_news_target(settings, target, posts)
        if not new_posts:
            self.storage.mark_news_target_posts_seen(
                target.target_id,
                (post.post_id for post in posts),
            )
            self.storage.mark_posts_seen(
                target.guild_id,
                (post.post_id for post in posts),
            )
            self.storage.set_last_seen_post_id(target.guild_id, posts[0].post_id)
            return 0

        announced = 0
        failed_post_ids: set[str] = set()
        for post in new_posts:
            sent = await self._send_news_post_to_target(channel, settings, target, post)
            if not sent:
                failed_post_ids.add(post.post_id)
                LOGGER.warning(
                    "뉴스 자동 전송 실패 "
                    "(guild_id=%s, channel_id=%s, language=%s, post_id=%s, title=%r, "
                    "missed_recovery_enabled=%s).",
                    target.guild_id,
                    target.channel_id,
                    target.language,
                    post.post_id,
                    post.title,
                    settings.missed_news_recovery_enabled,
                )
                continue
            self.storage.mark_news_target_posts_seen(
                target.target_id,
                [post.post_id],
                announced=True,
            )
            self.storage.mark_posts_seen(target.guild_id, [post.post_id], announced=True)
            LOGGER.info(
                "새 뉴스 공지 (guild %s, channel %s, language %s): %s",
                target.guild_id,
                target.channel_id,
                target.language,
                post.title,
            )
            announced += 1

        if failed_post_ids and not settings.missed_news_recovery_enabled:
            LOGGER.warning(
                "누락 뉴스 복구가 비활성화되어 있습니다. 실패한 게시물은 해당 대상에서 본 것으로 처리되어 "
                "이후 자동 폴링에서 건너뜁니다 (guild_id=%s, channel_id=%s, post_ids=%s).",
                target.guild_id,
                target.channel_id,
                ", ".join(sorted(failed_post_ids)),
            )
        seen_posts = [
            post.post_id
            for post in posts
            if not settings.missed_news_recovery_enabled or post.post_id not in failed_post_ids
        ]
        self.storage.mark_news_target_posts_seen(target.target_id, seen_posts)
        self.storage.mark_posts_seen(target.guild_id, seen_posts)
        if not failed_post_ids or not settings.missed_news_recovery_enabled:
            self.storage.set_last_seen_post_id(target.guild_id, posts[0].post_id)
        return announced

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
                return 0
            except discord.NotFound as exc:
                LOGGER.warning(
                    "뉴스 자동 전송 건너뜀: 설정된 채널을 찾을 수 없음 "
                    "(guild_id=%s, channel_id=%s, discord_code=%s). /서버설정을 다시 실행하세요.",
                    settings.guild_id,
                    settings.channel_id,
                    getattr(exc, "code", None),
                )
                return 0
            except discord.HTTPException as exc:
                LOGGER.exception(
                    "뉴스 자동 전송 건너뜀: 설정된 채널 조회 실패 "
                    "(guild_id=%s, channel_id=%s, discord_status=%s, discord_code=%s).",
                    settings.guild_id,
                    settings.channel_id,
                    getattr(exc, "status", None),
                    getattr(exc, "code", None),
                )
                return 0

        if not isinstance(channel, discord.abc.Messageable):
            LOGGER.warning(
                "뉴스 자동 전송 건너뜀: 설정된 채널에 메시지를 보낼 수 없음 "
                "(guild_id=%s, channel_id=%s, channel_type=%s).",
                settings.guild_id,
                settings.channel_id,
                type(channel).__name__,
            )
            return 0

        new_posts = self._new_posts_for_guild(settings, posts)
        if not new_posts:
            self.storage.mark_posts_seen(
                settings.guild_id,
                (post.post_id for post in posts),
            )
            self.storage.set_last_seen_post_id(settings.guild_id, posts[0].post_id)
            return 0

        announced = 0
        failed_post_ids: set[str] = set()
        for post in new_posts:
            sent = await self._send_news_post(channel, settings, post)
            if not sent:
                failed_post_ids.add(post.post_id)
                LOGGER.warning(
                    "뉴스 자동 전송 실패 "
                    "(guild_id=%s, channel_id=%s, post_id=%s, title=%r, "
                    "missed_recovery_enabled=%s).",
                    settings.guild_id,
                    settings.channel_id,
                    post.post_id,
                    post.title,
                    settings.missed_news_recovery_enabled,
                )
                continue
            self.storage.mark_posts_seen(settings.guild_id, [post.post_id], announced=True)
            LOGGER.info("새 뉴스 공지 (guild %s): %s", settings.guild_id, post.title)
            announced += 1

        if failed_post_ids and not settings.missed_news_recovery_enabled:
            LOGGER.warning(
                "누락 뉴스 복구가 비활성화되어 있습니다. 실패한 게시물은 본 것으로 처리되어 "
                "이후 자동 폴링에서 건너뜁니다 (guild_id=%s, channel_id=%s, post_ids=%s).",
                settings.guild_id,
                settings.channel_id,
                ", ".join(sorted(failed_post_ids)),
            )
        seen_posts = [
            post.post_id
            for post in posts
            if not settings.missed_news_recovery_enabled or post.post_id not in failed_post_ids
        ]
        self.storage.mark_posts_seen(
            settings.guild_id,
            seen_posts,
        )
        if not failed_post_ids or not settings.missed_news_recovery_enabled:
            self.storage.set_last_seen_post_id(settings.guild_id, posts[0].post_id)
        return announced

    def _new_posts_for_guild(
        self, settings: GuildSettings, posts_newest_first: list[NewsPost]
    ) -> list[NewsPost]:
        fetched_post_ids = [post.post_id for post in posts_newest_first]
        has_seen_baseline = self.storage.has_seen_posts(settings.guild_id)
        seen_post_ids = self.storage.get_seen_post_ids(settings.guild_id, fetched_post_ids)
        if seen_post_ids:
            return [
                post
                for post in reversed(posts_newest_first)
                if post.post_id not in seen_post_ids
            ]

        if not has_seen_baseline and not self.config.announce_existing_on_first_run:
            LOGGER.info(
                "서버 %s의 뉴스 기준선을 초기화했습니다 (게시물 %s개). 이전 게시물은 공지되지 않습니다.",
                settings.guild_id,
                len(fetched_post_ids),
            )
            return []

        if settings.last_seen_post_id is None:
            if self.config.announce_existing_on_first_run:
                return list(reversed(posts_newest_first))
            return []

        ids = [post.post_id for post in posts_newest_first]
        if settings.last_seen_post_id in ids:
            index = ids.index(settings.last_seen_post_id)
            return list(reversed(posts_newest_first[:index]))

        last_seen_post = self.storage.get_post(settings.last_seen_post_id)
        if last_seen_post and last_seen_post.created_at:
            return [
                post
                for post in reversed(posts_newest_first)
                if post.created_at and post.created_at > last_seen_post.created_at
            ]

        if settings.last_seen_post_id.isdigit():
            last_seen_id = int(settings.last_seen_post_id)
            return [
                post
                for post in reversed(posts_newest_first)
                if post.post_id.isdigit() and int(post.post_id) > last_seen_id
            ]

        return []

    def _new_posts_for_news_target(
        self,
        settings: GuildSettings,
        target: GuildNewsTarget,
        posts_newest_first: list[NewsPost],
    ) -> list[NewsPost]:
        fetched_post_ids = [post.post_id for post in posts_newest_first]
        has_seen_baseline = self.storage.news_target_has_seen_posts(target.target_id)
        seen_post_ids = self.storage.get_news_target_seen_post_ids(
            target.target_id,
            fetched_post_ids,
        )
        if seen_post_ids:
            return [
                post
                for post in reversed(posts_newest_first)
                if post.post_id not in seen_post_ids
            ]

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
                return list(reversed(posts_newest_first))
            return []

        ids = [post.post_id for post in posts_newest_first]
        if settings.last_seen_post_id in ids:
            index = ids.index(settings.last_seen_post_id)
            return list(reversed(posts_newest_first[:index]))

        last_seen_post = self.storage.get_post(settings.last_seen_post_id)
        if last_seen_post and last_seen_post.created_at:
            return [
                post
                for post in reversed(posts_newest_first)
                if post.created_at and post.created_at > last_seen_post.created_at
            ]

        return []

    async def _send_news_post(
        self,
        channel: discord.abc.Messageable,
        settings: GuildSettings,
        post: NewsPost,
    ) -> bool:
        channel_id = getattr(channel, "id", settings.channel_id)
        try:
            await self._broadcast_post(
                channel,
                post,
                settings.role_id,
            )
            return True
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
            return False
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
            return False
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
            return False
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
            return False

    async def _send_news_post_to_target(
        self,
        channel: discord.abc.Messageable,
        settings: GuildSettings,
        target: GuildNewsTarget,
        post: NewsPost,
    ) -> bool:
        try:
            await self._broadcast_post(
                channel,
                post,
                settings.role_id,
            )
            return True
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
            return False
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
            return False
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
            return False
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
            return False

    async def _broadcast_post_updates(self, post_ids: list[str]) -> None:
        for post_id in post_ids:
            post = self.storage.get_post(post_id)
            if post is None:
                continue
            targets = self.storage.get_announced_news_targets(post_id)
            if not targets:
                continue
            LOGGER.info(
                "뉴스 수정 감지 — 재전송 (post_id=%s, title=%r, targets=%s).",
                post_id,
                post.title,
                len(targets),
            )
            for target in targets:
                settings = self.storage.get_settings(target.guild_id)
                if not settings.enabled:
                    continue
                channel = self.bot.get_channel(target.channel_id)
                if channel is None or not isinstance(channel, discord.abc.Messageable):
                    continue
                try:
                    await self._broadcast_post(
                        channel,
                        post,
                        settings.role_id,
                        is_update=True,
                    )
                except Exception:
                    LOGGER.exception(
                        "뉴스 수정 재전송 실패 (guild_id=%s, channel_id=%s, post_id=%s).",
                        target.guild_id,
                        target.channel_id,
                        post_id,
                    )

    async def _broadcast_post(
        self,
        channel: discord.abc.Messageable,
        post: NewsPost,
        role_id: int | None,
        *,
        is_update: bool = False,
    ) -> None:
        mention = f"<@&{role_id}>" if role_id else None
        allowed_mentions = discord.AllowedMentions(
            everyone=False,
            users=False,
            roles=[discord.Object(id=role_id)] if role_id else False,
        )

        standalone_urls = _standalone_image_urls(post, attach_images=True)
        banner_file = _news_banner_file()
        news_view = _build_layout_view_for_post(
            post,
            include_zip_button=True,
            include_banner=banner_file is not None,
            is_update=is_update,
        )
        batch_tasks = self._start_image_batch_tasks(standalone_urls) if standalone_urls else []

        if mention:
            await channel.send(
                content=mention,
                allowed_mentions=allowed_mentions,
            )
        send_kwargs = {
            "view": news_view,
            "allowed_mentions": discord.AllowedMentions.none(),
        }
        if banner_file is not None:
            send_kwargs["file"] = banner_file
        await channel.send(**send_kwargs)

        youtube_content = _youtube_links_content(post)
        if youtube_content:
            await channel.send(
                content=youtube_content,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        self._schedule_channel_image_messages(
            channel,
            post,
            batch_tasks=batch_tasks,
            image_urls=standalone_urls,
        )

    async def _send_news_post_followups(
        self,
        interaction: discord.Interaction,
        post: NewsPost,
        *,
        private: bool,
        attach_photos: bool = True,
    ) -> list[discord.Message | None]:
        sent_messages: list[discord.Message | None] = []
        standalone_urls = _standalone_image_urls(post, attach_images=attach_photos)
        banner_file = _news_banner_file()
        news_view = _build_layout_view_for_post(
            post,
            include_zip_button=attach_photos,
            include_banner=banner_file is not None,
        )
        use_image_embeds = self._bot_is_missing_from_interaction_guild(interaction)
        batch_tasks = [] if use_image_embeds or not standalone_urls else self._start_image_batch_tasks(standalone_urls)

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

        if self._bot_is_missing_from_interaction_guild(interaction):
            self._schedule_interaction_image_embed_followups(
                interaction,
                post,
                private=private,
                image_urls=standalone_urls,
            )
        elif private:
            self._schedule_interaction_image_followups(
                interaction,
                post,
                private=True,
                batch_tasks=batch_tasks,
                image_urls=standalone_urls,
            )
        elif isinstance(interaction.channel, discord.abc.Messageable):
            self._schedule_channel_image_messages(
                interaction.channel,
                post,
                track_guild_id=interaction.guild_id,
                track_channel_id=interaction.channel_id,
                batch_tasks=batch_tasks,
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

    async def handle_zip_request(
        self, interaction: discord.Interaction, post_id: str
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        post = self.storage.get_post(post_id)
        language = _post_language(post) if post is not None else "koreana"
        if post is None or not _content_image_urls(post):
            await interaction.followup.send(
                _news_ui_text(language, "zip_no_images"), ephemeral=True
            )
            return

        try:
            cached = self._zip_cache.get(post.post_id)
            if cached is None:
                buffer, count = await self._build_image_zip(post)
                if buffer is not None and count > 0:
                    self._cache_zip(post.post_id, buffer.getvalue(), count)
            else:
                zip_bytes, count = cached
                buffer = io.BytesIO(zip_bytes)
        except Exception:
            LOGGER.exception("게시물 %s의 이미지 ZIP 생성 실패.", post_id)
            await interaction.followup.send(
                _news_ui_text(language, "zip_fetch_failed"),
                ephemeral=True,
            )
            return

        if buffer is None or count == 0:
            await interaction.followup.send(
                _news_ui_text(language, "zip_empty"), ephemeral=True
            )
            return

        filename = _safe_zip_filename(post)
        file = discord.File(buffer, filename=filename)
        await interaction.followup.send(
            _news_ui_text(language, "zip_ready").format(count=count),
            file=file,
            ephemeral=True,
        )

    async def _build_image_zip(self, post: NewsPost) -> tuple[io.BytesIO | None, int]:
        buffer = io.BytesIO()
        count = 0
        used_names: set[str] = set()
        urls = _content_image_urls(post)
        semaphore = asyncio.Semaphore(ZIP_IMAGE_CONCURRENCY)

        tasks = [
            asyncio.create_task(self._prepare_zip_image(semaphore, index, url))
            for index, url in enumerate(urls)
        ]
        images = await asyncio.gather(*tasks)

        with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_STORED) as archive:
            for item in images:
                if item is None:
                    continue
                index, url, content_type, image_bytes = item
                name = _unique_zip_name(used_names, index, url, content_type)
                archive.writestr(name, image_bytes)
                count += 1

        if count == 0:
            return None, 0

        buffer.seek(0)
        return buffer, count

    async def _prepare_zip_image(
        self, semaphore: asyncio.Semaphore, index: int, url: str
    ) -> tuple[int, str, str | None, bytes] | None:
        async with semaphore:
            downloaded = await self._download_image(url)
            if downloaded is None:
                return None

            data, content_type = downloaded
            return index, url, content_type, data

    def _schedule_channel_image_messages(
        self,
        channel: discord.abc.Messageable,
        post: NewsPost,
        *,
        track_guild_id: int | None = None,
        track_channel_id: int | None = None,
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
            self._send_channel_image_messages(
                channel,
                post,
                track_guild_id=track_guild_id,
                track_channel_id=track_channel_id,
                batch_tasks=resolved_tasks,
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
    ) -> None:
        target = await self._resolve_background_channel(channel, track_channel_id)
        if target is None:
            LOGGER.debug("대상 채널을 사용할 수 없어 이미지 첨부를 건너뜁니다.")
            return

        tasks = batch_tasks or []
        for batch_task in tasks:
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
        for batch_task in (batch_tasks or []):
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
    ) -> list[asyncio.Task[list[discord.File]]]:
        if not urls:
            return []
        semaphore = asyncio.Semaphore(ZIP_IMAGE_CONCURRENCY)
        tasks: list[asyncio.Task[list[discord.File]]] = []
        for batch_start in range(0, len(urls), FILES_PER_MESSAGE):
            batch_urls = urls[batch_start : batch_start + FILES_PER_MESSAGE]
            task = asyncio.create_task(
                self._download_file_batch(semaphore, batch_start, batch_urls)
            )
            task.add_done_callback(self._log_background_task_result)
            tasks.append(task)
        return tasks

    async def _download_file_batch(
        self,
        semaphore: asyncio.Semaphore,
        offset: int,
        urls: list[str],
    ) -> list[discord.File]:
        image_tasks = [
            asyncio.create_task(self._prepare_zip_image(semaphore, offset + i, url))
            for i, url in enumerate(urls)
        ]
        images = await asyncio.gather(*image_tasks)
        used_names: set[str] = set()
        files: list[discord.File] = []
        for item in images:
            if item is None:
                continue
            index, url, content_type, data = item
            filename = _unique_zip_name(used_names, index, url, content_type)
            files.append(discord.File(io.BytesIO(data), filename=filename))
        return files

    def _schedule_image_cache_warmup(self, posts: list[NewsPost]) -> None:
        urls: list[str] = []
        seen_urls: set[str] = set()
        for post in posts[: IMAGE_CACHE_WARM_POST_LIMIT * len(SYNC_LANGUAGES)]:
            for url in _content_image_urls(post):
                if url in seen_urls or url in self._image_cache:
                    continue
                seen_urls.add(url)
                urls.append(url)

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

        try:
            async with self.session.get(url) as response:
                if response.status >= 400:
                    LOGGER.warning("이미지 다운로드 실패 (%s): %s", response.status, url)
                    return None
                content_type = response.headers.get("Content-Type")
                data = await response.read()
                self._cache_image(url, data, content_type)
                return data, content_type
        except aiohttp.ClientError:
            LOGGER.exception("이미지 다운로드 오류: %s", url)
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

    def _cache_zip(self, post_id: str, zip_bytes: bytes, count: int) -> None:
        self._zip_cache[post_id] = (zip_bytes, count)
        while len(self._zip_cache) > ZIP_CACHE_MAX_ITEMS:
            oldest_post_id = next(iter(self._zip_cache))
            self._zip_cache.pop(oldest_post_id, None)

    async def run_startup_sync(self) -> None:
        if self.news_source is None or self._startup_synced:
            return
        try:
            await self._sync_global_news_cache()
            self._startup_synced = True
            LOGGER.info("시작 시 뉴스 동기화 완료 (limit=%s).", NEWS_POST_LIMIT)
        except Exception:
            LOGGER.exception("시작 시 뉴스 동기화 실패.")

    async def _track_manual_message(
        self,
        guild_id: int | None,
        channel_id: int | None,
        message: discord.Message | None,
    ) -> None:
        if guild_id is None or channel_id is None or message is None:
            return
        self.storage.add_tracked_message(guild_id, channel_id, message.id)

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
    )
    @app_commands.describe(
        role="새 소식과 함께 핑할 역할입니다.",
        enabled="새 게시물 자동 알림을 켜거나 끕니다.",
        language="서버에서 기본으로 사용할 소식 언어입니다.",
        auto_cleanup="조회한 소식 메시지를 일정 시간 뒤 자동으로 지울지 여부입니다.",
        cleanup_days="자동 삭제까지의 유예 기간(일)입니다. 1~7 사이로 입력합니다.",
        public_news_send="관리자가 아닌 다른 유저가 /이전소식보기 이나 /최근소식보기 으로 서버 채널에 공개 소식을 보낼 수 있는지 설정합니다.",
    )
    @app_commands.choices(
        language=LANGUAGE_CHOICES,
        public_news_send=BOOLEAN_CHOICES,
        enabled=BOOLEAN_CHOICES,
        auto_cleanup=BOOLEAN_CHOICES,
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

        settings = self.storage.update_settings(
            interaction.guild_id,
            role_id=role.id if role else None,
            post_format=POST_FORMAT_RICH,
            enabled=_choice_bool(enabled),
            language=language.value if language else None,
            auto_cleanup_enabled=_choice_bool(auto_cleanup),
            auto_cleanup_days=cleanup_days,
            public_news_lookup_allowed=_choice_bool(public_news_send),
        )
        role_text = f"<@&{settings.role_id}>" if settings.role_id else "없음"
        enabled_text = "켜짐" if settings.enabled else "꺼짐"
        language_text = _language_label(settings.language)
        cleanup_text = "켜짐" if settings.auto_cleanup_enabled else "꺼짐"
        public_news_send_text = _bool_label(settings.public_news_lookup_allowed)
        embed = discord.Embed(
            title="설정이 완료되었어요~!",
            description=(
                f"역할 핑: {role_text}\n"
                f"새 게시물 자동 알림: {enabled_text}\n"
                f"기본 언어: {language_text}\n"
                f"조회 메시지 자동 삭제: {cleanup_text}\n"
                f"자동 삭제 유예: {settings.auto_cleanup_days}일\n"
                f"공개 소식 전송: {public_news_send_text}"
            ),
            color=discord.Color.from_rgb(179, 28, 28),
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

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
            color=discord.Color.from_rgb(179, 28, 28),
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
    @app_commands.rename(channel="채널", language="언어")
    @app_commands.describe(
        channel="해제할 소식 채널입니다.",
        language="이 채널에서 해제할 소식 언어입니다.",
    )
    @app_commands.choices(language=LANGUAGE_CHOICES)
    async def remove_news_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        language: app_commands.Choice[str],
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("서버 안에서만 설정할 수 있어요.", ephemeral=True)
            return

        removed = self.storage.delete_news_target(
            interaction.guild_id,
            channel_id=channel.id,
            language=language.value,
        )
        targets = self.storage.list_news_targets(interaction.guild_id)
        if removed:
            message = f"{channel.mention}의 {_language_label(language.value)} 소식 자동 발송을 해제했어요."
        else:
            message = f"{channel.mention}에는 {_language_label(language.value)} 소식 채널 설정이 없어요."

        embed = discord.Embed(
            title="소식 채널 설정",
            description=f"{message}\n\n언어별 소식 채널\n{_format_news_targets(targets)}",
            color=discord.Color.from_rgb(179, 28, 28),
        )
        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="누락소식설정", description="자동 발송 실패로 누락된 소식을 다음 폴링 때 다시 보낼지 설정합니다.")
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.rename(enabled="허용")
    @app_commands.describe(
        enabled="허용하면 권한 오류나 일시 오류로 못 보낸 새 소식을 다음 자동 확인 때 다시 시도합니다.",
    )
    @app_commands.choices(enabled=BOOLEAN_CHOICES)
    async def configure_missed_news_recovery(
        self,
        interaction: discord.Interaction,
        enabled: app_commands.Choice[str],
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("서버 안에서만 설정할 수 있어요.", ephemeral=True)
            return

        settings = self.storage.update_settings(
            interaction.guild_id,
            missed_news_recovery_enabled=_choice_bool(enabled, False),
        )
        await interaction.response.send_message(
            (
                f"누락 소식 자동 재시도: {_bool_label(settings.missed_news_recovery_enabled)}\n"
                "허용 상태에서는 자동 발송에 실패한 새 소식을 본 것으로 처리하지 않고 다음 확인 때 다시 보냅니다."
            ),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

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
                "매주 목요일 10:00(KST)에 점검 시작 알림, 12:00(KST)에 업데이트 알림을 임베드로 보내요."
            ),
            color=discord.Color.from_rgb(179, 28, 28),
        )
        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="유저설정", description="앱에서 사용할 봇 개인 언어를 설정합니다.")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.rename(language="언어")
    @app_commands.describe(language="앱으로 사용하는 /최근소식보기, /이전소식보기의 표시 언어입니다.")
    @app_commands.choices(language=LANGUAGE_CHOICES)
    async def configure_user(
        self,
        interaction: discord.Interaction,
        language: app_commands.Choice[str],
    ) -> None:
        user_id, username, nickname = self._interaction_user_values(interaction)
        settings = self.storage.update_user_language(
            user_id,
            username=username,
            nickname=nickname,
            language=language.value,
        )

        await interaction.response.send_message(
            (
                f"개인 언어를 {_language_label(settings.language)}로 설정했어요.\n"
                "앱으로 사용하는 /최근소식보기와 /이전소식보기에서 이 언어를 사용할게요."
            ),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

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
        await interaction.response.send_message("역할 핑을 제거했어요.", ephemeral=True)

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
                "점검 알림: 꺼짐"
            ),
            color=discord.Color.from_rgb(179, 28, 28),
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

        settings = self.storage.get_settings(interaction.guild_id)
        targets = self.storage.list_news_targets(interaction.guild_id)
        target_languages = sorted({target.language for target in targets})
        if not target_languages:
            target_languages = [settings.language]
        source_status = "Steam 뉴스 허브 (" + ", ".join(
            _language_label(language) for language in target_languages
        ) + ")"
        role_text = f"<@&{settings.role_id}>" if settings.role_id else "없음"
        enabled_text = "켜짐" if settings.enabled else "꺼짐"
        maintenance_text = "켜짐" if settings.maintenance_notifications_enabled else "꺼짐"
        cleanup_text = "켜짐" if settings.auto_cleanup_enabled else "꺼짐"
        public_news_send_text = _bool_label(settings.public_news_lookup_allowed)

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
                f"새 게시물 자동 알림: {enabled_text}"
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

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="이전소식보기", description="저장된 림버스 컴퍼니 이전 소식을 다시 봅니다.")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.rename(title="게시물", private="나만보기", attach_photos="사진첨부")
    @app_commands.describe(
        title="게시물의 첫 번째 줄을 선택합니다.",
        private="켜면 나에게만 보이고, 끄면 채널에 메시지를 보냅니다.",
        attach_photos="켜면 소식에 포함된 이미지를 임베드로 함께 표시합니다.",
    )
    @app_commands.choices(private=BOOLEAN_CHOICES, attach_photos=BOOLEAN_CHOICES)
    async def previous_news(
        self,
        interaction: discord.Interaction,
        title: str,
        private: app_commands.Choice[str] | None = None,
        attach_photos: app_commands.Choice[str] | None = None,
    ) -> None:
        private_value = bool(_choice_bool(private, True))
        attach_photos_value = bool(_choice_bool(attach_photos, True))
        if not await self._allow_public_news_send(interaction, private=private_value):
            return

        language = self._interaction_language(interaction)
        post = self.storage.get_post_by_id_or_title(title, language=language)
        if post is None:
            post = self.storage.get_post_by_id_or_title(title)
        if post is None:
            await interaction.response.send_message(
                "아직 저장된 게시물을 찾지 못했어요. 림피가 자동 동기화한 뒤 다시 선택해 주세요.",
                ephemeral=True,
            )
            return

        if not await self._confirm_external_news_send(interaction):
            return

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=private_value, thinking=True)
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

    @previous_news.autocomplete("title")
    async def previous_news_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        language = self._interaction_language(interaction)
        posts = self.storage.search_posts(
            current,
            limit=25,
            language=language,
        )
        return [
            app_commands.Choice(name=_choice_name(post, include_language=False), value=post.post_id)
            for post in posts
        ]

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
        if not await self._confirm_external_news_send(interaction):
            return

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=private_value, thinking=True)

        post: NewsPost | None = None
        if self.news_source is not None:
            try:
                fresh = await self.news_source.fetch_recent_posts(language, limit=NEWS_POST_LIMIT)
            except Exception:
                LOGGER.exception("최신 뉴스 조회 실패. 캐시를 사용합니다.")
                fresh = []
            if fresh:
                self.storage.save_posts(fresh[:NEWS_POST_LIMIT])
                self._schedule_image_cache_warmup(fresh[:NEWS_POST_LIMIT])
                post = fresh[0]

        if post is None:
            post = self.storage.get_latest_post(language)

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

    @app_commands.command(
        name="소식보내기",
        description="저장된 소식을 지정한 채널에 맨션과 함께 보냅니다.",
    )
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.rename(title="게시물", channel="채널", role="역할")
    @app_commands.describe(
        title="보낼 게시물을 선택합니다.",
        channel="보낼 채널입니다. 비워두면 /소식채널설정 채널 전체에 각 채널 언어 버전으로 보냅니다.",
        role="함께 핑할 역할입니다. 비워두면 /서버설정에서 지정한 역할을 사용합니다.",
    )
    async def send_news(
        self,
        interaction: discord.Interaction,
        title: str,
        channel: discord.TextChannel | None = None,
        role: discord.Role | None = None,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "서버 안에서만 사용할 수 있어요.", ephemeral=True
            )
            return

        language = self._interaction_language(interaction)
        post = self.storage.get_post_by_id_or_title(title, language=language)
        if post is None:
            post = self.storage.get_post_by_id_or_title(title)
        if post is None:
            await interaction.response.send_message(
                "해당 게시물을 찾지 못했어요.", ephemeral=True
            )
            return

        settings = self.storage.get_settings(interaction.guild_id)
        configured_targets = self.storage.list_news_targets(interaction.guild_id)
        delivery_targets: list[tuple[discord.abc.Messageable, str]] = []
        if channel is not None:
            channel_languages = [
                target.language
                for target in configured_targets
                if target.channel_id == channel.id
            ]
            if not channel_languages:
                channel_languages = [_post_language(post) or language]
            delivery_targets = [(channel, target_language) for target_language in channel_languages]
        else:
            for news_target in configured_targets:
                resolved = await self._resolve_target_channel(None, news_target.channel_id)
                if resolved is not None:
                    delivery_targets.append((resolved, news_target.language))

        if not delivery_targets:
            await interaction.response.send_message(
                "보낼 채널이 없어요. 채널 옵션을 지정하거나 /소식채널설정으로 언어별 채널을 설정해주세요.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        role_id = role.id if role else settings.role_id
        sent_channel_ids: list[int] = []
        failed_channel_ids: list[int | None] = []
        missing_languages: set[str] = set()

        for target, target_language in delivery_targets:
            channel_id = getattr(target, "id", None)
            target_post = self._post_variant_for_language(post, target_language)
            if target_post is None:
                missing_languages.add(target_language)
                failed_channel_ids.append(channel_id)
                continue

            try:
                await self._broadcast_post(
                    target,
                    target_post,
                    role_id,
                )
            except discord.Forbidden:
                failed_channel_ids.append(channel_id)
                continue
            except discord.HTTPException:
                LOGGER.exception("수동 뉴스 전송 실패.")
                failed_channel_ids.append(channel_id)
                continue

            if isinstance(channel_id, int):
                sent_channel_ids.append(channel_id)

        if not sent_channel_ids:
            await interaction.followup.send(
                "소식을 보낼 수 있는 채널이 없어요. 채널 권한을 확인해주세요.",
                ephemeral=True,
            )
            return

        sent_text = ", ".join(f"<#{channel_id}>" for channel_id in sent_channel_ids)
        if failed_channel_ids:
            failed_text = ", ".join(
                f"<#{channel_id}>" if channel_id else "지정한 채널"
                for channel_id in failed_channel_ids
            )
            message = f"{sent_text}에 소식을 보냈어요.\n전송 실패: {failed_text}"
        else:
            message = f"{sent_text}에 소식을 보냈어요."
        if missing_languages:
            missing_text = ", ".join(
                _language_label(language)
                for language in sorted(missing_languages)
            )
            message += f"\n같은 소식의 {missing_text} 게시물을 아직 찾지 못했어요."

        await interaction.followup.send(message, ephemeral=True)

    @send_news.autocomplete("title")
    async def send_news_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        language = self._interaction_language(interaction)
        posts = self.storage.search_posts(current, limit=25, language=language)
        return [
            app_commands.Choice(
                name=_choice_name(post, include_language=False),
                value=post.post_id,
            )
            for post in posts
        ]

    @app_commands.command(name="명령어", description="림피의 모든 명령어 사용법을 봅니다.")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def list_commands(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="림피 명령어 안내",
            color=discord.Color.from_rgb(179, 28, 28),
            description="**림피는 림버스 컴퍼니 스팀 뉴스를 가져와 디스코드에 보내주는 봇이에요!**",
        )
        embed.add_field(
            name="/서버설정",
            value=(
                "역할, 자동 알림, 기본 언어, 공개 소식 전송, 조회 메시지 자동 삭제(유예 1~7일)를 설정합니다.\n"
                "자동 알림·자동 삭제 같은 선택 옵션은 `허용`/`비허용`으로 고릅니다.\n"
                "서버에서만 사용 가능 (서버 관리 권한 필요)."
            ),
            inline=False,
        )
        embed.add_field(
            name="/소식채널설정",
            value=(
                "언어와 채널을 골라 자동 소식 채널을 등록합니다.\n"
                "한국어·English·日本語 채널을 나누려면 언어와 채널을 바꿔 이 명령을 반복 실행합니다.\n"
                "같은 채널은 한 언어로만 등록되며, 다시 설정하면 기존 언어가 새 언어로 바뀝니다. (서버 관리 권한 필요)"
            ),
            inline=False,
        )
        embed.add_field(
            name="/누락소식설정",
            value=(
                "권한 오류나 일시 오류로 자동 발송에 실패한 새 소식을 다음 확인 때 다시 보낼지 설정합니다.\n"
                "`허용` 상태에서는 실패한 새 소식을 본 것으로 처리하지 않아 자동 재시도합니다. (서버 관리 권한 필요)"
            ),
            inline=False,
        )
        embed.add_field(
            name="/소식채널해제",
            value="언어와 채널을 골라 자동 소식 채널 등록을 해제합니다. (서버 관리 권한 필요)",
            inline=False,
        )
        embed.add_field(
            name="/점검알림설정",
            value=(
                "매주 목요일 10:00(KST) 점검 시작과 12:00(KST) 업데이트 알림을 임베드로 보낼지 설정합니다.\n"
                "채널을 비우면 현재 채널 또는 기존 점검 알림 채널을 사용합니다. (서버 관리 권한 필요)"
            ),
            inline=False,
        )
        embed.add_field(
            name="/유저설정",
            value="앱으로 사용할 때의 개인 언어를 설정합니다.",
            inline=False,
        )
        embed.add_field(
            name="/서버설정상태",
            value="현재 봇 서버 설정과 뉴스 소스를 보여줍니다. (서버 전용)",
            inline=False,
        )
        embed.add_field(
            name="/서버설정초기화",
            value="서버 공통 설정, 언어별 소식 채널, 읽음 기준선을 초기 상태로 되돌립니다. (서버 관리 권한 필요)",
            inline=False,
        )
        embed.add_field(
            name="/역할핑해제",
            value="새 소식 알림에 붙는 역할 핑을 제거합니다. (서버 전용)",
            inline=False,
        )
        embed.add_field(
            name="/서버동기화",
            value="현재 서버를 림피 DB에 등록하고 명령어 사용 준비 상태를 확인합니다. (서버 관리 권한 필요)",
            inline=False,
        )
        embed.add_field(
            name="/최근소식보기",
            value="설정한 언어의 가장 최근 소식을 즉시 가져와 보여줍니다. 나만보기·사진 첨부 옵션은 `허용`/`비허용`으로 고릅니다.",
            inline=False,
        )
        embed.add_field(
            name="/소식보내기",
            value=(
                "저장된 소식을 골라 지정 채널에 맨션과 함께 보냅니다.\n"
                "채널을 비우면 /소식채널설정 채널 전체에 각 채널 언어와 같은 Steam 게시물을 찾아 보내고, 역할을 비우면 /서버설정 값을 사용합니다. (서버 관리 권한 필요)"
            ),
            inline=False,
        )
        embed.add_field(
            name="/이전소식보기",
            value=(
                "저장된 이전 소식을 다시 봅니다. 자동완성은 설정한 언어로 필터링됩니다.\n"
                "서버와 앱 설치에서 모두 사용 가능하며, 나만보기·사진 첨부 옵션은 `허용`/`비허용`으로 고릅니다."
            ),
            inline=False,
        )
        embed.add_field(
            name="/명령어",
            value="이 안내를 봅니다.",
            inline=False,
        )
        embed.add_field(
            name="/크레딧",
            value="림피 제작 크레딧을 봅니다.",
            inline=False,
        )
        embed.set_footer(text=f"한 번에 가져오는 소식 수: 최대 {NEWS_POST_LIMIT}개")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="크레딧", description="림피 제작 크레딧을 봅니다.")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def credits(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="크레딧",
            description=(
                "림피(Limpi) 봇 By. 2P\n"
                "알림 배너 그림 By. @gamstergd7"
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
            color=discord.Color.from_rgb(179, 28, 28),
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
                f"새 게시물 자동 알림: {'켜짐' if settings.enabled else '꺼짐'}"
            ),
            inline=False,
        )
        embed.add_field(
            name="다음 설정",
            value=(
                "자동 소식 채널은 `/소식채널설정`으로 등록해주세요.\n"
                "역할 핑, 기본 언어, 공개 소식 전송은 `/서버설정`에서 바꿀 수 있어요."
            ),
            inline=False,
        )
        await interaction.followup.send(
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


def _filter_image_urls(urls: list[str]) -> list[str]:
    return [
        url
        for url in urls
        if url and YOUTUBE_PLACEHOLDER_IMAGE_FRAGMENT not in url
    ]


def _resource_path(relative_path: Path) -> Path:
    base_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base_path / relative_path


def _news_banner_file() -> discord.File | None:
    path = _resource_path(NEWS_BANNER_PATH)
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


def _thumbnail_url_for_post(post: NewsPost) -> str | None:
    raw_thumbnail = post.raw.get("thumbnail_url")
    if isinstance(raw_thumbnail, str) and raw_thumbnail:
        return raw_thumbnail

    for url in _filter_image_urls(post.image_urls):
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
        embed = discord.Embed(url=post.url, color=discord.Color.from_rgb(179, 28, 28))
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
    chunks = _split_message_content(
        (post.text or post.url).strip(),
        EMBED_DESCRIPTION_LIMIT,
    )
    description = chunks[0] if chunks else post.url
    schedule_text = _schedule_text_for_post(post)
    if schedule_text:
        schedule_label = _news_ui_text(_post_language(post), "schedule")
        schedule_block = f"\n\n**{schedule_label}**\n{schedule_text}"
        if len(description) + len(schedule_block) <= EMBED_DESCRIPTION_LIMIT:
            description = f"{description}{schedule_block}"
    return description


def _embed_groups_for_post(post: NewsPost) -> list[list[discord.Embed]]:
    fallback = discord.Embed(
        title=post.title[:256],
        description=_description_for_post(post),
        url=post.url,
        color=discord.Color.from_rgb(179, 28, 28),
    )
    return [[fallback]]


def _embeds_for_post(post: NewsPost) -> list[discord.Embed]:
    groups = _embed_groups_for_post(post)
    return groups[0] if groups else []


def _build_layout_view_for_post(
    post: NewsPost,
    *,
    include_zip_button: bool,
    include_banner: bool,
    leading_text: str | None = None,
    is_update: bool = False,
) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=None)
    container = discord.ui.Container(accent_color=discord.Color.from_rgb(179, 28, 28))
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
    schedule_text = _schedule_text_for_post(post)
    schedule_display = (
        f"**{_news_ui_text(language, 'schedule')}**\n{schedule_text}"
        if schedule_text
        else ""
    )
    overhead = (
        (len(update_badge) if is_update else 0)
        + (len(schedule_display) if schedule_display else 0)
        + (len(leading_text) if leading_text else 0)
    )
    body_limit = max(100, 4000 - overhead)

    container.add_item(
        discord.ui.TextDisplay(
            _truncate_component_text(
                f"### {post.title.strip() or post.url}\n\n{(post.text or post.url).strip()}",
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

    if schedule_display:
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(schedule_display))

    action_row = discord.ui.ActionRow()
    if post.url:
        action_row.add_item(
            discord.ui.Button(
                label=_news_ui_text(language, "original"),
                style=discord.ButtonStyle.link,
                url=post.url,
            )
        )
    if include_zip_button and _content_image_urls(post):
        action_row.add_item(ZipDownloadButton(post.post_id, language=language))
    if action_row.children:
        container.add_item(discord.ui.Separator())
        container.add_item(action_row)

    view.add_item(container)
    return view


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
    if include_zip_button and _content_image_urls(post):
        view.add_item(ZipDownloadButton(post.post_id, language=language))
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


def _maintenance_embed(title: str, description: str) -> discord.Embed:
    return discord.Embed(
        title=title,
        description=description,
        color=discord.Color.from_rgb(179, 28, 28),
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




def _choice_bool(choice: app_commands.Choice[str] | None, default: bool | None = None) -> bool | None:
    if choice is None:
        return default
    return choice.value == BOOLEAN_TRUE


def _bool_label(value: bool) -> str:
    return "허용" if value else "비허용"


def _youtube_links_content(post: NewsPost) -> str | None:
    links = _youtube_urls_for_post(post)[:3]
    return "\n".join(links) if links else None


def _youtube_urls_for_post(post: NewsPost) -> list[str]:
    value = post.raw.get("youtube_urls")
    if not isinstance(value, list):
        return []

    return [str(url) for url in value if url]


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
    return value.astimezone(KST).strftime("%Y-%m-%d %H:%M KST")


def _choice_name(post: NewsPost, *, include_language: bool = False) -> str:
    title = post.title.strip() or post.post_id
    prefix = ""
    if include_language:
        language = _post_language(post)
        if language:
            prefix = f"[{_language_label(language)}] "

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
    used_names: set[str], index: int, url: str, content_type: str | None
) -> str:
    extension = _image_file_extension(url, content_type)
    candidate = f"소식_이미지_({index + 1}){extension}"
    counter = 2
    while candidate in used_names:
        candidate = f"소식_이미지_({index + 1}_{counter}){extension}"
        counter += 1
    used_names.add(candidate)
    return candidate


def _image_file_extension(url: str, content_type: str | None) -> str:
    if content_type:
        normalized = content_type.split(";", 1)[0].strip().lower()
        if normalized == "image/jpeg":
            return ".jpg"
        if normalized == "image/png":
            return ".png"
        if normalized == "image/gif":
            return ".gif"
        if normalized == "image/webp":
            return ".webp"
        if normalized == "image/bmp":
            return ".bmp"

    suffix = urlparse(url).path.rsplit("/", 1)[-1].lower().rsplit(".", 1)
    if len(suffix) == 2:
        extension = f".{suffix[1]}"
        if extension in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}:
            return ".jpg" if extension == ".jpeg" else extension

    return ".img"


def _safe_zip_filename(post: NewsPost) -> str:
    title = (post.title or "").strip()
    cleaned = _UNSAFE_FILENAME_RE.sub(" ", title).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        cleaned = post.post_id
    if len(cleaned) > 80:
        cleaned = cleaned[:80].rstrip()
    return f"림버스_소식_({cleaned}).zip"


async def main() -> None:
    _base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
    _log_dir = os.path.join(_base, "logs")
    os.makedirs(_log_dir, exist_ok=True)
    _now = datetime.now()
    _log_file = os.path.join(
        _log_dir,
        f"limpi_{_now.strftime('%Y-%m-%d')}-{_now.hour}_{_now.strftime('%M_%S')}.log",
    )
    _fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    _file_handler = logging.FileHandler(_log_file, encoding="utf-8")
    _file_handler.setFormatter(_fmt)
    import io as _io
    _stdout_stream = (
        _io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
        if hasattr(sys.stdout, "buffer")
        else sys.stdout
    )
    _console_handler = logging.StreamHandler(_stdout_stream)
    _console_handler.setFormatter(_fmt)
    logging.basicConfig(level=logging.INFO, handlers=[_file_handler, _console_handler])

    class _DropExpiredInteraction(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            if record.exc_info:
                exc = record.exc_info[1]
                original = getattr(exc, "original", exc)
                if isinstance(original, discord.errors.NotFound) and original.code == 10062:
                    return False
            return "10062" not in record.getMessage()

    logging.getLogger("discord.app_commands.tree").addFilter(_DropExpiredInteraction())
    logging.getLogger("discord.client").addFilter(_DropExpiredInteraction())
    test_mode = "--test" in sys.argv
    if test_mode:
        LOGGER.info("테스트 모드: DISCORD_TOKEN_TEST 토큰으로 실행합니다.")
    config = AppConfig.from_env(test=test_mode)
    storage = SQLiteStorage(config.database_path)

    async with aiohttp.ClientSession() as session:
        news_source = build_news_source(config, session)
        bot = LimpiBot(config)

        cog = NewsCog(bot, config, storage, news_source, session)

        @bot.event
        async def on_ready() -> None:
            LOGGER.info("%s (%s)로 로그인했습니다.", bot.user, bot.user.id if bot.user else "unknown")
            if not bot._logged_startup_summary:
                cog.log_startup_summary()
                bot._logged_startup_summary = True
            await bot.sync_connected_guild_commands()
            await cog.run_startup_sync()

        @bot.event
        async def on_guild_join(guild: discord.Guild) -> None:
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
            LOGGER.info(
                "서버 퇴장: name=%s, guild_id=%s — DB 데이터 삭제.",
                guild.name,
                guild.id,
            )
            storage.delete_guild_data(guild.id)

        await bot.add_cog(cog)
        try:
            await bot.start(config.discord_token)
        finally:
            storage.close()


if __name__ == "__main__":
    asyncio.run(main())
