// 병원 채팅 패널의 화면과 통신. 기존 병원 웹과 같은 API만 쓰고 새 서버 기능은 만들지 않는다.
// - 목록: 환자 한 명이 대화 하나. 먼저 온 순서로 두고 중증도 색, 상태, 배지(답할 일 + 안 읽은 메시지)를 보여 준다.
// - 확정 전: 문의 한 건의 요약 카드, 회신 버튼(수용 가능·확인 중·수용 불가), 추가 질문
// - 확정 후: 이송 대화, 수용 유지·수용 취소 요청, 환자 도착 확인
// - 다른 화면(EMR 등)에 iframe으로 들어가면 건수를 알리고, 같은 출처의 화면과는 대화 열기·침상 안내를 주고받는다.
import { buildThreads, threadStatus, summaryFields, severityLabel, severityKey, patientTitle, acceptBlock, inquiryLog, chatLog, compactChatLog,
  chatSummary, threadBadge, needsReview, etaLabel, DECLINE_REASONS, SEVERITY } from './panel-state.mjs';
import { ChatRecoveryStore, newChatRequest, confirmsChatRequest, isRejected } from './chat-recovery.mjs';

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const base = location.origin;
const POLL_MS = 3000;
const VOLATILE = new Set(['eta_remaining_seconds', 'elapsed_seconds', 'age_seconds', 'last_checked_at', 'checked_at', 'updated_at']);
const wide = window.matchMedia('(min-width: 560px)');
const embedded = window.parent !== window;

function stored(key) { try { return localStorage.getItem(key); } catch { return null; } }
function store(key, value) { try { localStorage.setItem(key, value); } catch { /* 저장소가 막혀 있으면 이번 화면에서만 유지 */ } }
let recovery = null;
try { recovery = new ChatRecoveryStore({ storage: localStorage }); } catch { recovery = null; }

let hid = params.get('hid') || 'H01';
let context = 0;                       // 병원을 바꾸면 늘려서 늦게 온 응답을 버린다
let inquiries = { open: [], recent: [] }, incoming = [], hospital = null;
let threads = [], activeKey = null, view = 'list', clockOffset = 0, polling = false, busy = false, loaded = false;
let ui = { declineOpen: false, reason: null, cancelOpen: false };
const chats = new Map();               // 대화 키 -> 확정 후 이송 대화(항목, 커서, 마지막으로 본 메시지)
let chatPending = null;
let known = null;                      // 이미 본 새 문의 id (첫 조회는 기준선)
let sound = null, soundOn = false, lastBeep = 0, errorTimer = null;
let sig = { threads: '', log: '', actions: '', head: '' }, shownKey = null;
let finishedOpen = stored('panelFinishedOpen') !== '0';   // 끝난 대화 묶음을 펼쳐 둘지. 고른 대로 기억한다.

const now = () => Date.now() / 1000 + clockOffset;
const clock = (at) => new Date(at * 1000).toLocaleTimeString('ko-KR', { hour: 'numeric', minute: '2-digit' });
const handler = () => $('handler').value.trim();
const isChatStage = (stage) => stage === 'confirmed' || stage === 'arrived';

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
const chatOf = (thread) => (thread ? chats.get(thread.key) : null) || null;
// 대화가 화면에 보이는 중인가: 넓은 창이면 늘, 좁은 창이면 대화 화면일 때만.
const chatShown = () => !document.hidden && (wide.matches || view === 'chat');

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
    // 확정된 환자의 대화는 보고 있지 않아도 조회해, 목록에 안 읽은 메시지를 표시한다.
    for (const thread of threads.filter((t) => t.stage === 'confirmed' || (t.stage === 'arrived' && t.key === activeKey))) {
      await loadChat(thread, version);
      if (version !== context) return;
    }
    rebuild();
  } catch (error) {
    if (version !== context) return;
    setConn('error', '연결 끊김');
    if (!loaded) showError(error.status ? error.message : '서버에 연결할 수 없습니다. 서버가 켜져 있는지 확인하세요.');
  } finally { polling = false; }
}
async function pollNow() { polling = false; await poll(); }

