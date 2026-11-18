// Synthetic DOM, HTTP and SpeechRecognition; executes the shipped scripts without a browser or microphone.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import vm from 'node:vm';
import test from 'node:test';

const sources = Object.fromEntries(['chat-voice', 'hospital-chat', 'transport-workspace', 'intake-commands', 'patient-review'].map(name =>
  [name, readFileSync(fileURLToPath(new URL('../static/' + name + '.js', import.meta.url)), 'utf8')]));
const settle = async () => { for (let i = 0; i < 12; i++) await new Promise(resolve => setImmediate(resolve)); };

function harness({restored = false, hospitalFailure = false, speech = true, viewport = false, embedded = false} = {}) {
  const nodes = new Map(), allNodes = new Set(), calls = [], timers = new Map(), speeches = [];
  let nextTimer = 0, uuid = 0;
  class Events {
    listeners = new Map();
    addEventListener(name, fn) { const list = this.listeners.get(name) || []; list.push(fn); this.listeners.set(name, list); }
    dispatchEvent(event) { for (const fn of this.listeners.get(event.type) || []) fn(event); return true; }
    async emit(name, detail) { await Promise.all((this.listeners.get(name) || []).map(fn => fn({type: name, detail, preventDefault() {}}))); }
  }
  class Element extends Events {
    constructor() { super(); this.children = []; this.dataset = {}; this.hidden = false; this.disabled = false;
      this.value = ''; this.textContent = ''; this.scrollHeight = 100; this.clientHeight = 100; this.scrollTop = 0; this.maxLength = 2000; allNodes.add(this); }
    set id(value) { this._id = value; nodes.set(value, this); }
    get id() { return this._id; }
    setAttribute(name, value) { this[name] = value; }
    append(...children) { for (const child of children) { if (child.parent) child.parent.children = child.parent.children.filter(node => node !== child); this.children.push(child); child.parent = this; } }
    replaceChildren(...children) { this.children = []; this.append(...children); }
    focus() {}
  }
  const ids = ['workspace', 'chat', 'gps', 'message', 'message-label', 'message-form', 'messages', 'intro',
    'participants', 'sharing', 'voice-start', 'voice-stop', 'voice-status', 'voice-interim', 'new-case', 'send',
    'discard', 'review-draft', 'pending', 'chat-stop', 'chat-resume', 'case', 'connection', 'chat-status',
    'hospitals', 'voice-panel', 'hospital-contact', 'hospital-status', 'reload-hospitals', 'inquiries', 'error', 'unread', 'toggle', 'toggle-label',
    'title', 'mark-read', 'review-panel', 'patient-info', 'start-search', 'review-close', 'review-close-top', 'review-form', 'search-status'];
  for (const id of ids) { const node = new Element(); node.id = 'transport-' + id; }
  const $ = id => nodes.get('transport-' + id);
  $('gps').hidden = true;
  const document = new Events(); document.hidden = false;
  document.getElementById = id => nodes.get(id) || null;
  document.createElement = () => new Element();
  document.querySelectorAll = () => [...allNodes].filter(node => node.dataset.transportConfirm || node.dataset.transportInquire);
  const window = new Events(); window.isSecureContext = true;
  if (viewport) {
    // A 360x740 phone; a test shrinks visualViewport.height to open the soft keyboard.
    Object.assign(window, {innerWidth: 360, innerHeight: 740, scrollY: 0,
      scrollBy(_x, y) { this.scrollY += y; }, visualViewport: Object.assign(new Events(), {height: 740, offsetTop: 0, scale: 1})});
    const composer = {getBoundingClientRect() { return {top: 350, bottom: 400}; }};
    $('message').closest = selector => selector === '.tw-composer' ? composer : null;
    $('workspace').closest = selector => selector === '.app-shell--workspace' && !embedded ? {} : null;
    document.documentElement = {style: {props: {}, setProperty(name, value) { this.props[name] = value; }, removeProperty(name) { delete this.props[name]; }}};
  }
  class Speech {
    constructor() { speeches.push(this); }
    start() { this.onstart?.(); }
    stop() { this.stopped = true; }
    abort() { this.aborted = true; }
    result(text, isFinal, index = 0) {
      const result = [{transcript: text}]; result.isFinal = isFinal;
      const results = Array.from({length: index + 1}, () => Object.assign([{transcript: ''}], {isFinal: false})); results[index] = result;
      this.onresult?.({resultIndex: index, results});
    }
  }
  if (speech) window.SpeechRecognition = Speech;
  const state = {session: null, failMessageOnce: false, beforeConversation: null, messages: [], inquiries: [], receipts: new Map()};
  const patientId = 'Q119-TEST-restored';
  const welcome = {id: 'event:1', kind: 'system', sender_role: 'system', sender_name: '시스템', at: 1,
    text: '환자 상태를 보고해 주세요.', order_key: [1, 0, 1]};
  function snapshot() {
    return {conversation: {state: 'OPEN'}, can_send: true, active_hospital_session: state.session,
      participants: [{id: 'system', role: 'system', name: '시스템'}, {id: 'crew', role: 'crew', name: '구급대'},
        ...(state.session ? [{id: 'hospital:H01', role: 'hospital', name: '가상 병원 H01', hospital_session_id: state.session.id}] : [])],
      inquiries: state.inquiries, items: [welcome, ...state.messages], cursors: {event_id: 1, message_id: state.receipts.size}, has_more: false};
  }
  async function fetch(url, options) {
    const body = options.body ? JSON.parse(options.body) : undefined;
    calls.push({url, body, method: options.method});
    if (url === '/api/hospitals') {
      if (hospitalFailure) throw new Error('hospital list offline');
      return response([{id: 'H01', name: '가상 병원 H01', source: 'synthetic', lat: 35.1, lon: 126.8}]);
    }
    if (url === '/api/patients') return response(body);
    if (url.includes('/conversation?')) { state.beforeConversation?.(); state.beforeConversation = null; return response(snapshot()); }
    if (url.endsWith('/messages')) {
      let receipt = state.receipts.get(body.client_message_id);
      if (!receipt) {
        const id = state.receipts.size + 1;
        const message = {...body, id: 'message:' + id, message_id: id, kind: 'message', sender_role: 'patient', at: 2 + id,
          order_key: [2 + id, 1, id]};
        const system = {...message, id: 'system-report:' + id, kind: 'system', type:'intake_report_received', sender_role: 'system', sender_name: '시스템',
          text: '보고를 받았습니다.', order_key: [...message.order_key, 1]};
        receipt = {duplicate: false, message, system_items: body.hospital_session_id === null ? [system] : []};
        state.receipts.set(body.client_message_id, receipt); state.messages.push(message, ...receipt.system_items);
      } else receipt = {...receipt, duplicate: true};
      if (state.failMessageOnce) { state.failMessageOnce = false; throw new Error('response lost after save'); }
      return response(receipt);
    }
    throw new Error('Unexpected HTTP path ' + url);
  }
  function response(body) { return {ok: true, status: 200, json: async () => body}; }
  const location = {href: 'http://127.0.0.1/transport/' + (restored ? '?pid=' + patientId : ''), search: restored ? '?pid=' + patientId : ''};
  const context = vm.createContext({window, document, location, history: {replaceState(_state, _title, url) { location.href = url.toString(); location.search = url.search; }},
    fetch, crypto: {randomUUID: () => 'uuid-' + (++uuid)}, console, URL, URLSearchParams, AbortController, Date,
    CustomEvent: class {constructor(type, {detail}) { this.type = type; this.detail = detail; }},
    Event: class {constructor(type) { this.type = type; }},
    requestAnimationFrame: fn => fn(),
    setTimeout: (fn, ms) => { const id = ++nextTimer; timers.set(id, {fn, ms}); return id; }, clearTimeout: id => timers.delete(id),
  });
  for (const name of ['transport-workspace', 'intake-commands', 'chat-voice', 'patient-review', 'hospital-chat']) vm.runInContext(sources[name], context, {filename: name + '.js'});
  return {$, state, calls, document, window, speeches,
    type: text => { $('message').value = text; $('message').dispatchEvent({type: 'input'}); },
    submit: () => $('message-form').emit('submit'),
    poll: async () => { const entry = [...timers].find(([, timer]) => timer.ms === 3000); assert.ok(entry, 'scheduled poll'); timers.delete(entry[0]); entry[1].fn(); await settle(); },
    runTimer: ms => { const entry = [...timers].find(([, timer]) => timer.ms === ms); assert.ok(entry, ms + 'ms timer'); timers.delete(entry[0]); entry[1].fn(); },
    join: () => { state.session = {id: 7, hospital_id: 'H01', hospital_name: '가상 병원 H01'}; },
  };
}

