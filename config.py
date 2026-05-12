from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


SUPPORTED_STEAM_LANGUAGES = {"koreana", "english", "japanese"}
SUPPORTED_COMMAND_SYNC_MODES = {"global", "guild"}
WEEKDAY_ALIASES = {
    "mon": 0,
    "monday": 0,
    "월": 0,
    "월요일": 0,
    "tue": 1,
    "tuesday": 1,
    "화": 1,
    "화요일": 1,
    "wed": 2,
    "wednesday": 2,
    "수": 2,
    "수요일": 2,
    "thu": 3,
    "thursday": 3,
    "목": 3,
    "목요일": 3,
    "fri": 4,
    "friday": 4,
    "금": 4,
    "금요일": 4,
    "sat": 5,
    "saturday": 5,
    "토": 5,
    "토요일": 5,
    "sun": 6,
    "sunday": 6,
    "일": 6,
    "일요일": 6,
}


def _load_dotenv_if_available() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    load_dotenv()


def _get_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default

    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _get_int(name: str, default: int, minimum: int | None = None) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default

    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc

    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be at least {minimum}.")

    return value


def _get_optional_int(name: str) -> int | None:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return None

    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc


def _get_steam_language(name: str, default: str) -> str:
    value = os.getenv(name, default).strip().lower() or default
    if value not in SUPPORTED_STEAM_LANGUAGES:
        supported = ", ".join(sorted(SUPPORTED_STEAM_LANGUAGES))
        raise ValueError(f"{name} must be one of: {supported}.")
    return value


def _get_command_sync_mode(command_guild_id: int | None) -> str:
    default = "guild" if command_guild_id else "global"
    value = os.getenv("COMMAND_SYNC_MODE", default).strip().lower() or default
    if value not in SUPPORTED_COMMAND_SYNC_MODES:
        supported = ", ".join(sorted(SUPPORTED_COMMAND_SYNC_MODES))
        raise ValueError(f"COMMAND_SYNC_MODE must be one of: {supported}.")
    return value


def _get_weekdays(name: str, default: tuple[int, ...]) -> tuple[int, ...]:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default

    days: list[int] = []
    for part in raw.split(","):
        key = part.strip().lower()
        if not key:
            continue
        if key.isdigit():
            day = int(key)
            if day < 0 or day > 6:
                raise ValueError(f"{name} weekday numbers must be 0-6.")
        else:
            if key not in WEEKDAY_ALIASES:
                raise ValueError(f"Unknown weekday in {name}: {part!r}.")
            day = WEEKDAY_ALIASES[key]
        if day not in days:
            days.append(day)

    return tuple(days)


@dataclass(frozen=True)
class AppConfig:
    discord_token: str
    database_path: Path
    steam_app_id: int
    steam_language: str
    steam_country: str
    steam_news_url: str | None
    poll_interval_seconds: int
    max_posts_per_poll: int
    high_frequency_poll_interval_seconds: int
    high_frequency_weekdays: tuple[int, ...]
    high_frequency_start_hour: int
    high_frequency_end_hour: int
    command_guild_id: int | None
    command_sync_mode: str
    announce_existing_on_first_run: bool

    @classmethod
    def from_env(cls) -> "AppConfig":
        _load_dotenv_if_available()

        discord_token = os.getenv("DISCORD_TOKEN", "").strip()
        if not discord_token:
            raise RuntimeError("DISCORD_TOKEN is required.")

        command_guild_id = _get_optional_int("COMMAND_GUILD_ID")
        return cls(
            discord_token=discord_token,
            database_path=Path(os.getenv("DATABASE_PATH", "limpi.sqlite3")),
            steam_app_id=_get_int("STEAM_APP_ID", 1973530, minimum=1),
            steam_language=_get_steam_language("STEAM_LANGUAGE", "koreana"),
            steam_country=os.getenv("STEAM_COUNTRY", "KR").strip() or "KR",
            steam_news_url=os.getenv("STEAM_NEWS_URL") or None,
            poll_interval_seconds=_get_int("POLL_INTERVAL_SECONDS", 300, minimum=60),
            max_posts_per_poll=_get_int("MAX_POSTS_PER_POLL", 10, minimum=5),
            high_frequency_poll_interval_seconds=_get_int(
                "HIGH_FREQUENCY_POLL_INTERVAL_SECONDS", 60, minimum=60
            ),
            high_frequency_weekdays=_get_weekdays(
                "HIGH_FREQUENCY_POLL_DAYS", (0, 1, 2, 3, 4, 5, 6)
            ),
            high_frequency_start_hour=_get_int("HIGH_FREQUENCY_START_HOUR", 18, minimum=0),
            high_frequency_end_hour=_get_int("HIGH_FREQUENCY_END_HOUR", 20, minimum=1),
            command_guild_id=command_guild_id,
            command_sync_mode=_get_command_sync_mode(command_guild_id),
            announce_existing_on_first_run=_get_bool("ANNOUNCE_EXISTING_ON_FIRST_RUN", False),
        )
