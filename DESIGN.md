# DESIGN.md — Centralized TODO App ("Taskflow") — Implementation Contract v1

Status: APPROVED design contract (2026-09-02). Derived from `SPEC.md` (APPROVED v1).
This document is the single source of truth for the backend and frontend implementation agents.
Where this document and SPEC.md disagree, this document wins; where both are silent, implement the
simplest reasonable thing and note it. **Every decision below is deliberate — do not "improve" it.**

Product name: **Taskflow**. Project root: `/home/hermes/projects/todo-app`.

---

## 1. High-level architecture

### 1.1 Stack (fixed, non-negotiable)

| Concern | Choice |
|---|---|
| Backend | FastAPI (Python 3.13), one process, `uvicorn` server |
| Persistence | SQLite via **stdlib `sqlite3`** — no ORM, no SQLAlchemy, no aiosqlite |
| Validation | Pydantic v2 models (FastAPI default), configured to emit string `detail` |
| Frontend | **Plain vanilla HTML/CSS/JS** — single `static/index.html`, `static/app.js`, `static/style.css`. No frameworks, no build step, no bundler, no CDN, no external fonts/icons. All icons are inline SVG. |
| PWA | `static/manifest.webmanifest`, `static/sw.js`, Pillow-generated PNG icons in `static/icons/` |
| venv | `/home/hermes/projects/todo-app/.venv` (created with `uv`) |
| API root | `/api` — every API route path starts with `/api` |
| SPA root | `/` serves `static/index.html`; `/share/<token>` serves the same shell (client-side routing) |

### 1.2 Runtime topology

```
Browser (SPA, PWA-capable)
   │  fetch JSON (same origin)
   ▼
uvicorn → FastAPI app (app.main:app)
   │  sync endpoints run in FastAPI's threadpool
   ▼
stdlib sqlite3 (one connection per request, WAL mode)
   ▼
todo.db  (default: <project root>/todo.db, override with env TODO_DB)
```

- **One SQLite connection per request**, opened in a FastAPI dependency, committed on success,
  rolled back on exception, always closed. `PRAGMA foreign_keys = ON` and
  `PRAGMA busy_timeout = 5000` on **every** connection (FK enforcement is per-connection in SQLite).
- Schema is created idempotently (`CREATE TABLE IF NOT EXISTS ...`) at app startup (lifespan handler).
- No auth, no CORS middleware (same-origin only), no pagination, no WebSockets.
- All item/list mutations run in a single SQLite transaction per request.

### 1.3 Static file serving (exact behavior)

Do **not** mount `StaticFiles`. Register API routes first, then a single catch-all route:

```
GET /{full_path:path}   (must be registered AFTER every /api route)
```
Handler logic:
1. `full_path == "" or full_path == "index.html"` or starts with `share/` → return `static/index.html`.
2. Otherwise resolve `<project_root>/static/<full_path>`; if a regular file exists → return it
   (`FileResponse`), with explicit `media_type`:
   - `*.webmanifest` → `application/manifest+json`
   - `*.js` → `text/javascript`
   - `*.css` → `text/css`
   - `*.png` → `image/png`
   - `*.html` → `text/html`
   - `*.svg` → `image/svg+xml`
3. If the path starts with `api/` and no API route matched → `404 {"detail": "Not found"}` (JSON).
4. Anything else (unknown SPA path) → return `static/index.html` (SPA fallback).

**Cache headers (all static responses):** `Cache-Control: no-cache`. This app is local; the service
worker owns offline caching, and `no-cache` prevents stale `sw.js`/`app.js` after deploys.

### 1.4 Server run

- Default: `127.0.0.1:8000`. Overrides: env `PORT`, env `HOST`.
- `run.sh` (executable) does: `cd "$(dirname "$0")" && exec .venv/bin/uvicorn app.main:app --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}"`.
- DB path: env `TODO_DB`, default `todo.db` relative to the project root (i.e. `/home/hermes/projects/todo-app/todo.db`).
- PWA service workers require a secure context: `http://localhost` / `http://127.0.0.1` qualify;
  HTTPS/LAN deployment is out of scope (SPEC §6). Test PWA features from `localhost`.

### 1.5 Dependency setup (implementation agents)

```
cd /home/hermes/projects/todo-app
/home/hermes/.local/bin/uv venv .venv
.venv/bin/python -m pip install -r requirements.txt      # or: uv pip install -r requirements.txt
.venv/bin/python scripts/generate_icons.py               # creates static/icons/*.png (needs Pillow, in requirements)
```

`requirements.txt` (exact):
```
fastapi>=0.115,<1.0
uvicorn[standard]>=0.30,<1.0
pydantic>=2.7,<3.0
pytest>=8.0
httpx>=0.27
Pillow>=10.0
```

### 1.6 Time and date conventions (fixed)

- `created_at` / `updated_at`: server-generated UTC timestamps, ISO-8601 with microseconds and `Z`
  suffix — format string `%Y-%m-%dT%H:%M:%S.%f` + `"Z"`, e.g. `2026-09-02T12:34:56.789012Z`.
  Fixed width ⇒ lexicographic TEXT ordering == chronological ordering.
- `due_date`: calendar date only, `YYYY-MM-DD`. Never stored/generated with a time or timezone.
  The "current date" is never needed for any algorithm in v1 (all recurrence math is anchored to
  stored dates), so no timezone handling is required anywhere. Document this in code.
- API never accepts client-supplied `created_at` / `updated_at` / `id` / `done` (on create) — server owns them.

---

## 2. REST API contract

### 2.0 Global rules

