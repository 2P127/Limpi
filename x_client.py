from __future__ import annotations

import asyncio
import json
import logging
import re
from asyncio import Lock
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import aiohttp

from config import AppConfig
from models import TwitterPost

LOGGER = logging.getLogger(__name__)
X_POST_CACHE_TTL = timedelta(seconds=5)
X_RATE_LIMIT_BACKOFF = timedelta(minutes=1)
X_RATE_LIMIT_BACKOFF_MAX = timedelta(minutes=10)
X_RATE_LIMIT_RESET_CAP = timedelta(minutes=20)
X_SERVER_ERROR_BACKOFF = timedelta(seconds=30)
X_SERVER_ERROR_BACKOFF_MAX = timedelta(minutes=5)
KST = timezone(timedelta(hours=9))

_VIDEO_THUMBNAIL_URL_FRAGMENTS = (
    "/ext_tw_video_thumb/",
    "/amplify_video_thumb/",
    "/tweet_video_thumb/",
)

_X_BEARER = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D"
    "1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)
_GQL_USER_BY_SCREEN_NAME_QID = "NimuplG1OB7Fd2btCLdBOw"
_GQL_USER_TWEETS_AND_REPLIES_QID = "D5eKzDa5ZoJuC1TCeAXbWA"
_GQL_USER_BY_SCREEN_NAME_FEATURES = {
    "hidden_profile_likes_enabled": True,
    "hidden_profile_subscriptions_enabled": True,
    "rweb_tipjar_consumption_enabled": True,
    "responsive_web_graphql_exclude_directive_enabled": True,
    "verified_phone_label_enabled": False,
    "subscriptions_verification_info_is_identity_verified_enabled": True,
    "subscriptions_verification_info_verified_since_enabled": True,
    "highlights_tweets_tab_ui_enabled": True,
    "responsive_web_twitter_article_notes_tab_enabled": True,
    "subscriptions_feature_can_gift_premium": True,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "responsive_web_graphql_timeline_navigation_enabled": True,
}
_GQL_USER_TWEETS_FEATURES = {
    "rweb_video_screen_enabled": True,
    "rweb_cashtags_enabled": True,
    "profile_label_improvements_pcf_label_in_post_enabled": True,
    "responsive_web_profile_redirect_enabled": True,
    "rweb_tipjar_consumption_enabled": True,
    "responsive_web_graphql_exclude_directive_enabled": True,
    "verified_phone_label_enabled": False,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "premium_content_api_read_enabled": False,
    "communities_web_enable_tweet_community_results_fetch": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "responsive_web_grok_analyze_button_fetch_trends_enabled": False,
    "responsive_web_grok_analyze_post_followups_enabled": True,
    "rweb_cashtags_composer_attachment_enabled": True,
    "responsive_web_jetfuel_frame": True,
    "responsive_web_grok_share_attachment_enabled": True,
    "responsive_web_grok_annotations_enabled": True,
    "articles_preview_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "rweb_conversational_replies_downvote_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "content_disclosure_indicator_enabled": True,
    "content_disclosure_ai_generated_indicator_enabled": True,
    "responsive_web_grok_show_grok_translated_post": True,
    "responsive_web_grok_analysis_button_from_backend": True,
    "post_ctas_fetch_enabled": True,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": True,
    "responsive_web_grok_image_annotation_enabled": True,
    "responsive_web_grok_imagine_annotation_enabled": True,
    "responsive_web_grok_community_note_auto_translation_is_enabled": True,
    "responsive_web_enhance_cards_enabled": False,
}
_GQL_USER_TWEETS_FIELD_TOGGLES = {
    "withPayments": True,
    "withAuxiliaryUserLabels": True,
    "withArticleRichContentState": True,
    "withArticlePlainText": True,
    "withArticleSummaryText": True,
    "withArticleVoiceOver": True,
    "withGrokAnalyze": True,
    "withDisallowedReplyControls": True,
}


class XClientError(RuntimeError):
    pass


class XRateLimitError(XClientError):
    def __init__(self, message: str, reset_at: datetime | None = None) -> None:
        super().__init__(message)
        self.reset_at = reset_at


