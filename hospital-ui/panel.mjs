// 병원 채팅 패널의 화면과 통신. 기존 병원 웹과 같은 API만 쓰고 새 서버 기능은 만들지 않는다.
// - 확정 전: 문의 한 건의 요약 카드, 회신 버튼(수용 가능·확인 중·수용 불가), 추가 질문
// - 확정 후: 이송 대화, 수용 유지·수용 취소 요청, 환자 도착 확인
import { buildThreads, threadStatus, summaryFields, severityLabel, patientTitle, acceptBlock, inquiryLog, chatLog,
  actionCount, needsReview, etaLabel, DECLINE_REASONS } from './panel-state.mjs';
import { ChatRecoveryStore, newChatRequest, confirmsChatRequest, isRejected } from './chat-recovery.mjs';

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const base = location.origin;
const POLL_MS = 3000;
const VOLATILE = new Set(['eta_remaining_seconds', 'elapsed_seconds', 'age_seconds', 'last_checked_at', 'checked_at', 'updated_at']);

function stored(key) { try { return localStorage.getItem(key); } catch { return null; } }
function store(key, value) { try { localStorage.setItem(key, value); } catch { /* 저장소가 막혀 있으면 이번 화면에서만 유지 */ } }
let recovery = null;
try { recovery = new ChatRecoveryStore({ storage: localStorage }); } catch { recovery = null; }

let hid = params.get('hid') || 'H01';
let context = 0;                       // 병원을 바꾸면 늘려서 늦게 온 응답을 버린다
let inquiries = { open: [], recent: [] }, incoming = [], hospital = null;
let threads = [], activeKey = null, clockOffset = 0, polling = false, busy = false, loaded = false;
let ui = { declineOpen: false, reason: null, cancelOpen: false };
let chat = null, chatPending = null, chatLoading = false;
let known = null;                      // 이미 본 새 문의 id (첫 조회는 기준선)
let sound = null, soundOn = false, lastBeep = 0, errorTimer = null;
let sig = { threads: '', log: '', actions: '' };

const now = () => Date.now() / 1000 + clockOffset;
const clock = (at) => new Date(at * 1000).toLocaleTimeString('ko-KR', { hour: 'numeric', minute: '2-digit' });
const handler = () => $('handler').value.trim();

function h(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value == null || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key === 'tone') node.dataset.tone = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? '' : value);
  }
  node.append(...children.filter((child) => child != null));
  return node;
}

async function api(path, options = {}) {
  const response = await fetch(base + path, { ...options, headers: { 'Content-Type': 'application/json' } });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(body.message || body.detail || `오류 ${response.status}`);
    error.body = body; error.status = response.status; throw error;
  }
  return body;
}
const post = (path, body) => api(path, { method: 'POST', body: JSON.stringify(body) });

function showError(text) {
  clearTimeout(errorTimer);
  $('error').textContent = text || ''; $('error').hidden = !text;
  if (text) errorTimer = setTimeout(() => { $('error').hidden = true; }, 8000);
}
function setConn(state, text) { $('panel').dataset.conn = state; $('conn').textContent = text; }

function requireHandler() {
  if (handler()) return true;
  openSettings(true); $('handler').focus();
  showError('회신에 남길 담당자 이름을 먼저 입력하세요.');
  return false;
}
function openSettings(open) {
  $('settings').hidden = !open; $('settingsToggle').setAttribute('aria-expanded', String(open));
}

const active = () => threads.find((t) => t.key === activeKey) || null;

