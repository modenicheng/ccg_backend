# Agent Guidelines for CCG Backend

*No Cursor rules (.cursor/rules/) or Copilot instructions (.github/copilot-instructions.md) are present.*

## Build, Test, and Run Commands

### Environment Setup
- Python 3.12+, [uv](https://github.com/astral-sh/uv) for dependencies
- Copy `.env.template` → `.env` (all env vars prefixed `CCG_`)
- Optional: copy `config.template.yaml` → `config.yaml`
- `config.yaml` should use semantic module‑based hierarchy (e.g. `ccg.database.url`, `ccg.songlist.fetch.concurrency`), not a flat dump of all env keys
- Runtime config merge order: `os.environ > .env > config.yaml`
- Optional YAML path override: `CCG_CONFIG_YAML_PATH`

### Running the Application
```bash
uv run uvicorn main:app --reload --port 8000   # development
uv run uvicorn main:app --host 0.0.0.0 --port 8000   # production style
```

### Database Migrations (Alembic)
```bash
uv run alembic revision --autogenerate -m "description"
uv run alembic upgrade head
uv run alembic downgrade -1
```

### Formatting and Linting
```bash
uv run yapf -i -r .               # format all Python files
uv run yapf -i --recursive .      # alternative
pylint $(git ls-files '*.py')     # lint all tracked Python files (excludes tests, alembic)
pylint --ignore=tests,alembic .   # alternative
# CI: GitHub Actions workflow (.github/workflows/pylint.yml) runs pylint on push
```

### Testing
```bash
uv run pytest                        # all tests
uv run pytest tests/test_db_crud.py  # specific file
uv run pytest tests/test_db_crud.py::test_create_user  # single test
uv run pytest -v                     # verbose
uv run pytest --cov=.                # coverage
uv run pytest tests/test_playback_message_ws.py  # WebSocket tests (needs Redis)
```
- `tests/ws_conn.py` helper for real WebSocket connections (see `tests/test_playback_message_ws.py`)
- Use `@pytest.mark.asyncio` for async tests (some with `loop_scope="session"` or `"module"`)
- Redis required for some tests; ensure `CCG_REDIS_URL` points to a running instance

### Task Queue (Huey)
```bash
uv run huey_consumer.py mq.tasks.huey
```

## Code Style Guidelines

### Imports
- Absolute imports from project root: `from db import crud`
- Group: stdlib → third‑party → local
- `from __future__ import annotations` in every Python file
- Use `|` for unions, `list[str]` etc. (Python 3.10+)
- Import typing constructs from `typing` (`Any`, `Optional` etc.)

### Type Hints
- Always annotate function arguments and return values
- SQLAlchemy: `Mapped[...]`, `mapped_column(...)`
- Pydantic: `Field(..., description="...")`
- Prefer explicit types over `Any`

### Naming Conventions
- **Classes**: `CamelCase` (`RoomPlayer`, `PlaybackState`)
- **Variables/Functions**: `snake_case` (`room_id`, `get_player_score`)
- **Constants**: `UPPER_SNAKE_CASE` (`MAX_PLAYERS`)
- **Private members**: `_leading_underscore`
- **SQLAlchemy models**: No suffix (`User`, `Room`, `Song`)
- **Pydantic schemas**: No suffix (`PlayMessage`, `PauseMessage`)
- **WebSocket events**: Use `GameEventType` enum values (`PLAY = 20`)

### Error Handling
- Use `try/except` for expected exceptions; log with `logger.exception()` or `logger.error()`
- In WebSocket handlers, raise `ValueError` for client errors
- `asyncio.gather(..., return_exceptions=True)` for concurrent ops
- Let SQLAlchemy exceptions propagate to FastAPI (HTTP 500)

### Logging
- `logger = get_logger(__name__)` at module level
- Include context (`room_id`, `player_id`) in log messages

### Async Patterns
- Use `async/await` for I/O; `asyncio.gather()` for concurrency
- WebSocket handlers are async, registered with `@regist(event, data_validator)`
- Database: SQLAlchemy async API (`AsyncSession`)
- Database transactions: FastAPI endpoints use `get_db` dependency yielding AsyncSession; non‑HTTP flows use `session_scope` async context manager

### Pydantic Schemas
- HTTP API schemas: `schemas/*.py`; WebSocket messages: `schemas/ws_messages/*.py`
- Inherit from `MessageBase` + `AutoEventConvertMixin`
- Use `model_config = ConfigDict(from_attributes=True)` for ORM compatibility
- Serialize with `model_dump()` (not `dict()`); deserialize with `.model_validate(obj)`

### SQLAlchemy Models
- SQLAlchemy 2.0 style: `Mapped`, `mapped_column`
- Define `__tablename__`; `relationship()` with `back_populates`
- `__table_args__` for constraints, indexes; `server_default` for timestamps
- Foreign keys: `ondelete="CASCADE"` where appropriate
- Use `Enum` classes for status fields (`RoomStatusORM`)

### Redis Caching & JSON Serialization
- Use `get_redis()` (from `cache.connection`) not `redis_client.get_client()` for Redis access
- Cache room state as Redis hash using Pydantic schemas with `to_redis_hash()`/`from_redis_hash()`
- TTL 6 hours (`ROOM_TTL_SECONDS`). Use `redis.expire(key, TTL)` after updates
- Cache keys generated via `RedisKeys` utility in `cache.utils`
- Prefer `orjson.loads()` and `orjson.dumps()` for performance (supports non‑string keys via `orjson.OPT_NON_STR_KEYS`)

### System Monitoring
- `MemoryMonitor` (from `utils.memory_monitor`) reports memory usage changes ≥20 MB
- Started automatically in FastAPI lifespan; configurable with `interval` and `report_threshold_mb`

## Project Structure
Standard FastAPI layout: `main.py` entry point; `db/` models; `cache/` Redis; `schemas/` Pydantic; `handlers/` WebSocket events; `router/` HTTP endpoints; `utils/` utilities; `mq/` task queue; `client_manager/` WebSocket clients; `tests/` pytest.

## Common Tasks

### Add New WebSocket Event
1. Add event to `GameEventType` in `utils/enumerations.py`
2. Create schema in `schemas/ws_messages/` (inherit `MessageBase`)
3. Create handler in appropriate `handlers/` file
4. Register with `@regist(event, data_validator=Schema)`
5. Import handler module in `handlers/__init__.py`

### Add New HTTP API Endpoint
1. Create/update router in `router/`
2. Define request/response schemas in `schemas/`
3. Add route with dependency `db: AsyncSession = Depends(get_db)`
4. Handle errors, document with docstrings

### Add New Database Model
1. Add class to `db/models.py` (SQLAlchemy 2.0 style)
2. Define relationships, constraints
3. `uv run alembic revision --autogenerate -m "add_model"`
4. After autogeneration, you must double check the migration file and modify it to adopt both SQLite and PostgreSQL traits
5. `uv run alembic upgrade head`
6. Add CRUD helpers in `db/crud.py` if needed

### Run a Single Test
```bash
uv run pytest tests/path/to/test.py::test_function -v
uv run pytest -k "pattern" -v
uv run pytest -v --log-level=DEBUG
```

## Ambiguous Patterns & Pitfalls

- **Redis access**: Use `get_redis()` (from `cache.connection`) NOT `redis_client.get_client()` for Redis access. The former handles connection lifecycle.
- **JSON serialization**: Prefer `orjson.loads()` and `orjson.dumps()` for performance (supports non‑string keys via `orjson.OPT_NON_STR_KEYS`). Redis hash serialization uses `to_redis_hash()`/`from_redis_hash()` methods on Pydantic schemas.
- **Database transactions**: FastAPI endpoints use `get_db` dependency yielding AsyncSession; commit/rollback handled automatically. Non‑HTTP flows (tasks, scripts) must use `session_scope` async context manager from `db.session`. CRUD functions in `db/crud.py` expect caller to manage commit.
- **Error handling in WebSocket handlers**: Raise `ValueError` for client errors; errors are sent via `ErrorMessage` schema with `ErrorEventType`. Use `asyncio.gather(..., return_exceptions=True)` for concurrent operations.
- **WebSocket event registration**: Import handler modules in `handlers/__init__.py` to trigger decorator registration; otherwise handlers won't be discovered.
- **MemoryMonitor**: Started automatically in FastAPI lifespan; logs memory usage changes ≥20 MB. Configurable with `interval` and `report_threshold_mb`. May be noisy in logs.
- **Repeat function bugs**: Watch for repeat function bugs in `connection_lifespan.py` (known issue).

## Environment Variables (CCG_*)
Key environment variables (prefixed `CCG_`): `CCG_DATABASE_URL`, `CCG_REDIS_URL`, `CCG_QQ_MUSIC_COOKIE`, `CCG_AUDIO_DOWNLOAD_DIR`, `CCG_AUDIO_TOKEN_TTL`, `CCG_LOG_LEVEL`, `CCG_ASSET_CACHE_MAX_ITEMS`, `CCG_CONFIG_YAML_PATH`.

Config is centralized in `config/settings.py` and validated at import time. `config.yaml` should use semantic module‑based hierarchy (e.g., `ccg.database.url`, `ccg.songlist.fetch.concurrency`), not a flat dump of all env keys. Invalid config should fail fast and block startup for both `uv run python main.py` and `uv run uvicorn main:app`.
See `.env.template` and `config.template.yaml` for defaults/examples.

## Troubleshooting
- **Database**: Check `CCG_DATABASE_URL` in `.env`; PostgreSQL required for production features
- **Redis**: Check `CCG_REDIS_URL`; Redis required for real‑time features
- **WebSocket tests**: Use `tests/ws_conn.py` factory; ensure Redis running if the test needs
- **Migrations**: review generated script; keep the data integrity as possible as you can
- **MemoryMonitor**: logs memory usage every 30s; adjust `interval` and `report_threshold_mb` if too verbose

---

## Coding Style Manual

1. Formatting the code

   Use yapf.

   ```bash
   uv run yapf -i -r $(git ls-files '*.py')
   ```

   **DO NOT** use `.` for the full format. the venv directory will be included unexceptedly.

2. Linting the code

   Use pylint.

   ```bash
   uv run pylint $(git ls-files '*.py') # All python files
   ```

   May be you can ignore the `tests/` and `alembic` directories.

*Last updated: March 2026*