from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from src.bot import NewsCog
from src.core.storage import SQLiteStorage


class PruneDisconnectedGuildDataTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.storage = SQLiteStorage(Path(self._tmpdir.name) / "test.sqlite3")
        self.addCleanup(self.storage.close)
        self.storage.ensure_guild_settings(100)
        self.storage.ensure_guild_settings(200)
        self.storage.upsert_news_target(100, channel_id=11, language="koreana")
        self.storage.upsert_news_target(200, channel_id=22, language="koreana")

    def _cog(self, *, test_mode: bool, connected_guild_ids: set[int]) -> NewsCog:
        cog = object.__new__(NewsCog)
        cog.test_mode = test_mode
        cog.storage = self.storage
        bot = MagicMock()
        bot.guilds = [MagicMock(id=guild_id) for guild_id in connected_guild_ids]
        cog.bot = bot
        return cog

    def test_production_mode_deletes_disconnected_guild_data(self) -> None:
        cog = self._cog(test_mode=False, connected_guild_ids={100})
        deleted = cog.prune_disconnected_guild_data()
        self.assertEqual(deleted, 1)
        self.assertEqual([t.guild_id for t in self.storage.list_all_news_targets()], [100])

    def test_test_mode_keeps_disconnected_guild_data(self) -> None:
        cog = self._cog(test_mode=True, connected_guild_ids={100})
        deleted = cog.prune_disconnected_guild_data()
        self.assertEqual(deleted, 0)
        self.assertEqual(
            sorted(t.guild_id for t in self.storage.list_all_news_targets()),
            [100, 200],
        )


if __name__ == "__main__":
    unittest.main()
