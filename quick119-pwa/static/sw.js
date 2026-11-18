/* QUICK119: 정적 허용 목록만 저장. 의료정보와 일반 HTML은 저장하지 않음. */
const CACHE_NAME = 'quick119-static-v3';
const OFFLINE_URL = '/static/offline.html';
const STATIC_ASSETS = [
  "/static/quick119.css",
  "/static/quick119.js",
  "/static/voice-intake.js",
  "/static/pwa.css",
  "/static/pwa.js",
  "/static/icons.svg",
  "/static/app-icons/icon-192.png",
  "/static/app-icons/icon-512.png",
  "/static/app-icons/maskable-512.png",
  "/static/offline.html"
];

self.addEventListener('install', event => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE_NAME);
    for (const path of STATIC_ASSETS) {
      const response = await fetch(path, { cache: 'reload', credentials: 'omit' });
      if (!response.ok || response.redirected || response.type === 'opaque') throw new Error('Static precache failed');
      await cache.put(path, response);
    }
    // 자동 skipWaiting/새로고침으로 진행 중인 문진을 중단하지 않습니다.
  })());
});

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names.filter(name => name.startsWith('quick119-static-') && name !== CACHE_NAME)
      .map(name => caches.delete(name)));
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', event => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== self.location.origin) return;
  if (request.mode === 'navigate') {
    // 위치/환자 ID 등이 있는 URL도 네트워크로만 요청하고 저장하지 않습니다.
    event.respondWith((async () => {
      try { return await fetch(request, { cache: 'no-store' }); }
      catch (_) {
        const cache = await caches.open(CACHE_NAME);
        return (await cache.match(OFFLINE_URL)) || new Response(
          'QUICK119: Offline. In an emergency call 119.',
          { status: 503, headers: { 'Content-Type': 'text/plain; charset=utf-8', 'Cache-Control': 'no-store' } }
        );
      }
    })());
    return;
  }
  // API, POST, 쿼리 문자열, 허용 목록 밖의 자원은 가로채거나 저장하지 않습니다.
  if (url.search || !STATIC_ASSETS.includes(url.pathname)) return;
  event.respondWith((async () => {
    const cache = await caches.open(CACHE_NAME);
    const cached = await cache.match(url.pathname);
    if (cached) return cached;
    return fetch(request); // 캐시 누락 시에도 런타임 응답을 저장하지 않습니다.
  })());
});
