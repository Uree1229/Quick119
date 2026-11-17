/* Runs the production script with real Leaflet, synthetic GPS/time and no route network. */
(async () => {
  const assert = (ok, message) => { if (!ok) throw new Error(message); };
  const checks = [];
  const check = (ok, message) => { assert(ok, message); checks.push(message); };
  document.body.innerHTML = PANEL_HTML.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '') +
    '<button class="car-route-button" data-name="모의 응급실" data-lat="37.51" data-lon="127">A</button>' +
    '<button class="car-route-button" data-name="다른 응급실" data-lat="37.52" data-lon="127">B</button>';
  document.querySelectorAll('link').forEach(link => link.remove());
  const style = document.createElement('style');
  style.textContent = PANEL_CSS + LEAFLET_CSS;
  document.head.appendChild(style);
  window.eval(LEAFLET_SOURCE);
  let map;
  const makeMap = L.map;
  L.map = (...args) => { map = makeMap(...args); return map; };
  // No real tile requests in this harness.
  L.tileLayer = () => ({addTo() { return this; }, on() { return this; }});
  const realNow = Date.now, realTimeout = window.setTimeout;
  let now = realNow(), hidden = false, secure = true;
  Date.now = () => now;
  Object.defineProperty(document, 'hidden', {configurable: true, get: () => hidden});
  Object.defineProperty(window, 'isSecureContext', {configurable: true, get: () => secure});
  const watchers = [], cleared = [], intervals = new Map(), requestTimers = new Map();
  let nextTimer = 1000000, denyGet = false, pendingGet = null, delayGet = false;
  window.setInterval = fn => { const id = nextTimer++; intervals.set(id, fn); return id; };
  window.clearInterval = id => intervals.delete(id);
  const realClearTimeout = window.clearTimeout;
  window.setTimeout = (fn, ms, ...args) => {
    if (ms !== 20000) return realTimeout(fn, ms, ...args);
    const id = nextTimer++; requestTimers.set(id, fn); return id;
  };
  window.clearTimeout = id => { requestTimers.delete(id); realClearTimeout(id); };
  const gps = {
    getCurrentPosition(ok, fail) {
      if (delayGet) { pendingGet = ok; return; }
      if (denyGet) fail({code: 1});
      else ok({coords: {latitude: 37.5, longitude: 127, accuracy: 10}, timestamp: now});
    },
    watchPosition(ok, fail, options) {
      assert(options.enableHighAccuracy && options.maximumAge === 0, 'Fresh high accuracy tracking');
      watchers.push({ok, fail}); return watchers.length - 1;
    },
    clearWatch(id) { cleared.push(id); },
  };
  Object.defineProperty(navigator, 'geolocation', {configurable: true, value: gps});
  const calls = [];
  let mode = 'ok', release = null;
  const response = (body, ok = true) => ({ok, json: async () => body});
  const route = (request, distance = 2000) => ({code: 'ok', distance_m: distance, duration_sec: 300,
    coordinates: [[127, 37.5], [127, request.destination.lat]], cached: false});
  window.fetch = async (url, options) => {
    assert(url === '/api/routes/car', 'Only existing route endpoint');
    const body = JSON.parse(options.body);
    assert(options.cache === 'no-store' && options.signal, 'Uncached cancellable route request');
    calls.push({body, signal: options.signal});
    if (mode === 'deferred') return new Promise(resolve => { release = () => resolve(response(route(body, 9999))); });
    if (mode === 'network') throw new TypeError('Synthetic offline');
    if (mode === 'quota') return response({code: 'quota_exceeded'}, false);
    if (mode === 'timeout') return new Promise((resolve, reject) => {
      options.signal.addEventListener('abort', () => reject(new DOMException('timeout', 'AbortError')));
    });
    if (mode === 'malformed') return response({code: 'ok', distance_m: -1, duration_sec: 1});
    if (mode === 'no_geometry') return response({...route(body), coordinates: []});
    return response(route(body));
  };
  window.eval(NAV_SOURCE);
  const el = id => document.getElementById('car-route-' + id);
  const destinations = document.querySelectorAll('.car-route-button');
  const settle = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };
  const choose = async (index = 0) => { destinations[index].click(); await settle(); };
  const start = () => el('start').click();
  const stop = () => el('stop').click();
  const fix = async (lat = 37.5, lon = 127, accuracy = 10, advance = 3000, timestamp) => {
    now += advance;
    watchers.at(-1).ok({coords: {latitude: lat, longitude: lon, accuracy}, timestamp: timestamp ?? now});
    await settle();
  };
  const line = () => Object.values(map._layers).find(layer => layer instanceof L.Polyline);
  const marker = () => Object.values(map._layers).find(layer => layer instanceof L.CircleMarker && layer.options.fillColor === '#2265b2');
  try {
    check(watchers.length === 0 && calls.length === 0, 'No GPS watch or API request before user action');
    await choose();
    check(calls.length === 1 && !el('start').disabled, 'Existing single GPS route remains available');
    const originalLine = line();
    start();
    check(watchers.length === 1 && !el('stop').disabled && el('refresh').disabled, 'Start watch and prevent manual overlapping refresh');
    start();
    check(watchers.length === 1, 'Repeated start cannot create duplicate watchers');
    await fix();
    await fix(37.5, 127, 10, 60000);
    check(calls.length === 1, 'Stationary GPS does not request routes after 30 seconds');
    await fix(37.5001, 127, 10, 30000);
    check(calls.length === 1, 'Small GPS drift does not count as real movement');
    await fix(37.501, 127, 200, 30000);
    check(calls.length === 1 && el('gps').textContent.includes('정확도'), 'Poor accuracy blocks routing');
    await fix(37.501, 127, 10, 3000);
    check(calls.length === 2 && line() === originalLine, '100m movement updates metrics and preserves route line');
    check(Math.abs(marker().getLatLng().lat - 37.501) < 1e-8, 'Current location marker moves');
    await fix(37.502, 127, 10, 3000);
    check(calls.length === 2, 'Minimum request interval prevents burst requests');
    // Manual map interaction must not be undone even by a reroute.
    map.fire('dragstart');
    const manualCenter = map.getCenter();
    await fix(37.502, 127, 10, 3000);
    check(el('follow').getAttribute('aria-pressed') === 'false' && map.getCenter().equals(manualCenter), 'User pan disables automatic centering');
    el('follow').click();
    check(el('follow').getAttribute('aria-pressed') === 'true' && Math.abs(map.getCenter().lat - 37.502) < .0001, 'Follow button restores current-position centering');
    await fix(38.5, 128, 10, 1000);
    check(el('gps').textContent.includes('위치 튐') && calls.length === 2, 'Implausible GPS jump is ignored');
    const savedMarker = marker().getLatLng();
    await fix(37.503, 127, 10, 2000, now - 20000);
    check(marker().getLatLng().equals(savedMarker), 'Stale GPS fix does not move marker');
    // A moderate displacement after 30 seconds also updates; elapsed time alone did not.
    await choose(); start(); await fix();
    let before = calls.length;
    await fix(37.5003, 127, 10, 31000);
    check(calls.length === before + 1, '30s refresh requires meaningful displacement and accurate GPS');
    // Reroute requires three independent fixes and resets after returning to route.
    await choose(); start(); await fix(); before = calls.length;
    await fix(37.5, 127.002, 10, 11000);
    const keptLine = line();
    await fix(37.5, 127.002, 10, 3000);
    check(line() === keptLine, 'Two off-route fixes do not replace route');
    await fix(37.5, 127, 10, 3000);
    await fix(37.5, 127.002, 10, 3000);
    check(line() === keptLine, 'On-route fix resets deviation confirmation');
    await fix(37.5, 127.002, 10, 500);
    check(line() === keptLine, 'Rapid duplicate samples do not confirm deviation');
    map.fire('dragstart');
    const rerouteCenter = map.getCenter();
    await fix(37.5, 127.002, 10, 3000);
    await fix(37.5, 127.002, 10, 3000);
    check(line() !== keptLine && calls.length >= before + 2, 'Three spaced off-route fixes trigger reroute');
    check(map.getCenter().equals(rerouteCenter), 'Reroute preserves manual map viewport');
    // Arrival must be accurate, repeated and near this destination.
    await choose(); start(); await fix();
    await fix(37.5099, 127, 40, 60000);
    check(!el('stop').disabled, 'Low accuracy cannot confirm arrival');
    await fix(37.5099, 127, 10, 3000);
    await fix(37.5099, 127, 10, 500);
    check(!el('stop').disabled, 'One fix and burst sample cannot confirm arrival');
    await fix(37.5099, 127, 10, 3000);
    await fix(37.5099, 127, 10, 3000);
    check(el('stop').disabled && el('status').textContent.includes('도착'), 'Three accurate spaced fixes confirm nearby arrival and stop watch');
    check(cleared.includes(0), 'Watch id zero is cleared correctly');
    await choose(); start(); await fix();
    await fix(37.5099, 127, 10, 60000);
    await fix(37.5099, 127, 10, 16000);
    await fix(37.5099, 127, 10, 3000);
    check(!el('stop').disabled, 'Long sampling gap resets arrival confirmation');
    await fix(37.5099, 127, 100, 3000);
    await fix(37.5099, 127, 10, 3000);
    await fix(37.5099, 127, 10, 3000);
    check(!el('stop').disabled, 'Inaccurate sample breaks arrival confirmation');
    await fix(37.5095, 127, 10, 500);
    await fix(37.5099, 127, 10, 2500);
    check(!el('stop').disabled, 'Contradictory burst sample resets arrival before counting again');
    // Old callbacks and requests must never cross a stop or destination boundary.
    await choose(); start(); await fix(); mode = 'deferred';
    await fix(37.502, 127, 10, 11000);
    const pendingSignal = calls.at(-1).signal, oldCallback = watchers.at(-1).ok;
    const oldMetric = el('distance').textContent;
    stop(); before = calls.length;
    oldCallback({coords: {latitude: 37.504, longitude: 127, accuracy: 10}, timestamp: now + 5000});
    release(); await settle();
    check(pendingSignal.aborted && el('distance').textContent === oldMetric && calls.length === before, 'Stop aborts request and rejects late GPS and response');
    mode = 'ok'; await choose(); start(); await fix(); mode = 'deferred';
    await fix(37.502, 127, 10, 11000);
    const oldRelease = release, previousSignal = calls.at(-1).signal;
    mode = 'ok'; await choose(1); oldRelease(); await settle();
    check(previousSignal.aborted && el('destination').textContent.includes('다른 응급실') && el('distance').textContent === '2.0 km' && el('stop').disabled, 'Destination change stops tracking and rejects previous route response');
    // Permission, GPS timeout, API failures and bounded retries.
    await choose(); start(); watchers.at(-1).fail({code: 3});
    check(el('gps').textContent.includes('초과') && !el('stop').disabled, 'GPS timeout reports recovery state without losing route');
    watchers.at(-1).fail({code: 2});
    check(el('gps').textContent.includes('확인할 수 없습니다'), 'GPS unavailable is explained');
    watchers.at(-1).fail({code: 1});
    check(el('stop').disabled && el('gps').textContent.includes('거부'), 'Permission denial stops tracking');
    denyGet = true; await choose();
    check(el('result').hidden && el('status').textContent.includes('거부'), 'Existing one-time GPS denial behavior preserved');
    denyGet = false; await choose(); start(); await fix(); mode = 'network';
    await fix(37.502, 127, 10, 11000); before = calls.length;
    check(el('status').textContent.includes('네트워크') && !el('result').hidden, 'API network failure retains route and last metrics');
    await fix(37.503, 127, 10, 11000);
    check(calls.length === before, 'Failure backoff prevents immediate retry');
    mode = 'ok'; await fix(37.503, 127, 10, 11000);
    check(calls.length === before + 1, 'Moving GPS recovers after retry backoff');
    for (const [failureMode, text] of [['quota', '한도'], ['malformed', '형식'], ['timeout', '초과']]) {
      stop(); mode = 'ok'; await choose(); start(); await fix(); mode = failureMode;
      await fix(37.502, 127, 10, 11000);
      if (failureMode === 'timeout') { [...requestTimers.values()].forEach(fn => fn()); await settle(); }
      check(el('status').textContent.includes(text) && !el('result').hidden, failureMode + ' route failure is handled');
    }
    mode = 'ok'; stop(); await choose(); start(); await fix();
    now += 31000; [...intervals.values()].forEach(fn => fn());
    check(el('gps').textContent.includes('위치 수신 지연'), 'No-fix watchdog exposes stale GPS');
    hidden = true; document.dispatchEvent(new Event('visibilitychange'));
    check(el('stop').disabled && intervals.size === 0 && el('gps').textContent.includes('화면 숨김'), 'Background transition clears GPS and watchdog');
    hidden = false; document.dispatchEvent(new Event('visibilitychange'));
    check(el('stop').disabled, 'Foreground return requires explicit restart');
    mode = 'ok'; await choose(); start(); await fix(); mode = 'deferred';
    await fix(37.502, 127, 10, 11000);
    const backgroundSignal = calls.at(-1).signal, backgroundRelease = release;
    const backgroundMetric = el('distance').textContent;
    hidden = true; document.dispatchEvent(new Event('visibilitychange'));
    backgroundRelease(); await settle();
    check(backgroundSignal.aborted && el('distance').textContent === backgroundMetric, 'Background aborts request and ignores late response');
    hidden = false; document.dispatchEvent(new Event('visibilitychange')); mode = 'ok';
    start(); window.dispatchEvent(new Event('pagehide'));
    check(el('stop').disabled && intervals.size === 0, 'Page exit clears tracking');
    // A one-time GPS callback cannot resurrect a request after leaving the screen.
    delayGet = true; destinations[0].click(); window.dispatchEvent(new Event('pagehide'));
    before = calls.length; pendingGet({coords: {latitude: 37.5, longitude: 127, accuracy: 10}, timestamp: now});
    await settle(); delayGet = false;
    check(calls.length === before, 'Page exit rejects pending one-time GPS');
    mode = 'no_geometry'; await choose(); start(); await fix();
    check(el('map-status').textContent.includes('좌표가 없어') && marker(), 'Missing road geometry still shows endpoints');
    stop(); secure = false; start();
    check(el('gps').textContent.includes('HTTPS') && el('stop').disabled, 'Insecure context cannot start tracking');
    secure = true;
    mode = 'ok'; await choose(); start(); await fix();
    const following = () => el('follow').getAttribute('aria-pressed') === 'true';
    const centered = lat => map.getCenter().distanceTo(L.latLng(lat, 127)) < 10;
    const container = map.getContainer();
    let dragStarts = 0;
    map.on('dragstart', () => { dragStarts++; });
    // Exercise Leaflet's real mouse drag handler, rather than firing dragstart ourselves.
    const mouse = (type, x, y) => {
      const event = new MouseEvent(type, {bubbles: true, cancelable: true, view: window,
        button: 0, buttons: type === 'mouseup' ? 0 : 1, clientX: x, clientY: y});
      Object.defineProperty(event, 'which', {value: 1});
      container.dispatchEvent(event);
    };
    const bounds = container.getBoundingClientRect();
    const x = bounds.left + 100, y = bounds.top + 100;
    for (const name of ['pointerdown', 'touchstart', 'wheel', 'keydown']) {
      container.dispatchEvent(new Event(name, {bubbles: true}));
      check(following(), name + ' alone preserves following');
    }
    mouse('mousedown', x, y); mouse('mouseup', x, y);
    container.dispatchEvent(new MouseEvent('click', {bubbles: true, clientX: x, clientY: y}));
    check(following() && dragStarts === 0, 'Tap without movement does not disable following');
    await fix(37.5003, 127, 10, 5000);
    check(following() && centered(37.5003), 'GPS panTo after tap keeps following active');
    const initialZoom = map.getZoom();
    map.setZoom(initialZoom + 2, {animate: false});
    map.fire('zoomstart', {originalEvent: new Event('wheel')});
    check(following(), 'Zoom with originalEvent preserves following');
    await fix(37.5005, 127, 10, 5000);
    check(following() && centered(37.5005) && map.getZoom() === initialZoom + 2, 'GPS follows after zoom-in and preserves chosen zoom');
    map.setZoom(initialZoom + 1, {animate: false});
    await fix(37.5007, 127, 10, 5000);
    check(following() && centered(37.5007) && map.getZoom() === initialZoom + 1, 'GPS follows after zoom-out and preserves chosen zoom');
    check(dragStarts === 0, 'GPS panTo and zoom never emit dragstart');
    map.options.inertia = false; // Make the simulated drag end deterministic.
    mouse('mousedown', x, y); mouse('mousemove', x + 60, y + 40); mouse('mouseup', x + 60, y + 40);
    check(dragStarts === 1 && !following(), 'Actual mouse drag emits Leaflet dragstart and disables following');
    const draggedCenter = map.getCenter();
    await fix(37.5009, 127, 10, 5000);
    check(!following() && map.getCenter().equals(draggedCenter) && Math.abs(marker().getLatLng().lat - 37.5009) < 1e-8,
      'GPS after drag moves marker while preserving manual viewport');
    el('follow').click();
    check(following() && centered(37.5009) && map.getZoom() === initialZoom + 1, 'Follow button recenters immediately without changing zoom');
    await fix(37.5011, 127, 10, 5000);
    check(following() && centered(37.5011) && map.getZoom() === initialZoom + 1, 'Future GPS continues following after restoration');
    stop();
    check(requestTimers.size === 0 && intervals.size === 0, 'All request timers and GPS watchdogs cleaned up');
    return {checks: checks.length, passed: checks};
  } finally {
    stop();
    Date.now = realNow;
    window.setTimeout = realTimeout;
    window.clearTimeout = realClearTimeout;
  }
})()
