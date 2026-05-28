from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

LOGGER = logging.getLogger(__name__)

from models import (
    GuildChzzkTarget,
    GuildNewsTarget,
    GuildTwitterTarget,
    GuildYoutubeTarget,
    GuildSettings,
    NewsPost,
    TrackedMessage,
    TwitterPost,
    UserSettings,
)


DEFAULT_AUTO_CLEANUP_ENABLED = True
DEFAULT_AUTO_CLEANUP_DAYS = 1
DEFAULT_IMAGE_DELIVERY = "files"
DEFAULT_NOTIFICATION_BANNER = "림피 배너.png"
DISABLED_NOTIFICATION_BANNER = "none"
DEFAULT_PUBLIC_NEWS_LOOKUP_ALLOWED = True
DEFAULT_MISSED_NEWS_RECOVERY_ENABLED = False
DEFAULT_MAINTENANCE_NOTIFICATIONS_ENABLED = False
DEFAULT_NEWS_SOURCE_MODE = "both"
MIN_CLEANUP_DAYS = 1
MAX_CLEANUP_DAYS = 7


def _post_content_hash(post: "NewsPost") -> str:
    raw = f"{post.title}\x00{post.text}\x00{json.dumps(post.image_urls, sort_keys=True)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _column_default_definition(definition: str) -> str:
    upper = definition.upper()
    if "TEXT" in upper:
        return f"{definition} DEFAULT ''"
    if "INTEGER" in upper:
        return f"{definition} DEFAULT 0"
    if "REAL" in upper:
        return f"{definition} DEFAULT 0"
    return f"{definition} DEFAULT ''"