- JSON in, JSON out. Content-Type `application/json`.
- **Error format (every 4xx/5xx):** `{"detail": "<human-readable string>"}` — `detail` is ALWAYS a
  string, never an array/object. Install FastAPI exception handlers so this holds for:
  - `RequestValidationError` (422) → `detail` = `"; ".join(f"{loc}: {msg}")` over all errors
    (Pydantic's `msg` text, `loc` like `body.title`).
  - `json.JSONDecodeError` (malformed JSON body) → `400 {"detail": "Invalid JSON body"}`.
  - `StarletteHTTPException` 404/405 → keep FastAPI's message strings ("Not Found", "Method Not Allowed").
- Status codes used and their exact meanings:
  - `400` — malformed JSON body; semantically invalid request that Pydantic can't see
    (e.g. PATCH body that is not an object).
  - `404` — the **URL's target resource** does not exist (list/item/share token).
  - `409` — a **referenced foreign resource in the request body does not exist** (FK conflict):
    `POST /api/items`, `PATCH /api/items/{id}`, shared-item writes with an unknown/foreign `list_id`.
  - `422` — Pydantic validation failure (wrong type, empty title, unknown enum, bad date, quantity ≤ 0, unknown field).
  - `403` — operation not permitted on this resource (write via read-only share).
  - `201` — resource created; `204` — DELETE success (empty body); `200` — everything else.
- String fields: `title`, `name` — trimmed of surrounding whitespace first; empty after trim ⇒ 422.
  Max lengths: `name` 200, `title` 200, `notes` 5000, share `permission` 4.
  Request bodies with unknown extra fields ⇒ 422 (Pydantic `extra="forbid"`).
- `notes` is nullable: JSON `null` allowed on create/PATCH; responses return `null` when unset.
- `quantity` JSON type: number > 0. Responses return an **int when integral** (`1`, `2`) else float (`0.5`) — never `1.0`.
- `done` is JSON boolean; stored as SQLite INTEGER 0/1.
- Item `recurrence_interval` is only meaningful for `recurrence="custom"`: requests must omit or
  null it otherwise (422 if set while recurrence ≠ custom); must be an integer ≥ 1 when custom (422 otherwise).
- **Default sort order (single canonical SQL, used by every item-returning endpoint):**

```sql
ORDER BY i.done ASC,
         CASE i.priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 WHEN 'low' THEN 2 ELSE 3 END ASC,
         i.due_date IS NULL ASC,   -- items without a due date sort AFTER dated ones
         i.due_date ASC,
         i.created_at ASC,
         i.id ASC                  -- final deterministic tie-break
```

  → pending first; within pending: high → medium → low → none; within priority: earliest due first
  (undated last); then oldest `created_at` first; `id` breaks ties.

### 2.1 JSON shapes (exact field names/types)

**List object**
```json
{
  "id": 1,
  "name": "Groceries",
  "item_count": 3,
  "pending_count": 2,
  "created_at": "2026-09-02T12:34:56.789012Z",
  "updated_at": "2026-09-02T12:34:56.789012Z"
}
```
`item_count` / `pending_count` are computed (LEFT JOIN counts) per request — read-only derived fields.

**Item object** (as returned by all item endpoints)
```json
{
  "id": 10,
  "list_id": 1,
  "title": "Buy milk",
  "notes": null,
  "priority": "high",
  "due_date": "2026-09-05",
  "quantity": 2,
  "done": false,
  "recurrence": "weekly",
  "recurrence_interval": null,
  "created_at": "2026-09-02T12:34:56.789012Z",
  "updated_at": "2026-09-02T12:34:56.789012Z"
}
```

**Shared-list object** (GET /api/shared/{token})
```json
{
  "list": { "id": 1, "name": "Groceries", "item_count": 3, "pending_count": 2, "created_at": "...", "updated_at": "..." },
  "items": [ /* Item objects, canonical sort */ ],
  "permission": "read"
}
```

**Enums (exact lowercase values):** `priority ∈ {none, low, medium, high}` (default `none`);
`status ∈ {all, pending, done}` (query only); `recurrence ∈ {none, daily, weekly, monthly, custom}` (default `none`);
`permission ∈ {read, edit}`.

### 2.2 Endpoint table

| # | Method & path | Success | Purpose |
|---|---|---|---|
| 1 | `GET /api/health` | 200 | Liveness/readiness: `{"status": "ok", "database": "ok"}` (runs `SELECT 1`) |
| 2 | `GET /api/lists` | 200 | All lists, ordered `name COLLATE NOCASE ASC, id ASC` |
| 3 | `POST /api/lists` | 201 | Create list |
| 4 | `PATCH /api/lists/{id}` | 200 | Rename list |
| 5 | `DELETE /api/lists/{id}` | 204 | Delete list **and cascade items + shares** |
| 6 | `GET /api/items` | 200 | Filtered/sorted items (params below) |
| 7 | `POST /api/items` | 201 | Create item |
| 8 | `PATCH /api/items/{id}` | 200 | Partial update / toggle done (may spawn) |
| 9 | `DELETE /api/items/{id}` | 204 | Delete item |
| 10 | `POST /api/lists/{id}/shares` | 201 | Create share link |
| 11 | `DELETE /api/shares/{token}` | 204 | Revoke share |
| 12 | `GET /api/shared/{token}` | 200 | Shared list + items + permission |
| 13 | `POST /api/shared/{token}/items` | 201 | Add item to a shared list (**edit permission only**) |
| 14 | `PATCH /api/shared/{token}/items/{item_id}` | 200 | Update/toggle item in a shared list (**edit only**, may spawn) |
| 15 | `DELETE /api/shared/{token}/items/{item_id}` | 204 | Delete item in a shared list (**edit only**) |

> Rationale for 13–15: SPEC §2.4 requires shares be "read-only **or editable**". A read-only GET alone
> would make `permission` meaningless, so editable shares expose item mutations scoped to their list.
> Every rule in §2.3/§2.4 applies identically (same validation, same recurrence spawning); the only
> difference is the extra authorization check: token permission must be `edit`, else `403`.

### 2.3 Per-endpoint contract

**1. GET /api/health** → `200 {"status": "ok", "database": "ok"}`. If the DB check fails → `500 {"detail": "Database unavailable"}`.

**2. GET /api/lists** → `200 [List, ...]`, order `ORDER BY name COLLATE NOCASE ASC, id ASC`.

**3. POST /api/lists**
Request: `{"name": "Groceries"}` (`name` required, string, 1–200 after trim).
→ `201` List object. 422 on missing/empty/too-long/typed-wrong `name`.

**4. PATCH /api/lists/{id}**
Request: `{"name": "New name"}`. 404 if list missing; 422 if invalid.
→ `200` updated List object. Bumps `updated_at`.

**5. DELETE /api/lists/{id}** → `204` (no body). 404 if missing.
SQLite FK `ON DELETE CASCADE` removes items and shares (requires `PRAGMA foreign_keys=ON`).

**6. GET /api/items** — query params:
| Param | Type | Behavior |
|---|---|---|
| `list_id` | int > 0, optional | Omit or absent ⇒ items from ALL lists. Unknown list ⇒ `200 []` (empty subset, not an error). |
| `status` | string, optional | `all` (default) \| `pending` (done=false) \| `done` (done=true). Any other value ⇒ 422. |
| `q` | string, optional | Case-insensitive substring over `title` **and** `notes` (`COALESCE(notes,'')`). Trimmed; empty ⇒ no filter. `%`/`_` in `q` escaped with `ESCAPE '\'`. |

→ `200 [Item, ...]` in canonical sort order. Invalid `list_id` type ⇒ 422.

**7. POST /api/items**
Request (all optional except `list_id`/`title`):
```json
{
  "list_id": 1,
  "title": "Buy milk",
  "notes": null,
  "priority": "none",
  "due_date": null,
  "quantity": 1,
  "recurrence": "none",
  "recurrence_interval": null
}
```
Validation: `list_id` required int; `title` required, trimmed 1–200; `notes` optional str ≤5000 or null;
`priority` enum (default `none`); `due_date` optional strict `YYYY-MM-DD` string that must parse via
`datetime.date.fromisoformat` (rejects `2026-02-30`) or null; `quantity` number > 0 (default 1);
`recurrence` enum (default `none`); `recurrence_interval` int ≥ 1, required iff `recurrence="custom"`,
forbidden otherwise. Server sets `done=false`, `created_at`, `updated_at`.
→ `201` Item object. 404 if `list_id` doesn't exist (**409 rule applies only to items being moved
between lists or PATCH `list_id` refs? No — see 2.0: FK conflict = 409 for POST too**): nonexistent
`list_id` ⇒ `409 {"detail": "Referenced list does not exist"}`.