class XServerError(XClientError):
    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class LimbusXClient:
    def __init__(self, config: AppConfig, session: Any) -> None:
        self.config = config
        self.session = session
        self._x_user_id: str | None = None
        self._fetch_lock = Lock()
        self._cached_posts: list[TwitterPost] = []
        self._cached_at: datetime | None = None
        self._rate_limited_until: datetime | None = None
        self._rate_limit_failures: int = 0
        self._server_error_failures: int = 0
        self._last_backoff_log_until: datetime | None = None

    def _has_twitter_auth(self) -> bool:
        cfg = self.config
        return bool(cfg.x_auth_token and cfg.x_ct0)

    def _twitter_api_headers(self) -> dict[str, str]:
        cfg = self.config
        return {
            "authorization": f"Bearer {_X_BEARER}",
            "x-csrf-token": cfg.x_ct0 or "",
            "cookie": f"auth_token={cfg.x_auth_token}; ct0={cfg.x_ct0}",
            "content-type": "application/json",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
            ),
            "x-twitter-active-user": "yes",
            "x-twitter-auth-type": "OAuth2Session",
            "x-twitter-client-language": "ko",
        }

    async def _fetch_user_id(self, username: str) -> str:
        if self._x_user_id:
            return self._x_user_id
        params = {
            "variables": json.dumps({
                "screen_name": username,
                "withSafetyModeUserFields": False,
            }),
            "features": json.dumps(_GQL_USER_BY_SCREEN_NAME_FEATURES),
        }
        async with self.session.get(
            _graphql_url(
                self.config.x_qid_user_by_screen_name or _GQL_USER_BY_SCREEN_NAME_QID,
                "UserByScreenName",
            ),
            headers=self._twitter_api_headers(),
            params=params,
            timeout=30,
        ) as resp:
            if resp.status >= 400:
                body = await resp.text()
                raise _build_x_http_error("UserByScreenName", resp, body)
            data = await resp.json(content_type=None)
        user = data.get("data", {}).get("user", {}).get("result", {})
        user_id = str(user.get("rest_id") or "").strip()
        if not user_id:
            raise XClientError(f"유저 ID를 찾을 수 없음: {username}")
        self._x_user_id = user_id
        LOGGER.debug("Twitter API: 유저 ID 조회 완료 %s → %s", username, user_id)
        return user_id

    async def fetch_recent_posts(self, *, limit: int = 20) -> list[TwitterPost]:
        if not self._has_twitter_auth():
            raise XClientError(
                "X_AUTH_TOKEN 또는 X_CT0가 설정되지 않아 Twitter 게시물을 가져올 수 없습니다."
            )
        async with self._fetch_lock:
            now = datetime.now(timezone.utc)
            if self._cached_posts and self._cached_at and now - self._cached_at < X_POST_CACHE_TTL:
                return self._cached_posts[:limit]
            if self._rate_limited_until and now < self._rate_limited_until:
                if self._cached_posts:
                    self._log_backoff_active()
                    return self._cached_posts[:limit]
                raise XClientError(
                    f"X API 백오프 중입니다: {self._rate_limited_until.isoformat()}"
                )

            try:
                posts = await self._fetch_via_twitter_api(limit=limit)
            except (asyncio.TimeoutError, aiohttp.ClientError) as exc:
                self._apply_backoff(_server_error_backoff_seconds(self._server_error_failures))
                self._server_error_failures += 1
                LOGGER.warning(
                    "X API 네트워크 오류 (%s). 캐시를 사용합니다. 다음 시도: %s",
                    type(exc).__name__,
                    _format_kst_datetime(self._rate_limited_until),
                )
                if self._cached_posts:
                    return self._cached_posts[:limit]
                raise XClientError(f"X API 네트워크 오류: {exc}") from exc
            except XRateLimitError as exc:
                seconds = _rate_limit_backoff_seconds(self._rate_limit_failures)
                if exc.reset_at is not None:
                    delta = (exc.reset_at - datetime.now(timezone.utc)).total_seconds()
                    if delta > 0:
                        seconds = min(
                            max(delta + 5, seconds),
                            X_RATE_LIMIT_RESET_CAP.total_seconds(),
                        )
                self._apply_backoff(seconds)
                self._rate_limit_failures += 1
                LOGGER.warning(
                    "X API 호출 제한에 걸렸습니다. 제한 해제 예상 시간: %s. 캐시를 사용합니다. 다음 시도: %s",
                    _format_kst_datetime(exc.reset_at),
                    _format_kst_datetime(self._rate_limited_until),
                )
                if self._cached_posts:
                    return self._cached_posts[:limit]
                raise
            except XServerError as exc:
                seconds = _server_error_backoff_seconds(self._server_error_failures)
                if exc.retry_after is not None and exc.retry_after > 0:
                    seconds = min(max(exc.retry_after + 1, seconds), X_SERVER_ERROR_BACKOFF_MAX.total_seconds())
                self._apply_backoff(seconds)
                self._server_error_failures += 1
                LOGGER.warning(
                    "X API 일시 서버 오류 (%s). retry_after=%s초. 캐시를 사용합니다. 다음 시도: %s",
                    exc,
                    exc.retry_after,
                    _format_kst_datetime(self._rate_limited_until),
                )
                if self._cached_posts:
                    return self._cached_posts[:limit]
                raise
            except XClientError as exc:
                if _is_rate_limit_error(exc):
                    self._apply_backoff(_rate_limit_backoff_seconds(self._rate_limit_failures))
                    self._rate_limit_failures += 1
                    LOGGER.warning(
                        "X API 호출 제한에 걸렸습니다. 캐시를 사용합니다. 다음 시도: %s",
                        _format_kst_datetime(self._rate_limited_until),
                    )
                    if self._cached_posts:
                        return self._cached_posts[:limit]
                elif _is_transient_server_error(exc):
                    self._apply_backoff(_server_error_backoff_seconds(self._server_error_failures))
                    self._server_error_failures += 1
                    LOGGER.warning(
                        "X API 일시 서버 오류 (%s). 캐시를 사용합니다. 다음 시도: %s",
                        exc,
                        _format_kst_datetime(self._rate_limited_until),
                    )
                    if self._cached_posts:
                        return self._cached_posts[:limit]
                raise

            self._cached_posts = posts
            self._cached_at = datetime.now(timezone.utc)
            self._rate_limited_until = None
            self._rate_limit_failures = 0
            self._server_error_failures = 0
            self._last_backoff_log_until = None
            return posts[:limit]

    def _apply_backoff(self, seconds: float) -> None:
        self._rate_limited_until = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        self._last_backoff_log_until = None

    def _log_backoff_active(self) -> None:
        until = self._rate_limited_until
        if until is None:
            return
        if self._last_backoff_log_until == until:
            LOGGER.debug(
                "X API 백오프 중입니다 (해제 예상: %s). 캐시를 사용합니다.",
                _format_kst_datetime(until),
            )
            return
        self._last_backoff_log_until = until
        LOGGER.warning(
            "X API 백오프 중이라 캐시된 게시물을 사용합니다. 다시 시도 가능 예상 시간: %s",
            _format_kst_datetime(until),
        )

    async def _fetch_via_twitter_api(self, *, limit: int) -> list[TwitterPost]:
        username = self.config.x_account_username
        user_id = await self._fetch_user_id(username)
        posts: list[TwitterPost] = []
        cursor: str | None = None

        for page in range(20):
            variables: dict[str, Any] = {
                "userId": user_id,
                "count": min(limit, 100),
                "includePromotedContent": True,
                "withCommunity": True,
                "withQuickPromoteEligibilityTweetFields": True,
                "withVoice": True,
                "withV2Timeline": True,
            }
            if cursor:
                variables["cursor"] = cursor

            payload = {
                "variables": variables,
                "features": _GQL_USER_TWEETS_FEATURES,
                "fieldToggles": _GQL_USER_TWEETS_FIELD_TOGGLES,
            }
            async with self.session.post(
                _graphql_url(
                    self.config.x_qid_user_tweets_and_replies
                    or _GQL_USER_TWEETS_AND_REPLIES_QID,
                    "UserTweetsAndReplies",
                ),
                headers=self._twitter_api_headers(),
                json=payload,
                timeout=30,
            ) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    raise _build_x_http_error("UserTweetsAndReplies", resp, body)
                data = await resp.json(content_type=None)

            page_posts = _extract_twitter_posts(data, username)
            _extend_unique_posts(posts, page_posts)
            LOGGER.debug("Twitter API: 페이지 %d — %d개 수집 (누적 %d개)", page + 1, len(page_posts), len(posts))

            if len(posts) >= limit:
                break

            cursor = _next_cursor_from_payload(data)
            if not cursor:
                break

        posts.sort(key=_tweet_sort_key, reverse=True)
        if not posts:
            raise XClientError("Twitter API에서 게시물을 가져오지 못했습니다.")
        return posts[:limit]

    async def _enrich_posts_with_fx_media(
        self, posts: list[TwitterPost]
    ) -> list[TwitterPost]:
        if self.session is None:
            return posts

        enriched: list[TwitterPost] = []
        for post in posts:
            if not post.raw.get("video_fallback_url"):
                enriched.append(post)
                continue

            groups = await self._fetch_fx_video_variant_groups(post)
            if not groups:
                enriched.append(post)
                continue

            raw = dict(post.raw)
            raw["video_variant_groups"] = groups
            raw["video_urls"] = [group[0] for group in groups if group]
            raw["video_fallback_url"] = raw["video_urls"][0]
            enriched.append(replace(post, raw=raw))
        return enriched

    async def _fetch_fx_video_variant_groups(
        self, post: TwitterPost
    ) -> list[list[str]]:
        tweet_id = str(post.raw.get("tweet_id") or post.post_id.removeprefix("x:"))
        url = f"https://api.fxtwitter.com/{post.author_username}/status/{tweet_id}"
        try:
            async with self.session.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 LimpiBot/1.0"},
                timeout=30,
            ) as response:
                if response.status >= 400:
                    return []
                payload = await response.json(content_type=None)
        except Exception:
            return []
        return _fx_video_variant_groups(payload)


