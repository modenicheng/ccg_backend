# Audit Fix Changelog

This document records all findings from the code audit of the CCG backend (2026-05). All Critical and High-severity issues have been fixed. Additional bug fixes discovered during review are also documented below.

---

## Critical Fixes (Applied — committed to `fix/audit-critical-c1-c4`)

### C1 — SQLAlchemy Identity Check on Boolean

| Field     | Detail |
|-----------|--------|
| **Severity** | Critical |
| **File(s)**  | `db/crud/cookie_crud.py:60` |
| **Date**     | 2026-05 |

**Bug:** `is True` Python identity check was used instead of SQLAlchemy's `is_(True)` operator. This causes incorrect query semantics — the expression evaluates to `False` at the Python level rather than generating a proper SQL `IS TRUE` clause, silently skipping the filter.

**Fix:** Replaced `is True` with `.is_(True)` to produce the correct SQL `WHERE ... IS TRUE` comparison.

---

### C2 — ATTEMPT_ANSWER TOCTOU Race Condition

| Field     | Detail |
|-----------|--------|
| **Severity** | Critical |
| **File(s)**  | `handlers/round_events_3x.py:406–480`, `cache/room_cache.py` |
| **Date**     | 2026-05 |

**Bug:** The ATTEMPT_ANSWER handler performed a read-then-write pattern (check if a player already answered, then set the answerer) across two separate Redis operations. Between the read and the write, a second concurrent request could pass the check, causing multiple players to register as the answerer for the same round — a classic TOCTOU (time-of-check-time-of-use) race condition.

**Fix:** Added `try_set_room_current_answerer()` in `cache/room_cache.py` using a Redis Lua script for atomic check-and-set. The handler now uses this single atomic operation instead of separate get/set calls.

---

### C3 — Fragmented Transaction in Judge Events

| Field     | Detail |
|-----------|--------|
| **Severity** | Critical |
| **File(s)**  | `handlers/judge_events_4x.py` |
| **Date**     | 2026-05 |

**Bug:** Three logically related database operations were wrapped in three separate `session_scope()` blocks. If the second or third block failed, earlier commits persisted, leaving the database in an inconsistent partial state (e.g., score recorded but judge result not applied).

**Fix:** Consolidated all three operations into a single `session_scope()` block so they succeed or fail atomically within one transaction. Broadcasts moved to post-commit.

---

### C4 — Async Task Missing Timeout

| Field     | Detail |
|-----------|--------|
| **Severity** | Critical |
| **File(s)**  | `mq/tasks.py:166` |
| **Date**     | 2026-05 |

**Bug:** The `_run_async` helper that bridges synchronous Huey tasks to the async event loop called `future.result()` with no timeout. A hung coroutine (e.g., unresponsive external API, deadlocked await) would block the Huey worker indefinitely, eventually exhausting the task queue.

**Fix:** Added a 300-second timeout to `future.result(timeout=300)` with explicit `TimeoutError` handling. On timeout, the pending coroutine is cancelled and a `TimeoutError` is raised.

---

## High-Severity Fixes (Applied)

### H1 — CORS Wildcard → Explicit Origins

| Field     | Detail |
|-----------|--------|
| **Severity** | High |
| **File(s)**  | `main.py:121-131` |
| **Status**   | **Fixed** |

**Was:** `allow_origins=["*"]` — any origin could make cross-origin requests.

**Fix:** Replaced with explicit origin list:
- `http://localhost:5173` (Vite dev server)
- `http://localhost:8000` (backend direct)
- `https://gs.modenc.top` (production)
- `https://ccg-origin.modenc.top` (production alternate)

Added `allow_credentials=True`, `allow_methods=["*"]`, `allow_headers=["*"]`.

---

### H2 — Database Connection Pool Configuration

| Field     | Detail |
|-----------|--------|
| **Severity** | High |
| **File(s)**  | `db/session.py:18-29` |
| **Status**   | **Fixed** |