test('auto-created private intake works even if the hospital list fails; no fabricated patient facts', async () => {
  const h = harness({hospitalFailure: true}); await settle();
  const creates = h.calls.filter(call => call.url === '/api/patients');
  assert.equal(creates.length, 1); assert.equal(creates[0].body.age, null); assert.equal(creates[0].body.symptoms, null);
  assert.equal(h.$('chat').hidden, false); assert.equal(h.$('gps').hidden, true); assert.equal(h.$('message').disabled, false);
  assert.deepEqual(h.$('participants').children.map(node => node.textContent), ['시스템', '구급대원']);
  h.type('60세 남성, 가상 환자 상태 보고'); await h.submit();
  const posts = h.calls.filter(call => call.url.endsWith('/messages'));
  assert.equal(posts.length, 1); assert.equal(posts[0].body.hospital_session_id, null);
  assert.equal(h.$('message').value, ''); assert.equal(h.state.messages.length, 2);
  assert.ok(!h.$('messages').children.some(node => node.id === 'transport-item-system-report:1'));
  assert.ok(h.$('messages').children.some(node => node.id === 'transport-item-message:1'));
});

test('reloading a pid restores the same conversation and never creates another case', async () => {
  const h = harness({restored: true}); await settle();
  assert.equal(h.calls.filter(call => call.url === '/api/patients').length, 0);
  assert.equal(h.$('new-case').hidden, true); assert.equal(h.$('message').disabled, false);
});