**8. PATCH /api/items/{id}** — partial update; any subset of:
`title, notes, priority, due_date, quantity, recurrence, recurrence_interval, done, list_id`.
- Same validation rules as POST for each supplied field. Empty `{}` ⇒ `400 {"detail": "No fields to update"}`.
- `due_date: null` clears it; `notes: null` clears it. `quantity: null` ⇒ 422 (not nullable).
- Changing `recurrence` away from `custom` while an interval is present ⇒ 422 (client must clear it).
- `list_id` moves the item to another list; unknown target list ⇒ 409.
- **Toggle semantics:** `done: true` on an item currently `done=false` flips it and, if
  `recurrence != "none"`, **spawns the next occurrence** (§2.5). `done: true` when already `done=true`
  is an idempotent no-op (no spawn — prevents duplicate spawns on double-clicks). `done: false` just
  clears the flag; it never deletes spawned occurrences and never spawns.
- Response envelope **always** `{"item": <Item>, "spawned": <Item|null>}` — `spawned` is the newly
  created next occurrence, or `null` when no spawn happened. `200`; 404 if item missing.
  Example: `{"item": {..., "done": true}, "spawned": {..., "done": false, "due_date": "2026-09-09"}}`.

**9. DELETE /api/items/{id}** → `204`. 404 if missing.

**10. POST /api/lists/{id}/shares**
Request: `{"permission": "read"}` or `{"permission": "edit"}`. 404 if list missing; 422 if permission invalid/missing.
Server generates `token = secrets.token_urlsafe(16)` (22 chars, `[A-Za-z0-9_-]`, unguessable, ≥16 chars).
Multiple active tokens per list are allowed.
→ `201`:
```json
{
  "token": "Ab3xY9zQwErT1uIoP2aSdF",
  "permission": "edit",
  "url": "http://127.0.0.1:8000/share/Ab3xY9zQwErT1uIoP2aSdF",
  "created_at": "2026-09-02T12:34:56.789012Z"
}
```
`url` = `str(request.base_url).rstrip('/') + "/share/" + token`.

**11. DELETE /api/shares/{token}** → `204`. 404 if token unknown. Revoking the last token makes the
share URL dead.

**12. GET /api/shared/{token}** → `200` Shared-list object (§2.1) with items in canonical order and
the token's `permission`. 404 `{"detail": "Share link not found or revoked"}` if token unknown.
Works identically for read and edit tokens (the link itself grants view access).

**13–15. Shared writes** (`permission=edit` required; read ⇒ `403 {"detail": "This shared list is read-only."}`):
- `POST /api/shared/{token}/items` — same body/validation as #7; `list_id` must not be sent (server
  binds to the shared list); item must belong to that list.
- `PATCH /api/shared/{token}/items/{item_id}` — same semantics as #8 incl. spawn + envelope; 404 if
  the item doesn't exist or belongs to a different list; request must not contain `list_id`.
- `DELETE /api/shared/{token}/items/{item_id}` — as #9; 404 rules as above.

### 2.4 Item PATCH partial-update rules recap
- Only fields present in the JSON body are changed; absent fields keep their values.
- `updated_at` is bumped on every accepted PATCH that changes any field (including value-identical
  writes); the idempotent already-done case (no field changes) does not bump.
- POST/PATCH list and item name/title fields are trimmed; store the trimmed value.

### 2.5 Recurrence — exact `next_due` algorithm and spawn behavior

Pure function (in `app/recurrence.py`, no I/O — unit-testable):
```
next_due(prev_due: date|None, created_at: str, recurrence: str, interval: int|None) -> date|None
  recurrence == 'none'              -> None (never called with 'none' in practice)
  anchor = prev_due                       # the occurrence's CURRENT due_date
           if prev_due is None:
           anchor = date(created_at[:10]) # UTC calendar date of created_at, "creation date if none"
  recurrence == 'daily'   -> anchor + 1 day
  recurrence == 'weekly'  -> anchor + 7 days
  recurrence == 'monthly' -> add one calendar month to anchor:
                               y, m = anchor.year, anchor.month
                               m += 1
                               if m == 13: y += 1; m = 1
                               last = calendar.monthrange(y, m)[1]
                               return date(y, m, min(anchor.day, last))     # Jan 31 + 1mo = Feb 28/29
  recurrence == 'custom'  -> anchor + interval days        (interval validated >= 1)
```

**Spawn procedure — PATCH `done: false → true` on an item with `recurrence != 'none'` (single transaction):**
1. Lock the row (`BEGIN IMMEDIATE`); read it. Missing ⇒ 404. Already `done=true` ⇒ idempotent no-op.
2. `UPDATE items SET done=1, updated_at=<now> WHERE id=<id> AND done=0`. If rowcount = 0 (race) ⇒ no-op.
3. Compute `next_due` using the row's **pre-spawn** `due_date` (may be NULL) and `created_at` as anchor.
   The result is **always a concrete date** (never NULL) because recurrence ≠ none.
4. `INSERT` a new item row copying: `list_id, title, notes, priority, quantity, recurrence,
   recurrence_interval`; with `done=0`, `due_date=<next_due>`, fresh `created_at`/`updated_at`.
5. Respond `{"item": <completed occurrence, done=true>, "spawned": <new occurrence>}`.

Documented, intended consequences (do not "fix"):
- The completed occurrence stays visible (done) with its original `due_date` — history is kept.
- Un-done (`done: false`) on a recurring item does nothing special; re-completing it later spawns one
  more occurrence (each `false→true` transition spawns exactly once). Duplicates created this way can
  be deleted by the user.
- Deleting an occurrence never affects other occurrences (rows are independent).

### 2.6 HTTP status quick reference

