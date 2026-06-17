from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BOT_VERSION = "0.3.3"

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

    import sys as _sys
    if getattr(_sys, "frozen", False):
        exe_dir = os.path.dirname(_sys.executable)
        env_path = os.path.join(exe_dir, ".env")
        if not os.path.exists(env_path):
            env_path = os.path.join(getattr(_sys, "_MEIPASS", exe_dir), ".env")
        load_dotenv(dotenv_path=env_path)
    else:
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


def _get_int_or_default(name: str, default: int, minimum: int | None = None) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default

    try:
        value = int(raw)
    except ValueError:
        return default

    if minimum is not None and value < minimum:
        return minimum

    return value


def _get_hour(name: str, default: int, *, maximum: int) -> int:
    return min(maximum, _get_int_or_default(name, default, minimum=0))


def _get_optional_int(name: str) -> int | None:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return None

    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc


def _get_optional_int_or_none(name: str) -> int | None:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return None

    try:
        return int(raw)
    except ValueError:
        return None


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


def _parse_clock_to_minutes(value: str) -> int | None:
    value = value.strip()
    if not value:
        return None
    parts = value.split(":")
    try:
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return None
    if not (0 <= hour <= 24 and 0 <= minute < 60):
        return None
    total = hour * 60 + minute
    return total if total <= 24 * 60 else None


def _get_twitter_windows(
    name: str, default: tuple[tuple[int, int], ...]
) -> tuple[tuple[int, int], ...]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    windows: list[tuple[int, int]] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk or "-" not in chunk:
            continue
        start_s, end_s = chunk.split("-", 1)
        start = _parse_clock_to_minutes(start_s)
        end = _parse_clock_to_minutes(end_s)
        if start is None or end is None:
            continue
        start %= 24 * 60
        end %= 24 * 60
        windows.append((start, end))
    return tuple(windows) if windows else default


def _get_float_or_default(name: str, default: float, *, minimum: float | None = None) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if minimum is not None and value < minimum:
        return minimum
    return value


def _get_weekdays_or_default(name: str, default: tuple[int, ...]) -> tuple[int, ...]:
    try:
        return _get_weekdays(name, default)
    except ValueError:
        return default


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
    x_account_username: str
    x_auth_token: str | None
    x_ct0: str | None
    x_qid_user_by_screen_name: str | None
    x_qid_user_tweets_and_replies: str | None
    twitter_tracking_windows_kst: tuple[tuple[int, int], ...]
    twitter_poll_interval_seconds: int
    twitter_min_poll_interval_seconds: int
    twitter_rate_limit_backoff_seconds: int
    twitter_429_backoff_multiplier: float
    twitter_max_backoff_seconds: int
    twitter_announce_max_age_seconds: int
    x_news_probe: bool

    @classmethod
    def from_env(cls, *, test: bool = False) -> "AppConfig":
        _load_dotenv_if_available()

        if test:
            discord_token = os.getenv("DISCORD_TOKEN_TEST", "").strip()
            if not discord_token:
                raise RuntimeError("DISCORD_TOKEN_TEST is required for test mode.")
        else:
            discord_token = os.getenv("DISCORD_TOKEN", "").strip()
            if not discord_token:
                raise RuntimeError("DISCORD_TOKEN is required.")

        command_guild_id = _get_optional_int_or_none("COMMAND_GUILD_ID")
        x_account_username = os.getenv("X_ACCOUNT_USERNAME", "LimbusCompany_B").strip() or "LimbusCompany_B"
        x_auth_token = os.getenv("X_AUTH_TOKEN", "").strip() or None
        x_ct0 = os.getenv("X_CT0", "").strip() or None
        x_qid_user_by_screen_name = os.getenv("X_QID_USER_BY_SCREEN_NAME", "").strip() or None
        x_qid_user_tweets_and_replies = (
            os.getenv("X_QID_USER_TWEETS_AND_REPLIES", "").strip() or None
        )
        return cls(
            discord_token=discord_token,
            database_path=Path(os.getenv("DATABASE_PATH", "limpi.sqlite3")),
            steam_app_id=_get_int("STEAM_APP_ID", 1973530, minimum=1),
            steam_language=_get_steam_language("STEAM_LANGUAGE", "koreana"),
            steam_country=os.getenv("STEAM_COUNTRY", "KR").strip() or "KR",
            steam_news_url=os.getenv("STEAM_NEWS_URL") or None,
            poll_interval_seconds=_get_int_or_default("POLL_INTERVAL_SECONDS", 30, minimum=1),
            max_posts_per_poll=_get_int_or_default("MAX_POSTS_PER_POLL", 10, minimum=1),
            high_frequency_poll_interval_seconds=_get_int_or_default(
                "HIGH_FREQUENCY_POLL_INTERVAL_SECONDS", 10, minimum=1
            ),
            high_frequency_weekdays=_get_weekdays_or_default(
                "HIGH_FREQUENCY_POLL_DAYS", (0, 1, 2, 3, 4, 5, 6)
            ),
            high_frequency_start_hour=_get_hour("HIGH_FREQUENCY_START_HOUR", 0, maximum=23),
            high_frequency_end_hour=_get_hour("HIGH_FREQUENCY_END_HOUR", 24, maximum=24),
            command_guild_id=command_guild_id,
            command_sync_mode=_get_command_sync_mode(command_guild_id),
            announce_existing_on_first_run=_get_bool("ANNOUNCE_EXISTING_ON_FIRST_RUN", False),
            x_account_username=x_account_username,
            x_auth_token=x_auth_token,
            x_ct0=x_ct0,
            x_qid_user_by_screen_name=x_qid_user_by_screen_name,
            x_qid_user_tweets_and_replies=x_qid_user_tweets_and_replies,
            twitter_tracking_windows_kst=_get_twitter_windows(
                "TWITTER_TRACKING_WINDOWS_KST",
                ((0, 60), (10 * 60, 16 * 60), (17 * 60, 60)),
            ),
            twitter_poll_interval_seconds=_get_int_or_default(
                "TWITTER_POLL_INTERVAL_SECONDS", 30, minimum=1
            ),
            twitter_min_poll_interval_seconds=_get_int_or_default(
                "TWITTER_MIN_POLL_INTERVAL_SECONDS", 20, minimum=1
            ),
            twitter_rate_limit_backoff_seconds=_get_int_or_default(
                "TWITTER_RATE_LIMIT_BACKOFF_SECONDS", 60, minimum=1
            ),
            twitter_429_backoff_multiplier=_get_float_or_default(
                "TWITTER_429_BACKOFF_MULTIPLIER", 2.0, minimum=1.0
            ),
            twitter_max_backoff_seconds=_get_int_or_default(
                "TWITTER_MAX_BACKOFF_SECONDS", 600, minimum=1
            ),
            twitter_announce_max_age_seconds=_get_int_or_default(
                "TWITTER_ANNOUNCE_MAX_AGE_SECONDS", 24 * 60 * 60, minimum=0
            ),
            x_news_probe=_get_bool("X_NEWS_PROBE", False),
        )
