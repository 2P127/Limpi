from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import aiohttp


PROJECT_MOON_CHZZK_CHANNEL_ID = "88ef610910ea642c198e0b05bca9967f"
PROJECT_MOON_CHZZK_URL = f"https://chzzk.naver.com/{PROJECT_MOON_CHZZK_CHANNEL_ID}"
PROJECT_MOON_CHZZK_LIVE_URL = f"https://chzzk.naver.com/live/{PROJECT_MOON_CHZZK_CHANNEL_ID}"
PROJECT_MOON_YOUTUBE_STREAMS_URL = "https://www.youtube.com/@ProjectMoonOfficial/streams"
CHZZK_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class ChzzkLive:
    live_id: str
    title: str
    category: str | None
    image_url: str | None
    open_date: datetime | None
    channel_name: str
    channel_image_url: str | None
    raw: dict[str, Any]


class ChzzkClient:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        channel_id: str = PROJECT_MOON_CHZZK_CHANNEL_ID,
    ) -> None:
        self.session = session
        self.channel_id = channel_id

    async def fetch_live(self) -> ChzzkLive | None:
        content = await self.fetch_live_detail()
        if not isinstance(content, dict):
            return None
        if not _is_live_content_active(content):
            return None

        live_id = content.get("liveId")
        if live_id is None:
            return None

        channel = content.get("channel") if isinstance(content.get("channel"), dict) else {}
        return ChzzkLive(
            live_id=str(live_id),
            title=str(content.get("liveTitle") or "ProjectMoon Official LIVE"),
            category=str(content["liveCategoryValue"]) if content.get("liveCategoryValue") else None,
            image_url=_normalize_image_url(content.get("liveImageUrl")),
            open_date=_parse_open_date(content.get("openDate")),
            channel_name=str(channel.get("channelName") or "ProjectMoon Official"),
            channel_image_url=(
                str(channel["channelImageUrl"]) if channel.get("channelImageUrl") else None
            ),
            raw=content,
        )

    async def fetch_live_detail(self) -> dict[str, Any] | None:
        if "/" in self.channel_id:
            return None

        url = f"https://api.chzzk.naver.com/service/v2/channels/{self.channel_id}/live-detail"
        async with self.session.get(
            url,
            headers={"User-Agent": CHZZK_USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=5),
        ) as response:
            if response.status != 200:
                return None
            payload = await response.json(content_type=None)

        content = payload.get("content")
        return content if isinstance(content, dict) else None

    async def fetch_youtube_latest_live_url(self) -> str:
        async with self.session.get(
            PROJECT_MOON_YOUTUBE_STREAMS_URL,
            headers={"User-Agent": CHZZK_USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as response:
            if response.status != 200:
                return PROJECT_MOON_YOUTUBE_STREAMS_URL
            text = await response.text()

        for match in re.finditer(r'"url":"(/watch\?v=[^"]+)"', text):
            path = html.unescape(match.group(1).replace("\\u0026", "&"))
            video_id = _youtube_video_id(path)
            if video_id:
                return f"https://www.youtube.com/watch?v={video_id}"
        return PROJECT_MOON_YOUTUBE_STREAMS_URL


def _normalize_image_url(value: object) -> str | None:
    if not value:
        return None
    url = str(value)
    return url.replace("_{type}", "_1080")


def _parse_open_date(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _is_live_content_active(content: dict[str, Any]) -> bool:
    status = str(content.get("status") or "").upper()
    if status and status != "OPEN":
        return False

    playback_json = content.get("livePlaybackJson")
    if playback_json:
        try:
            playback = json.loads(str(playback_json))
        except json.JSONDecodeError:
            playback = {}
        live = playback.get("live") if isinstance(playback, dict) else None
        playback_status = str(live.get("status") or "").upper() if isinstance(live, dict) else ""
        if playback_status and playback_status != "OPEN":
            return False

    return True


def _youtube_video_id(path: str) -> str | None:
    match = re.search(r"[?&]v=([A-Za-z0-9_-]{6,})", path)
    return match.group(1) if match else None