def _graphql_url(query_id: str, operation_name: str) -> str:
    return f"https://x.com/i/api/graphql/{query_id}/{operation_name}"


def _is_rate_limit_error(exc: XClientError) -> bool:
    message = str(exc).lower()
    return " 429" in message or "rate limit" in message


def _is_transient_server_error(exc: XClientError) -> bool:
    msg = str(exc)
    return any(f" {code}" in msg for code in ("500", "502", "503", "504"))


def _rate_limit_backoff_seconds(failures: int) -> float:
    base = X_RATE_LIMIT_BACKOFF.total_seconds()
    cap = X_RATE_LIMIT_BACKOFF_MAX.total_seconds()
    return min(base * (2 ** max(0, failures)), cap)


def _server_error_backoff_seconds(failures: int) -> float:
    base = X_SERVER_ERROR_BACKOFF.total_seconds()
    cap = X_SERVER_ERROR_BACKOFF_MAX.total_seconds()
    return min(base * (2 ** max(0, failures)), cap)


def _parse_reset_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        epoch = int(float(value.strip()))
    except (TypeError, ValueError):
        return None
    if epoch <= 0:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


def _format_kst_datetime(value: datetime | None) -> str:
    if value is None:
        return "알 수 없음"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    local = value.astimezone(KST)
    return (
        f"{local.year}년 {local.month}월 {local.day}일 "
        f"{local.hour:02d}시 {local.minute:02d}분 {local.second:02d}초"
    )


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    text = value.strip()
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    delta = (when - datetime.now(timezone.utc)).total_seconds()
    return max(0.0, delta)


