/* Taskflow service worker — DESIGN.md §5.2
 *
 * Cache versioning: bump SHELL_CACHE whenever static assets change materially
 * (and remember: during development use DevTools "Update on reload", and
 * unregister old service workers when testing fresh changes — stale workers
 * haunt offline testing otherwise).
 */
const SHELL_CACHE = 'taskflow-shell-v14';
const API_CACHE = 'taskflow-api-v1';

/* Every entry MUST exist on the server or install fails (addAll rejects). */
const PRECACHE = [
  '/',
  '/index.html',
  '/style.css',
  '/app.js',
  '/vendor/sortable.min.js',
  '/manifest.webmanifest',
  '/icons/icon-32.png',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
  '/icons/icon-maskable-512.png',
  '/icons/apple-touch-icon.png'
];

const API_CACHE_MAX = 40;

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE).then((cache) => cache.addAll(PRECACHE))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((key) => key !== SHELL_CACHE && key !== API_CACHE)
          .map((key) => caches.delete(key))
      )
    )
  );
  self.clients.claim();
});

async function trimApiCache(cache) {
  const keys = await cache.keys();
  if (keys.length > API_CACHE_MAX) {
    await cache.delete(keys[0]); // oldest first (insertion order)
  }
}

self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Same-origin only. Ignore cross-origin requests entirely.
  if (url.origin !== self.location.origin) return;

  // Non-GET (POST/PATCH/DELETE) → plain fetch passthrough, never cached.
  if (request.method !== 'GET') return;

  // Navigation requests → network-first, fall back to cached app shell.
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then((response) => {
          // Shell responses carry Cache-Control: no-cache; cache the fresh copy.
          const copy = response.clone();
          caches.open(SHELL_CACHE).then((cache) => cache.put(request, copy));
          return response;
        })
        .catch(() => caches.match('/index.html'))
    );
    return;
  }

  // GET /api/… → network-first with offline fallback (last-known data).
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(
      fetch(request)
        .then((response) => {
          if (response.ok) {
            const copy = response.clone();
            caches
              .open(API_CACHE)
              .then((cache) => cache.put(request, copy).then(() => trimApiCache(cache)));
          }
          return response;
        })
        .catch(() =>
          caches.match(request).then((hit) => {
            if (hit) return hit;
            // Response.error()-style failure — the UI shows its error toast.
            return Response.error();
          })
        )
    );
    return;
  }

  // GET to other same-origin static assets → cache-first (precached).
  event.respondWith(
    caches.match(request).then(
      (hit) =>
        hit ||
        fetch(request).then((response) => {
          const copy = response.clone();
          caches.open(SHELL_CACHE).then((cache) => cache.put(request, copy));
          return response;
        })
    )
  );
});

self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});