**Was:** `create_async_engine(DATABASE_URL, echo=..., future=True)` — all defaults (pool_size=5, max_overflow=10, no pool_pre_ping).

**Fix:** Added `pool_pre_ping=True` for all backends (detects stale connections). For PostgreSQL only: `pool_size=20`, `max_overflow=10`, `pool_recycle=3600`. SQLite skips pool tuning (not applicable).

---

### H3 — Missing Composite Indexes

| Field     | Detail |
|-----------|--------|
| **Severity** | High |
| **File(s)**  | `db/models.py`, `alembic/versions/a1b2c3d4e5f6_add_audit_indexes.py` |
| **Status**   | **Fixed** |

**Was:** `player_answers` queried by `(room_id, song_id, round_index)` with no composite index; `scores` queried by `(room_id, user_id)` with no index.

**Fix:** Added 4 composite indexes in `models.py`:
- `idx_player_answers_room_song_round` on `player_answers(room_id, song_id, round_index)`
- `idx_scores_room_user` on `scores(room_id, user_id)`
- `idx_song_tag_history_song` on `song_tag_history(song_id)`
- `idx_song_description_history_song` on `song_description_history(song_id)`

Alembic migration created: `a1b2c3d4e5f6_add_audit_indexes.py`. Run `uv run alembic upgrade head` to apply.

---

### H4 — File Cache Unbounded Memory → Size-Based Eviction

| Field     | Detail |
|-----------|--------|
| **Severity** | High |
| **File(s)**  | `cache/file_cache.py` |
| **Status**   | **Fixed** |

**Was:** Count-based LRU eviction only (`ASSET_CACHE_MAX_ITEMS`). With large audio files, cache could consume unbounded RAM.

**Fix:** Added `ASSET_CACHE_MAX_BYTES = 256 MB` byte limit with `_cache_total_bytes` tracking. Eviction now triggers when either count OR byte limit is exceeded. Extracted `_evict_excess()` helper. Old entry sizes are properly accounted for on replacement.

---

### H5 — Internal Error Details Leaked to Clients

| Field     | Detail |
|-----------|--------|
| **Severity** | High |
| **File(s)**  | `main.py:312`, `handlers/judge_events_4x.py:280,518`, `handlers/round_state_events.py:70` |
| **Status**   | **Fixed** |

**Was:** Exception messages (`str(e)`) included in WebSocket error responses sent to clients.

**Fix:** Removed exception details from all 4 error message locations. Full details still logged server-side via `logger.error(..., exc_info=True)`.

---

### H6 — Double Pop/Close in Disconnect

| Field     | Detail |
|-----------|--------|
| **Severity** | High |
| **File(s)**  | `main.py:317-325` (both WS endpoints) |
| **Status**   | **Fixed** |

**Was:** `finally` block called `ws.close()` + `clients_manager.pop()` before calling `on_disconnect()`, which also calls `ws.close()` + `clients.pop()`. Redundant operations producing warning logs.

**Fix:** Removed duplicate `ws.close()` and `clients_manager.pop()` from `main.py` `finally` blocks. Now delegates entirely to `on_disconnect()` which handles both in its own `finally`.

---

### H7 — Spectator Fixed `id=0` Collision

| Field     | Detail |
|-----------|--------|
| **Severity** | High |
| **File(s)**  | `main.py:345-349`, `client_manager/__init__.py:21-24,112-123` |
| **Status**   | **Fixed** |

**Was:** All spectators shared `user_id=0`. Second spectator rejected by `has_user_connection()` duplicate detection.

**Fix:** Added `is_spectator` flag to `Client` class (set via `spectator_user.is_spectator = True`). `has_user_connection()` now skips clients with `is_spectator=True`, allowing multiple spectators with `id=0`.

---

### H8 — JUDGING Missing Owner Check

| Field     | Detail |
|-----------|--------|
| **Severity** | High |
| **File(s)**  | `handlers/judge_events_4x.py:271-277` |
| **Status**   | **Fixed** |