async function loadChat(thread, version = context) {
  let chat = chats.get(thread.key);
  if (!chat) { chat = { key: thread.key, items: new Map(), eventCursor: 0, messageCursor: 0, canSend: false, session: null, seenId: null }; chats.set(thread.key, chat); }
  try {
    for (let page = 0; page < 20; page++) {
      const result = await api(`/api/hospitals/${encodeURIComponent(hid)}/patients/${encodeURIComponent(thread.patientId)}/conversation?event_cursor=${chat.eventCursor}&message_cursor=${chat.messageCursor}&limit=100`);
      if (version !== context) return;
      for (const item of result.items) chat.items.set(item.id, item);
      chat.eventCursor = result.cursors.event_id; chat.messageCursor = result.cursors.message_id;
      chat.canSend = !!result.can_send; chat.session = result.active_hospital_session?.id ?? null;
      if (!result.has_more) break;
    }
    // 이 화면을 열기 전에 오간 메시지는 읽은 것으로 본다. 그 뒤에 온 것부터 안 읽음으로 센다.
    if (chat.seenId === null) chat.seenId = chatSummary([...chat.items.values()]).maxId;
  } catch { chat.canSend = false; }
}

function markSeen() {
  const chat = chatOf(active());
  if (chat && chat.seenId !== null && chatShown()) chat.seenId = chatSummary([...chat.items.values()], chat.seenId).maxId;
}

function rebuild() {
  threads = buildThreads({ open: inquiries.open, recent: inquiries.recent, incoming }, now());
  for (const key of [...chats.keys()]) if (!threads.some((t) => t.key === key)) chats.delete(key);
  if (!active()) { activeKey = threads[0]?.key ?? null; resetThreadUi(); if (!activeKey) view = 'list'; }
  $('who').textContent = `${hospital?.name || hid} · ${handler() || '담당자 미입력'}`;
  markSeen();
  render();
  notifyHost();
}

function rows() {
  return threads.map((t) => {
    const chat = chats.get(t.key), status = threadStatus(t, now());
    const summary = chat ? chatSummary([...chat.items.values()], chat.seenId ?? 0) : { unread: 0, lastAt: null, preview: '' };
    const q = t.inquiry;
    return { key: t.key, patientId: t.patientId, title: patientTitle(t.patient), sev: severityKey(t.patient), finished: !!t.finished, stage: t.stage,
      status: status.text, tone: status.tone, unread: summary.unread, badge: threadBadge(t, now(), summary.unread),
      sub: summary.unread ? summary.preview : status.text, subTone: summary.unread ? null : status.tone,
      at: summary.lastAt ?? q?.responded_at ?? q?.created_at ?? null, on: t.key === activeKey };
  });
}

// 이 패널을 iframe으로 품은 화면에 알린다. 어느 화면에든 건수만 보내고(환자 정보 없음),
// 같은 출처의 화면(시연용 가상 EMR)에는 목록 요약도 보낸다.
function notifyHost(list = rows()) {
  const pending = list.filter((row) => row.tone === 'alert').length, unread = list.reduce((sum, row) => sum + row.unread, 0);
  document.title = (pending + unread ? `(${pending + unread}) ` : '') + '퀵119 수용 문의';
  if (!embedded) return;
  window.parent.postMessage({ source: 'quick119-hospital-panel', pending, unread }, '*');
  window.parent.postMessage({ source: 'quick119-hospital-panel', type: 'threads', hospital: hid, threads: threads.map((t, index) => ({
    patientId: t.patientId, title: list[index].title, age: t.patient.age ?? null, symptoms: t.patient.symptoms || '', stage: t.stage,
    status: list[index].status, severity: list[index].sev, severityLabel: SEVERITY[list[index].sev], eta: etaLabel(t.patient, now()),
    canChat: t.stage === 'confirmed' && !!chats.get(t.key)?.canSend })) }, location.origin);
}

