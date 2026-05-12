from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import AppConfig
from models import GuildSettings, NewsPost
from storage import SQLiteStorage
from steam_client import NewsSource, build_news_source


POST_FORMAT_RICH = "rich"
LOGGER = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))
LANGUAGE_CHOICES = [
    app_commands.Choice(name="한국어", value="koreana"),
    app_commands.Choice(name="English", value="english"),
    app_commands.Choice(name="日本語", value="japanese"),
]
LANGUAGE_LABELS = {
    "koreana": "한국어",
    "english": "English",
    "japanese": "日本語",
}
SYNC_LANGUAGES = ("koreana", "english", "japanese")


class LimpiBot(commands.Bot):
    def __init__(self, config: AppConfig) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)
        self.config = config
        self._synced_connected_guilds = False
        self._cleared_global_commands = False
        self._cleared_connected_guild_commands = False

    async def setup_hook(self) -> None:
        if self.config.command_sync_mode == "global":
            synced = await self.tree.sync()
            LOGGER.info(
                "Synced %s global commands. Connected guild command duplicates will be cleaned on ready.",
                len(synced),
            )
            return

        if self.config.command_guild_id:
            guild = discord.Object(id=self.config.command_guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            LOGGER.info("Synced %s commands to guild %s.", len(synced), guild.id)
            await self.clear_global_command_duplicates()
        else:
            LOGGER.info(
                "Skipping global command sync. Commands will be synced to connected guilds on ready."
            )
            await self.clear_global_command_duplicates()

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
        if self.config.command_sync_mode == "global":
            await self.clear_connected_guild_command_duplicates()
            return

        if self.config.command_guild_id or self._synced_connected_guilds:
            if self.config.command_guild_id:
                await self.clear_global_command_duplicates()
            return

        if not self.guilds:
            LOGGER.warning("No connected guilds are available for guild command sync.")
            return

        command_names = ", ".join(command.name for command in self.tree.get_commands())
        for guild in self.guilds:
            guild_object = discord.Object(id=guild.id)
            try:
                self.tree.copy_global_to(guild=guild_object)
                synced = await self.tree.sync(guild=guild_object)
                LOGGER.info(
                    "Synced %s guild commands to %s (%s): %s",
                    len(synced),
                    guild.name,
                    guild.id,
                    command_names,
                )
            except discord.HTTPException:
                LOGGER.exception("Failed to sync guild commands to %s (%s).", guild.name, guild.id)

        await self.clear_global_command_duplicates()
        self._synced_connected_guilds = True


class NewsCog(commands.Cog):
    def __init__(
        self,
        bot: LimpiBot,
        config: AppConfig,
        storage: SQLiteStorage,
        news_source: NewsSource | None,
    ) -> None:
        self.bot = bot
        self.config = config
        self.storage = storage
        self.news_source = news_source
        self._poll_lock = asyncio.Lock()
        self._last_poll_at: datetime | None = None

    async def cog_load(self) -> None:
        if self.news_source is None:
            LOGGER.warning("News polling is disabled because no Steam news source is configured.")
            return

        self.poll_news.start()

    async def cog_unload(self) -> None:
        self.poll_news.cancel()

    @tasks.loop(seconds=60)
    async def poll_news(self) -> None:
        async with self._poll_lock:
            try:
                now = datetime.now(timezone.utc)
                if not self._should_poll_now(now):
                    return
                self._last_poll_at = now
                await self._poll_once()
            except Exception:
                LOGGER.exception("News polling failed.")

    @poll_news.before_loop
    async def before_poll_news(self) -> None:
        await self.bot.wait_until_ready()

    async def _poll_once(self) -> int:
        if self.news_source is None:
            return 0

        settings_by_language = self._settings_by_language()
        posts_by_language = await self._sync_global_news_cache(
            self._fetch_limits_by_language(settings_by_language)
        )

        announced_count = 0
        for language, settings_list in settings_by_language.items():
            posts = posts_by_language.get(language, [])
            if not posts:
                continue

            for settings in settings_list:
                guild_posts = posts[: settings.max_posts_per_poll]
                if not guild_posts:
                    continue
                newest_post_id = guild_posts[0].post_id
                fetched_post_ids = [post.post_id for post in guild_posts]
                announced_count += await self._process_guild(settings, guild_posts)
                if not settings.channel_id or not settings.enabled:
                    self.storage.mark_posts_seen(settings.guild_id, fetched_post_ids)
                    self.storage.set_last_seen_post_id(settings.guild_id, newest_post_id)

        return announced_count

    async def _sync_global_news_cache(
        self, fetch_limits_by_language: dict[str, int]
    ) -> dict[str, list[NewsPost]]:
        if self.news_source is None:
            return {}

        posts_by_language: dict[str, list[NewsPost]] = {}
        all_posts: list[NewsPost] = []
        for language, fetch_limit in fetch_limits_by_language.items():
            posts = await self.news_source.fetch_recent_posts(language, limit=fetch_limit)
            posts_by_language[language] = posts
            all_posts.extend(posts)

        if all_posts:
            self.storage.save_posts(all_posts)
        return posts_by_language

    def _fetch_limits_by_language(
        self, settings_by_language: dict[str, list[GuildSettings]]
    ) -> dict[str, int]:
        fetch_limits = {
            language: self.config.max_posts_per_poll
            for language in SYNC_LANGUAGES
        }
        for language, settings_list in settings_by_language.items():
            settings_limit = max(
                (settings.max_posts_per_poll for settings in settings_list),
                default=self.config.max_posts_per_poll,
            )
            fetch_limits[language] = max(
                fetch_limits.get(language, self.config.max_posts_per_poll),
                settings_limit,
            )
        return fetch_limits

    def _settings_by_language(self) -> dict[str, list[GuildSettings]]:
        settings_by_language: dict[str, list[GuildSettings]] = {}
        for settings in self.storage.list_settings():
            language = settings.language or self.config.steam_language
            settings_by_language.setdefault(language, []).append(settings)
        return settings_by_language

    def _interaction_language(self, interaction: discord.Interaction) -> str:
        if interaction.guild_id is None:
            return self.config.steam_language
        return self.storage.get_settings(interaction.guild_id).language

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

        channel = self.bot.get_channel(settings.channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(settings.channel_id)
            except (discord.Forbidden, discord.NotFound):
                LOGGER.warning(
                    "Cannot access configured channel %s for guild %s.",
                    settings.channel_id,
                    settings.guild_id,
                )
                return 0

        if not isinstance(channel, discord.abc.Messageable):
            LOGGER.warning("Configured channel %s is not messageable.", settings.channel_id)
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
        for post in new_posts:
            await self._send_news_post(channel, settings, post)
            self.storage.mark_posts_seen(settings.guild_id, [post.post_id], announced=True)
            announced += 1

        self.storage.mark_posts_seen(
            settings.guild_id,
            (post.post_id for post in posts),
        )
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
    ) -> None:
        mention = f"<@&{settings.role_id}>" if settings.role_id else None
        allowed_mentions = discord.AllowedMentions(
            everyone=False, users=False, roles=bool(settings.role_id)
        )

        embeds = _embeds_for_post(post)
        await channel.send(
            content=mention,
            embeds=embeds,
            allowed_mentions=allowed_mentions,
        )
        youtube_content = _youtube_links_content(post)
        if youtube_content:
            await channel.send(
                content=youtube_content,
                allowed_mentions=discord.AllowedMentions.none(),
            )

    @app_commands.command(name="림피설정", description="림피 알림 채널, 역할, 언어를 설정합니다.")
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.rename(
        channel="채널",
        role="역할",
        enabled="활성화",
        language="언어",
        max_posts_per_poll="가져올개수",
    )
    @app_commands.describe(
        channel="공지할 채널입니다. 비워두면 현재 채널을 사용합니다.",
        role="새 소식과 함께 핑할 역할입니다.",
        enabled="알림을 켜거나 끕니다.",
        language="Steam 뉴스 언어입니다.",
        max_posts_per_poll="한 번 동기화할 최근 게시물 개수입니다. 5-100 사이로 입력합니다.",
    )
    @app_commands.choices(
        language=LANGUAGE_CHOICES,
    )
    async def configure(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
        role: discord.Role | None = None,
        enabled: bool | None = None,
        language: app_commands.Choice[str] | None = None,
        max_posts_per_poll: int | None = None,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("서버 안에서만 설정할 수 있어요.", ephemeral=True)
            return

        if max_posts_per_poll is not None and not 5 <= max_posts_per_poll <= 100:
            await interaction.response.send_message(
                "가져올 개수는 5부터 100 사이로 입력해주세요.",
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
            enabled=enabled,
            language=language.value if language else None,
            max_posts_per_poll=max_posts_per_poll,
        )
        channel_text = f"<#{settings.channel_id}>" if settings.channel_id else "미설정"
        role_text = f"<@&{settings.role_id}>" if settings.role_id else "없음"
        enabled_text = "켜짐" if settings.enabled else "꺼짐"
        language_text = _language_label(settings.language)
        max_posts_text = str(settings.max_posts_per_poll)
        embed = discord.Embed(
            title="설정이 완료되었어요~!",
            description=(
                f"채널: {channel_text}\n"
                f"역할 핑: {role_text}\n"
                f"알림: {enabled_text}\n"
                f"언어: {language_text}\n"
                f"동기화 시 가져올 개수: {max_posts_text}"
            ),
            color=discord.Color.from_rgb(179, 28, 28),
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="림피역할해제", description="새 소식 알림의 역할 핑을 제거합니다.")
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

    @app_commands.command(name="림피상태", description="현재 림피 알림 설정을 확인합니다.")
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

        await interaction.response.send_message(
            (
                f"채널: {channel_text}\n"
                f"역할 핑: {role_text}\n"
                f"알림: {enabled_text}\n"
                f"언어: {_language_label(settings.language)}\n"
                f"동기화 시 가져올 개수: {settings.max_posts_per_poll}\n"
                f"뉴스 소스: {source_status}"
            ),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="이전소식보기", description="저장된 림버스 컴퍼니 이전 소식을 다시 봅니다.")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.rename(title="게시물")
    @app_commands.describe(title="게시물의 첫 번째 줄을 선택합니다.")
    async def previous_news(self, interaction: discord.Interaction, title: str) -> None:
        post = self.storage.get_post_by_id_or_title(title)
        if post is None:
            await interaction.response.send_message(
                "아직 저장된 게시물을 찾지 못했어요. 림피가 자동 동기화한 뒤 다시 선택해 주세요.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(embeds=_embeds_for_post(post))
        youtube_content = _youtube_links_content(post)
        if youtube_content:
            await interaction.followup.send(
                content=youtube_content,
                allowed_mentions=discord.AllowedMentions.none(),
            )

    @previous_news.autocomplete("title")
    async def previous_news_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        posts = self.storage.search_posts(
            current,
            limit=25,
        )
        return [
            app_commands.Choice(name=_choice_name(post, include_language=True), value=post.post_id)
            for post in posts
        ]

    @app_commands.command(name="소식동기화", description="한국어, 영어, 일본어 최근 Steam 뉴스를 저장합니다.")
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
            limit = settings.max_posts_per_poll if settings else self.config.max_posts_per_poll
            try:
                posts_by_language = await self._sync_global_news_cache(
                    {language: limit for language in SYNC_LANGUAGES}
                )
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
            f"최근 게시물을 저장했어요: {summary}",
            ephemeral=True,
        )


def _embeds_for_post(post: NewsPost) -> list[discord.Embed]:
    description = post.text[:4096] if post.text else post.url
    embed = discord.Embed(
        title=post.title[:256],
        description=description,
        url=post.url,
        color=discord.Color.from_rgb(179, 28, 28),
        timestamp=post.created_at,
    )
    source_url = str(post.raw.get("source_url") or "https://store.steampowered.com/news/app/1973530")
    embed.set_author(name=post.source_user, url=source_url)
    embed.add_field(name="원문", value=f"[Steam에서 보기]({post.url})", inline=False)
    schedule_text = _schedule_text_for_post(post)
    if schedule_text:
        embed.add_field(name="일정", value=schedule_text, inline=False)
    if post.image_urls:
        embed.set_image(url=post.image_urls[0])

    embeds = [embed]
    for image_url in post.image_urls[1:4]:
        image_embed = discord.Embed(url=post.url, color=discord.Color.from_rgb(179, 28, 28))
        image_embed.set_image(url=image_url)
        embeds.append(image_embed)

    return embeds


def _language_label(language: str) -> str:
    return LANGUAGE_LABELS.get(language, language)


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


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = AppConfig.from_env()
    storage = SQLiteStorage(config.database_path)

    async with aiohttp.ClientSession() as session:
        news_source = build_news_source(config, session)
        bot = LimpiBot(config)

        @bot.event
        async def on_ready() -> None:
            LOGGER.info("Logged in as %s (%s).", bot.user, bot.user.id if bot.user else "unknown")
            await bot.sync_connected_guild_commands()

        await bot.add_cog(NewsCog(bot, config, storage, news_source))
        try:
            await bot.start(config.discord_token)
        finally:
            storage.close()


if __name__ == "__main__":
    asyncio.run(main())
