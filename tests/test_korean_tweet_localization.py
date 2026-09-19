from __future__ import annotations

from unittest import IsolatedAsyncioTestCase, TestCase, mock

from src.bot import NewsCog
from src.bot_helpers import (
    _extract_korean_only_tweet_text,
    _tweet_text_has_korean,
    _tweet_text_needs_korean_translation,
    _twitter_status_id_for_translation,
    _with_localized_twitter_text,
)
from src.clients.x_client import LimbusXClient, _translation_text_from_payload
from src.core.models import TwitterPost


HAMPANG_MULTILINGUAL = """\
< ‘LIMBUS COMPANY - Tokyo Game Show 2026’ 21일 부스 운영 취소 안내 >

안녕하세요.
프로젝트문 햄햄팡팡 점장 송명선입니다.

태풍의 영향으로 9월 21일(월) 도쿄게임쇼 2026의 개최가 취소됨에 따라, ‘림버스 컴퍼니 부스’ 역시 해당 일자의 운영을 진행하지 않습니다.

9월 20일(일)은 기존 일정과 동일하게 정상 운영됩니다.

ー
「‘LIMBUS COMPANY - Tokyo Game Show 2026’ 21日ブース運営中止のお知らせ」

こんにちは。
プロジェクトムーン ハムハムパンパン店長の宋明善です。

台風の影響により、9月21日(月)の東京ゲームショウ2026の開催が中止となったため、
『リンバスカンパニーブース』も当該日の運営を行いません。

-
Notice Regarding Cancellation of ‘LIMBUS COMPANY - Tokyo Game Show 2026’ Booth Operations on the 21st

Hello.
This is Song Myung-sun, manager of Project Moon HamHamPangPang.

Due to the typhoon, Tokyo Game Show 2026 on September 21 (Mon) has been cancelled.
"""


JAPANESE_ONLY = """\
【重要】台風25号接近に伴う
9月20日(日)・21日(月・祝)の開催及びチケットの返金について

最新の気象情報や交通機関への影響等を勘案して慎重に検討した結果、
下記のとおり決定しましたのでお知らせいたします。
"""


class KoreanTweetFilterTests(TestCase):
    def test_extracts_korean_section_from_multilingual_post(self) -> None:
        extracted = _extract_korean_only_tweet_text(HAMPANG_MULTILINGUAL)
        self.assertIn("부스 운영 취소 안내", extracted)
        self.assertIn("프로젝트문 햄햄팡팡", extracted)
        self.assertNotIn("プロジェクトムーン", extracted)
        self.assertNotIn("Notice Regarding Cancellation", extracted)
        self.assertTrue(_tweet_text_has_korean(extracted))
        self.assertFalse(_tweet_text_needs_korean_translation(extracted))

    def test_preserves_rt_prefix_when_filtering(self) -> None:
        text = f"RT @Ham_PangPang: {HAMPANG_MULTILINGUAL}"
        extracted = _extract_korean_only_tweet_text(text)
        self.assertTrue(extracted.startswith("RT @Ham_PangPang:"))
        self.assertIn("프로젝트문 햄햄팡팡", extracted)
        self.assertNotIn("プロジェクトムーン", extracted)

    def test_japanese_only_needs_translation(self) -> None:
        self.assertFalse(_tweet_text_has_korean(JAPANESE_ONLY))
        self.assertTrue(_tweet_text_needs_korean_translation(JAPANESE_ONLY))
        self.assertEqual(_extract_korean_only_tweet_text(JAPANESE_ONLY), JAPANESE_ONLY)

    def test_korean_only_does_not_need_translation(self) -> None:
        text = "안녕하세요. 림버스 컴퍼니 소식입니다."
        self.assertEqual(_extract_korean_only_tweet_text(text), text)
        self.assertFalse(_tweet_text_needs_korean_translation(text))

    def test_translation_id_prefers_retweeted_tweet(self) -> None:
        post = TwitterPost(
            post_id="x:111",
            author_username="LimbusCompany_B",
            url="https://x.com/LimbusCompany_B/status/111",
            text="RT @tokyo_game_show: hello",
            title="hello",
            created_at=None,
            image_urls=[],
            raw={
                "tweet_id": "111",
                "retweeted_tweet_id": "222",
                "retweeted_username": "tokyo_game_show",
            },
        )
        self.assertEqual(_twitter_status_id_for_translation(post), "222")

    def test_with_localized_twitter_text_keeps_full_text(self) -> None:
        post = TwitterPost(
            post_id="x:1",
            author_username="Ham_PangPang",
            url="https://x.com/Ham_PangPang/status/1",
            text=HAMPANG_MULTILINGUAL,
            title="title",
            created_at=None,
            image_urls=[],
            raw={"tweet_id": "1"},
        )
        localized = _with_localized_twitter_text(post, "한국어만")
        self.assertEqual(localized.text, "한국어만")
        self.assertEqual(localized.raw["full_text"], HAMPANG_MULTILINGUAL)
        self.assertTrue(localized.raw["korean_localized"])


