from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class NewsPost:
    post_id: str
    source_user: str
    url: str
    text: str
    title: str
    created_at: datetime | None
    image_urls: list[str]
    raw: dict


@dataclass(frozen=True)
class GuildSettings:
    guild_id: int
    channel_id: int | None
    role_id: int | None
    post_format: str
    enabled: bool
    last_seen_post_id: str | None
    language: str
    max_posts_per_poll: int
    auto_cleanup_enabled: bool
    auto_cleanup_days: int
    image_delivery: str
    public_news_lookup_allowed: bool
    missed_news_recovery_enabled: bool
    maintenance_notifications_enabled: bool
    last_maintenance_start_notice: str | None
    last_maintenance_update_notice: str | None


@dataclass(frozen=True)
class GuildNewsTarget:
    target_id: int
    guild_id: int
    channel_id: int
    language: str
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True)
class UserSettings:
    user_id: int
    username: str
    nickname: str | None
    language: str
    image_delivery: str | None
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True)
class TrackedMessage:
    guild_id: int
    channel_id: int
    message_id: int
    sent_at: datetime