def _build_x_http_error(operation: str, response: Any, body: str) -> XClientError:
    message = _format_x_http_error(operation, response, body)
    status = getattr(response, "status", 0)
    headers = getattr(response, "headers", {})
    retry_after = _parse_retry_after(headers.get("retry-after"))
    if status == 429:
        reset_at = _parse_reset_at(headers.get("x-rate-limit-reset"))
        if reset_at is None and retry_after is not None:
            reset_at = datetime.now(timezone.utc) + timedelta(seconds=retry_after)
        return XRateLimitError(message, reset_at=reset_at)
    if status in (500, 502, 503, 504):
        return XServerError(message, retry_after=retry_after)
    return XClientError(message)


def _format_x_http_error(operation: str, response: Any, body: str) -> str:
    body_text = body.strip()
    body_summary = body_text[:200] if body_text else "<empty body>"
    details = [
        f"reason={response.reason or 'unknown'}",
        f"body={body_summary}",
    ]
    content_type = response.headers.get("content-type")
    retry_after = response.headers.get("retry-after")
    rate_limit_reset = response.headers.get("x-rate-limit-reset")
    rate_limit_remaining = response.headers.get("x-rate-limit-remaining")
    if content_type:
        details.append(f"content_type={content_type}")
    if retry_after:
        details.append(f"retry_after={retry_after}")
    if rate_limit_reset:
        details.append(f"x_rate_limit_reset={rate_limit_reset}")
    if rate_limit_remaining:
        details.append(f"x_rate_limit_remaining={rate_limit_remaining}")
    return f"{operation} {response.status}: " + "; ".join(details)


