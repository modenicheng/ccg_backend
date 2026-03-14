"""Application settings and configuration management."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

from rich import print as rprint
import yaml

from .schema_map import YAML_PATH_TO_ENV_KEY

_ROOT_DIR = Path(__file__).resolve().parent.parent
_DEFAULT_ENV_PATH = _ROOT_DIR / ".env"
_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}
_LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}


@dataclass(frozen=True)  # pylint: disable=too-many-instance-attributes
class AppConfig:
    """Application configuration dataclass."""

    database_url: str
    database_echo: bool
    redis_url: str
    qq_music_cookie: str
    songlist_fetch_concurrency: int
    songlist_fetch_retries: int
    songlist_fetch_backoff_seconds: float
    audio_download_retries: int
    audio_download_backoff_seconds: float
    audio_download_dir: str
    song_url_retries: int
    song_url_backoff_seconds: float
    log_level: str
    audio_token_ttl: int
    asset_cache_max_items: int


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            result[key] = value
    return result


def _to_bool(value: Any, key: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        str_value = str(int(value)).strip().lower()
    else:
        str_value = str(value).strip().lower()

    if str_value in _TRUTHY:
        return True
    if str_value in _FALSY:
        return False
    raise ValueError(f"Invalid boolean value for {key}: {value!r}. Expected one of "
                     f"{sorted(_TRUTHY | _FALSY)}")


def _to_int(value: Any, key: str, minimum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid integer value for {key}: {value!r}") from exc
    if minimum is not None and parsed < minimum:
        raise ValueError(
            f"Invalid integer value for {key}: {parsed}. Must be >= {minimum}")
    return parsed


def _to_float(value: Any, key: str, minimum: float | None = None) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid float value for {key}: {value!r}") from exc
    if minimum is not None and parsed < minimum:
        raise ValueError(
            f"Invalid float value for {key}: {parsed}. Must be >= {minimum}")
    return parsed


def _to_str(value: Any, key: str, allow_empty: bool = True) -> str:
    parsed = str(value).strip()
    if not allow_empty and not parsed:
        raise ValueError(f"Invalid empty value for {key}")
    return parsed


def _normalize_database_url(raw_url: str | None) -> str:
    if raw_url:
        url = raw_url.strip()
    else:
        default_db_path = _ROOT_DIR / "data" / "game.db"
        default_db_path.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite+aiosqlite:///{default_db_path.as_posix()}"

    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    if url.startswith("postgresql://") and "+" not in url.split("://", 1)[0]:
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


def _flatten_yaml_paths(
    values: dict[str, Any], parent: tuple[str, ...] = ()) -> dict[tuple[str, ...], Any]:
    result: dict[tuple[str, ...], Any] = {}
    for key, value in values.items():
        path_part = str(key).strip().lower()
        current_path = parent + (path_part,)
        if isinstance(value, dict):
            result.update(_flatten_yaml_paths(value, current_path))
            continue
        result[current_path] = value
    return result


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(
            f"YAML config file not found: {path}. Please create it from config.template.yaml"
        )

    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"Failed to parse YAML config at {path}: {exc}") from exc

    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ValueError("YAML config root must be an object (mapping)")

    if "ccg" in loaded:
        nested = loaded["ccg"]
        if not isinstance(nested, dict):
            raise ValueError("YAML key 'ccg' must be an object (mapping)")
        source = nested
    else:
        source = loaded

    flattened_paths = _flatten_yaml_paths(source)
    normalized: dict[str, Any] = {}

    for path_parts, value in flattened_paths.items():
        if len(path_parts) == 1:
            raw_key = path_parts[0]
            if raw_key.startswith("ccg_"):
                normalized[raw_key.upper()] = value
                continue

        env_key = YAML_PATH_TO_ENV_KEY.get(path_parts)
        if env_key:
            normalized[env_key] = value

    return normalized


def _pick_value(values: dict[str, Any], env_key: str, fallback: Any) -> Any:
    if env_key in values:
        return values[env_key]
    lowered = env_key.lower()
    if lowered in values:
        return values[lowered]
    return fallback


def _build_from_values(values: dict[str, Any]) -> AppConfig:
    database_url_raw = _pick_value(values, "CCG_DATABASE_URL", None)

    config = AppConfig(
        database_url=_normalize_database_url(
            _to_str(database_url_raw, "CCG_DATABASE_URL"
                    ) if database_url_raw is not None else None),
        database_echo=_to_bool(
            _pick_value(values, "CCG_DATABASE_ECHO", "false"),
            "CCG_DATABASE_ECHO",
        ),
        redis_url=_to_str(
            _pick_value(values, "CCG_REDIS_URL", "redis://localhost:6379/0"),
            "CCG_REDIS_URL",
            allow_empty=False,
        ),
        qq_music_cookie=_to_str(_pick_value(values, "CCG_QQ_MUSIC_COOKIE", ""),
                                "CCG_QQ_MUSIC_COOKIE"),
        songlist_fetch_concurrency=_to_int(
            _pick_value(values, "CCG_SONGLIST_FETCH_CONCURRENCY", 8),
            "CCG_SONGLIST_FETCH_CONCURRENCY",
            minimum=1,
        ),
        songlist_fetch_retries=_to_int(
            _pick_value(values, "CCG_SONGLIST_FETCH_RETRIES", 5),
            "CCG_SONGLIST_FETCH_RETRIES",
            minimum=1,
        ),
        songlist_fetch_backoff_seconds=_to_float(
            _pick_value(values, "CCG_SONGLIST_FETCH_BACKOFF_SECONDS", 0.4),
            "CCG_SONGLIST_FETCH_BACKOFF_SECONDS",
            minimum=0.0,
        ),
        audio_download_retries=_to_int(
            _pick_value(values, "CCG_AUDIO_DOWNLOAD_RETRIES", 3),
            "CCG_AUDIO_DOWNLOAD_RETRIES",
            minimum=1,
        ),
        audio_download_backoff_seconds=_to_float(
            _pick_value(values, "CCG_AUDIO_DOWNLOAD_BACKOFF_SECONDS", 0.4),
            "CCG_AUDIO_DOWNLOAD_BACKOFF_SECONDS",
            minimum=0.0,
        ),
        audio_download_dir=_to_str(
            _pick_value(values, "CCG_AUDIO_DOWNLOAD_DIR", "assets/audio"),
            "CCG_AUDIO_DOWNLOAD_DIR",
            allow_empty=False,
        ),
        song_url_retries=_to_int(
            _pick_value(values, "CCG_SONG_URL_RETRIES", 3),
            "CCG_SONG_URL_RETRIES",
            minimum=1,
        ),
        song_url_backoff_seconds=_to_float(
            _pick_value(values, "CCG_SONG_URL_BACKOFF_SECONDS", 0.4),
            "CCG_SONG_URL_BACKOFF_SECONDS",
            minimum=0.0,
        ),
        log_level=_to_str(
            _pick_value(values, "CCG_LOG_LEVEL", "INFO"),
            "CCG_LOG_LEVEL",
            allow_empty=False,
        ).upper(),
        audio_token_ttl=_to_int(
            _pick_value(values, "CCG_AUDIO_TOKEN_TTL", 21600),
            "CCG_AUDIO_TOKEN_TTL",
            minimum=1,
        ),
        asset_cache_max_items=_to_int(
            _pick_value(values, "CCG_ASSET_CACHE_MAX_ITEMS", 64),
            "CCG_ASSET_CACHE_MAX_ITEMS",
            minimum=1,
        ),
    )

    if config.log_level not in _LOG_LEVELS:
        raise ValueError(f"Invalid CCG_LOG_LEVEL: {config.log_level!r}. "
                         f"Expected one of {sorted(_LOG_LEVELS)}")

    return config


def load_config() -> AppConfig:
    """Load application configuration from YAML and environment variables."""
    yaml_path_raw = os.getenv("CCG_CONFIG_YAML_PATH", "config.yaml").strip()
    yaml_path = Path(yaml_path_raw)
    if not yaml_path.is_absolute():
        yaml_path = _ROOT_DIR / yaml_path

    try:
        yaml_values: dict[str, Any] = {}
        yaml_loaded = False
        if yaml_path.exists():
            yaml_values = _load_yaml_mapping(yaml_path)
            yaml_loaded = True

        env_file_values = _read_env_file(_DEFAULT_ENV_PATH)
        merged = dict(yaml_values)
        merged.update(env_file_values)
        merged.update(os.environ)

        config = _build_from_values(merged)

        if yaml_loaded:
            rprint("[bold green]Config loaded[/]: "
                   "yaml({yaml_path}) + env(.env + process env, 覆盖 yaml)")
        else:
            rprint("[bold yellow]Config loaded[/]: "
                   "yaml not found, using env(.env + process env) only")
        return config
    except Exception as exc:
        rprint(f"[bold red]配置解析失败，服务启动终止[/]: {exc}")
        raise


app_config = load_config()
