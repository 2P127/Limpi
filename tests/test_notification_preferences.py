import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, mock

from src.bot import NewsCog
from src.core.models import TwitterPost
from src.core.storage import SQLiteStorage


class NotificationPreferencesTests(IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.storage = SQLiteStorage(Path(tmp.name) / "prefs.sqlite3")
        self.addCleanup(self.storage.close)
        self.cog = object.__new__(NewsCog)
        self.cog.storage = self.storage
        self.storage.update_settings(1, role_id=100, language="english")

    async def test_roles_are_isolated_and_none_disables_mentions(self):
        self.storage.set_notification_preferences(1, "hampang", 10, role_id=200)
        self.storage.set_notification_preferences(1, "upload", 10, role_id=None)
        self.assertEqual(self.cog._notification_role(1, "hampang", 10), 200)
        self.assertIsNone(self.cog._notification_role(1, "upload", 10))
        self.assertEqual(self.cog._notification_role(1, "chzzk", 10), 100)
        self.assertEqual(self.cog._notification_role(1, "hampang", 11), 100)
        self.storage.delete_guild_data(1)
        self.assertIsNone(self.storage.get_notification_preferences(1, "hampang", 10))

    async def test_channel_language_takes_priority(self):
        self.storage.upsert_news_target(1, channel_id=10, language="japanese")
        self.assertEqual(self.cog._notification_language(1, "news", 10), "japanese")
        self.assertEqual(self.cog._notification_language(1, "hampang", 10), "english")
        self.storage.set_notification_preferences(1, "hampang", 10, role_id=None, language="koreana")
        self.assertEqual(self.cog._notification_language(1, "hampang", 10), "koreana")

    async def test_english_translation_uses_original_not_korean_filter(self):
        self.cog.x_source = mock.Mock(translate_tweet=mock.AsyncMock(return_value="English translation"))
        post = TwitterPost("x:123", "test", "https://x.com/test/status/123", "한국어", "title", None, [],
                           {"full_text": "元の日本語", "korean_localized": True})
        result = await self.cog._localize_twitter_post(post, language="english")
        self.cog.x_source.translate_tweet.assert_awaited_once_with("123", destination_language="en")
        self.assertEqual(result.text, "English translation")
        self.assertEqual(result.raw["full_text"], "元の日本語")
        self.assertFalse(result.raw["korean_localized"])

    async def test_foreign_translation_failure_keeps_original(self):
        self.cog.x_source = mock.Mock(translate_tweet=mock.AsyncMock(return_value=None))
        post = TwitterPost("x:123", "test", "https://x.com/test/status/123", "한국어", "title", None, [],
                           {"full_text": "Original text", "korean_localized": True})
        result = await self.cog._localize_twitter_post(post, language="japanese")
        self.assertEqual(result.text, "Original text")
        self.assertFalse(result.raw["korean_localized"])