// ---------------------------------------------------------------- 조회
async function poll() {
  if (polling || busy) return;
  polling = true;
  const version = context, hospitalId = hid;
  try {
    const path = `/api/hospitals/${encodeURIComponent(hospitalId)}`;
    const [nextInquiries, nextIncoming, nextHospital] = await Promise.all([api(path + '/inquiries'), api(path + '/incoming'), api(path)]);
    if (version !== context) return;
    inquiries = nextInquiries; incoming = nextIncoming.incoming || []; hospital = nextHospital; loaded = true;
    // 서버 시계 기준으로 경과·도착 시간을 계산한다(PC 시계가 달라도 맞도록).
    const anchor = inquiries.open.find((q) => q.created_at != null && q.elapsed_seconds != null);
    const eta = incoming.find((p) => p.eta_at != null && p.eta_remaining_seconds > 0);
    if (anchor) clockOffset = anchor.created_at + anchor.elapsed_seconds - Date.now() / 1000;
    else if (eta) clockOffset = eta.eta_at - eta.eta_remaining_seconds - Date.now() / 1000;
    setConn('live', '연결됨');
    rebuild();
    alertNew();
    if (active()?.stage === 'confirmed' || active()?.stage === 'arrived') await pollChat();
  } catch (error) {
    if (version !== context) return;
    setConn('error', '연결 끊김');
    if (!loaded) showError(error.status ? error.message : '서버에 연결할 수 없습니다. 서버가 켜져 있는지 확인하세요.');
  } finally { polling = false; }
}

function rebuild() {
  threads = buildThreads({ open: inquiries.open, recent: inquiries.recent, incoming }, now());
  if (!active()) { activeKey = threads[0]?.key ?? null; resetThreadUi(); }
  $('who').textContent = `${hospital?.name || hid} · ${handler() || '담당자 미입력'}`;
  render();
  const count = actionCount(threads, now());
  document.title = (count ? `(${count}) ` : '') + '퀵119 수용 문의';
  // 이 패널을 iframe으로 품은 화면이 배지를 달 수 있도록 건수만 알린다(환자 정보는 보내지 않는다).
  if (window.parent !== window) window.parent.postMessage({ source: 'quick119-hospital-panel', pending: count }, '*');
}

function resetThreadUi() {
  ui = { declineOpen: false, reason: null, cancelOpen: false };
  chat = null; chatPending = null; $('message').value = '';
  sig.log = sig.actions = '';
}

async function pollChat() {
  const thread = active();
  if (!thread || chatLoading) return;
  const key = thread.key, pid = thread.patientId, version = context;
  chatLoading = true;
  if (!chat || chat.key !== key) {
    chat = { key, items: new Map(), eventCursor: 0, messageCursor: 0, canSend: false, session: null };
    chatPending = recovery?.load({ server: base, hospital: hid, patient: pid })?.request || null;
  }
  try {
    for (let page = 0; page < 20; page++) {
      const result = await api(`/api/hospitals/${encodeURIComponent(hid)}/patients/${encodeURIComponent(pid)}/conversation?event_cursor=${chat.eventCursor}&message_cursor=${chat.messageCursor}&limit=100`);
      if (version !== context || activeKey !== key) return;
      for (const item of result.items) chat.items.set(item.id, item);
      chat.eventCursor = result.cursors.event_id; chat.messageCursor = result.cursors.message_id;
      chat.canSend = !!result.can_send; chat.session = result.active_hospital_session?.id ?? null;
      if (!result.has_more) break;
    }
  } catch (error) {
    if (version === context && activeKey === key) chat.canSend = false;
  } finally { chatLoading = false; }
  if (version === context && activeKey === key) render();
}

// ---------------------------------------------------------------- 그리기
function render() { renderThreads(); renderLog(); renderActions(); }

function renderThreads() {
  const rows = threads.map((t) => ({ key: t.key, title: patientTitle(t.patient), ...threadStatus(t, now()), on: t.key === activeKey }));
  const next = JSON.stringify(rows);
  if (next === sig.threads) return;
  sig.threads = next;
  $('threads').replaceChildren(...rows.map((row) => h('button', { type: 'button', class: 'qp-thread', 'aria-pressed': String(row.on),
    'data-thread': row.key, onclick: () => { if (busy || row.key === activeKey) return; activeKey = row.key; resetThreadUi(); sig.threads = ''; render(); void pollNow(); } },
  h('strong', { text: row.title }), h('span', { text: row.text, tone: row.tone }))));
}

function card(thread) {
  const p = thread.patient, q = thread.inquiry;
  const source = thread.stage === 'confirmed' || thread.stage === 'arrived' ? '우리 병원으로 확정된 환자'
    : `수용 문의${q?.created_at ? ' · ' + clock(q.created_at) : ''}`;
  const fields = summaryFields(p, hospital).map((f) => h('div', { class: 'qp-field', tone: f.tone }, h('dt', { text: f.label }), h('dd', { text: f.value })));
  return h('article', { class: 'qp-card' }, h('div', { class: 'qp-card-src', text: source }),
    h('div', { class: 'qp-card-head' }, h('strong', { text: patientTitle(p) }), h('span', { text: severityLabel(p) })),
    h('dl', { class: 'qp-fields' }, ...fields));
}

