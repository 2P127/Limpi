from __future__ import annotations

import asyncio
import html
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp


PROJECT_MOON_YOUTUBE_STREAMS_URL = "https://www.youtube.com/@ProjectMoonOfficial/streams"
YOUTUBE_LIVE_CACHE_SECONDS = 30
YOUTUBE_STREAM_CANDIDATE_LIMIT = 3
YOUTUBE_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class YoutubeLive:
    video_id: str
    title: str
    url: str
    thumbnail_url: str | None
    start_time: datetime | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class YoutubeStream:
    video_id: str
    title: str
    url: str
    thumbnail_url: str | None


class YoutubeClient:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        streams_url: str = PROJECT_MOON_YOUTUBE_STREAMS_URL,
    ) -> None:
        self.session = session
        self.streams_url = streams_url
        self._live_cache: YoutubeLive | None = None
        self._live_cache_at: datetime | None = None

    async def fetch_latest_live_url(self) -> str:
        live = await self.fetch_live()
        if live is not None:
            return live.url
        return self.streams_url

    async def fetch_live(self) -> YoutubeLive | None:
        now = datetime.now(timezone.utc)
        if (
            self._live_cache_at is not None
            and now - self._live_cache_at < timedelta(seconds=YOUTUBE_LIVE_CACHE_SECONDS)
        ):
            return self._live_cache

        candidates = await self._fetch_stream_candidates(limit=YOUTUBE_STREAM_CANDIDATE_LIMIT)
        live = await self._live_from_candidates(candidates)
        self._live_cache = live
        self._live_cache_at = now
        return live

    async def fetch_latest_stream(self) -> YoutubeStream | None:
        candidates = await self._fetch_stream_candidates(limit=1)
        if not candidates:
            return None
        candidate = candidates[0]
        return YoutubeStream(
            video_id=str(candidate["video_id"]),
            title=str(candidate["title"] or "ProjectMoon Official LIVE"),
            url=str(candidate["url"]),
            thumbnail_url=candidate["thumbnail_url"],
        )

    async def _live_from_candidates(
        self,
        candidates: list[dict[str, Any]],
    ) -> YoutubeLive | None:
        if not candidates:
            return None
        players = await asyncio.gather(
            *[self._fetch_player_response(str(c["video_id"])) for c in candidates]
        )
        for candidate, player in zip(candidates, players):
            if not _is_youtube_live_now(player):
                continue
            details = player.get("videoDetails") if isinstance(player.get("videoDetails"), dict) else {}
            microformat = (
                player.get("microformat", {}).get("playerMicroformatRenderer", {})
                if isinstance(player.get("microformat"), dict)
                else {}
            )
            live_details = (
                microformat.get("liveBroadcastDetails", {})
                if isinstance(microformat.get("liveBroadcastDetails"), dict)
                else {}
            )
            return YoutubeLive(
                video_id=str(candidate["video_id"]),
                title=str(details.get("title") or candidate["title"] or "ProjectMoon Official LIVE"),
                url=str(candidate["url"]),
                thumbnail_url=_youtube_thumbnail_url(details) or candidate["thumbnail_url"],
                start_time=_parse_youtube_timestamp(live_details.get("startTimestamp")),
                raw=player,
            )
        return None

    async def _fetch_stream_candidates(self, *, limit: int) -> list[dict[str, Any]]:
        async with self.session.get(
            self.streams_url,
            headers={"User-Agent": YOUTUBE_USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as response:
            if response.status != 200:
                return []
            text = await response.text()

        candidates: list[dict[str, Any]] = []
        seen: set[str] = set()
        data = _extract_youtube_initial_data(text)
        for item in _walk_youtube_lockups(data):
            video_id = str(item.get("contentId") or "").strip()
            if not video_id or video_id in seen:
                continue
            seen.add(video_id)
            candidates.append(
                {
                    "video_id": video_id,
                    "title": _youtube_lockup_title(item),
                    "url": f"https://www.youtube.com/watch?v={video_id}",
                    "thumbnail_url": _youtube_lockup_thumbnail_url(item),
                }
            )
            if len(candidates) >= limit:
                return candidates

        for match in re.finditer(r'"url":"(/watch\?v=[^"]+)"', text):
            path = html.unescape(match.group(1).replace("\\u0026", "&"))
            video_id = _youtube_video_id(path)
            if not video_id or video_id in seen:
                continue
            seen.add(video_id)
            candidates.append(
                {
                    "video_id": video_id,
                    "title": "ProjectMoon Official LIVE",
                    "url": f"https://www.youtube.com/watch?v={video_id}",
                    "thumbnail_url": None,
                }
            )
            if len(candidates) >= limit:
                break
        return candidates

    async def _fetch_player_response(self, video_id: str) -> dict[str, Any]:
        url = f"https://www.youtube.com/watch?v={video_id}"
        async with self.session.get(
            url,
            headers={"User-Agent": YOUTUBE_USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as response:
            if response.status != 200:
                return {}
            text = await response.text()
        match = re.search(r"ytInitialPlayerResponse\s*=\s*(\{.*?\});", text)
        if not match:
            return {}
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}


def _extract_youtube_initial_data(text: str) -> dict[str, Any]:
    match = re.search(r"var ytInitialData = (\{.*?\});</script>", text)
    if match is None:
        match = re.search(r"ytInitialData\s*=\s*(\{.*?\});", text)
    if match is None:
        return {}
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _walk_youtube_lockups(value: object) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def walk(item: object) -> None:
        if isinstance(item, dict):
            lockup = item.get("lockupViewModel")
            if isinstance(lockup, dict) and lockup.get("contentType") == "LOCKUP_CONTENT_TYPE_VIDEO":
                found.append(lockup)
            for child in item.values():
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)
    return found


def _youtube_lockup_title(item: dict[str, Any]) -> str:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    lockup = (
        metadata.get("lockupMetadataViewModel", {})
        if isinstance(metadata.get("lockupMetadataViewModel"), dict)
        else {}
    )
    title = _youtube_text(lockup.get("title"))
    return title or "ProjectMoon Official LIVE"


def _youtube_lockup_thumbnail_url(item: dict[str, Any]) -> str | None:
    content_image = item.get("contentImage") if isinstance(item.get("contentImage"), dict) else {}
    thumbnail = (
        content_image.get("thumbnailViewModel", {})
        if isinstance(content_image.get("thumbnailViewModel"), dict)
        else {}
    )
    image = thumbnail.get("image", {}) if isinstance(thumbnail.get("image"), dict) else {}
    sources = image.get("sources") if isinstance(image.get("sources"), list) else []
    urls = [source for source in sources if isinstance(source, dict) and source.get("url")]
    if not urls:
        return None
    best = max(urls, key=lambda source: int(source.get("width") or 0))
    return str(best["url"])


def _youtube_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if value.get("content") is not None:
            return _youtube_text(value["content"])
        if value.get("simpleText") is not None:
            return str(value["simpleText"])
        if value.get("text") is not None:
            return str(value["text"])
        if isinstance(value.get("runs"), list):
            return "".join(_youtube_text(run) for run in value["runs"])
    if isinstance(value, list):
        return "".join(_youtube_text(item) for item in value)
    return ""


def _is_youtube_live_now(player: dict[str, Any]) -> bool:
    if not isinstance(player, dict):
        return False
    details = player.get("videoDetails") if isinstance(player.get("videoDetails"), dict) else {}
    if details.get("isLive") is True:
        return True

    microformat = (
        player.get("microformat", {}).get("playerMicroformatRenderer", {})
        if isinstance(player.get("microformat"), dict)
        else {}
    )
    live_details = (
        microformat.get("liveBroadcastDetails", {})
        if isinstance(microformat.get("liveBroadcastDetails"), dict)
        else {}
    )
    return live_details.get("isLiveNow") is True


def _youtube_thumbnail_url(details: dict[str, Any]) -> str | None:
    thumbnail = details.get("thumbnail") if isinstance(details.get("thumbnail"), dict) else {}
    thumbnails = thumbnail.get("thumbnails") if isinstance(thumbnail.get("thumbnails"), list) else []
    urls = [item for item in thumbnails if isinstance(item, dict) and item.get("url")]
    if not urls:
        return None
    best = max(urls, key=lambda item: int(item.get("width") or 0))
    return str(best["url"])


def _parse_youtube_timestamp(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _youtube_video_id(path: str) -> str | None:
    match = re.search(r"[?&]v=([A-Za-z0-9_-]{6,})", path)
    return match.group(1) if match else None