| Code | When |
|---|---|
| 200 | OK (GET/PATCH success) |
| 201 | Created (POST list/item/share) |
| 204 | Deleted (DELETE) |
| 400 | Malformed JSON; PATCH `{}` with no fields |
| 403 | Write through a read-only share |
| 404 | URL resource missing: list/item/token |
| 409 | Body references a nonexistent `list_id` (FK conflict) |
| 422 | Pydantic validation failure (type/enum/date/empty/extra field) |
| 500 | Unexpected error (also DB-unavailable health) |

---

## 3. SQLite schema (exact DDL)

```sql
PRAGMA foreign_keys = ON;              -- set per-connection in code, NOT stored

CREATE TABLE IF NOT EXISTS lists (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  name       TEXT    NOT NULL,
  created_at TEXT    NOT NULL,         -- ISO-8601 UTC 'Z', see §1.6
  updated_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS items (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  list_id             INTEGER NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
  title               TEXT    NOT NULL,
  notes               TEXT,
  priority            TEXT    NOT NULL DEFAULT 'none'
                      CHECK (priority IN ('none','low','medium','high')),
  due_date            TEXT    CHECK (due_date IS NULL OR
                              due_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  quantity            REAL    NOT NULL DEFAULT 1 CHECK (quantity > 0),
  done                INTEGER NOT NULL DEFAULT 0 CHECK (done IN (0,1)),
  recurrence          TEXT    NOT NULL DEFAULT 'none'
                      CHECK (recurrence IN ('none','daily','weekly','monthly','custom')),
  recurrence_interval INTEGER CHECK (recurrence_interval IS NULL OR recurrence_interval >= 1),
  created_at          TEXT    NOT NULL,
  updated_at          TEXT    NOT NULL,
  CHECK ((recurrence = 'custom') = (recurrence_interval IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS shares (
  token      TEXT PRIMARY KEY,         -- secrets.token_urlsafe(16), 22 chars
  list_id    INTEGER NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
  permission TEXT    NOT NULL CHECK (permission IN ('read','edit')),
  created_at TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_items_list_done ON items(list_id, done);
CREATE INDEX IF NOT EXISTS idx_items_done      ON items(done);
CREATE INDEX IF NOT EXISTS idx_shares_list     ON shares(list_id);
```

Notes:
- The two cross-column CHECKs are last-resort DB guards; Pydantic is the primary validator
  (`due_date` GLOB is format-only — calendar validity like `2026-02-30` is rejected by the API layer).
- The `done` column is INTEGER 0/1; the API serializes to JSON boolean and accepts JSON boolean input.
- No `AUTOINCREMENT` pitfalls: `INTEGER PRIMARY KEY` suffices; `AUTOINCREMENT` (used above) additionally
  guarantees ids are never reused — keep it for predictable tests.
- Set `PRAGMA journal_mode = WAL` once at startup (persistent).
- Item-count subqueries for lists (`item_count`, `pending_count`) use the `idx_items_list_done` index:
  `COUNT(*)` and `COUNT(*) FILTER (WHERE done = 0)` per list via `LEFT JOIN ... GROUP BY`.

---

## 4. Frontend design system ("Taskflow" UI)

Constraints: **vanilla HTML/CSS/JS, zero dependencies, no build step, offline-capable.** Design quality
is a hard requirement — this section is normative (tokens, layout, components, behaviors), not suggestive.

### 4.1 Visual language
Linear/Notion-class: calm neutral surfaces, one indigo accent used sparingly, 1px hairline borders,
tight 4px spacing grid, 6–12px radii, micro-shadows, 120–180ms ease transitions, compact 13–15px UI text,
uppercase micro-labels for section headers. Rows and buttons are quiet until hover; nothing shouts.

### 4.2 Theme tokens (CSS custom properties — exact names)

`:root` (light, also the default before JS runs) and `[data-theme='dark']` on `<html>`:

| Token | Light | Dark |
|---|---|---|
| `--bg` | `#FAFAFB` | `#0F0F13` |
| `--surface` | `#FFFFFF` | `#17171B` |
| `--surface-2` | `#F3F3F5` | `#1D1D22` |
| `--surface-3` | `#ECECEF` | `#232329` |
| `--border` | `#E6E6EA` | `#26262C` |
| `--border-strong` | `#D4D4DA` | `#33333B` |
| `--text` | `#1B1B1F` | `#EFEFF2` |
| `--text-secondary` | `#5B5B66` | `#A6A6B0` |
| `--text-muted` | `#90909B` | `#6E6E79` |
| `--accent` | `#5E6AD2` | `#6B77E0` |
| `--accent-hover` | `#4A56C6` | `#7C87E6` |
| `--accent-soft` | `rgba(94,106,210,0.10)` | `rgba(107,119,224,0.16)` |
| `--danger` | `#E5484D` | `#F2555A` |
| `--danger-soft` | `rgba(229,72,77,0.10)` | `rgba(242,85,90,0.14)` |
| `--success` | `#30A46C` | `#46A758` |
| `--warning` | `#B25E09` | `#F5A524` |
| `--warning-soft` | `rgba(245,165,36,0.14)` | `rgba(245,165,36,0.16)` |
| `--priority-high` | `#E5484D` | `#F2555A` |
| `--priority-medium` | `#B25E09` | `#F5A524` |
| `--priority-low` | `#30A46C` | `#46A758` |
| `--priority-none` | `#90909B` | `#6E6E79` |
| `--shadow-xs` | `0 1px 2px rgba(16,16,20,0.05)` | `0 1px 2px rgba(0,0,0,0.35)` |
| `--shadow-sm` | `0 1px 3px rgba(16,16,20,0.07), 0 1px 2px rgba(16,16,20,0.04)` | `0 1px 3px rgba(0,0,0,0.4)` |
| `--shadow-md` | `0 4px 14px rgba(16,16,20,0.09)` | `0 4px 16px rgba(0,0,0,0.45)` |
| `--shadow-lg` | `0 16px 40px rgba(16,16,20,0.16)` | `0 20px 48px rgba(0,0,0,0.6)` |
| `--focus-ring` | `0 0 0 2px rgba(94,106,210,0.35)` | `0 0 0 2px rgba(107,119,224,0.45)` |

### 4.3 Typography, spacing, radius, motion tokens

