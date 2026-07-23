from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, Mock

import aiohttp

from src.bot import NewsCog
from src.bot_runtime import _internet_error_detail


class BotRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def test_internet_error_detail_includes_http_status_and_message(self) -> None:
        error = aiohttp.ClientResponseError(
            request_info=None,
            history=(),
            status=502,
            message="Bad Gateway: Error",
        )

        self.assertEqual(
            _internet_error_detail(error),
            ("HTTP 502 Bad Gateway: Error", "ClientResponseError"),
        )

    async def test_unused_language_failure_does_not_block_active_targets(self) -> None:
        cog = object.__new__(NewsCog)
        cog.news_source = object()
        cog.x_source = object()
        cog._news_recovery_baseline_pending = False
        cog._news_targets_by_language = Mock(return_value={"koreana": [object()]})
        cog._combined_posts_by_language = AsyncMock(
            return_value=({"koreana": []}, [], {"english", "japanese"}, False)
        )
        cog._news_target_tasks_for_poll = Mock(return_value=[])
        cog._broadcast_post_updates = AsyncMock()

        self.assertEqual(await cog._poll_once(), 0)
        self.assertFalse(cog._news_recovery_baseline_pending)
        cog._news_target_tasks_for_poll.assert_called_once()

    async def test_active_language_failure_still_enters_recovery_mode(self) -> None:
        cog = object.__new__(NewsCog)
        cog.news_source = object()
        cog.x_source = object()
        cog._news_recovery_baseline_pending = False
        cog._news_targets_by_language = Mock(return_value={"koreana": [object()]})
        cog._combined_posts_by_language = AsyncMock(
            return_value=({"koreana": []}, [], {"koreana"}, False)
        )
        cog._news_target_tasks_for_poll = Mock(return_value=[])

        self.assertEqual(await cog._poll_once(), 0)
        self.assertTrue(cog._news_recovery_baseline_pending)
        cog._news_target_tasks_for_poll.assert_not_called()


if __name__ == "__main__":
    unittest.main()