function renderLog() {
  const thread = active();
  if (!thread) {
    const next = 'empty:' + loaded;
    if (next === sig.log) return;
    sig.log = next;
    $('banner').hidden = true;
    $('logInner').replaceChildren(h('div', { class: 'qp-empty' }, h('strong', { text: loaded ? '대기 중인 문의가 없습니다' : '불러오는 중' }),
      loaded ? '새 수용 문의가 오면 여기에 표시됩니다.' : ''));
    return;
  }
  const entries = thread.stage === 'confirmed' || thread.stage === 'arrived' ? chatLog(chat?.key === thread.key ? [...chat.items.values()] : [])
    : inquiryLog(thread.inquiry);
  const status = threadStatus(thread, now());
  const banner = thread.stage === 'confirmed' ? { text: status.text, tone: status.tone === 'ok' ? null : status.tone } : null;
  // 매 조회마다 바뀌는 경과 시간 값은 빼고 비교해, 읽는 중에 대화가 다시 그려지지 않게 한다.
  const next = JSON.stringify([thread.key, thread.stage, thread.patient, hospital?.resources, entries, banner],
    (key, value) => (VOLATILE.has(key) ? undefined : value));
  if (next === sig.log) return;
  sig.log = next;
  $('banner').hidden = !banner;
  if (banner) { $('banner').textContent = banner.text; if (banner.tone) $('banner').dataset.tone = banner.tone; else delete $('banner').dataset.tone; }
  $('logInner').replaceChildren(card(thread), ...entries.map((entry) => entry.kind === 'notice'
    ? h('p', { class: 'qp-notice', text: entry.text })
    : h('div', { class: 'qp-msg', 'data-kind': entry.kind }, h('p', { text: entry.text }),
      entry.at ? h('small', { text: (entry.kind === 'crew' && entry.sender ? entry.sender + ' · ' : '') + clock(entry.at) }) : null)));
}

function button(label, cls, onclick, extra = {}) { return h('button', { type: 'button', class: 'qp-btn ' + cls, text: label, onclick, disabled: busy, ...extra }); }

