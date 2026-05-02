# Audit Fix Changelog

This document records all critical and high-severity findings from the code audit of the CCG backend (2026-05). Critical issues have been fixed; high-severity issues are documented as known/intentional with future remediation notes.

---

## Critical Fixes (Applied)

### C1 — SQLAlchemy Identity Check on Boolean

| Field     | Detail |
|-----------|--------|
| **Severity** | Critical |
| **File(s)**  | `db/cookie_crud.py:60` |
| **Date**     | 2026-05 |

**Bug:** `is True` Python identity check was used instead of SQLAlchemy's `is_(True)` operator. This causes incorrect query semantics — the expression evaluates to `False` at the Python level rather than generating a proper SQL `IS TRUE` clause, silently skipping the filter.

**Fix:** Replaced `is True` with `.is_(True)` to produce the correct SQL `WHERE ... IS TRUE` comparison.

---

### C2 — ATTEMPT_ANSWER TOCTOU Race Condition

| Field     | Detail |
|-----------|--------|
| **Severity** | Critical |
| **File(s)**  | `handlers/round_events_3x.py:406–480` |
| **Date**     | 2026-05 |

**Bug:** The ATTEMPT_ANSWER handler performed a read-then-write pattern (check if a player already answered, then set the answerer) across two separate Redis operations. Between the read and the write, a second concurrent request could pass the check, causing multiple players to register as the answerer for the same round — a classic TOCTOU (time-of-check-time-of-use) race condition.

**Fix:** Replaced the multi-step check-and-set with an atomic Redis Lua script exposed via `try_set_room_current_answerer`. This guarantees the read, conditional check, and write happen as a single atomic operation.

---

### C3 — Fragmented Transaction in Judge Events

| Field     | Detail |
|-----------|--------|
| **Severity** | Critical |
| **File(s)**  | `handlers/judge_events_4x.py` |
| **Date**     | 2026-05 |

**Bug:** Three logically related database operations were wrapped in three separate `session_scope()` blocks. If the second or third block failed, earlier commits persisted, leaving the database in an inconsistent partial state (e.g., score recorded but judge result not applied).

**Fix:** Consolidated all three operations into a single `session_scope()` block so they succeed or fail atomically within one transaction.

---

### C4 — Async Task Missing Timeout

| Field     | Detail |
|-----------|--------|
| **Severity** | Critical |
| **File(s)**  | `mq/tasks.py:166` |
| **Date**     | 2026-05 |

**Bug:** The `_run_async` helper that bridges synchronous Huey tasks to the async event loop called `asyncio.run()` (or equivalent) with no timeout. A hung coroutine (e.g., unresponsive external API, deadlocked await) would block the Huey worker indefinitely, eventually exhausting the task queue.

**Fix:** Added a 300-second timeout to the async execution with explicit `TimeoutError` handling. On timeout, the task is logged and marked failed, allowing the worker to recover.

---

## High-Severity Issues (Known / Intentional — Not Yet Fixed)

### H1 — CORS Wildcard `allow_origins=["*"]`

| Field     | Detail |
|-----------|--------|
| **Severity** | High |
| **File(s)**  | `main.py` |
| **Status**   | Intentional |

The application uses `allow_origins=["*"]` for CORS. This is acceptable for a LAN party game where clients connect over local network and origin restrictions would impede usability. Should be scoped to known origins if deployed to a public endpoint.

---

### H2 — Database Connection Pool Defaults

| Field     | Detail |
|-----------|--------|
| **Severity** | High |
| **File(s)**  | `db/session.py` |
| **Status**   | Noted for production tuning |

SQLAlchemy connection pool uses default `pool_size` and `max_overflow` values. Under sustained load these defaults may cause connection starvation. Tune `pool_size`, `max_overflow`, and `pool_timeout` based on expected concurrency and database limits before production scaling.

---

### H3 — Missing Composite Indexes

| Field     | Detail |
|-----------|--------|
| **Severity** | High |
| **File(s)**  | `db/models.py` (player_answers, scores tables) |
| **Status**   | Noted for migration |

Queries on `player_answers` and `scores` frequently filter by composite keys (e.g., room + round + player) but lack corresponding composite indexes. This causes full table scans under load. A migration adding targeted composite indexes is recommended.

---

### H4 — File Cache Unbounded Memory

| Field     | Detail |
|-----------|--------|
| **Severity** | High |
| **File(s)**  | `cache/` (file cache implementation) |
| **Status**   | Noted for size-based eviction |

The in-memory LRU file cache (for audio assets) has no upper bound on total memory consumption. If the asset set grows large, cache eviction should be based on total byte size rather than item count alone. Consider implementing size-based eviction with a configurable memory ceiling.

---

### H5 — Internal Error Details Leaked to WebSocket Clients

| Field     | Detail |
|-----------|--------|
| **Severity** | High |
| **File(s)**  | `handlers/` (WebSocket error handling) |
| **Status**   | Noted for generic messages |

WebSocket error messages include internal exception details (stack traces, class names) sent to clients. In production, these should be replaced with generic error messages to avoid information disclosure. Log full details server-side; send only safe, user-facing messages.

---

### H6 — Double Pop/Close in Disconnect

| Field     | Detail |
|-----------|--------|
| **Severity** | High |
| **File(s)**  | `handlers/connection_lifespan.py` |
| **Status**   | Noted for cleanup |

The disconnect handler may attempt to pop a player and close a WebSocket that has already been removed/closed by a prior event (e.g., kick, timeout). This can produce no-op errors or double-free warnings. Add idempotency guards before cleanup operations.

---

### H7 — Spectator Fixed `id=0` Collision

| Field     | Detail |
|-----------|--------|
| **Severity** | High |
| **File(s)**  | Spectator connection handling |
| **Status**   | Noted for unique ID generation |

Spectators are assigned a hardcoded `id=0`. If multiple spectators connect, or if a player has `id=0`, collisions occur in room state and scoring. Generate unique IDs for spectators using UUID or a separate ID namespace.

---

### H8 — JUDGING Missing Owner Check

| Field     | Detail |
|-----------|--------|
| **Severity** | High |
| **File(s)**  | `handlers/judge_events_4x.py` |
| **Status**   | Noted for auth |

The JUDGING event handler does not verify that the sender is the room owner/judge. Any connected client can submit judging decisions. Add an ownership or role check before processing judge events.

---

### H9 — Audio Re-download No Rate Limiting

| Field     | Detail |
|-----------|--------|
| **Severity** | High |
| **File(s)**  | `mq/tasks.py` (audio download tasks) |
| **Status**   | Noted for rate limit |

Failed audio downloads can be retried without rate limiting, potentially causing excessive requests to the QQ Music API. Implement exponential backoff or a per-resource cooldown to prevent API throttling or bans.

---

### H10 — N+1 Query in `update_player_answer_order`

| Field     | Detail |
|-----------|--------|
| **Severity** | High |
| **File(s)**  | `db/crud.py` (`update_player_answer_order`) |
| **Status**   | Noted for bulk query |

`update_player_answer_order` issues individual queries/updates per player answer. With many players this produces N+1 queries. Refactor to use bulk operations (`UPDATE ... WHERE id IN (...)`) or a single multi-row update to reduce round trips.