| Token family | Values |
|---|---|
| `--font-sans` | `-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, "Noto Sans", sans-serif` |
| `--font-mono` | `ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace` |
| Type scale | `--fs-2xs: 0.6875rem` (11px, uppercase micro-labels, `letter-spacing: 0.06em`), `--fs-xs: 0.75rem` (12px, meta/dates/chips), `--fs-sm: 0.8125rem` (13px, secondary UI), `--fs-base: 0.9375rem` (15px, item titles, body), `--fs-lg: 1.125rem` (18px, page/panel titles), `--fs-xl: 1.5rem` (24px, app header) |
| Line heights | `1.4` body/items, `1.5` notes/paragraphs, `1.25` headings |
| Spacing (4px grid) | `--space-1: 4px, -2: 8px, -3: 12px, -4: 16px, -5: 20px, -6: 24px, -7: 32px, -8: 40px, -9: 48px` |
| Radius | `--radius-xs: 4px, -sm: 6px, -md: 8px, -lg: 12px, -xl: 16px, -full: 999px` (checkbox: `-full` round) |
| Motion | default `--ease: cubic-bezier(0.2, 0, 0, 1)`; micro `120ms`, standard `160ms`, modal/drawer `220ms`; respect `prefers-reduced-motion: reduce` → near-zero durations |
| Layout | `--sidebar-w: 264px`, `--header-h: 56px`, content column max-width `860px`, gutters `--space-6` desktop / `--space-4` mobile |
| Focus | `outline: 2px solid var(--accent); outline-offset: 2px` via `:focus-visible` on all interactive elements |

### 4.4 App layout

Three regions:
1. **Header** (`position: sticky`, height `--header-h`, `backdrop-filter: blur(8px)`,
   `background: color-mix(in srgb, var(--bg) 80%, transparent)`, hairline bottom border):
   left = hamburger (mobile only) + brand mark (indigo rounded-square glyph + "Taskflow"),
   center/right = search field, install button (appears on `beforeinstallprompt`), theme toggle.
2. **Sidebar** (`width: var(--sidebar-w)`, `background: var(--surface)`, right hairline border):
   - Section label "LISTS" (micro-label), then a nav list: **All tasks** row (inbox icon, always first,
     shows total pending count) then one row per list (dot/list glyph + name + pending count badge).
   - Row hover: reveal inline icon buttons — rename (pencil), share (link), delete (trash).
   - Active row: `background: var(--accent-soft)`, text `var(--text)`, 3px left accent bar (rounded).
   - Footer button "+ New list" (ghost, full-width). Name edit happens in a modal, not inline.
   - **Mobile (<720px):** sidebar is a drawer — hidden by default, slides in over a dimmed backdrop
     (`transform: translateX`, `--shadow-lg`), hamburger toggles, Esc/backdrop closes.
3. **Main column** (scrollable; content `max-width: 860px; margin: 0 auto`):
   - View header: list title (`--fs-lg`, medium) + pending count subtitle ("2 of 5 done") + actions
     (share button when a concrete list is open).
   - Filter bar: status segmented control — **All | Pending | Done** (3 equal pills in one
     `--surface-2` track, active pill = white surface + `--shadow-xs` in light / `--surface` in dark)
     and, in **All tasks** view, an inline list-name chip on every row instead of grouping.
   - Quick-add composer, then the item list. In the shared view (§4.8) the header shows the shared
     list name plus a permission banner instead of sidebar/nav.

### 4.5 Components (inventory + normative behavior)

| Component | Spec |
|---|---|
| **Quick-add composer** | Card at top of item list (`--surface`, `--radius-lg`, hairline border, `--shadow-xs`). One-line row: round "add" glyph button + text input (placeholder "Add a task…"); **Enter or + submits**; typing and pressing Enter submits immediately with defaults (no due date, priority none). An **"options" chevron** expands the second row: notes input, due-date `<input type="date">`, priority select, quantity number input (step 0.1 min 0.1), recurrence select (none/daily/weekly/monthly/custom) — choosing custom reveals "every N days" number input. Composer is disabled (opacity .5) in read-only shared view. |
| **Item row** | Grid `[checkbox 20px] [content 1fr] [meta] [hover actions]`, padding `--space-3 --space-4`, `border-radius: var(--radius-md)`, hairline separation instead of borders between rows; hover: `background: var(--surface-2)` + reveal actions (opacity 0→1, 120ms). Content: title (`--fs-base`, medium); when done → color `var(--text-muted)` + line-through; below title a meta line (`--fs-xs`, `--text-muted`) assembled from: list-name chip (All view only), priority chip, due chip, "×N" quantity, ⟳ recurrence hint. **Clicking anywhere on the row body (not checkbox/actions) opens the edit modal.** |
| **Checkbox** | 20px round (`--radius-full`), 1.5px `var(--border-strong)` border, transparent fill; checked: `--accent` fill + white inline-SVG check, 140ms spring-ish scale animation; entire row toggles optimistically then reconciles with the PATCH response (spawn shows a toast "Repeats {date}"). `role="checkbox"`-style native `<input type="checkbox">` styled, `aria-label` = "Mark {title} done". |
| **Chips** | 20px tall, `--radius-full`, padding 0 8px, `--fs-2xs` uppercase? No — `--fs-xs` normal case, 500 weight. Due chip: future = neutral (`--surface-2`, `--text-secondary`); **overdue** = `--danger-soft` bg, `--danger` text; **today** = `--warning-soft`/`--warning`. Priority chip: colored 6px dot + label, soft bg from the priority color at 10–14%. Done rows: chips mute to `--text-muted`. |
| **Edit modal** | Centered dialog `--radius-xl`, `--surface`, `--shadow-lg`, width `min(480px, calc(100vw - 32px))`, padding `--space-6`; title "Edit task" + Esc/✕ close; backdrop `rgba(0,0,0,0.4)` + `backdrop-filter: blur(2px)`; body identical field set to composer's options row + done toggle (checkbox + "Completed"); footer: left = Delete (ghost, `--danger` text, opens delete-confirm), right = Cancel / **Save** (accent, full, `--radius-md`). Enter saves; focus moves to first field on open; focus returns to the triggering row after close; `role="dialog" aria-modal="true"`. |
| **Delete-confirm modal** | Same shell, narrow (`min(360px, …)`), centered icon (trash in `--danger-soft` circle), title "Delete {name}?" / "Delete {n} items in this list?" for lists, body explains cascading delete for lists ("Items and share links will also be deleted"), buttons Cancel / **Delete** (danger filled). Only destructive actions open this modal — no instant destructive deletes. |
| **List rename modal** | Same shell: single text input + Cancel/Save. |
| **Share dialog** | Shell `min(520px, …)`. Header: list name. Section 1 "Invite by link": permission select (Can view / Can edit) + accent button "Create link" → on 201 shows the full URL read-only + Copy button (clipboard, then toast "Copied"). Section 2 "Active links": each token row: permission label, short token (mono, truncated middle), copy icon, revoke (trash → small confirm via danger-styled second click state or the delete-confirm modal — use the modal). Revoke calls `DELETE /api/shares/{token}` then removes the row. Empty state: "No active links". |
| **Segmented control** | Status filter (§4.4), `aria-pressed` buttons, 160ms background morph. |
| **Search field** | Header, `min-width: 180px`, icon-left, `--surface-2` bg, `--radius-md`, focus → white bg + `--focus-ring`; desktop shortcut `/` focuses it (unless typing in an input); clears via ✕. Debounced 150ms → refetch items with `q`. |
| **Theme toggle** | Icon button (sun/moon SVG), toggles `data-theme` on `<html>`, persists `localStorage["taskflow-theme"] = "light"|"dark"`; initial value = stored ?? `matchMedia('(prefers-color-scheme: dark)')`. Also updates both `<meta name="theme-color">` contents (see §5.5). |
| **Empty states** | 40px soft-tinted circular icon + title + one-line hint + CTA button, vertically centered, generous padding. Variants: (a) no lists yet → "Create your first list" → focuses new-list field; (b) list empty → "Nothing here yet" → focuses composer; (c) search/filter no results → "No matching tasks" + "Clear filters" ghost button; (d) shared list empty. |
| **Loading state** | First render per view shows 5 skeleton rows (title line + meta line bars, `@keyframes shimmer` over `--surface-2`→`--surface-3`), replaced when fetch resolves. |
| **Toast** | Fixed bottom-center (mobile) / bottom-right (desktop), `--radius-lg`, `--surface` elevated by `--shadow-lg`, hairline border, icon + message, 2.8s auto-dismiss with slide-up/fade; stack max 3. Types: success (check, accent), error (alert, `--danger`), info. `role="status"`/`aria-live="polite"`. |
| **Install button** | Header icon (download) — shown only after `beforeinstallprompt` fires (stored event); click → `prompt()`; after installed → hidden. |
| **Scrollbar** | Main column: thin overlay scrollbar, thumb `--border-strong`, radius full. |