**Was:** `@regist(GameEventType.JUDGING)` handler had no `is_owner` check. Any client could trigger judging.

**Fix:** Added `if not client.user.is_owner` guard at the top of `handle_judging()`, matching the pattern used in `handle_judge_submit()`.

---

### H9 — Audio Re-download Rate Limiting

| Field     | Detail |
|-----------|--------|
| **Severity** | High |
| **File(s)**  | `handlers/audio_error_handler.py:20-21,37-48` |
| **Status**   | **Fixed** |

**Was:** Any client could trigger unlimited `download_and_cache_song()` tasks by sending ERROR events.

**Fix:** Added per-room 30-second cooldown using `_last_redownload` dict. No auth check (intentional — other clients may need re-download due to audio expiry). Rate-limited requests return early with a log message.

---

### H10 — N+1 Query Optimization

| Field     | Detail |
|-----------|--------|
| **Severity** | High |
| **File(s)**  | `handlers/judge_events_4x.py:190-201`, `db/crud/judge_related.py:134-159` |
| **Status**   | **Fixed** |

**Fix 1 — `broadcast_judging_event` username lookup:** Replaced per-player `SELECT username WHERE id = ?` loop with a single batch `SELECT id, username WHERE id IN (...)` query. Results stored in `username_map` dict.

**Fix 2 — `update_player_answer_order`:** Replaced per-player `SELECT ... WHERE user_id = ?` loop with a single batch fetch of all `PlayerAnswer` rows for the round. Builds `user_id -> latest_answer` lookup in Python, then updates in a single loop + flush.

---

## Bug Fixes (Applied during review)

### B1 — Auto-Judging Triggered Answer Broadcast

| Field     | Detail |
|-----------|--------|
| **Severity** | Bug |
| **File(s)**  | `handlers/round_events_3x.py:643–652` |
| **Date**     | 2026-05 |

**Bug:** When all players finished answering (`should_finish_answering = True`), `handle_submit_answer` automatically called `broadcast_judging_event()`, which broadcasts all player answers (JUDGING event) to everyone. This violated the design requirement that answers should only be displayed when the owner explicitly triggers judging (DESIGN.md §5.5 step 7).

**Scenario:** Owner buzzes in → submits answer → no more players in queue → auto-judge fires → all answers broadcast to everyone. This was especially problematic because the owner could be the only answerer.

**Fix:** Removed the auto-trigger of `broadcast_judging_event` from `handle_submit_answer`. After answering finishes, the handler now only logs and waits. The owner must manually send JUDGING (event 40) to trigger judging and display answers.

---

### B2 — JUDGING → PLAYING_AUDIO State Transition Blocked

| Field     | Detail |
|-----------|--------|
| **Severity** | Bug |
| **File(s)**  | `room_state/state_machine.py:131` |
| **Date**     | 2026-05 |

**Bug:** The `RoundStateMachine` did not allow `JUDGING → PLAYING_AUDIO` transition. When the frontend attempted to force-next-round (via `ROUND_STATE_UPDATE` event 45 or `SKIP_ROUND` event 43) while the room was in JUDGING state, the transition was rejected with "Failed to transition to PLAYING_AUDIO".

**Fix:** Added `RoundState.PLAYING_AUDIO` to the allowed transitions from `RoundState.JUDGING`. This enables skipping the rest of the judging phase to start a new round. Full transition rules after fix:

```
PENDING      → [PLAYING_AUDIO]
PLAYING_AUDIO → [ANSWERING, COMPLETED]
ANSWERING    → [PLAYING_AUDIO, JUDGING, COMPLETED]
JUDGING      → [COMPLETED, PLAYING_AUDIO]   ← added PLAYING_AUDIO
COMPLETED    → [PENDING, PLAYING_AUDIO]
```

---

### B3 — WebSocket Auth Failure Lacks Diagnostic Detail