function resetThreadUi() {
  ui = { declineOpen: false, reason: null, cancelOpen: false };
  const thread = active();
  chatPending = thread && isChatStage(thread.stage) ? recovery?.load({ server: base, hospital: hid, patient: thread.patientId })?.request || null : null;
  $('message').value = '';
  sig.log = sig.actions = sig.head = '';
}

// 위치 지도는 새 창 대신 대화 자리에 연다. 새로고침은 지도 화면 안에 있다.
function showLocation(open) {
  const link = $('locationLink'), sheet = $('locationSheet'), frame = $('locationFrame');
  open = open && !link.hidden && !!link.href;
  if (open && frame.getAttribute('src') !== link.href) frame.src = link.href;
  if (!open && sheet.hidden) return;
  sheet.hidden = !open;
  link.setAttribute('aria-expanded', String(open));
  link.textContent = open ? '위치 닫기' : '위치 조회';
  if (open) document.querySelector('.qp-chat').dataset.location = 'open';
  else { delete document.querySelector('.qp-chat').dataset.location; frame.removeAttribute('src'); }
}

function selectThread(key, { show = true } = {}) {
  if (busy || !threads.some((t) => t.key === key)) return false;
  if (key !== activeKey) { activeKey = key; resetThreadUi(); showLocation(false); }
  if (show) view = 'chat';
  sig.threads = '';
  markSeen(); render(); notifyHost();
  void pollNow();
  return true;
}

// ---------------------------------------------------------------- 그리기
function render() {
  $('panel').dataset.view = view;
  renderThreads(); renderHead(); renderLog(); renderActions();
}

function renderThreads() {
  const list = rows();
  const next = JSON.stringify([list, loaded, finishedOpen]);
  if (next === sig.threads) return;
  sig.threads = next;
  const finishedCount = list.filter((row) => row.finished).length;
  const nodes = [];
  if (!list.length) {
    nodes.push(h('p', { class: 'qp-list-empty' }, h('strong', { text: loaded ? '대기 중인 문의가 없습니다' : '불러오는 중' }),
      loaded ? '새 수용 문의가 오면 여기에 먼저 온 순서로 쌓입니다.' : ''));
  }
  let divided = false;
  for (const row of list) {
    if (row.finished && !divided) {
      divided = true;
      // 끝난 대화는 한 줄로 접을 수 있다. 접어도 진행 중인 대화의 순서와 배지는 그대로다.
      nodes.push(h('button', { type: 'button', class: 'qp-list-divider', id: 'finishedToggle', 'aria-expanded': String(finishedOpen),
        text: `끝난 대화 ${finishedCount}`, onclick: () => {
          finishedOpen = !finishedOpen; store('panelFinishedOpen', finishedOpen ? '1' : '0');
          renderThreads(); $('finishedToggle')?.focus();
        } }));
    }
    if (row.finished && !finishedOpen) continue;
    nodes.push(h('button', { type: 'button', class: 'qp-row', 'data-thread': row.key, 'data-finished': String(row.finished), 'aria-pressed': String(row.on),
      'aria-label': `${SEVERITY[row.sev]} · ${row.title} · ${row.status}${row.badge ? ` · 확인할 것 ${row.badge}건` : ''}`, onclick: () => selectThread(row.key) },
    h('span', { class: 'qp-sev', 'data-sev': row.sev, 'aria-hidden': 'true', text: SEVERITY[row.sev] }),
    h('span', { class: 'qp-row-main' }, h('strong', { text: row.title }),
      h('span', { class: 'qp-row-line' }, h('span', { class: 'qp-row-sub', text: row.sub, tone: row.subTone }), row.at ? h('time', { text: clock(row.at) }) : null)),
    row.badge ? h('span', { class: 'qp-unread', text: String(row.badge) }) : null));
  }
  $('threads').replaceChildren(...nodes);
}

