# CCG Backend — Management API Documentation

> Auto-generated from codebase analysis (May 2026).
> All endpoints are under `/api/` prefix.

---

## Table of Contents

1. [Room Management](#1-room-management)
2. [Room Songs Management](#2-room-songs-management)
3. [Song Management](#3-song-management)
4. [Songlist Management](#4-songlist-management)
5. [Tag & TagGroup Management](#5-tag--taggroup-management)
6. [Audio Streaming](#6-audio-streaming)
7. [Authentication](#7-authentication)
8. [Changes from Fix Branches](#8-changes-from-fix-branches)

---

## 1. Room Management

**Router:** `router/room.py` — Prefix: `/api/room`

### Public Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/room/` | Create a new room |
| `GET` | `/api/room/{roomid}` | Get room info |
| `POST` | `/api/room/{roomid}/join` | Join an existing room |
| `POST` | `/api/room/{roomid}/dissolve` | Dissolve a room |
| `POST` | `/api/room/{roomid}/auto-setup-test-audio` | Auto-setup test audio |
| `POST` | `/api/room/{roomid}/set-test-audio` | Set specific test audio |
| `PATCH` | `/api/room/{roomid}` | Update room settings (title, description, tags, etc.) |

### Schemas

**`CreateRoomRequest`**:
```json
{
  "title": "string",
  "host_name": "string"
}
```

**`CreateRoomResponse`**:
```json
{
  "room_id": "string",
  "host": { "id": "string", "token": "string", "username": "string", "is_owner": true }
}
```

**`RoomInfoResponse`**:
```json
{
  "room_id": "string",
  "host_player_id": "string",
  "status": "string",
  "title": "string",
  "players": [],
  "tag_groups": []
}
```

**`PatchRoomRequest`** (all fields optional):
```json
{
  "song_queue": [],
  "title": "string",
  "description": "string",
  "tag_group_ids": [],
  "tag_groups": []
}
```

**`JoinRoomRequest`**:
```json
{ "username": "string" }
```

**`JoinRoomResponse`**:
```json
{
  "room_id": "string",
  "user": { "id": "string", "token": "string", "username": "string", "is_owner": true }
}
```

### Error Handling (H5 fix)

All error responses use generic messages — internal exceptions are no longer leaked to clients:
```json
{ "detail": "Failed to <action>" }
```

---

## 2. Room Songs Management

**Router:** `router/room_songs.py` — Prefix: `/api/room/{roomid}/songs`

All endpoints require **room-owner authentication** via `_require_room_owner`.

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/room/{roomid}/songs/` | Add songs to room queue |
| `DELETE` | `/api/room/{roomid}/songs/` | Remove songs from room queue |
| `PATCH` | `/api/room/{roomid}/songs/order` | Batch update song order |
| `POST` | `/api/room/{roomid}/songs/shuffle` | Shuffle room song list |
| `DELETE` | `/api/room/{roomid}/songs/all` | Clear all songs from room |

### Schemas

**`AddRoomSongsRequest`**:
```json
{ "song_ids": ["string"] }
```

**`RemoveRoomSongsRequest`**:
```json
{ "song_ids": ["string"] }
```

**`BatchUpdateRoomSongOrderRequest`**:
```json
{ "items": [{ "song_id": "string", "order": 0 }] }
```

### Performance (H10 fix)

- `batch_update_room_song_order` uses a single batch query (`get_room_songs_by_ids` + `batch_update_room_song_orders`) instead of N+1 individual lookups.

### Error Handling

- All error responses use generic detail messages (H5).
- Shuffle separates DB commit from post-commit operations to prevent rollback issues.

---

## 3. Song Management

**Router:** `router/song.py` — Prefix: `/api/songs`

### Public Endpoints (no auth required)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/songs/` | List all songs |
| `GET` | `/api/songs/{song_id}` | Get a specific song |

### Authenticated Endpoints (require `_require_auth`)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/songs/` | Create a new song |
| `PUT` | `/api/songs/{song_id}` | Update a song |
| `DELETE` | `/api/songs/{song_id}` | Delete a song |
| `GET` | `/api/songs/cache/{song_id}` | Get cached song data |
| `POST` | `/api/songs/cache/{song_id}` | Cache song data |

Auth uses query parameters: `?token=xxx&user_id=yyy`

### Schemas

**`SongCreate`** (defined inline in router):
```json
{
  "mid": "string",
  "name": "string",
  "artist": "string",
  "album": "string",
  "duration": 0,
  ...
}
```

**`SongResponse`**:
```json
{
  "id": "string",
  "mid": "string",
  "name": "string",
  "artist": "string",
  ...
}
```

---

## 4. Songlist Management

**Router:** `router/songlist.py` — Prefix: `/api/songlists`

### Public Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/songlists/` | List all songlists |
| `GET` | `/api/songlists/{songlist_id}` | Get a specific songlist |
| `POST` | `/api/songlists/fetch-from-platform` | Fetch songlist from platform |

### Authenticated Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/songlists/` | Create a new songlist |
| `PUT` | `/api/songlists/{songlist_id}` | Update a songlist |
| `DELETE` | `/api/songlists/{songlist_id}` | Delete a songlist |

### Schemas

**`SonglistBase`** / **`SonglistResponse`**:
```json
{
  "id": "string",
  "name": "string",
  "description": "string",
  "songs": []
}
```

**`SonglistFromMidRequest`**:
```json
{ "mid": "string" }
```

---

## 5. Tag & TagGroup Management

**Router:** `router/tags.py` — Prefix: `/api/tags`

### Public Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/tags/` | List all tags |
| `GET` | `/api/tags/groups/` | List all tag groups |

### Authenticated Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/tags/` | Create tags |
| `PATCH` | `/api/tags/{tag_id}` | Update a tag |
| `DELETE` | `/api/tags/{tag_id}` | Delete a tag |
| `POST` | `/api/tags/groups/` | Create a tag group |
| `PATCH` | `/api/tags/groups/` | Update tag groups |
| `DELETE` | `/api/tags/groups/{group_id}` | Delete a tag group |

### Schemas

**`TagsCreateRequest`**:
```json
{ "tags": [{ "name": "string", "group_id": "string" }] }
```

**`TagPatch`**:
```json
{ "name": "string" }
```

**`TagGroupCreate`**:
```json
{ "name": "string", "description": "string" }
```

**`TagGroupPatch`**:
```json
{ "name": "string", "description": "string" }
```

### Portability Fix

- Replaced PostgreSQL-specific `insert().on_conflict_do_nothing()` with portable SAVEPOINT pattern (`session.begin_nested()` + `IntegrityError` catch) for cross-database compatibility (SQLite in dev, PostgreSQL in prod).

---

## 6. Audio Streaming

**Router:** `router/audio_stream.py` — Prefix: `/api/songs`

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/songs/file/{song_id}` | Stream audio file (no auth required) |
| `GET` | `/api/songs/stream/token/{token}` | Stream via token-based auth |

### Changes

- Removed in-memory LRU cache (`load_song_asset_with_cache`).
- Now uses `build_file_response(path)` which returns a `FileResponse` for disk streaming — files are NOT loaded into memory.
- Removed `Range` header / partial response logic.
- File endpoint no longer requires authentication.

---

## 7. Authentication

Two authentication patterns are used across the API:

### 1. Room-Scoped Auth (`_require_room_owner`)

Used by room-related endpoints (room songs, room settings).

```
GET /api/room/{roomid}/songs/?token=xxx&user_id=yyy
```

Calls `authenticate_user_for_room_http(session, roomid, query_params, cookies)` → verifies `is_owner`.

### 2. Global Auth (`_require_auth`)

Used by song, songlist, and tag management endpoints.

```
POST /api/songs/?token=xxx&user_id=yyy
```

Calls `authenticate_user_global(session, token, user_id)` from `db.crud`.

Both patterns extract credentials from **query parameters** (`token` + `user_id`).

---

## 8. Changes from Fix Branches

### fix/show-answer-sync (WebSocket only)

No HTTP API changes. Modified WebSocket answer-sync behavior in:
- `handlers/audio_events_2x.py`
- `handlers/judge_events_4x.py`
- `cache/room_cache.py`
- `schemas/ws_messages/room_schemas.py`

### fix/audit-critical-c1-c4 (36 files, +1374/-399)

#### HTTP API Changes (Router Layer)

| Router | Changes |
|--------|---------|
| `room.py` | Error detail leaks removed from `set_test_audio`, `dissolve_room`, and broadcast helpers (H5) |
| `room_songs.py` | Error detail leaks removed (H5); batch queries replace N+1 loops in `batch_update_room_song_order` (H10); shuffle commit/rollback safety |
| `song.py` | Added `_require_auth` to write endpoints (POST/PUT/DELETE/cache); error detail leaks removed; file streaming replaces in-memory cache |
| `songlist.py` | Added `_require_auth` to write endpoints; error detail leaks removed |
| `tags.py` | Added `_require_auth` to all write endpoints; portable SAVEPOINT pattern replaces `on_conflict_do_nothing()` |
| `audio_stream.py` | Removed in-memory LRU cache; uses `build_file_response()` for disk streaming; auth removed from file endpoint |

#### Non-Router Changes

| Component | Changes |
|-----------|---------|
| `main.py` | CORS wildcard → explicit origins (H1); WS error leaks removed (H5); double disconnect fixed (H6); spectator flag (H7) |
| `handlers/round_events_3x.py` | Atomic Lua CAS for `ATTEMPT_ANSWER` (C2) |
| `handlers/judge_events_4x.py` | Single transaction (C3); owner check for JUDGING (H8); batch username fetch (H10) |
| `db/session.py` | Connection pool settings (H2) |
| `db/models.py` | Index definitions (H3) |
| `cache/room_cache.py` | Atomic CAS Lua scripts |
| `mq/tasks.py` | Timeout for `_run_async` (C4); download rate limiting (H9) |

#### Fix Categories

- **C1–C4 (Critical)**: SQLAlchemy `is True` fix, ATTEMPT_ANSWER TOCTOU, judge transaction atomicity, async task timeout
- **H1–H10 (High)**: CORS origins, DB pool tuning, DB indexes, file cache limit, error detail leaks, double disconnect, spectator flag, judge auth, download rate limit, N+1 queries

For full details, see `docs/AUDIT_FIXES.md`.
