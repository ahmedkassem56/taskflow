# DESIGN — Auto-refresh polling (implementation contract)

Derived from `SPEC-polling.md` (APPROVED). Single source of truth for the
implementation agent. All changes are confined to `static/app.js` — no backend,
no CSS, no HTML changes. Read `static/app.js` in full before editing.

## 1. Architecture

Add a lightweight polling module inside `static/app.js` (no new files, no new
deps). A 5-second `setInterval` drives a single background refresh loop that:

- respects `document.visibilityState` (pause when hidden, immediate refresh on
  visible)
- never overlaps requests (in-flight guard)
- skips re-render when a modal is open
- preserves composer focus
- fails silently on network errors

## 2. Exact implementation

### 2.1 State additions (in the existing `state` object, ~line 70)

```js
poll: {
  timer: null,
  running: false,   // in-flight guard
  visible: true
}
```

### 2.2 Functions (place near `refreshApp` / after `refreshShare`)

```js
const POLL_INTERVAL_MS = 5000;

async function pollTick() {
  // guard: no overlap, only when visible, only when not already fetching
  if (state.poll.running || !state.poll.visible) return;
  state.poll.running = true;
  try {
    if (state.mode === 'share') {
      await loadShare(state.shareToken, { silent: true });
    } else {
      await fetchAppData(false);
      if (!modalOpen()) renderAll();
    }
  } catch (err) {
    // silent — background polls never toast
  } finally {
    state.poll.running = false;
  }
}

function startPolling() {
  if (state.poll.timer != null) return;
  state.poll.timer = setInterval(pollTick, POLL_INTERVAL_MS);
  document.addEventListener('visibilitychange', onVisibilityChange);
}

function stopPolling() {
  if (state.poll.timer != null) { clearInterval(state.poll.timer); state.poll.timer = null; }
  document.removeEventListener('visibilitychange', onVisibilityChange);
}

function onVisibilityChange() {
  state.poll.visible = document.visibilityState === 'visible';
  if (state.poll.visible) pollTick(); // immediate refresh on return
}
```

### 2.3 Integration points (EXACT)

1. **`init()`** (bottom of file): call `startPolling()` after `loadApp()` /
   `loadShare()` is kicked off, i.e. after the existing boot logic:
   ```js
   if (route.mode === 'share') { loadShare(route.token); }
   else { loadApp(); }
   registerSW();
   startPolling();
   ```
2. **Modal safety:** `pollTick` in app mode calls `renderAll()` only when
   `!modalOpen()`. In share mode, `loadShare(token, {silent:true})` sets
   `state.skeleton = false` and re-renders via its own path — guard that the
   same way: only call `loadShare` poll when `!modalOpen()`. (Simplest: wrap
   BOTH branches with `if (modalOpen()) return;` at the top of `pollTick`.)
   When the modal closes, `closeModal()` does not need to trigger a refresh —
   the next tick handles it (max 5s).

   FINAL RULE: `pollTick` returns immediately when `modalOpen()` is true.

3. **Composer focus (R4):** `fetchAppData` only replaces `state.lists` /
   `state.items` and `renderAll` re-renders innerHTML. The composer's
   `<input id="composer-title">` is NOT inside `#item-list`, so re-rendering
   the list does not clear it. No extra code needed — verify it stays focused
   in the E2E (AC5). If a regression appears, guard `renderItems` (do not
   re-render while `document.activeElement` is the composer input).

4. **Share mode:** share `loadShare` with `{silent:true}` already exists
   (`refreshShare` uses it). Polling calls it directly.

### 2.4 Order of checks in pollTick (final)

```
if (state.poll.running) return;
if (!state.poll.visible) return;
if (modalOpen()) return;
state.poll.running = true;
try { mode==='share' ? loadShare(token,{silent:true}) : (fetchAppData(false), renderAll()) }
catch { /* silent */ }
finally { state.poll.running = false; }
```

## 3. Verification (coordinator will run)

1. `node --check static/app.js` (or python ast fallback)
2. Existing pytest suite: `cd /home/hermes/projects/todo-app && .venv/bin/python -m pytest tests/ -q` → 44 passed
3. Playwright E2E (coordinator writes it in /tmp): two browser contexts on the
   same list; context A creates an item via API; context B must show it within
   ~6s with no user action; hidden-tab test: context B hidden → no poll
   requests (count via request listener) → visible again → refresh fires.
4. Bump `SHELL_CACHE` in `static/sw.js` v5 → v6 so the new app.js reaches
   installed clients; update the matching assertion in `tests/test_pwa.py`.
5. Commit + push to github.com/ahmedkassem56/taskflow.

## 4. Out of scope
Backend, SSE/WebSockets, diffs, presence, CRDT.