class TranslateTweetClientTests(IsolatedAsyncioTestCase):
    async def test_404_pauses_requests_then_recovers(self) -> None:
        from datetime import datetime, timedelta, timezone

        client = LimbusXClient(mock.Mock(x_account_username="test"), mock.Mock())
        client._has_twitter_auth = mock.Mock(return_value=True)
        client._twitter_api_headers = mock.Mock(return_value={})
        client._active_rate_limited_until = mock.Mock(return_value=None)
        missing = mock.AsyncMock(status=404, reason="Not Found", headers={})
        missing.text.return_value = '{"errors":[{"code":34}]}'
        missing.__aenter__.return_value = missing
        success = mock.AsyncMock(status=200)
        success.json.return_value = {
            "translationState": "Success", "translation": "번역 성공", "destinationLanguage": "ko",
        }
        success.__aenter__.return_value = success
        client.session.get.side_effect = [missing, success]

        self.assertIsNone(await client.translate_tweet("123"))
        self.assertIsNone(await client.translate_tweet("456"))
        self.assertEqual(client.session.get.call_count, 1)
        client._translate_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.assertEqual(await client.translate_tweet("123"), "번역 성공")
        self.assertEqual(client.session.get.call_count, 2)

    async def test_wrong_language_is_not_cached_as_success(self) -> None:
        client = LimbusXClient(mock.Mock(x_account_username="test"), mock.Mock())
        client._has_twitter_auth = mock.Mock(return_value=True)
        client._twitter_api_headers = mock.Mock(return_value={})
        client._active_rate_limited_until = mock.Mock(return_value=None)
        response = mock.AsyncMock(status=200)
        response.json.return_value = {"translation": "English text", "destinationLanguage": "en"}
        response.__aenter__.return_value = response
        client.session.get.return_value = response
        self.assertIsNone(await client.translate_tweet("123"))
        self.assertEqual(client._translate_cache, {})

    async def test_translate_tweet_parses_translation_field(self) -> None:
        client = object.__new__(LimbusXClient)
        client.config = mock.Mock(x_auth_token="auth", x_ct0="ct0")
        client.session = mock.AsyncMock()
        client._rate_limited_until = None
        client._rate_limit_failures = 0
        client._server_error_failures = 0
        client._translate_cache = {}
        client._last_backoff_log_until = None
        client.account_username = "LimbusCompany_B"

        response = mock.AsyncMock()
        response.status = 200
        response.json = mock.AsyncMock(
            return_value={
                "id": "2101204902513180905",
                "translation": "한국어 번역 본문입니다.",
                "sourceLanguage": "ja",
                "destinationLanguage": "ko",
            }
        )
        response.__aenter__ = mock.AsyncMock(return_value=response)
        response.__aexit__ = mock.AsyncMock(return_value=None)
        client.session.get = mock.Mock(return_value=response)
        client._twitter_api_headers = mock.Mock(return_value={})
        client._active_rate_limited_until = mock.Mock(return_value=None)
        client._has_twitter_auth = mock.Mock(return_value=True)

        translated = await LimbusXClient.translate_tweet(client, "2101204902513180905")
        self.assertEqual(translated, "한국어 번역 본문입니다.")
        cached = await LimbusXClient.translate_tweet(client, "2101204902513180905")
        self.assertEqual(cached, translated)
        self.assertEqual(client.session.get.call_count, 1)
        args, kwargs = client.session.get.call_args
        self.assertIn("destinationLanguage=None", args[0])
        self.assertEqual(kwargs["headers"]["x-twitter-client-language"], "ko")

    async def test_translate_tweet_returns_none_on_http_error(self) -> None:
        client = object.__new__(LimbusXClient)
        client.config = mock.Mock(x_auth_token="auth", x_ct0="ct0")
        client.session = mock.AsyncMock()
        client._rate_limited_until = None
        client._rate_limit_failures = 0
        client._server_error_failures = 0
        client._translate_cache = {}
        client._last_backoff_log_until = None
        client.account_username = "LimbusCompany_B"

        response = mock.AsyncMock()
        response.status = 403
        response.reason = "Forbidden"
        response.headers = {}
        response.text = mock.AsyncMock(return_value="denied")
        response.__aenter__ = mock.AsyncMock(return_value=response)
        response.__aexit__ = mock.AsyncMock(return_value=None)
        client.session.get = mock.Mock(return_value=response)
        client._twitter_api_headers = mock.Mock(return_value={})
        client._active_rate_limited_until = mock.Mock(return_value=None)
        client._has_twitter_auth = mock.Mock(return_value=True)

        translated = await LimbusXClient.translate_tweet(client, "123")
        self.assertIsNone(translated)
        self.assertIsNone(await client.translate_tweet("456"))
        self.assertEqual(client.session.get.call_count, 1)

    def test_translation_payload_parser(self) -> None:
        self.assertEqual(
            _translation_text_from_payload({"translation": " 안녕 "}),
            "안녕",
        )
        self.assertIsNone(_translation_text_from_payload({"translation": ""}))


