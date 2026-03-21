"""Schema mapping from YAML paths to environment variable keys."""

from __future__ import annotations

YAML_PATH_TO_ENV_KEY: dict[tuple[str, ...], str] = {
    ("database", "url"):
        "CCG_DATABASE_URL",
    ("database", "echo"):
        "CCG_DATABASE_ECHO",
    ("redis", "url"):
        "CCG_REDIS_URL",
    ("qq_music", "cookie"):
        "CCG_QQ_MUSIC_COOKIE",
    ("qq_music", "cookies"):
        "CCG_QQ_MUSIC_COOKIES",
    ("qq_music", "rotation", "enabled"):
        "CCG_COOKIE_ROTATION_ENABLED",
    ("qq_music", "rotation", "strategy"):
        "CCG_COOKIE_ROTATION_STRATEGY",
    ("qq_music", "rotation", "failure_max_retries"):
        "CCG_COOKIE_FAILURE_MAX_RETRIES",
    ("qq_music", "refresh", "enabled"):
        "CCG_COOKIE_REFRESH_ENABLED",
    ("qq_music", "refresh", "check_interval_seconds"):
        "CCG_COOKIE_REFRESH_CHECK_INTERVAL",
    ("songlist", "fetch", "concurrency"):
        "CCG_SONGLIST_FETCH_CONCURRENCY",
    ("songlist", "fetch", "retries"):
        "CCG_SONGLIST_FETCH_RETRIES",
    ("songlist", "fetch", "backoff_seconds"):
        "CCG_SONGLIST_FETCH_BACKOFF_SECONDS",
    ("audio", "download", "retries"):
        "CCG_AUDIO_DOWNLOAD_RETRIES",
    ("audio", "download", "backoff_seconds"):
        "CCG_AUDIO_DOWNLOAD_BACKOFF_SECONDS",
    ("audio", "download", "dir"):
        "CCG_AUDIO_DOWNLOAD_DIR",
    ("song", "url", "retries"):
        "CCG_SONG_URL_RETRIES",
    ("song", "url", "backoff_seconds"):
        "CCG_SONG_URL_BACKOFF_SECONDS",
    ("app", "log_level"):
        "CCG_LOG_LEVEL",
    ("audio", "token", "ttl"):
        "CCG_AUDIO_TOKEN_TTL",
    ("cache", "asset", "max_items"):
        "CCG_ASSET_CACHE_MAX_ITEMS",
}
