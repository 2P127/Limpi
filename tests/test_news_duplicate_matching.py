from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from src.bot_helpers import (
    _matching_steam_posts_for_twitter,
    _news_posts_without_later_content_duplicates,
    _steam_news_post_ids_for_twitter_posts,
    _twitter_posts_as_news_posts,
    _youtube_video_id_from_url,
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
    post_id: str = "1900000000000000000",
) -> TwitterPost:
    return TwitterPost(
        post_id=post_id,
        author_username="LimbusCompany_B",
        url=f"https://x.com/LimbusCompany_B/status/{post_id}",
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

        self.assertEqual(_matching_steam_posts_for_twitter(tweet, [steam]), [steam])
        self.assertNotIn("prefer_steam_post_ids", converted[0].raw)

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
            text="Steam link is not included in this post.",
            created_at=tweet_created_at,
        )

        self.assertEqual(_matching_steam_posts_for_twitter(tweet, [steam]), [])

    def test_exact_steam_link_matches_even_after_thirty_minutes(self) -> None:
        steam = steam_post(
            title="7/9 정기 업데이트 이후 발생한 이슈 추가 안내 및 동시접속자 갱신 기념 보상 안내",
            created_at=datetime(2026, 7, 9, 9, 6, tzinfo=timezone.utc),
        )
        tweet = twitter_post(
            title="[X(트위터)] 7/9 정기 업데이트 이후 발생한 이슈 추가 안내 및 동시접속자 갱신 기념 보상 안내",
            text=f"자세한 내용은 Steam 공지를 확인해주세요.\n{STEAM_URL}",
            created_at=datetime(2026, 7, 10, 2, 43, tzinfo=timezone.utc),
        )

        self.assertEqual(_matching_steam_posts_for_twitter(tweet, [steam]), [steam])

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
        converted = _twitter_posts_as_news_posts([tweet], [steam])
        self.assertEqual(converted[0].raw["prefer_steam_post_ids"], [steam.post_id])

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


def youtube_watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def youtube_short_url(video_id: str) -> str:
    return f"https://youtu.be/{video_id}"


def linked_steam_post(
    *,
    post_id: str,
    title: str,
    created_at: datetime,
    youtube_id: str,
) -> NewsPost:
    watch_url = youtube_watch_url(youtube_id)
    return NewsPost(
        post_id=f"steam:koreana:{post_id}",
        source_user="Limbus Company Steam News",
        url=f"https://store.steampowered.com/news/app/1973530/view/{post_id}",
        title=title,
        text=f"{title}\n{watch_url}",
        created_at=created_at,
        image_urls=[],
        raw={
            "language": "koreana",
            "event_gid": post_id,
            "youtube_urls": [watch_url],
        },
    )


def linked_twitter_post(
    *,
    post_id: str,
    title: str,
    created_at: datetime,
    youtube_id: str,
    raw: dict | None = None,
) -> TwitterPost:
    youtube_url = youtube_short_url(youtube_id)
    payload = {
        "youtube_urls": [youtube_url],
        "link_urls": [youtube_url],
    }
    if raw:
        payload.update(raw)
    return twitter_post(
        post_id=post_id,
        title=title,
        text=f"{title}\n{youtube_url}",
        raw=payload,
        created_at=created_at,
    )


def twitter_as_news(post: TwitterPost) -> NewsPost:
    return _twitter_posts_as_news_posts([post], [])[0]