function renderActions() {
  const thread = active();
  const box = $('actions'), composer = $('composer');
  if (!thread) { box.replaceChildren(); composer.hidden = true; sig.actions = ''; return; }
  const p = thread.patient, q = thread.inquiry, stage = thread.stage;
  const blocked = stage === 'new' || stage === 'checking' ? acceptBlock(p, hospital, now()) : null;
  const canChat = stage === 'confirmed' && chat?.key === thread.key && (chat.canSend || !!chatPending);
  const next = JSON.stringify([thread.key, stage, q?.version, ui, busy, blocked, canChat, !!chatPending, p.needs_adjustment, needsReview(p), p.state, etaLabel(p, now())]);
  if (next === sig.actions) return;
  sig.actions = next;
  const nodes = [];
  if (stage === 'new' || stage === 'checking') {
    if (ui.declineOpen) {
      nodes.push(h('div', { class: 'qp-form' }, h('h2', { text: '수용 불가 사유' }),
        h('div', { class: 'qp-reasons' }, ...DECLINE_REASONS.map((reason) => h('button', { type: 'button', class: 'qp-chip', text: reason,
          'aria-pressed': String(ui.reason === reason), disabled: busy, onclick: () => { ui.reason = reason; renderActions(); } }))),
        h('div', { class: 'qp-row' }, button('취소', 'qp-btn-plain', () => { ui.declineOpen = false; ui.reason = null; renderActions(); }),
          button(ui.reason ? `수용 불가 · ${ui.reason} 보내기` : '사유를 고르세요', 'qp-btn-danger qp-grow', () => respond('decline', { reason: ui.reason }),
            { disabled: busy || !ui.reason, id: 'declineSend' }))));
    } else {
      nodes.push(h('div', { class: 'qp-reply' },
        button('수용 가능', 'qp-btn-accept', () => respond('accept'), { disabled: busy || !!blocked, 'data-action': 'accept' }),
        button('확인 중', '', () => respond('checking'), { 'data-action': 'checking' }),
        button('수용 불가', 'qp-btn-decline', () => { ui.declineOpen = true; ui.reason = null; renderActions(); }, { 'data-action': 'decline' })));
      if (blocked) nodes.push(h('p', { class: 'qp-note', tone: 'alert', text: blocked + ' 제한이나 자원 정보는 전체 병원 웹에서 바꿉니다.' }));
    }
  } else if (stage === 'accepted') {
    nodes.push(h('div', { class: 'qp-done', tone: 'wait', role: 'status', text: '수용 가능으로 회신했습니다 · 구급대의 목적지 확정을 기다리는 중' }));
  } else if (stage === 'confirmed') {
    if (ui.cancelOpen) {
      nodes.push(h('div', { class: 'qp-form' }, h('h2', { text: '수용 취소 요청 사유' }),
        h('label', { class: 'qp-sr', for: 'cancelNote', text: '수용 취소 요청 사유' }),
        h('input', { id: 'cancelNote', type: 'text', maxlength: '1000', autocomplete: 'off', placeholder: '예: 중환자실 병상이 방금 찼습니다' }),
        h('div', { class: 'qp-row' }, button('취소', 'qp-btn-plain', () => { ui.cancelOpen = false; renderActions(); }),
          button('수용 취소 요청 보내기', 'qp-btn-danger qp-grow', () => decide('adjust', $('cancelNote').value.trim())))));
    } else {
      if (needsReview(p)) {
        nodes.push(h('p', { class: 'qp-note', tone: 'alert', text: '이송 중 환자 상태나 조건이 바뀌었습니다. 그대로 받을 수 있는지 확인해 주세요.' }));
        nodes.push(h('div', { class: 'qp-row' }, button('수용 유지', 'qp-btn-primary qp-grow', () => decide('keep', ''), { 'data-action': 'keep' }),
          button('수용 취소 요청', 'qp-btn-decline', () => { ui.cancelOpen = true; renderActions(); }, { 'data-action': 'adjust' })));
      } else {
        nodes.push(h('div', { class: 'qp-row' }, button('환자 도착 확인', 'qp-btn-primary qp-grow', () => patientAction('arrive'), { 'data-action': 'arrive' }),
          p.needs_adjustment ? null : button('수용 취소 요청', 'qp-btn-decline', () => { ui.cancelOpen = true; renderActions(); }, { 'data-action': 'adjust' })));
      }
      if (p.needs_adjustment) nodes.push(h('p', { class: 'qp-note', text: '수용 취소를 요청했습니다. 구급대가 새 목적지를 확정할 때까지 목적지는 우리 병원으로 유지됩니다.' }));
    }
  } else {
    const text = stage === 'arrived' ? '환자 도착을 확인했습니다 · 퇴실 처리는 전체 병원 웹에서 합니다'
      : stage === 'declined' ? '수용 불가로 회신했습니다 · 문의 종료' : threadStatus(thread, now()).text;
    nodes.push(h('div', { class: 'qp-done', role: 'status', text }));
  }
  box.replaceChildren(...nodes);
  if (ui.cancelOpen) $('cancelNote')?.focus();
  const asking = (stage === 'new' || stage === 'checking') && !ui.declineOpen;
  composer.hidden = !(asking || (canChat && !ui.cancelOpen));
  $('message').placeholder = asking ? '구급대에 추가 질문' : '구급대에 전달할 내용';
  $('messageLabel').textContent = asking ? '구급대에 보낼 추가 질문' : '구급대에 전달할 내용';
  $('message').readOnly = !!chatPending && !asking;
  if (chatPending && !asking) $('message').value = chatPending.body;
  $('send').textContent = chatPending && !asking ? '재시도' : '보내기';
  $('send').disabled = busy; $('message').disabled = busy;
}

