/* ============================================================
 * Taskflow — app.js  (DESIGN.md §4, §5)
 * Vanilla SPA: state, apiFetch wrapper, renderers, event delegation,
 * client-side routing (/ and /share/<token>), theme toggle + localStorage,
 * service-worker registration, toasts, modals.
 *
 * Conventions:
 *  - All API reads go through apiFetch(); every mutation re-renders the
 *    current view from fresh state (GET /api/lists + GET /api/items after
 *    POST/PATCH/DELETE) — single source of truth, no client-side merge.
 *  - Checkbox toggling is optimistic; it reconciles with the PATCH envelope
 *    {item, spawned} (gotcha 10: use resp.item, insert resp.spawned).
 *  - Event delegation: containers read data-action attributes.
 * ============================================================ */
'use strict';

/* ---------------- tiny helpers ---------------- */
const $ = (sel, root) => (root || document).querySelector(sel);
const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function svg(markup, cls) {
  return '<svg class="' + (cls || 'icon') + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + markup + '</svg>';
}

const ICONS = {
  menu: '<line x1="4" y1="7" x2="20" y2="7"/><line x1="4" y1="12" x2="20" y2="12"/><line x1="4" y1="17" x2="20" y2="17"/>',
  plus: '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
  search: '<circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.5" y2="16.5"/>',
  x: '<line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/>',
  chevron: '<path d="M6 9l6 6 6-6"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  pencil: '<path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/>',
  link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
  trash: '<path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/>',
  copy: '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
  inbox: '<polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>',
  list: '<line x1="9" y1="6" x2="21" y2="6"/><line x1="9" y1="12" x2="21" y2="12"/><line x1="9" y1="18" x2="21" y2="18"/><circle cx="4" cy="6" r="1.4"/><circle cx="4" cy="12" r="1.4"/><circle cx="4" cy="18" r="1.4"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/>',
  moon: '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
  download: '<path d="M12 3v11"/><path d="M7 10l5 5 5-5"/><path d="M4 19h16"/>',
  repeat: '<path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/>',
  checkCircle: '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><path d="M22 4 12 14.01l-3-3"/>',
  alert: '<path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
  info: '<circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/>'
};

function icon(name, cls) { return svg(ICONS[name] || '', cls); }

/* ---------------- state ---------------- */
const state = {
  mode: 'app',                 // 'app' | 'share'
  shareToken: null,
  share: null,                 // { list, items, permission } once loaded
  shareError: false,
  lists: [],                   // app mode lists
  items: [],                   // app mode items (current filter)
  view: { type: 'all', listId: null },   // 'all' | 'list'
  status: 'all',               // 'all' | 'pending' | 'done'
  q: '',
  viewKey: null,               // cache key of the view currently rendered
  skeleton: false,
  shareLinks: [],              // session-scoped active share links (no GET endpoint)
  modal: null,                 // { kind, opener, refocusSel }
  pendingDelete: null,         // { kind, id, token, listName, extra }
  deferredPrompt: null,
  installed: false,
  sidebarOpener: null,
  searchOpener: null,
  rearrange: {
    active: false,
    suppressClick: false,
    doneGroup: 'pending'   // 'pending' | 'done' — group being rearranged
  },
  poll: {
    timer: null,
    running: false,   // in-flight guard
    visible: true
  }
};

function listName(id) {
  const l = state.lists.find((x) => x.id === id);
  return l ? l.name : '';
}

function currentItems() {
  return state.mode === 'share' ? (state.share ? state.share.items : []) : state.items;
}

function findItem(id) {
  return currentItems().find((x) => x.id === id) || null;
}

/* Canonical sort mirror of the server ORDER BY (§2.0). */
const PRIO_ORDER = { high: 0, medium: 1, low: 2, none: 3 };
function cmpItems(a, b) {
  const da = a.done ? 1 : 0, db = b.done ? 1 : 0;
  if (da !== db) return da - db;
  const pa = PRIO_ORDER[a.priority] != null ? PRIO_ORDER[a.priority] : 3;
  const pb = PRIO_ORDER[b.priority] != null ? PRIO_ORDER[b.priority] : 3;
  if (pa !== pb) return pa - pb;
  const na = a.due_date ? 0 : 1, nb = b.due_date ? 0 : 1;
  if (na !== nb) return na - nb;
  if ((a.due_date || '') !== (b.due_date || '')) return a.due_date < b.due_date ? -1 : 1;
  if (a.created_at !== b.created_at) return a.created_at < b.created_at ? -1 : 1;
  return a.id - b.id;
}

function isEditable(el) {
  const t = el && el.tagName ? el.tagName.toLowerCase() : '';
  return t === 'input' || t === 'textarea' || t === 'select' || el && el.isContentEditable;
}

/* ---------------- apiFetch wrapper ---------------- */
class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

async function apiFetch(path, opts) {
  opts = opts || {};
  const init = {
    method: opts.method || 'GET',
    headers: { 'Accept': 'application/json' }
  };
  if (opts.body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(opts.body);
  }
  let res;
  try {
    res = await fetch(path, init);
  } catch (err) {
    throw new ApiError('Cannot reach the server. Check your connection.', 0);
  }
  if (res.status === 204) return null;
  let data = null;
  try { data = await res.json(); } catch (err) { /* no JSON body */ }
  if (!res.ok) {
    const msg = data && typeof data.detail === 'string' ? data.detail : 'Request failed (' + res.status + ')';
    throw new ApiError(msg, res.status);
  }
  return data;
}

/* ---------------- dates (calendar dates only, YYYY-MM-DD — §1.6) ---------------- */
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function pad2(n) { return (n < 10 ? '0' : '') + n; }

function todayISO() {
  const d = new Date();
  return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate());
}

/* "Sep 5" (+ ", 2026" when not the current year) */
function fmtDateShort(iso) {
  if (!iso) return '';
  const parts = iso.split('-').map(Number);
  const d = new Date(parts[0], parts[1] - 1, parts[2]);
  const base = MONTHS[d.getMonth()] + ' ' + d.getDate();
  return d.getFullYear() === new Date().getFullYear() ? base : base + ', ' + d.getFullYear();
}

function dueKind(iso) {
  const t = todayISO();
  if (iso < t) return 'overdue';
  if (iso === t) return 'today';
  return 'future';
}

function dueLabel(iso) {
  if (!iso) return '';
  const k = dueKind(iso);
  if (k === 'today') return 'Today';
  if (k === 'overdue') return 'Overdue';
  return 'Due ' + fmtDateShort(iso);
}

/* UI quantity formatting: never "1.0" (gotcha 8). */
function fmtQty(n) {
  return String(n).replace(/\.0+$/, '');
}

const RECURRENCE_LABEL = { daily: 'Daily', weekly: 'Weekly', monthly: 'Monthly' };
function recurrenceLabel(item) {
  if (!item.recurrence || item.recurrence === 'none') return '';
  if (item.recurrence === 'custom') return 'Every ' + item.recurrence_interval + ' day' + (item.recurrence_interval === 1 ? '' : 's');
  return RECURRENCE_LABEL[item.recurrence] || item.recurrence;
}

const PRIORITY_LABEL = { high: 'High', medium: 'Medium', low: 'Low' };

/* ---------------- toasts (§4.5) ---------------- */
const TOAST_ICON = { success: 'checkCircle', error: 'alert', info: 'info' };

function toast(message, type, opts) {
  type = type || 'info';
  opts = opts || {};
  const root = $('#toast-root');
  if (!root) return;
  const el = document.createElement('div');
  el.className = 'toast toast-' + type;
  el.setAttribute('role', 'status');
  el.innerHTML = '<span class="toast-icon">' + icon(TOAST_ICON[type] || 'info') + '</span>' +
    '<span class="toast-message">' + esc(message) + '</span>';
  if (opts.onClick) {
    el.style.cursor = 'pointer';
    el.addEventListener('click', opts.onClick);
  }
  root.appendChild(el);
  /* stack max 3 */
  while (root.children.length > 3) root.firstChild.remove();
  const duration = opts.duration != null ? opts.duration : 2800;
  const timer = setTimeout(function () { dismiss(el); }, duration);
  el.addEventListener('click', function () {
    if (opts.onClick) { clearTimeout(timer); return; }
    dismiss(el);
  });
  function dismiss(node) {
    if (!node.parentNode) return;
    node.classList.add('is-leaving');
    setTimeout(function () { node.remove(); }, 180);
  }
}
const toastSuccess = (m, o) => toast(m, 'success', o);
const toastError = (m, o) => toast(m, 'error', o);
const toastInfo = (m, o) => toast(m, 'info', o);

/* ---------------- theme (§4.5/§5.5) ---------------- */
const THEME_META = { dark: '#17171B', light: '#FAFAFB' };

function currentTheme() {
  return document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
}

function syncThemeMeta(t) {
  const c = THEME_META[t] || THEME_META.light;
  const dm = $('#theme-color-dark');
  const lm = $('#theme-color-light');
  if (dm) dm.setAttribute('content', c);
  if (lm) lm.setAttribute('content', c);
}

function paintThemeButton(t) {
  const btn = $('#btn-theme');
  if (!btn) return;
  const dark = t === 'dark';
  btn.innerHTML = icon(dark ? 'sun' : 'moon');
  btn.setAttribute('aria-label', dark ? 'Switch to light theme' : 'Switch to dark theme');
  btn.setAttribute('title', dark ? 'Switch to light theme' : 'Switch to dark theme');
}

