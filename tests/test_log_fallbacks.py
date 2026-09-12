from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase, mock

from src.bot import NewsCog
from src.bot_helpers import (
    _display_body_and_trailing_tags,
    _display_title_for_post,
    _display_title_for_twitter_post,
    _embed_for_twitter_post,
    _legacy_x_display_needs_rewrite,
    _post_source_label,
    _refresh_legacy_x_display_text,
    _response_content_length,
    _select_twitter_video_url,
    _twitter_status_id_from_url,
    _twitter_video_upload_candidates,
    _unescape_html_text,
)
from src.bot_constants import (
    IMAGE_DELIVERY_EMBEDS,
    IMAGE_DELIVERY_FILES,
    IMAGE_FAILED_URL_RETRY_AFTER_SECONDS,
    KST,
    NEWS_SOURCE_TWITTER,
    X_SOURCE_DISPLAY_NAME,
)
from src.clients.steam_client import _title_from_text as steam_title_from_text
from src.clients.x_client import _clean_tweet_text, _title_from_text as x_title_from_text
from src.core.models import NewsPost, TwitterPost


ENCODED_TITLE = "&lt; TGS 2026 프로젝트문 ‘림버스 컴퍼니’ 부스 추가 안내 &gt;"
DECODED_TITLE = "< TGS 2026 프로젝트문 ‘림버스 컴퍼니’ 부스 추가 안내 >"
VIDEO_URLS = [
    "https://video.twimg.com/ext_tw_video/1/pu/vid/640x360/low.mp4",
    "https://video.twimg.com/ext_tw_video/1/pu/vid/1280x720/mid.mp4",
    "https://video.twimg.com/ext_tw_video/1/pu/vid/1920x1080/high.mp4",
]


def news_post(**overrides: object) -> NewsPost:
    payload = {
        "post_id": "koreana:1",
        "source_user": "Limbus Company Steam News",
        "url": "https://example.test/news",
        "text": "본문",
        "title": ENCODED_TITLE,
        "created_at": datetime.now(timezone.utc),
        "image_urls": [],
        "raw": {},
    }
    payload.update(overrides)
    return NewsPost(**payload)  # type: ignore[arg-type]


def twitter_post(**overrides: object) -> TwitterPost:
    payload = {
        "post_id": "2098360000000000000",
        "author_username": "Ham_PangPang",
        "url": "https://x.com/Ham_PangPang/status/2098360000000000000",
        "title": f"RT @LimbusCompany_B: {ENCODED_TITLE}",
        "text": f"RT @LimbusCompany_B: {ENCODED_TITLE}",
        "created_at": datetime.now(timezone.utc),
        "image_urls": [],
        "raw": {"retweeted_username": "LimbusCompany_B"},
    }
    payload.update(overrides)
    return TwitterPost(**payload)  # type: ignore[arg-type]


class HtmlTitleTests(TestCase):
    def test_steam_title_unescapes_entities(self) -> None:
        self.assertEqual(steam_title_from_text(ENCODED_TITLE, "fallback"), DECODED_TITLE)

    def test_tweet_text_unescapes_entities(self) -> None:
        self.assertEqual(_clean_tweet_text(ENCODED_TITLE), DECODED_TITLE)
        self.assertEqual(x_title_from_text(ENCODED_TITLE), DECODED_TITLE[:80])

    def test_display_titles_keep_inner_rt_text(self) -> None:
        steam = news_post()
        twitter = news_post(
            post_id="twitter:x:1",
            source_user="Ham_PangPang",
            title=f"RT @LimbusCompany_B: {ENCODED_TITLE}",
            text=f"RT @LimbusCompany_B: {ENCODED_TITLE}",
            raw={
                "source_type": NEWS_SOURCE_TWITTER,
                "retweeted_username": "LimbusCompany_B",
            },
        )

        self.assertIn(DECODED_TITLE, _display_title_for_post(steam))
        self.assertIn("[X(구 트위터)]", _display_title_for_post(twitter))
        self.assertIn("RT @LimbusCompany_B:", _display_title_for_post(twitter))
        self.assertIn("TGS 2026", _display_title_for_post(twitter))
        self.assertIn("TGS 2026", _display_title_for_twitter_post(twitter_post()))

    def test_hampang_embed_unescapes_html_and_uses_new_x_label(self) -> None:
        embed = _embed_for_twitter_post(twitter_post())
        self.assertIn(DECODED_TITLE, embed.title or "")
        self.assertNotIn("&lt;", embed.title or "")
        self.assertNotIn("&lt;", embed.description or "")
        self.assertIn(DECODED_TITLE, embed.description or "")
        self.assertEqual(embed.footer.text, f"출처: {X_SOURCE_DISPLAY_NAME}")

    def test_news_body_unescapes_html_entities(self) -> None:
        steam = news_post(text=ENCODED_TITLE)
        body, _tags = _display_body_and_trailing_tags(steam)
        self.assertEqual(body, DECODED_TITLE)

    def test_html_entities_need_rewrite_even_with_new_x_label(self) -> None:
        self.assertTrue(
            _legacy_x_display_needs_rewrite(
                f"출처: {X_SOURCE_DISPLAY_NAME}\n{ENCODED_TITLE}",
                image_delivery=IMAGE_DELIVERY_EMBEDS,
            )
        )

    def test_unescape_handles_double_encoding(self) -> None:
        self.assertEqual(_unescape_html_text("&amp;lt;TGS&amp;gt;"), "<TGS>")


