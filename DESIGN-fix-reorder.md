# DESIGN — Reorder UX fixes (implementation contract)

Derived from `SPEC-fix-reorder.md` (APPROVED). Backend agent owns:
`app/schemas.py`, `app/main.py`, `app/db.py`, `tests/test_api.py`. Frontend
agent owns: `static/app.js`, `static/style.css`, `static/sw.js` (v8),
`tests/test_pwa.py`. Read the current files in full first. No GitHub push.

## 1. Backend — `move_to` single-request move

### 1.1 Schema (`app/schemas.py`)

- Add to `_ItemPatchFields`:
  ```python
  move_to: Optional[int] = Field(default=None, ge=0)
  ```
- Extend the move-exclusivity validator: if `move` OR `move_to` is in
  `model_fields_set`, then `len(model_fields_set)` MUST be 1, else 422
  (message: "move/move_to cannot be combined with other fields").

### 1.2 Apply (`app/main.py`, in `_apply_item_patch`, BEFORE other handling)

If `patch.move_to is not None`:
1. `SELECT id, position FROM items WHERE list_id = ? AND done = ?
   ORDER BY position ASC, id ASC` (list_id = row["list_id"], done = row["done"]).
2. `ids = [r["id"] for r in rows]`; `cur = ids.index(row["id"])`.
3. If `cur == K` → return `(db.item_from_row(row), None)` (no-op, no bump).
4. Clamp `K = max(0, min(K, len(ids) - 1))`.
5. `new_ids = list(ids); new_ids.pop(cur); new_ids.insert(K, row["id"])`.
6. In the SAME transaction (caller holds BEGIN IMMEDIATE), renumber:
   `for i, rid in enumerate(new_ids): UPDATE items SET position = i, updated_at = now WHERE id = rid`.
   (Positions may collide across the two done-groups of one list — harmless:
   the sort separates groups by `done` first.)
7. Return `(db.fetch_item(db_conn, row["id"]), None)`.

`move` (up/down) handling stays unchanged. Shared PATCH inherits `move_to`
via `_apply_item_patch`; read-only still 403.

### 1.3 Tests (`tests/test_api.py`) — add

- `move_to` moves item to ordinal K; order after GET matches.
- `move_to` K == current index → no-op (200, order unchanged, no updated_at bump).
- `move_to` out-of-range K → clamped to last position (200).
- `move_to` scoped to list+done-group (never crosses).
- `move_to` combined with another field (e.g. `{"move_to": 1, "title": "x"}`) → 422.
- Shared edit `move_to` works; read-only `move_to` → 403.
- Regression: existing `move` tests still pass.

## 2. Frontend

### 2.1 Drop-slot math fix (`static/app.js`)

- **Live preview** (`dragTargetSlot`, used during pointermove): exclude the
  dragged row from the count and cap at `members.length - 1`:
  ```js
  function dragTargetSlot() {
    if (!dragCtx) return 0;
    const curMid = dragCtx.dragNaturalMid + state.rearrange.shift;
    let t = 0;
    for (let j = 0; j < dragCtx.members.length; j++) {
      if (j === dragCtx.members.length ? false : dragCtx.members[j].idx === state.rearrange.dragId) continue; // exclude self
      if (dragCtx.members[j].mid < curMid) t++;
    }
    return Math.max(0, Math.min(t, dragCtx.members.length - 1));
  }
  ```
  (members entries have `idx` = index in `currentItems()`; the dragged row is
  the one whose `idx` matches the dragId's current index — or store the
  dragged member's j in dragCtx and skip exactly that j.)
- **Drop ordinal** (new `dropOrdinal()`): on pointerup, compute K from LIVE
  rects: for each same-group member EXCEPT the dragged row,
  `liveMid = el.getBoundingClientRect().top + el.getBoundingClientRect().height / 2`;
  dragged live mid from its own live rect (includes translateY). K = count of
  members with liveMid < draggedLiveMid, capped at members.length - 1. This is
  the ground truth of the visible slot (fixes the second-from-bottom bug).

### 2.2 Optimistic drop + single-request persist

- On pointerup (end of drag):
  1. `K = dropOrdinal()`.
  2. Locally reorder `state.items` (in the current view's item array):
     remove the dragged item, insert at group ordinal K (relative to the
     start of its same-done group); `renderAll()` immediately → row snaps.
  3. `rearrangeSaving = true`; `await apiFetch(mutationPathFor(id), {method:'PATCH', body:{move_to: K}})`;
     `rearrangeSaving = false`; then `silentRefresh()` (resync with server).
     On error: `rearrangeSaving = false`; `silentRefresh()` (resync; no toast).
- Delete the sequential multi-PATCH loop (`persistDragOrder` with `res.k`).
- Arrows keep using single `{"move": dir}` (unchanged).

### 2.3 Overlay toolbar — zero layout shift (`app.js` + `style.css`)

- `syncRearrangeToolbar`: keep inserting a `#rearrange-toolbar` element, but
  style it as an OVERLAY: absolute, top-right of the list card
  (`#item-list`'s card gets `position: relative`), `z-index: 30`, compact:
  a short "Reorder" label + the Done button. NO long hint text (remove the
  "Hold a task and drag it" sentence — it was the selectable-text offender).
  The card's rows must not move: nothing in normal flow changes size/position.
- While rearrange active, add class `rearrange-active` to `<body>`:
  - `#item-list, .rearrange-toolbar { user-select: none; -webkit-user-select: none; }`
  - card stays same size/position (AC2: list top offset unchanged).
- The pill may overlay the first row's top-right corner (over the arrows);
  that is acceptable (rows are draggable, pill is dismissible). `pointer-events`
  only on the pill itself.
- While `state.rearrange.active`: ignore checkbox `change` events (guard in
  the change handler) so toggling can't race a drag-persist.

### 2.4 SW bump

- `static/sw.js`: `SHELL_CACHE` → `taskflow-shell-v8`.
- `tests/test_pwa.py`: assertion v7 → v8.

## 3. Verification (coordinator)

1. `node --check static/app.js`; full pytest (report exact count).
2. Playwright E2E: drag last item to the very bottom → lands LAST (reload
   confirms); entering rearrange mode → list top offset unchanged + no text
   selection; release → row lands immediately (optimistic), order persists
   after reload; arrows still work; Done/Escape exits.
3. Polling regression (two-tab live sync) still passes.
4. NO GITHUB PUSH — await Ahmed's approval.
