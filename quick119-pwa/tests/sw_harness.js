// 실제 네트워크/저장소 대신 모의 객체로 SW 이벤트와 캐시 정책을 실행합니다.
(async () => {
  const handlers = {};
  const entries = new Map();
  const writes = [];
  const fetches = [];
  const deleted = [];
  let networkFails = false;
  let claimed = false;
  const cache = {
    put: async (key, value) => { writes.push(key); entries.set(key, value); },
    match: async key => entries.get(key)
  };
  const store = {
    open: async () => cache,
    keys: async () => ['other-app-cache', 'quick119-static-v0', 'quick119-static-v1'],
    delete: async key => { deleted.push(key); }
  };
  const worker = {
    location: { origin: 'https://quick119.test' },
    clients: { claim: async () => { claimed = true; } },
    addEventListener: (name, callback) => { handlers[name] = callback; }
  };
  const fakeFetch = async (request, options) => {
    const path = typeof request === 'string' ? request : new URL(request.url).pathname;
    fetches.push({ path, options });
    if (networkFails) throw new Error('offline');
    return new Response(path, { status: 200 });
  };
  let count = 0;
  const assert = (condition, label) => { if (!condition) throw new Error(label); count++; };
  const dispatch = (path, method = 'GET', mode = 'cors') => {
    let promise;
    handlers.fetch({ request: { url: new URL(path, worker.location.origin).href, method, mode }, respondWith: p => { promise = p; } });
    return promise;
  };
  try {
    new Function('self', 'caches', 'fetch', WORKER_SOURCE)(worker, store, fakeFetch);
    let pending;
    handlers.install({ waitUntil: p => { pending = p; } });
    await pending;
    assert(writes.length === 10 && writes.includes('/static/voice-intake.js') && writes.every(path => path.startsWith('/static/') && !path.includes('?')), 'precache allowlist');
    assert(fetches.every(call => call.options.credentials === 'omit'), 'precache omits credentials');
    const baseline = writes.length;
    for (const path of ['/patients', '/register', '/emergency/report?patient_id=1', '/emergency/hospitals?latitude=37&longitude=127', '/api/patients']) {
      if (dispatch(path) !== undefined) throw new Error('dynamic resource intercepted');
    }
    assert(writes.length === baseline, 'dynamic/API responses not cached');
    assert(dispatch('/patients', 'POST') === undefined && dispatch('/static/pwa.css?patient_id=1') === undefined
      && dispatch('https://external.invalid/static/pwa.css') === undefined, 'POST/query/external bypass');
    const fetched = fetches.length;
    await dispatch('/static/pwa.css');
    assert(fetches.length === fetched, 'static cache hit');
    const page = await dispatch('/patients?patient_id=1', 'GET', 'navigate');
    assert(await page.text() === '/patients' && writes.length === baseline && fetches.at(-1).options.cache === 'no-store', 'online navigation never stored');
    networkFails = true;
    const offline = await dispatch('/emergency/hospitals?latitude=37', 'GET', 'navigate');
    assert(await offline.text() === '/static/offline.html' && writes.length === baseline, 'offline returns safe page only');
    entries.delete('/static/offline.html');
    const noFallback = await dispatch('/patients', 'GET', 'navigate');
    assert(noFallback.status === 503 && noFallback.headers.get('Cache-Control') === 'no-store', 'missing fallback returns safe 503');
    networkFails = false;
    entries.delete('/static/pwa.css');
    await dispatch('/static/pwa.css');
    assert(writes.length === baseline, 'static miss not runtime cached');
    handlers.activate({ waitUntil: p => { pending = p; } });
    await pending;
    assert(deleted.length === 2 && deleted.includes('quick119-static-v0') && deleted.includes('quick119-static-v1') && claimed, 'owned old cache cleanup');
    document.getElementById('result').textContent = `PASS: ${count} worker checks`;
  } catch (error) {
    document.getElementById('result').textContent = 'FAIL: ' + error.message;
  }
})();