class VideoCandidateTests(TestCase):
    def test_prefers_1080p_then_lower_resolutions(self) -> None:
        ordered = _twitter_video_upload_candidates(VIDEO_URLS)

        self.assertTrue(ordered[0].endswith("1920x1080/high.mp4"))
        self.assertEqual(_select_twitter_video_url(VIDEO_URLS), ordered[0])
        self.assertEqual(
            [url.rsplit("/", 2)[1] for url in ordered],
            ["1920x1080", "1280x720", "640x360"],
        )

    def test_content_range_reports_full_size(self) -> None:
        response = SimpleNamespace(
            headers={"Content-Range": "bytes 0-0/312663731", "Content-Length": "1"}
        )

        self.assertEqual(_response_content_length(response), 312663731)


class LogCooldownTests(TestCase):
    def cog(self) -> NewsCog:
        instance = object.__new__(NewsCog)
        instance._failed_image_urls = {}
        instance._steam_sync_failure_log_at = {}
        instance._logged_stale_news_post_ids = {}
        instance._twitter_video_too_large_logged = {}
        instance._twitter_video_content_lengths = {}
        return instance

    def test_stale_news_is_logged_once_per_post(self) -> None:
        cog = self.cog()

        self.assertTrue(cog._should_log_stale_news_post("twitter:x:1"))
        self.assertFalse(cog._should_log_stale_news_post("twitter:x:1"))
        self.assertTrue(cog._should_log_stale_news_post("twitter:x:2"))

    def test_steam_sync_failure_log_is_rate_limited(self) -> None:
        cog = self.cog()

        self.assertTrue(cog._should_log_steam_sync_failure("koreana"))
        self.assertFalse(cog._should_log_steam_sync_failure("koreana"))
        self.assertTrue(cog._should_log_steam_sync_failure("english"))

    def test_failed_image_urls_are_skipped_until_cooldown(self) -> None:
        cog = self.cog()
        url = "https://clan.fastly.steamstatic.com/images/example.jpg"

        cog._remember_failed_image_url(url)
        self.assertTrue(cog._image_url_recently_failed(url))

        cog._failed_image_urls[url] = perf_counter() - IMAGE_FAILED_URL_RETRY_AFTER_SECONDS - 1
        self.assertFalse(cog._image_url_recently_failed(url))
        self.assertNotIn(url, cog._failed_image_urls)

    def test_oversized_video_warning_is_logged_once(self) -> None:
        cog = self.cog()
        url = VIDEO_URLS[0]

        with mock.patch("src.bot.LOGGER") as logger:
            cog._log_twitter_video_too_large(url, 312663731)
            cog._log_twitter_video_too_large(url, 312663731)

        logger.warning.assert_called_once()


class EgoRefreshFallbackTests(IsolatedAsyncioTestCase):
    async def test_failed_weekly_refresh_does_not_retry_the_same_day(self) -> None:
        cog = object.__new__(NewsCog)
        cog._last_ego_gift_update_check_date = None
        cog._refresh_ego_gift_data = mock.AsyncMock(side_effect=NotImplementedError)
        friday = datetime(2026, 9, 11, 8, 13, tzinfo=KST)

        with mock.patch("src.bot._is_ego_gift_update_window", return_value=True):
            with mock.patch("src.bot.datetime") as mocked_datetime:
                mocked_datetime.now.return_value = friday
                await NewsCog.refresh_ego_gifts.coro(cog)

        self.assertEqual(cog._last_ego_gift_update_check_date, friday.date())
        cog._refresh_ego_gift_data.assert_awaited_once()


class XDisplayRewriteTests(TestCase):
    def test_source_label_uses_former_twitter_name(self) -> None:
        twitter = news_post(
            post_id="twitter:x:1",
            raw={"source_type": NEWS_SOURCE_TWITTER},
        )
        self.assertEqual(_post_source_label(twitter), X_SOURCE_DISPLAY_NAME)
        self.assertEqual(
            _refresh_legacy_x_display_text("출처: X(트위터)"),
            f"출처: {X_SOURCE_DISPLAY_NAME}",
        )

    def test_status_id_is_read_from_tweet_url(self) -> None:
        self.assertEqual(
            _twitter_status_id_from_url(
                "https://x.com/Ham_PangPang/status/2098360000000000000"
            ),
            "2098360000000000000",
        )

    def test_legacy_embed_and_file_conflict_is_detected(self) -> None:
        self.assertTrue(
            _legacy_x_display_needs_rewrite(
                "출처: X(트위터)",
                image_delivery=IMAGE_DELIVERY_EMBEDS,
            )
        )
        self.assertTrue(
            _legacy_x_display_needs_rewrite(
                f"출처: {X_SOURCE_DISPLAY_NAME}",
                has_attachments=True,
                image_delivery=IMAGE_DELIVERY_EMBEDS,
            )
        )
        self.assertTrue(
            _legacy_x_display_needs_rewrite(
                f"출처: {X_SOURCE_DISPLAY_NAME}",
                has_embed_image=True,
                image_delivery=IMAGE_DELIVERY_FILES,
            )
        )
        self.assertFalse(
            _legacy_x_display_needs_rewrite(
                f"출처: {X_SOURCE_DISPLAY_NAME}",
                image_delivery=IMAGE_DELIVERY_EMBEDS,
            )
        )