function applyTheme(t) {
  document.documentElement.setAttribute('data-theme', t);
  try { localStorage.setItem('taskflow-theme', t); } catch (e) { /* private mode */ }
  syncThemeMeta(t);
  paintThemeButton(t);
}

function toggleTheme() {
  applyTheme(currentTheme() === 'dark' ? 'light' : 'dark');
}

/* ---------------- routing (§4.6) ---------------- */
function parseRoute() {
  const m = location.pathname.match(/^\/share\/([A-Za-z0-9_-]{16,})\/?$/);
  if (m) return { mode: 'share', token: m[1] };
  return { mode: 'app', token: null };
}

/* ============================================================
 * Rendering — app mode
 * ============================================================ */

function itemsQueryString() {
  const p = new URLSearchParams();
  if (state.view.type === 'list' && state.view.listId != null) p.set('list_id', state.view.listId);
  if (state.status === 'pending') p.set('status', 'pending');
  else if (state.status === 'done') p.set('status', 'done');
  if (state.q) p.set('q', state.q);
  const s = p.toString();
  return s ? '?' + s : '';
}

function listTotals() {
  let total = 0, pending = 0;
  state.lists.forEach(function (l) {
    total += l.item_count;
    pending += l.pending_count;
  });
  return { total: total, done: total - pending };
}

async function fetchAppData(showSkeleton) {
  if (showSkeleton) { state.skeleton = true; renderItems(); }
  const [lists, items] = await Promise.all([
    apiFetch('/api/lists'),
    apiFetch('/api/items' + itemsQueryString())
  ]);
  state.lists = lists;
  state.items = items;
  state.skeleton = false;
}

async function refreshApp() {
  await fetchAppData(false);
  renderAll();
}

/* ---------------- view persistence ----------------
   The app reloads to the last-opened list (SPEC-v3 §R6.2): selection survives
   refresh / PWA relaunch. Stored separately from the theme key. */
const VIEW_KEY = 'taskflow:view:v1';

function currentViewType() {
  return state.view ? state.view.type : 'all';
}

function setViewParams(type, listId) {
  state.view = { type: type, listId: listId != null ? Number(listId) : null };
}

function persistView() {
  try {
    localStorage.setItem(VIEW_KEY, JSON.stringify(state.view || { type: 'all', listId: null }));
  } catch (err) { /* storage unavailable — ignore */ }
}

function restoreView() {
  try {
    const raw = localStorage.getItem(VIEW_KEY);
    if (!raw) return null;
    const v = JSON.parse(raw);
    if (v && (v.type === 'all' || v.type === 'list')) {
      return { type: v.type, listId: v.type === 'list' ? Number(v.listId) : null };
    }
  } catch (err) { /* malformed — fall through */ }
  return null;
}

function viewStorageKey() {
  return state.view ? (state.view.type === 'all' ? 'all' : 'list:' + state.view.listId) : null;
}

function enterView(type, listId) {
  exitRearrange();
  const view = { type: type, listId: listId != null ? Number(listId) : null };
  setViewParams(view.type, view.listId);
  state.viewKey = viewStorageKey();
  persistView();
  state.skeleton = true;
  renderAll();
  fetchAppData(true)
    .then(renderAll)
    .catch(function (err) { state.skeleton = false; toastError(err.message); renderAll(); });
}

async function loadApp() {
  exitRearrange();
  state.mode = 'app';
  state.viewKey = null;
  document.body.classList.remove('share-mode');
  const hamburger = $('#btn-sidebar');
  const brand = $('#brand');
  const shareIdentity = $('#share-identity');
  const openApp = $('#open-app-link');
  if (hamburger) hamburger.hidden = false;
  if (brand) brand.hidden = false;
  if (shareIdentity) shareIdentity.hidden = true;
  if (openApp) openApp.hidden = true;
  $('#sidebar').hidden = false;
  $('#filter-bar').hidden = false;
  try {
    await fetchAppData(true);
  } catch (err) {
    state.skeleton = false;
    renderAll();
    toastError(err.message);
    return;
  }
  const saved = restoreView();
  if (saved && saved.type === 'list' &&
    state.lists.some(function (l) { return l.id === saved.listId; })) {
    setViewParams('list', saved.listId);
  } else {
    setViewParams('all', null);
  }
  state.viewKey = viewStorageKey();
  renderAll();
}

function renderAll() {
  renderSidebar();
  renderViewHeader();
  renderComposer();
  renderItems();
}

/* --- sidebar --- */
function renderSidebar() {
  const nav = $('#list-nav');
  if (!nav) return;
  const totalPending = state.lists.reduce(function (acc, l) { return acc + l.pending_count; }, 0);
  const activeAll = state.view.type === 'all';
  let html = '';
  html += '<div role="button" tabindex="0" class="nav-row' + (activeAll ? ' active' : '') + '" data-action="select-view" data-view="all" ' +
    (activeAll ? 'aria-current="true"' : '') + ' title="All tasks">' +
    '<span class="nav-ico">' + icon('inbox') + '</span>' +
    '<span class="nav-name">All tasks</span>' +
    (totalPending > 0 ? '<span class="nav-count">' + totalPending + '</span>' : '') +
    '</div>';
  state.lists.forEach(function (l) {
    const active = state.view.type === 'list' && state.view.listId === l.id;
    html += '<div role="button" tabindex="0" class="nav-row' + (active ? ' active' : '') + '" data-action="select-view" data-view="list" data-id="' + l.id + '" ' +
      (active ? 'aria-current="true"' : '') + ' title="' + esc(l.name) + '">' +
      '<span class="nav-ico">' + icon('list') + '</span>' +
      '<span class="nav-name">' + esc(l.name) + '</span>' +
      (l.pending_count > 0 ? '<span class="nav-count">' + l.pending_count + '</span>' : '') +
      '<span class="row-actions">' +
      '<button type="button" class="icon-btn small" data-action="rename-list" data-id="' + l.id + '" aria-label="Rename list" title="Rename">' + icon('pencil') + '</button>' +
      '<button type="button" class="icon-btn small" data-action="open-share" data-id="' + l.id + '" aria-label="Share list" title="Share">' + icon('link') + '</button>' +
      '<button type="button" class="icon-btn small" data-action="delete-list" data-id="' + l.id + '" aria-label="Delete list" title="Delete">' + icon('trash') + '</button>' +
      '</span>' +
      '</div>';
  });
  nav.innerHTML = html;
}

/* --- view header --- */
function currentViewMeta() {
  if (state.view.type === 'list') {
    const l = state.lists.find(function (x) { return x.id === state.view.listId; });
    if (l) return { title: l.name, done: l.item_count - l.pending_count, total: l.item_count, isList: true };
    return null;
  }
  const t = listTotals();
  return { title: 'All tasks', done: t.done, total: t.total, isList: false };
}

function renderViewHeader() {
  const meta = currentViewMeta();
  const titleEl = $('#view-title');
  const subEl = $('#view-subtitle');
  const actionsEl = $('#view-actions');
  if (!meta) return;
  titleEl.textContent = meta.title;
  subEl.textContent = meta.total ? meta.done + ' of ' + meta.total + ' done' : 'No tasks yet';
  /* rearrange mode: the header is the action bar (list name + Done) — the
     Share action hides while reordering. syncRearrangeToolbar() rebuilds it. */
  if (state.rearrange.active) { syncRearrangeToolbar(); return; }
  const header = $('#view-header');
  if (header) header.classList.remove('rearrange-bar');
  let actions = '';
  if (meta.isList) {
    actions = '<button type="button" class="btn btn-ghost" data-action="open-share" data-id="' + state.view.listId + '">' +
      icon('link') + '<span>Share</span></button>';
  }
  actionsEl.innerHTML = actions;
}

/* --- composer (§4.5) --- */
function renderComposer() {
  const composer = $('#composer');
  const listField = $('#composer-list-field');
  const select = $('#composer-list');
  if (!composer) return;
  const hasLists = state.lists.length > 0;
  composer.hidden = !hasLists;
  if (!hasLists) return;
  const isAll = state.view.type === 'all';
  listField.hidden = !isAll;
  if (isAll && select) {
    const prev = select.value;
    select.innerHTML = state.lists.map(function (l) {
      return '<option value="' + l.id + '">' + esc(l.name) + '</option>';
    }).join('');
    if (prev && Array.from(select.options).some(function (o) { return o.value === prev; })) select.value = prev;
  }
}

function composerListId() {
  if (state.view.type === 'list') return state.view.listId;
  const sel = $('#composer-list');
  if (sel && sel.value) return Number(sel.value);
  return state.lists.length ? state.lists[0].id : null;
}

function resetComposer() {
  $('#composer-title').value = '';
  $('#composer-notes').value = '';
  $('#composer-due').value = '';
  $('#composer-priority').value = 'none';
  $('#composer-quantity').value = '1';
  $('#composer-recurrence').value = 'none';
  $('#composer-interval').value = '1';
  $('#composer-interval-field').hidden = true;
  $('#composer-title').focus();
}

