from __future__ import annotations

from datetime import timedelta, timezone
from pathlib import Path

from discord import app_commands

POST_FORMAT_RICH = "rich"
KST = timezone(timedelta(hours=9))
NEWS_POST_LIMIT = 30
TWITTER_POST_LIMIT = 30
NEWS_SELECT_PAGE_SIZE = 25
NEWS_SELECT_POST_LIMIT = 250
NEWS_POLL_TICK_SECONDS = 10
TWITTER_POLL_TICK_SECONDS = 5
TWITTER_PRIORITY_POLL_TIMES_KST = ((18, 0), (18, 15), (18, 30))
TWITTER_PRIORITY_POLL_INTERVAL_SECONDS = 5
TWITTER_PRIORITY_POLL_WINDOW_SECONDS = 2 * 60
TWITTER_PRIORITY_POLL_PREP_SECONDS = 3 * 60
CHZZK_POLL_INTERVAL_SECONDS = 60
YOUTUBE_UPLOAD_POLL_INTERVAL_SECONDS = 60
HAMPANG_X_USERNAME = "Ham_PangPang"
HAMPANG_X_URL = "https://x.com/Ham_PangPang"
HAMPANG_YOUTUBE_TITLE_MARKER = "hamhampangpang"
HAMPANG_AUTO_POLL_INTERVAL_SECONDS = 15 * 60
HAMPANG_SOURCE_BOTH = "both"
HAMPANG_SOURCE_X = "x"
HAMPANG_SOURCE_YOUTUBE = "youtube"
CHZZK_LIVE_ANNOUNCE_MAX_AGE = timedelta(minutes=10)
CHZZK_LIVE_END_ANNOUNCE_MAX_AGE = timedelta(minutes=10)
YOUTUBE_LIVE_ANNOUNCE_MAX_AGE = timedelta(minutes=10)
NEWS_TARGET_SEND_CONCURRENCY = 12
NEWS_ROLE_MENTION_COOLDOWN_SECONDS = 2 * 60 + 30
TWITTER_STEAM_DUPLICATE_WINDOW_SECONDS = 30 * 60
TWITTER_STEAM_PREFERENCE_GRACE_SECONDS = TWITTER_STEAM_DUPLICATE_WINDOW_SECONDS
USER_COMMAND_COOLDOWN_SECONDS = 3.0
ZIP_CUSTOM_ID_PREFIX = "limpi:zip:"
BRIGHTEN_CUSTOM_ID_PREFIX = "limpi:brighten:"
ZIP_IMAGE_CONCURRENCY = 20
ZIP_CACHE_MAX_ITEMS = 10
ZIP_UPLOAD_SAFE_BYTES = 7 * 1024 * 1024
ZIP_UPLOAD_HEADROOM_BYTES = 256 * 1024
BRIGHTEN_PROCESS_CONCURRENCY = 2
BRIGHTEN_CACHE_MAX_ITEMS = 24
BRIGHTEN_CACHE_MAX_BYTES = 64 * 1024 * 1024
BRIGHTEN_CACHE_MAX_ITEM_BYTES = 16 * 1024 * 1024
IMAGE_PROCESS_CONCURRENCY = 10
IMAGE_CACHE_MAX_ITEMS = 256
IMAGE_CACHE_MAX_BYTES = 256 * 1024 * 1024
IMAGE_CACHE_MAX_ITEM_BYTES = 16 * 1024 * 1024
IMAGE_FAILED_URL_CACHE_MAX_ITEMS = 1024
# 처리 완료된 에고 기프트 첨부 이미지(150px PNG)는 작아서 넉넉히 캐싱한다.
EGO_GIFT_IMAGE_CACHE_MAX_ITEMS = 512
EGO_GIFT_IMAGE_CACHE_MAX_BYTES = 64 * 1024 * 1024
EGO_GIFT_IMAGE_PROCESS_CONCURRENCY = 4
EGO_GIFT_IMAGE_WARMUP_LIMIT = 12
EGO_GIFT_IMAGE_WARMUP_CONCURRENCY = 4
IMAGE_CACHE_WARM_POST_LIMIT = 20
IMAGE_DOWNLOAD_ATTEMPTS = 5
IMAGE_DOWNLOAD_TIMEOUT_SECONDS = 15
EGO_GIFT_FALLBACK_IMAGE_INDEX_URL = "https://www.archivum.dev/ko/limbus/egoGifts"
EGO_GIFT_FALLBACK_IMAGE_BASE_URL = "https://cdn.archivum.dev/file/butterflytheory/limbus/ego_gift"
DISCORD_HEARTBEAT_TIMEOUT_SECONDS = 120.0
AIOHTTP_KEEPALIVE_TIMEOUT_SECONDS = 90
TCP_KEEPALIVE_IDLE_SECONDS = 30
TCP_KEEPALIVE_INTERVAL_SECONDS = 10
TCP_KEEPALIVE_PROBES = 3
WINDOWS_KEEPALIVE_TIME_MS = TCP_KEEPALIVE_IDLE_SECONDS * 1000
WINDOWS_KEEPALIVE_INTERVAL_MS = TCP_KEEPALIVE_INTERVAL_SECONDS * 1000
ASYNCIO_RESET_LOG_COOLDOWN_SECONDS = 300
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
NEWS_BANNER_DIR = Path("img")
NEWS_BANNER_ATTACHMENT_NAME = "limpi_news_banner.png"
MAX_INLINE_GALLERY_IMAGES = 10
NEWS_UPDATE_NOTICE_COOLDOWN = timedelta(minutes=30)
COMMAND_GUIDE_IMAGE_NAME = "honglu.jpg"
EGO_GIFT_STORE_PATH = Path("ego_gifts.json")
EGO_GIFT_UPDATE_WEEKDAY = 4
EGO_GIFT_UPDATE_HOUR_KST = 8
EGO_GIFT_SELECT_PAGE_SIZE = 25
NEWS_BANNER_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
NEWS_BANNER_DISABLED_LABEL = "사용 안 함"
YOUTUBE_PLACEHOLDER_IMAGE_FRAGMENT = "youtube_16x9_placeholder.gif"
LEGACY_STEAM_CARD_THUMBNAIL_FRAGMENTS = (
)
EMBEDS_PER_MESSAGE = 10
IMAGE_ONLY_EMBEDS_PER_MESSAGE = 10
MAX_TWITTER_EMBED_IMAGES = EMBEDS_PER_MESSAGE
EMBED_DESCRIPTION_LIMIT = 8096
FILES_PER_MESSAGE = 10
IMAGE_FILES_PER_MESSAGE = 10
BOOLEAN_TRUE = "true"
BOOLEAN_FALSE = "false"
BOOLEAN_CHOICES = [
    app_commands.Choice(name="허용", value=BOOLEAN_TRUE),
    app_commands.Choice(name="비허용", value=BOOLEAN_FALSE),
]
BROADCAST_SOURCE_BOTH = "both"
BROADCAST_SOURCE_CHZZK = "chzzk"
BROADCAST_SOURCE_YOUTUBE = "youtube"
BROADCAST_SOURCE_CHOICES = [
    app_commands.Choice(name="치지직 & 유튜브", value=BROADCAST_SOURCE_BOTH),
    app_commands.Choice(name="치지직", value=BROADCAST_SOURCE_CHZZK),
    app_commands.Choice(name="유튜브", value=BROADCAST_SOURCE_YOUTUBE),
]
NEWS_SOURCE_BOTH = "both"
NEWS_SOURCE_STEAM = "steam"
NEWS_SOURCE_TWITTER = "twitter"
TWITTER_NEWS_DEFAULT_MAX_AGE_SECONDS = 24 * 60 * 60
NEWS_SOURCE_CHOICES = [
    app_commands.Choice(name="Steam & X(트위터)", value=NEWS_SOURCE_BOTH),
    app_commands.Choice(name="Steam", value=NEWS_SOURCE_STEAM),
    app_commands.Choice(name="X(트위터)", value=NEWS_SOURCE_TWITTER),
]
NEWS_LOOKUP_SOURCE_CHOICES = [
    app_commands.Choice(name="Steam", value=NEWS_SOURCE_STEAM),
    app_commands.Choice(name="트위터", value=NEWS_SOURCE_TWITTER),
]
HAMPANG_SOURCE_CHOICES = [
    app_commands.Choice(name="X(트위터) & YouTube", value=HAMPANG_SOURCE_BOTH),
    app_commands.Choice(name="X(트위터)", value=HAMPANG_SOURCE_X),
    app_commands.Choice(name="YouTube", value=HAMPANG_SOURCE_YOUTUBE),
]
LANGUAGE_CHOICES = [
    app_commands.Choice(name="한국어", value="koreana"),
    app_commands.Choice(name="English", value="english"),
    app_commands.Choice(name="日本語", value="japanese"),
]
IMAGE_DELIVERY_FILES = "files"
IMAGE_DELIVERY_EMBEDS = "embeds"
IMAGE_DELIVERY_CHOICES = [
    app_commands.Choice(name="임베드에 이미지 표시", value=IMAGE_DELIVERY_EMBEDS),
    app_commands.Choice(name="첨부파일로 따로 전송", value=IMAGE_DELIVERY_FILES),
]
LANGUAGE_LABELS = {
    "koreana": "한국어",
    "english": "English",
    "japanese": "日本語",
}
NEWS_UI_TEXT = {
    "koreana": {
        "schedule": "일정",
        "original": "원문 보기",
        "download_images": "이미지 다운로드",
        "updated": "-# 🔄 수정된 소식입니다.",
        "reply_context": "> @{username} 님에게 보내는 답글:",
        "retweet_context": "> @{username} 님의 게시물 리트윗:",
        "zip_unavailable": "지금은 다운로드를 처리할 수 없어요.",
        "zip_no_images": "이 게시물에는 이미지가 없어요.",
        "zip_fetch_failed": "이미지를 가져오는 중 문제가 생겼어요. 잠시 후 다시 시도해주세요.",
        "zip_empty": "이미지를 다운로드하지 못했어요.",
        "zip_ready": "이미지 {count}장을 압축했어요.",
        "zip_ready_split": "이미지 {count}장을 압축했어요. 파일이 커서 {parts}개로 나눠 보냅니다.",
        "zip_oversized_skipped": "Discord 업로드 한도보다 큰 이미지 {skipped}장은 제외했어요.",
        "zip_upload_too_large": "압축 파일이 Discord 업로드 한도보다 커서 보낼 수 없어요. 원문에서 이미지를 확인해주세요.",
    },
    "english": {
        "schedule": "Schedule",
        "original": "View original",
        "download_images": "Download images",
        "updated": "-# 🔄 This news was updated.",
        "reply_context": "> Replying to @{username}:",
        "retweet_context": "> Retweeted @{username}'s post:",
        "zip_unavailable": "Downloads are unavailable right now.",
        "zip_no_images": "This post has no images.",
        "zip_fetch_failed": "Something went wrong while fetching the images. Please try again later.",
        "zip_empty": "Could not download the images.",
        "zip_ready": "Compressed {count} images.",
        "zip_ready_split": "Compressed {count} images. The file is large, so I am sending it in {parts} parts.",
        "zip_oversized_skipped": "Skipped {skipped} images that are larger than Discord's upload limit.",
        "zip_upload_too_large": "The ZIP is larger than Discord's upload limit. Please open the original post for the images.",
    },
    "japanese": {
        "schedule": "日程",
        "original": "原文を見る",
        "download_images": "画像をダウンロード",
        "updated": "-# 🔄 このお知らせは更新されました。",
        "reply_context": "> @{username} さんへの返信:",
        "retweet_context": "> @{username} さんの投稿をリツイート:",
        "zip_unavailable": "現在、ダウンロードを処理できません。",
        "zip_no_images": "この投稿には画像がありません。",
        "zip_fetch_failed": "画像の取得中に問題が発生しました。しばらくしてからもう一度お試しください。",
        "zip_empty": "画像をダウンロードできませんでした。",
        "zip_ready": "画像{count}枚を圧縮しました。",
        "zip_ready_split": "画像{count}枚を圧縮しました。ファイルが大きいため{parts}個に分けて送信します。",
        "zip_oversized_skipped": "Discordのアップロード上限を超える画像{skipped}枚は除外しました。",
        "zip_upload_too_large": "ZIPがDiscordのアップロード上限を超えているため送信できません。元の投稿から画像を確認してください。",
    },
}
SYNC_LANGUAGES = ("koreana", "english", "japanese")
MAINTENANCE_WEEKDAY = 3
MAINTENANCE_START_HOUR = 10
MAINTENANCE_UPDATE_HOUR = 12
MAINTENANCE_START_TITLE = "림버스 컴퍼니 점검 알림"
MAINTENANCE_START_DESCRIPTION = (
    "지금부터 림버스 컴퍼니가 점검에 들어가요! "
    "관리자 분들은 점검이 끝날때까지 기다리시면 될거에요! <3"
)
MAINTENANCE_UPDATE_TITLE = "림버스 컴퍼니 업데이트"
MAINTENANCE_UPDATE_DESCRIPTION = (
    "지금 림버스 컴퍼니가 점검이 끝나고 업데이트가 되었어요! "
    "스팀 또는 앱 스토어에 들어가서 림버스를 업데이트 해주세요! <3"
)

