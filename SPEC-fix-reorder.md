# SPEC — Reorder UX fixes (v2)

Status: APPROVED (Ahmed, 2026-09-02). Fixes three defects in the reorder
feature shipped in SPEC-reorder.md. Extends DESIGN-reorder.md; where this spec
conflicts, this wins. No push to GitHub until Ahmed tests and approves.

## 1. Defects

1. **Drop off-by-one at the bottom** — dragging an item to the end places it
   second-from-bottom instead of last.
2. **Layout drift + text selection on hold** — entering rearrange mode inserts
   a toolbar in document flow, pushing the whole list down (the held item
   escapes the finger) and the hint text is selectable (finger drag highlights
   text).
3. **Slow settle on release** — a multi-slot drag persists via N sequential
   PATCH {move} calls + full refetch; 1–2s before the row lands.

## 2. Fixes

### F1 — Correct drop slot math (frontend)
- Compute the target slot at drop time from **live** bounding rects of the
  same-group members, **excluding the dragged row's own rect** (it is
  mid-translation). Dragging to the very bottom must land in the last slot.
- The computed target is the group **ordinal** K (0-based index within the
  same done-group).

### F2 — Overlay toolbar, no layout shift (frontend + CSS)
- The rearrange toolbar renders as an **overlay** (position:absolute over the
  list's card, z-index above rows) so it never pushes the list down. The held
  item stays under the pointer when rearrange mode activates.
- While rearrange mode is active: `user-select: none` on the item list and the
  toolbar (no text highlighting during drag).
- The Done pill remains the exit affordance; the hint text stays.

### F3 — Single-request persistence (backend + frontend)
- New PATCH operation: `{"move_to": <group ordinal K>}` — moves the item to
  ordinal K within its same-list same-done group in **one** request (one
  transaction). Mutually exclusive with all other fields (including `move`).
- Frontend: on drop, reorder **optimistically** (row lands instantly), then
  one `PATCH {move_to: K}`; on success silent refetch, on failure silent
  refetch (resync). Remove the sequential-move loop.
- `move` (up/down arrows) stays as-is.

## 3. Non-goals
- No drag-and-drop library, no animation framework, no cross-list moves.

## 4. Acceptance criteria

1. AC1 — Dragging the last item to the very bottom lands it in the **last**
   slot (persists after reload).
2. AC2 — Entering rearrange mode causes **zero layout shift** (list top offset
   unchanged); held item stays under the pointer.
3. AC3 — No text is selected/highlighted during drag in rearrange mode.
4. AC4 — Releasing an item lands it in its slot **immediately** (optimistic);
   the persisted order matches after reload.
5. AC5 — `move_to` API: moves to ordinal K within the group; K==current is a
   no-op (200); K out of range clamps; combined with other fields → 422;
   scoped to list+done-group; shared edit mode works; read-only 403.
6. AC6 — Arrows still work; mode still exits via Done/Escape/tap-elsewhere.
7. AC7 — Suite green (updated + new move_to tests); `node --check` passes;
   polling and live-sync regression still pass.
