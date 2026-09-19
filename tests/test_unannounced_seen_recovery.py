from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from src.bot import NewsCog
from src.core.models import NewsPost
from src.core.storage import SQLiteStorage


def steam_post(gid: str, title: str, *, created_at: datetime) -> NewsPost:
    return NewsPost(
        post_id=f"steam:koreana:{gid}",
        source_user="Limbus Company Steam News",
        url=f"https://store.steampowered.com/news/app/1973530/view/{gid}",
        title=title,
        text=title,
        created_at=created_at,
        image_urls=[],
        raw={"language": "koreana", "event_gid": gid},
    )


class UnannouncedSeenRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.storage = SQLiteStorage(Path(self._tmpdir.name) / "test.sqlite3")
        self.addCleanup(self.storage.close)
        self.storage.ensure_guild_settings(100)
        self.target = self.storage.upsert_news_target(
            100,
            channel_id=11,
            language="koreana",
        )
        # Channel existed before the posts under test.
        self.storage._connection.execute(
            "UPDATE guild_news_targets SET created_at = ? WHERE target_id = ?",
            ("2026-09-01T00:00:00+00:00", self.target.target_id),
        )
        self.storage._connection.commit()
        self.target = self.storage.get_news_target_by_id(self.target.target_id)
        self.cog = object.__new__(NewsCog)
        self.cog.storage = self.storage
        self.cog.config = SimpleNamespace(announce_existing_on_first_run=False)

    def test_seen_but_not_announced_post_is_retried_after_sibling_was_sent(self) -> None:
        published_at = datetime(2026, 9, 17, 9, 3, tzinfo=timezone.utc)
        control = steam_post(
            "713412856137122031",
            "주요 이야기 10장 특정 스테이지의 조작 방식 개선 예정 안내",
            created_at=published_at,
        )
        issues = steam_post(
            "713412856137122033",
            "9월 17일 1.114.0 버전에서 발생한 알려진 이슈 안내",
            created_at=published_at,
        )
        self.storage.save_posts([control, issues])
        self.storage.mark_news_target_posts_seen(
            self.target.target_id,
            [control.post_id],
            announced=True,
        )
        self.storage.mark_news_target_posts_seen(
            self.target.target_id,
            [issues.post_id],
            announced=False,
        )

        settings = self.storage.get_settings(100)
        target = self.storage.get_news_target_by_id(self.target.target_id)
        assert target is not None
        posts = self.cog._new_posts_for_news_target(
            settings,
            target,
            [control, issues],
        )

        self.assertEqual([post.post_id for post in posts], [issues.post_id])

    def test_first_run_baseline_without_announcements_stays_suppressed(self) -> None:
        published_at = datetime(2026, 9, 17, 9, 3, tzinfo=timezone.utc)
        older = steam_post("1", "예전 소식", created_at=published_at)
        self.storage.save_posts([older])
        self.storage.mark_news_target_posts_seen(
            self.target.target_id,
            [older.post_id],
            announced=False,
        )

        settings = self.storage.get_settings(100)
        target = self.storage.get_news_target_by_id(self.target.target_id)
        assert target is not None
        posts = self.cog._new_posts_for_news_target(
            settings,
            target,
            [older],
        )

        self.assertEqual(posts, [])


if __name__ == "__main__":
    unittest.main()