// ---------------------------------------------------------------- 동작
async function run(task) {
  if (busy) return;
  busy = true; sig.actions = ''; renderActions();
  try { await task(); showError(''); }
  catch (error) {
    let text = error.message || '요청을 처리하지 못했습니다.';
    if (error.body?.error === 'CONFLICT' || error.status === 409) text = '다른 담당자의 처리나 상태 변경이 먼저 반영됐습니다. 최신 상태를 확인하세요. ' + text;
    if (error.body?.shortfalls?.length) text += ' 부족: ' + error.body.shortfalls.map((s) => s.resource_type).join(', ');
    showError(text);
  } finally { busy = false; sig.actions = ''; await pollNow(); renderActions(); }
}
async function pollNow() { polling = false; await poll(); }

function respond(action, extra = {}) {
  const q = active()?.inquiry;
  if (!q || !requireHandler()) return;
  return run(async () => {
    const body = { action, handled_by: handler(), ...extra };
    if (action === 'accept' || action === 'decline') body.expected_version = q.version;
    await post(`/api/inquiries/${q.id}/respond`, body);
    ui.declineOpen = false; ui.reason = null;
    if (action === 'question') $('message').value = '';
  });
}

function patientAction(action) {
  const thread = active();
  if (!thread || !requireHandler()) return;
  return run(() => post(`/api/hospitals/${encodeURIComponent(hid)}/patients/${encodeURIComponent(thread.patientId)}/${action}`, { handled_by: handler() }));
}

// 수용 유지·취소 요청은 화면에 보인 검토 버전 그대로일 때만 보낸다(기존 병원 웹과 같은 규칙).
function decide(decision, note) {
  const thread = active();
  if (!thread || !requireHandler()) return;
  if (decision === 'adjust' && !note) { showError('수용 취소 요청 사유를 입력하세요.'); return; }
  return run(async () => {
    const pid = thread.patientId, shown = thread.patient.transport_review || {};
    const fresh = (await api(`/api/patients/${encodeURIComponent(pid)}/transport-review`)).review;
    if (fresh.enabled !== !!shown.enabled || (fresh.enabled && (fresh.version !== shown.version || fresh.risk_signature !== shown.risk_signature))) {
      throw new Error('수용 조건이 바뀌었습니다. 갱신된 내용을 확인한 뒤 다시 판단하세요.');
    }
    const payload = { handled_by: handler(), decision, note };
    if (fresh.enabled) Object.assign(payload, { expected_review_version: fresh.version, expected_risk_signature: fresh.risk_signature });
    await post(`/api/hospitals/${encodeURIComponent(hid)}/patients/${encodeURIComponent(pid)}/decision`, payload);
    ui.cancelOpen = false;
  });
}

// 이송 대화 전송. 결과를 확인하지 못한 요청은 보관했다가 같은 요청으로만 다시 보낸다.
function sendChat() {
  const thread = active();
  if (!thread || !chat || chat.key !== thread.key) return;
  const retry = chatPending, text = $('message').value.trim();
  if (!retry && (!text || !chat.canSend || !requireHandler())) return;
  const scope = { server: base, hospital: hid, patient: thread.patientId };
  return run(async () => {
    let request = retry;
    if (!request) {
      request = newChatRequest({ body: text, session: chat.session, senderName: handler() });
      if (recovery) recovery.save(scope, request);
      chatPending = request;
    }
    try {
      const result = await post(`/api/hospitals/${encodeURIComponent(hid)}/patients/${encodeURIComponent(thread.patientId)}/messages`, request);
      if (!confirmsChatRequest(result, request)) throw new Error('서버 응답에서 메시지 저장을 확인하지 못했습니다. 재시도를 눌러 주세요.');
      recovery?.clear(scope, request.client_message_id);
      chatPending = null; chat.items.set(result.message.id, result.message); $('message').value = '';
    } catch (error) {
      if (isRejected(error)) { recovery?.clear(scope, request.client_message_id); chatPending = null; }
      else error.message = '전송 결과를 확인하지 못했습니다. 재시도를 누르면 같은 메시지를 다시 보냅니다.';
      throw error;
    } finally { sig.log = ''; renderLog(); }
  });
}