test('private draft requires explicit review when a confirmed hospital joins', async () => {
  const h = harness(); await settle(); h.type('병원 참여 전 작성한 초안'); h.join(); await h.poll();
  assert.equal(h.$('send').disabled, true); assert.equal(h.$('review-draft').hidden, false);
  await h.submit(); assert.equal(h.calls.filter(call => call.url.endsWith('/messages')).length, 0);
  assert.deepEqual(h.$('participants').children.map(node => node.textContent), ['시스템', '구급대원', '가상 병원 H01']);
  await h.$('review-draft').emit('click'); await h.submit();
  assert.equal(h.calls.filter(call => call.url.endsWith('/messages'))[0].body.hospital_session_id, 7);
});

test('a session change during send preflight does not send a private draft to the new hospital', async () => {
  const h = harness(); await settle(); h.type('비공개 초안'); h.state.beforeConversation = h.join;
  await h.submit();
  assert.equal(h.calls.filter(call => call.url.endsWith('/messages')).length, 0);
  assert.equal(h.$('message').value, '비공개 초안'); assert.equal(h.$('review-draft').hidden, false);
});

test('an ambiguous private send retries the same null scope and request id after hospital confirmation', async () => {
  const h = harness(); await settle(); h.type('저장 결과 확인이 필요한 보고'); h.state.failMessageOnce = true;
  await h.submit(); assert.equal(h.$('discard').hidden, false);
  h.join(); await h.poll(); await h.submit();
  const posts = h.calls.filter(call => call.url.endsWith('/messages'));
  assert.equal(posts.length, 2); assert.deepEqual(posts[0].body, posts[1].body);
  assert.equal(posts[1].body.hospital_session_id, null); assert.equal(h.state.receipts.size, 1);
  assert.equal(h.$('message').value, '');
});