| Field     | Detail |
|-----------|--------|
| **Severity** | Diagnostic |
| **File(s)**  | `db/crud/room_song_related.py:416–437` |
| **Date**     | 2026-05 |

**Bug:** `authenticate_user_by_room_token` queries `WHERE id = ? AND token = ? AND room_id = ?`. When the query returns no match, the original warning only logged `user_id` — making it impossible to tell which condition failed (user deleted? token mismatch? room mismatch?).

**Fix:** Added per-field diagnostic logging: checks whether the user exists in DB first, then (if exists) compares token and room_id individually. Logs the specific mismatch cause for efficient debugging.

---

## Remaining Low-Severity Issues (Not Fixed)

| # | Issue | File | Notes |
|---|-------|------|-------|
| L1 | `assert` in production code | `db/crud/task_related.py:46` | Replace with `if ... raise RuntimeError` |
| L2 | Inconsistent logger | `db/crud/cookie_crud.py:14` | Uses `logging.getLogger` instead of `get_logger` |
| L3 | `clear_room_songs` per-row DELETE | `db/crud/room_song_related.py:297` | Use bulk DELETE |
| L4 | `int(user_id)` no error handling | `db/crud/room_song_related.py:375` | Validate or try/except |
| L5 | `del client` no-op | `client_manager/__init__.py:300` | Remove misleading line |
| L6 | Dead commented-out code | `handlers/round_events_3x.py:490-513` | Remove or move to VCS |
| L7 | Class-level `uid` default | `utils/dataframe.py:169` | Move to `__init__` |
| L8 | Class-level `timestamp` default | `utils/dataframe.py:49` | Move to `__init__` |
| L9 | `sys.argv` huey detection | `mq/tasks.py:321` | Use env var |
| L10 | `json.loads` vs `orjson` | `config/settings.py:134` | Use `orjson` for consistency |

---

## Second Round Audit Fixes (Applied — 2026-05-02)

16 files changed, +407 / -168 lines. Branch: `fix/audit-critical-c1-c4` (same branch, continued).

---

### CRIT-1 — `on_disconnect` Self-Match Bug

| Field     | Detail |
|-----------|--------|
| **Severity** | Critical |
| **File(s)**  | `handlers/connection_lifespan.py:247-248` |
| **Status**   | **Fixed** |

**Bug:** `on_disconnect` checked `any(other.user.id == cl.user.id for other in clients.get_clients(room_id))` to detect if the user had other active sessions. But `cl` was still in the clients set (not yet popped — that happens in the `finally` block at line 312). The check always matched `cl` itself, so `has_other_active_session` was always `True`, and the user was **never marked offline or broadcast as leaving**.

**Fix:** Added `and other is not cl` to the generator expression to exclude the disconnecting client from the self-match check.

---

### CRIT-2 — `set_room_playback_progress` Missing TTL Refresh

| Field     | Detail |
|-----------|--------|
| **Severity** | Critical |
| **File(s)**  | `cache/room_cache.py:242-263` |
| **Status**   | **Fixed** |

**Bug:** Unlike `set_room_playback_state` which called `redis.expire(key, ROOM_TTL_SECONDS)` after updating the hash, `set_room_playback_progress` only did `hset` without refreshing TTL. A room running for >6 hours would lose its playback state key mid-session even though progress was being continuously updated.

**Fix:** Added `redis.expire(key, ROOM_TTL_SECONDS)` after the `hset` call in `set_room_playback_progress`, matching the pattern in `set_room_playback_state`.

---

### CRIT-3 — Redis State Written Before DB Commit in Shuffle

| Field     | Detail |
|-----------|--------|
| **Severity** | Critical |
| **File(s)**  | `router/room_songs.py:355-367` |
| **Status**   | **Fixed** |

**Bug:** In `shuffle_room_songs_list`, the Redis playback state was written **before** the DB commit:
```python
await crud.shuffle_room_songs(session, roomid)      # DB change (uncommitted)
await _refresh_default_playback_initial_song(...)    # Writes to Redis
await session.commit()                               # DB commit
```
If `session.commit()` failed, Redis would have a playback state pointing to a song order that doesn't exist in the DB.

