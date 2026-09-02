# SPEC — New-on-top ordering + hold-to-drag reorder

Status: APPROVED (Ahmed, 2026-09-02). Extends SPEC.md. Stack unchanged.

## 1. Problem

1. New items append to the bottom; user wants new entries at the **top**.
2. No reordering exists. User wants: **press-and-hold any item → rearrange
   mode → hold an item and drag it up/down to move it**, with up/down arrows
   available on every entry as a secondary affordance while that mode is
   active.

## 2. Requirements

### R1 — New items on top (position ordering)
- Add integer `position` column to items (default 0). Lower = higher on screen.
- New items are inserted at `position = 0` (top); all other items in the same
  list shift down by 1.
- Canonical item sort becomes: **done (0 first) → position ASC → id ASC**
  everywhere (list view, All view, shared view, filtered).
- Migration: idempotent `ALTER TABLE items ADD COLUMN position INTEGER NOT NULL
  DEFAULT 0` guarded by column check; backfill `position = id` for existing
  rows (ids are monotonic in creation order → preserves current relative
  order). Runs inside existing startup `init_schema`.

### R2 — Hold-to-drag reorder
- **Press-and-hold (≥500ms)** on any item row enters **rearrange mode**: a
  "Done" pill appears, and the held row lifts visually.
- In rearrange mode:
  - **Drag**: hold any item and drag up/down — the item follows the pointer,
    other rows shift to indicate the drop slot; release places it. Touch and
    mouse both work.
  - **Arrows**: up/down arrow buttons appear on EVERY entry (secondary
    affordance); one tap = move one position.
- Drag/arrows operate within the **same list and same done-state group**
  (pending items can't be dragged below the done group; done items are
  draggable among themselves when viewing the done filter).
- Exits: tap "Done", press Escape, tap elsewhere. Dragging an item to its slot
  does not exit.
- Long-press must suppress the row-click (edit modal); a short tap still opens
  edit.
- Rearrange mode must survive poll re-renders (poll skips re-render while
  active).

### R3 — Persistence
- Moves persist via API: `PATCH /api/items/{id}` accepts `{"move":"up"}` or
  `{"move":"down"}` — swaps `position` with the adjacent same-list same-done
  item. Boundary moves are no-ops (200, item unchanged).
- Shared (edit) lists support move via `/api/shared/{token}/items/{id}`; 
  read-only shares show no move controls.
- A drag spanning N positions issues N serial `move` requests on release
  (delta is small in practice; each is a cheap local SQLite swap).

## 3. Non-goals
- No true drag-and-drop library, no cross-list moves, no restore of
  priority/due-date sorting, no animated drop-target ghosts beyond row shifts.

## 4. Acceptance criteria

1. AC1 — Creating an item places it at top (position 0), others shift; API
   returns it first in canonical order.
2. AC2 — Migration backfills existing rows by id (relative order preserved);
   migration is idempotent across restarts.
3. AC3 — `{"move":"up"|"down"}` swaps with adjacent same-list same-done item;
   boundary = no-op 200.
4. AC4 — Move never crosses lists or done-state groups.
5. AC5 — Sort is `done, position, id` in every items-returning endpoint.
6. AC6 — 500ms hold enters rearrange mode and suppresses the edit click; short
   tap still opens edit.
7. AC7 — Dragging an item reorders it visually and persists (release → API);
   arrows move one position each and keep mode active; Done/Escape/tap-elsewhere
   exits.
8. AC8 — Polling skips re-render while rearrange mode is active.
9. AC9 — Share edit mode supports move; read-only shows no controls.
10. AC10 — Suite green (updated + new tests for AC1–AC5, AC9); `node --check`
    passes; real-browser E2E covers AC6–AC8.