### 4.6 Interaction & state patterns
- Optimistic checkbox toggling; on API error revert + error toast. All other mutations are
  request-then-render (fast local API; no spinners except first load).
- Every mutation response re-renders the current view from fresh state (`GET /api/lists` +
  `GET /api/items` after POST/PATCH/DELETE) — single source of truth, no client-side merge logic.
- Event delegation: one listener per container (list nav, item list, toolbar) reading
  `data-action` attributes — `data-action="toggle-done" data-id="10"` etc.
- Keyboard: Enter adds/submits; Esc closes modal/drawer/toast-menu; `/` focuses search (desktop);
  Delete key inside edit modal triggers delete-confirm. All icon buttons carry `aria-label` + `title`.
- Row hover actions: **edit** (pencil) and **delete** (trash), 28px icon buttons, revealed at `opacity: 1`
  on row hover / `:focus-within`; on touch devices always visible (no hover).
- Router: read `location.pathname`; if it matches `^/share/([A-Za-z0-9_-]{16,})/?$` → shared mode;
  else app mode (no other client routes). Use `history.replaceState`-free plain reads; no hash routing.

### 4.7 Responsive breakpoints (mobile-first)

| Range | Layout |
|---|---|
| `< 720px` (default) | Single column. Sidebar = off-canvas drawer. Header: hamburger + brand + search icon (expands to full-width overlay input when tapped). Composer options collapsed. Toasts bottom-center full-width-ish. `meta` chips wrap to second line. |
| `≥ 720px` | Two-pane: sidebar 264px visible + main. Search always visible in header (min 200px). Hover actions enabled. |
| `≥ 1100px` | Comfortable: sidebar stays 264px, main content column max 860px centered; keep two-pane. |

CSS: author **mobile-first** (base = single column) and layer desktop via
`@media (min-width: 720px)` / `@media (min-width: 1100px)`. Buttons/targets ≥ 44px touch height on
the base layer. Also honor `prefers-color-scheme` only as the *default* before JS runs: emit the
dark token block under `@media (prefers-color-scheme: dark) { :root:not([data-theme]) {...} }` so
there is no light flash, then let `[data-theme]` override.

### 4.8 Shared view (`/share/<token>`)
Same shell minus sidebar; header shows list name + permission badge ("Read-only" ghost / "Can edit").
Read-only: composer hidden, row hover actions hidden, checkbox disabled, row click does nothing.
Edit: composer + row actions + toggle enabled; mutations go to the `/api/shared/<token>/...`
endpoints. Load: `GET /api/shared/<token>`; 404 → branded "This link is invalid or has been revoked"
empty state (no retry loop). A "Back to my lists" link appears only when the visitor also owns lists —
simplest: always show a subtle "Open app" link to `/`.

### 4.9 Accessibility baseline
Semantic landmarks (`<header>`, `<nav>`, `<main>`), `aria-label`s on icon-only controls, real
`<input type="checkbox">`/`<button>`/`<label>` elements, modals as `role="dialog" aria-modal="true"`
with focus trap-in (first field) and restore-out, toasts `aria-live="polite"`, visible `:focus-visible`
rings everywhere, `prefers-reduced-motion` honored, color is never the only signal (chips pair dot
color with text, done pairs strikethrough with color).

---

## 5. PWA plan

### 5.1 `static/manifest.webmanifest` — exact content
```json
{
  "id": "/",
  "name": "Taskflow",
  "short_name": "Tasks",
  "description": "Centralized lists for todos, shopping, and anything else — installable and offline-ready.",
  "lang": "en",
  "dir": "ltr",
  "start_url": "/",
  "scope": "/",
  "display": "standalone",
  "background_color": "#17171B",
  "theme_color": "#17171B",
  "icons": [
    { "src": "/icons/icon-192.png",  "sizes": "192x192", "type": "image/png", "purpose": "any" },
    { "src": "/icons/icon-512.png",  "sizes": "512x512", "type": "image/png", "purpose": "any" },
    { "src": "/icons/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable" }
  ]
}
```
Served with `Content-Type: application/manifest+json` (see §1.3).

### 5.2 Service worker (`static/sw.js`)
- Cache versioning: `const SHELL_CACHE = 'taskflow-shell-v1'; const API_CACHE = 'taskflow-api-v1';`
  (bump the shell cache name whenever static assets change materially).
- **`install`:** `event.waitUntil(caches.open(SHELL_CACHE).then(c => c.addAll(PRECACHE)))` where
  `PRECACHE = ['/', '/index.html', '/style.css', '/app.js', '/manifest.webmanifest',
  '/icons/icon-32.png', '/icons/icon-192.png', '/icons/icon-512.png', '/icons/icon-maskable-512.png',
  '/icons/apple-touch-icon.png']` — every entry **must exist** or install fails; `self.skipWaiting()`.
