from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from src.bot_helpers import (
    _matching_steam_posts_for_twitter,
    _steam_news_post_ids_for_twitter_posts,
    _twitter_posts_as_news_posts,
)
from src.core.models import NewsPost, TwitterPost


STEAM_URL = "https://store.steampowered.com/news/app/1973530/view/669495150925320961"


def steam_post(
    *,
    title: str,
    text: str = "",
    created_at: datetime | None = None,
) -> NewsPost:
    return NewsPost(
        post_id="steam:koreana:669495150925320961",
        source_user="Limbus Company Steam News",
        url=STEAM_URL,
        title=title,
        text=text or title,
        created_at=created_at or datetime(2026, 7, 6, 9, 34, tzinfo=timezone.utc),
        image_urls=[],
        raw={"language": "koreana", "event_gid": "669495150925320961"},
    )


def twitter_post(
    *,
    title: str,
    text: str,
    raw: dict | None = None,
    created_at: datetime | None = None,
) -> TwitterPost:
    return TwitterPost(
        post_id="1900000000000000000",
        author_username="LimbusCompany_B",
        url="https://x.com/LimbusCompany_B/status/1900000000000000000",
        title=title,
        text=text,
        created_at=created_at or datetime(2026, 7, 6, 9, 35, tzinfo=timezone.utc),
        image_urls=[],
        raw=raw or {},
    )


class NewsDuplicateMatchingTests(unittest.TestCase):
    def test_twitter_text_steam_url_matches_without_link_metadata(self) -> None:
        steam = steam_post(
            title="2026.07.09 (KST) 제9회 발푸르기스의 밤 신규 인격 & E.G.O 정보 안내"
        )
        tweet = twitter_post(
            title="[X(트위터)] 2026.07.09 (KST) 제9회 발푸르기스의 밤 신규 인격 & E.G.O 정보 안내",
            text=(
                "2026.07.09 (KST) 제9회 발푸르기스의 밤 신규 인격 & E.G.O 정보 안내\n\n"
                f"{STEAM_URL}"
            ),
        )

        self.assertEqual(_matching_steam_posts_for_twitter(tweet, [steam]), [steam])

    def test_same_title_matches_without_steam_link(self) -> None:
        steam = steam_post(
            title="2026.07.09 (KST) 제9회 발푸르기스의 밤 신규 인격 & E.G.O 정보 안내"
        )
        tweet = twitter_post(
            title=(
                "[X(트위터)] 2026.07.09 (KST) 제9회 발푸르기스의 밤 신규 인격 & E.G.O 정보 안내 "
                "/ 第9回ヴァルプルギスの夜新規人格&E.G.O情報のご案内"
            ),
            text="[000] 새벽 사무소 해결사 파우스트 / Dawn Office Fixer Faust",
        )

        self.assertEqual(_matching_steam_posts_for_twitter(tweet, [steam]), [steam])

    def test_bracketed_marker_does_not_block_title_match(self) -> None:
        steam = steam_post(
            title="2026.07.09 (KST) 제9회 발푸르기스의 밤 [나] 신규 인격 정보 안내"
        )
        tweet = twitter_post(
            title="2026.07.09 (KST) 제9회 발푸르기스의 밤 신규 인격 정보 안내",
            text="새벽 사무소 해결사 파우스트 / Dawn Office Fixer Faust",
        )

        converted = _twitter_posts_as_news_posts([tweet], [steam])

        self.assertEqual(converted[0].raw["prefer_steam_post_ids"], [steam.post_id])

    def test_steam_link_in_twitter_text_is_used_for_cache_refresh(self) -> None:
        tweet = twitter_post(
            title="신규 인격 정보 안내",
            text=f"자세한 내용은 Steam 공지를 확인해주세요. {STEAM_URL}",
        )

        self.assertEqual(
            _steam_news_post_ids_for_twitter_posts([tweet]),
            ["669495150925320961"],
        )

    def test_matching_is_limited_to_thirty_minutes_before_or_after(self) -> None:
        tweet_created_at = datetime(2026, 7, 6, 9, 35, tzinfo=timezone.utc)
        steam = steam_post(
            title="2026.07.09 (KST) 제9회 발푸르기스의 밤 신규 인격 & E.G.O 정보 안내",
            created_at=tweet_created_at + timedelta(minutes=31),
        )
        tweet = twitter_post(
            title="2026.07.09 (KST) 제9회 발푸르기스의 밤 신규 인격 & E.G.O 정보 안내",
            text=f"{STEAM_URL}",
            created_at=tweet_created_at,
        )

        self.assertEqual(_matching_steam_posts_for_twitter(tweet, [steam]), [])

    def test_reply_update_is_suppressed_when_steam_already_contains_fix(self) -> None:
        steam = steam_post(
            title="2026.07.09 (KST) 제9회 발푸르기스의 밤 신규 인격 & E.G.O 정보 안내",
            text=(
                "2026.07.09 (KST) 제9회 발푸르기스의 밤 신규 인격 & E.G.O 정보 안내\n\n"
                "(한국어) 새벽 사무소 대표 그레고르의 스킬3, 스킬3-2의 오탈자를 수정했습니다."
            ),
        )
        tweet = twitter_post(
            title="(한국어) 새벽 사무소 대표 그레고르의 스킬3, 스킬3-2의 오탈자를 수정했습니다.",
            text=(
                "(한국어) 새벽 사무소 대표 그레고르의 스킬3, 스킬3-2의 오탈자를 수정했습니다.\n"
                "(KR Only) Fixed typos in Dawn Office Rep Gregor's Skill 3 and Skill 3-2."
            ),
            raw={
                "in_reply_to_status_id_str": "1900000000000000001",
                "in_reply_to_screen_name": "LimbusCompany_B",
            },
            created_at=datetime(2026, 7, 6, 10, 29, tzinfo=timezone.utc),
        )

        self.assertEqual(_matching_steam_posts_for_twitter(tweet, [steam]), [steam])

    def test_reply_update_without_steam_fix_is_not_suppressed(self) -> None:
        steam = steam_post(
            title="2026.07.09 (KST) 제9회 발푸르기스의 밤 신규 인격 & E.G.O 정보 안내",
            text="새벽 사무소 대표 그레고르 인격 안내",
        )
        tweet = twitter_post(
            title="(한국어) 새벽 사무소 대표 그레고르의 스킬3, 스킬3-2의 오탈자를 수정했습니다.",
            text="(한국어) 새벽 사무소 대표 그레고르의 스킬3, 스킬3-2의 오탈자를 수정했습니다.",
            raw={
                "in_reply_to_status_id_str": "1900000000000000001",
                "in_reply_to_screen_name": "LimbusCompany_B",
            },
        )

        self.assertEqual(_matching_steam_posts_for_twitter(tweet, [steam]), [])


if __name__ == "__main__":
    unittest.main()
