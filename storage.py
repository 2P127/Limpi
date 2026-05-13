from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from models import GuildSettings, NewsPost, TrackedMessage, UserSettings


DEFAULT_AUTO_CLEANUP_ENABLED = True
DEFAULT_AUTO_CLEANUP_DAYS = 1
DEFAULT_IMAGE_DELIVERY = "files"
MIN_CLEANUP_DAYS = 1
MAX_CLEANUP_DAYS = 7


class SQLiteStorage:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._initialize()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _initialize(self) -> None:
        with self._lock:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS guild_settings (
                    guild_id INTEGER PRIMARY KEY,
                    channel_id INTEGER,
                    role_id INTEGER,
                    post_format TEXT NOT NULL DEFAULT 'rich',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    last_seen_post_id TEXT,
                    language TEXT NOT NULL DEFAULT 'koreana',
                    max_posts_per_poll INTEGER NOT NULL DEFAULT 30,
                    auto_cleanup_enabled INTEGER NOT NULL DEFAULT 1,
                    auto_cleanup_days INTEGER NOT NULL DEFAULT 1,
                    image_delivery TEXT NOT NULL DEFAULT 'files',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS posts (
                    post_id TEXT PRIMARY KEY,
                    source_user TEXT NOT NULL,
                    url TEXT NOT NULL,
                    text TEXT NOT NULL,
                    title TEXT NOT NULL,
                    created_at TEXT,
                    language TEXT NOT NULL DEFAULT 'koreana',
                    image_urls TEXT NOT NULL DEFAULT '[]',
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    saved_at TEXT NOT NULL
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS guild_seen_posts (
                    guild_id INTEGER NOT NULL,
                    post_id TEXT NOT NULL,
                    seen_at TEXT NOT NULL,
                    announced_at TEXT,
                    PRIMARY KEY (guild_id, post_id)
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tracked_messages (
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    sent_at TEXT NOT NULL,
                    PRIMARY KEY (guild_id, channel_id, message_id)
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS user_settings (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT NOT NULL,
                    nickname TEXT,
                    language TEXT NOT NULL DEFAULT 'koreana',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._ensure_column("guild_settings", "language", "TEXT NOT NULL DEFAULT 'koreana'")
            self._ensure_column(
                "guild_settings",
                "max_posts_per_poll",
                "INTEGER NOT NULL DEFAULT 30",
            )
            self._ensure_column(
                "guild_settings",
                "auto_cleanup_enabled",
                "INTEGER NOT NULL DEFAULT 1",
            )
            self._ensure_column(
                "guild_settings",
                "auto_cleanup_days",
                "INTEGER NOT NULL DEFAULT 1",
            )
            self._ensure_column(
                "guild_settings",
                "image_delivery",
                "TEXT NOT NULL DEFAULT 'files'",
            )
            self._ensure_column("posts", "language", "TEXT NOT NULL DEFAULT 'koreana'")
            self._ensure_column("user_settings", "username", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column("user_settings", "nickname", "TEXT")
            self._ensure_column("user_settings", "language", "TEXT NOT NULL DEFAULT 'koreana'")
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_posts_created_at ON posts(created_at DESC)"
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_posts_title ON posts(title)"
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_posts_language_created_at
                ON posts(language, created_at DESC)
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_guild_seen_posts_post_id
                ON guild_seen_posts(post_id)
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tracked_messages_sent_at
                ON tracked_messages(sent_at)
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_user_settings_updated_at
                ON user_settings(updated_at)
                """
            )
            self._migrate_legacy_post_ids()
            self._connection.commit()

    def _ensure_column(self, table_name: str, column_name: str, definition: str) -> None:
        rows = self._connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        existing_columns = {str(row["name"]) for row in rows}
        if column_name not in existing_columns:
            self._connection.execute(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}"
            )

    def _migrate_legacy_post_ids(self) -> None:
        rows = self._connection.execute(
            """
            SELECT post_id, language, raw_json FROM posts
            WHERE post_id NOT LIKE 'steam:%'
            """
        ).fetchall()
        for row in rows:
            old_id = str(row["post_id"] or "").strip()
            if not old_id:
                continue

            language = _legacy_post_language(row)
            new_id = f"steam:{language}:{old_id}"
            existing = self._connection.execute(
                "SELECT 1 FROM posts WHERE post_id = ?",
                (new_id,),
            ).fetchone()

            self._merge_seen_post_id(old_id, new_id)
            self._connection.execute(
                """
                UPDATE guild_settings
                SET last_seen_post_id = ?
                WHERE last_seen_post_id = ?
                """,
                (new_id, old_id),
            )

            if existing:
                self._connection.execute("DELETE FROM posts WHERE post_id = ?", (old_id,))
            else:
                self._connection.execute(
                    """
                    UPDATE posts
                    SET post_id = ?, language = ?
                    WHERE post_id = ?
                    """,
                    (new_id, language, old_id),
                )

    def _merge_seen_post_id(self, old_id: str, new_id: str) -> None:
        rows = self._connection.execute(
            """
            SELECT guild_id, seen_at, announced_at
            FROM guild_seen_posts
            WHERE post_id = ?
            """,
            (old_id,),
        ).fetchall()
        for row in rows:
            guild_id = int(row["guild_id"])
            existing = self._connection.execute(
                """
                SELECT seen_at, announced_at
                FROM guild_seen_posts
                WHERE guild_id = ? AND post_id = ?
                """,
                (guild_id, new_id),
            ).fetchone()
            if existing:
                self._connection.execute(
                    """
                    UPDATE guild_seen_posts
                    SET seen_at = ?, announced_at = ?
                    WHERE guild_id = ? AND post_id = ?
                    """,
                    (
                        _earliest_text(existing["seen_at"], row["seen_at"]),
                        existing["announced_at"] or row["announced_at"],
                        guild_id,
                        new_id,
                    ),
                )
            else:
                self._connection.execute(
                    """
                    INSERT INTO guild_seen_posts (guild_id, post_id, seen_at, announced_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (guild_id, new_id, row["seen_at"], row["announced_at"]),
                )

        self._connection.execute(
            "DELETE FROM guild_seen_posts WHERE post_id = ?",
            (old_id,),
        )

    def get_settings(self, guild_id: int) -> GuildSettings:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,)
            ).fetchone()

        if row is None:
            return GuildSettings(
                guild_id=guild_id,
                channel_id=None,
                role_id=None,
                post_format="rich",
                enabled=True,
                last_seen_post_id=None,
                language="koreana",
                max_posts_per_poll=30,
                auto_cleanup_enabled=DEFAULT_AUTO_CLEANUP_ENABLED,
                auto_cleanup_days=DEFAULT_AUTO_CLEANUP_DAYS,
                image_delivery=DEFAULT_IMAGE_DELIVERY,
            )

        return self._row_to_settings(row)

    def list_settings(self) -> list[GuildSettings]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM guild_settings ORDER BY guild_id"
            ).fetchall()

        return [self._row_to_settings(row) for row in rows]

    def get_user_settings(self, user_id: int) -> UserSettings:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM user_settings WHERE user_id = ?", (user_id,)
            ).fetchone()

        if row is None:
            return UserSettings(
                user_id=user_id,
                username="",
                nickname=None,
                language="koreana",
                created_at=None,
                updated_at=None,
            )

        return self._row_to_user_settings(row)

    def upsert_user_settings(
        self,
        user_id: int,
        *,
        username: str,
        nickname: str | None,
        language: str | None = None,
    ) -> UserSettings:
        current = self.get_user_settings(user_id)
        now = _now_iso()
        next_language = language if language is not None else current.language

        with self._lock:
            self._connection.execute(
                """
                INSERT INTO user_settings (
                    user_id, username, nickname, language, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = excluded.username,
                    nickname = excluded.nickname,
                    language = excluded.language,
                    updated_at = excluded.updated_at
                """,
                (user_id, username, nickname, next_language, now, now),
            )
            self._connection.commit()

        return self.get_user_settings(user_id)

    def update_user_language(
        self,
        user_id: int,
        *,
        username: str,
        nickname: str | None,
        language: str,
    ) -> UserSettings:
        return self.upsert_user_settings(
            user_id,
            username=username,
            nickname=nickname,
            language=language,
        )

    def update_settings(
        self,
        guild_id: int,
        *,
        channel_id: int | None = None,
        role_id: int | None = None,
        post_format: str | None = None,
        enabled: bool | None = None,
        language: str | None = None,
        max_posts_per_poll: int | None = None,
        auto_cleanup_enabled: bool | None = None,
        auto_cleanup_days: int | None = None,
        image_delivery: str | None = None,
    ) -> GuildSettings:
        current = self.get_settings(guild_id)
        now = _now_iso()
        next_settings = GuildSettings(
            guild_id=guild_id,
            channel_id=channel_id if channel_id is not None else current.channel_id,
            role_id=role_id if role_id is not None else current.role_id,
            post_format=post_format if post_format is not None else current.post_format,
            enabled=enabled if enabled is not None else current.enabled,
            last_seen_post_id=current.last_seen_post_id,
            language=language if language is not None else current.language,
            max_posts_per_poll=(
                max_posts_per_poll
                if max_posts_per_poll is not None
                else current.max_posts_per_poll
            ),
            auto_cleanup_enabled=(
                auto_cleanup_enabled
                if auto_cleanup_enabled is not None
                else current.auto_cleanup_enabled
            ),
            auto_cleanup_days=(
                auto_cleanup_days
                if auto_cleanup_days is not None
                else current.auto_cleanup_days
            ),
            image_delivery=(
                image_delivery
                if image_delivery is not None
                else current.image_delivery
            ),
        )

        with self._lock:
            self._connection.execute(
                """
                INSERT INTO guild_settings (
                    guild_id, channel_id, role_id, post_format, enabled, language,
                    max_posts_per_poll, auto_cleanup_enabled, auto_cleanup_days,
                    image_delivery, last_seen_post_id, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    channel_id = excluded.channel_id,
                    role_id = excluded.role_id,
                    post_format = excluded.post_format,
                    enabled = excluded.enabled,
                    language = excluded.language,
                    max_posts_per_poll = excluded.max_posts_per_poll,
                    auto_cleanup_enabled = excluded.auto_cleanup_enabled,
                    auto_cleanup_days = excluded.auto_cleanup_days,
                    image_delivery = excluded.image_delivery,
                    updated_at = excluded.updated_at
                """,
                (
                    next_settings.guild_id,
                    next_settings.channel_id,
                    next_settings.role_id,
                    next_settings.post_format,
                    int(next_settings.enabled),
                    next_settings.language,
                    next_settings.max_posts_per_poll,
                    int(next_settings.auto_cleanup_enabled),
                    next_settings.auto_cleanup_days,
                    next_settings.image_delivery,
                    next_settings.last_seen_post_id,
                    now,
                    now,
                ),
            )
            self._connection.commit()

        return next_settings

    def clear_role(self, guild_id: int) -> GuildSettings:
        now = _now_iso()
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO guild_settings (
                    guild_id, channel_id, role_id, post_format, enabled, language,
                    max_posts_per_poll, auto_cleanup_enabled, auto_cleanup_days,
                    image_delivery, last_seen_post_id, created_at, updated_at
                )
                VALUES (?, NULL, NULL, 'rich', 1, 'koreana', 30, 1, 1, 'files', NULL, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    role_id = NULL,
                    updated_at = excluded.updated_at
                """,
                (guild_id, now, now),
            )
            self._connection.commit()

        return self.get_settings(guild_id)

    def set_last_seen_post_id(self, guild_id: int, post_id: str) -> None:
        now = _now_iso()
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO guild_settings (
                    guild_id, channel_id, role_id, post_format, enabled, language,
                    max_posts_per_poll, auto_cleanup_enabled, auto_cleanup_days,
                    image_delivery, last_seen_post_id, created_at, updated_at
                )
                VALUES (?, NULL, NULL, 'rich', 1, 'koreana', 30, 1, 1, 'files', ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    last_seen_post_id = excluded.last_seen_post_id,
                    updated_at = excluded.updated_at
                """,
                (guild_id, post_id, now, now),
            )
            self._connection.commit()

    def get_seen_post_ids(self, guild_id: int, post_ids: Iterable[str]) -> set[str]:
        ids = list(dict.fromkeys(post_ids))
        if not ids:
            return set()

        placeholders = ", ".join("?" for _ in ids)
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT post_id FROM guild_seen_posts
                WHERE guild_id = ? AND post_id IN ({placeholders})
                """,
                (guild_id, *ids),
            ).fetchall()

        return {str(row["post_id"]) for row in rows}

    def has_seen_posts(self, guild_id: int) -> bool:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT 1 FROM guild_seen_posts
                WHERE guild_id = ?
                LIMIT 1
                """,
                (guild_id,),
            ).fetchone()

        return row is not None

    def mark_posts_seen(
        self,
        guild_id: int,
        post_ids: Iterable[str],
        *,
        announced: bool = False,
    ) -> None:
        ids = list(dict.fromkeys(post_ids))
        if not ids:
            return

        now = _now_iso()
        announced_at = now if announced else None
        with self._lock:
            self._connection.executemany(
                """
                INSERT INTO guild_seen_posts (guild_id, post_id, seen_at, announced_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id, post_id) DO UPDATE SET
                    seen_at = excluded.seen_at,
                    announced_at = COALESCE(
                        excluded.announced_at,
                        guild_seen_posts.announced_at
                    )
                """,
                ((guild_id, post_id, now, announced_at) for post_id in ids),
            )
            self._connection.commit()

    def save_posts(self, posts: Iterable[NewsPost]) -> int:
        saved = 0
        now = _now_iso()
        with self._lock:
            for post in posts:
                cursor = self._connection.execute(
                    """
                    INSERT INTO posts (
                        post_id, source_user, url, text, title, created_at, language,
                        image_urls, raw_json, saved_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(post_id) DO UPDATE SET
                        source_user = excluded.source_user,
                        url = excluded.url,
                        text = excluded.text,
                        title = excluded.title,
                        created_at = excluded.created_at,
                        language = excluded.language,
                        image_urls = excluded.image_urls,
                        raw_json = excluded.raw_json
                    """,
                    (
                        post.post_id,
                        post.source_user,
                        post.url,
                        post.text,
                        post.title,
                        _datetime_to_iso(post.created_at),
                        str(post.raw.get("language") or "koreana"),
                        json.dumps(post.image_urls, ensure_ascii=False),
                        json.dumps(post.raw, ensure_ascii=False),
                        now,
                    ),
                )
                if cursor.rowcount:
                    saved += 1
            self._connection.commit()

        return saved

    def get_post(self, post_id: str) -> NewsPost | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM posts WHERE post_id = ?", (post_id,)
            ).fetchone()

        return self._row_to_post(row) if row else None

    def get_post_by_id_or_title(self, value: str, language: str | None = None) -> NewsPost | None:
        post = self.get_post(value)
        if post is not None:
            return post

        where = "title = ?"
        params: tuple[object, ...]
        if language:
            where += " AND language = ?"
            params = (value, language)
        else:
            params = (value,)

        with self._lock:
            row = self._connection.execute(
                f"""
                SELECT * FROM posts
                WHERE {where}
                ORDER BY created_at DESC
                LIMIT 1
                """,
                params,
            ).fetchone()

        return self._row_to_post(row) if row else None

    def get_latest_post(self, language: str | None = None) -> NewsPost | None:
        params: tuple[object, ...]
        if language:
            sql = """
                SELECT * FROM posts
                WHERE language = ?
                ORDER BY created_at DESC
                LIMIT 1
            """
            params = (language,)
        else:
            sql = "SELECT * FROM posts ORDER BY created_at DESC LIMIT 1"
            params = ()

        with self._lock:
            row = self._connection.execute(sql, params).fetchone()

        return self._row_to_post(row) if row else None

    def search_posts(
        self, query: str, limit: int = 25, language: str | None = None
    ) -> list[NewsPost]:
        query = query.strip()
        conditions = []
        params_list: list[object] = []
        if query:
            conditions.append("title LIKE ?")
            params_list.append(f"%{query}%")
        if language:
            conditions.append("language = ?")
            params_list.append(language)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"""
            SELECT * FROM posts
            {where_clause}
            ORDER BY created_at DESC
            LIMIT ?
        """
        fetch_limit = max(limit * 4, limit)
        params_list.append(fetch_limit)
        params: tuple[object, ...] = tuple(params_list)

        with self._lock:
            rows = self._connection.execute(sql, params).fetchall()

        posts = [self._row_to_post(row) for row in rows]
        return _dedupe_posts_for_choices(posts, limit)

    def add_tracked_message(
        self, guild_id: int, channel_id: int, message_id: int
    ) -> None:
        now = _now_iso()
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO tracked_messages (guild_id, channel_id, message_id, sent_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id, channel_id, message_id) DO UPDATE SET
                    sent_at = excluded.sent_at
                """,
                (guild_id, channel_id, message_id, now),
            )
            self._connection.commit()

    def list_tracked_messages(self) -> list[TrackedMessage]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT guild_id, channel_id, message_id, sent_at FROM tracked_messages"
            ).fetchall()
        return [
            TrackedMessage(
                guild_id=int(row["guild_id"]),
                channel_id=int(row["channel_id"]),
                message_id=int(row["message_id"]),
                sent_at=_datetime_from_iso(row["sent_at"]) or datetime.now(timezone.utc),
            )
            for row in rows
        ]

    def delete_tracked_message(
        self, guild_id: int, channel_id: int, message_id: int
    ) -> None:
        with self._lock:
            self._connection.execute(
                """
                DELETE FROM tracked_messages
                WHERE guild_id = ? AND channel_id = ? AND message_id = ?
                """,
                (guild_id, channel_id, message_id),
            )
            self._connection.commit()

    @staticmethod
    def _row_to_settings(row: sqlite3.Row) -> GuildSettings:
        cleanup_days = int(row["auto_cleanup_days"] or DEFAULT_AUTO_CLEANUP_DAYS)
        cleanup_days = max(MIN_CLEANUP_DAYS, min(MAX_CLEANUP_DAYS, cleanup_days))
        image_delivery = str(row["image_delivery"] or DEFAULT_IMAGE_DELIVERY)
        if image_delivery not in {"files", "embeds"}:
            image_delivery = DEFAULT_IMAGE_DELIVERY
        return GuildSettings(
            guild_id=int(row["guild_id"]),
            channel_id=_optional_int(row["channel_id"]),
            role_id=_optional_int(row["role_id"]),
            post_format=str(row["post_format"]),
            enabled=bool(row["enabled"]),
            last_seen_post_id=row["last_seen_post_id"],
            language=str(row["language"] or "koreana"),
            max_posts_per_poll=int(row["max_posts_per_poll"] or 30),
            auto_cleanup_enabled=bool(row["auto_cleanup_enabled"]),
            auto_cleanup_days=cleanup_days,
            image_delivery=image_delivery,
        )

    @staticmethod
    def _row_to_user_settings(row: sqlite3.Row) -> UserSettings:
        return UserSettings(
            user_id=int(row["user_id"]),
            username=str(row["username"] or ""),
            nickname=str(row["nickname"]) if row["nickname"] is not None else None,
            language=str(row["language"] or "koreana"),
            created_at=_datetime_from_iso(row["created_at"]),
            updated_at=_datetime_from_iso(row["updated_at"]),
        )

    @staticmethod
    def _row_to_post(row: sqlite3.Row) -> NewsPost:
        return NewsPost(
            post_id=str(row["post_id"]),
            source_user=str(row["source_user"]),
            url=str(row["url"]),
            text=str(row["text"]),
            title=str(row["title"]),
            created_at=_datetime_from_iso(row["created_at"]),
            image_urls=json.loads(row["image_urls"]),
            raw=json.loads(row["raw_json"]),
        )


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _legacy_post_language(row: sqlite3.Row) -> str:
    try:
        raw = json.loads(row["raw_json"] or "{}")
    except (TypeError, json.JSONDecodeError):
        raw = {}

    raw_language = raw.get("language") if isinstance(raw, dict) else None
    raw_language_text = str(raw_language or "").strip()
    if raw_language_text:
        return raw_language_text

    language = str(row["language"] or "").strip()
    return language or "koreana"


def _earliest_text(left: object, right: object) -> str:
    left_text = str(left or "")
    right_text = str(right or "")
    if not left_text:
        return right_text
    if not right_text:
        return left_text
    return min(left_text, right_text)


def _dedupe_posts_for_choices(posts: list[NewsPost], limit: int) -> list[NewsPost]:
    deduped: list[NewsPost] = []
    seen_keys: set[tuple[str, str]] = set()
    for post in posts:
        language = str(post.raw.get("language") or "")
        title = post.title.strip().casefold()
        key = (language, title or post.post_id)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(post)
        if len(deduped) >= limit:
            break
    return deduped


def _datetime_to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc).isoformat()


def _datetime_from_iso(value: str | None) -> datetime | None:
    if not value:
        return None

    return datetime.fromisoformat(value)
