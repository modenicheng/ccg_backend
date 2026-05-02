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
