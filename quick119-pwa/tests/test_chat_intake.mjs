// Synthetic DOM, HTTP and SpeechRecognition; executes the shipped scripts without a browser or microphone.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import vm from 'node:vm';
import test from 'node:test';

const sources = Object.fromEntries(['chat-voice', 'hospital-chat', 'transport-workspace', 'intake-commands', 'patient-review'].map(name =>
  [name, readFileSync(fileURLToPath(new URL('../static/' + name + '.js', import.meta.url)), 'utf8')]));
const settle = async () => { for (let i = 0; i < 12; i++) await new Promise(resolve => setImmediate(resolve)); };

function harness({restored = false, hospitalFailure = false, seoul = false, speech = true, tts = true, cloudSpeech = false,
  ttsVoices = [{name: '한국어', lang: 'ko-KR', localService: true, default: true}], audio = false, pcVoice = true,
  intake = false, intakeEffect = null, viewport = false, embedded = false, conversationPages = []} = {}) {
  const nodes = new Map(), allNodes = new Set(), calls = [], timers = new Map(), speeches = [], utterances = [], audios = [];
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
  const ids = ['workspace', 'chat', 'gps', 'sheet', 'sheet-handle', 'message', 'message-label', 'message-form', 'messages',
    'participants', 'depart', 'voice-start', 'voice-stop', 'voice-status', 'voice-interim', 'new-case', 'send',
    'discard', 'review-draft', 'pending', 'connection', 'chat-status',
    'hospitals', 'voice-panel', 'hospital-contact', 'hospital-status', 'reload-hospitals', 'inquiries', 'error', 'unread', 'toggle', 'toggle-label',
    'title', 'mark-read', 'review-panel', 'patient-info', 'start-search', 'review-close', 'review-close-top', 'review-form', 'search-status',
    'tts-toggle', 'tts-status', 'intake-feedback', 'answer', 'command-panel', 'command-close', 'command-confirm',
    'command-title', 'command-fields', 'command-reports', 'command-warnings'];
  for (const id of ids) { const node = new Element(); node.id = 'transport-' + id; }
  const $ = id => nodes.get('transport-' + id);
  Object.assign($('command-panel'), {open: false, showModal() { this.open = true; }, close() { this.open = false; }});
  $('gps').hidden = true;
  $('workspace').dataset.ttsCloud = cloudSpeech ? 'enabled' : 'disabled';
  const document = new Events(); document.hidden = false;
  document.getElementById = id => nodes.get(id) || null;
  document.createElement = () => new Element();
  document.querySelectorAll = () => [...allNodes].filter(node => node.dataset.transportConfirm || node.dataset.transportInquire);
  const window = new Events(); window.isSecureContext = true;
  class Utterance { constructor(text) { this.text = text; utterances.push(this); } }
  const synthesis = Object.assign(new Events(), {voices: ttsVoices, canceled: 0,
    getVoices() { return this.voices; }, speak() {},
    cancel() { this.canceled++; }});
  if (tts) { window.speechSynthesis = synthesis; window.SpeechSynthesisUtterance = Utterance; }
  class Player { constructor() { this.plays = []; this.paused = 0; audios.push(this); }
    play() { this.plays.push(this.src); return Promise.resolve(); }
    pause() { this.paused++; } }
  if (audio) window.Audio = Player;
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
  const welcome = {id: 'event:1', kind: 'system', type: 'patient_created', sender_role: 'system', sender_name: '시스템', at: 1,
    text: '환자 상태를 보고해 주세요.', order_key: [1, 0, 1]};
  function snapshot() {
    return {conversation: {state: 'OPEN'}, can_send: true, active_hospital_session: state.session,
      participants: [{id: 'system', role: 'system', name: '시스템'}, {id: 'crew', role: 'crew', name: '구급대'},
        ...(state.session ? [{id: 'hospital:H01', role: 'hospital', name: '가상 병원 H01', hospital_session_id: state.session.id}] : [])],
      inquiries: state.inquiries, ...(intake ? {intake_commands: true} : {}),
      items: [welcome, ...state.messages], cursors: {event_id: 1, message_id: state.receipts.size}, has_more: false};
  }
  async function fetch(url, options) {
    const body = options.body ? JSON.parse(options.body) : undefined;
    calls.push({url, body, method: options.method});
    if (url === '/static/seoul-demo-hospitals.json') return response({origin:{lat:37.5051,lon:126.9573}});
    if (url === '/api/hospitals') {
      if (seoul) return response([{id:'H01', name:'시연 대상 병원', source:'synthetic', source_ids:{identity_source:'seoul_public'}}]);
      if (hospitalFailure) throw new Error('hospital list offline');
      return response([{id: 'H01', name: '가상 병원 H01', source: 'synthetic', lat: 35.1, lon: 126.8}]);
    }
    if (url === '/api/patients') return response(body);
    if (url === '/api/transport-tts') {
      const available = Array.isArray(pcVoice) ? pcVoice.shift() : pcVoice;
      return available ? {ok: true, status: 200, blob: async () => new Blob(['RIFF'], {type: 'audio/wav'})} : {ok: false, status: 503};
    }
    if (url.endsWith('/intake-draft') && options.method === 'GET') {
      return response({draft: {id: 'draft-1', version: 1, fields: {}, field_metadata: {}, speak_text: ''}});
    }
    if (url.endsWith('/intake-draft') && options.method === 'POST') {
      return response({input_id: body.request_id, draft: {id: 'draft-1', version: 1, fields: {age: 10}, field_metadata: {},
        speak_text: intakeEffect ? '나이 10세. 입력 내용을 확인해 주세요.' : '', ...(intakeEffect ? {warnings: ['나이 수치를 원문과 확인해 주세요.']} : {})},
        hints: [], action: 'observe', feedback: '환자 상태를 확인해 주세요.', revision: ['draft-1', 1], ...(intakeEffect ? {effect: intakeEffect} : {}),
        system_items: [{id: 'event:9', kind: 'system', type: 'crew_intake_reply', sender_role: 'system', sender_name: '시스템',
          at: 5, text: '', data: {reply: '나이: 10세', command_text: body.text}, order_key: [5, 0, 9]}]});
    }
    if (url.includes('/conversation?')) { state.beforeConversation?.(); state.beforeConversation = null; return response({...snapshot(), ...conversationPages.shift()}); }
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
  return {$, state, calls, document, window, speeches, utterances, synthesis, audios,
    type: text => { $('message').value = text; $('message').dispatchEvent({type: 'input'}); },
    submit: () => $('message-form').emit('submit'),
    poll: async () => { const entry = [...timers].find(([, timer]) => timer.ms === 3000); assert.ok(entry, 'scheduled poll'); timers.delete(entry[0]); entry[1].fn(); await settle(); },
    runTimer: ms => { const entry = [...timers].find(([, timer]) => timer.ms === ms); assert.ok(entry, ms + 'ms timer'); timers.delete(entry[0]); entry[1].fn(); },
    join: () => { state.session = {id: 7, hospital_id: 'H01', hospital_name: '가상 병원 H01'}; },
  };
}

test('TTS selects a local Korean voice, reads only new system chat turns, and stays silent for history', async () => {
  const oldSystem = {id: 'event:2', kind: 'system', type: 'crew_intake_reply', sender_role: 'system',
    text: '', data: {reply: '기존 시스템 답변'}, at: 2, order_key: [2, 0, 2]};
  const h = harness({restored: true, conversationPages: [{items: [
    {id: 'event:1', kind: 'system', type: 'patient_created', sender_role: 'system', text: '기존 환영', at: 1, order_key: [1, 0, 1]},
    {id: 'message:1', kind: 'message', sender_role: 'patient', text: '기존 보고', at: 2, order_key: [2, 1, 1]},
    oldSystem,
  ]}],
    ttsVoices: [
      {name: '원격 기본 한국어', lang: 'ko-KR', localService: false, default: true},
      {name: '로컬 기본 영어', lang: 'en-US', localService: true, default: true},
      {name: '로컬 한국어', lang: 'ko-KR', localService: true, default: false},
      {name: '로컬 기본 한국어', lang: 'ko-KR', localService: true, default: true},
    ]});
  await h.$('tts-toggle').emit('click'); await settle();
  assert.equal(h.utterances.length, 0, 'history and an answered greeting are not read after enabling');
  const next = {id: 'event:3', kind: 'system', type: 'crew_intake_reply', sender_role: 'system',
    text: '', data: {reply: '나이: 10세', command_text: '나이 10세'}, at: 3, order_key: [3, 0, 3]};
  h.state.messages.push(next,
    {...next, id: 'system-report:2', type: 'intake_report_received', text: '현장 보고가 이송 기록에 저장됐습니다.'},
    {...next, id: 'event:4', type: 'patient_updated', text: '환자정보가 수정됐습니다.'},
    {...next, id: 'crew:1', kind: 'message', sender_role: 'patient', type: null, text: '구급대원 보고'},
    {...next, id: 'hospital:1', kind: 'message', sender_role: 'hospital', type: null, text: '병원 답장'},
    {...next, id: 'event:5', type: 'inquiry_status_changed', text: '상태 변경'},
    {...next, id: 'event:6', type: 'inquiry_workflow_batch', text: '후보 2곳에 문의했습니다.'},
    {...next, id: 'event:7', type: 'inquiry_responded', text: '가상 병원 H01에서 수용 가능으로 응답했습니다.'});
  await h.poll();
  assert.deepEqual(h.utterances.map(item => item.text), ['나이: 10세']);
  h.utterances[0].onend(); await settle();
  assert.deepEqual(h.utterances.map(item => item.text), ['나이: 10세', '환자정보가 수정됐습니다.']);
  h.utterances[1].onend(); await settle();
  assert.deepEqual(h.utterances.map(item => item.text), ['나이: 10세', '환자정보가 수정됐습니다.', '가상 병원 H01에서 수용 가능으로 응답했습니다.'],
    'hospital responses are read; workflow bookkeeping is not');
  assert.equal(h.utterances[0].voice.name, '로컬 기본 한국어');
  assert.equal(h.utterances[0].lang, 'ko-KR'); assert.equal(h.utterances[0].rate, 1.15);
});

test('TTS asks the opening question and reads the intake reply once, not the receipt or draft summary', async () => {
  const h = harness({intake: true}); await h.$('tts-toggle').emit('click'); await settle();
  assert.deepEqual(h.utterances.map(item => item.text), ['환자 상태를 알려주세요.']);
  h.type('나이 10세'); await h.submit(); await h.poll();
  assert.deepEqual(h.utterances.map(item => item.text), ['환자 상태를 알려주세요.', '나이: 10세']);
  const restored = harness({restored: true}); await settle();
  assert.equal(restored.utterances.length, 0);
  await restored.$('tts-toggle').emit('click'); await settle();
  assert.deepEqual(restored.utterances.map(item => item.text), ['환자 상태를 알려주세요.'], 'enabling before the first report asks the pending question');
});

test('the confirmation sheet lists every sent report in full instead of the spoken summary and warnings', async () => {
  const report = {id: 'message:1', message_id: 1, kind: 'message', sender_role: 'patient', at: 2, order_key: [2, 1, 1],
    text: '31세 남성 흉통\n혈압 150에 90, 맥박 110'};
  const h = harness({restored: true, intake: true, intakeEffect: 'explicit_submit_api_required', conversationPages: [{items: [report]}]});
  await settle();
  h.type('병원 찾아줘'); await h.submit(); await settle();
  assert.equal(h.$('command-panel').open, true);
  assert.equal(h.$('command-title').textContent, '이 정보로 병원을 탐색할까요?');
  const [block] = h.$('command-reports').children;
  assert.equal(h.$('command-reports').children.length, 1);
  assert.equal(block.children[1].textContent, report.text);
  assert.equal(h.$('command-warnings').textContent, '', 'extraction warnings and the spoken summary are not shown');
});

test('TTS plays PC-rendered Korean audio in order and falls back to the device voice when the PC voice is down', async () => {
  const h = harness({restored: true, audio: true}); await h.$('tts-toggle').emit('click'); await settle();
  const [player] = h.audios, spoken = () => h.calls.filter(call => call.url === '/api/transport-tts').map(call => call.body.text);
  const system = (n, text) => ({id: 'event:' + n, kind: 'system', type: 'patient_updated', sender_role: 'system',
    text, at: n, order_key: [n, 0, n]});
  assert.match(player.plays[0], /^data:audio\/wav/, 'the toggle tap unlocks later playback');
  h.state.messages.push(system(3, '첫 번째 안내'), system(4, '두 번째 안내')); await h.poll();
  assert.deepEqual(spoken(), ['첫 번째 안내'], 'the next message waits for the current audio');
  assert.match(player.plays[1], /^blob:/);
  player.onended(); await settle();
  assert.deepEqual(spoken(), ['첫 번째 안내', '두 번째 안내']);
  assert.equal(player.plays.length, 3); assert.equal(h.utterances.length, 0);
  await h.$('toggle').emit('click');
  assert.equal(player.paused, 1, 'map navigation stops PC audio');
  const down = harness({restored: true, audio: true, pcVoice: false});
  await down.$('tts-toggle').emit('click'); await settle();
  down.state.messages.push({id: 'event:3', kind: 'system', type: 'patient_updated', sender_role: 'system',
    text: '기기 음성 대체 안내', at: 3, order_key: [3, 0, 3]});
  await down.poll();
  assert.deepEqual(down.utterances.map(item => item.text), ['기기 음성 대체 안내']);
});

for (const pcVoice of [true, false]) {
  test(`inquiry registration and acceptance are spoken once in order with ${pcVoice ? 'PC audio' : 'device fallback'}`, async () => {
    const notice = (n, type, text) => ({id: 'event:' + n, kind: 'system', type, sender_role: 'system',
      text, at: n, order_key: [n, 0, n]});
    const oldSent = notice(2, 'inquiry_sent', '이전 수용 문의를 등록했습니다.');
    const oldCreated = notice(3, 'inquiry_created', '이전 병원에 수용을 문의했습니다.');
    const report = {id: 'message:1', message_id: 1, kind: 'message', sender_role: 'patient',
      text: '기존 보고', at: 1, order_key: [1, 1, 1]};
    const h = harness({restored: true, audio: true, pcVoice, conversationPages: [{items: [report, oldSent, oldCreated]}]});
    await settle(); await h.$('tts-toggle').emit('click'); await settle();
    const spoken = () => h.calls.filter(call => call.url === '/api/transport-tts').map(call => call.body.text);
    assert.deepEqual(spoken(), [], 'historical registrations are silent');
    const sent = notice(4, 'inquiry_sent', '병원에 수용 문의를 등록했습니다.');
    const created = notice(5, 'inquiry_created', '다른 병원에 수용을 문의했습니다.');
    const accepted = notice(6, 'inquiry_responded', '병원에서 수용 가능으로 응답했습니다.');
    h.state.messages.push(oldSent, oldCreated, sent, created, accepted);
    await h.poll(); await h.poll();
    assert.deepEqual(spoken(), [sent.text], 'overlapping polls do not restart the queue');
    const finish = async index => {
      if (pcVoice) h.audios[0].onended();
      else h.utterances[index].onend();
      await settle();
    };
    await finish(0);
    assert.deepEqual(spoken(), [sent.text, created.text]);
    await finish(1);
    assert.deepEqual(spoken(), [sent.text, created.text, accepted.text]);
    await finish(2); await h.poll();
    assert.deepEqual(spoken(), [sent.text, created.text, accepted.text], 'repeated polling does not replay notices');
    if (pcVoice) assert.equal(h.utterances.length, 0);
    else assert.deepEqual(h.utterances.map(item => item.text), [sent.text, created.text, accepted.text]);
  });
}

test('cloud-enabled TTS supports remote and OS Korean voices and reports browser errors', async () => {
  const online = harness({restored: true, cloudSpeech: true, ttsVoices: [
    {name: '온라인 한국어', lang: 'ko-KR', localService: false, default: true},
    {name: '로컬 영어', lang: 'en-US', localService: true, default: true},
  ]});
  await online.$('tts-toggle').emit('click'); await settle();
  online.state.messages.push({id: 'event:3', kind: 'system', type: 'patient_updated', sender_role: 'system',
    text: '온라인 음성 안내', at: 3, order_key: [3, 0, 3]});
  await online.poll();
  assert.equal(online.utterances[0].voice.name, '온라인 한국어');
  const noVoice = harness({restored: true, cloudSpeech: true, ttsVoices: [{name: '로컬 영어', lang: 'en-US', localService: true, default: true}]});
  await noVoice.$('tts-toggle').emit('click'); await settle();
  noVoice.state.messages.push({id: 'event:3', kind: 'system', type: 'patient_updated', sender_role: 'system',
    text: '기본 엔진 안내', at: 3, order_key: [3, 0, 3]});
  await noVoice.poll();
  assert.equal(noVoice.utterances.length, 1);
  assert.equal(noVoice.utterances[0].voice, undefined);
  assert.equal(noVoice.utterances[0].lang, 'ko-KR'); assert.equal(noVoice.utterances[0].rate, 1.15);
  noVoice.utterances[0].onerror({error: 'language-unavailable'});
  assert.match(noVoice.$('tts-status').textContent, /한국어 음성을 설치/);
  const unsupported = harness({restored: true, tts: false});
  await unsupported.$('tts-toggle').emit('click'); await settle();
  unsupported.state.messages.push({id: 'event:3', kind: 'system', type: 'patient_updated', sender_role: 'system',
    text: '미지원 안내', at: 3, order_key: [3, 0, 3]});
  await unsupported.poll();
  assert.match(unsupported.$('tts-status').textContent, /지원하지 않습니다/);
  const blocked = harness({restored: true});
  await blocked.$('tts-toggle').emit('click'); await settle();
  blocked.state.messages.push({id: 'event:3', kind: 'system', type: 'patient_updated', sender_role: 'system',
    text: '자동재생 차단 안내', at: 3, order_key: [3, 0, 3]});
  await blocked.poll();
  blocked.utterances[0].onerror({error: 'not-allowed'});
  assert.equal(blocked.$('tts-toggle')['aria-pressed'], 'false');
  assert.match(blocked.$('tts-status').textContent, /다시 눌러 활성화/);
});

test('offline TTS skips remote and unknown Korean voices even after PC audio fails', async () => {
  const voices = [{lang: 'ko-KR', localService: false}, {lang: 'ko-KR'}, {lang: 'en-US', localService: true}];
  const h = harness({restored: true, ttsVoices: voices, audio: true, pcVoice: false});
  await settle(); await h.$('tts-toggle').emit('click'); await settle();
  assert.equal(h.calls.filter(call => call.url === '/api/transport-tts').length, 1);
  assert.equal(h.utterances.length, 0);
  assert.match(h.$('tts-status').textContent, /한국어 음성을 설치/);
  h.synthesis.voices = [{name: '설치된 한국어', lang: 'ko-KR', localService: true}];
  await h.synthesis.emit('voiceschanged');
  assert.match(h.$('tts-status').textContent, /한국어 음성을 사용할 수 있습니다/);
  h.state.messages.push({id: 'event:7', kind: 'system', type: 'patient_updated', sender_role: 'system',
    text: '새 시스템 안내', at: 7, order_key: [7, 0, 7]});
  await h.poll();
  assert.equal(h.utterances[0].voice.name, '설치된 한국어');
});

test('PC playback waits for device fallback and cancel discards queued messages and stale callbacks', async () => {
  const h = harness({restored: true, audio: true, pcVoice: [false, true, false, true]});
  await h.$('tts-toggle').emit('click'); await settle();
  const system = n => ({id: 'event:' + n, kind: 'system', type: 'patient_updated', sender_role: 'system',
    text: '안내 ' + n, at: n, order_key: [n, 0, n]});
  const spoken = () => h.calls.filter(call => call.url === '/api/transport-tts').map(call => call.body.text);
  h.state.messages.push(system(2), system(3)); await h.poll();
  assert.deepEqual(spoken(), ['안내 2']);
  assert.deepEqual(h.utterances.map(u => u.text), ['안내 2']);
  h.utterances[0].onend(); await settle();
  assert.deepEqual(spoken(), ['안내 2', '안내 3']);
  h.audios[0].onended(); await settle();
  h.state.messages.push(system(4), system(5)); await h.poll();
  const lateError = h.utterances[1].onerror;
  await h.$('toggle').emit('click'); await settle();
  assert.equal(h.synthesis.canceled, 1);
  lateError({error: 'not-allowed'});
  assert.equal(h.$('tts-toggle')['aria-pressed'], 'true');
  await h.$('toggle').emit('click');
  h.state.messages.push(system(6)); await h.poll();
  assert.deepEqual(spoken(), ['안내 2', '안내 3', '안내 4', '안내 6']);
});

test('TTS stops for map navigation, microphone input, and app suspension', async () => {
  const h = harness(); await h.$('tts-toggle').emit('click'); await settle();
  const event = suffix => ({id: 'event:' + suffix, kind: 'system', type: 'patient_updated', sender_role: 'system',
    text: '중단 대상 안내 ' + suffix, at: Number(suffix), order_key: [Number(suffix), 0, Number(suffix)]});
  h.state.messages.push(event('2')); await h.poll();
  await h.$('toggle').emit('click');
  assert.equal(h.synthesis.canceled, 1);
  await h.$('toggle').emit('click');
  h.state.messages.push(event('3')); await h.poll();
  await h.document.emit('quick119:voice-state', {listening: true});
  assert.equal(h.synthesis.canceled, 2);
  await h.document.emit('quick119:voice-state', {listening: false});
  h.state.messages.push(event('4')); await h.poll();
  h.document.hidden = true; await h.document.emit('visibilitychange');
  assert.equal(h.synthesis.canceled, 3);
});

test('auto-created private intake works even if the hospital list fails; no fabricated patient facts', async () => {
  const h = harness({hospitalFailure: true}); await settle();
  const creates = h.calls.filter(call => call.url === '/api/patients');
  assert.equal(creates.length, 1); assert.equal(creates[0].body.age, null); assert.equal(creates[0].body.symptoms, null);
  assert.equal(h.$('chat').hidden, false); assert.equal(h.$('gps').hidden, true); assert.equal(h.$('message').disabled, false);
  assert.deepEqual(h.$('participants').children.map(node => node.textContent), ['시스템', '구급대원']);
  const greeting = h.$('messages').children[0];
  assert.equal(h.$('messages').children.length, 1);
  assert.equal(greeting.dataset.role, 'system');
  assert.equal(greeting.children[1].textContent, '환자 상태를 알려주세요.');
  await h.poll();
  assert.deepEqual(h.$('messages').children.map(node => node.id), [greeting.id]);
  h.type('60세 남성, 가상 환자 상태 보고'); await h.submit();
  const posts = h.calls.filter(call => call.url.endsWith('/messages'));
  assert.equal(posts.length, 1); assert.equal(posts[0].body.hospital_session_id, null);
  assert.equal(h.$('message').value, ''); assert.equal(h.state.messages.length, 2);
  assert.ok(!h.$('messages').children.some(node => node.id === 'transport-item-system-report:1'));
  assert.ok(h.$('messages').children.some(node => node.id === 'transport-item-message:1'));
});

test('a Seoul public identity selects the local Seoul demo origin', async () => {
  const h = harness({seoul:true}); await settle();
  const created = h.calls.find(call => call.url === '/api/patients').body;
  assert.deepEqual([created.lat, created.lon], [37.5051, 126.9573]);
});

test('reloading a pid restores one short welcome before notices from the next history page', async () => {
  const notice = {id: 'event:2', kind: 'system', type: 'inquiry_created', sender_role: 'system',
    text: '병원에 수용을 문의했습니다.', at: 2, order_key: [2, 0, 2]};
  const h = harness({restored: true, conversationPages: [
    {has_more: true}, {items: [notice], cursors: {event_id: 2, message_id: 0}},
  ]});
  await settle();
  assert.ok(h.calls.some(call => call.url.includes('event_cursor=1&')));
  assert.deepEqual(h.$('messages').children.map(node => node.id), ['transport-item-intake-welcome', 'transport-item-event:2']);
  const [greeting, message] = h.$('messages').children;
  assert.equal(greeting.children[1].textContent, '환자 상태를 알려주세요.');
  assert.equal(message.dataset.kind, 'notice');
  assert.equal(message.children[1].textContent, notice.text);
  assert.equal(h.calls.filter(call => call.url === '/api/patients').length, 0);
  assert.equal(h.$('new-case').hidden, true); assert.equal(h.$('message').disabled, false);
});

test('legacy open intake without a creation event receives a single welcome, but a joined room does not gain one', async () => {
  const h = harness({restored: true, conversationPages: [{items: []}, {items: []}]});
  await settle(); await h.poll();
  assert.deepEqual(h.$('messages').children.map(node => node.id), ['transport-item-intake-welcome']);
  assert.equal(h.$('messages').children[0].children[2].textContent, '');
  const joined = harness({restored: true, conversationPages: [{items: [], active_hospital_session: {id: 7, hospital_id: 'H01'}}]});
  await settle();
  assert.equal(joined.$('messages').children.length, 0);
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