class NewsContentDuplicateTests(unittest.TestCase):
    def test_youtube_short_and_watch_urls_share_the_same_video_id(self) -> None:
        video_id = "abcdefghijk"
        self.assertEqual(_youtube_video_id_from_url(youtube_short_url(video_id)), video_id)
        self.assertEqual(_youtube_video_id_from_url(youtube_watch_url(video_id)), video_id)
        self.assertEqual(
            _youtube_video_id_from_url(f"{youtube_watch_url(video_id)}&feature=share"),
            video_id,
        )

    def test_shared_youtube_video_matches_even_when_titles_differ(self) -> None:
        steam = linked_steam_post(
            post_id="555000111222333444",
            title="신규 영상 안내",
            created_at=datetime(2026, 9, 11, 10, 8, tzinfo=timezone.utc),
            youtube_id="abcdefghijk",
        )
        tweet = linked_twitter_post(
            post_id="1901111111111111111",
            title="티저 공개",
            created_at=datetime(2026, 9, 11, 10, 6, tzinfo=timezone.utc),
            youtube_id="abcdefghijk",
        )

        self.assertEqual(_matching_steam_posts_for_twitter(tweet, [steam]), [steam])
        converted = _twitter_posts_as_news_posts([tweet], [steam])
        self.assertNotIn("prefer_steam_post_ids", converted[0].raw)

    def test_earliest_post_wins_for_the_same_youtube_video(self) -> None:
        steam = linked_steam_post(
            post_id="555000111222333444",
            title="신규 영상 안내",
            created_at=datetime(2026, 9, 11, 10, 8, tzinfo=timezone.utc),
            youtube_id="abcdefghijk",
        )
        original = twitter_as_news(
            linked_twitter_post(
                post_id="1901111111111111111",
                title="티저 공개",
                created_at=datetime(2026, 9, 11, 10, 6, tzinfo=timezone.utc),
                youtube_id="abcdefghijk",
            )
        )
        retweet = twitter_as_news(
            linked_twitter_post(
                post_id="1902222222222222222",
                title="RT @LimbusCompany_B",
                created_at=datetime(2026, 9, 11, 10, 29, tzinfo=timezone.utc),
                youtube_id="abcdefghijk",
                raw={
                    "retweeted_tweet_id": "1901111111111111111",
                    "retweeted_username": "LimbusCompany_B",
                },
            )
        )

        selected, skipped = _news_posts_without_later_content_duplicates(
            [steam, original, retweet],
            [],
        )

        self.assertEqual([post.post_id for post in selected], [original.post_id])
        self.assertEqual(
            {post.post_id for post in skipped},
            {steam.post_id, retweet.post_id},
        )

    def test_already_announced_post_blocks_later_sources(self) -> None:
        steam = linked_steam_post(
            post_id="555000111222333444",
            title="신규 영상 안내",
            created_at=datetime(2026, 9, 11, 10, 8, tzinfo=timezone.utc),
            youtube_id="abcdefghijk",
        )
        original = twitter_as_news(
            linked_twitter_post(
                post_id="1901111111111111111",
                title="티저 공개",
                created_at=datetime(2026, 9, 11, 10, 6, tzinfo=timezone.utc),
                youtube_id="abcdefghijk",
            )
        )
        retweet = twitter_as_news(
            linked_twitter_post(
                post_id="1902222222222222222",
                title="RT @LimbusCompany_B",
                created_at=datetime(2026, 9, 11, 10, 29, tzinfo=timezone.utc),
                youtube_id="abcdefghijk",
                raw={"retweeted_tweet_id": "1901111111111111111"},
            )
        )

        selected, skipped = _news_posts_without_later_content_duplicates(
            [steam, retweet],
            [original],
        )

        self.assertEqual(selected, [])
        self.assertEqual({post.post_id for post in skipped}, {steam.post_id, retweet.post_id})

    def test_unrelated_youtube_videos_are_not_collapsed(self) -> None:
        steam = linked_steam_post(
            post_id="555000111222333444",
            title="신규 영상 안내",
            created_at=datetime(2026, 9, 11, 10, 8, tzinfo=timezone.utc),
            youtube_id="abcdefghijk",
        )
        other = twitter_as_news(
            linked_twitter_post(
                post_id="1903333333333333333",
                title="다른 영상",
                created_at=datetime(2026, 9, 11, 10, 9, tzinfo=timezone.utc),
                youtube_id="AAAAAAAAAAA",
            )
        )

        selected, skipped = _news_posts_without_later_content_duplicates(
            [steam, other],
            [],
        )

        self.assertEqual({post.post_id for post in selected}, {steam.post_id, other.post_id})
        self.assertEqual(skipped, [])

    def test_duplicate_rule_applies_to_any_shared_video_not_a_specific_title(self) -> None:
        first_event_steam = linked_steam_post(
            post_id="111000111222333444",
            title="정기 업데이트 안내",
            created_at=datetime(2026, 3, 2, 10, 8, tzinfo=timezone.utc),
            youtube_id="FirstVideo1",
        )
        first_event_tweet = twitter_as_news(
            linked_twitter_post(
                post_id="1911111111111111111",
                title="업데이트 예고",
                created_at=datetime(2026, 3, 2, 10, 5, tzinfo=timezone.utc),
                youtube_id="FirstVideo1",
            )
        )
        second_event_steam = linked_steam_post(
            post_id="222000111222333444",
            title="발푸르기스 정보 안내",
            created_at=datetime(2026, 7, 9, 9, 40, tzinfo=timezone.utc),
            youtube_id="SecondVide1",
        )
        second_event_tweet = twitter_as_news(
            linked_twitter_post(
                post_id="1922222222222222222",
                title="발푸르기스 티저",
                created_at=datetime(2026, 7, 9, 9, 34, tzinfo=timezone.utc),
                youtube_id="SecondVide1",
            )
        )

        selected, skipped = _news_posts_without_later_content_duplicates(
            [first_event_steam, first_event_tweet, second_event_steam, second_event_tweet],
            [],
        )

        self.assertEqual(
            {post.post_id for post in selected},
            {first_event_tweet.post_id, second_event_tweet.post_id},
        )
        self.assertEqual(
            {post.post_id for post in skipped},
            {first_event_steam.post_id, second_event_steam.post_id},
        )

    def test_steam_link_still_prefers_steam(self) -> None:
        steam = steam_post(
            title="2026.07.09 (KST) 제9회 발푸르기스의 밤 신규 인격 & E.G.O 정보 안내"
        )
        tweet = twitter_post(
            title="신규 인격 정보 안내",
            text=f"자세한 내용은 Steam 공지를 확인해주세요. {STEAM_URL}",
            raw={"link_urls": [STEAM_URL]},
        )
        converted = _twitter_posts_as_news_posts([tweet], [steam])
        self.assertEqual(converted[0].raw["prefer_steam_post_ids"], [steam.post_id])


if __name__ == "__main__":
    unittest.main()