test('voice preserves typed draft, ignores interim results, deduplicates finals and requires explicit send', async () => {
  const h = harness(); await settle(); h.type('직접 입력한 원문.');
  await h.$('voice-start').emit('click'); const speech = h.speeches[0];
  assert.equal(speech.lang, 'ko-KR'); assert.equal(h.$('message').disabled, true); assert.equal(h.$('send').disabled, true);
  speech.result('중간 결과', false); assert.equal(h.$('message').value, '직접 입력한 원문.');
  speech.result('확정 보고', true); speech.result('확정 보고', true);
  assert.equal(h.$('message').value, '직접 입력한 원문. 확정 보고');
  assert.equal(h.calls.filter(call => call.url.endsWith('/messages')).length, 0);
  await h.$('voice-stop').emit('click'); assert.equal(speech.stopped, true);
  speech.onend(); assert.equal(h.$('message').disabled, false); assert.equal(h.$('voice-interim').hidden, true);
  await h.submit(); assert.equal(h.calls.filter(call => call.url.endsWith('/messages')).length, 1);
});

test('hospital join aborts voice, keeps final draft in the old scope and discards late recognition', async () => {
  const h = harness(); await settle(); await h.$('voice-start').emit('click'); const speech = h.speeches[0];
  speech.result('참여 전 확정 보고', true); h.join(); await h.poll();
  assert.equal(speech.aborted, true); speech.result('늦게 도착한 결과', true, 1);
  assert.equal(h.$('message').value, '참여 전 확정 보고'); assert.equal(h.$('send').disabled, true);
  assert.equal(h.$('review-draft').hidden, false);
});

test('hiding the app or switching to GPS stops voice without adding provisional text', async () => {
  const h = harness(); await settle(); h.type('초안'); await h.$('voice-start').emit('click');
  const first = h.speeches[0]; first.result('미확정', false);
  h.document.hidden = true; await h.document.emit('visibilitychange');
  assert.equal(first.aborted, true); assert.equal(h.$('message').value, '초안');
  h.document.hidden = false; await h.document.emit('visibilitychange'); await settle();
  await h.$('voice-start').emit('click'); const second = h.speeches[1];
  await h.$('toggle').emit('click'); assert.equal(second.aborted, true); assert.equal(h.$('gps').hidden, false);
});

test('unsupported speech leaves direct typing and private message submission available', async () => {
  const h = harness({speech: false}); await settle();
  const help = h.$('voice-start');
  assert.equal(help.disabled, false); assert.notEqual(help['aria-disabled'], 'true');
  assert.equal(help['aria-label'], '받아쓰기 안내'); assert.equal(help['aria-controls'], h.$('voice-panel').id);
  assert.equal(help['aria-expanded'], 'false'); assert.match(h.$('voice-status').textContent, /직접 입력/);
  assert.equal(h.$('voice-panel').hidden, true);
  await h.$('voice-start').emit('click'); assert.equal(h.$('voice-panel').hidden, false); assert.equal(h.speeches.length, 0);
  assert.equal(help['aria-expanded'], 'true');
  await h.$('voice-start').emit('click'); assert.equal(h.$('voice-panel').hidden, true);
  assert.equal(help['aria-expanded'], 'false');
  assert.equal(h.$('message').disabled, false); h.type('직접 입력한 보고'); await h.submit();
  assert.equal(h.calls.filter(call => call.url.endsWith('/messages')).length, 1);
});

test('voice overflow preserves all final text but prevents sending an overlong report', async () => {
  const h = harness(); await settle(); h.type('가'.repeat(1999)); await h.$('voice-start').emit('click');
  const speech = h.speeches[0]; speech.result('확정', true);
  assert.equal(speech.aborted, true); assert.equal(h.$('message').value.length, 2002);
  await h.submit(); assert.equal(h.calls.filter(call => call.url.endsWith('/messages')).length, 0);
  assert.match(h.$('error').textContent, /2,000/);
});

