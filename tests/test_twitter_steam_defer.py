from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from src.bot import NewsCog
from src.core.models import NewsPost, TwitterPost


STEAM_URL = "https://store.steampowered.com/news/app/1973530/view/669495150925320961"


def cog_for_defer_tests() -> NewsCog:
    cog = object.__new__(NewsCog)
    cog._twitter_steam_grace_started_at = {}
    cog._twitter_steam_defer_logged_post_ids = set()
    cog._twitter_steam_retry_count = {}
    cog._twitter_steam_defer_expired_post_ids = set()
    return cog


class TwitterSteamDeferTests(unittest.TestCase):
    def test_news_post_with_steam_link_defers_for_thirty_minutes(self) -> None:
        cog = cog_for_defer_tests()
        post = NewsPost(
            post_id="twitter:1900000000000000000",
            source_user="LimbusCompany_B",
            url="https://x.com/LimbusCompany_B/status/1900000000000000000",
            title="신규 인격 정보 안내",
            text=f"Steam 공지: {STEAM_URL}",
            created_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            image_urls=[],
            raw={"source_type": "twitter"},
        )

        self.assertTrue(cog._defer_linked_twitter_for_steam(post))

    def test_old_linked_news_post_does_not_defer_forever(self) -> None:
        cog = cog_for_defer_tests()
        post = NewsPost(
            post_id="twitter:1900000000000000000",
            source_user="LimbusCompany_B",
            url="https://x.com/LimbusCompany_B/status/1900000000000000000",
            title="신규 인격 정보 안내",
            text=f"Steam 공지: {STEAM_URL}",
            created_at=datetime.now(timezone.utc) - timedelta(minutes=31),
            image_urls=[],
            raw={"source_type": "twitter"},
        )

        self.assertFalse(cog._defer_linked_twitter_for_steam(post))

    def test_twitter_target_post_with_steam_link_uses_same_defer_window(self) -> None:
        cog = cog_for_defer_tests()
        post = TwitterPost(
            post_id="1900000000000000000",
            author_username="LimbusCompany_B",
            url="https://x.com/LimbusCompany_B/status/1900000000000000000",
            title="신규 인격 정보 안내",
            text=f"Steam 공지: {STEAM_URL}",
            created_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            image_urls=[],
            raw={},
        )

        self.assertTrue(cog._defer_linked_twitter_post_for_steam(post))


if __name__ == "__main__":
    unittest.main()
