// Synthetic DOM, HTTP and SpeechRecognition; executes the shipped scripts without a browser or microphone.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import vm from 'node:vm';
import test from 'node:test';

const sources = Object.fromEntries(['chat-voice', 'hospital-chat', 'transport-workspace'].map(name =>
  [name, readFileSync(fileURLToPath(new URL('../static/' + name + '.js', import.meta.url)), 'utf8')]));
const settle = async () => { for (let i = 0; i < 12; i++) await new Promise(resolve => setImmediate(resolve)); };

function harness({restored = false, hospitalFailure = false, speech = true} = {}) {
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
    'hospitals', 'hospital-status', 'reload-hospitals', 'inquiries', 'error', 'unread', 'toggle', 'toggle-label',
    'title', 'mark-read'];
  for (const id of ids) { const node = new Element(); node.id = 'transport-' + id; }
  const $ = id => nodes.get('transport-' + id);
  $('gps').hidden = true;
  const document = new Events(); document.hidden = false;
  document.getElementById = id => nodes.get(id) || null;
  document.createElement = () => new Element();
  document.querySelectorAll = () => [...allNodes].filter(node => node.dataset.transportConfirm || node.dataset.transportInquire);
  const window = new Events(); window.isSecureContext = true;
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
  const state = {session: null, failMessageOnce: false, beforeConversation: null, messages: [], receipts: new Map()};
  const patientId = 'Q119-TEST-restored';
  const welcome = {id: 'event:1', kind: 'system', sender_role: 'system', sender_name: '시스템', at: 1,
    text: '환자 상태를 보고해 주세요.', order_key: [1, 0, 1]};
  function snapshot() {
    return {conversation: {state: 'OPEN'}, can_send: true, active_hospital_session: state.session,
      participants: [{id: 'system', role: 'system', name: '시스템'}, {id: 'crew', role: 'crew', name: '구급대'},
        ...(state.session ? [{id: 'hospital:H01', role: 'hospital', name: '가상 병원 H01', hospital_session_id: state.session.id}] : [])],
      inquiries: [], items: [welcome, ...state.messages], cursors: {event_id: 1, message_id: state.receipts.size}, has_more: false};
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
        const system = {...message, id: 'system-report:' + id, kind: 'system', sender_role: 'system', sender_name: '시스템',
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
  for (const name of ['transport-workspace', 'chat-voice', 'hospital-chat']) vm.runInContext(sources[name], context, {filename: name + '.js'});
  return {$, state, calls, document, window, speeches,
    type: text => { $('message').value = text; $('message').dispatchEvent({type: 'input'}); },
    submit: () => $('message-form').emit('submit'),
    poll: async () => { const entry = [...timers].find(([, timer]) => timer.ms === 3000); assert.ok(entry, 'scheduled poll'); timers.delete(entry[0]); entry[1].fn(); await settle(); },
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
  assert.ok(h.$('messages').children.some(node => node.id === 'transport-item-system-report:1'));
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
  assert.equal(h.$('voice-start').disabled, true); assert.match(h.$('voice-status').textContent, /직접 입력/);
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
