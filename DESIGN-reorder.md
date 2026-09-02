# DESIGN — New-on-top + hold-to-drag reorder (implementation contract)

Derived from `SPEC-reorder.md` (APPROVED). Source of truth for the two
implementation agents. Backend agent owns: `app/db.py`, `app/schemas.py`,
`app/main.py`, `tests/test_api.py`. Frontend agent owns: `static/app.js`,
`static/style.css`, `static/sw.js` (v7 bump), `tests/test_pwa.py` (v7 assert).
No other files. Read the current files in full before editing.

## 1. Backend contract

### 1.1 Schema + migration (`app/db.py`)

- Add to `SCHEMA_SQL` items table: `position INTEGER NOT NULL DEFAULT 0`
  (place after `quantity`, before `done`).
- In `init_schema(conn)`: after `executescript`, run a guarded migration:
  ```python
  cols = [r[1] for r in conn.execute("PRAGMA table_info(items)").fetchall()]
  if "position" not in cols:
      conn.execute("ALTER TABLE items ADD COLUMN position INTEGER NOT NULL DEFAULT 0")
      conn.execute("UPDATE items SET position = id")   # preserve creation order
  ```
  Idempotent: only runs when the column is missing; backfill exactly once.

### 1.2 Canonical sort (`app/db.py`)

Replace `ITEM_ORDER_SQL` with:
```python
ITEM_ORDER_SQL = (
    "ORDER BY i.done ASC, i.position ASC, i.id ASC"
)
```

### 1.3 Create item → position 0, shift others (`app/db.py` + `app/main.py`)

- `db.create_item(...)` (or the existing INSERT path in `app/main.py`) must,
  inside the same transaction as the insert:
  1. `UPDATE items SET position = position + 1 WHERE list_id = ? AND done = 0`
  2. INSERT with `position = 0`.
- New item serializer includes `position` (add to `item_from_row`).

### 1.4 Move operation (PATCH)

- `app/schemas.py`: add to `_ItemPatchFields`:
  ```python
  move: Optional[Literal["up", "down"]] = None
  ```
  Add a model_validator: `move` is mutually exclusive with every other
  provided field (if `move` provided AND len(provided) > 1 → 422
  "move cannot be combined with other fields").
- In `_apply_item_patch` (app/main.py), handle `move` FIRST:
  - If `patch.move` provided:
    - Neighbor query (same list, same done, adjacent in (position, id)):
      - up: `SELECT * FROM items WHERE list_id=? AND done=? AND (position < ? OR (position = ? AND id < ?)) ORDER BY position DESC, id DESC LIMIT 1`
      - down: `SELECT * FROM items WHERE list_id=? AND done=? AND (position > ? OR (position = ? AND id > ?)) ORDER BY position ASC, id ASC LIMIT 1`
    - If no neighbor → return `{"item": <unchanged row dict>, "swapped": None}` (200 no-op).
    - Else swap positions (`UPDATE ... SET position = other.position WHERE id = mine`, and vice versa — two UPDATEs inside the existing transaction), return `{"item": <moved item dict>, "swapped": <neighbor dict>}`.
  - `move` never touches `updated_at` (ordering op, not content edit) — or bump it; pick ONE and be consistent. (Decision: bump it — every other PATCH bumps; keeps timestamps honest.)
- The shared PATCH endpoint uses the same `_apply_item_patch`, so shared edit lists get move for free; read-only stays 403 on writes.
- The two INSERT statements that create items (recurrence spawn in `_apply_item_patch`, and create_item) must also set `position`:
  - spawn: new occurrence gets `position = 0` and shifts its list's done=0 items +1 too? NO — spawn creates a PENDING occurrence; apply the same shift rule (shift done=0 items of that list +1, insert at 0). Keep consistent with 1.3.
  - Add `position` column to both INSERT column lists + VALUES.

### 1.5 Tests (`tests/test_api.py`)

- Update the canonical-sort test: expect `done, position, id` ordering
  (craft rows, verify exact id sequence).
- New tests:
  - create-at-top: existing items shift (positions verify), new item first.
  - migration idempotent: fresh schema has column; `init_schema` twice → no error; backfill correctness (simulate old DB without column → after init, positions == ids).
  - move up/down swap; boundary no-op (200, positions unchanged).
  - move scoped: never crosses list or done group (create 2 lists + mixed done, move, verify).
  - move combined with other field → 422.