function setComposerOptionsVisible(open) {
  const panel = $('#composer-options');
  const btn = $('#composer-options-toggle');
  panel.hidden = !open;
  btn.setAttribute('aria-expanded', open ? 'true' : 'false');
  if (open) {
    const rec = $('#composer-recurrence');
    $('#composer-interval-field').hidden = rec.value !== 'custom';
  }
}

function readComposerPayload() {
  const title = $('#composer-title').value.trim();
  if (!title) { toastError('Give the task a title.'); return null; }
  const listId = composerListId();
  if (listId == null) { toastError('Create a list first.'); return null; }
  const notes = $('#composer-notes').value.trim();
  const recurrence = $('#composer-recurrence').value;
  let interval = null;
  if (recurrence === 'custom') {
    interval = parseInt($('#composer-interval').value, 10);
    if (!(interval >= 1)) { toastError('Enter how many days between repetitions.'); return null; }
  }
  let quantity = parseFloat($('#composer-quantity').value);
  if (!(quantity > 0) || Number.isNaN(quantity)) quantity = 1;
  const payload = {
    list_id: listId,
    title: title,
    notes: notes ? notes : null,
    priority: $('#composer-priority').value,
    due_date: $('#composer-due').value ? $('#composer-due').value : null,
    quantity: quantity,
    recurrence: recurrence
  };
  if (interval != null) payload.recurrence_interval = interval;
  return payload;
}

async function submitComposer() {
  const payload = readComposerPayload();
  if (!payload) return;
  const isShareEdit = state.mode === 'share';
  try {
    if (isShareEdit) {
      delete payload.list_id;
      await apiFetch('/api/shared/' + state.shareToken + '/items', { method: 'POST', body: payload });
      await refreshShare(true);
    } else {
      await apiFetch('/api/items', { method: 'POST', body: payload });
      await refreshApp();
    }
    resetComposer();
    setComposerOptionsVisible(false);
  } catch (err) {
    toastError(err.message);
  }
}

/* --- item row rendering --- */
function chipHtml(label, extraClass, dotColor) {
  let dot = '';
  if (dotColor) dot = '<span class="chip-dot" style="background:' + dotColor + '"></span>';
  return '<span class="chip' + (extraClass ? ' ' + extraClass : '') + '">' + dot + '<span class="chip-label">' + esc(label) + '</span></span>';
}

function metaHtml(item, opts) {
  const parts = [];
  if (opts.showListName) parts.push(chipHtml(listName(item.list_id), 'chip-listname'));
  if (item.priority && item.priority !== 'none' && PRIORITY_LABEL[item.priority]) {
    parts.push(chipHtml(PRIORITY_LABEL[item.priority], 'chip-priority-' + item.priority));
  }
  if (item.due_date) {
    const kind = dueKind(item.due_date);
    const cls = kind === 'overdue' ? 'chip-due overdue' : (kind === 'today' ? 'chip-due today' : 'chip-due');
    parts.push(chipHtml(dueLabel(item.due_date), cls));
  }
  if (item.quantity && item.quantity !== 1) {
    parts.push('<span class="meta-qty">×' + esc(fmtQty(item.quantity)) + '</span>');
  }
  const rec = recurrenceLabel(item);
  if (rec) {
    parts.push('<span class="chip chip-rec">' + icon('repeat', 'chip-ico') + '<span class="chip-label">' + esc(rec) + '</span></span>');
  }
  return parts.join('');
}

function rowHtml(item, opts) {
  const done = !!item.done;
  const readOnly = opts.readOnly;
  /* Rows always render their normal (edit/delete) actions. When rearrange
     mode is ON, CSS (body.rearrange-active) swaps the actions to move arrows
     for the active group and dims the other group — the DOM is NOT re-built
     on mode entry, so a held row survives the transition and drag works on
     the very first gesture. */
  const title = item.title;
  const checkLabel = done ? 'Mark "' + title + '" not done' : 'Mark "' + title + '" done';
  const meta = metaHtml(item, opts);
  let cls = 'item-row' + (done ? ' done' : '');
  let actions = '';
  if (!readOnly) {
    actions =
      '<div class="row-actions">' +
      '<button type="button" class="icon-btn small" data-action="edit-item" data-id="' + item.id + '" aria-label="Edit task" title="Edit">' + icon('pencil') + '</button>' +
      '<button type="button" class="icon-btn small" data-action="delete-item" data-id="' + item.id + '" aria-label="Delete task" title="Delete">' + icon('trash') + '</button>' +
      '<span class="move-arrows">' +
      '<button type="button" class="icon-btn small move-arrow" data-action="move-up" data-id="' + item.id + '" aria-label="Move up" title="Move up">\u2191</button>' +
      '<button type="button" class="icon-btn small move-arrow" data-action="move-down" data-id="' + item.id + '" aria-label="Move down" title="Move down">\u2193</button>' +
      '</span>' +
      '</div>';
  }
  return '<li class="' + cls + '" data-id="' + item.id + '" data-done="' + (done ? '1' : '0') + '">' +
    '<label class="checkbox" title="' + esc(checkLabel) + '">' +
    '<input type="checkbox" class="checkbox-input" data-action="toggle-done" data-id="' + item.id + '" ' +
    (done ? 'checked ' : '') + (readOnly ? 'disabled ' : '') + 'aria-label="' + esc(checkLabel) + '">' +
    icon('check', 'check-svg') +
    '</label>' +
    '<div class="item-body">' +
    '<div class="item-title">' + esc(title) + '</div>' +
    (meta ? '<div class="item-meta">' + meta + '</div>' : '') +
    '</div>' +
    actions +
    '</li>';
}

function skeletonHtml() {
  let out = '';
  for (let i = 0; i < 5; i++) {
    out += '<li class="skeleton-row" aria-hidden="true"><div class="sk-bar title"></div><div class="sk-bar meta' + (i % 2 ? ' short' : '') + '"></div></li>';
  }
  return out;
}

function stateHtml(iconName, iconCls, title, hint, ctaHtml) {
  return '<li class="state">' +
    '<div class="state-icon' + (iconCls ? ' ' + iconCls : '') + '">' + icon(iconName) + '</div>' +
    '<h2 class="state-title">' + esc(title) + '</h2>' +
    (hint ? '<p class="state-hint">' + esc(hint) + '</p>' : '') +
    (ctaHtml ? ctaHtml : '') +
    '</li>';
}

function ctaBtn(label, action, extra) {
  return '<button type="button" class="btn ' + (extra || 'btn-ghost') + '" data-action="' + action + '">' + esc(label) + '</button>';
}

function filtersActive() {
  return !!state.q || state.status !== 'all';
}

function clearFilters() {
  exitRearrange();
  state.q = '';
  state.status = 'all';
  const input = $('#search-input');
  if (input) { input.value = ''; syncSearchClear(); }
  syncSegmented();
  if (state.mode === 'share') renderItems();
  else refreshApp();
}

function renderEmptyState() {
  const list = $('#item-list');
  destroySortable();   // no rows → nothing to drag
  const hasFilters = filtersActive();
  if (state.mode === 'share') {
    if (state.shareError) {
      list.classList.add('plain');
      list.innerHTML = stateHtml('link', 'tinted-muted', 'This link is invalid or has been revoked',
        'The share may have been deleted or mistyped.', '');
      return;
    }
    const readOnly = state.share.permission !== 'edit';
    if (currentItems().length === 0) {
      if (hasFilters) {
        list.classList.remove('plain');
        list.innerHTML = stateHtml('search', '', 'No matching tasks', 'Try a different search or filter.',
          ctaBtn('Clear filters', 'clear-filters'));
      } else {
        list.classList.remove('plain');
        list.innerHTML = stateHtml('list', '', 'Nothing here yet',
          readOnly ? 'This shared list has no tasks.' : 'Add the first task to this list.',
          readOnly ? '' : ctaBtn('Add a task', 'focus-composer', 'btn-accent'));
      }
    }
    return;
  }
  /* app mode */
  if (state.lists.length === 0) {
    list.classList.remove('plain');
    list.innerHTML = stateHtml('list', '', 'Create your first list',
      'Lists keep your tasks organized. Start one and add tasks to it.',
      ctaBtn('New list', 'new-list', 'btn-accent'));
    return;
  }
  if (currentItems().length === 0) {
    if (hasFilters) {
      list.classList.remove('plain');
      list.innerHTML = stateHtml('search', '', 'No matching tasks', 'Try a different search or filter.',
        ctaBtn('Clear filters', 'clear-filters'));
      return;
    }
    const meta = currentViewMeta();
    const hint = state.view.type === 'list' && meta
      ? 'Add your first task to "' + meta.title + '".'
      : 'Add your first task and it will show up here.';
    list.classList.remove('plain');
    list.innerHTML = stateHtml('list', '', 'Nothing here yet', hint,
      ctaBtn('Add a task', 'focus-composer', 'btn-accent'));
    return;
  }
  /* items exist but no rendering happened (shouldn't reach) */
  list.innerHTML = '';
}