function renderHead() {
  const thread = active(), list = rows();
  const others = list.filter((row) => row.key !== activeKey).reduce((sum, row) => sum + row.badge, 0);
  const status = thread ? threadStatus(thread, now()) : null;
  const next = JSON.stringify([thread?.key, thread && patientTitle(thread.patient), thread?.stage, thread?.patient?.state, status, others]);
  if (next === sig.head) return;
  sig.head = next;
  $('chatTitle').textContent = thread ? patientTitle(thread.patient) : '대화를 선택하세요';
  $('chatStatus').textContent = status?.text || '';
  const canLocate = thread?.stage === 'confirmed' && ['CONFIRMED', 'IN_TRANSIT'].includes(thread.patient.state);
  $('locationLink').hidden = !canLocate;
  if (canLocate) {
    const url = new URL('transport-location.html', location.href);
    url.searchParams.set('hid', hid);
    url.searchParams.set('pid', thread.patientId);
    $('locationLink').href = url.href;
    $('locationPopout').href = url.href;
  } else { $('locationLink').removeAttribute('href'); showLocation(false); }
  if (status?.tone) $('chatStatus').dataset.tone = status.tone; else delete $('chatStatus').dataset.tone;
  $('backBadge').hidden = !others; $('backBadge').textContent = String(others);
  $('back').setAttribute('aria-label', others ? `대화 목록으로 · 다른 대화에 확인할 것 ${others}건` : '대화 목록으로');
}

function card(thread) {
  const p = thread.patient, q = thread.inquiry;
  const source = isChatStage(thread.stage) ? '우리 병원으로 확정된 환자' : `수용 문의${q?.created_at ? ' · ' + clock(q.created_at) : ''}`;
  const fields = summaryFields(p, hospital).map((f) => h('div', { class: 'qp-field', tone: f.tone }, h('dt', { text: f.label }), h('dd', { text: f.value })));
  return h('article', { class: 'qp-card' }, h('div', { class: 'qp-card-src', text: source }),
    h('div', { class: 'qp-card-head' }, h('strong', { text: patientTitle(p) }), h('span', { text: severityLabel(p) })),
    h('dl', { class: 'qp-fields' }, ...fields));
}

function renderLog() {
  const thread = active(), box = $('log');
  if (!thread) {
    const next = 'empty:' + loaded;
    if (next === sig.log) return;
    sig.log = next; shownKey = null;
    $('logInner').replaceChildren(h('div', { class: 'qp-empty' }, h('strong', { text: loaded ? '대기 중인 문의가 없습니다' : '불러오는 중' }),
      loaded ? '새 수용 문의가 오면 목록에 표시됩니다.' : ''));
    return;
  }
  const chat = chatOf(thread);
  const entries = isChatStage(thread.stage) ? compactChatLog(chatLog(chat ? [...chat.items.values()] : [])) : inquiryLog(thread.inquiry);
  // 매 조회마다 바뀌는 경과 시간 값은 빼고 비교해, 읽는 중에 대화가 다시 그려지지 않게 한다.
  const next = JSON.stringify([thread.key, thread.stage, thread.patient, hospital?.resources, entries],
    (key, value) => (VOLATILE.has(key) ? undefined : value));
  if (next === sig.log) return;
  sig.log = next;
  const pinned = shownKey !== thread.key || box.scrollHeight - box.scrollTop - box.clientHeight < 48;
  const openGroups = new Set(shownKey === thread.key
    ? [...$('logInner').querySelectorAll('details[data-routine-id][open]')].map(node => node.dataset.routineId) : []);
  shownKey = thread.key;
  $('logInner').replaceChildren(card(thread), ...entries.map((entry) => entry.kind === 'routine-group'
    ? h('details', {class: 'qp-routine', 'data-routine-id': entry.id, open: openGroups.has(entry.id)},
      h('summary', {text: `진행 기록 ${entry.entries.length}건`}),
      ...entry.entries.map(item => h('p', {text: item.text})))
    : entry.kind === 'notice'
    ? h('p', { class: 'qp-notice', text: entry.text })
    : h('div', { class: 'qp-msg', 'data-kind': entry.kind }, h('p', { text: entry.text }),
      entry.at ? h('small', { text: (entry.kind === 'crew' && entry.sender ? entry.sender + ' · ' : '') + clock(entry.at) }) : null)));
  // 맨 아래를 보고 있었거나 대화를 바꿨으면 최신 내용으로 내린다. 위를 읽는 중이면 그대로 둔다.
  if (pinned) box.scrollTop = box.scrollHeight;
}

