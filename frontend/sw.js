// Service worker — NETWORK-FIRST for HTML/JS/CSS so users never get stuck
// on stale code after a deploy. Old cache-first strategy caused sign-in and
// gating logic to run mixed versions across files.
const CACHE_NAME = 'market-wisdom-v35';
const urlsToCache = [
  '/', '/index.html', '/style.css', '/app.js',
  '/components.js', '/api.js', '/auth.js', '/manifest.json'
];

// Install: pre-cache the shell for offline use; DO NOT skipWaiting so an open
// tab keeps its current SW until it's actually reloaded.
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(urlsToCache))
  );
});

// Activate: nuke every old cache and take control of open pages immediately.
self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names.map(n => n !== CACHE_NAME ? caches.delete(n) : null));
    await self.clients.claim();
  })());
});

// Allow the page to force an update by posting {type:'SKIP_WAITING'}.
self.addEventListener('message', event => {
  if (event.data && event.data.type === 'SKIP_WAITING') self.skipWaiting();
});

// Fetch strategy — NETWORK-FIRST for HTML/JS/CSS; fall back to cache offline.
self.addEventListener('fetch', event => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  // Don't cache API or third-party (Google GSI, fonts, etc.) — pass through.
  if (url.pathname.startsWith('/api/') || url.origin !== self.location.origin) return;

  event.respondWith((async () => {
    try {
      const fresh = await fetch(req, { cache: 'no-store' });
      if (fresh && fresh.status === 200 && fresh.type === 'basic') {
        const clone = fresh.clone();
        caches.open(CACHE_NAME).then(c => c.put(req, clone)).catch(() => {});
      }
      return fresh;
    } catch (e) {
      const cached = await caches.match(req);
      if (cached) return cached;
      if (req.mode === 'navigate') return caches.match('/index.html');
      throw e;
    }
  })());
});
