/* TecnoTools service worker.

   Strateji:
     - HTML (gezinme): network-first — taze içerik öncelikli, offline'da cache.
     - JS / CSS / font / resim: stale-while-revalidate — cache döner, arkada
       network ile tazelenir; bir sonraki ziyarette güncel olur.
     - API ve WebSocket: SW asla araya girmez (her zaman canlı).

   CACHE_NAME değişince activate'te eski cache otomatik silinir.
   Deploy sırasında dosyalar değiştiyse CACHE_NAME'i bump'la (vN → vN+1).
*/

const CACHE_NAME = 'tt-static-v8';
const PRECACHE_ASSETS = [
  '/',
  '/index.html',
  '/admin.html',
  '/js/api.js',
  '/manifest.webmanifest',
];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE_NAME).then((c) => c.addAll(PRECACHE_ASSETS)).catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)));
    await self.clients.claim();
  })());
});

/** Network-first: ağdan çek, başarısızsa cache. HTML için ideal. */
async function networkFirst(req) {
  try {
    const resp = await fetch(req);
    if (resp.ok && resp.type === 'basic') {
      const clone = resp.clone();
      caches.open(CACHE_NAME).then((c) => c.put(req, clone)).catch(() => {});
    }
    return resp;
  } catch (_) {
    const cached = await caches.match(req);
    if (cached) return cached;
    return caches.match('/index.html');
  }
}

/** Stale-while-revalidate: cache'i hemen döndür, arka planda tazele. */
async function staleWhileRevalidate(req) {
  const cached = await caches.match(req);
  const network = fetch(req).then((resp) => {
    if (resp.ok && resp.type === 'basic') {
      const clone = resp.clone();
      caches.open(CACHE_NAME).then((c) => c.put(req, clone)).catch(() => {});
    }
    return resp;
  }).catch(() => null);
  return cached || (await network) || new Response('', { status: 504 });
}

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  // API ve WebSocket — her zaman canlı
  if (url.pathname.startsWith('/api/') || url.protocol.startsWith('ws')) return;
  // HTML navigasyon (mode='navigate' veya .html uzantısı) → network-first
  const isHTML =
    req.mode === 'navigate' ||
    (req.headers.get('accept') || '').includes('text/html') ||
    url.pathname.endsWith('.html');
  if (isHTML) {
    e.respondWith(networkFirst(req));
    return;
  }
  // Statik asset → stale-while-revalidate
  e.respondWith(staleWhileRevalidate(req));
});

/** İstemciden 'SKIP_WAITING' mesajı gelirse anında yeni SW aktive olur. */
self.addEventListener('message', (e) => {
  if (e.data === 'SKIP_WAITING') self.skipWaiting();
});
