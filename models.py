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


@dataclass(frozen=True)
class TrackedMessage:
    guild_id: int
    channel_id: int
    message_id: int
    sent_at: datetime
