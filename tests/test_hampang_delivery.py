from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from src.bot import NewsCog
from src.bot_constants import (
    HAMPANG_AUTO_POLL_INTERVAL_SECONDS,
    IMAGE_DELIVERY_EMBEDS,
    IMAGE_DELIVERY_FILES,
)
from src.bot_helpers import _twitter_image_urls_for_delivery
from src.core.models import GuildHampangTarget, TwitterPost


IMAGE_URLS = [
    "https://pbs.twimg.com/media/example1.jpg?name=orig",
    "https://pbs.twimg.com/media/example2.jpg?name=orig",
]


def twitter_post(*, created_at: datetime | None = None) -> TwitterPost:
    return TwitterPost(
        post_id="1900000000000000000",
        author_username="Ham_PangPang",
        url="https://x.com/Ham_PangPang/status/1900000000000000000",
        title="TGS 2026 안내",
        text="부스 추가 안내",
        created_at=created_at or datetime.now(timezone.utc),
        image_urls=list(IMAGE_URLS),
        raw={},
    )


def hampang_target(*, created_at: datetime | None = None) -> GuildHampangTarget:
    return GuildHampangTarget(
        guild_id=1,
        channel_id=2,
        enabled=True,
        last_x_post_id="1",
        last_youtube_video_id=None,
        created_at=created_at,
        updated_at=None,
    )


class HampangImageDeliveryTests(unittest.TestCase):
    def test_embed_delivery_keeps_images_in_embed_only(self) -> None:
        embed_urls, file_urls = _twitter_image_urls_for_delivery(
            twitter_post(),
            attach_photos=True,
            image_delivery=IMAGE_DELIVERY_EMBEDS,
        )

        self.assertEqual(embed_urls, IMAGE_URLS)
        self.assertEqual(file_urls, [])

    def test_file_delivery_keeps_images_as_attachments_only(self) -> None:
        embed_urls, file_urls = _twitter_image_urls_for_delivery(
            twitter_post(),
            attach_photos=True,
            image_delivery=IMAGE_DELIVERY_FILES,
        )

        self.assertEqual(embed_urls, [])
        self.assertEqual(file_urls, IMAGE_URLS)


class HampangPollTimingTests(unittest.TestCase):
    def cog_for_poll_tests(self) -> NewsCog:
        cog = object.__new__(NewsCog)
        cog._last_hampang_poll_at = None
        return cog

    def test_hampang_polls_outside_twitter_tracking_windows(self) -> None:
        cog = self.cog_for_poll_tests()
        now = datetime(2026, 9, 10, 23, 0, tzinfo=timezone.utc)

        self.assertTrue(cog._should_poll_hampang_now(now))

    def test_hampang_poll_interval_matches_regular_news_cadence(self) -> None:
        cog = self.cog_for_poll_tests()
        now = datetime.now(timezone.utc)
        cog._last_hampang_poll_at = now - timedelta(seconds=HAMPANG_AUTO_POLL_INTERVAL_SECONDS)

        self.assertEqual(HAMPANG_AUTO_POLL_INTERVAL_SECONDS, 30)
        self.assertTrue(cog._should_poll_hampang_now(now))
        cog._last_hampang_poll_at = now - timedelta(seconds=HAMPANG_AUTO_POLL_INTERVAL_SECONDS - 1)
        self.assertFalse(cog._should_poll_hampang_now(now))


class HampangAutoSendFilterTests(unittest.TestCase):
    def test_posts_before_current_twitter_window_are_still_sendable(self) -> None:
        cog = object.__new__(NewsCog)
        now = datetime.now(timezone.utc)
        target = hampang_target(
            created_at=now - timedelta(hours=6),
        )
        post = twitter_post(
            created_at=now - timedelta(hours=4),
        )

        sendable = cog._auto_sendable_hampang_x_posts(
            [post],
            target=target,
            max_age_seconds=24 * 60 * 60,
        )

        self.assertEqual(sendable, [post])


if __name__ == "__main__":
    unittest.main()