def _next_cursor_from_payload(payload: dict[str, Any]) -> str | None:
    for instruction in _timeline_instructions(payload):
        entries = instruction.get("entries")
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            entry_id = str(entry.get("entryId") or "")
            if not entry_id.startswith("cursor-bottom"):
                continue
            content = entry.get("content", {})
            value = content.get("value") or content.get("itemContent", {}).get("value")
            if isinstance(value, str) and value:
                return value
    return None


def _extend_unique_posts(posts: list[TwitterPost], new_posts: list[TwitterPost]) -> None:
    seen = {post.post_id for post in posts}
    for post in new_posts:
        if post.post_id in seen:
            continue
        seen.add(post.post_id)
        posts.append(post)


def _extract_twitter_posts_from_payloads(
    payloads: list[dict[str, Any]], username: str
) -> list[TwitterPost]:
    posts: list[TwitterPost] = []
    seen: set[str] = set()
    for payload in payloads:
        for post in _extract_twitter_posts(payload, username):
            if post.post_id in seen:
                continue
            seen.add(post.post_id)
            posts.append(post)
    return posts


def _extract_twitter_posts(payload: dict[str, Any], username: str) -> list[TwitterPost]:
    posts: list[TwitterPost] = []
    seen: set[str] = set()
    results = _timeline_tweet_results(payload)
    for result in results:
        tweet = _unwrap_tweet_result(result)
        if tweet is None:
            continue
        post = _tweet_to_post(tweet, username)
        if post is not None and post.post_id not in seen:
            seen.add(post.post_id)
            posts.append(post)
    return posts