function renderItems() {
  const list = $('#item-list');
  if (!list) return;
  const items = currentItems();
  if (state.mode === 'share' && state.shareError) { renderEmptyState(); return; }
  if (state.skeleton) {
    list.classList.remove('plain');
    list.innerHTML = skeletonHtml();
    return;
  }
  if (!items.length) { renderEmptyState(); return; }
  if (state.rearrange.active) {
    /* nothing left in the rearranged group (e.g. last item toggled done) → leave mode */
    const groupDone = state.rearrange.doneGroup === 'done';
    const hasGroup = items.some(function (it) { return !!it.done === groupDone; });
    if (!hasGroup) { exitRearrange(); return; }
  }
  list.classList.remove('plain');
  const readOnly = state.mode === 'share' && state.share.permission !== 'edit';
  const showListName = state.mode === 'app' && state.view.type === 'all';
  list.innerHTML = items.map(function (item) {
    return rowHtml(item, {
      showListName: showListName,
      readOnly: readOnly
    });
  }).join('');
  /* the list DOM was rebuilt: (re)bind SortableJS if rearrange mode is on */
  if (state.rearrange.active && !readOnly) createSortable();
  else if (!state.rearrange.active && sortable) destroySortable();
  syncRearrangeToolbar();
}

/* ---------------- share view (§4.8) ---------------- */
async function loadShare(token, opts) {
  opts = opts || {};
  if (!opts.silent) exitRearrange();
  state.mode = 'share';
  state.shareToken = token;
  state.share = null;
  state.shareError = false;
  document.body.classList.add('share-mode');
  const hamburger = $('#btn-sidebar');
  const brand = $('#brand');
  const shareIdentity = $('#share-identity');
  const openApp = $('#open-app-link');
  if (hamburger) hamburger.hidden = true;
  if (brand) brand.hidden = true;
  if (shareIdentity) shareIdentity.hidden = false;
  if (openApp) openApp.hidden = false;
  $('#sidebar').hidden = true;
  $('#filter-bar').hidden = false;
  $('#view-header').hidden = true;
  renderShareIdentity(null);
  if (!opts.silent) { state.skeleton = true; renderItems(); }
  try {
    const data = await apiFetch('/api/shared/' + token);
    state.share = {
      list: data.list,
      items: data.items,
      permission: data.permission,
      token: token
    };
    state.skeleton = false;
  } catch (err) {
    state.skeleton = false;
    state.shareError = true;
    if (err.status === 404) {
      /* branded "revoked" state; no retry loop */
    } else if (!opts.silent) {
      /* silent (poll / reorder) refreshes never toast */
      toastError(err.message);
    }
  }
  renderShareAll();
}

function renderShareIdentity(share) {
  const identity = $('#share-identity');
  if (!identity) return;
  const titleEl = $('#share-title-text');
  const badgeEl = $('#share-permission-badge');
  const subEl = $('#share-subtitle');
  if (!share) {
    titleEl.textContent = 'Shared list';
    badgeEl.textContent = '';
    subEl.textContent = '';
    return;
  }
  const readOnly = share.permission !== 'edit';
  titleEl.textContent = share.list.name;
  badgeEl.textContent = readOnly ? 'Read-only' : 'Can edit';
  badgeEl.className = 'perm-badge ' + (readOnly ? 'read' : 'edit');
  const done = share.list.item_count - share.list.pending_count;
  subEl.textContent = share.list.item_count
    ? done + ' of ' + share.list.item_count + ' done'
    : 'No tasks yet';
}

function shareFilteredItems() {
  const items = state.share ? state.share.items : [];
  let out = items;
  if (state.status === 'pending') out = out.filter(function (i) { return !i.done; });
  else if (state.status === 'done') out = out.filter(function (i) { return i.done; });
  if (state.q) {
    const q = state.q.toLowerCase();
    out = out.filter(function (i) {
      return i.title.toLowerCase().indexOf(q) !== -1 ||
        (i.notes || '').toLowerCase().indexOf(q) !== -1;
    });
  }
  return out;
}

function renderShareAll() {
  const share = state.share;
  renderShareIdentity(share);
  renderShareComposer();
  renderShareItems();
}

function renderShareComposer() {
  const composer = $('#composer');
  const listField = $('#composer-list-field');
  const share = state.share;
  if (!composer) return;
  if (!share || share.permission !== 'edit') { composer.hidden = true; return; }
  composer.hidden = false;
  listField.hidden = true; /* shared adds bind to the shared list server-side */
  $('#composer-title').placeholder = 'Add a task…';
}

function renderShareItems() {
  const list = $('#item-list');
  if (!list) return;
  if (state.shareError) { renderEmptyState(); return; }
  if (state.skeleton) {
    list.classList.remove('plain');
    list.innerHTML = skeletonHtml();
    return;
  }
  const filtered = shareFilteredItems();
  if (!filtered.length) {
    /* reuse app empty-state logic for the no-results / empty variants */
    const prevItems = state.items;
    state.items = [];
    renderEmptyState();
    state.items = prevItems;
    return;
  }
  if (state.rearrange.active) {
    const groupDone = state.rearrange.doneGroup === 'done';
    const hasGroup = filtered.some(function (it) { return !!it.done === groupDone; });
    if (!hasGroup) { exitRearrange(); return; }
  }
  list.classList.remove('plain');
  const readOnly = state.share.permission !== 'edit';
  list.innerHTML = filtered.map(function (item) {
    return rowHtml(item, {
      showListName: false,
      readOnly: readOnly
    });
  }).join('');
  /* the list DOM was rebuilt: (re)bind SortableJS if rearrange mode is on */
  if (state.rearrange.active && !readOnly) createSortable();
  else if (!state.rearrange.active && sortable) destroySortable();
  syncRearrangeToolbar();
}

async function refreshShare(silent) {
  await loadShare(state.shareToken, { silent: !!silent });
}

/* ============================================================
 * Hold-to-reorder rearrange mode (SortableJS)
 * ============================================================ */
const HOLD_MS = 450;          // long-press before a drag starts (Sortable delay)
let rearrangeSaving = false;  // true while a drop-PATCH is in flight
let sortable = null;          // live SortableJS instance on #item-list

function rearrangePermitted() {
  /* 'All tasks' is a cross-list view: items belong to different lists, so a
     "position" there is meaningless — reorder only makes sense inside one
     list. Hold-to-reorder and the arrows are disabled in the All view. */
  if (state.mode === 'app' && state.view.type === 'all') return false;
  if (state.mode === 'share') return !!(state.share && state.share.permission === 'edit');
  return true;
}

function enterRearrange(itemId) {
  if (state.rearrange.active) return;
  const item = findItem(itemId);
  if (!item || !rearrangePermitted()) return;
  state.rearrange.active = true;
  /* the group being rearranged is the group of the row the user grabbed */
  state.rearrange.doneGroup = !!item.done ? 'done' : 'pending';
  /* swallow the click that ends this long-press (it must not open edit) */
  state.rearrange.suppressClick = true;
  /* rearranging disables text selection inside the list */
  document.body.classList.add('rearrange-active');
  document.body.classList.toggle('rearrange-done', state.rearrange.doneGroup === 'done');
  /* Rows are NEVER re-rendered here — SortableJS holds a live reference to
     the element being dragged, so rebuilding the list mid-gesture would kill
     the drag. The header swaps to the action bar; CSS on body.rearrange-active
     reveals the move arrows. */
  if (state.mode === 'share') renderShareItems();
  else renderViewHeader();
  /* while mode is on, subsequent drags start instantly (no re-hold) */
  if (sortable) sortable.option('delay', 0);
}

function exitRearrange() {
  if (!state.rearrange.active) return;
  destroySortable();
  state.rearrange.active = false;
  state.rearrange.suppressClick = false;
  state.rearrange.doneGroup = 'pending';
  document.body.classList.remove('rearrange-active', 'rearrange-done');
  /* Defer the re-render: exiting runs inside the Done button's click handler,
     and renderAll() swaps the Share button in at the SAME coordinates the
     in-flight click is targeting — the Share button would receive the tail of
     the same click and open the share modal. Next frame = clean. */
  if (state.mode === 'share') requestAnimationFrame(renderShareItems);
  else requestAnimationFrame(renderAll);
}

/* Cancel any armed long-press (leave rearrange mode) when the user opens a
   different view or the current list is deleted — see select-view / delete. */
function leaveRearrangeIfActive() {
  if (state.rearrange.active) exitRearrange();
}

/* Rearrange UI lives in the VIEW HEADER (normal flow — never overlaps rows):
   when rearrange mode is active the header becomes a compact action bar with
   the list name + a Done button; the row arrows are ALWAYS fully visible on
   the active group (no hover-hiding, no overlay). */
function syncRearrangeToolbar() {
  const header = $('#view-header');
  if (!header || header.hidden) return;   // share view has no app header
  const existing = $('#rearrange-toolbar');
  if (!state.rearrange.active) {
    if (existing) existing.remove();
    header.classList.remove('rearrange-bar');
    if (state.mode === 'app') renderViewHeader();
    return;
  }
  /* rearrange active: rebuild the header as the action bar */
  const meta = currentViewMeta();
  const titleEl = $('#view-title');
  const subEl = $('#view-subtitle');
  const actionsEl = $('#view-actions');
  header.classList.add('rearrange-bar');
  if (meta) {
    titleEl.textContent = meta.title;
    subEl.textContent = '';
  }
  actionsEl.innerHTML =
    '<button type="button" class="btn btn-accent" data-action="exit-rearrange">' +
    icon('check') + '<span>Done</span></button>';
}

/* ---------------- silent persistence (no toasts) ---------------- */
async function silentRefresh() {
  try {
    if (state.mode === 'share') {
      await loadShare(state.shareToken, { silent: true });
    } else {
      await fetchAppData(false);
      renderAll();
    }
  } catch (err) {
    /* silent — reorder refreshes never toast */
  }
}