// ---------------------------------------------------------------- 알림
function alertNew() {
  const fresh = inquiries.open.filter((q) => q.state === 'PENDING');
  const ids = new Set(fresh.map((q) => q.id));
  const added = known === null ? [] : fresh.filter((q) => !known.has(q.id));
  known = ids;
  if (added.length && 'Notification' in window && Notification.permission === 'granted' && (document.hidden || !document.hasFocus())) {
    const q = added[0];
    try {
      const note = new Notification('새 수용 문의', { body: patientTitle(q.patient), tag: 'quick119-inquiry-' + q.id });
      note.onclick = () => { window.focus(); activeKey = `p:${q.patient?.id ?? q.patient_id}`; resetThreadUi(); sig.threads = ''; render(); };
    } catch { /* 알림을 띄울 수 없는 환경 */ }
  }
  // 답하지 않은 새 문의가 있는 동안 알림음을 되풀이한다. 소리만 끈다고 회신이 되지는 않는다.
  if (soundOn && sound && ids.size && Date.now() - lastBeep > 5500) { lastBeep = Date.now(); beep(); }
}
function beep() {
  try {
    const osc = sound.createOscillator(), gain = sound.createGain();
    osc.frequency.value = 880; gain.gain.setValueAtTime(0.0001, sound.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.18, sound.currentTime + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, sound.currentTime + 0.35);
    osc.connect(gain).connect(sound.destination); osc.start(); osc.stop(sound.currentTime + 0.4);
  } catch { /* 소리를 낼 수 없는 환경 */ }
}

// ---------------------------------------------------------------- 시작
async function loadHospitals() {
  try {
    const list = await api('/api/hospitals');
    $('hospital').replaceChildren(...list.map((item) => h('option', { value: item.id, text: `${item.name} · ${item.id}` })));
    if (!list.some((item) => item.id === hid) && list.length) hid = list[0].id;
  } catch { $('hospital').replaceChildren(h('option', { value: hid, text: hid })); }
  $('hospital').value = hid;
}

$('handler').value = stored('handler') || params.get('handler') || '';
$('fullLink').href = './?hid=' + encodeURIComponent(hid);
$('settingsToggle').addEventListener('click', () => openSettings($('settings').hidden));
$('handler').addEventListener('input', () => { store('handler', handler()); $('who').textContent = `${hospital?.name || hid} · ${handler() || '담당자 미입력'}`; });
$('hospital').addEventListener('change', () => {
  hid = $('hospital').value; context++; known = null; loaded = false; activeKey = null; threads = [];
  inquiries = { open: [], recent: [] }; incoming = []; hospital = null; resetThreadUi(); sig = { threads: '', log: '', actions: '' };
  const url = new URL(location.href); url.searchParams.set('hid', hid); history.replaceState(null, '', url);
  $('fullLink').href = './?hid=' + encodeURIComponent(hid);
  render(); void pollNow();
});
$('notifyToggle').addEventListener('click', async () => {
  if (!('Notification' in window)) { showError('이 브라우저는 알림을 지원하지 않습니다.'); return; }
  const permission = Notification.permission === 'default' ? await Notification.requestPermission() : Notification.permission;
  $('notifyToggle').textContent = permission === 'granted' ? '브라우저 알림 켜짐' : '브라우저 알림이 차단됨';
});
if ('Notification' in window && Notification.permission === 'granted') $('notifyToggle').textContent = '브라우저 알림 켜짐';
$('soundToggle').addEventListener('click', () => {
  soundOn = !soundOn;
  if (soundOn && !sound) { const Ctx = window.AudioContext || window.webkitAudioContext; sound = Ctx ? new Ctx() : null; }
  if (soundOn && !sound) { soundOn = false; showError('이 브라우저에서는 알림음을 낼 수 없습니다.'); }
  if (soundOn) beep();
  $('soundToggle').setAttribute('aria-pressed', String(soundOn)); $('soundToggle').textContent = soundOn ? '알림음 켜짐' : '알림음 켜기';
});
$('composer').addEventListener('submit', (event) => {
  event.preventDefault();
  const stage = active()?.stage;
  if (stage === 'new' || stage === 'checking') {
    const question = $('message').value.trim();
    if (question) void respond('question', { question });
  } else void sendChat();
});
document.addEventListener('visibilitychange', () => { if (!document.hidden) void poll(); });

if (!handler()) openSettings(true);
render();
await loadHospitals();
await poll();
setInterval(() => { void poll(); renderThreads(); }, POLL_MS);
