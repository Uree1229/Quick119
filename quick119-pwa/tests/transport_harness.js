/* Production GPS + chat + Leaflet together, with explicitly synthetic HTTP/GPS/time. */
(async () => {
  const checks = [];
  const check = (value, name) => { if (!value) throw new Error(name); checks.push(name); };
  document.body.innerHTML = WORKSPACE_HTML.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '');
  document.querySelectorAll('link').forEach(link => link.remove());
  const style = document.createElement('style'); style.textContent = WORKSPACE_CSS + PANEL_CSS + LEAFLET_CSS; document.head.append(style);
  document.body.style.margin = '0'; document.body.style.padding = '8px';
  const viewport = document.createElement('meta'); viewport.name = 'viewport'; viewport.content = 'width=device-width, initial-scale=1'; document.head.append(viewport);
  window.eval(LEAFLET_SOURCE);
  let map, mapCount = 0, invalidations = 0;
  const originalMap = L.map;
  L.map = (...args) => { mapCount++; map = originalMap(...args); const invalidate = map.invalidateSize;
    map.invalidateSize = (...args) => { invalidations++; return invalidate.apply(map, args); }; return map; };
  let tileError;
  L.tileLayer = () => ({addTo() { return this; }, on(event, handler) { if (event === 'tileerror') tileError = handler; return this; }});
  let hidden = false, now = Date.now();
  Date.now = () => now;
  Object.defineProperty(document, 'hidden', {configurable: true, get: () => hidden});
  Object.defineProperty(window, 'isSecureContext', {configurable: true, value: true});
  if (!crypto.randomUUID) { let sequence = 0; crypto.randomUUID = () => `00000000-0000-4000-8000-${String(++sequence).padStart(12, '0')}`; }
  const realTimeout = window.setTimeout, realClearTimeout = window.clearTimeout;
  const timers = new Map(), intervals = new Map(); let nextTimer = 1000000;
  window.setTimeout = (fn, ms, ...args) => {
    if ([3000, 12000, 20000].includes(ms)) { const id = nextTimer++; timers.set(id, {fn, ms}); return id; }
    return realTimeout(fn, ms, ...args);
  };
  window.clearTimeout = id => { timers.delete(id); realClearTimeout(id); };
  window.setInterval = (fn, ms) => { const id = nextTimer++; intervals.set(id, {fn, ms}); return id; };
  window.clearInterval = id => intervals.delete(id);
  const watchers = [], cleared = [];
  let current = {lat: 35.1595, lon: 126.8526};
  const position = (p = current, accuracy = 10) => ({coords: {latitude: p.lat, longitude: p.lon, accuracy}, timestamp: now});
  Object.defineProperty(navigator, 'geolocation', {configurable: true, value: {
    getCurrentPosition(ok) { ok(position()); },
    watchPosition(ok, fail) { watchers.push({ok, fail}); return watchers.length - 1; },
    clearWatch(id) { cleared.push(id); },
  }});
  const storageWrites = [];
  Storage.prototype.setItem = function(key) { storageWrites.push(key); throw new Error('Persistent storage forbidden'); };
  let replacements = 0;
  history.replaceState = () => { replacements++; };
  const $ = id => document.getElementById('transport-' + id), nav = id => document.getElementById('car-route-' + id);
  const hospitals = [{id: 'H01', name: '가상 응급실 <script>', source: 'synthetic', lat: 35.1715, lon: 126.8326},
    {id: 'H02', name: '가상 병원 B', source: 'synthetic', lat: 35.1895, lon: 126.8826}];
  let patient = null, accepted = false, session = null, state = 'OPEN', losePost = false;
  const messages = [], events = [], posts = [], routeCalls = [], inquiries = [];
  const reply = body => ({ok: true, status: 200, json: async () => body});
  const fail = (status, error) => ({ok: false, status, json: async () => ({error, message: error})});
  window.fetch = async (raw, options) => {
    const url = new URL(raw, 'http://127.0.0.1'); const body = options.body ? JSON.parse(options.body) : null;
    check(options.cache === 'no-store', 'Every API request avoids HTTP cache');
    if (url.pathname === '/api/routes/car') {
      routeCalls.push(body); return reply({code: 'ok', distance_m: 2000, duration_sec: 300,
        coordinates: [[body.origin.lon, body.origin.lat], [body.destination.lon, body.destination.lat]], cached: false});
    }
    if (url.pathname === '/api/transport/patients') return reply({patients: []});
    if (url.pathname === '/api/hospitals') return reply(hospitals);
    if (url.pathname === '/api/patients') { patient = {...body, state: 'WAITING', version: 1}; events.push({id: 'event:1', kind: 'system', type: 'patient_created', sender_role: 'system', at: now / 1000, text: '가상 이송 생성', order_key: [1, 0, 1]}); return reply(body); }
    if (patient && url.pathname === '/api/patients/' + patient.id) {
      if (options.method === 'PATCH') patient = {...patient, ...body, version: patient.version + 1};
      return reply(patient);
    }
    if (url.pathname.endsWith('/candidates')) {
      if (options.method === 'POST') {
        inquiries.push(...hospitals.map((h, index) => ({id: index + 1, hospital_id: h.id,
          hospital_name: h.name, state: 'PENDING', usable: false})));
        return reply({candidates: hospitals.map(h => ({...h, hospital_id: h.id})),
          automatic_inquiry: {hospital_ids: hospitals.map(h => h.id), already_started: false}});
      }
      return reply({candidates: hospitals.map(h => ({...h, hospital_id: h.id}))});
    }
    if (url.pathname.endsWith('/inquiries') && body) { inquiries.push({id: 1, hospital_id: body.hospital_ids[0], hospital_name: hospitals[0].name, state: 'PENDING', usable: false}); return reply({inquiries}); }
    if (url.pathname.endsWith('/confirm')) { check(accepted, 'Confirmation follows staff acceptance'); session = 1; inquiries[0].state = 'CONFIRMED'; return reply({id: patient.id}); }
    if (url.pathname.endsWith('/conversation')) {
      const ec = Number(url.searchParams.get('event_cursor')), mc = Number(url.searchParams.get('message_cursor'));
      const limit = Number(url.searchParams.get('limit')) || 100;
      const ev = events.filter(e => Number(e.id.split(':')[1]) > ec).slice(0, limit);
      const msg = messages.filter(m => m.message_id > mc).slice(0, limit);
      return reply({patient, conversation: {id: 1, state}, active_hospital_session: session ? {id: session, hospital_id: 'H01', hospital_name: hospitals[0].name} : null,
        can_send: state === 'OPEN', inquiries: [...inquiries], items: [...ev, ...msg],
        cursors: {event_id: ev.length ? Number(ev.at(-1).id.split(':')[1]) : ec, message_id: msg.length ? msg.at(-1).message_id : mc}, has_more: false});
    }
    if (url.pathname.endsWith('/messages')) {
      posts.push(body);
      const prior = messages.find(m => m.client_message_id === body.client_message_id);
      if (prior) return reply({message: prior, duplicate: true});
      if (body.hospital_session_id !== session) return fail(409, 'STALE_SESSION');
      const m = {id: `message:${messages.length + 1}`, message_id: messages.length + 1, kind: 'message', sender_role: 'patient',
        sender_name: body.sender_name, body: body.body, text: body.body, client_message_id: body.client_message_id,
        hospital_session_id: session, at: now / 1000, order_key: [1, 1, messages.length + 1]};
      messages.push(m); if (losePost) { losePost = false; throw new TypeError('Synthetic lost response'); }
      return reply({message: m, duplicate: false});
    }
    throw new Error('Unexpected API: ' + raw);
  };
  const settle = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); await new Promise(resolve => realTimeout(resolve, 40)); };
  const tick = async () => {
    const polls = [...timers].filter(([, v]) => v.ms === 3000);
    check(polls.length <= 1, 'At most one chat polling timer');
    for (const [id, value] of polls) { timers.delete(id); value.fn(); }
    await settle();
  };
  const fix = async (dlat, advance = 11000, accuracy = 10) => {
    now += advance; current = {...current, lat: current.lat + dlat}; watchers.at(-1).ok(position(current, accuracy)); await settle();
  };
  const staffMessage = text => { const n = messages.length + 1; messages.push({id: 'message:' + n, message_id: n, kind: 'message',
    sender_role: 'hospital', sender_name: '가상 의료진', text, body: text, at: now / 1000, order_key: [1, 1, n]}); };
  window.eval(NAV_SOURCE); window.eval(WORKSPACE_SOURCE); window.eval(VOICE_SOURCE); window.eval(REVIEW_SOURCE); window.eval(CHAT_SOURCE); await settle();
  check(!patient && !$('new-case').disabled, 'A plain app launch does not create a new case');
  $('new-case').click(); await settle();
  check(patient?.id.startsWith('Q119-TEST-'), 'Only a synthetic episode is created: ' + $('error').textContent);
  check(!$('chat').hidden && $('gps').hidden && !$('send').disabled, 'Explicit emergency entry opens writable system/crew chat');
  check($('participants').children.length === 2, 'Only system and crew participate before confirmation');
  check(patient.age == null && patient.symptoms == null, 'New room does not invent patient age or symptoms');
  $('message').value = '병원 확정 전 환자 상태 보고'; $('message').dispatchEvent(new Event('input'));
  $('message-form').dispatchEvent(new Event('submit', {bubbles: true, cancelable: true})); await settle();
  check(posts.length === 1 && posts[0].hospital_session_id === null && $('message').value === '', 'Private report is sent to the same room before any inquiry');
  $('start-search').click(); $('review-submit').click(); await settle();
  check(!$('gps').hidden && document.querySelectorAll('[data-candidate-id]').length === hospitals.length
    && !document.querySelector('[data-transport-inquire]'), 'Review opens map with ranked candidates and no manual inquiry controls');
  const handle = $('sheet-handle'), sheet = $('sheet');
  const sheetOpen = () => !sheet.hidden && handle.getAttribute('aria-expanded') === 'true';
  const listMapHeight = nav('map').clientHeight;
  check(handle instanceof HTMLButtonElement && handle.getAttribute('aria-controls') === sheet.id && sheetOpen(), 'Map opens with an accessible list control');
  sheet.scrollTop = sheet.scrollHeight;
  check(handle.getBoundingClientRect().bottom <= sheet.getBoundingClientRect().top + 1, 'Scrolling details leaves the handle above the scroll area');
  handle.click();
  check(sheet.hidden && handle.getAttribute('aria-expanded') === 'false' && handle.offsetHeight >= 44 && nav('map').clientHeight > listMapHeight, 'Collapsing the list enlarges the map and keeps its return control');
  handle.click();
  check(sheetOpen(), 'Native click activation reopens the list');
  // Synthetic pointer events do not register an active browser pointer for capture.
  handle.setPointerCapture = () => {};
  const swipe = (dy, end = 'pointerup') => {
    const pointer = (type, clientY) => handle.dispatchEvent(new PointerEvent(type, {pointerId: 7, isPrimary: true, clientY}));
    pointer('pointerdown', 80); pointer(end, 80 + dy);
    if (end === 'pointerup') handle.dispatchEvent(new MouseEvent('click', {detail: 1}));
  };
  swipe(48, 'pointercancel');
  check(sheetOpen(), 'Cancelled swipe leaves the list open');
  swipe(48);
  check(sheet.hidden, 'Downward swipe survives its compatibility click');
  swipe(-48);
  check(sheetOpen(), 'Upward swipe survives its compatibility click');
  delete handle.setPointerCapture;
  handle.click();
  const callsBeforePin = routeCalls.length;
  const inquiriesBeforePin = inquiries.length;
  document.querySelector('.tw-pin').click();
  check(sheetOpen() && $('cand-H01').dataset.focus === 'true' && routeCalls.length === callsBeforePin
    && inquiries.length === inquiriesBeforePin, 'A candidate pin reveals its card without changing the automatic inquiries');
  tileError();
  check(nav('map-status').textContent === '배경지도 일부를 불러오지 못했습니다.', 'Tile failure does not mislabel candidate or route data');
  check(inquiries.length === hospitals.length && inquiries.every(q => q.state === 'PENDING')
    && !$('send').disabled && $('participants').children.length === 2, 'Automatic inquiries keep system/crew chat writable without adding hospitals');
  accepted = true; inquiries[0].state = 'ACCEPTED'; inquiries[0].usable = true; await tick();
  const beforeConfirmation = {sendDisabled: $('send').disabled,
    participants: [...$('participants').children].map(person => ({role: person.dataset.role, name: person.textContent})),
    session, confirmButton: !!document.querySelector('[data-transport-confirm]')};
  check(!beforeConfirmation.sendDisabled && beforeConfirmation.participants.length === 2 && beforeConfirmation.confirmButton,
    'Staff acceptance alone does not add hospital: ' + JSON.stringify(beforeConfirmation));
  $('message').value = '시스템에 작성 중인 미전송 초안'; $('message').dispatchEvent(new Event('input'));
  handle.click();
  document.querySelector('[data-transport-confirm]').click(); await settle();
  check(sheetOpen() && sheet.scrollTop === 0, 'Selecting the confirmed route reopens details at the top');
  check(session === 1 && $('participants').children.length === 3, 'Confirmation adds hospital to the existing system/crew room');
  check($('send').disabled && !$('review-draft').hidden, 'Private draft requires review before sharing with newly joined hospital');
  $('message').value = ''; $('message').dispatchEvent(new Event('input'));
  check(!$('send').disabled && $('messages').textContent.includes('병원 확정 전 환자 상태 보고'), 'Confirmation preserves prior report history and enables shared messages');
  nav('start').click();
  check(watchers.length === 1 && cleared.length === 0, 'GPS starts once');
  await fix(0);
  map.setZoom(17, {animate: false}); const sameMap = map, sameLog = $('messages');
  const before = routeCalls.length;
  $('toggle').click(); await settle();
  check($('gps').hidden && !$('chat').hidden && cleared.length === 0, 'Chat panel switch keeps watcher alive');
  await fix(.0011);
  check(routeCalls.length > before, 'Route API refresh continues while chat is visible');
  const callsAfter = routeCalls.length; await fix(.0003, 1000);
  check(routeCalls.length === callsAfter, 'Original route rate limit remains active');
  await fix(.00001, 2000, 500);
  check(routeCalls.length === callsAfter, 'Poor GPS accuracy remains filtered');
  $('message').value = '<img src=x onerror="window.injected=true">가상 메시지'; losePost = true;
  $('message').dispatchEvent(new Event('input'));
  const sentBeforeRetry = posts.length;
  $('message-form').dispatchEvent(new Event('submit', {bubbles: true, cancelable: true})); await settle();
  check(!$('discard').hidden && $('send').textContent.includes('재시도'), 'Uncertain delivery keeps original request in memory');
  $('message-form').dispatchEvent(new Event('submit', {bubbles: true, cancelable: true})); await settle();
  check(posts.length === sentBeforeRetry + 2 && JSON.stringify(posts[sentBeforeRetry]) === JSON.stringify(posts[sentBeforeRetry + 1]), 'Lost response retries exact ID, body and session');
  check(messages.filter(m => m.sender_role === 'patient').length === 2 && $('discard').hidden, 'Retry does not duplicate saved message');
  check(!$('messages').querySelector('img') && !window.injected, 'Messages render as plain text');
  $('message').value = '이전 병원에 쓰던 초안'; $('message').dispatchEvent(new Event('input'));
  session = 2; await tick();
  check($('send').disabled && !$('review-draft').hidden, 'Changed hospital session requires explicit draft review');
  $('review-draft').click(); check(!$('send').disabled, 'Draft sends to changed session only after explicit review');
  $('message').value = ''; $('message').dispatchEvent(new Event('input')); session = 1; await tick();
  $('toggle').click(); await settle();
  check(map === sameMap && mapCount === 1 && map.getZoom() === 17, 'Map instance and zoom survive hidden route updates: ' + map.getZoom() + '/' + mapCount);
  check(nav('follow').getAttribute('aria-pressed') === 'true' && Math.abs(map.getCenter().lat - (current.lat - .00001)) < .00001, 'Follow mode restores latest accepted GPS center');
  staffMessage('의료진 새 메시지'); await tick();
  check(!$('unread').hidden && $('unread').textContent === '1', 'GPS view receives staff message and unread badge');
  await tick(); check($('unread').textContent === '1', 'Polling does not double-count unread messages');
  $('toggle').click(); await settle();
  if (!$('mark-read').hidden) $('mark-read').click();
  check($('unread').hidden && $('messages') === sameLog, 'Viewing the message clears badge without recreating chat');
  $('message').value = '전환 중 초안';
  for (let i = 0; i < 8; i++) { $('toggle').click(); await settle(); }
  check(watchers.length === 1 && mapCount === 1 && $('message').value === '전환 중 초안', 'Repeated switches preserve draft and single GPS/map');
  $('toggle').click(); await settle();
  map.fire('dragstart'); map.panTo([35.17, 126.84], {animate: false}); const center = map.getCenter();
  $('toggle').click(); await settle(); await fix(.0001, 11000);
  $('toggle').click(); await settle();
  check(nav('follow').getAttribute('aria-pressed') === 'false' && map.getCenter().distanceTo(center) < 1, 'Manual view center and follow-off survive switches');
  $('toggle').click(); await settle();
  const count = routeCalls.length; await fix(.0011, 11000);
  check(cleared.length === 0 && routeCalls.length > count, 'GPS updates continue in chat view');
  nav('stop').click(); const pollBefore = [...timers.values()].filter(t => t.ms === 3000).length;
  staffMessage('GPS 종료 뒤 답장'); await tick();
  check(cleared.length === 1 && pollBefore === 1 && $('messages').textContent.includes('GPS 종료 뒤 답장'), 'GPS stop leaves chat receiving');
  nav('start').click(); await fix(0);
  hidden = true; document.dispatchEvent(new Event('visibilitychange')); await settle();
  check(cleared.length === 2 && ![...timers.values()].some(t => t.ms === 3000), 'Actual background stops GPS and pauses chat');
  staffMessage('백그라운드 중 답장');
  hidden = false; document.dispatchEvent(new Event('visibilitychange')); await settle();
  check(nav('stop').disabled && watchers.length === 2 && $('messages').textContent.includes('백그라운드 중 답장'), 'Foreground catches up chat but GPS needs explicit restart');
  state = 'CLOSED'; await tick(); check($('send').disabled && $('chat-status').textContent.includes('종료'), 'Server conversation closure makes chat read-only');
  check(storageWrites.length === 0, 'No message or patient writes to persistent browser storage');
  // A view switch only rewrites the address (?view=map) in place, so a reload reopens the same screen; it never loads a page.
  check(invalidations > 0 && replacements >= 1, 'Map resize is handled; view switches do not navigate');
  for (const width of [360, 375, 390, 412, 430]) {
    document.documentElement.style.width = width + 'px'; document.body.style.width = (width - 16) + 'px';
    for (let i = 0; i < 2; i++) { $('toggle').click(); await settle(); check(document.getElementById('transport-workspace').scrollWidth <= width - 16, 'No horizontal overflow at ' + width + 'px'); }
  }
  window.dispatchEvent(new Event('pagehide')); await settle();
  check(![...timers.values()].some(t => [3000, 12000, 20000].includes(t.ms)) && intervals.size === 0, 'Page exit clears both controllers and timers');
  return {checks: checks.length, names: checks, mapCount, watchCount: watchers.length};
})()