class SQLiteStorage:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._initialize()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    _TABLE_SCHEMAS: dict[str, str] = {
        "guild_settings": """
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
                notification_banner TEXT DEFAULT '림피 배너.png',
                public_news_lookup_allowed INTEGER NOT NULL DEFAULT 1,
                missed_news_recovery_enabled INTEGER NOT NULL DEFAULT 0,
                maintenance_notifications_enabled INTEGER NOT NULL DEFAULT 0,
                news_source_mode TEXT NOT NULL DEFAULT 'both',
                last_maintenance_start_notice TEXT,
                last_maintenance_update_notice TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """,
        "posts": """
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
                content_hash TEXT NOT NULL DEFAULT '',
                saved_at TEXT NOT NULL
            )
        """,
        "guild_seen_posts": """
            CREATE TABLE IF NOT EXISTS guild_seen_posts (
                guild_id INTEGER NOT NULL,
                post_id TEXT NOT NULL,
                seen_at TEXT NOT NULL,
                announced_at TEXT,
                PRIMARY KEY (guild_id, post_id)
            )
        """,
        "twitter_posts": """
            CREATE TABLE IF NOT EXISTS twitter_posts (
                post_id TEXT PRIMARY KEY,
                author_username TEXT NOT NULL,
                url TEXT NOT NULL,
                text TEXT NOT NULL,
                title TEXT NOT NULL,
                created_at TEXT,
                image_urls TEXT NOT NULL DEFAULT '[]',
                raw_json TEXT NOT NULL DEFAULT '{}',
                saved_at TEXT NOT NULL
            )
        """,
        "guild_news_targets": """
            CREATE TABLE IF NOT EXISTS guild_news_targets (
                target_id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                language TEXT NOT NULL DEFAULT 'koreana',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(guild_id, channel_id)
            )
        """,
        "guild_twitter_targets": """
            CREATE TABLE IF NOT EXISTS guild_twitter_targets (
                guild_id INTEGER PRIMARY KEY,
                channel_id INTEGER NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                last_seen_post_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """,
        "guild_chzzk_targets": """
            CREATE TABLE IF NOT EXISTS guild_chzzk_targets (
                guild_id INTEGER PRIMARY KEY,
                channel_id INTEGER NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                last_live_id TEXT,
                is_live INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """,
        "guild_youtube_targets": """
            CREATE TABLE IF NOT EXISTS guild_youtube_targets (
                guild_id INTEGER PRIMARY KEY,
                channel_id INTEGER NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                last_live_id TEXT,
                is_live INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """,
        "guild_news_target_seen_posts": """
            CREATE TABLE IF NOT EXISTS guild_news_target_seen_posts (
                target_id INTEGER NOT NULL,
                post_id TEXT NOT NULL,
                seen_at TEXT NOT NULL,
                announced_at TEXT,
                PRIMARY KEY (target_id, post_id),
                FOREIGN KEY (target_id) REFERENCES guild_news_targets(target_id)
                    ON DELETE CASCADE
            )
        """,
        "guild_news_target_pending_updates": """
            CREATE TABLE IF NOT EXISTS guild_news_target_pending_updates (
                target_id INTEGER NOT NULL,
                post_id TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                queued_at TEXT NOT NULL,
                sent_at TEXT,
                PRIMARY KEY (target_id, post_id),
                FOREIGN KEY (target_id) REFERENCES guild_news_targets(target_id)
                    ON DELETE CASCADE
            )
        """,
        "tracked_messages": """
            CREATE TABLE IF NOT EXISTS tracked_messages (
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                sent_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, channel_id, message_id)
            )
        """,
        "user_settings": """
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id INTEGER PRIMARY KEY,
                username TEXT NOT NULL DEFAULT '',
                nickname TEXT,
                language TEXT NOT NULL DEFAULT 'koreana',
                image_delivery TEXT,
                news_banner TEXT DEFAULT '림피 배너.png',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """,
    }

    def _initialize(self) -> None:
        with self._lock:
            self._connection.execute("PRAGMA journal_mode=WAL")

            for table_name, ddl in self._TABLE_SCHEMAS.items():
                self._connection.execute(ddl)
                self._auto_migrate_table(table_name, ddl)

            self._backfill_schema_defaults()
            self._connection.execute(
                "UPDATE guild_settings SET image_delivery = 'files' WHERE image_delivery = 'embeds'"
            )
            self._connection.execute(
                "UPDATE user_settings SET image_delivery = NULL WHERE image_delivery = 'embeds'"
            )
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
                CREATE INDEX IF NOT EXISTS idx_twitter_posts_created_at
                ON twitter_posts(created_at DESC)
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_twitter_posts_title
                ON twitter_posts(title)
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
                CREATE INDEX IF NOT EXISTS idx_guild_twitter_targets_channel
                ON guild_twitter_targets(channel_id)
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_guild_chzzk_targets_channel
                ON guild_chzzk_targets(channel_id)
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_guild_youtube_targets_channel
                ON guild_youtube_targets(channel_id)
                """
            )
            self._dedupe_news_target_channels()
            self._dedupe_news_target_languages()
            self._connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_guild_news_targets_guild_channel
                ON guild_news_targets(guild_id, channel_id)
                """
            )
            self._connection.execute("DROP INDEX IF EXISTS idx_guild_news_targets_guild_language")
            self._connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_guild_news_targets_unique_guild_language
                ON guild_news_targets(guild_id, language)
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_guild_news_target_seen_posts_post_id
                ON guild_news_target_seen_posts(post_id)
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_guild_news_target_pending_updates_sent_at
                ON guild_news_target_pending_updates(sent_at, queued_at)
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
            self._migrate_legacy_news_targets()
            self._connection.commit()

    def _ensure_column(self, table_name: str, column_name: str, definition: str) -> None:
        rows = self._connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        existing_columns = {str(row["name"]) for row in rows}
        if column_name not in existing_columns:
            self._add_missing_column(table_name, column_name, definition)

    def _add_missing_column(self, table_name: str, column_name: str, definition: str) -> None:
        sql = f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}"
        try:
            self._connection.execute(sql)
        except sqlite3.OperationalError as exc:
            message = str(exc)
            needs_default = (
                "Cannot add a NOT NULL column with default value NULL" in message
                and "DEFAULT" not in definition.upper()
            )
            if not needs_default:
                raise
            fallback = _column_default_definition(definition)
            LOGGER.info(
                "Schema migration: adding %s.%s with fallback default (%s)",
                table_name,
                column_name,
                fallback,
            )
            self._connection.execute(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {fallback}"
            )

    def _auto_migrate_table(self, table_name: str, ddl: str) -> None:
        import re

        rows = self._connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        existing = {str(row["name"]) for row in rows}

        start = ddl.index("(") + 1
        end = ddl.rindex(")")
        body = ddl[start:end]

        depth, current, clauses = 0, [], []
        for ch in body:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            if ch == "," and depth == 0:
                clauses.append("".join(current).strip())
                current = []
            else:
                current.append(ch)
        if current:
            clauses.append("".join(current).strip())

        _CONSTRAINT_KEYWORDS = {"PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT"}
        for clause in clauses:
            clause = clause.strip()
            if not clause:
                continue
            m = re.match(r"[A-Za-z_]+", clause)
            first_token = m.group(0).upper() if m else ""
            if first_token in _CONSTRAINT_KEYWORDS:
                continue
            col_name = first_token.lower()
            col_def = clause[len(first_token):].strip()
            if col_name in existing:
                continue
            LOGGER.info("스키마 마이그레이션: %s.%s 컬럼 추가 (%s)", table_name, col_name, col_def)
            self._add_missing_column(table_name, col_name, col_def)

    def _backfill_schema_defaults(self) -> None:
        now = _now_iso()
        self._connection.execute(
            """
            UPDATE guild_settings
            SET
                post_format = COALESCE(NULLIF(post_format, ''), 'rich'),
                enabled = COALESCE(enabled, 1),
                language = COALESCE(NULLIF(language, ''), 'koreana'),
                max_posts_per_poll = COALESCE(max_posts_per_poll, 30),
                auto_cleanup_enabled = COALESCE(auto_cleanup_enabled, 1),
                auto_cleanup_days = COALESCE(auto_cleanup_days, 1),
                image_delivery = COALESCE(NULLIF(image_delivery, ''), 'files'),
                notification_banner = COALESCE(NULLIF(notification_banner, ''), ?),
                public_news_lookup_allowed = COALESCE(public_news_lookup_allowed, 1),
                missed_news_recovery_enabled = COALESCE(missed_news_recovery_enabled, 0),
                maintenance_notifications_enabled = COALESCE(maintenance_notifications_enabled, 0),
                news_source_mode = COALESCE(NULLIF(news_source_mode, ''), ?),
                created_at = COALESCE(NULLIF(created_at, ''), ?),
                updated_at = COALESCE(NULLIF(updated_at, ''), ?)
            """,
            (DEFAULT_NOTIFICATION_BANNER, DEFAULT_NEWS_SOURCE_MODE, now, now),
        )
        self._connection.execute(
            """
            UPDATE posts
            SET
                source_user = COALESCE(NULLIF(source_user, ''), 'ProjectMoon'),
                url = COALESCE(url, ''),
                text = COALESCE(text, ''),
                title = COALESCE(NULLIF(title, ''), COALESCE(NULLIF(text, ''), post_id)),
                language = COALESCE(NULLIF(language, ''), 'koreana'),
                image_urls = COALESCE(NULLIF(image_urls, ''), '[]'),
                raw_json = COALESCE(NULLIF(raw_json, ''), '{}'),
                content_hash = COALESCE(content_hash, ''),
                saved_at = COALESCE(NULLIF(saved_at, ''), ?)
            """,
            (now,),
        )
        self._connection.execute(
            """
            UPDATE twitter_posts
            SET
                author_username = COALESCE(NULLIF(author_username, ''), 'LimbusCompany_B'),
                url = COALESCE(url, ''),
                text = COALESCE(text, ''),
                title = COALESCE(NULLIF(title, ''), COALESCE(NULLIF(text, ''), post_id)),
                image_urls = COALESCE(NULLIF(image_urls, ''), '[]'),
                raw_json = COALESCE(NULLIF(raw_json, ''), '{}'),
                saved_at = COALESCE(NULLIF(saved_at, ''), ?)
            """,
            (now,),
        )
        self._connection.execute(
            """
            UPDATE guild_news_targets
            SET
                language = COALESCE(NULLIF(language, ''), 'koreana'),
                created_at = COALESCE(NULLIF(created_at, ''), ?),
                updated_at = COALESCE(NULLIF(updated_at, ''), ?)
            """,
            (now, now),
        )
        self._connection.execute(
            """
            UPDATE guild_twitter_targets
            SET
                enabled = COALESCE(enabled, 1),
                created_at = COALESCE(NULLIF(created_at, ''), ?),
                updated_at = COALESCE(NULLIF(updated_at, ''), ?)
            """,
            (now, now),
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

    def _migrate_legacy_news_targets(self) -> None:
        rows = self._connection.execute(
            """
            SELECT guild_id, channel_id, language, created_at, updated_at
            FROM guild_settings
            WHERE channel_id IS NOT NULL
            """
        ).fetchall()
        for row in rows:
            self._connection.execute(
                """
                INSERT OR IGNORE INTO guild_news_targets (
                    guild_id, channel_id, language, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    int(row["guild_id"]),
                    int(row["channel_id"]),
                    str(row["language"] or "koreana"),
                    row["created_at"] or _now_iso(),
                    row["updated_at"] or _now_iso(),
                ),
            )
            target = self._connection.execute(
                """
                SELECT target_id FROM guild_news_targets
                WHERE guild_id = ? AND channel_id = ? AND language = ?
                """,
                (
                    int(row["guild_id"]),
                    int(row["channel_id"]),
                    str(row["language"] or "koreana"),
                ),
            ).fetchone()
            if target is None:
                continue
            self._connection.execute(
                """
                INSERT OR IGNORE INTO guild_news_target_seen_posts (
                    target_id, post_id, seen_at, announced_at
                )
                SELECT ?, gsp.post_id, gsp.seen_at, gsp.announced_at
                FROM guild_seen_posts gsp
                JOIN posts p ON p.post_id = gsp.post_id
                WHERE gsp.guild_id = ? AND p.language = ?
                """,
                (
                    int(target["target_id"]),
                    int(row["guild_id"]),
                    str(row["language"] or "koreana"),
                ),
            )

    def _dedupe_news_target_channels(self) -> None:
        duplicate_rows = self._connection.execute(
            """
            SELECT guild_id, channel_id
            FROM guild_news_targets
            GROUP BY guild_id, channel_id
            HAVING COUNT(*) > 1
            """
        ).fetchall()
        for duplicate in duplicate_rows:
            rows = self._connection.execute(
                """
                SELECT target_id
                FROM guild_news_targets
                WHERE guild_id = ? AND channel_id = ?
                ORDER BY updated_at DESC, target_id DESC
                """,
                (int(duplicate["guild_id"]), int(duplicate["channel_id"])),
            ).fetchall()
            keep_id = int(rows[0]["target_id"])
            delete_ids = [int(row["target_id"]) for row in rows[1:]]
            if not delete_ids:
                continue

            placeholders = ", ".join("?" for _ in delete_ids)
            self._connection.execute(
                f"DELETE FROM guild_news_target_seen_posts WHERE target_id IN ({placeholders})",
                delete_ids,
            )
            self._connection.execute(
                f"DELETE FROM guild_news_targets WHERE target_id IN ({placeholders})",
                delete_ids,
            )

    def _dedupe_news_target_languages(self) -> None:
        duplicate_rows = self._connection.execute(
            """
            SELECT guild_id, language
            FROM guild_news_targets
            GROUP BY guild_id, language
            HAVING COUNT(*) > 1
            """
        ).fetchall()
        for duplicate in duplicate_rows:
            rows = self._connection.execute(
                """
                SELECT target_id
                FROM guild_news_targets
                WHERE guild_id = ? AND language = ?
                ORDER BY updated_at DESC, target_id DESC
                """,
                (int(duplicate["guild_id"]), str(duplicate["language"] or "koreana")),
            ).fetchall()
            keep_id = int(rows[0]["target_id"])
            delete_ids = [int(row["target_id"]) for row in rows[1:] if int(row["target_id"]) != keep_id]
            if not delete_ids:
                continue

            placeholders = ", ".join("?" for _ in delete_ids)
            self._connection.execute(
                f"DELETE FROM guild_news_target_seen_posts WHERE target_id IN ({placeholders})",
                delete_ids,
            )
            self._connection.execute(
                f"DELETE FROM guild_news_targets WHERE target_id IN ({placeholders})",
                delete_ids,
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
                notification_banner=DEFAULT_NOTIFICATION_BANNER,
                public_news_lookup_allowed=DEFAULT_PUBLIC_NEWS_LOOKUP_ALLOWED,
                missed_news_recovery_enabled=DEFAULT_MISSED_NEWS_RECOVERY_ENABLED,
                maintenance_notifications_enabled=DEFAULT_MAINTENANCE_NOTIFICATIONS_ENABLED,
                news_source_mode=DEFAULT_NEWS_SOURCE_MODE,
                last_maintenance_start_notice=None,
                last_maintenance_update_notice=None,
            )

        return self._row_to_settings(row)

    def ensure_guild_settings(self, guild_id: int) -> tuple[GuildSettings, bool]:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,)
            ).fetchone()

        if row is not None:
            return self._row_to_settings(row), False

        settings = self.update_settings(guild_id)
        return settings, True

    def list_settings(self) -> list[GuildSettings]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM guild_settings ORDER BY guild_id"
            ).fetchall()

        return [self._row_to_settings(row) for row in rows]

    def list_news_targets(self, guild_id: int) -> list[GuildNewsTarget]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT target_id, guild_id, channel_id, language, created_at, updated_at
                FROM guild_news_targets
                WHERE guild_id = ?
                ORDER BY language, channel_id
                """,
                (guild_id,),
            ).fetchall()

        return [self._row_to_news_target(row) for row in rows]

    def list_all_news_targets(self) -> list[GuildNewsTarget]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT target_id, guild_id, channel_id, language, created_at, updated_at
                FROM guild_news_targets
                ORDER BY guild_id, language, channel_id
                """
            ).fetchall()

        return [self._row_to_news_target(row) for row in rows]

    def list_news_targets_for_language(self, language: str) -> list[GuildNewsTarget]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT target_id, guild_id, channel_id, language, created_at, updated_at
                FROM guild_news_targets
                WHERE language = ?
                ORDER BY guild_id, channel_id
                """,
                (language,),
            ).fetchall()

        return [self._row_to_news_target(row) for row in rows]

    def upsert_news_target(
        self,
        guild_id: int,
        *,
        channel_id: int,
        language: str,
    ) -> GuildNewsTarget:
        now = _now_iso()
        with self._lock:
            existing = self._connection.execute(
                """
                SELECT target_id, language
                FROM guild_news_targets
                WHERE guild_id = ? AND channel_id = ?
                """,
                (guild_id, channel_id),
            ).fetchone()
            if existing is not None:
                target_id = int(existing["target_id"])
                old_language = str(existing["language"] or "koreana")
                language_rows = self._connection.execute(
                    """
                    SELECT target_id
                    FROM guild_news_targets
                    WHERE guild_id = ? AND language = ? AND target_id != ?
                    """,
                    (guild_id, language, target_id),
                ).fetchall()
                language_delete_ids = [int(row["target_id"]) for row in language_rows]
                if language_delete_ids:
                    placeholders = ", ".join("?" for _ in language_delete_ids)
                    self._connection.execute(
                        f"DELETE FROM guild_news_target_seen_posts WHERE target_id IN ({placeholders})",
                        language_delete_ids,
                    )
                    self._connection.execute(
                        f"DELETE FROM guild_news_targets WHERE target_id IN ({placeholders})",
                        language_delete_ids,
                    )
                if old_language != language:
                    self._connection.execute(
                        "DELETE FROM guild_news_target_seen_posts WHERE target_id = ?",
                        (target_id,),
                    )
                self._connection.execute(
                    """
                    UPDATE guild_news_targets
                    SET language = ?, updated_at = ?
                    WHERE target_id = ?
                    """,
                    (language, now, target_id),
                )
            else:
                language_rows = self._connection.execute(
                    """
                    SELECT target_id
                    FROM guild_news_targets
                    WHERE guild_id = ? AND language = ?
                    """,
                    (guild_id, language),
                ).fetchall()
                language_delete_ids = [int(row["target_id"]) for row in language_rows]
                if language_delete_ids:
                    placeholders = ", ".join("?" for _ in language_delete_ids)
                    self._connection.execute(
                        f"DELETE FROM guild_news_target_seen_posts WHERE target_id IN ({placeholders})",
                        language_delete_ids,
                    )
                    self._connection.execute(
                        f"DELETE FROM guild_news_targets WHERE target_id IN ({placeholders})",
                        language_delete_ids,
                    )
                self._connection.execute(
                    """
                    INSERT INTO guild_news_targets (
                        guild_id, channel_id, language, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (guild_id, channel_id, language, now, now),
                )
            self._connection.commit()
            row = self._connection.execute(
                """
                SELECT target_id, guild_id, channel_id, language, created_at, updated_at
                FROM guild_news_targets
                WHERE guild_id = ? AND channel_id = ?
                """,
                (guild_id, channel_id),
            ).fetchone()

        if row is None:
            raise RuntimeError("Failed to upsert guild news target")
        return self._row_to_news_target(row)

    def get_news_target_by_channel(
        self,
        guild_id: int,
        *,
        channel_id: int,
    ) -> GuildNewsTarget | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT target_id, guild_id, channel_id, language, created_at, updated_at
                FROM guild_news_targets
                WHERE guild_id = ? AND channel_id = ?
                """,
                (guild_id, channel_id),
            ).fetchone()

        return self._row_to_news_target(row) if row is not None else None

    def get_news_target(
        self,
        guild_id: int,
        *,
        channel_id: int,
        language: str,
    ) -> GuildNewsTarget | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT target_id, guild_id, channel_id, language, created_at, updated_at
                FROM guild_news_targets
                WHERE guild_id = ? AND channel_id = ? AND language = ?
                """,
                (guild_id, channel_id, language),
            ).fetchone()

        return self._row_to_news_target(row) if row is not None else None

    def delete_news_target(
        self,
        guild_id: int,
        *,
        channel_id: int,
        language: str,
    ) -> bool:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT target_id FROM guild_news_targets
                WHERE guild_id = ? AND channel_id = ? AND language = ?
                """,
                (guild_id, channel_id, language),
            ).fetchone()
            if row is None:
                return False

            target_id = int(row["target_id"])
            self._connection.execute(
                "DELETE FROM guild_news_target_seen_posts WHERE target_id = ?",
                (target_id,),
            )
            self._connection.execute(
                "DELETE FROM guild_news_targets WHERE target_id = ?",
                (target_id,),
            )
            self._connection.commit()
            return True

    def delete_chzzk_target(self, guild_id: int) -> bool:
        with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM guild_chzzk_targets WHERE guild_id = ?",
                (guild_id,),
            )
            self._connection.commit()
        return cursor.rowcount > 0

    def delete_youtube_target(self, guild_id: int) -> bool:
        with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM guild_youtube_targets WHERE guild_id = ?",
                (guild_id,),
            )
            self._connection.commit()
        return cursor.rowcount > 0

    def get_twitter_target(self, guild_id: int) -> GuildTwitterTarget | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT guild_id, channel_id, enabled, last_seen_post_id, created_at, updated_at
                FROM guild_twitter_targets
                WHERE guild_id = ?
                """,
                (guild_id,),
            ).fetchone()
        return self._row_to_twitter_target(row) if row is not None else None

    def list_twitter_targets(self) -> list[GuildTwitterTarget]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT guild_id, channel_id, enabled, last_seen_post_id, created_at, updated_at
                FROM guild_twitter_targets
                WHERE enabled = 1
                ORDER BY guild_id
                """
            ).fetchall()
        return [self._row_to_twitter_target(row) for row in rows]

    def upsert_twitter_target(
        self,
        guild_id: int,
        *,
        channel_id: int,
        enabled: bool = True,
        last_seen_post_id: str | None = None,
    ) -> GuildTwitterTarget:
        current = self.get_twitter_target(guild_id)
        now = _now_iso()
        next_last_seen = (
            last_seen_post_id if last_seen_post_id is not None else (
                current.last_seen_post_id if current is not None else None
            )
        )
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO guild_twitter_targets (
                    guild_id, channel_id, enabled, last_seen_post_id, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    channel_id = excluded.channel_id,
                    enabled = excluded.enabled,
                    last_seen_post_id = COALESCE(
                        excluded.last_seen_post_id,
                        guild_twitter_targets.last_seen_post_id
                    ),
                    updated_at = excluded.updated_at
                """,
                (guild_id, channel_id, int(enabled), next_last_seen, now, now),
            )
            self._connection.commit()
        target = self.get_twitter_target(guild_id)
        if target is None:
            raise RuntimeError("Failed to upsert guild twitter target")
        return target

    def delete_twitter_target(self, guild_id: int) -> bool:
        with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM guild_twitter_targets WHERE guild_id = ?",
                (guild_id,),
            )
            self._connection.commit()
        return cursor.rowcount > 0

    def mark_twitter_target_seen(self, guild_id: int, post_id: str) -> None:
        now = _now_iso()
        with self._lock:
            self._connection.execute(
                """
                UPDATE guild_twitter_targets
                SET last_seen_post_id = ?, updated_at = ?
                WHERE guild_id = ?
                """,
                (post_id, now, guild_id),
            )
            self._connection.commit()

    def get_chzzk_target(self, guild_id: int) -> GuildChzzkTarget | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT guild_id, channel_id, enabled, last_live_id, is_live, created_at, updated_at
                FROM guild_chzzk_targets
                WHERE guild_id = ?
                """,
                (guild_id,),
            ).fetchone()
        return self._row_to_chzzk_target(row) if row is not None else None

    def list_chzzk_targets(self) -> list[GuildChzzkTarget]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT guild_id, channel_id, enabled, last_live_id, is_live, created_at, updated_at
                FROM guild_chzzk_targets
                WHERE enabled = 1
                ORDER BY guild_id
                """
            ).fetchall()
        return [self._row_to_chzzk_target(row) for row in rows]

    def upsert_chzzk_target(
        self,
        guild_id: int,
        *,
        channel_id: int,
        enabled: bool = True,
        last_live_id: str | None = None,
        is_live: bool | None = None,
    ) -> GuildChzzkTarget:
        current = self.get_chzzk_target(guild_id)
        now = _now_iso()
        next_last_live_id = (
            last_live_id if last_live_id is not None else (
                current.last_live_id if current is not None else None
            )
        )
        next_is_live = is_live if is_live is not None else (
            current.is_live if current is not None else False
        )
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO guild_chzzk_targets (
                    guild_id, channel_id, enabled, last_live_id, is_live, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    channel_id = excluded.channel_id,
                    enabled = excluded.enabled,
                    last_live_id = COALESCE(
                        excluded.last_live_id,
                        guild_chzzk_targets.last_live_id
                    ),
                    is_live = excluded.is_live,
                    updated_at = excluded.updated_at
                """,
                (
                    guild_id,
                    channel_id,
                    int(enabled),
                    next_last_live_id,
                    int(next_is_live),
                    now,
                    now,
                ),
            )
            self._connection.commit()
        target = self.get_chzzk_target(guild_id)
        if target is None:
            raise RuntimeError("Failed to upsert guild chzzk target")
        return target

    def mark_chzzk_target_seen(self, guild_id: int, live_id: str) -> None:
        now = _now_iso()
        with self._lock:
            self._connection.execute(
                """
                UPDATE guild_chzzk_targets
                SET last_live_id = ?, is_live = 1, updated_at = ?
                WHERE guild_id = ?
                """,
                (live_id, now, guild_id),
            )
            self._connection.commit()

    def mark_chzzk_target_offline(self, guild_id: int) -> None:
        now = _now_iso()
        with self._lock:
            self._connection.execute(
                """
                UPDATE guild_chzzk_targets
                SET is_live = 0, updated_at = ?
                WHERE guild_id = ?
                """,
                (now, guild_id),
            )
            self._connection.commit()

    def get_youtube_target(self, guild_id: int) -> GuildYoutubeTarget | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT guild_id, channel_id, enabled, last_live_id, is_live, created_at, updated_at
                FROM guild_youtube_targets
                WHERE guild_id = ?
                """,
                (guild_id,),
            ).fetchone()
        return self._row_to_youtube_target(row) if row is not None else None

    def list_youtube_targets(self) -> list[GuildYoutubeTarget]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT guild_id, channel_id, enabled, last_live_id, is_live, created_at, updated_at
                FROM guild_youtube_targets
                WHERE enabled = 1
                ORDER BY guild_id
                """
            ).fetchall()
        return [self._row_to_youtube_target(row) for row in rows]

    def upsert_youtube_target(
        self,
        guild_id: int,
        *,
        channel_id: int,
        enabled: bool = True,
        last_live_id: str | None = None,
        is_live: bool | None = None,
    ) -> GuildYoutubeTarget:
        current = self.get_youtube_target(guild_id)
        now = _now_iso()
        next_last_live_id = (
            last_live_id if last_live_id is not None else (
                current.last_live_id if current is not None else None
            )
        )
        next_is_live = is_live if is_live is not None else (
            current.is_live if current is not None else False
        )
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO guild_youtube_targets (
                    guild_id, channel_id, enabled, last_live_id, is_live, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    channel_id = excluded.channel_id,
                    enabled = excluded.enabled,
                    last_live_id = COALESCE(
                        excluded.last_live_id,
                        guild_youtube_targets.last_live_id
                    ),
                    is_live = excluded.is_live,
                    updated_at = excluded.updated_at
                """,
                (
                    guild_id,
                    channel_id,
                    int(enabled),
                    next_last_live_id,
                    int(next_is_live),
                    now,
                    now,
                ),
            )
            self._connection.commit()
        target = self.get_youtube_target(guild_id)
        if target is None:
            raise RuntimeError("Failed to upsert guild youtube target")
        return target

    def mark_youtube_target_seen(self, guild_id: int, live_id: str) -> None:
        now = _now_iso()
        with self._lock:
            self._connection.execute(
                """
                UPDATE guild_youtube_targets
                SET last_live_id = ?, is_live = 1, updated_at = ?
                WHERE guild_id = ?
                """,
                (live_id, now, guild_id),
            )
            self._connection.commit()

    def mark_youtube_target_offline(self, guild_id: int) -> None:
        now = _now_iso()
        with self._lock:
            self._connection.execute(
                """
                UPDATE guild_youtube_targets
                SET is_live = 0, updated_at = ?
                WHERE guild_id = ?
                """,
                (now, guild_id),
            )
            self._connection.commit()

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
                image_delivery=None,
                news_banner=DEFAULT_NOTIFICATION_BANNER,
                created_at=None,
                updated_at=None,
            )

        return self._row_to_user_settings(row)

    def count_user_settings(self) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS count FROM user_settings"
            ).fetchone()
        return int(row["count"] or 0) if row is not None else 0

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

    def update_user_image_delivery(
        self,
        user_id: int,
        *,
        username: str,
        nickname: str | None,
        image_delivery: str | None,
    ) -> UserSettings:
        current = self.get_user_settings(user_id)
        now = _now_iso()
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO user_settings (
                    user_id, username, nickname, language, image_delivery, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = excluded.username,
                    nickname = excluded.nickname,
                    image_delivery = excluded.image_delivery,
                    updated_at = excluded.updated_at
                """,
                (user_id, username, nickname, current.language, image_delivery, now, now),
            )
            self._connection.commit()
        return self.get_user_settings(user_id)

    def update_user_news_banner(
        self,
        user_id: int,
        *,
        username: str,
        nickname: str | None,
        news_banner: str | None,
    ) -> UserSettings:
        current = self.get_user_settings(user_id)
        now = _now_iso()
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO user_settings (
                    user_id, username, nickname, language, image_delivery,
                    news_banner, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = excluded.username,
                    nickname = excluded.nickname,
                    news_banner = excluded.news_banner,
                    updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    username,
                    nickname,
                    current.language,
                    current.image_delivery,
                    news_banner,
                    now,
                    now,
                ),
            )
            self._connection.commit()
        return self.get_user_settings(user_id)

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
        notification_banner: str | None = None,
        public_news_lookup_allowed: bool | None = None,
        missed_news_recovery_enabled: bool | None = None,
        maintenance_notifications_enabled: bool | None = None,
        news_source_mode: str | None = None,
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
            notification_banner=(
                notification_banner
                if notification_banner is not None
                else current.notification_banner
            ),
            public_news_lookup_allowed=(
                public_news_lookup_allowed
                if public_news_lookup_allowed is not None
                else current.public_news_lookup_allowed
            ),
            missed_news_recovery_enabled=(
                missed_news_recovery_enabled
                if missed_news_recovery_enabled is not None
                else current.missed_news_recovery_enabled
            ),
            maintenance_notifications_enabled=(
                maintenance_notifications_enabled
                if maintenance_notifications_enabled is not None
                else current.maintenance_notifications_enabled
            ),
            news_source_mode=(
                news_source_mode
                if news_source_mode is not None
                else current.news_source_mode
            ),
            last_maintenance_start_notice=current.last_maintenance_start_notice,
            last_maintenance_update_notice=current.last_maintenance_update_notice,
        )

        with self._lock:
            self._connection.execute(
                """
                INSERT INTO guild_settings (
                    guild_id, channel_id, role_id, post_format, enabled, language,
                    max_posts_per_poll, auto_cleanup_enabled, auto_cleanup_days,
                    image_delivery, notification_banner, public_news_lookup_allowed,
                    missed_news_recovery_enabled, maintenance_notifications_enabled, news_source_mode, last_maintenance_start_notice,
                    last_maintenance_update_notice, last_seen_post_id, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    notification_banner = excluded.notification_banner,
                    public_news_lookup_allowed = excluded.public_news_lookup_allowed,
                    missed_news_recovery_enabled = excluded.missed_news_recovery_enabled,
                    maintenance_notifications_enabled = excluded.maintenance_notifications_enabled,
                    news_source_mode = excluded.news_source_mode,
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
                    next_settings.notification_banner,
                    int(next_settings.public_news_lookup_allowed),
                    int(next_settings.missed_news_recovery_enabled),
                    int(next_settings.maintenance_notifications_enabled),
                    next_settings.news_source_mode,
                    next_settings.last_maintenance_start_notice,
                    next_settings.last_maintenance_update_notice,
                    next_settings.last_seen_post_id,
                    now,
                    now,
                ),
            )
            self._connection.commit()

        return next_settings

    def update_maintenance_notifications(
        self,
        guild_id: int,
        *,
        enabled: bool,
        channel_id: int | None = None,
    ) -> GuildSettings:
        current = self.get_settings(guild_id)
        return self.update_settings(
            guild_id,
            channel_id=channel_id if channel_id is not None else current.channel_id,
            maintenance_notifications_enabled=enabled,
        )

    def mark_maintenance_notice_sent(
        self,
        guild_id: int,
        *,
        notice_type: str,
        notice_key: str,
    ) -> None:
        column = {
            "start": "last_maintenance_start_notice",
            "update": "last_maintenance_update_notice",
        }.get(notice_type)
        if column is None:
            raise ValueError(f"Unknown maintenance notice type: {notice_type!r}")

        now = _now_iso()
        with self._lock:
            self._connection.execute(
                f"""
                UPDATE guild_settings
                SET {column} = ?, updated_at = ?
                WHERE guild_id = ?
                """,
                (notice_key, now, guild_id),
            )
            self._connection.commit()

    def clear_role(self, guild_id: int) -> GuildSettings:
        now = _now_iso()
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO guild_settings (
                    guild_id, channel_id, role_id, post_format, enabled, language,
                    max_posts_per_poll, auto_cleanup_enabled, auto_cleanup_days,
                    image_delivery, public_news_lookup_allowed,
                    maintenance_notifications_enabled, last_maintenance_start_notice,
                    last_maintenance_update_notice, last_seen_post_id, created_at, updated_at
                )
                VALUES (?, NULL, NULL, 'rich', 1, 'koreana', 30, 1, 1, 'files', 1, 0, NULL, NULL, NULL, ?, ?)
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
                    image_delivery, public_news_lookup_allowed,
                    maintenance_notifications_enabled, last_maintenance_start_notice,
                    last_maintenance_update_notice, last_seen_post_id, created_at, updated_at
                )
                VALUES (?, NULL, NULL, 'rich', 1, 'koreana', 30, 1, 1, 'files', 1, 0, NULL, NULL, ?, ?, ?)
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

    def get_seen_post_statuses(self, guild_id: int, post_ids: Iterable[str]) -> dict[str, bool]:
        ids = list(dict.fromkeys(post_ids))
        if not ids:
            return {}

        placeholders = ", ".join("?" for _ in ids)
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT post_id, announced_at FROM guild_seen_posts
                WHERE guild_id = ? AND post_id IN ({placeholders})
                """,
                (guild_id, *ids),
            ).fetchall()

        return {str(row["post_id"]): row["announced_at"] is not None for row in rows}

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

    def get_news_target_seen_post_ids(
        self, target_id: int, post_ids: Iterable[str]
    ) -> set[str]:
        ids = list(dict.fromkeys(post_ids))
        if not ids:
            return set()

        placeholders = ", ".join("?" for _ in ids)
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT post_id FROM guild_news_target_seen_posts
                WHERE target_id = ? AND post_id IN ({placeholders})
                """,
                (target_id, *ids),
            ).fetchall()

        return {str(row["post_id"]) for row in rows}

    def get_news_target_seen_post_statuses(
        self, target_id: int, post_ids: Iterable[str]
    ) -> dict[str, bool]:
        ids = list(dict.fromkeys(post_ids))
        if not ids:
            return {}

        placeholders = ", ".join("?" for _ in ids)
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT post_id, announced_at FROM guild_news_target_seen_posts
                WHERE target_id = ? AND post_id IN ({placeholders})
                """,
                (target_id, *ids),
            ).fetchall()

        return {str(row["post_id"]): row["announced_at"] is not None for row in rows}

    def news_target_has_seen_posts(self, target_id: int) -> bool:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT 1 FROM guild_news_target_seen_posts
                WHERE target_id = ?
                LIMIT 1
                """,
                (target_id,),
            ).fetchone()

        return row is not None

    def mark_news_target_posts_seen(
        self,
        target_id: int,
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
                INSERT INTO guild_news_target_seen_posts (
                    target_id, post_id, seen_at, announced_at
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(target_id, post_id) DO UPDATE SET
                    seen_at = excluded.seen_at,
                    announced_at = COALESCE(
                        excluded.announced_at,
                        guild_news_target_seen_posts.announced_at
                    )
                """,
                ((target_id, post_id, now, announced_at) for post_id in ids),
            )
            self._connection.commit()

    def _queue_news_update_targets_locked(
        self,
        post_id: str,
        content_hash: str,
        queued_at: str,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO guild_news_target_pending_updates (
                target_id, post_id, content_hash, queued_at, sent_at
            )
            SELECT target_id, post_id, ?, ?, NULL
            FROM guild_news_target_seen_posts
            WHERE post_id = ? AND announced_at IS NOT NULL
            ON CONFLICT(target_id, post_id) DO UPDATE SET
                content_hash = excluded.content_hash,
                queued_at = excluded.queued_at,
                sent_at = NULL
            WHERE guild_news_target_pending_updates.content_hash != excluded.content_hash
            """,
            (content_hash, queued_at, post_id),
        )

    def get_pending_news_update_targets(
        self,
        post_ids: Iterable[str] | None = None,
    ) -> list[tuple[str, GuildNewsTarget]]:
        ids = list(dict.fromkeys(post_ids or []))
        params: list[object] = []
        post_filter = ""
        if ids:
            placeholders = ",".join("?" * len(ids))
            post_filter = f"AND pending.post_id IN ({placeholders})"
            params.extend(ids)

        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT pending.post_id,
                       gnt.target_id, gnt.guild_id, gnt.channel_id, gnt.language,
                       gnt.created_at, gnt.updated_at
                FROM guild_news_target_pending_updates pending
                JOIN guild_news_targets gnt ON gnt.target_id = pending.target_id
                JOIN guild_settings gs ON gs.guild_id = gnt.guild_id
                WHERE pending.sent_at IS NULL
                  AND gs.enabled = 1
                  {post_filter}
                ORDER BY pending.queued_at ASC, gnt.guild_id, gnt.channel_id
                """,
                params,
            ).fetchall()

        return [
            (str(row["post_id"]), self._row_to_news_target(row))
            for row in rows
        ]

    def mark_news_update_sent(self, target_id: int, post_id: str) -> None:
        now = _now_iso()
        with self._lock:
            self._connection.execute(
                """
                UPDATE guild_news_target_pending_updates
                SET sent_at = ?
                WHERE target_id = ? AND post_id = ?
                """,
                (now, target_id, post_id),
            )
            self._connection.commit()

    def queue_news_update_for_announced_targets(self, post_id: str) -> int:
        now = _now_iso()
        with self._lock:
            cursor = self._connection.execute(
                """
                INSERT INTO guild_news_target_pending_updates (
                    target_id, post_id, content_hash, queued_at, sent_at
                )
                SELECT DISTINCT gnt.target_id, posts.post_id, posts.content_hash, ?, NULL
                FROM posts
                JOIN guild_news_targets gnt ON gnt.language = posts.language
                JOIN guild_settings gs ON gs.guild_id = gnt.guild_id
                WHERE posts.post_id = ?
                  AND gs.enabled = 1
                  AND (
                    EXISTS (
                        SELECT 1
                        FROM guild_news_target_seen_posts gntsp
                        WHERE gntsp.target_id = gnt.target_id
                          AND gntsp.post_id = posts.post_id
                          AND gntsp.announced_at IS NOT NULL
                    )
                    OR EXISTS (
                        SELECT 1
                        FROM guild_seen_posts gsp
                        WHERE gsp.guild_id = gnt.guild_id
                          AND gsp.post_id = posts.post_id
                          AND gsp.announced_at IS NOT NULL
                    )
                  )
                ON CONFLICT(target_id, post_id) DO UPDATE SET
                    content_hash = excluded.content_hash,
                    queued_at = excluded.queued_at,
                    sent_at = NULL
                WHERE guild_news_target_pending_updates.sent_at IS NULL
                """,
                (now, post_id),
            )
            self._connection.commit()

        return int(cursor.rowcount or 0)

    def save_posts(self, posts: Iterable[NewsPost]) -> tuple[int, list[str]]:
        posts_list = list(posts)
        if not posts_list:
            return 0, []
        now = _now_iso()
        saved = 0
        changed_post_ids: list[str] = []
        with self._lock:
            ids = [p.post_id for p in posts_list]
            placeholders = ",".join("?" * len(ids))
            existing_hashes: dict[str, str] = {
                row["post_id"]: row["content_hash"]
                for row in self._connection.execute(
                    f"SELECT post_id, content_hash FROM posts WHERE post_id IN ({placeholders})",
                    ids,
                ).fetchall()
            }
            for post in posts_list:
                new_hash = _post_content_hash(post)
                old_hash = existing_hashes.get(post.post_id)
                cursor = self._connection.execute(
                    """
                    INSERT INTO posts (
                        post_id, source_user, url, text, title, created_at, language,
                        image_urls, raw_json, saved_at, content_hash
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(post_id) DO UPDATE SET
                        source_user = excluded.source_user,
                        url = excluded.url,
                        text = excluded.text,
                        title = excluded.title,
                        created_at = excluded.created_at,
                        language = excluded.language,
                        image_urls = excluded.image_urls,
                        raw_json = excluded.raw_json,
                        content_hash = excluded.content_hash
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
                        new_hash,
                    ),
                )
                if cursor.rowcount:
                    saved += 1
                if old_hash is not None and old_hash != "" and old_hash != new_hash:
                    changed_post_ids.append(post.post_id)
                    self._queue_news_update_targets_locked(post.post_id, new_hash, now)
            self._connection.commit()

        return saved, changed_post_ids

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

    def save_twitter_posts(self, posts: Iterable[TwitterPost]) -> int:
        posts_list = list(posts)
        if not posts_list:
            return 0
        now = _now_iso()
        saved = 0
        with self._lock:
            for post in posts_list:
                cursor = self._connection.execute(
                    """
                    INSERT INTO twitter_posts (
                        post_id, author_username, url, text, title, created_at,
                        image_urls, raw_json, saved_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(post_id) DO UPDATE SET
                        author_username = excluded.author_username,
                        url = excluded.url,
                        text = excluded.text,
                        title = excluded.title,
                        created_at = excluded.created_at,
                        image_urls = excluded.image_urls,
                        raw_json = excluded.raw_json
                    """,
                    (
                        post.post_id,
                        post.author_username,
                        post.url,
                        post.text,
                        post.title,
                        _datetime_to_iso(post.created_at),
                        json.dumps(post.image_urls, ensure_ascii=False),
                        json.dumps(post.raw, ensure_ascii=False),
                        now,
                    ),
                )
                if cursor.rowcount:
                    saved += 1
            self._connection.commit()
        return saved

    def replace_twitter_posts(self, posts: Iterable[TwitterPost]) -> int:
        posts_list = list(posts)
        now = _now_iso()
        with self._lock:
            saved = 0
            for post in posts_list:
                cursor = self._connection.execute(
                    """
                    INSERT OR IGNORE INTO twitter_posts (
                        post_id, author_username, url, text, title, created_at,
                        image_urls, raw_json, saved_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        post.post_id,
                        post.author_username,
                        post.url,
                        post.text,
                        post.title,
                        _datetime_to_iso(post.created_at),
                        json.dumps(post.image_urls, ensure_ascii=False),
                        json.dumps(post.raw, ensure_ascii=False),
                        now,
                    ),
                )
                saved += cursor.rowcount
            self._connection.commit()
        return saved

    def clear_twitter_posts(self) -> None:
        with self._lock:
            self._connection.execute("DELETE FROM twitter_posts")
            self._connection.commit()

    def get_twitter_post(self, post_id: str) -> TwitterPost | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM twitter_posts WHERE post_id = ?", (post_id,)
            ).fetchone()
        return self._row_to_twitter_post(row) if row else None

    def get_twitter_post_by_id_or_title(self, value: str) -> TwitterPost | None:
        post = self.get_twitter_post(value)
        if post is not None:
            return post
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM twitter_posts
                WHERE title = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (value,),
            ).fetchone()
        return self._row_to_twitter_post(row) if row else None

    def get_latest_twitter_post(self) -> TwitterPost | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM twitter_posts
                ORDER BY created_at DESC
                LIMIT 1
                """
            ).fetchone()
        return self._row_to_twitter_post(row) if row else None

    def search_twitter_posts(self, query: str, limit: int = 25) -> list[TwitterPost]:
        query = query.strip()
        if query:
            sql = """
                SELECT * FROM twitter_posts
                WHERE title LIKE ? OR text LIKE ?
                ORDER BY created_at DESC
                LIMIT ?
            """
            params: tuple[object, ...] = (f"%{query}%", f"%{query}%", limit)
        else:
            sql = """
                SELECT * FROM twitter_posts
                ORDER BY created_at DESC
                LIMIT ?
            """
            params = (limit,)
        with self._lock:
            rows = self._connection.execute(sql, params).fetchall()
        return [self._row_to_twitter_post(row) for row in rows]

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

    def get_announced_guild_ids(self, post_id: str) -> list[int]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT gs.guild_id
                FROM guild_seen_posts gsp
                JOIN guild_settings gs ON gs.guild_id = gsp.guild_id
                WHERE gsp.post_id = ? AND gsp.announced_at IS NOT NULL
                  AND gs.enabled = 1 AND gs.channel_id IS NOT NULL
                """,
                (post_id,),
            ).fetchall()
        return [int(row["guild_id"]) for row in rows]

    def get_announced_news_targets(self, post_id: str) -> list[GuildNewsTarget]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT gnt.target_id, gnt.guild_id, gnt.channel_id, gnt.language,
                       gnt.created_at, gnt.updated_at
                FROM guild_news_target_seen_posts gntsp
                JOIN guild_news_targets gnt ON gnt.target_id = gntsp.target_id
                JOIN guild_settings gs ON gs.guild_id = gnt.guild_id
                WHERE gntsp.post_id = ? AND gntsp.announced_at IS NOT NULL
                  AND gs.enabled = 1
                ORDER BY gnt.guild_id, gnt.channel_id
                """,
                (post_id,),
            ).fetchall()

        return [self._row_to_news_target(row) for row in rows]

    def delete_guild_data(self, guild_id: int) -> None:
        with self._lock:
            target_rows = self._connection.execute(
                "SELECT target_id FROM guild_news_targets WHERE guild_id = ?",
                (guild_id,),
            ).fetchall()
            target_ids = [int(row["target_id"]) for row in target_rows]
            if target_ids:
                placeholders = ", ".join("?" for _ in target_ids)
                self._connection.execute(
                    f"DELETE FROM guild_news_target_seen_posts WHERE target_id IN ({placeholders})",
                    target_ids,
                )
            self._connection.execute(
                "DELETE FROM guild_news_targets WHERE guild_id = ?", (guild_id,)
            )
            self._connection.execute(
                "DELETE FROM guild_twitter_targets WHERE guild_id = ?", (guild_id,)
            )
            self._connection.execute(
                "DELETE FROM guild_chzzk_targets WHERE guild_id = ?", (guild_id,)
            )
            self._connection.execute(
                "DELETE FROM guild_youtube_targets WHERE guild_id = ?", (guild_id,)
            )
            self._connection.execute(
                "DELETE FROM guild_settings WHERE guild_id = ?", (guild_id,)
            )
            self._connection.execute(
                "DELETE FROM guild_seen_posts WHERE guild_id = ?", (guild_id,)
            )
            self._connection.execute(
                "DELETE FROM tracked_messages WHERE guild_id = ?", (guild_id,)
            )
            self._connection.commit()

    def reset_guild_settings(self, guild_id: int) -> None:
        self.delete_guild_data(guild_id)

    @staticmethod
    def _row_to_settings(row: sqlite3.Row) -> GuildSettings:
        cleanup_days = int(row["auto_cleanup_days"] or DEFAULT_AUTO_CLEANUP_DAYS)
        cleanup_days = max(MIN_CLEANUP_DAYS, min(MAX_CLEANUP_DAYS, cleanup_days))
        image_delivery = str(row["image_delivery"] or DEFAULT_IMAGE_DELIVERY)
        if image_delivery != "files":
            image_delivery = DEFAULT_IMAGE_DELIVERY
        news_source_mode = str(row["news_source_mode"] or DEFAULT_NEWS_SOURCE_MODE)
        if news_source_mode not in {"both", "steam", "twitter"}:
            news_source_mode = DEFAULT_NEWS_SOURCE_MODE
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
            notification_banner=row["notification_banner"] or DEFAULT_NOTIFICATION_BANNER,
            public_news_lookup_allowed=bool(row["public_news_lookup_allowed"]),
            missed_news_recovery_enabled=bool(row["missed_news_recovery_enabled"]),
            maintenance_notifications_enabled=bool(row["maintenance_notifications_enabled"]),
            news_source_mode=news_source_mode,
            last_maintenance_start_notice=row["last_maintenance_start_notice"],
            last_maintenance_update_notice=row["last_maintenance_update_notice"],
        )

    @staticmethod
    def _row_to_news_target(row: sqlite3.Row) -> GuildNewsTarget:
        return GuildNewsTarget(
            target_id=int(row["target_id"]),
            guild_id=int(row["guild_id"]),
            channel_id=int(row["channel_id"]),
            language=str(row["language"] or "koreana"),
            created_at=_datetime_from_iso(row["created_at"]),
            updated_at=_datetime_from_iso(row["updated_at"]),
        )

    @staticmethod
    def _row_to_twitter_target(row: sqlite3.Row) -> GuildTwitterTarget:
        return GuildTwitterTarget(
            guild_id=int(row["guild_id"]),
            channel_id=int(row["channel_id"]),
            enabled=bool(row["enabled"]),
            last_seen_post_id=row["last_seen_post_id"],
            created_at=_datetime_from_iso(row["created_at"]),
            updated_at=_datetime_from_iso(row["updated_at"]),
        )

    @staticmethod
    def _row_to_chzzk_target(row: sqlite3.Row) -> GuildChzzkTarget:
        return GuildChzzkTarget(
            guild_id=int(row["guild_id"]),
            channel_id=int(row["channel_id"]),
            enabled=bool(row["enabled"]),
            last_live_id=row["last_live_id"],
            is_live=bool(row["is_live"]),
            created_at=_datetime_from_iso(row["created_at"]),
            updated_at=_datetime_from_iso(row["updated_at"]),
        )

    @staticmethod
    def _row_to_youtube_target(row: sqlite3.Row) -> GuildYoutubeTarget:
        return GuildYoutubeTarget(
            guild_id=int(row["guild_id"]),
            channel_id=int(row["channel_id"]),
            enabled=bool(row["enabled"]),
            last_live_id=row["last_live_id"],
            is_live=bool(row["is_live"]),
            created_at=_datetime_from_iso(row["created_at"]),
            updated_at=_datetime_from_iso(row["updated_at"]),
        )

    @staticmethod
    def _row_to_user_settings(row: sqlite3.Row) -> UserSettings:
        raw_delivery = row["image_delivery"] if "image_delivery" in row.keys() else None
        image_delivery = "files" if raw_delivery == "files" else None
        news_banner = row["news_banner"] if "news_banner" in row.keys() else None
        return UserSettings(
            user_id=int(row["user_id"]),
            username=str(row["username"] or ""),
            nickname=str(row["nickname"]) if row["nickname"] is not None else None,
            language=str(row["language"] or "koreana"),
            image_delivery=image_delivery,
            news_banner=str(news_banner) if news_banner else None,
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

    @staticmethod
    def _row_to_twitter_post(row: sqlite3.Row) -> TwitterPost:
        return TwitterPost(
            post_id=str(row["post_id"]),
            author_username=str(row["author_username"]),
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