- Keep everything else green: `44 passed` becomes more; report the exact new count.

## 2. Frontend contract (`static/app.js`, `static/style.css`)

### 2.1 State

```js
rearrange: { active: false, dragId: null, pointerId: null, startY: 0, currentY: 0, shift: 0, suppressClick: false, holdTimer: null, doneGroup: 'pending' }
```
`doneGroup` = the done-state of the group being rearranged ('pending' unless the
active filter is `done`). Used by render/drag so done items only reorder among
themselves when viewing the done filter.

### 2.2 Entering rearrange mode (long-press ≥500ms)

- `pointerdown` on an `.item-row` (ignore if target is inside `.checkbox`,
  `.row-actions`, a button, or when read-only share) → start `holdTimer` (500ms).
- `pointerup`/`pointercancel`/`pointerleave` before 500ms → clear timer (normal
  click proceeds → edit modal).
- Timer fires → `enterRearrange(itemId)`:
  - `state.rearrange.active = true; state.rearrange.doneGroup = <group of held item>; suppressClick = true`
  - re-render rows with `.rearrange-mode` class + a toolbar (Done pill) above the list.
- The click after a long-press must be suppressed: in the document click
  handler, if `state.rearrange.suppressClick` → prevent the row-click edit and
  reset the flag.

### 2.3 Row rendering in rearrange mode

- Each row (of the active done group; other-group rows get `.dimmed`) gains:
  - up/down arrow buttons in `.row-actions`:
    `<button data-action="move-up" data-id="N">↑</button>` / `move-down`.
  - `data-id` preserved; dragging still works.
- Read-only share: no arrows, no drag, no long-press.

### 2.4 Drag

- In rearrange mode, `pointerdown` on `.item-body` of a same-group row starts
  a drag: `setPointerCapture`, record `startY`, add `.dragging` class to the
  row (transform: translateY follows `currentY - startY` via
  `pointermove`).
- On `pointermove`, compute how many row-heights crossed and visually shift
  sibling rows (CSS class `.shift-up`/`.shift-down` or transform on siblings —
  implementation choice, must look smooth).
- On `pointerup`: compute delta slots from original index to target index
  (within the same done group); reorder `state.items` array locally;
  persist: issue sequential `PATCH {move: 'up'|'down'}` calls (one per slot
  crossed, awaiting each); then silent `fetchAppData(false)` + `renderAll()`
  (keeps positions authoritative). On any failure: silent refetch (no toast).
- Drag does NOT exit rearrange mode.

### 2.5 Arrows

- `move-up`/`move-down` actions → single `PATCH {move}` → silent refresh +
  `renderAll()`. Mode stays active. Boundary no-op is fine (server 200 no-op).

### 2.6 Exit

- "Done" pill (`data-action="exit-rearrange"`), Escape key, or pointerdown
  outside any item row → `state.rearrange.active = false`, re-render normal.

### 2.7 Polling interaction

- `pollTick` returns early when `state.rearrange.active` is true (no
  re-render mid-drag). The existing `poll` guard section gains this check.

### 2.8 CSS (`static/style.css`)

- `.item-row.rearrange-mode` (slight lift/shadow), `.item-row.dragging`
  (transform + z-index + shadow), `.item-row.dimmed` (opacity .45),
  `.rearrange-toolbar` (sticky Done pill bar), `.move-arrow` buttons
  (32px ghost icon buttons matching `.icon-btn.small`), shift animations for
  siblings (transition on transform 120ms).
- Mobile + desktop consistent; arrows only visible in rearrange mode.

### 2.9 SW bump

- `static/sw.js`: `SHELL_CACHE` → `taskflow-shell-v7`.
- `tests/test_pwa.py`: assertion v6 → v7.

## 3. Verification (coordinator)

1. `node --check static/app.js`; pytest suite (report new count, all green).
2. Playwright E2E (coordinator): create → appears top; hold 500ms → rearrange
   mode + Done pill; tap arrow → row moves, persists (reload check); drag via
   mouse down/move/up → order persists after reload; short tap still opens
   edit; Escape exits; polling paused during rearrange.
3. Live-sync regression: two-tab poll test still passes.
4. Commit + push.
