# Centralized TODO App — Requirements Specification (v1)

Status: APPROVED (Ahmed, 2026-09-02)

## 1. Product summary

A centralized, browser-based list app usable for TODOs, shopping lists, groceries,
and general item tracking. Installable as a PWA. Modern, pretty UI.

## 2. Non-negotiable requirements

### 2.1 Core list & item management
- Multiple lists (create, rename, delete). Deleting a list deletes its items.
- Items belong to exactly one list.
- Item fields: title (required), notes (optional), priority (none/low/medium/high),
  due_date (optional, ISO date), quantity (optional positive number, default 1),
  done (boolean), created_at, updated_at.
- Item CRUD: create, edit, delete, toggle done.
- Items ordered: not-done first, then by priority (high→low), then due date, then created.

### 2.2 Filtering & search
- Filter by list (global "All" view + per-list view).
- Filter by status: all / pending / done.
- Search across title and notes, case-insensitive substring.

### 2.3 Recurring tasks
- Recurrence: none, daily, weekly, monthly, or custom interval (every N days).
- When a recurring item is marked done, it is NOT deleted; a new occurrence is
  spawned with due_date = next occurrence date (anchor = previous due date, or
  creation date if none). The completed occurrence stays as a done item.
- next_due calculation: daily +1d, weekly +7d, monthly +1 month, custom +N days.

### 2.4 Sharing (link-based, no accounts)
- Any list can be shared via a unique unguessable token (URL /share/<token>).
- Share permission: read-only or editable, chosen at share creation time.
- Sharing can be revoked (token deleted).
- No authentication system in v1.

### 2.5 Persistence
- SQLite database, file-backed, survives server restarts.

### 2.6 PWA (Progressive Web App)
- Web app manifest (name, short_name, icons incl. 192px & 512px, theme color,
  background color, display: standalone, start_url).
- Service worker: caches app shell (HTML/CSS/JS, manifest, icons) for offline load;
  runtime fetch with network-first for API calls.
- Icons: generated PNG assets (192, 512, maskable).
- meta theme-color, apple-touch-icon, viewport with viewport-fit=cover.
- Mobile-first responsive layout (works well on phones).

### 2.7 Design
- Modern, polished aesthetic (Linear/Notion-class: clean typography, spacing,
  subtle borders/shadows, smooth transitions, tasteful accent color).
- Light and dark theme (system-preference based, with manual toggle).
- Empty states, loading states, toast/confirmation for destructive actions.
- No heavy UI frameworks required, but design quality is a hard requirement.

## 3. API contract (FastAPI + SQLite)

- REST-ish JSON API under /api.
- Lists:  GET /api/lists · POST /api/lists · PATCH /api/lists/{id} · DELETE /api/lists/{id}
- Items:  GET /api/items?list_id=&status=&q= · POST /api/items · PATCH /api/items/{id} · DELETE /api/items/{id}
- Sharing: POST /api/lists/{id}/shares (body: permission) → {token, url}
           DELETE /api/shares/{token} · GET /api/shared/{token} (list + items, respecting permission)
- Errors: JSON {detail: "..."} with proper HTTP status codes (404, 400, 422, 409).
- Item PATCH accepts partial updates; toggling done on a recurring item spawns next occurrence (see 2.3).
- Frontend served statically at / (index.html), API at /api.

## 4. Data model (SQLite)

- lists: id INTEGER PK, name TEXT NOT NULL, created_at TEXT, updated_at TEXT
- items: id INTEGER PK, list_id INTEGER FK→lists ON DELETE CASCADE, title TEXT NOT NULL,
  notes TEXT, priority TEXT CHECK(none|low|medium|high), due_date TEXT, quantity REAL NOT NULL DEFAULT 1,
  done INTEGER NOT NULL DEFAULT 0, recurrence TEXT CHECK(none|daily|weekly|monthly|custom),
  recurrence_interval INTEGER (days, for custom), created_at TEXT, updated_at TEXT
- shares: token TEXT PK (urlsafe random ≥16 chars), list_id INTEGER FK→lists ON DELETE CASCADE,
  permission TEXT CHECK(read|edit), created_at TEXT

## 5. Acceptance criteria (testable)

1. CRUD for lists and items works via API; validation rejects empty titles and bad priorities/dates with 4xx.
2. Default sort order: pending first, then priority, then due date, then created_at.
3. Filters (list_id, status pending/done, search q) return correct subsets.
4. Recurring item done → new occurrence spawned with correct next_due for each recurrence type; original marked done.
5. Share token created with read/edit permission; shared GET respects permission; revoke makes token 404.
6. Database persists across server restart (write → restart → read).
7. PWA: manifest served with required fields; service worker registers and precaches app shell; icons exist at referenced paths; audit passes for installability basics (manifest + SW + HTTPS note).
8. UI: create/edit/delete/toggle list & item, filters, search, recurring task creation all work from the browser.
9. Dark/light theme toggle persists (localStorage).

## 6. Out of scope (v1)

User accounts/auth, real-time sync, drag-and-drop reordering, attachments, notifications/reminders, multi-device sync beyond PWA offline shell, HTTPS deployment (post-step).

## 7. Non-goals / decisions

- SQLite only. No ORM required (stdlib sqlite3 is fine).
- Vanilla JS frontend (no build step) — keeps PWA simple and the app dependency-free.
- Server: uvicorn. Port 8000 default, configurable via env PORT.
- Project root: ~/projects/todo-app