- **`activate`:** delete caches whose names are not the two current ones; `self.clients.claim()`.
- **`fetch`** (same-origin only; ignore cross-origin and non-GET handling per row):
  - Navigation requests (`request.mode === 'navigate'`) → **network-first**: try `fetch`, on success
    `cache.put` a copy into `SHELL_CACHE`? No — shell responses carry `Cache-Control: no-cache`;
    cache the fresh copy, fall back to `caches.match('/index.html')` on network failure (offline load).
  - `GET` to `/api/` (URL starts with `/api/`) → **network-first with offline fallback**: try network;
    on `ok` clone into `API_CACHE` (keyed by full request URL incl. query) and trim to newest 40
    entries (oldest deleted); on network failure serve the cached copy for that exact URL if present,
    else `Response.error()`-style failure (UI shows its error toast). This yields last-known data offline.
  - `GET` to other same-origin static assets → **cache-first** (they are precached), falling back to
    network + `cache.put`.
  - Anything else (`POST`/`PATCH`/`DELETE`, or cross-origin) → `fetch` passthrough, never cached.
- Register from `app.js` **on `window.load`**, only when
  `'serviceWorker' in navigator` (secure context implied): `navigator.serviceWorker.register('/sw.js')`;
  on success log `[taskflow] SW registered`. Listen for `updatefound` → optional "Update available —
  reload" toast using `registration.waiting` + `postMessage({type:'SKIP_WAITING'})`.
- During development `sw.js` must be updated via browser DevTools "Update on reload" — remember to
  unregister old SWs when testing fresh changes (document in code comment).

### 5.3 Icon generation (`scripts/generate_icons.py`, Pillow)
- Run once (or after palette changes) with the venv Python: writes PNGs into `static/icons/`.
  Idempotent; outputs committed to disk (no runtime generation).
- Art: rounded-square vertical gradient `#5E6AD2 → #4A56C6` (accent → accent-hover), 1px inner
  lighter top highlight, and a white check mark drawn as a thick polyline (no text → no font
  dependency). Render at 4× supersample then `Image.LANCZOS` downscale for crisp edges.
- Files: `icon-32.png`, `icon-192.png`, `icon-512.png` (any purpose — rounded square with the canvas
  corners transparent), `icon-maskable-512.png` (full-bleed square background, glyph contained within
  the central 66% safe zone), `apple-touch-icon.png` (180×180, full-bleed, no transparency).
- The script self-verifies dimensions and exits non-zero on mismatch.

### 5.4 `static/index.html` — meta tags (exact)
```html
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Taskflow</title>
<meta name="description" content="Centralized lists for todos, shopping, and more.">
<link rel="manifest" href="/manifest.webmanifest">
<meta name="theme-color" content="#17171B" media="(prefers-color-scheme: dark)">
<meta name="theme-color" content="#FAFAFB" media="(prefers-color-scheme: light)">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Tasks">
<link rel="apple-touch-icon" href="/icons/apple-touch-icon.png">
<link rel="icon" type="image/png" sizes="32x32" href="/icons/icon-32.png">
<link rel="icon" type="image/png" sizes="192x192" href="/icons/icon-192.png">
```
Both `theme-color` metas get `id`s; the theme-toggle JS sets both `content` attributes to the active
theme's chrome color on manual toggle (keeps mobile browser chrome in sync).

### 5.5 PWA acceptance checklist (how the QA agent verifies AC7)
1. `GET /manifest.webmanifest` → 200, `application/manifest+json`, JSON contains required fields
   (name, short_name, start_url, display=standalone, theme_color, background_color, icons with 192+512+maskable).
2. `GET /sw.js` → 200, registers with no console errors on `localhost`.
3. Precache list entries all return 200 from the server (addAll would fail otherwise).
4. Icons exist at referenced paths; decode with Pillow and assert exact pixel dimensions.
5. DevTools/Lighthouse "Installable" audit passes on `localhost` (manifest + SW + HTTPS-or-localhost).
6. Airplane-mode reload of `/` still renders the shell (offline app shell).

---

## 6. File / folder layout (complete tree — implementation agents create exactly these files)

```
/home/hermes/projects/todo-app/
├── SPEC.md                        # existing — requirements (do not modify)
├── DESIGN.md                      # this document (do not modify)
├── requirements.txt               # §1.5 exact contents
├── run.sh                         # §1.4 launcher (chmod +x)
├── todo.db                        # runtime artifact — SQLite DB (default location; gitignore-able)
├── app/
│   ├── __init__.py                # empty
│   ├── main.py                    # FastAPI app factory/lifespan, ALL routes, static catch-all (§1.3),
│   │                              #   exception handlers → string {detail}, health endpoint
│   ├── db.py                      # connect() with PRAGMAs, get_db() dependency, init_schema() DDL (§3),
│   │                              #   row→dict serializers (done→bool, quantity int-if-integral),
│   │                              #   item_count/pending_count queries, canonical item ORDER BY constant
│   ├── schemas.py                 # Pydantic models + validators (trim, strict dates, custom-interval
│   │                              #   rules, extra="forbid") for every request body
│   ├── recurrence.py              # pure next_due() + add_months() (§2.5) — no I/O
│   └── services.py                # optional thin layer: list/item/share CRUD SQL + spawn transaction (§2.5)
│                                  #   (if agents prefer, fold into main.py — but keep ONE place per concern)
├── static/
│   ├── index.html                 # SPA shell + all meta tags (§5.4)
│   ├── style.css                  # tokens (§4.2/4.3), layout, all components (§4.5), breakpoints (§4.7)
│   ├── app.js                     # state, apiFetch wrapper, renderers, event delegation, router,
│   │                              #   theme toggle + localStorage, SW registration (§5.2), toasts/modals
│   ├── manifest.webmanifest       # §5.1 exact JSON
│   ├── sw.js                      # §5.2 service worker
│   └── icons/                     # generated by scripts/generate_icons.py (§5.3)
│       ├── icon-32.png
│       ├── icon-192.png
│       ├── icon-512.png
│       ├── icon-maskable-512.png
│       └── apple-touch-icon.png
├── scripts/
│   └── generate_icons.py          # Pillow icon generator (§5.3)
└── tests/
    ├── conftest.py                # tmp DB per test (env TODO_DB or override), TestClient fixture,
    │                              #   fresh-schema helper; restart helper (second app instance on same file)
    ├── test_api.py                # §7 column "API tests" — full endpoint/validation/sort/filter/
    │                              #   recurrence/share/persistence coverage
    └── test_pwa.py                # manifest fields, SW precache list resolvability, icon dimensions,
                                  #   meta tags present in index.html
```

Suggested build order for agents: `db.py` → `schemas.py` → `recurrence.py` → `main.py` →
`tests/test_api.py` green → `index.html`/`style.css`/`app.js` → icons script + PWA assets →
`tests/test_pwa.py` → `run.sh` smoke test.