function button(label, cls, onclick, extra = {}) { return h('button', { type: 'button', class: 'qp-btn ' + cls, text: label, onclick, disabled: busy, ...extra }); }

function renderActions() {
  const thread = active();
  const box = $('actions'), composer = $('composer');
  if (!thread) { box.replaceChildren(); composer.hidden = true; sig.actions = ''; return; }
  const p = thread.patient, q = thread.inquiry, stage = thread.stage, chat = chatOf(thread);
  const blocked = stage === 'new' || stage === 'checking' ? acceptBlock(p, hospital, now()) : null;
  const canChat = stage === 'confirmed' && !!chat && (chat.canSend || !!chatPending);
  const next = JSON.stringify([thread.key, stage, q?.version, ui, busy, blocked, canChat, !!chatPending, p.needs_adjustment, needsReview(p), p.state]);
  if (next === sig.actions) return;
  sig.actions = next;
  const nodes = [];
  if (stage === 'new' || stage === 'checking') {
    if (ui.declineOpen) {
      nodes.push(h('div', { class: 'qp-form' }, h('h2', { text: '수용 불가 사유' }),
        h('div', { class: 'qp-reasons' }, ...DECLINE_REASONS.map((reason) => h('button', { type: 'button', class: 'qp-chip', text: reason,
          'aria-pressed': String(ui.reason === reason), disabled: busy, onclick: () => { ui.reason = reason; renderActions(); } }))),
        h('div', { class: 'qp-row-actions' }, button('취소', 'qp-btn-plain', () => { ui.declineOpen = false; ui.reason = null; renderActions(); }),
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
    nodes.push(h('div', { class: 'qp-done', tone: 'wait', role: 'status', text: '구급대의 목적지 확정을 기다리는 중' }));
  } else if (stage === 'confirmed') {
    if (ui.cancelOpen) {
      nodes.push(h('div', { class: 'qp-form' }, h('h2', { text: '수용 취소 요청 사유' }),
        h('label', { class: 'qp-sr', for: 'cancelNote', text: '수용 취소 요청 사유' }),
        h('input', { id: 'cancelNote', type: 'text', maxlength: '1000', autocomplete: 'off', placeholder: '예: 중환자실 병상이 방금 찼습니다' }),
        h('div', { class: 'qp-row-actions' }, button('취소', 'qp-btn-plain', () => { ui.cancelOpen = false; renderActions(); }),
          button('수용 취소 요청 보내기', 'qp-btn-danger qp-grow', () => decide('adjust', $('cancelNote').value.trim())))));
    } else {
      if (needsReview(p)) {
        nodes.push(h('p', { class: 'qp-note', tone: 'alert', text: '이송 중 환자 상태나 조건이 바뀌었습니다. 그대로 받을 수 있는지 확인해 주세요.' }));
        nodes.push(h('div', { class: 'qp-row-actions' }, button('수용 유지', 'qp-btn-primary qp-grow', () => decide('keep', ''), { 'data-action': 'keep' }),
          button('수용 취소 요청', 'qp-btn-decline', () => { ui.cancelOpen = true; renderActions(); }, { 'data-action': 'adjust' })));
      } else {
        nodes.push(h('div', { class: 'qp-row-actions' }, button('환자 도착 확인', 'qp-btn-primary qp-grow', () => patientAction('arrive'), { 'data-action': 'arrive' }),
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
  if (busy) return false;
  busy = true; sig.actions = ''; renderActions();
  let ok = true;
  try { await task(); showError(''); }
  catch (error) {
    ok = false;
    let text = error.message || '요청을 처리하지 못했습니다.';
    if (error.body?.error === 'CONFLICT' || error.status === 409) text = '다른 담당자의 처리나 상태 변경이 먼저 반영됐습니다. 최신 상태를 확인하세요. ' + text;
    if (error.body?.shortfalls?.length) text += ' 부족: ' + error.body.shortfalls.map((s) => s.resource_type).join(', ');
    showError(text);
  } finally { busy = false; sig.actions = ''; await pollNow(); renderActions(); }
  return ok;
}

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
function sendChat(text = $('message').value.trim()) {
  const thread = active(), chat = chatOf(thread);
  if (!thread || !chat) return false;
  const retry = chatPending;
  if (!retry && (!text || !chat.canSend || !requireHandler())) return false;
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
      note.onclick = () => { window.focus(); selectThread(`p:${q.patient?.id ?? q.patient_id}`); };
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

// ---------------------------------------------------------------- 품은 화면(같은 출처)에서 온 요청
// 시연용 가상 EMR이 보내는 두 가지만 받는다: 대화 열기, 배정한 침상을 구급대에 안내하기.
window.addEventListener('message', (event) => {
  if (!embedded || event.source !== window.parent || event.origin !== location.origin) return;
  const data = event.data;
  if (!data || data.source !== 'quick119-emr-host' || typeof data.patientId !== 'string') return;
  const key = 'p:' + data.patientId;
  if (data.type === 'open') { selectThread(key); return; }
  if (data.type === 'bed' && typeof data.bed === 'string' && /^[0-9A-Za-z가-힣-]{1,12}$/.test(data.bed)) {
    if (!selectThread(key)) { showError('이 환자의 대화를 찾지 못해 침상을 안내하지 못했습니다.'); return; }
    const thread = active(), chat = chatOf(thread);
    if (thread.stage !== 'confirmed' || !chat?.canSend || chatPending) { showError('지금은 이 환자에게 메시지를 보낼 수 없어 침상을 안내하지 못했습니다.'); return; }
    void sendChat(`응급실 ${data.bed} 베드로 오세요.`);
  }
});

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
  hid = $('hospital').value; context++; known = null; loaded = false; activeKey = null; threads = []; view = 'list'; chats.clear();
  inquiries = { open: [], recent: [] }; incoming = []; hospital = null; resetThreadUi(); sig = { threads: '', log: '', actions: '', head: '' };
  const url = new URL(location.href); url.searchParams.set('hid', hid); history.replaceState(null, '', url);
  $('fullLink').href = './?hid=' + encodeURIComponent(hid);
  render(); void pollNow();
});
$('locationLink').addEventListener('click', (event) => {
  if (event.ctrlKey || event.metaKey || event.shiftKey || event.button) return;   // 새 탭·새 창으로 여는 동작은 그대로 둔다
  event.preventDefault();
  showLocation($('locationSheet').hidden);
});
$('locationClose').addEventListener('click', () => { showLocation(false); $('locationLink').focus(); });
$('back').addEventListener('click', () => { view = 'list'; render(); $('threads').querySelector('[aria-pressed=true]')?.focus(); });
wide.addEventListener('change', () => { markSeen(); render(); });
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
document.addEventListener('visibilitychange', () => { if (!document.hidden) { markSeen(); void poll(); } });

if (!handler()) openSettings(true);
render();
await loadHospitals();
await poll();
setInterval(() => { void poll(); renderThreads(); renderHead(); }, POLL_MS);
