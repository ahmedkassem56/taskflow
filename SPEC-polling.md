# SPEC — Auto-refresh polling (live sync for shared lists)

Status: APPROVED (Ahmed, 2026-09-02). Supersedes DESIGN.md §1.2 "no WebSockets"
for the polling addition. Stack/architecture unchanged: vanilla JS frontend,
FastAPI backend, SQLite. No backend changes required.

## 1. Problem

Taskflow has no real-time sync: when another user edits a shared list, an open
tab shows stale data until the user manually reloads or performs an action that
triggers a refetch.

## 2. Requirements

### R1 — Background polling
- While the app tab is **visible**, refetch `GET /api/lists` + `GET /api/items`
  (same query string as the active view) every **5 seconds**.
- When the tab is **hidden** (document.visibilityState !== 'visible'),
  pause polling. Resume when it becomes visible again, with an immediate
  refresh on visibility return.
- Polling must not fire while a request is already in flight (no overlap).
- Share mode (`/share/<token>`) polls `GET /api/shared/<token>` the same way.

### R2 — Non-disruptive
- A background refresh must **not** disturb the user: no full-page reload, no
  modal/scroll disruption, no toast on success. Silent refetch + re-render.
- If a poll fetch fails (network/server), fail silently and try again next
  tick — never show an error toast for background polls.

### R3 — No data-loss / no clobber
- The poll's re-render must preserve the user's current state: active list,
  status filter, search query, checkbox states, and any **open modal** must
  survive the re-render. When a modal is open, skip re-render (or render only
  the parts outside the modal) so the user's in-progress edits are untouched.
- Rendering must be idempotent (same data → same DOM, no flicker).

### R4 — Respect focus for composition
- If the composer input is focused and the user is mid-typing an item, a poll
  re-render must not clear or steal focus from the composer.

### R5 — Efficient
- 5s interval, pause on hidden, no overlap, one in-flight request at a time.
- Do not refetch when nothing changed if cheap to detect (optional; simple
  refetch is acceptable — this is a tiny app).

## 3. Non-goals
- No backend changes, no SSE/WebSockets, no CRDT/OT, no presence indicators,
  no per-item diffs. Simple whole-state refresh is the contract.

## 4. Acceptance criteria (testable)

1. AC1 — With two tabs open on the same list, an item created in tab A appears
   in tab B within ~6 seconds **without any user action** in tab B.
2. AC2 — With tab B hidden (visibilityState hidden), no poll requests fire;
   on becoming visible again, tab B refreshes immediately.
3. AC3 — No overlapping poll requests (max one in-flight) — verifiable via
   network inspection.
4. AC4 — Opening a modal (e.g., Edit item) while a poll fires does not close
   or corrupt the modal; typed-but-unsaved modal values are preserved.
5. AC5 — An in-progress composer draft (typed title) survives a poll tick;
   focus stays in the composer.
6. AC6 — No error toast appears when the server is unreachable during a poll;
   polling resumes when the server returns.
7. AC7 — Background poll re-render preserves active list, status filter, and
   search query.
8. AC8 — Existing 44-test suite stays green; `node --check static/app.js`
   passes.
