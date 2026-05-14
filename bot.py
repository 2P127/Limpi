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
from models import GuildSettings, NewsPost
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
# 메모리 풋프린트 제한: 큰 이미지가 캐시를 점령하지 못하게 항목 수 + 총 바이트 + 항목당 상한을 둡니다.
IMAGE_CACHE_MAX_ITEMS = 64
IMAGE_CACHE_MAX_BYTES = 64 * 1024 * 1024   # 누적 64 MB 상한
IMAGE_CACHE_MAX_ITEM_BYTES = 4 * 1024 * 1024  # 항목당 4 MB 초과 시 캐시하지 않음
IMAGE_CACHE_WARM_POST_LIMIT = 5
YOUTUBE_PLACEHOLDER_IMAGE_FRAGMENT = "youtube_16x9_placeholder.gif"
LEGACY_STEAM_CARD_THUMBNAIL_FRAGMENTS = (
    # "1dc5775f3444c32d11acb9d57c03232157739877",
    # "62e63adbc551470064256668df2ba6cae5138cad",
)
EMBEDS_PER_MESSAGE = 10
# 이미지 전용 임베드를 한 메시지에 많이 넣으면 디스코드가 격자로 붙여 레이아웃이 깨집니다.
IMAGE_ONLY_EMBEDS_PER_MESSAGE = 4
EMBED_DESCRIPTION_LIMIT = 4096
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
IMAGE_DELIVERY_EMBEDS = "embeds"
IMAGE_DELIVERY_CHOICES = [
    app_commands.Choice(name="첨부파일", value=IMAGE_DELIVERY_FILES),
    app_commands.Choice(name="임베드", value=IMAGE_DELIVERY_EMBEDS),
]
LANGUAGE_LABELS = {
    "koreana": "한국어",
    "english": "English",
    "japanese": "日本語",
}
SYNC_LANGUAGES = ("koreana", "english", "japanese")
MAINTENANCE_WEEKDAY = 3  # Thursday in datetime.weekday(), KST 기준
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
    "스팀에 들어가서 업데이트 해주세요! :3"
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
                "Command %s by %s (%s) in guild %s (%s) %s in %.3fs.",
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
            "Synced %s global commands for guild and user app installs.",
            len(synced),
        )

        if self.config.command_guild_id:
            guild = discord.Object(id=self.config.command_guild_id)
            self.tree.clear_commands(guild=guild)
            guild_synced = await self.tree.sync(guild=guild)
            LOGGER.info(
                "Cleared %s guild-scoped commands from %s; global commands stay active.",
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
                "Cleared global application commands to prevent duplicate slash command entries."
            )
        except discord.HTTPException:
            LOGGER.exception("Failed to clear global application commands.")
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
            LOGGER.warning("No connected guilds are available for guild command cleanup.")
            return

        for guild in self.guilds:
            guild_object = discord.Object(id=guild.id)
            try:
                self.tree.clear_commands(guild=guild_object)
                synced = await self.tree.sync(guild=guild_object)
                LOGGER.info(
                    "Cleared %s guild commands from %s (%s) to avoid duplicate global entries.",
                    len(synced),
                    guild.name,
                    guild.id,
                )
            except discord.HTTPException:
                LOGGER.exception(
                    "Failed to clear guild commands from %s (%s).",
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
    def __init__(self, post_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="이미지 ZIP 다운로드",
                style=discord.ButtonStyle.primary,
                custom_id=f"{ZIP_CUSTOM_ID_PREFIX}{post_id}",
                emoji="🗂️",
            )
        )
        self.post_id = post_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):  # type: ignore[override]
        return cls(match["post_id"])

    async def callback(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("NewsCog")
        if not isinstance(cog, NewsCog):
            await interaction.response.send_message(
                "지금은 다운로드를 처리할 수 없어요.", ephemeral=True
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
            LOGGER.warning("News polling is disabled because no Steam news source is configured.")
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
            "Connected guild summary: count=%s guilds=%s",
            len(self.bot.guilds),
            ", ".join(
                f"{guild.name} ({guild.id})"
                for guild in sorted(self.bot.guilds, key=lambda item: item.id)
            )
            or "none",
        )

        settings_list = self.storage.list_settings()
        notification_settings = [
            settings
            for settings in settings_list
            if settings.channel_id
            and (settings.enabled or settings.maintenance_notifications_enabled)
        ]
        LOGGER.info(
            "Notification settings summary: configured_guilds=%s active_targets=%s",
            len(settings_list),
            len(notification_settings),
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
                "Notification target: guild=%s (%s), connected=%s, "
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

        orphan_settings = [
            settings
            for settings in settings_list
            if settings.guild_id not in connected_guild_ids
        ]
        if orphan_settings:
            LOGGER.warning(
                "Stored settings exist for guilds where the bot is not connected: %s",
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
                LOGGER.exception("News polling failed.")

    @poll_news.before_loop
    async def before_poll_news(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=60)
    async def maintenance_notifications(self) -> None:
        try:
            await self._process_maintenance_notifications()
        except Exception:
            LOGGER.exception("Maintenance notification processing failed.")

    @maintenance_notifications.before_loop
    async def before_maintenance_notifications(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=15)
    async def cleanup_messages(self) -> None:
        try:
            await self._cleanup_expired_messages()
        except Exception:
            LOGGER.exception("Tracked message cleanup failed.")
        # 장시간 가동 시 cyclic GC가 잘 안 도는 큰 객체(이미지 바이트 등)를 회수해
        # RSS가 우상향하는 현상을 막습니다.
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
                    "Failed to delete tracked message %s in channel %s.", message_id, channel_id
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
                    "Maintenance notification skipped: missing access to configured channel "
                    "(guild_id=%s, channel_id=%s, notice_type=%s, discord_code=%s).",
                    settings.guild_id,
                    settings.channel_id,
                    notice_type,
                    getattr(exc, "code", None),
                )
                return False
            except discord.NotFound as exc:
                LOGGER.warning(
                    "Maintenance notification skipped: configured channel was not found "
                    "(guild_id=%s, channel_id=%s, notice_type=%s, discord_code=%s).",
                    settings.guild_id,
                    settings.channel_id,
                    notice_type,
                    getattr(exc, "code", None),
                )
                return False
            except discord.HTTPException as exc:
                LOGGER.warning(
                    "Maintenance notification skipped: failed to fetch configured channel "
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
                "Maintenance notification skipped: configured channel is not messageable "
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
                "Maintenance notification failed: Discord rejected message send "
                "(guild_id=%s, channel_id=%s, notice_type=%s).",
                settings.guild_id,
                settings.channel_id,
                notice_type,
            )
            return False
        except discord.HTTPException as exc:
            LOGGER.warning(
                "Maintenance notification failed: Discord HTTP error "
                "(guild_id=%s, channel_id=%s, notice_type=%s, discord_status=%s, discord_code=%s).",
                settings.guild_id,
                settings.channel_id,
                notice_type,
                exc.status,
                getattr(exc, "code", None),
            )
            return False

        LOGGER.info(
            "Maintenance notification sent (guild_id=%s, channel_id=%s, notice_type=%s).",
            settings.guild_id,
            settings.channel_id,
            notice_type,
        )
        return True

    async def _poll_once(self) -> int:
        if self.news_source is None:
            return 0

        posts_by_language = await self._sync_global_news_cache()
        settings_by_language = self._settings_by_language()

        announced_count = 0
        for language, settings_list in settings_by_language.items():
            posts = posts_by_language.get(language, [])
            if not posts:
                continue

            for settings in settings_list:
                guild_posts = posts[:NEWS_POST_LIMIT]
                if not guild_posts:
                    continue
                newest_post_id = guild_posts[0].post_id
                fetched_post_ids = [post.post_id for post in guild_posts]
                announced_count += await self._process_guild(settings, guild_posts)
                if not settings.channel_id or not settings.enabled:
                    self.storage.mark_posts_seen(settings.guild_id, fetched_post_ids)
                    self.storage.set_last_seen_post_id(settings.guild_id, newest_post_id)

        return announced_count

    async def _sync_global_news_cache(self) -> dict[str, list[NewsPost]]:
        if self.news_source is None:
            return {}

        posts_by_language: dict[str, list[NewsPost]] = {}
        all_posts: list[NewsPost] = []
        for language in SYNC_LANGUAGES:
            posts = await self.news_source.fetch_recent_posts(language, limit=NEWS_POST_LIMIT)
            posts_by_language[language] = posts[:NEWS_POST_LIMIT]
            all_posts.extend(posts_by_language[language])

        if all_posts:
            self.storage.save_posts(all_posts)
            self._schedule_image_cache_warmup(all_posts)
        return posts_by_language

    def _settings_by_language(self) -> dict[str, list[GuildSettings]]:
        settings_by_language: dict[str, list[GuildSettings]] = {}
        for settings in self.storage.list_settings():
            if self.bot.get_guild(settings.guild_id) is None:
                LOGGER.debug(
                    "News auto-send settings skipped because the bot is not connected "
                    "to the configured guild (guild_id=%s, channel_id=%s).",
                    settings.guild_id,
                    settings.channel_id or "none",
                )
                continue
            language = settings.language or self.config.steam_language
            settings_by_language.setdefault(language, []).append(settings)
        return settings_by_language

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
            # DM·유저 앱에서는 외부 URL 임베드가 첨부 파일보다 안정적으로 표시됩니다.
            return IMAGE_DELIVERY_EMBEDS
        return self.storage.get_settings(interaction.guild_id).image_delivery

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

    async def _process_guild(self, settings: GuildSettings, posts: list[NewsPost]) -> int:
        if not settings.channel_id or not settings.enabled:
            return 0
        if self.bot.get_guild(settings.guild_id) is None:
            LOGGER.debug(
                "News auto-send skipped because the bot is not connected to the guild "
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
                    "News auto-send skipped: missing access to configured channel "
                    "(guild_id=%s, channel_id=%s, discord_code=%s). "
                    "Check channel/category permissions for View Channel and Send Messages.",
                    settings.guild_id,
                    settings.channel_id,
                    getattr(exc, "code", None),
                )
                return 0
            except discord.NotFound as exc:
                LOGGER.warning(
                    "News auto-send skipped: configured channel was not found "
                    "(guild_id=%s, channel_id=%s, discord_code=%s). Re-run server settings.",
                    settings.guild_id,
                    settings.channel_id,
                    getattr(exc, "code", None),
                )
                return 0
            except discord.HTTPException as exc:
                LOGGER.exception(
                    "News auto-send skipped: failed to fetch configured channel "
                    "(guild_id=%s, channel_id=%s, discord_status=%s, discord_code=%s).",
                    settings.guild_id,
                    settings.channel_id,
                    getattr(exc, "status", None),
                    getattr(exc, "code", None),
                )
                return 0

        if not isinstance(channel, discord.abc.Messageable):
            LOGGER.warning(
                "News auto-send skipped: configured channel is not messageable "
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
                    "News auto-send failed "
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
                "Missed news recovery is disabled; failed posts will be marked as seen "
                "and skipped on future automatic polls (guild_id=%s, channel_id=%s, post_ids=%s).",
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
                "Initialized news baseline for guild %s with %s posts. No old posts will be announced.",
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
                image_delivery=settings.image_delivery,
            )
            return True
        except discord.Forbidden as exc:
            LOGGER.warning(
                "News auto-send forbidden: Discord rejected message send "
                "(guild_id=%s, channel_id=%s, role_id=%s, post_id=%s, title=%r, "
                "discord_code=%s). Check channel/category overrides for the bot role.",
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
                "News auto-send target disappeared "
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
                "News auto-send HTTP error "
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
                "Unexpected news auto-send error "
                "(guild_id=%s, channel_id=%s, role_id=%s, post_id=%s, title=%r).",
                settings.guild_id,
                channel_id,
                settings.role_id,
                post.post_id,
                post.title,
            )
            return False

    async def _broadcast_post(
        self,
        channel: discord.abc.Messageable,
        post: NewsPost,
        role_id: int | None,
        *,
        image_delivery: str = IMAGE_DELIVERY_FILES,
    ) -> None:
        mention = f"<@&{role_id}>" if role_id else None
        allowed_mentions = discord.AllowedMentions(
            everyone=False,
            users=False,
            roles=[discord.Object(id=role_id)] if role_id else False,
        )

        standalone_urls = _standalone_image_urls(post, attach_images=True)
        groups = _embed_groups_for_post(post)
        file_batches_task = (
            self._start_image_file_batches_task(post, urls=standalone_urls)
            if image_delivery == IMAGE_DELIVERY_FILES
            else None
        )

        first, *rest = groups if groups else ([],)
        news_view = _build_view_for_post(post, include_zip_button=True) or discord.utils.MISSING
        await channel.send(
            content=mention,
            embeds=first,
            view=news_view,
            allowed_mentions=allowed_mentions,
        )

        for extra_embeds in rest:
            await channel.send(
                embeds=extra_embeds,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        youtube_content = _youtube_links_content(post)
        if youtube_content:
            await channel.send(
                content=youtube_content,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        if image_delivery == IMAGE_DELIVERY_EMBEDS:
            self._schedule_channel_image_embed_messages(
                channel, post, image_urls=standalone_urls
            )
        else:
            self._schedule_channel_image_messages(
                channel,
                post,
                file_batches_task=file_batches_task,
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
        groups = _embed_groups_for_post(post)
        first, *rest = groups if groups else ([],)
        image_delivery = self._interaction_image_delivery(interaction)
        use_image_embeds = (
            self._bot_is_missing_from_interaction_guild(interaction)
            or image_delivery == IMAGE_DELIVERY_EMBEDS
        )
        file_batches_task = (
            None
            if use_image_embeds
            else self._start_image_file_batches_task(post, urls=standalone_urls)
        )

        news_view = (
            _build_view_for_post(post, include_zip_button=attach_photos)
            or discord.utils.MISSING
        )
        sent_messages.append(
            await interaction.followup.send(
                embeds=first,
                ephemeral=private,
                view=news_view,
                allowed_mentions=discord.AllowedMentions.none(),
                wait=True,
            )
        )

        for extra_embeds in rest:
            sent_messages.append(
                await interaction.followup.send(
                    embeds=extra_embeds,
                    ephemeral=private,
                    allowed_mentions=discord.AllowedMentions.none(),
                    wait=True,
                )
            )

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
        elif image_delivery == IMAGE_DELIVERY_EMBEDS and private:
            self._schedule_interaction_image_embed_followups(
                interaction,
                post,
                private=True,
                image_urls=standalone_urls,
            )
        elif image_delivery == IMAGE_DELIVERY_EMBEDS and isinstance(
            interaction.channel,
            discord.abc.Messageable,
        ):
            self._schedule_channel_image_embed_messages(
                interaction.channel,
                post,
                track_guild_id=interaction.guild_id,
                track_channel_id=interaction.channel_id,
                image_urls=standalone_urls,
            )
        elif private:
            self._schedule_interaction_image_followups(
                interaction,
                post,
                private=True,
                file_batches_task=file_batches_task,
                image_urls=standalone_urls,
            )
        elif isinstance(interaction.channel, discord.abc.Messageable):
            self._schedule_channel_image_messages(
                interaction.channel,
                post,
                track_guild_id=interaction.guild_id,
                track_channel_id=interaction.channel_id,
                file_batches_task=file_batches_task,
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
        if post is None or not _content_image_urls(post):
            await interaction.followup.send(
                "이 게시물에는 이미지가 없어요.", ephemeral=True
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
            LOGGER.exception("Failed to build image ZIP for post %s.", post_id)
            await interaction.followup.send(
                "이미지를 가져오는 중 문제가 생겼어요. 잠시 후 다시 시도해주세요.",
                ephemeral=True,
            )
            return

        if buffer is None or count == 0:
            await interaction.followup.send(
                "이미지를 다운로드하지 못했어요.", ephemeral=True
            )
            return

        filename = _safe_zip_filename(post)
        file = discord.File(buffer, filename=filename)
        await interaction.followup.send(
            f"이미지 {count}장을 압축했어요.",
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
        file_batches_task: asyncio.Task[list[list[discord.File]]] | None = None,
        image_urls: list[str] | None = None,
    ) -> None:
        urls = (
            image_urls
            if image_urls is not None
            else _content_image_urls(post)
        )
        if not urls and file_batches_task is None:
            return

        task = asyncio.create_task(
            self._send_channel_image_messages(
                channel,
                post,
                track_guild_id=track_guild_id,
                track_channel_id=track_channel_id,
                file_batches_task=file_batches_task,
                image_urls=urls,
            )
        )
        task.add_done_callback(self._log_background_task_result)

    def _schedule_interaction_image_followups(
        self,
        interaction: discord.Interaction,
        post: NewsPost,
        *,
        private: bool,
        file_batches_task: asyncio.Task[list[list[discord.File]]] | None = None,
        image_urls: list[str] | None = None,
    ) -> None:
        urls = (
            image_urls
            if image_urls is not None
            else _content_image_urls(post)
        )
        if not urls and file_batches_task is None:
            return

        task = asyncio.create_task(
            self._send_interaction_image_followups(
                interaction,
                post,
                private=private,
                file_batches_task=file_batches_task,
                image_urls=urls,
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
        file_batches_task: asyncio.Task[list[list[discord.File]]] | None = None,
        image_urls: list[str] | None = None,
    ) -> None:
        target = await self._resolve_background_channel(channel, track_channel_id)
        if target is None:
            LOGGER.debug("Skipping image attachments because the target channel is unavailable.")
            return

        for file_batch in await self._resolve_image_file_batches(
            post, file_batches_task, urls=image_urls
        ):
            try:
                message = await target.send(
                    files=file_batch,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except (discord.Forbidden, discord.NotFound):
                LOGGER.debug(
                    "Skipping image attachments because channel %s is no longer accessible.",
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
            LOGGER.debug("Skipping image embeds because the target channel is unavailable.")
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
                    "Skipping image embeds because channel %s is no longer accessible.",
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
        file_batches_task: asyncio.Task[list[list[discord.File]]] | None = None,
        image_urls: list[str] | None = None,
    ) -> None:
        for file_batch in await self._resolve_image_file_batches(
            post, file_batches_task, urls=image_urls
        ):
            try:
                message = await interaction.followup.send(
                    files=file_batch,
                    ephemeral=private,
                    allowed_mentions=discord.AllowedMentions.none(),
                    wait=True,
                )
            except (discord.Forbidden, discord.NotFound):
                LOGGER.debug("Skipping image followups because the interaction is no longer accessible.")
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
                LOGGER.debug("Skipping image embed followups because the interaction is no longer accessible.")
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
            LOGGER.exception("Background image send failed.")

    def _start_image_file_batches_task(
        self,
        post: NewsPost,
        *,
        urls: list[str] | None = None,
    ) -> asyncio.Task[list[list[discord.File]]] | None:
        use_urls = urls if urls is not None else _content_image_urls(post)
        if not use_urls:
            return None
        task = asyncio.create_task(self._image_file_batches_for_post(post, urls=use_urls))
        task.add_done_callback(self._log_image_prefetch_task_result)
        return task

    @staticmethod
    def _log_image_prefetch_task_result(
        task: asyncio.Task[list[list[discord.File]]],
    ) -> None:
        if task.cancelled():
            return
        try:
            task.exception()
        except asyncio.CancelledError:
            pass

    async def _resolve_image_file_batches(
        self,
        post: NewsPost,
        file_batches_task: asyncio.Task[list[list[discord.File]]] | None,
        *,
        urls: list[str] | None = None,
    ) -> list[list[discord.File]]:
        if file_batches_task is not None:
            return await file_batches_task
        return await self._image_file_batches_for_post(post, urls=urls)

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

        semaphore = asyncio.Semaphore(ZIP_IMAGE_CONCURRENCY)
        tasks = [
            asyncio.create_task(self._prepare_zip_image(semaphore, index, url))
            for index, url in enumerate(resolved)
        ]
        images = await asyncio.gather(*tasks)

        used_names: set[str] = set()
        files: list[discord.File] = []
        for item in images:
            if item is None:
                continue

            index, url, content_type, image_bytes = item
            filename = _unique_zip_name(used_names, index, url, content_type)
            files.append(discord.File(io.BytesIO(image_bytes), filename=filename))

        return [
            files[index : index + FILES_PER_MESSAGE]
            for index in range(0, len(files), FILES_PER_MESSAGE)
        ]

    async def _download_image(self, url: str) -> tuple[bytes, str | None] | None:
        cached = self._image_cache.get(url)
        if cached is not None:
            # Hit: dict 끝으로 재삽입해 LRU 신선도 갱신 (Python dict는 삽입 순서 보존).
            self._image_cache[url] = self._image_cache.pop(url)
            return cached

        try:
            async with self.session.get(url) as response:
                if response.status >= 400:
                    LOGGER.warning("Image download failed (%s): %s", response.status, url)
                    return None
                content_type = response.headers.get("Content-Type")
                data = await response.read()
                self._cache_image(url, data, content_type)
                return data, content_type
        except aiohttp.ClientError:
            LOGGER.exception("Image download error: %s", url)
            return None

    def _cache_image(self, url: str, data: bytes, content_type: str | None) -> None:
        size = len(data)
        # 단발성 거대 이미지가 캐시를 폭발시키지 않도록 항목당 상한 통과 못하면 스킵.
        if size > IMAGE_CACHE_MAX_ITEM_BYTES:
            return
        prev = self._image_cache.pop(url, None)
        if prev is not None:
            self._image_cache_bytes -= len(prev[0])
        self._image_cache[url] = (data, content_type)
        self._image_cache_bytes += size
        # 항목 수 또는 누적 바이트 중 하나라도 초과하면 가장 오래된 항목부터 evict (LRU).
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
            LOGGER.info("Startup news sync completed (limit=%s).", NEWS_POST_LIMIT)
        except Exception:
            LOGGER.exception("Startup news sync failed.")

    async def _track_manual_message(
        self,
        guild_id: int | None,
        channel_id: int | None,
        message: discord.Message | None,
    ) -> None:
        if guild_id is None or channel_id is None or message is None:
            return
        self.storage.add_tracked_message(guild_id, channel_id, message.id)

    @app_commands.command(name="서버설정", description="서버의 봇 알림 채널, 역할, 언어를 설정합니다.")
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.rename(
        channel="채널",
        role="역할",
        enabled="자동알림",
        language="언어",
        auto_cleanup="자동삭제",
        cleanup_days="자동삭제일수",
        image_delivery="이미지전송",
        public_news_send="공개소식전송",
    )
    @app_commands.describe(
        channel="공지할 채널입니다. 비워두면 현재 채널을 사용합니다.",
        role="새 소식과 함께 핑할 역할입니다.",
        enabled="새 게시물 자동 알림을 켜거나 끕니다.",
        language="Steam 뉴스 언어입니다.",
        auto_cleanup="조회한 소식 메시지를 일정 시간 뒤 자동으로 지울지 여부입니다.",
        cleanup_days="자동 삭제까지의 유예 기간(일)입니다. 1~7 사이로 입력합니다.",
        image_delivery="소식 이미지 전송 방식입니다.",
        public_news_send="수동 명령으로 서버 채널에 공개 소식을 보낼 수 있는지 설정합니다.",
    )
    @app_commands.choices(
        language=LANGUAGE_CHOICES,
        image_delivery=IMAGE_DELIVERY_CHOICES,
        public_news_send=BOOLEAN_CHOICES,
        enabled=BOOLEAN_CHOICES,
        auto_cleanup=BOOLEAN_CHOICES,
    )
    async def configure(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
        role: discord.Role | None = None,
        enabled: app_commands.Choice[str] | None = None,
        language: app_commands.Choice[str] | None = None,
        auto_cleanup: app_commands.Choice[str] | None = None,
        cleanup_days: int | None = None,
        image_delivery: app_commands.Choice[str] | None = None,
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

        if channel is None:
            channel = interaction.channel if isinstance(interaction.channel, discord.TextChannel) else None

        settings = self.storage.update_settings(
            interaction.guild_id,
            channel_id=channel.id if channel else None,
            role_id=role.id if role else None,
            post_format=POST_FORMAT_RICH,
            enabled=_choice_bool(enabled),
            language=language.value if language else None,
            auto_cleanup_enabled=_choice_bool(auto_cleanup),
            auto_cleanup_days=cleanup_days,
            image_delivery=image_delivery.value if image_delivery else None,
            public_news_lookup_allowed=_choice_bool(public_news_send),
        )
        channel_text = f"<#{settings.channel_id}>" if settings.channel_id else "미설정"
        role_text = f"<@&{settings.role_id}>" if settings.role_id else "없음"
        enabled_text = "켜짐" if settings.enabled else "꺼짐"
        language_text = _language_label(settings.language)
        cleanup_text = "켜짐" if settings.auto_cleanup_enabled else "꺼짐"
        image_delivery_text = _image_delivery_label(settings.image_delivery)
        public_news_send_text = _bool_label(settings.public_news_lookup_allowed)
        embed = discord.Embed(
            title="설정이 완료되었어요~!",
            description=(
                f"채널: {channel_text}\n"
                f"역할 핑: {role_text}\n"
                f"새 게시물 자동 알림: {enabled_text}\n"
                f"언어: {language_text}\n"
                f"조회 메시지 자동 삭제: {cleanup_text}\n"
                f"자동 삭제 유예: {settings.auto_cleanup_days}일\n"
                f"이미지 전송: {image_delivery_text}\n"
                f"공개 소식 전송: {public_news_send_text}"
            ),
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
        source_status = f"Steam 뉴스 허브 ({_language_label(settings.language)})"
        channel_text = f"<#{settings.channel_id}>" if settings.channel_id else "미설정"
        role_text = f"<@&{settings.role_id}>" if settings.role_id else "없음"
        enabled_text = "켜짐" if settings.enabled else "꺼짐"
        maintenance_text = "켜짐" if settings.maintenance_notifications_enabled else "꺼짐"
        cleanup_text = "켜짐" if settings.auto_cleanup_enabled else "꺼짐"
        image_delivery_text = _image_delivery_label(settings.image_delivery)
        public_news_send_text = _bool_label(settings.public_news_lookup_allowed)

        await interaction.response.send_message(
            (
                f"채널: {channel_text}\n"
                f"역할 핑: {role_text}\n"
                f"새 게시물 자동 알림: {enabled_text}\n"
                f"점검 알림: {maintenance_text}\n"
                f"언어: {_language_label(settings.language)}\n"
                f"조회 메시지 자동 삭제: {cleanup_text}\n"
                f"자동 삭제 유예: {settings.auto_cleanup_days}일\n"
                f"이미지 전송: {image_delivery_text}\n"
                f"공개 소식 전송: {public_news_send_text}\n"
                f"뉴스 소스: {source_status}"
            ),
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
                LOGGER.exception("Fresh recent news fetch failed; falling back to cache.")
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
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.rename(title="게시물", channel="채널", role="역할")
    @app_commands.describe(
        title="보낼 게시물을 선택합니다.",
        channel="보낼 채널입니다. 비워두면 /서버설정에서 지정한 채널을 사용합니다.",
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

        if not await self._allow_public_news_send(interaction, private=False):
            return

        settings = self.storage.get_settings(interaction.guild_id)
        target = await self._resolve_target_channel(channel, settings.channel_id)
        if target is None:
            await interaction.response.send_message(
                "보낼 채널이 없어요. 채널 옵션을 지정하거나 /서버설정으로 채널을 설정해주세요.",
                ephemeral=True,
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

        await interaction.response.defer(ephemeral=True, thinking=True)

        role_id = role.id if role else settings.role_id
        channel_id = getattr(target, "id", None)
        where = f"<#{channel_id}>" if channel_id else "지정한 채널"

        try:
            await self._broadcast_post(
                target,
                post,
                role_id,
                image_delivery=settings.image_delivery,
            )
        except discord.Forbidden:
            await interaction.followup.send(
                f"{where}에 메시지를 보낼 권한이 없어요.", ephemeral=True
            )
            return
        except discord.HTTPException:
            LOGGER.exception("Manual news send failed.")
            await interaction.followup.send(
                "소식을 보내는 중 오류가 발생했어요.", ephemeral=True
            )
            return

        await interaction.followup.send(
            f"{where}에 소식을 보냈어요.", ephemeral=True
        )

    @send_news.autocomplete("title")
    async def send_news_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        language = self._interaction_language(interaction)
        posts = self.storage.search_posts(current, limit=25, language=language)
        return [
            app_commands.Choice(name=_choice_name(post, include_language=False), value=post.post_id)
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
                "채널, 역할, 언어, 이미지 전송, 자동 알림, 공개 소식 전송, 조회 메시지 자동 삭제(유예 1~7일)를 설정합니다.\n"
                "자동 알림·자동 삭제 같은 선택 옵션은 `허용`/`비허용`으로 고릅니다.\n"
                "서버에서만 사용 가능 (서버 관리 권한 필요)."
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
            name="/점검알림설정",
            value=(
                "매주 목요일 10:00(KST) 점검 시작과 12:00(KST) 업데이트 알림을 임베드로 보낼지 설정합니다.\n"
                "채널을 비우면 현재 채널 또는 /서버설정 채널을 사용합니다. (서버 관리 권한 필요)"
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
            name="/역할핑해제",
            value="새 소식 알림에 붙는 역할 핑을 제거합니다. (서버 전용)",
            inline=False,
        )
        embed.add_field(
            name="/서버동기화",
            value="서버를 즉시 동기화합니다. (서버 전용)",
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
                "채널·역할을 비우면 /서버설정 값을 사용합니다. (서버 관리 권한 필요)"
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
        embed.set_footer(text=f"한 번에 가져오는 소식 수: 최대 {NEWS_POST_LIMIT}개")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="서버동기화", description="Steam 뉴스 소스 + 디스코드 서버와 봇을 연동합니다.")
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def sync_news(self, interaction: discord.Interaction) -> None:
        if self.news_source is None:
            await interaction.response.send_message(
                "Steam 뉴스 소스가 설정되지 않았어요. .env의 STEAM_APP_ID 또는 STEAM_NEWS_URL을 확인해주세요.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        async with self._poll_lock:
            settings = self.storage.get_settings(interaction.guild_id) if interaction.guild_id else None
            try:
                posts_by_language = await self._sync_global_news_cache()
            except Exception:
                LOGGER.exception("Manual Steam news sync failed.")
                await interaction.followup.send(
                    "Steam 뉴스 피드를 가져오지 못했어요. 콘솔 로그를 확인해주세요.",
                    ephemeral=True,
                )
                return
            all_posts = [
                post
                for posts in posts_by_language.values()
                for post in posts
            ]
            if interaction.guild_id and all_posts:
                self.storage.mark_posts_seen(
                    interaction.guild_id,
                    (post.post_id for post in all_posts),
                )
                if settings and settings.last_seen_post_id is None:
                    preferred_posts = posts_by_language.get(settings.language) if settings else None
                    newest_post = (preferred_posts or all_posts)[0]
                    self.storage.set_last_seen_post_id(interaction.guild_id, newest_post.post_id)

        summary = ", ".join(
            f"{_language_label(language)} {len(posts_by_language.get(language, []))}개"
            for language in SYNC_LANGUAGES
        )
        await interaction.followup.send(
            f"서버와 동기화가 완료되었어요!",
            ephemeral=True,
        )


def _filter_image_urls(urls: list[str]) -> list[str]:
    return [
        url
        for url in urls
        if url and YOUTUBE_PLACEHOLDER_IMAGE_FRAGMENT not in url
    ]


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


def _description_chunks_for_post(post: NewsPost) -> list[str]:
    text_chunks = _split_message_content(
        (post.text or post.url).strip(),
        EMBED_DESCRIPTION_LIMIT,
    )
    if not text_chunks:
        text_chunks = [post.url]

    schedule_text = _schedule_text_for_post(post)
    if schedule_text:
        schedule_block = f"\n\n**일정**\n{schedule_text}"
        if len(text_chunks[-1]) + len(schedule_block) <= EMBED_DESCRIPTION_LIMIT:
            text_chunks[-1] = f"{text_chunks[-1]}{schedule_block}"
        else:
            text_chunks.append(schedule_block.strip())

    return text_chunks


def _embed_groups_for_post(post: NewsPost) -> list[list[discord.Embed]]:
    text_chunks = _description_chunks_for_post(post)

    main = discord.Embed(
        title=post.title[:256],
        description=text_chunks[0],
        url=post.url,
        color=discord.Color.from_rgb(179, 28, 28),
    )
    source_url = str(post.raw.get("source_url") or "https://store.steampowered.com/news/app/1973530")
    main.set_author(name=post.source_user, url=source_url)
    thumbnail_url = _thumbnail_url_for_post(post)
    if thumbnail_url:
        main.set_image(url=thumbnail_url)

    embeds: list[discord.Embed] = [main]
    for index, chunk in enumerate(text_chunks[1:], start=2):
        embeds.append(
            discord.Embed(
                title=f"{post.title[:240]} ({index})",
                description=chunk,
                url=post.url,
                color=discord.Color.from_rgb(179, 28, 28),
            )
        )

    return [
        embeds[index : index + EMBEDS_PER_MESSAGE]
        for index in range(0, len(embeds), EMBEDS_PER_MESSAGE)
    ]


def _embeds_for_post(post: NewsPost) -> list[discord.Embed]:
    groups = _embed_groups_for_post(post)
    return groups[0] if groups else []


def _build_view_for_post(
    post: NewsPost,
    *,
    include_zip_button: bool,
) -> discord.ui.View | None:
    view = discord.ui.View(timeout=None)
    if post.url:
        view.add_item(
            discord.ui.Button(
                label="원문 보기",
                style=discord.ButtonStyle.link,
                url=post.url,
            )
        )
    if include_zip_button and _content_image_urls(post):
        view.add_item(ZipDownloadButton(post.post_id))
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


def _image_delivery_label(image_delivery: str) -> str:
    if image_delivery == IMAGE_DELIVERY_EMBEDS:
        return "임베드"
    return "첨부파일"


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

    # 10062 = Discord interaction expired before bot responded — harmless, suppress noise.
    # Covers both autocomplete timeouts and commands where defer() races the 3-second window.
    # exc_info holds the actual exception; getMessage() only has the headline.
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
    config = AppConfig.from_env()
    storage = SQLiteStorage(config.database_path)

    async with aiohttp.ClientSession() as session:
        news_source = build_news_source(config, session)
        bot = LimpiBot(config)

        cog = NewsCog(bot, config, storage, news_source, session)

        @bot.event
        async def on_ready() -> None:
            LOGGER.info("Logged in as %s (%s).", bot.user, bot.user.id if bot.user else "unknown")
            if not bot._logged_startup_summary:
                cog.log_startup_summary()
                bot._logged_startup_summary = True
            await bot.sync_connected_guild_commands()
            await cog.run_startup_sync()

        @bot.event
        async def on_guild_join(guild: discord.Guild) -> None:
            LOGGER.info(
                "Joined guild: name=%s, guild_id=%s, owner_id=%s, member_count=%s",
                guild.name,
                guild.id,
                guild.owner_id,
                guild.member_count,
            )

            settings = storage.get_settings(guild.id)
            if settings.channel_id:
                channel = bot.get_channel(settings.channel_id) if settings.channel_id else None
                channel_name = getattr(channel, "name", None) or "unknown"
                role = guild.get_role(settings.role_id) if settings.role_id else None
                LOGGER.info(
                    "Joined guild has stored settings: guild=%s (%s), news_enabled=%s, "
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
                    "Joined guild has no notification channel configured yet: guild=%s (%s)",
                    guild.name,
                    guild.id,
                )

        await bot.add_cog(cog)
        try:
            await bot.start(config.discord_token)
        finally:
            storage.close()


if __name__ == "__main__":
    asyncio.run(main())
