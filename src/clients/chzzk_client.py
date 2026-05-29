from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import aiohttp


PROJECT_MOON_CHZZK_CHANNEL_ID = "88ef610910ea642c198e0b05bca9967f"
PROJECT_MOON_CHZZK_URL = f"https://chzzk.naver.com/{PROJECT_MOON_CHZZK_CHANNEL_ID}"
PROJECT_MOON_CHZZK_LIVE_URL = f"https://chzzk.naver.com/live/{PROJECT_MOON_CHZZK_CHANNEL_ID}"
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


@dataclass(frozen=True)
class ChzzkBroadcast:
    live_id: str
    title: str
    category: str | None
    image_url: str | None
    open_date: datetime | None
    close_date: datetime | None
    channel_name: str
    channel_image_url: str | None


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

    async def fetch_latest_broadcast(self) -> ChzzkBroadcast | None:
        content = await self.fetch_live_detail()
        if not isinstance(content, dict):
            return None

        live_id = content.get("liveId")
        if live_id is None:
            return None

        channel = content.get("channel") if isinstance(content.get("channel"), dict) else {}
        return ChzzkBroadcast(
            live_id=str(live_id),
            title=str(content.get("liveTitle") or "ProjectMoon Official LIVE"),
            category=str(content["liveCategoryValue"]) if content.get("liveCategoryValue") else None,
            image_url=_normalize_image_url(content.get("liveImageUrl")),
            open_date=_parse_open_date(content.get("openDate")),
            close_date=_parse_open_date(content.get("closeDate")),
            channel_name=str(channel.get("channelName") or "ProjectMoon Official"),
            channel_image_url=(
                str(channel["channelImageUrl"]) if channel.get("channelImageUrl") else None
            ),
        )

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
    active_statuses = {"OPEN", "STARTED"}
    closed_statuses = {"CLOSE", "CLOSED", "ENDED", "END"}

    playback_status = ""
    has_playback_media = False
    playback_json = content.get("livePlaybackJson")
    if playback_json:
        try:
            playback = json.loads(str(playback_json))
        except json.JSONDecodeError:
            playback = {}
        live = playback.get("live") if isinstance(playback, dict) else None
        playback_status = str(live.get("status") or "").upper() if isinstance(live, dict) else ""
        has_playback_media = bool(playback.get("media")) if isinstance(playback, dict) else False

    status = str(content.get("status") or "").upper()
    if status in closed_statuses:
        return playback_status in active_statuses and has_playback_media
    if status and status not in active_statuses:
        return False
    if playback_status in closed_statuses:
        return False
    if playback_status and playback_status not in active_statuses:
        return False

    return True