**Fix:** Split into two try blocks: first does `crud.shuffle_room_songs` + `session.commit()`, second does `_refresh_default_playback_initial_song` + `_trigger_preload_top_songs`.

---

### CRIT-4 — Redis Connection Creation Race Condition

| Field     | Detail |
|-----------|--------|
| **Severity** | Critical |
| **File(s)**  | `cache/connection.py:30-54,67-77` |
| **Status**   | **Fixed** |

**Bug:** Two coroutines calling `get_client()` simultaneously when `connected=False` would both enter `connect()`, create two separate `Redis` instances via `from_url()`, and both ping. The second to complete would overwrite `self.client`, orphaning the first connection.

**Fix:** Added `asyncio.Lock` (`_connect_lock`) to `RedisClient` class. `get_client()` now uses a double-checked locking pattern — checks `self.connected and self.client` before acquiring lock, re-checks after.

---

### CRIT-5 — Corrupt Partial Files After Download Failure

| Field     | Detail |
|-----------|--------|
| **Severity** | Critical |
| **File(s)**  | `mq/tasks.py:404-428` |
| **Status**   | **Fixed** |

**Bug:** Audio downloads wrote directly to the final path. If the disk was full or write failed mid-stream, a corrupt partial file was left on disk. The next run would see `os.path.exists(path)` and skip the download, serving the corrupt file.

**Fix:** Rewrote `_download_audio_file_impl` to: (1) write to a temp file with `.tmp` suffix, (2) use `os.replace()` for atomic move on success, (3) clean up the temp file on failure. Also added `httpx.Timeout(connect=10, read=60, write=30, pool=10)` (addressing HIGH-7).

---

### CRIT-6 — No Authentication on Song/Songlist/Tag/Audio CRUD Endpoints

| Field     | Detail |
|-----------|--------|
| **Severity** | Critical |
| **File(s)**  | `router/song.py`, `router/songlist.py`, `router/tags.py`, `router/audio_stream.py`, `db/crud/room_song_related.py`, `db/crud/__init__.py` |
| **Status**   | **Fixed** |

**Bug:** 15+ HTTP endpoints for creating/modifying/deleting songs, songlists, tags, and tag groups had **no authentication**. Any client could modify the global library, trigger mass downloads, or delete data. Compare with `room_songs.py` which correctly called `_require_room_owner`.

**Fix:**
1. Added `authenticate_user_global(session, token, user_id)` in `db/crud/room_song_related.py:486-498` — validates `User.id + User.token` without room constraint.
2. Added `_require_auth(request, session)` FastAPI dependency in each affected router.
3. Applied `Depends(_require_auth)` to all write endpoints:
   - Song: `POST /`, `PUT /{song_id}`, `DELETE /{song_id}`, `GET /cache/{song_id}`, `POST /cache/{song_id}`
   - Songlist: `POST /`, `PUT /{songlist_id}`, `DELETE /{songlist_id}`
   - Tags: `POST /`, `PATCH /{tag_id}`, `DELETE /{tag_id}`, `POST /groups/`, `PATCH /groups/`, `DELETE /groups/{group_id}`
   - Audio: `GET /file/{song_id}`

---

### CRIT-7 — `handle_submit_answer` TOCTOU in Next-Player Selection

| Field     | Detail |
|-----------|--------|
| **Severity** | Critical |
| **File(s)**  | `handlers/round_events_3x.py:589-630`, `cache/room_cache.py:564-610` |
| **Status**   | **Fixed** |

**Bug:** After a player submitted an answer, the handler read the answer queue, found the current player's index, and picked the next player using a plain `set_room_current_answerer` (HSET). Between reading the queue and setting the next answerer, a concurrent `ATTEMPT_ANSWER` handler could modify the queue, causing the next player chosen to be stale or wrong.

