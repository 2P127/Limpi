from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.bot import _configure_test_mode_notifications
from src.bot_constants import TEST_MODE_CHANNEL_ID, TEST_MODE_GUILD_ID
from src.core.storage import SQLiteStorage


class TestModeNotificationConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.storage = SQLiteStorage(Path(self._tmpdir.name) / "test.sqlite3")
        self.addCleanup(self.storage.close)

    def test_configures_every_automatic_notification_target(self) -> None:
        self.storage.upsert_news_target(
            TEST_MODE_GUILD_ID,
            channel_id=123,
            language="english",
        )
        self.storage.upsert_chzzk_target(
            TEST_MODE_GUILD_ID,
            channel_id=123,
            enabled=False,
            last_live_id="chzzk-live",
            is_live=True,
        )
        self.storage.upsert_youtube_target(
            TEST_MODE_GUILD_ID,
            channel_id=123,
            enabled=False,
            last_live_id="youtube-live",
            is_live=True,
        )
        self.storage.upsert_youtube_upload_target(
            TEST_MODE_GUILD_ID,
            channel_id=123,
            enabled=False,
            last_video_id="youtube-upload",
        )
        self.storage.upsert_hampang_target(
            TEST_MODE_GUILD_ID,
            channel_id=123,
            enabled=False,
            last_x_post_id="hampang-x",
            last_youtube_video_id="hampang-youtube",
        )

        _configure_test_mode_notifications(self.storage)

        settings = self.storage.get_settings(TEST_MODE_GUILD_ID)
        self.assertEqual(settings.channel_id, TEST_MODE_CHANNEL_ID)
        self.assertTrue(settings.enabled)
        self.assertTrue(settings.maintenance_notifications_enabled)
        self.assertEqual(settings.news_source_mode, "both")

        news_targets = self.storage.list_news_targets(TEST_MODE_GUILD_ID)
        self.assertEqual(
            [(target.channel_id, target.language) for target in news_targets],
            [(TEST_MODE_CHANNEL_ID, "koreana")],
        )

        chzzk = self.storage.get_chzzk_target(TEST_MODE_GUILD_ID)
        youtube = self.storage.get_youtube_target(TEST_MODE_GUILD_ID)
        upload = self.storage.get_youtube_upload_target(TEST_MODE_GUILD_ID)
        hampang = self.storage.get_hampang_target(TEST_MODE_GUILD_ID)
        self.assertEqual((chzzk.channel_id, chzzk.enabled), (TEST_MODE_CHANNEL_ID, True))
        self.assertEqual((youtube.channel_id, youtube.enabled), (TEST_MODE_CHANNEL_ID, True))
        self.assertEqual((upload.channel_id, upload.enabled), (TEST_MODE_CHANNEL_ID, True))
        self.assertEqual((hampang.channel_id, hampang.enabled), (TEST_MODE_CHANNEL_ID, True))

        self.assertEqual(chzzk.last_live_id, "chzzk-live")
        self.assertTrue(chzzk.is_live)
        self.assertEqual(youtube.last_live_id, "youtube-live")
        self.assertTrue(youtube.is_live)
        self.assertEqual(upload.last_video_id, "youtube-upload")
        self.assertEqual(hampang.last_x_post_id, "hampang-x")
        self.assertEqual(hampang.last_youtube_video_id, "hampang-youtube")

    def test_is_idempotent_and_does_not_create_duplicate_x_target(self) -> None:
        _configure_test_mode_notifications(self.storage)
        _configure_test_mode_notifications(self.storage)

        self.assertEqual(len(self.storage.list_news_targets(TEST_MODE_GUILD_ID)), 1)
        self.assertIsNone(self.storage.get_twitter_target(TEST_MODE_GUILD_ID))


if __name__ == "__main__":
    unittest.main()