test('contact requests use the confirmed destination and stay out of the shared conversation', async () => {
  const h = harness(); await settle();
  const {isHospitalCall, dialNumber} = h.window.Quick119Intake;
  for (const phrase of ['확정된 병원에게 전화해줘', '이송할 응급실에 전화 걸어줄래?', '받아준 병원 연락처 알려 주세요', '목적지 병원과 통화 연결해줘']) {
    assert.equal(isHospitalCall(phrase), true, phrase);
  }
  for (const phrase of ['병원에 전화하지 마', '보호자에게 전화해줘', '병원에서 전화했어', '병원에 전화해 달라고 했어']) {
    assert.equal(isHospitalCall(phrase), false, phrase);
  }
  assert.equal(dialNumber('000-0000-0100'), '');
  assert.equal(dialNumber('119'), '');
  assert.equal(dialNumber('02-1234-5678'), '0212345678');
  h.type('확정된 병원에게 전화해줘'); await h.submit();
  assert.match(h.$('hospital-contact').textContent, /아직 없습니다/);
  h.join(); await h.poll();
  assert.equal(h.$('hospital-contact').hidden, true);
  h.type('목적지에 전화 걸어 주세요'); await h.submit();
  assert.match(h.$('hospital-contact').textContent, /가상 병원 H01/);
  assert.equal(h.calls.filter(call => call.url.endsWith('/messages')).length, 0);
});

test('typing mode follows the focused composer and the keyboard height, with hysteresis', async () => {
  const h = harness({viewport: true}); await settle();
  const vv = h.window.visualViewport, root = h.$('workspace'), style = h.document.documentElement.style;
  vv.height = 450; await vv.emit('resize');
  assert.equal(root.dataset.kb, undefined, 'a short viewport alone is not typing');
  h.document.activeElement = h.$('message'); await h.$('message-form').emit('focusin');
  assert.equal(root.dataset.kb, 'open'); assert.equal(style.props['--tw-vvh'], '450px');
  vv.height = 620; await vv.emit('resize');
  assert.equal(root.dataset.kb, 'open', 'a partly closed keyboard keeps the mode (no flicker)');
  assert.equal(style.props['--tw-vvh'], '620px', 'use the current height even during hysteresis');
  vv.height = 735; await vv.emit('resize');
  assert.equal(root.dataset.kb, undefined); assert.equal(style.props['--tw-vvh'], undefined);
  vv.height = 450; await vv.emit('resize'); assert.equal(root.dataset.kb, 'open');
  h.document.activeElement = null; await h.$('message-form').emit('focusout');
  assert.equal(root.dataset.kb, 'open', 'focus leaving gets a short grace period');
  h.runTimer(150); assert.equal(root.dataset.kb, undefined);
});

test('typing tracks rotation with the keyboard still open and ignores pinch zoom', async () => {
  const h = harness({viewport: true}); await settle();
  const vv = h.window.visualViewport, style = h.document.documentElement.style;
  h.document.activeElement = h.$('message');
  vv.height = 440; await vv.emit('resize');
  Object.assign(h.window, {innerWidth: 740, innerHeight: 200}); vv.height = 200;
  await h.window.emit('resize');
  assert.equal(h.$('workspace').dataset.kb, 'open'); assert.equal(style.props['--tw-vvh'], '200px');
  vv.scale = 1.5; vv.height = 120; await vv.emit('resize');
  assert.equal(style.props['--tw-vvh'], '200px');
  vv.scale = 1; vv.height = 360; h.window.innerHeight = 360; await vv.emit('resize');
  assert.equal(h.$('workspace').dataset.kb, undefined); assert.equal(style.props['--tw-vvh'], undefined);
});