class LocalizeTwitterPostTests(IsolatedAsyncioTestCase):
    async def test_localize_filters_multilingual_without_translate(self) -> None:
        cog = object.__new__(NewsCog)
        cog.x_source = mock.AsyncMock()
        post = TwitterPost(
            post_id="x:1",
            author_username="Ham_PangPang",
            url="https://x.com/Ham_PangPang/status/1",
            text=HAMPANG_MULTILINGUAL,
            title="title",
            created_at=None,
            image_urls=[],
            raw={"tweet_id": "1"},
        )
        localized = await NewsCog._localize_twitter_post(cog, post)
        self.assertIn("프로젝트문 햄햄팡팡", localized.text)
        self.assertNotIn("プロジェクトムーン", localized.text)
        cog.x_source.translate_tweet.assert_not_called()

    async def test_localize_translates_foreign_only_and_falls_back(self) -> None:
        cog = object.__new__(NewsCog)
        client = mock.AsyncMock()
        client.translate_tweet = mock.AsyncMock(return_value="한국어 번역")
        cog.x_source = client
        post = TwitterPost(
            post_id="x:2101204902513180905",
            author_username="LimbusCompany_B",
            url="https://x.com/LimbusCompany_B/status/2101204902513180905",
            text=f"RT @tokyo_game_show: {JAPANESE_ONLY}",
            title="title",
            created_at=None,
            image_urls=[],
            raw={
                "tweet_id": "2101204902513180905",
                "retweeted_tweet_id": "2101204902513180905",
                "retweeted_username": "tokyo_game_show",
            },
        )
        localized = await NewsCog._localize_twitter_post(cog, post, client=client)
        self.assertIn("한국어 번역", localized.text)
        self.assertEqual(localized.raw["translation_source"], "x_translate")
        client.translate_tweet.assert_awaited_once()

        client.translate_tweet = mock.AsyncMock(return_value=None)
        post2 = TwitterPost(
            post_id="x:2",
            author_username="LimbusCompany_B",
            url="https://x.com/LimbusCompany_B/status/2",
            text=JAPANESE_ONLY,
            title="title",
            created_at=None,
            image_urls=[],
            raw={"tweet_id": "2"},
        )
        fallback = await NewsCog._localize_twitter_post(cog, post2, client=client)
        self.assertEqual(fallback.text, JAPANESE_ONLY)
        self.assertFalse(fallback.raw.get("korean_localized", False))