---

## 7. Requirements → acceptance criteria traceability

SPEC.md §5 acceptance criteria (rows) vs. where each is verified. Automated = pytest
(`.venv/bin/python -m pytest tests/ -v`); manual = QA checklist against `http://127.0.0.1:8000` on
localhost (PWA features require localhost or HTTPS).

| # | Acceptance criterion (SPEC §5) | Automated verification (tests/test_api.py unless noted) | Manual / UI verification | PWA checks (tests/test_pwa.py / audit) |
|---|---|---|---|---|
| 1 | CRUD lists & items via API; validation rejects empty titles, bad priorities/dates with 4xx | `test_list_crud` (POST/GET/PATCH/DELETE, 404s), `test_item_crud` (POST/GET/PATCH/DELETE, 404s, PATCH envelope `{item,spawned}`), `test_validation_rejects_empty_title` (422), `test_validation_rejects_bad_priority` (422), `test_validation_rejects_bad_due_date` (422 incl. `2026-02-30`), `test_validation_quantity` (≤0 ⇒ 422), `test_validation_unknown_list_id` (409), `test_validation_extra_fields` (422), `test_error_shape_is_string_detail` (every 4xx body == `{"detail": str}`), `test_delete_list_cascades_items` | Create/rename/delete lists and items end-to-end; empty-title and garbage-date inputs show inline 422 toasts | — |
| 2 | Canonical sort: pending → priority high→low → due date → created_at | `test_sort_order_pending_first_then_priority_then_due_then_created` (crafted rows incl. same-second `created_at` and NULL due dates; asserts exact id sequence) | Order visibly matches in All view and per-list view | — |
| 3 | Filters (list_id, status pending/done, search q) return correct subsets | `test_filter_by_list`, `test_filter_status_pending`, `test_filter_status_done`, `test_filter_status_invalid` (422), `test_search_title_and_notes_case_insensitive`, `test_search_escapes_like_wildcards`, `test_filter_combined` | Segmented All/Pending/Done; search box matches typing across title+notes | — |
| 4 | Recurring done → spawn with correct next_due per type; original done | `test_recurrence_daily`, `test_recurrence_weekly`, `test_recurrence_monthly` (incl. month-end clamp: Jan 31 → Feb 28), `test_recurrence_monthly_anchor_fallback` (no due_date → created_at anchor), `test_recurrence_custom_interval`, `test_recurrence_spawn_copies_fields`, `test_recurrence_toggle_idempotent_no_double_spawn`, `test_recurrence_undo_does_not_delete_spawn`, `test_recurrence_validation` (custom without interval ⇒ 422; interval without custom ⇒ 422) | Create recurring task; toggle → completed row stays + new pending row appears with future due; UI shows "Repeats …" toast | — |
| 5 | Share create read/edit; shared GET respects permission; revoke ⇒ 404 | `test_share_create` (201, token ≥16 chars urlsafe, url contains `/share/`), `test_share_get_read`, `test_share_get_edit`, `test_share_edit_allows_write` (POST/PATCH/DELETE via `/api/shared/<token>/...`), `test_share_read_forbids_write` (403), `test_share_revoke_then_404`, `test_share_unknown_token_404`, `test_shared_write_item_belongs_to_list` (404) | Share dialog creates link, copy works; open `/share/<token>` in another browser/incognito → correct view; read-only hides editing; revoke breaks the URL | — |
| 6 | Persistence across restart | `test_persistence_across_restart` (write via app instance A on tmp file → close → new app instance B same file → data present) | Restart `run.sh`; data intact | — |
| 7 | PWA: manifest fields, SW registers + precaches shell, icons exist at referenced paths, installability audit | — (HTTP-level bits live in test_pwa.py) | DevTools Application tab: manifest parses, SW "activated and running", cache list matches PRECACHE | `test_manifest_required_fields`, `test_manifest_served_with_json_mime`, `test_sw_precache_entries_resolvable` (each PRECACHE URL → 200), `test_icon_files_exist_and_dimensions` (192/512/maskable-512/apple-touch 180 via Pillow), `test_index_meta_tags` (theme-color ×2, apple-touch-icon, manifest, viewport-fit=cover). Manual: Lighthouse installable audit on localhost; offline reload shows shell |
| 8 | UI flows: create/edit/delete/toggle list & item, filters, search, recurring creation | — (API covered above; UI layer is thin) | QA checklist: create list → add item with notes/priority/due/quantity → edit via row click → toggle done → delete w/ confirm modal → filters/search → All view vs per-list → create recurring (each type) → dark theme → responsive at 375px/768px/1280px → error toast on stopped server | Install prompt appears; app opens standalone |
| 9 | Dark/light theme toggle persists (localStorage) | — | QA: toggle → `data-theme` flips, palette swaps, no flash on reload; reload keeps choice; OS-preference default honored before first toggle (localStorage key `taskflow-theme`) | — |

Every automated test asserts `response.status_code`, exact JSON field names/types, and exact
`{"detail": "<string>"}` error bodies where applicable. Tests run against an isolated temp DB
(`conftest.py`), not `todo.db`.

---

## 8. Implementation gotchas (read before coding)

1. **`PRAGMA foreign_keys=ON` per connection** — otherwise `ON DELETE CASCADE` silently no-ops and
   deleting a list orphans items/shares (breaks AC1 cascade test).
2. Never store timestamps with variable width (e.g. `+00:00` vs `Z`, or missing microseconds) —
   lexicographic ordering of the sort's final tiebreak depends on fixed-width ISO strings.
3. Pydantic `extra="forbid"` + string `detail` for 422 — register the `RequestValidationError`
   handler or the default array-shaped detail violates the error contract.
4. `addMonths` clamp (Jan 31 → Feb 28/29) must be a pure function — unit-test it directly.
5. The spawn must read `due_date` **before** flipping `done`, and the whole mark+insert must be one
   transaction with a `WHERE done = 0` guard to stay idempotent under double PATCH.
6. `LIKE` is ASCII-case-insensitive only; escaping `%`/`_` with `ESCAPE '\'` keeps user input honest.
7. Serve `/sw.js` and `/index.html` with `Cache-Control: no-cache` (§1.3) or stale service-worker
   updates will haunt offline testing.
8. UI quantity formatting: `1` renders "1", `1.5` renders "1.5" (never `1.0`) — backend already
   returns int-when-integral (§2.1); frontend should also strip a trailing `.0` defensively.
9. `.webmanifest` needs an explicit media type; browsers refuse manifests with wrong MIME.
10. Checkbox/done PATCH responses are `{item, spawned}` — the renderer must use `resp.item`, not the
    raw response, when refreshing the row (and insert `resp.spawned` when non-null).