def _timeline_tweet_results(payload: dict[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for instruction in _timeline_instructions(payload):
        entries = instruction.get("entries")
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            entry_id = str(entry.get("entryId") or entry.get("entry_id") or "")
            if entry_id.startswith("cursor-"):
                continue
            if _entry_is_pinned(entry):
                continue
            results.extend(_entry_tweet_results(entry))
    return results


def _timeline_instructions(payload: dict[str, Any]) -> list[dict[str, Any]]:
    timelines = []
    user = payload.get("data", {}).get("user", {}).get("result")
    if isinstance(user, dict):
        timeline = user.get("timeline_v2", {}).get("timeline")
        if isinstance(timeline, dict):
            timelines.append(timeline)
        timeline = user.get("timeline", {}).get("timeline")
        if isinstance(timeline, dict):
            timelines.append(timeline)

    instructions: list[dict[str, Any]] = []
    for timeline in timelines:
        value = timeline.get("instructions")
        if isinstance(value, list):
            instructions.extend(
                instruction
                for instruction in value
                if isinstance(instruction, dict)
                and str(instruction.get("type") or "") != "TimelinePinEntry"
            )
    return instructions


def _entry_tweet_result(entry: dict[str, Any]) -> dict[str, Any] | None:
    results = _entry_tweet_results(entry)
    return results[0] if results else None


def _entry_tweet_results(entry: dict[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    content = entry.get("content")
    if not isinstance(content, dict):
        return results

    item_contents: list[dict[str, Any]] = []
    item_content = content.get("itemContent")
    if isinstance(item_content, dict):
        item_contents.append(item_content)

    items = content.get("items")
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            item_content = item.get("item", {}).get("itemContent")
            if isinstance(item_content, dict):
                item_contents.append(item_content)

    for item_content in item_contents:
        tweet_results = item_content.get("tweet_results")
        if not isinstance(tweet_results, dict):
            continue
        result = tweet_results.get("result")
        if isinstance(result, dict):
            results.append(result)
    return results


def _entry_is_pinned(entry: dict[str, Any]) -> bool:
    for value in _walk(entry):
        if not isinstance(value, dict):
            continue
        text = value.get("text")
        if isinstance(text, str) and text.strip().lower() in {"pinned", "pinned tweet", "고정된 게시물"}:
            return True
        if value.get("type") == "TimelinePinEntry":
            return True
    return False


def _tweet_sort_key(post: TwitterPost) -> tuple[int, datetime]:
    tweet_id = str(post.raw.get("tweet_id") or post.post_id.removeprefix("x:"))
    try:
        numeric_id = int(tweet_id)
    except ValueError:
        numeric_id = 0
    created_at = post.created_at or datetime.min.replace(tzinfo=timezone.utc)
    return numeric_id, created_at


def _walk(value: Any) -> list[Any]:
    items = [value]
    if isinstance(value, dict):
        for nested in value.values():
            items.extend(_walk(nested))
    elif isinstance(value, list):
        for nested in value:
            items.extend(_walk(nested))
    return items


def _unwrap_tweet_result(result: Any) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    typename = str(result.get("__typename") or "")
    if typename in {"Tweet", "TweetWithVisibilityResults"}:
        tweet = result.get("tweet") if typename == "TweetWithVisibilityResults" else result
        return tweet if isinstance(tweet, dict) else None
    return None


def _tweet_to_post(tweet: dict[str, Any], username: str) -> TwitterPost | None:
    legacy = tweet.get("legacy")
    if not isinstance(legacy, dict):
        return None
    author_username = _tweet_author_username(tweet) or username
    if author_username.lower() != username.lower():
        return None
    tweet_id = str(tweet.get("rest_id") or legacy.get("id_str") or "").strip()
    if not tweet_id:
        return None
    text = _tweet_full_text(tweet, legacy)
    link_urls = _external_link_urls(legacy, username, tweet_id)
    text = _expand_note_tweet_urls(text, tweet)
    text = _strip_tco_links(text, legacy)
    text = _clean_tweet_text(text)
    if text in {"고정된 게시물", "Pinned", "Pinned Tweet"}:
        return None
    title = _title_from_text(text) or f"X 게시물 {tweet_id}"
    created_at = _parse_twitter_datetime(legacy.get("created_at"))
    video_variant_groups = _video_variant_groups(tweet)
    video_urls = [group[0] for group in video_variant_groups if group]
    image_urls = _photo_urls(tweet)
    if video_urls and len(image_urls) == 1:
        image_urls = []
    youtube_urls = _youtube_urls(legacy)
    raw: dict[str, Any] = {
        "source": "x",
        "language": "koreana",
        "tweet_id": tweet_id,
        "username": username,
        "created_at": legacy.get("created_at"),
        "in_reply_to_status_id_str": legacy.get("in_reply_to_status_id_str"),
        "in_reply_to_screen_name": legacy.get("in_reply_to_screen_name"),
    }
    if video_urls:
        raw["video_urls"] = video_urls
        raw["video_variant_groups"] = video_variant_groups
    if youtube_urls:
        raw["youtube_urls"] = youtube_urls
    if link_urls:
        raw["link_urls"] = link_urls
    return TwitterPost(
        post_id=f"x:{tweet_id}",
        author_username=author_username,
        url=f"https://x.com/{author_username}/status/{tweet_id}",
        text=text or f"https://x.com/{author_username}/status/{tweet_id}",
        title=title,
        created_at=created_at,
        image_urls=image_urls,
        raw=raw,
    )


def _strip_tco_links(text: str, legacy: dict[str, Any]) -> str:
    urls: set[str] = set()
    for media in legacy.get("extended_entities", {}).get("media", []) or []:
        if isinstance(media, dict) and media.get("url"):
            urls.add(str(media["url"]))
    for media in legacy.get("entities", {}).get("media", []) or []:
        if isinstance(media, dict) and media.get("url"):
            urls.add(str(media["url"]))
    for url_entity in legacy.get("entities", {}).get("urls", []) or []:
        if isinstance(url_entity, dict) and url_entity.get("url"):
            urls.add(str(url_entity["url"]))
    for url in urls:
        text = text.replace(url, "")
    return text


def _tweet_full_text(tweet: dict[str, Any], legacy: dict[str, Any]) -> str:
    note_result = (
        tweet.get("note_tweet", {})
        .get("note_tweet_results", {})
        .get("result")
    )
    if isinstance(note_result, dict):
        text = note_result.get("text")
        if isinstance(text, str) and text.strip():
            return text
    return str(legacy.get("full_text") or legacy.get("text") or "")


def _expand_note_tweet_urls(text: str, tweet: dict[str, Any]) -> str:
    note_result = (
        tweet.get("note_tweet", {})
        .get("note_tweet_results", {})
        .get("result")
    )
    if not isinstance(note_result, dict):
        return text
    urls = note_result.get("entity_set", {}).get("urls")
    if not isinstance(urls, list):
        return text
    for url_entity in urls:
        if not isinstance(url_entity, dict):
            continue
        short_url = str(url_entity.get("url") or "")
        expanded_url = str(url_entity.get("expanded_url") or "")
        if short_url and expanded_url:
            text = text.replace(short_url, expanded_url)
    return text


def _clean_tweet_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t　]+", " ", line).strip() for line in text.split("\n")]
    cleaned = "\n".join(lines).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned


def _title_from_text(text: str) -> str:
    for line in text.splitlines():
        cleaned = line.strip()
        if cleaned:
            return cleaned[:80]
    return ""


def _parse_twitter_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _tweet_author_username(tweet: dict[str, Any]) -> str | None:
    user_result = tweet.get("core", {}).get("user_results", {}).get("result")
    if not isinstance(user_result, dict):
        return None
    legacy = user_result.get("legacy")
    if not isinstance(legacy, dict):
        return None
    screen_name = str(legacy.get("screen_name") or "").strip()
    return screen_name or None


def _photo_urls(tweet: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for media in _iter_media_entities(tweet):
        media_type = str(media.get("type") or "").lower()
        if (
            media_type == "photo"
            and not media.get("video_info")
            and media.get("media_url_https")
        ):
            url = str(media["media_url_https"])
            if _is_video_thumbnail_url(url):
                continue
            urls.append(_highest_quality_photo_url(url))
    return list(dict.fromkeys(urls))


def _highest_quality_photo_url(url: str) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["name"] = "large"
    if parsed.netloc.endswith("twimg.com") and "format" not in query:
        suffix = parsed.path.rsplit(".", 1)
        if len(suffix) == 2 and suffix[1]:
            query["format"] = suffix[1].lower()
    return urlunparse(parsed._replace(query=urlencode(query)))


def _is_video_thumbnail_url(url: str) -> bool:
    lowered = url.lower()
    return any(fragment in lowered for fragment in _VIDEO_THUMBNAIL_URL_FRAGMENTS)


def _fx_video_variant_groups(payload: dict[str, Any]) -> list[list[str]]:
    tweet = payload.get("tweet")
    if not isinstance(tweet, dict):
        return []

    media_root = tweet.get("media")
    if not isinstance(media_root, dict):
        return []

    media_items: list[dict[str, Any]] = []
    for key in ("all", "videos"):
        value = media_root.get(key)
        if isinstance(value, list):
            media_items.extend(item for item in value if isinstance(item, dict))

    groups: list[list[str]] = []
    for media in media_items:
        variants: list[dict[str, Any]] = []
        for key in ("variants", "formats"):
            value = media.get(key)
            if isinstance(value, list):
                variants.extend(item for item in value if isinstance(item, dict))

        mp4_variants = [
            variant
            for variant in variants
            if _fx_variant_url(variant)
            and (
                variant.get("content_type") == "video/mp4"
                or variant.get("container") == "mp4"
                or ".mp4" in str(_fx_variant_url(variant)).lower()
            )
        ]
        mp4_variants.sort(
            key=lambda variant: int(variant.get("bitrate") or 0),
            reverse=True,
        )

        group = list(dict.fromkeys(str(_fx_variant_url(variant)) for variant in mp4_variants))
        direct_url = str(media.get("url") or "")
        if not group and ".mp4" in direct_url.lower():
            group = [direct_url]
        if group:
            groups.append(group)

    return _dedupe_video_variant_groups(groups)


def _fx_variant_url(variant: dict[str, Any]) -> str | None:
    url = variant.get("url")
    if not url:
        url = variant.get("href")
    return str(url) if url else None


def _video_variant_groups(tweet: dict[str, Any]) -> list[list[str]]:
    groups: list[list[str]] = []
    for media in _iter_media_entities(tweet):
        video_info = media.get("video_info")
        if not isinstance(video_info, dict):
            continue
        variants = video_info.get("variants") or []
        mp4_variants = [
            v
            for v in variants
            if isinstance(v, dict) and v.get("content_type") == "video/mp4" and v.get("url")
        ]
        if not mp4_variants:
            continue
        mp4_variants.sort(key=lambda v: int(v.get("bitrate") or 0), reverse=True)
        group = list(dict.fromkeys(str(variant["url"]) for variant in mp4_variants))
        if group:
            groups.append(group)
    return _dedupe_video_variant_groups(groups)


def _video_urls(tweet: dict[str, Any]) -> list[str]:
    return [group[0] for group in _video_variant_groups(tweet) if group]


def _dedupe_video_variant_groups(groups: list[list[str]]) -> list[list[str]]:
    deduped: list[list[str]] = []
    seen_first_urls: set[str] = set()
    for group in groups:
        if not group or group[0] in seen_first_urls:
            continue
        seen_first_urls.add(group[0])
        deduped.append(group)
    return deduped


def _iter_media_entities(tweet: dict[str, Any]) -> list[dict[str, Any]]:
    import json as _json
    media: list[dict[str, Any]] = []
    legacy = tweet.get("legacy")
    if isinstance(legacy, dict):
        media.extend(_media_list(legacy.get("extended_entities", {}).get("media")))
        media.extend(_media_list(legacy.get("entities", {}).get("media")))

    card = tweet.get("card")
    if isinstance(card, dict):
        media.extend(_iter_card_media_entities(card))

    media.extend(
        item
        for item in _walk(tweet)
        if isinstance(item, dict) and isinstance(item.get("video_info"), dict)
    )

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in media:
        key = str(
            item.get("media_key")
            or item.get("id_str")
            or item.get("media_url_https")
            or item.get("url")
            or id(item)
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _media_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _iter_card_media_entities(card: dict[str, Any]) -> list[dict[str, Any]]:
    media: list[dict[str, Any]] = []
    for binding in card.get("legacy", {}).get("binding_values", []) or []:
        if not isinstance(binding, dict):
            continue
        value = binding.get("value")
        if not isinstance(value, dict):
            continue
        if isinstance(value.get("image_value"), dict):
            media.append(value["image_value"])
        string_value = value.get("string_value")
        if isinstance(string_value, str) and string_value.strip().startswith("{"):
            try:
                decoded = json.loads(string_value)
            except json.JSONDecodeError:
                continue
            media.extend(
                item
                for item in _walk(decoded)
                if isinstance(item, dict)
                and (
                    isinstance(item.get("video_info"), dict)
                    or item.get("media_url_https")
                    or item.get("media_url")
                )
            )
    return media


def _youtube_urls(legacy: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for url_entity in legacy.get("entities", {}).get("urls", []) or []:
        if not isinstance(url_entity, dict):
            continue
        expanded = str(url_entity.get("expanded_url") or "")
        if "youtube.com/watch" in expanded or "youtu.be/" in expanded:
            urls.append(expanded)
    return list(dict.fromkeys(urls))


def _external_link_urls(
    legacy: dict[str, Any],
    username: str,
    tweet_id: str,
) -> list[str]:
    urls: list[str] = []
    for url_entity in legacy.get("entities", {}).get("urls", []) or []:
        if not isinstance(url_entity, dict):
            continue
        expanded = str(url_entity.get("expanded_url") or url_entity.get("url") or "").strip()
        if not expanded or _is_tweet_self_url(expanded, username, tweet_id):
            continue
        urls.append(expanded)
    return list(dict.fromkeys(urls))


def _is_tweet_self_url(url: str, username: str, tweet_id: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host not in {"x.com", "twitter.com", "mobile.twitter.com"}:
        return False
    parts = [part for part in parsed.path.split("/") if part]
    return (
        len(parts) >= 3
        and parts[0].lower() == username.lower()
        and parts[1] == "status"
        and parts[2] == tweet_id
    )
