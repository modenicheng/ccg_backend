# Agent Guidelines for CCG Backend

*No Cursor rules (.cursor/rules/) or Copilot instructions (.github/copilot-instructions.md) are present.*

## Build, Test, and Run Commands

### Environment Setup
- Python 3.12+, [uv](https://github.com/astral-sh/uv) for dependency management
- `uv.lock` pins exact versions; run `uv sync` to install dependencies
- Copy `.env.template` → `.env` (all environment variables prefixed `CCG_`)
- Optional: copy `config.template.yaml` → `config.yaml`
- `config.yaml` should use semantic module‑based hierarchy (e.g., `ccg.database.url`, `ccg.songlist.fetch.concurrency`), not a flat dump of all env keys
- Runtime config merge order: `os.environ > .env > config.yaml` (env overrides YAML)
- Optional YAML path override: `CCG_CONFIG_YAML_PATH`
- Configuration is validated at startup via `config/settings.py`; invalid config blocks startup

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
uv run yapf -i $(git ls-files '*.py')     # format all Python files
pylint $(git ls-files '*.py')     # lint all tracked Python files (excludes tests, alembic)
pylint --ignore=tests,alembic .   # alternative
# CI: GitHub Actions workflow (.github/workflows/pylint.yml) runs pylint on push
```
- YAPF configuration in `pyproject.toml`: based on Google style, 4‑space indent, 88‑column limit
- Linting excludes `tests/` and `alembic/` directories by default
- Run `uv run yapf -i path/to/file.py` to format a single file

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
- `pytest.ini` configuration in `pyproject.toml` sets `testpaths = ["tests"]` and `pythonpath = ["."]`
- Database fixtures in `tests/conftest.py` provide `db_session` for SQLite in‑memory testing
- WebSocket tests require Redis; use `tests/ws_conn.py` factory for real connections
- Run a single test with `uv run pytest tests/path/to/test.py::test_function -v`
- Filter tests with `uv run pytest -k "pattern" -v`
- Debug logs with `uv run pytest -v --log-level=DEBUG`

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

### Comments
- Use docstrings for public modules, classes, and functions (Google style or one‑line)
- Inline comments should explain “why” rather than “what”
- Avoid unnecessary comments when code is self‑explanatory

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

### Add New Task Queue Task
1. Define a function in `mq/tasks.py` (or appropriate module)
2. Decorate with `@huey.task()` (or `@huey.periodic_task()` for scheduled tasks)
3. Ensure the function handles its own database session via `session_scope` if needed
4. The consumer picks up tasks automatically when running `uv run huey_consumer.py mq.tasks.huey`

## Ambiguous Patterns & Pitfalls

- **Redis access**: Use `get_redis()` (from `cache.connection`) NOT `redis_client.get_client()` for Redis access. The former handles connection lifecycle.
- **JSON serialization**: Prefer `orjson.loads()` and `orjson.dumps()` for performance (supports non‑string keys via `orjson.OPT_NON_STR_KEYS`). Redis hash serialization uses `to_redis_hash()`/`from_redis_hash()` methods on Pydantic schemas.
- **Database transactions**: FastAPI endpoints use `get_db` dependency yielding AsyncSession; commit/rollback handled automatically. Non‑HTTP flows (tasks, scripts) must use `session_scope` async context manager from `db.session`. CRUD functions in `db/crud.py` expect caller to manage commit.
- **Error handling in WebSocket handlers**: Raise `ValueError` for client errors; errors are sent via `ErrorMessage` schema with `ErrorEventType`. Use `asyncio.gather(..., return_exceptions=True)` for concurrent operations.
- **WebSocket event registration**: Import handler modules in `handlers/__init__.py` to trigger decorator registration; otherwise handlers won't be discovered.
- **MemoryMonitor**: Started automatically in FastAPI lifespan; logs memory usage changes ≥20 MB. Configurable with `interval` and `report_threshold_mb`. May be noisy in logs.
- **Repeat function bugs**: Watch for repeat function bugs in `connection_lifespan.py` (known issue).
- **Configuration loading order**: Config values are merged as `os.environ > .env > config.yaml`. Environment variables override YAML. Invalid config blocks startup; validation occurs in `config/settings.py`.

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

1. **Formatting**: Use yapf (`uv run yapf -i path/to/file.py`). Do NOT use `.` - venv will be included.
2. **Linting**: Use pylint (`pylint --ignore=tests,alembic .`). Excludes tests/ and alembic/ directories.

*Last updated: March 2026*

<skills_system priority="1">

## Available Skills

<!-- SKILLS_TABLE_START -->
<usage>
When users ask you to perform tasks, check if any of the available skills below can help complete the task more effectively. Skills provide specialized capabilities and domain knowledge.

How to use skills:
- Invoke: `npx openskills read <skill-name>` (run in your shell)
  - For multiple: `npx openskills read skill-one,skill-two`
- The skill content will load with detailed instructions on how to complete the task
- Base directory provided in output for resolving bundled resources (references/, scripts/, assets/)

Usage notes:
- Only use skills listed in <available_skills> below
- Do not invoke a skill that is already loaded in your context
- Each skill invocation is stateless
</usage>

<available_skills>

<skill>
<name>dev-browser</name>
<description>Browser automation with persistent page state. Use when users ask to navigate websites, fill forms, take screenshots, extract web data, test web apps, or automate browser workflows. Trigger phrases include "go to [url]", "click on", "fill out the form", "take a screenshot", "scrape", "automate", "test the website", "log into", or any browser interaction request.</description>
<location>project</location>
</skill>

<skill>
<name>gastown</name>
<description>Multi-agent orchestrator for Claude Code. Use when user mentions gastown, gas town, gt commands, bd commands, convoys, polecats, crew, rigs, slinging work, multi-agent coordination, beads, hooks, molecules, workflows, the witness, the mayor, the refinery, the deacon, dogs, escalation, or wants to run multiple AI agents on projects simultaneously. Handles installation, workspace setup, work tracking, agent lifecycle, crash recovery, and all gt/bd CLI operations.</description>
<location>project</location>
</skill>

<skill>
<name>open-source-maintainer</name>
<description>End-to-end GitHub repository maintenance for open-source projects. Use when asked to triage issues, review PRs, analyze contributor activity, generate maintenance reports, or maintain a repository. Triggers include "triage", "maintain", "review PRs", "analyze issues", "repo maintenance", "what needs attention", "open source maintenance", or any request to understand and act on GitHub issues/PRs. Supports human-in-the-loop workflows with persistent memory across sessions.</description>
<location>project</location>
</skill>

<skill>
<name>orchestration</name>
<description>Multi-agent orchestration for complex tasks. Use when tasks require parallel work, multiple agents, or sophisticated coordination. Triggers include requests for features, reviews, refactoring, testing, documentation, or any work that benefits from decomposition into parallel subtasks. This skill defines how to orchestrate work using cc-mirror tasks for persistent dependency tracking and TodoWrite for real-time session visibility.</description>
<location>project</location>
</skill>

<skill>
<name>zai-cli</name>
<description>|</description>
<location>project</location>
</skill>

</available_skills>
<!-- SKILLS_TABLE_END -->

</skills_system>