/* Arrow buttons: single PATCH {move}, silent refresh, mode stays active. */
async function apiMoveItem(id, dir) {
  if (rearrangeSaving) return;
  rearrangeSaving = true;
  try {
    await apiFetch(mutationPathFor(id), { method: 'PATCH', body: { move: dir } });
  } catch (err) {
    /* silent — boundary no-ops and network failures never toast */
  } finally {
    rearrangeSaving = false;
  }
  await silentRefresh();
}

/* ---------------- SortableJS drag engine ---------------- */

/* SortableJS only allows reordering WITHIN the active group: an element from
   the pending group cannot be dropped among done items or vice versa. We
   render pending + done as one Sortable list (so the ghost can move freely),
   and onMove vetoes any crossing of the group boundary. */
function sortableOnMove(evt) {
  const activeDone = document.body.classList.contains('rearrange-done');
  const fromDone = evt.dragged.dataset.done === '1';
  const toDone = evt.related && evt.related.dataset.done === '1';
  if (fromDone === activeDone && (toDone === activeDone || !evt.related)) return true;
  return false;
}

function createSortable() {
  const listEl = $('#item-list');
  if (!listEl || typeof Sortable === 'undefined') return null;
  if (sortable) sortable.destroy();
  sortable = Sortable.create(listEl, {
    animation: 180,                    // smooth reactive reflow while dragging
    delay: state.rearrange.active ? 0 : HOLD_MS,  // already in mode → drags start instantly
    delayOnTouchOnly: false,
    touchStartThreshold: 6,            // px of movement before the hold is cancelled
    filter: '.checkbox, .row-actions, .move-arrows, button, a, input, select, textarea',
    preventOnFilter: false,
    ghostClass: 'sortable-ghost',
    chosenClass: 'sortable-chosen',
    dragClass: 'sortable-drag',
    draggable: '.item-row',
    fallbackOnBody: true,
    forceFallback: true,       // pointer-based drag: smooth, custom-styled, no native HTML5 ghost
    onMove: sortableOnMove,
    onEnd: onSortableEnd
  });
  return sortable;
}

function destroySortable() {
  if (sortable) {
    sortable.destroy();
    sortable = null;
  }
}

/* One drop happened: the DOM order is already correct (Sortable moved the
   row). Persist with a single move_to PATCH, then silent-refresh. */
async function onSortableEnd(evt) {
  const row = evt.item;
  if (!row || evt.oldIndex === evt.newIndex) return;
  const dragId = Number(row.dataset.id);
  if (!dragId) return;
  /* group-relative ordinal: count ACTIVE-group rows above the dropped row */
  const activeDone = document.body.classList.contains('rearrange-done');
  let k = 0;
  let cur = row.previousElementSibling;
  while (cur) {
    if (cur.classList && cur.classList.contains('item-row') &&
      (cur.dataset.done === '1') === activeDone) k++;
    cur = cur.previousElementSibling;
  }
  if (rearrangeSaving) return;
  rearrangeSaving = true;
  try {
    await apiFetch(mutationPathFor(dragId), { method: 'PATCH', body: { move_to: k } });
  } catch (err) {
    /* silent — the refresh below resyncs with the server */
  } finally {
    rearrangeSaving = false;
  }
  await silentRefresh();
}

/* Long-press to ENTER rearrange mode (SortableJS does the rest of the
   dragging; we just flip the mode on after the hold). Uses pointerdown +
   timer so a plain tap still opens the edit modal. */
document.addEventListener('pointerdown', function (e) {
  /* every gesture starts clean — a stale suppression flag must never swallow
     an unrelated later click */
  if (state.rearrange.suppressClick) state.rearrange.suppressClick = false;
  if (e.button !== undefined && e.button !== 0) return;
  if (modalOpen()) return;
  const row = e.target.closest ? e.target.closest('.item-row') : null;
  if (!row) {
    /* pointerdown outside every item row exits rearrange mode — BUT never
       when the press is on a BUTTON or the action bar: the Done button's
       tap begins here, and swapping the Share button in mid-tap (between
       this pointerdown and the click) makes the click land on Share. */
    if (state.rearrange.active && !(e.target.closest && e.target.closest('.rearrange-toolbar')) &&
      !(e.target.closest && e.target.closest('button, a, [data-action]'))) {
      exitRearrange();
    }
    return;
  }
  if (state.rearrange.active) return;   // SortableJS handles drags from here
  if (!rearrangePermitted()) return;
  if (e.target.closest('.checkbox, .row-actions, button, a, input, select, textarea')) return;
  const id = Number(row.dataset.id);
  if (!id || !findItem(id)) return;
  /* arm the hold: if the pointer stays ~HOLD_MS on a row without moving,
     enter rearrange mode. SortableJS's own delay then takes over for the
     drag itself. */
  const startX = e.clientX;
  const startY = e.clientY;
  let moved = false;
  const onMove = function (me) {
    if (Math.abs(me.clientX - startX) + Math.abs(me.clientY - startY) > 8) moved = true;
  };
  const onUp = function () {
    window.removeEventListener('pointermove', onMove);
    window.removeEventListener('pointerup', onUp);
    window.removeEventListener('pointercancel', onUp);
  };
  window.addEventListener('pointermove', onMove);
  window.addEventListener('pointerup', onUp);
  window.addEventListener('pointercancel', onUp);
  setTimeout(function () {
    if (moved) return;
    if (state.rearrange.active) return;
    if (state.q) {
      /* reordering through a search filter would skip hidden rows — refuse */
      state.rearrange.suppressClick = true;
      toastInfo('Clear the search to reorder tasks.');
      return;
    }
    enterRearrange(id);
    createSortable();
  }, HOLD_MS);
});

/* A long-press that entered rearrange mode must not ALSO open the edit
   modal on release — suppressClick swallows exactly that one click.
   (Capture phase: wins before the row's own click-to-edit handler.) */
document.addEventListener('click', function (e) {
  if (state.rearrange.suppressClick) {
    state.rearrange.suppressClick = false;
    e.stopPropagation();
    e.preventDefault();
  }
}, true);

/* ---------------- auto-refresh polling (DESIGN-polling §2) ---------------- */
const POLL_INTERVAL_MS = 5000;