__all__ = [
    "POST_FORMAT_RICH",
    "KST",
    "NEWS_POST_LIMIT",
    "TWITTER_POST_LIMIT",
    "NEWS_SELECT_PAGE_SIZE",
    "NEWS_SELECT_POST_LIMIT",
    "NEWS_POLL_TICK_SECONDS",
    "TWITTER_POLL_TICK_SECONDS",
    "TWITTER_PRIORITY_POLL_TIMES_KST",
    "TWITTER_PRIORITY_POLL_INTERVAL_SECONDS",
    "TWITTER_PRIORITY_POLL_WINDOW_SECONDS",
    "TWITTER_PRIORITY_POLL_PREP_SECONDS",
    "CHZZK_POLL_INTERVAL_SECONDS",
    "YOUTUBE_UPLOAD_POLL_INTERVAL_SECONDS",
    "HAMPANG_X_USERNAME",
    "HAMPANG_X_URL",
    "HAMPANG_YOUTUBE_TITLE_MARKER",
    "HAMPANG_AUTO_POLL_INTERVAL_SECONDS",
    "HAMPANG_SOURCE_BOTH",
    "HAMPANG_SOURCE_X",
    "HAMPANG_SOURCE_YOUTUBE",
    "CHZZK_LIVE_ANNOUNCE_MAX_AGE",
    "CHZZK_LIVE_END_ANNOUNCE_MAX_AGE",
    "YOUTUBE_LIVE_ANNOUNCE_MAX_AGE",
    "NEWS_TARGET_SEND_CONCURRENCY",
    "NEWS_ROLE_MENTION_COOLDOWN_SECONDS",
    "TWITTER_STEAM_PREFERENCE_GRACE_SECONDS",
    "TWITTER_STEAM_DUPLICATE_WINDOW_SECONDS",
    "USER_COMMAND_COOLDOWN_SECONDS",
    "ZIP_CUSTOM_ID_PREFIX",
    "BRIGHTEN_CUSTOM_ID_PREFIX",
    "ZIP_IMAGE_CONCURRENCY",
    "ZIP_CACHE_MAX_ITEMS",
    "ZIP_UPLOAD_SAFE_BYTES",
    "ZIP_UPLOAD_HEADROOM_BYTES",
    "BRIGHTEN_PROCESS_CONCURRENCY",
    "BRIGHTEN_CACHE_MAX_ITEMS",
    "BRIGHTEN_CACHE_MAX_BYTES",
    "BRIGHTEN_CACHE_MAX_ITEM_BYTES",
    "IMAGE_PROCESS_CONCURRENCY",
    "IMAGE_CACHE_MAX_ITEMS",
    "IMAGE_CACHE_MAX_BYTES",
    "IMAGE_CACHE_MAX_ITEM_BYTES",
    "IMAGE_FAILED_URL_CACHE_MAX_ITEMS",
    "EGO_GIFT_IMAGE_CACHE_MAX_ITEMS",
    "EGO_GIFT_IMAGE_CACHE_MAX_BYTES",
    "EGO_GIFT_IMAGE_PROCESS_CONCURRENCY",
    "EGO_GIFT_IMAGE_WARMUP_LIMIT",
    "EGO_GIFT_IMAGE_WARMUP_CONCURRENCY",
    "IMAGE_CACHE_WARM_POST_LIMIT",
    "IMAGE_DOWNLOAD_ATTEMPTS",
    "IMAGE_DOWNLOAD_TIMEOUT_SECONDS",
    "EGO_GIFT_FALLBACK_IMAGE_INDEX_URL",
    "EGO_GIFT_FALLBACK_IMAGE_BASE_URL",
    "DISCORD_HEARTBEAT_TIMEOUT_SECONDS",
    "AIOHTTP_KEEPALIVE_TIMEOUT_SECONDS",
    "TCP_KEEPALIVE_IDLE_SECONDS",
    "TCP_KEEPALIVE_INTERVAL_SECONDS",
    "TCP_KEEPALIVE_PROBES",
    "WINDOWS_KEEPALIVE_TIME_MS",
    "WINDOWS_KEEPALIVE_INTERVAL_MS",
    "ASYNCIO_RESET_LOG_COOLDOWN_SECONDS",
    "ES_CONTINUOUS",
    "ES_SYSTEM_REQUIRED",
    "NEWS_BANNER_DIR",
    "NEWS_BANNER_ATTACHMENT_NAME",
    "MAX_INLINE_GALLERY_IMAGES",
    "NEWS_UPDATE_NOTICE_COOLDOWN",
    "COMMAND_GUIDE_IMAGE_NAME",
    "EGO_GIFT_STORE_PATH",
    "EGO_GIFT_UPDATE_WEEKDAY",
    "EGO_GIFT_UPDATE_HOUR_KST",
    "EGO_GIFT_SELECT_PAGE_SIZE",
    "NEWS_BANNER_EXTENSIONS",
    "NEWS_BANNER_DISABLED_LABEL",
    "YOUTUBE_PLACEHOLDER_IMAGE_FRAGMENT",
    "LEGACY_STEAM_CARD_THUMBNAIL_FRAGMENTS",
    "EMBEDS_PER_MESSAGE",
    "IMAGE_ONLY_EMBEDS_PER_MESSAGE",
    "MAX_TWITTER_EMBED_IMAGES",
    "EMBED_DESCRIPTION_LIMIT",
    "FILES_PER_MESSAGE",
    "IMAGE_FILES_PER_MESSAGE",
    "BOOLEAN_TRUE",
    "BOOLEAN_FALSE",
    "BOOLEAN_CHOICES",
    "BROADCAST_SOURCE_BOTH",
    "BROADCAST_SOURCE_CHZZK",
    "BROADCAST_SOURCE_YOUTUBE",
    "BROADCAST_SOURCE_CHOICES",
    "NEWS_SOURCE_BOTH",
    "NEWS_SOURCE_STEAM",
    "NEWS_SOURCE_TWITTER",
    "TWITTER_NEWS_DEFAULT_MAX_AGE_SECONDS",
    "NEWS_SOURCE_CHOICES",
    "NEWS_LOOKUP_SOURCE_CHOICES",
    "HAMPANG_SOURCE_CHOICES",
    "LANGUAGE_CHOICES",
    "IMAGE_DELIVERY_FILES",
    "IMAGE_DELIVERY_EMBEDS",
    "IMAGE_DELIVERY_CHOICES",
    "LANGUAGE_LABELS",
    "NEWS_UI_TEXT",
    "SYNC_LANGUAGES",
    "MAINTENANCE_WEEKDAY",
    "MAINTENANCE_START_HOUR",
    "MAINTENANCE_UPDATE_HOUR",
    "MAINTENANCE_START_TITLE",
    "MAINTENANCE_START_DESCRIPTION",
    "MAINTENANCE_UPDATE_TITLE",
    "MAINTENANCE_UPDATE_DESCRIPTION",
]