**Fix:**
1. Added `_TRANSITION_ANSWERER_SCRIPT` Lua script to `cache/room_cache.py` — atomically checks `current == from_player` then sets `to_player` (or deletes the key if empty).
2. Added `transition_room_current_answerer(redis, room_id, from_player_id, to_player_id | None)` function wrapped with `@handle_redis_operation`.
3. Updated `handle_submit_answer` to use `transition_room_current_answerer` instead of separate `set`/`clear` calls. Both branches now check the CAS return value — if `False`, logs and returns early.

---

### HIGH-1 — Exception Detail Leaks in HTTP + WS Responses

| Field     | Detail |
|-----------|--------|
| **Severity** | High |
| **File(s)**  | `main.py:272`, `router/room.py` (4), `router/room_songs.py` (5), `router/song.py` (1), `router/songlist.py` (1), `handlers/round_events_3x.py` (1), `handlers/round_state_events.py` (1) |
| **Status**   | **Fixed** |

**Bug:** 12+ HTTP endpoints and 2 WS handlers included `str(e)` or `str(exc)` in error responses sent to clients. This leaked internal details (DB constraint names, file paths, Python tracebacks) to clients.

**Fix:** Replaced all `detail=f"...: {str(e)}"` patterns with generic messages (e.g., `"Failed to dissolve room"`). Full details still logged server-side with `logger.error(..., exc_info=True)`.

Locations fixed:
- `main.py:272`: `f"Invalid JSON payload: {exc}"` → `"Invalid JSON payload"`
- `router/room.py:455,570,711,840`: 4 broadcast/dissolve error locations
- `router/room_songs.py:218,254,305,335,372`: 5 CRUD error locations
- `router/song.py:115`: song creation error
- `router/songlist.py:135`: task record creation error
- `handlers/round_events_3x.py:571`: answer save error
- `handlers/round_state_events.py:121`: round state update error

---

### HIGH-2 — Missing Round-State Validation in Answer Handlers

| Field     | Detail |
|-----------|--------|
| **Severity** | High |
| **File(s)**  | `handlers/round_events_3x.py:337-345,540-548` |
| **Status**   | **Fixed** |

**Bug:** `handle_attempt_answer` and `handle_submit_answer` checked `current_answerer` but **never validated that the round was in the correct state**. A malicious or buggy client could send `ATTEMPT_ANSWER` during `PLAYING_AUDIO` or `SUBMIT_ANSWER` during `JUDGING`/`COMPLETED`, corrupting game state.

**Fix:** Added `session_scope` + `RoundStateManager.get_round_state` checks:
- `handle_attempt_answer`: only allows attempts when `round_state in (RoundState.PLAYING_AUDIO, RoundState.ANSWERING)`
- `handle_submit_answer`: only allows submissions when `round_state == RoundState.ANSWERING`

---

### HIGH-6 — File Cache Removed, Switched to Disk Streaming

| Field     | Detail |
|-----------|--------|
| **Severity** | High |
| **File(s)**  | `cache/file_cache.py`, `router/audio_stream.py`, `router/song.py` |
| **Status**   | **Fixed** |

**Bug:** The in-memory LRU cache (`cache/file_cache.py`) loaded entire audio files into `bytes` objects (up to 256MB total). A single large file could nearly exhaust the budget. `build_range_response` in `utils/http_utils.py` required the full file content even for small range requests.

**Fix:** Replaced the entire in-memory cache with `fastapi.responses.FileResponse` for direct disk streaming:
- `cache/file_cache.py`: Rewrote to a single `build_file_response(path, content_disposition=None)` function. No more LRU cache, no more memory usage. `FileResponse` handles Range requests natively via sendfile/streaming.
- `router/audio_stream.py`: Updated `stream_audio` and `get_audio_file` to use `build_file_response`. Removed `load_song_asset_with_cache`, `build_range_response`, and unused `request` parameter.
- `router/song.py`: Updated `get_song_asset` to use `build_file_response`.
- `build_range_response` in `utils/http_utils.py` is now dead code.