async function pollTick() {
  // guards: no overlap, only when visible, never while a modal is open
  if (state.poll.running) return;
  if (!state.poll.visible) return;
  if (modalOpen()) return;
  if (state.rearrange.active) return; // no re-render mid-rearrange/drag (DESIGN-reorder §2.7)
  state.poll.running = true;
  try {
    if (state.mode === 'share') {
      await loadShare(state.shareToken, { silent: true });
    } else {
      await fetchAppData(false);
      renderAll();
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

/* ---------------- item mutations ---------------- */
function mutationPathFor(itemId) {
  if (state.mode === 'share') return '/api/shared/' + state.shareToken + '/items/' + itemId;
  return '/api/items/' + itemId;
}

/* Reconcile a {item, spawned} PATCH envelope (gotcha 10). Returns spawned (or null). */
function applyItemEnvelope(env) {
  if (!env || !env.item) return null;
  const list = state.mode === 'share' ? (state.share ? state.share.items : null) : state.items;
  if (!list) return null;
  const idx = list.findIndex(function (x) { return x.id === env.item.id; });
  if (idx !== -1) list[idx] = env.item;
  if (env.spawned) {
    list.push(env.spawned);
    list.sort(cmpItems);
  }
  return env.spawned || null;
}

function spawnedToast(spawned) {
  if (spawned && spawned.due_date) {
    toastInfo('Repeats ' + fmtDateShort(spawned.due_date), { duration: 3600 });
  }
}

async function handleToggle(id, inputEl) {
  const item = findItem(id);
  if (!item || !inputEl || inputEl.disabled) return;
  if (state.mode === 'share' && state.share.permission !== 'edit') return;
  const nextDone = !item.done;
  /* optimistic flip */
  item.done = nextDone;
  const row = inputEl.closest('.item-row');
  if (row) row.classList.toggle('done', nextDone);
  inputEl.disabled = true;
  try {
    const env = await apiFetch(mutationPathFor(id), {
      method: 'PATCH',
      body: { done: nextDone }
    });
    const spawned = applyItemEnvelope(env);
    if (spawned) spawnedToast(spawned);
    if (state.mode === 'share') await refreshShare(true);
    else await refreshApp();
  } catch (err) {
    /* revert */
    item.done = !nextDone;
    if (row) row.classList.toggle('done', item.done);
    toastError(err.message);
    inputEl.disabled = false;
    if (row) { const cb = row.querySelector('.checkbox-input'); if (cb) cb.disabled = false; }
  }
}

async function deleteItem(id) {
  try {
    await apiFetch(mutationPathFor(id), { method: 'DELETE' });
    closeModal();
    if (state.mode === 'share') await refreshShare(true);
    else await refreshApp();
  } catch (err) {
    toastError(err.message);
  }
}

/* ============================================================
 * Modals (§4.5)
 * ============================================================ */
const FOCUSABLE = 'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [href], [tabindex]:not([tabindex="-1"])';

function modalOpen() { return !!state.modal; }

function openModal(kind, innerHtml, opts) {
  exitRearrange();   /* modals never open over rearrange mode */
  opts = opts || {};
  const root = $('#modal-root');
  state.modal = {
    kind: kind,
    opener: document.activeElement,
    refocusSel: opts.refocusSel || null,
    data: opts.data || null
  };
  root.innerHTML =
    '<div class="modal-backdrop" data-action="close-modal-backdrop"></div>' +
    '<div class="modal' + (opts.width === 'narrow' ? ' narrow' : opts.width === 'wide' ? ' wide' : '') +
    '" role="dialog" aria-modal="true"' + (opts.ariaLabel ? ' aria-label="' + esc(opts.ariaLabel) + '"' : '') + '>' +
    innerHtml +
    '</div>';
  const focusSel = opts.focusSel || 'input:not([type="hidden"]):not([disabled]), textarea, select, button';
  const first = $('.modal ' + focusSel, root) || $('.modal [data-autofocus]', root) || $('.modal button', root);
  if (first) setTimeout(function () { first.focus(); }, 10);
}

function closeModal() {
  const root = $('#modal-root');
  const m = state.modal;
  state.modal = null;
  root.innerHTML = '';
  if (m) {
    let target = null;
    if (m.refocusSel) target = $(m.refocusSel);
    if (!target && m.opener && m.opener.isConnected) target = m.opener;
    if (target && target.focus) target.focus();
  }
}

function trapTab(e) {
  if (e.key !== 'Tab' || !modalOpen()) return;
  const modalEl = $('.modal', $('#modal-root'));
  if (!modalEl) return;
  const focusables = $$(FOCUSABLE, modalEl).filter(function (el) {
    return el.offsetParent !== null || el === document.activeElement;
  });
  if (!focusables.length) return;
  const first = focusables[0];
  const last = focusables[focusables.length - 1];
  if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
}

function modalShell(title, opts) {
  opts = opts || {};
  return '<div class="modal-head">' +
    '<div><h2 class="modal-title">' + esc(title) + '</h2>' +
    (opts.sub ? '<p class="modal-sub">' + opts.sub + '</p>' : '') +
    '</div>' +
    '<button type="button" class="icon-btn small modal-close" data-action="close-modal" aria-label="Close dialog" title="Close">' + icon('x') + '</button>' +
    '</div>';
}

/* --- item edit modal --- */
function fieldWrap(id, label, controlHtml) {
  return '<div class="field">' +
    '<label class="field-label" for="' + id + '">' + esc(label) + '</label>' +
    controlHtml +
    '</div>';
}

function selectControl(id, options, current) {
  return '<select id="' + id + '" class="field-input field-select">' +
    options.map(function (o) {
      return '<option value="' + o[0] + '"' + (String(o[0]) === String(current) ? ' selected' : '') + '>' + esc(o[1]) + '</option>';
    }).join('') +
    '</select>';
}

function openEditModal(itemId) {
  const item = findItem(itemId);
  if (!item) return;
  const rowSel = '.item-row[data-id="' + itemId + '"]';
  const done = !!item.done;
  const body =
    '<form id="modal-form">' +
    modalShell('Edit task', {}) +
    '<div class="modal-body">' +
    fieldWrap('edit-title', 'Title',
      '<input type="text" id="edit-title" class="field-input" maxlength="200" autocomplete="off" value="' + esc(item.title) + '">') +
    fieldWrap('edit-notes', 'Notes',
      '<textarea id="edit-notes" class="field-input" maxlength="5000">' + esc(item.notes || '') + '</textarea>') +
    '<div class="field-row">' +
    fieldWrap('edit-due', 'Due date',
      '<input type="date" id="edit-due" class="field-input" value="' + esc(item.due_date || '') + '">') +
    fieldWrap('edit-priority', 'Priority',
      selectControl('edit-priority',
        [['none', 'None'], ['low', 'Low'], ['medium', 'Medium'], ['high', 'High']], item.priority)) +
    fieldWrap('edit-quantity', 'Quantity',
      '<input type="number" id="edit-quantity" class="field-input" min="0.1" step="0.1" inputmode="decimal" value="' + esc(fmtQty(item.quantity == null ? 1 : item.quantity)) + '">') +
    fieldWrap('edit-recurrence', 'Repeats',
      selectControl('edit-recurrence',
        [['none', 'None'], ['daily', 'Daily'], ['weekly', 'Weekly'], ['monthly', 'Monthly'], ['custom', 'Custom…']], item.recurrence)) +
    '</div>' +
    '<div id="edit-interval-field" class="field" ' + (item.recurrence === 'custom' ? '' : 'style="display:none"') + '>' +
    '<label class="field-label" for="edit-interval">Every N days</label>' +
    '<input type="number" id="edit-interval" class="field-input" min="1" step="1" inputmode="numeric" value="' + esc(item.recurrence_interval || 1) + '">' +
    '</div>' +
    '<label class="done-row">' +
    '<span class="checkbox">' +
    '<input type="checkbox" id="edit-done" class="checkbox-input"' + (done ? ' checked' : '') + '>' +
    icon('check', 'check-svg') +
    '</span>' +
    '<span class="done-row-label">Completed</span>' +
    '</label>' +
    '</div>' +
    '<div class="modal-foot spread">' +
    '<button type="button" class="btn btn-danger-ghost" data-action="edit-delete" data-id="' + item.id + '">' + icon('trash') + '<span>Delete</span></button>' +
    '<span class="foot-right">' +
    '<button type="button" class="btn btn-ghost" data-action="close-modal">Cancel</button>' +
    '<button type="submit" class="btn btn-accent">Save</button>' +
    '</span>' +
    '</div>' +
    '</form>';
  openModal('edit', body, { ariaLabel: 'Edit task', refocusSel: rowSel, data: { itemId: itemId } });
  /* custom-interval reveal */
  $('#edit-recurrence').addEventListener('change', function () {
    $('#edit-interval-field').style.display = this.value === 'custom' ? '' : 'none';
  });
}

function collectEditPayload(itemId) {
  const item = findItem(itemId);
  if (!item) return null;
  const title = $('#edit-title').value.trim();
  if (!title) { toastError('Give the task a title.'); return null; }
  const notes = $('#edit-notes').value.trim();
  const priority = $('#edit-priority').value;
  const due = $('#edit-due').value ? $('#edit-due').value : null;
  const recurrence = $('#edit-recurrence').value;
  let interval = null;
  if (recurrence === 'custom') {
    interval = parseInt($('#edit-interval').value, 10);
    if (!(interval >= 1)) { toastError('Enter how many days between repetitions.'); return null; }
  }
  let quantity = parseFloat($('#edit-quantity').value);
  if (!(quantity > 0) || Number.isNaN(quantity)) quantity = item.quantity == null ? 1 : item.quantity;
  const done = $('#edit-done').checked;
  const p = {};
  if (title !== item.title) p.title = title;
  const oldNotes = item.notes || '';
  if (notes !== oldNotes) p.notes = notes ? notes : null;
  if (priority !== item.priority) p.priority = priority;
  if (due !== (item.due_date || null)) p.due_date = due;
  if (recurrence !== item.recurrence) p.recurrence = recurrence;
  if (recurrence === 'custom' && interval !== item.recurrence_interval) p.recurrence_interval = interval;
  if (quantity !== item.quantity) p.quantity = quantity;
  if (done !== item.done) p.done = done;
  return p;
}

async function saveEditModal() {
  const itemId = state.modal && state.modal.data ? state.modal.data.itemId : null;
  if (itemId == null) return;
  const payload = collectEditPayload(itemId);
  if (!payload) return;
  if (Object.keys(payload).length === 0) { closeModal(); return; }
  try {
    const env = await apiFetch(mutationPathFor(itemId), { method: 'PATCH', body: payload });
    const spawned = applyItemEnvelope(env);
    closeModal();
    if (spawned) spawnedToast(spawned);
    if (state.mode === 'share') await refreshShare(true);
    else await refreshApp();
  } catch (err) {
    toastError(err.message);
  }
}

/* --- delete-confirm modal --- */
function openDeleteItemConfirm(itemId) {
  const item = findItem(itemId);
  if (!item) return;
  state.pendingDelete = { kind: 'item', id: itemId };
  const body =
    '<div class="modal-body">' +
    '<div class="state-icon tinted-danger" style="margin-bottom:var(--space-2)">' + icon('trash') + '</div>' +
    '<h2 class="modal-title">Delete "' + esc(item.title) + '"?</h2>' +
    '<p class="modal-sub">This task will be permanently deleted.</p>' +
    '</div>' +
    '<div class="modal-foot">' +
    '<button type="button" class="btn btn-ghost" data-action="close-modal">Cancel</button>' +
    '<button type="button" class="btn btn-danger" data-action="confirm-delete">Delete</button>' +
    '</div>';
  openModal('confirm', body, { ariaLabel: 'Delete task', width: 'narrow', refocusSel: '.item-row[data-id="' + itemId + '"]' });
}

function openDeleteListConfirm(listId) {
  const list = state.lists.find(function (l) { return l.id === listId; });
  if (!list) return;
  state.pendingDelete = { kind: 'list', id: listId };
  const sub = list.item_count > 0
    ? 'Delete ' + list.item_count + (list.item_count === 1 ? ' item' : ' items') + ' in this list? Items and share links will also be deleted.'
    : 'Items and share links will also be deleted.';
  const body =
    '<div class="modal-body">' +
    '<div class="state-icon tinted-danger" style="margin-bottom:var(--space-2)">' + icon('trash') + '</div>' +
    '<h2 class="modal-title">Delete "' + esc(list.name) + '"?</h2>' +
    '<p class="modal-sub">' + esc(sub) + '</p>' +
    '</div>' +
    '<div class="modal-foot">' +
    '<button type="button" class="btn btn-ghost" data-action="close-modal">Cancel</button>' +
    '<button type="button" class="btn btn-danger" data-action="confirm-delete">Delete list</button>' +
    '</div>';
  openModal('confirm', body, { ariaLabel: 'Delete list', width: 'narrow' });
}

function openRevokeShareConfirm(token, permission) {
  state.pendingDelete = { kind: 'share', token: token, permission: permission };
  const body =
    '<div class="modal-body">' +
    '<div class="state-icon tinted-danger" style="margin-bottom:var(--space-2)">' + icon('trash') + '</div>' +
    '<h2 class="modal-title">Revoke this link?</h2>' +
    '<p class="modal-sub">Anyone with this link will lose access to the list.</p>' +
    '</div>' +
    '<div class="modal-foot">' +
    '<button type="button" class="btn btn-ghost" data-action="close-modal">Cancel</button>' +
    '<button type="button" class="btn btn-danger" data-action="confirm-delete">Revoke link</button>' +
    '</div>';
  openModal('confirm', body, { ariaLabel: 'Revoke share link', width: 'narrow' });
}

async function runPendingDelete() {
  const pd = state.pendingDelete;
  if (!pd) return;
  try {
    if (pd.kind === 'item') {
      await deleteItem(pd.id);
    } else if (pd.kind === 'list') {
      await apiFetch('/api/lists/' + pd.id, { method: 'DELETE' });
      closeModal();
      const wasCurrent = state.view.type === 'list' && state.view.listId === pd.id;
      if (wasCurrent) state.viewKey = null;
      await refreshApp();
      if (wasCurrent) { setViewParams('all', null); state.viewKey = 'all'; persistView(); enterView('all', null); }
    } else if (pd.kind === 'share') {
      await apiFetch('/api/shares/' + pd.token, { method: 'DELETE' });
      state.shareLinks = state.shareLinks.filter(function (s) { return s.token !== pd.token; });
      const wasShareDialog = state.modal && state.modal.kind === 'share';
      closeModal();
      toastSuccess('Link revoked');
      if (wasShareDialog) openShareModal(currentShareListId());
    }
  } catch (err) {
    toastError(err.message);
  }
  state.pendingDelete = null;
}

/* --- list name modal (create / rename) --- */
function openNameModal(mode, list) {
  const isCreate = mode === 'create';
  const title = isCreate ? 'New list' : 'Rename list';
  const body =
    '<form id="modal-form">' +
    modalShell(title, {}) +
    '<div class="modal-body">' +
    fieldWrap('name-input', 'Name',
      '<input type="text" id="name-input" class="field-input" maxlength="200" autocomplete="off" value="' + (list ? esc(list.name) : '') + '" placeholder="Groceries">') +
    '</div>' +
    '<div class="modal-foot">' +
    '<button type="button" class="btn btn-ghost" data-action="close-modal">Cancel</button>' +
    '<button type="submit" class="btn btn-accent">' + (isCreate ? 'Create list' : 'Save') + '</button>' +
    '</div>' +
    '</form>';
  openModal(isCreate ? 'list-create' : 'list-rename', body, {
    ariaLabel: title,
    data: list ? { listId: list.id } : null,
    refocusSel: list ? '.nav-row[data-id="' + list.id + '"]' : null
  });
  const input = $('#name-input');
  input.focus();
  input.select();
}

async function submitNameModal() {
  const val = $('#name-input').value.trim();
  if (!val) { toastError('Give the list a name.'); return; }
  const kind = state.modal ? state.modal.kind : null;
  const listId = state.modal && state.modal.data ? state.modal.data.listId : null;
  try {
    if (kind === 'list-create') {
      await apiFetch('/api/lists', { method: 'POST', body: { name: val } });
      closeModal();
      await refreshApp();
    } else if (kind === 'list-rename' && listId != null) {
      await apiFetch('/api/lists/' + listId, { method: 'PATCH', body: { name: val } });
      closeModal();
      await refreshApp();
    }
  } catch (err) {
    toastError(err.message);
  }
}

/* --- share dialog --- */
function currentShareListId() {
  const listId = state.modal && state.modal.data ? state.modal.data.listId : null;
  return listId;
}

function openShareModal(listId) {
  const list = state.lists.find(function (l) { return l.id === listId; });
  if (!list) return;
  const linksHtml = state.shareLinks.length
    ? state.shareLinks.map(function (s) {
      return shareLinkRowHtml(s);
    }).join('')
    : '<div class="share-empty">No active links</div>';
  const body =
    modalShell('Share "' + list.name + '"', {}) +
    '<div class="modal-body">' +
    '<div class="share-section">' +
    '<div class="share-section-title">Invite by link</div>' +
    '<div class="share-create-row">' +
    selectControl('share-permission', [['read', 'Can view'], ['edit', 'Can edit']], 'edit') +
    '<button type="button" class="btn btn-accent" data-action="share-create">Create link</button>' +
    '</div>' +
    '<div id="share-created"></div>' +
    '</div>' +
    '<div class="share-section">' +
    '<div class="share-section-title">Active links</div>' +
    '<div class="share-links" id="share-links">' + linksHtml + '</div>' +
    '</div>' +
    '</div>' +
    '<div class="modal-foot">' +
    '<button type="button" class="btn btn-ghost" data-action="close-modal">Done</button>' +
    '</div>';
  openModal('share', body, {
    ariaLabel: 'Share list',
    width: 'wide',
    data: { listId: listId },
    refocusSel: null
  });
}

function shareLinkRowHtml(s) {
  const permLabel = s.permission === 'edit' ? 'Can edit' : 'Can view';
  return '<div class="share-link-row" data-token="' + esc(s.token) + '">' +
    '<span class="share-link-perm ' + (s.permission === 'read' ? 'read' : '') + '">' + permLabel + '</span>' +
    '<span class="share-link-token" title="' + esc(s.url) + '"><span>' + esc(s.token) + '</span></span>' +
    '<button type="button" class="icon-btn small" data-action="share-copy" data-url="' + esc(s.url) + '" aria-label="Copy link" title="Copy link">' + icon('copy') + '</button>' +
    '<button type="button" class="icon-btn small" data-action="share-revoke" data-token="' + esc(s.token) + '" data-permission="' + s.permission + '" aria-label="Revoke link" title="Revoke link">' + icon('trash') + '</button>' +
    '</div>';
}

async function createShareLink() {
  const listId = currentShareListId();
  if (listId == null) return;
  const permission = $('#share-permission').value;
  const btn = $('[data-action="share-create"]');
  if (btn) btn.disabled = true;
  try {
    const data = await apiFetch('/api/lists/' + listId + '/shares', {
      method: 'POST',
      body: { permission: permission }
    });
    const entry = { token: data.token, permission: data.permission, url: data.url };
    state.shareLinks = state.shareLinks.filter(function (s) { return s.token !== entry.token; });
    state.shareLinks.unshift(entry);
    /* show URL + copy, update the active links list */
    const created = $('#share-created');
    if (created) {
      created.innerHTML =
        '<div class="share-url-row">' +
        '<div class="share-url" id="share-url" title="' + esc(data.url) + '">' + esc(data.url) + '</div>' +
        '<button type="button" class="btn btn-ghost" data-action="share-copy" data-url="' + esc(data.url) + '">' + icon('copy') + '<span>Copy</span></button>' +
        '</div>';
    }
    const links = $('#share-links');
    if (links) links.innerHTML = state.shareLinks.map(shareLinkRowHtml).join('');
  } catch (err) {
    toastError(err.message);
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function copyText(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
    } else {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      ta.remove();
    }
    return true;
  } catch (e) {
    return false;
  }
}

async function handleCopy(url) {
  const ok = await copyText(url);
  toastSuccess(ok ? 'Copied' : 'Copy failed — select the link manually');
}

/* ============================================================
 * Event delegation
 * ============================================================ */
function handleAction(action, el) {
  switch (action) {
    case 'toggle-sidebar':
      toggleSidebar();
      break;
    case 'toggle-theme':
      toggleTheme();
      break;
    case 'open-search-mobile':
      openMobileSearch();
      break;
    case 'close-search-mobile':
      closeMobileSearch();
      break;
    case 'clear-search':
      setSearch('');
      break;
    case 'install-app':
      promptInstall();
      break;
    case 'select-view': {
      const view = el.dataset.view;
      const id = el.dataset.id ? Number(el.dataset.id) : null;
      if (view === 'all') enterView('all', null);
      else if (view === 'list' && id != null) enterView('list', id);
      leaveRearrangeIfActive();
      closeSidebar();
      break;
    }
    case 'new-list':
      openNameModal('create', null);
      break;
    case 'rename-list':
      openNameModal('rename', state.lists.find(function (l) { return l.id === Number(el.dataset.id); }));
      break;
    case 'delete-list':
      openDeleteListConfirm(Number(el.dataset.id));
      break;
    case 'open-share':
      openShareModal(Number(el.dataset.id));
      break;
    case 'set-status':
      setStatus(el.dataset.status);
      break;
    case 'toggle-composer-options': {
      const open = $('#composer-options').hidden;
      setComposerOptionsVisible(open);
      break;
    }
    case 'focus-composer':
      $('#composer-title').focus();
      break;
    case 'clear-filters':
      clearFilters();
      break;
    case 'edit-item':
      openEditModal(Number(el.dataset.id));
      break;
    case 'delete-item':
      openDeleteItemConfirm(Number(el.dataset.id));
      break;
    case 'edit-delete':
      openDeleteItemConfirm(Number(el.dataset.id));
      break;
    case 'move-up':
      apiMoveItem(Number(el.dataset.id), 'up');
      break;
    case 'move-down':
      apiMoveItem(Number(el.dataset.id), 'down');
      break;
    case 'exit-rearrange':
      exitRearrange();
      break;
    case 'confirm-delete':
      runPendingDelete();
      break;
    case 'close-modal':
      closeModal();
      break;
    case 'close-modal-backdrop':
      closeModal();
      break;
    case 'share-create':
      createShareLink();
      break;
    case 'share-copy':
      handleCopy(el.dataset.url);
      break;
    case 'share-revoke':
      openRevokeShareConfirm(el.dataset.token, el.dataset.permission);
      break;
    default:
      break;
  }
}

document.addEventListener('click', function (e) {
  /* (suppressClick for long-press releases is handled by the CAPTURE-phase
     click listener above — it must never reach here.) */
  const actionEl = e.target.closest('[data-action]');
  if (actionEl) {
    handleAction(actionEl.dataset.action, actionEl);
    return;
  }
  /* row click (not checkbox/actions) opens the edit modal */
  const row = e.target.closest('.item-row');
  if (!row) return;
  if (e.target.closest('.checkbox, .row-actions, button, a, input, select, textarea')) return;
  if (state.mode === 'share' && state.share && state.share.permission !== 'edit') return; /* read-only: row click does nothing */
  if (state.rearrange.active) return; /* rearrange mode: taps on rows never open edit */
  if (!row.dataset.id) return;
  openEditModal(Number(row.dataset.id));
});

/* checkbox toggles fire on change (label clicks included) */
document.addEventListener('change', function (e) {
  const input = e.target;
  if (input && input.classList && input.classList.contains('checkbox-input')) {
    /* rearrange mode: ignore checkbox toggles entirely (they must not race a
       drag-persist — DESIGN-fix-reorder §2.3). Revert the visual flip; the
       next render restores the row. */
    if (state.rearrange.active) {
      input.checked = !input.checked;
      return;
    }
    handleToggle(Number(input.dataset.id), input);
  }
});

/* composer options: recurrence -> interval reveal */
document.addEventListener('change', function (e) {
  if (e.target.id === 'composer-recurrence') {
    $('#composer-interval-field').hidden = e.target.value !== 'custom';
  }
});

/* keyboard (§4.6) */
document.addEventListener('keydown', function (e) {
  if (modalOpen()) {
    if (e.key === 'Escape') { e.preventDefault(); closeModal(); return; }
    if (e.key === 'Delete' && state.modal.kind === 'edit' && !isEditable(e.target) &&
      state.modal.data && state.modal.data.itemId != null) {
      e.preventDefault();
      openDeleteItemConfirm(state.modal.data.itemId);
      return;
    }
    trapTab(e);
    return;
  }
  if (e.key === 'Escape') {
    if (state.rearrange.active) { exitRearrange(); return; }
    if (document.body.classList.contains('sidebar-open')) { closeSidebar(); return; }
    if (document.body.classList.contains('search-open')) { closeMobileSearch(); return; }
    return;
  }
  if (e.key === '/' && !isEditable(e.target) && !e.metaKey && !e.ctrlKey && !e.altKey &&
    window.matchMedia('(min-width: 720px)').matches) {
    e.preventDefault();
    focusSearch();
  }
  /* nav rows are <div role=button> — activate on Enter/Space like a native button */
  if ((e.key === 'Enter' || e.key === ' ') && e.target && e.target.classList &&
    e.target.classList.contains('nav-row') && !isEditable(e.target)) {
    e.preventDefault();
    e.target.click();
  }
});

/* ============================================================
 * Sidebar drawer / mobile search / segmented / search
 * ============================================================ */
function toggleSidebar() {
  if (document.body.classList.contains('sidebar-open')) closeSidebar();
  else openSidebar();
}

function openSidebar() {
  document.body.classList.add('sidebar-open');
  const btn = $('#btn-sidebar');
  if (btn) { btn.setAttribute('aria-expanded', 'true'); btn.setAttribute('aria-label', 'Close sidebar'); }
  state.sidebarOpener = document.activeElement;
}

function closeSidebar() {
  document.body.classList.remove('sidebar-open');
  const btn = $('#btn-sidebar');
  if (btn) { btn.setAttribute('aria-expanded', 'false'); btn.setAttribute('aria-label', 'Open sidebar'); }
  if (state.sidebarOpener && state.sidebarOpener.isConnected) state.sidebarOpener.focus();
}

function openMobileSearch() {
  document.body.classList.add('search-open');
  const backdrop = $('#search-backdrop');
  if (backdrop) backdrop.hidden = false;
  state.searchOpener = document.activeElement;
  setTimeout(function () { $('#search-input').focus(); }, 30);
}

function closeMobileSearch() {
  document.body.classList.remove('search-open');
  const backdrop = $('#search-backdrop');
  if (backdrop) backdrop.hidden = true;
  if (state.searchOpener && state.searchOpener.isConnected && !document.body.classList.contains('search-open')) {
    state.searchOpener.focus();
  }
}

function focusSearch() {
  const input = $('#search-input');
  if (input) input.focus();
}

function syncSearchClear() {
  const input = $('#search-input');
  const btn = $('#search-clear');
  if (btn) btn.hidden = !(input && input.value.length > 0);
}

function syncSegmented() {
  $$('#segmented .segment').forEach(function (b) {
    b.setAttribute('aria-pressed', String(b.dataset.status === state.status));
  });
}

function setStatus(status) {
  if (state.status === status) return;
  exitRearrange();
  state.status = status;
  syncSegmented();
  if (state.mode === 'share') renderShareItems();
  else refreshApp().catch(function (err) { toastError(err.message); });
}

function setSearch(value) {
  exitRearrange();
  state.q = value.trim();
  syncSearchClear();
  if (state.mode === 'share') renderShareItems();
  else refreshApp().catch(function (err) { toastError(err.message); });
}

let searchTimer = null;
function onSearchInput() {
  const input = $('#search-input');
  syncSearchClear();
  clearTimeout(searchTimer);
  searchTimer = setTimeout(function () {
    setSearch(input.value);
  }, 150);
}

/* ---------------- install prompt (§4.5/§5.2) ---------------- */
function promptInstall() {
  if (!state.deferredPrompt) return;
  state.deferredPrompt.prompt();
  state.deferredPrompt.userChoice.then(function () {
    state.deferredPrompt = null;
    hideInstall();
  });
}

function showInstall() {
  const btn = $('#btn-install');
  if (btn && !state.installed) btn.hidden = false;
}

function hideInstall() {
  const btn = $('#btn-install');
  if (btn) btn.hidden = true;
}

/* ---------------- service worker (§5.2) ---------------- */
function registerSW() {
  if (!('serviceWorker' in navigator)) return;
  window.addEventListener('load', function () {
    navigator.serviceWorker.register('/sw.js').then(function (reg) {
      console.log('[taskflow] SW registered');
      reg.addEventListener('updatefound', function () {
        const nw = reg.installing;
        if (!nw) return;
        nw.addEventListener('statechange', function () {
          if (nw.state === 'installed' && navigator.serviceWorker.controller) {
            toast('Update available — tap to reload', 'info', {
              duration: 8000,
              onClick: function () {
                if (reg.waiting) reg.waiting.postMessage({ type: 'SKIP_WAITING' });
                location.reload();
              }
            });
          }
        });
      });
    }).catch(function (err) {
      console.warn('[taskflow] SW registration failed', err);
    });
  });
}

/* ============================================================
 * Boot
 * ============================================================ */
function bindStaticEvents() {
  const composerForm = $('#composer-form');
  composerForm.addEventListener('submit', function (e) {
    e.preventDefault();
    submitComposer();
  });
  const modalRoot = $('#modal-root');
  modalRoot.addEventListener('submit', function (e) {
    if (e.target.id !== 'modal-form') return;
    e.preventDefault();
    const kind = state.modal ? state.modal.kind : null;
    if (kind === 'edit') saveEditModal();
    else if (kind === 'list-create' || kind === 'list-rename') submitNameModal();
  });
  const searchInput = $('#search-input');
  searchInput.addEventListener('input', onSearchInput);
  searchInput.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && !e.target.value) closeMobileSearch();
  });

  window.addEventListener('beforeinstallprompt', function (e) {
    e.preventDefault();
    state.deferredPrompt = e;
    showInstall();
  });
  window.addEventListener('appinstalled', function () {
    state.installed = true;
    hideInstall();
  });
}

function init() {
  applyTheme(currentTheme());
  bindStaticEvents();
  const route = parseRoute();
  if (route.mode === 'share') {
    loadShare(route.token);
  } else {
    loadApp();
  }
  registerSW();
  startPolling();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