test('an oversized embedded chat scrolls only enough to show its composer in a panned visual viewport', async () => {
  const h = harness({viewport: true, embedded: true}); await settle();
  const vv = h.window.visualViewport, composer = h.$('message').closest('.tw-composer');
  h.document.activeElement = h.$('message'); h.window.scrollY = 650;
  h.$('workspace').getBoundingClientRect = () => ({top: 700 - h.window.scrollY, bottom: 1400 - h.window.scrollY, height: 700});
  composer.getBoundingClientRect = () => ({top: 1300 - h.window.scrollY, bottom: 1350 - h.window.scrollY});
  Object.assign(vv, {height: 450, offsetTop: 50}); await vv.emit('resize');
  assert.equal(h.window.scrollY, 850); // bottom = 500, the keyboard edge in layout coordinates
  await vv.emit('scroll'); assert.equal(h.window.scrollY, 850, 'already visible');
  vv.offsetTop = 470; await vv.emit('scroll'); assert.equal(h.window.scrollY, 830, 'show a composer panned above the visual top');
});

test('an embedded chat reveals both log and composer after the OS centered the old input', async () => {
  const h = harness({viewport: true, embedded: true}); await settle();
  const vv = h.window.visualViewport, workspace = h.$('workspace');
  const composer = h.$('message').closest('.tw-composer');
  workspace.getBoundingClientRect = () => ({top: 1100 - h.window.scrollY, bottom: 1100 - h.window.scrollY + vv.height, height: vv.height});
  composer.getBoundingClientRect = () => {
    const bottom = workspace.getBoundingClientRect().bottom - 8;
    return {top: bottom - 48, bottom};
  };
  h.document.activeElement = h.$('message'); h.window.scrollY = 1400;
  Object.assign(vv, {height: 450, offsetTop: 10}); await vv.emit('resize');
  assert.equal(workspace.getBoundingClientRect().top, 10); assert.equal(h.window.scrollY, 1090);
  h.window.scrollY += 30; await vv.emit('scroll');
  assert.equal(h.window.scrollY, 1120, 'ordinary scroll while editing is preserved');
  Object.assign(h.window, {innerWidth: 740, innerHeight: 220}); vv.height = 220; await vv.emit('resize');
  assert.equal(workspace.getBoundingClientRect().top, 10, 'align the newly rotated layout');
  assert.equal(composer.getBoundingClientRect().bottom, 222);
  vv.height = 245; await vv.emit('resize');
  assert.equal(h.window.scrollY, 1090, 'already visible at the new height');
});

test('confirmed and accepted inquiry chips lead DOM order while preserving order within each group', async () => {
  const h = harness(); await settle();
  h.state.inquiries = ['PENDING', 'ACCEPTED', 'CONFIRMED', 'ACCEPTED', 'DECLINED'].map((state, i) =>
    ({id: i + 1, hospital_id: 'H0' + (i + 1), state, usable: state === 'ACCEPTED'}));
  await h.poll();
  const rows = h.$('inquiries').children;
  assert.deepEqual(rows.map(row => row.children[1].children[0].textContent), ['H03', 'H02', 'H04', 'H01', 'H05']);
  assert.deepEqual(rows.flatMap(row => row.children.filter(node => node.dataset.transportConfirm).map(node => node.dataset.transportConfirm)), [2, 4]);
});

test('search status announces a changed reason once, including repeated draft edits', async () => {
  const h = harness(); await settle();
  const status = h.$('search-status');
  let current = status.textContent, writes = 0;
  Object.defineProperty(status, 'textContent', {get: () => current, set(value) { current = value; writes++; }});
  h.type('첫 글자'); h.type('첫 글자와 다음 글자');
  assert.equal(status.dataset.kind, 'blocked'); assert.match(current, /작성 중인 메시지/); assert.equal(writes, 1);
  h.type(''); assert.equal(status.dataset.kind, 'hint'); assert.equal(writes, 2);
});

test('a missed refresh blocks sending but keeps the draft editable', async () => {
  const h = harness(); await settle(); h.type('작성 중인 보고');
  h.state.beforeConversation = () => { h.state.beforeConversation = null; throw new Error('offline'); }; await h.poll();
  assert.equal(h.$('send').disabled, true); assert.equal(h.$('message').disabled, false);
  assert.equal(h.$('message').value, '작성 중인 보고');
  await h.poll(); assert.equal(h.$('send').disabled, false);
});