---

### HIGH-7 — httpx Missing Timeout

| Field     | Detail |
|-----------|--------|
| **Severity** | High |
| **File(s)**  | `mq/tasks.py:414` |
| **Status**   | **Fixed** (merged into CRIT-5) |

**Bug:** `httpx.AsyncClient()` was created without `timeout=`. A slow/hanging remote server would block the Huey worker until `_run_async`'s 300s timeout fired.

**Fix:** Added `httpx.Timeout(connect=10, read=60, write=30, pool=10)` when creating the client.

---

### HIGH-10 — PostgreSQL-Only SQL Crashes SQLite

| Field     | Detail |
|-----------|--------|
| **Severity** | High |
| **File(s)**  | `router/tags.py:7,189-190` |
| **Status**   | **Fixed** |

**Bug:** `from sqlalchemy.dialects.postgresql import insert` followed by `insert(Tag).values([...]).on_conflict_do_nothing(index_elements=["name"])` is PostgreSQL-specific. SQLite-based tests and any SQLite deployment would crash.

**Fix:** Removed the PostgreSQL-specific import. Replaced `_create_tags` function's `insert().on_conflict_do_nothing()` with a portable approach: iterate tag names, use `session.begin_nested()` (SAVEPOINT) for each insert, catch `IntegrityError` for concurrent race conditions.

---

### MED-10→HIGH — N+1 Queries in `batch_update_room_song_order`

| Field     | Detail |
|-----------|--------|
| **Severity** | High (upgraded from Medium) |
| **File(s)**  | `db/crud/room_song_related.py:332-373`, `router/room_songs.py:278-292`, `db/crud/__init__.py` |
| **Status**   | **Fixed** |

**Bug:** Validation loop called `crud.get_room_song()` once per item (N queries), then the update loop called `crud.update_room_song_order()` once per item (N more queries).

**Fix:**
1. Added `get_room_songs_by_ids(session, room_id, song_ids)` — single `WHERE song_id IN (...)` query returning `dict[song_id, RoomSong]`.
2. Added `batch_update_room_song_orders(session, room_id, orders)` — single batch update setting `song_order` for all songs at once.
3. Refactored the router validation+update loop to use batch queries. Missing songs now reported as a list.

---

### Summary of All Second-Round Changes

| Fix | ID | Description | Files |
|-----|----|-------------|-------|
| CRIT-1 | `on_disconnect` self-match | `and other is not cl` guard | `handlers/connection_lifespan.py` |
| CRIT-2 | Missing TTL refresh | Added `expire()` to `set_room_playback_progress` | `cache/room_cache.py` |
| CRIT-3 | Redis before DB commit | Split into two try blocks, commit first | `router/room_songs.py` |
| CRIT-4 | Redis connection race | `asyncio.Lock` double-checked locking | `cache/connection.py` |
| CRIT-5 | Corrupt partial files | Temp file + `os.replace()` + cleanup | `mq/tasks.py` |
| CRIT-6 | No auth on CRUD endpoints | `_require_auth` dependency + `authenticate_user_global` | 6 files |
| CRIT-7 | Submit answer TOCTOU | Lua CAS `transition_room_current_answerer` | 2 files |
| HIGH-1 | Exception detail leaks | Generic error messages in 14 locations | 8 files |
| HIGH-2 | Round state validation | `get_round_state` checks in answer handlers | `handlers/round_events_3x.py` |
| HIGH-6 | File cache → streaming | `FileResponse` replaces in-memory LRU | 3 files |
| HIGH-7 | httpx timeout | `Timeout(connect=10, read=60, ...)` | `mq/tasks.py` |
| HIGH-10 | PostgreSQL-only SQL | Portable SAVEPOINT + IntegrityError | `router/tags.py` |
| MED→HIGH | N+1 batch queries | Batch `WHERE IN` + batch update | 2 files |
