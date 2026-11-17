/* Existing team contracts. All messages, drafts, cursors and retries live in RAM. */
(() => {
  'use strict';
  if (!document.getElementById('transport-workspace')) return;
  const $ = name => document.getElementById('transport-' + name);
  const idPattern = /^Q119-TEST-[A-Za-z0-9-]{1,80}$/;
  let pid = null, creation = null, hospitals = [], snapshot = null;
  let eventCursor = 0, messageCursor = 0, epoch = 0, timer = null, pollTask = null;
  let stopped = false, suspended = document.hidden, operation = false, sending = false, fresh = false;
  let initialRead = true, pending = null;
  let draftSession = null;
  const items = new Map(), unread = new Set(), controllers = new Set();
  const active = () => !!pid && !stopped && !suspended;
  const path = () => '/api/patients/' + encodeURIComponent(pid);
  const chatVisible = () => !$('chat').hidden && !document.hidden;
  const atBottom = () => $('messages').scrollHeight - $('messages').scrollTop - $('messages').clientHeight < 45;
  function error(message) { $('error').textContent = message; $('error').hidden = !message; }
  async function api(url, payload) {
    const controller = new AbortController(); controllers.add(controller);
    const timeout = setTimeout(() => controller.abort(), 12000);
    try {
      const response = await fetch(url, {method: payload === undefined ? 'GET' : 'POST',
        headers: {'Content-Type': 'application/json'}, cache: 'no-store', credentials: 'same-origin',
        signal: controller.signal, ...(payload === undefined ? {} : {body: JSON.stringify(payload)})});
      const body = await response.json();
      if (!response.ok) {
        const failure = new Error(body.message || (typeof body.detail === 'string' ? body.detail : '') || '요청을 처리하지 못했습니다.');
        failure.status = response.status; failure.code = body.error; throw failure;
      }
      return body;
    } finally { clearTimeout(timeout); controllers.delete(controller); }
  }
  function abortRequests() { controllers.forEach(controller => controller.abort()); }
  function badge() {
    $('unread').hidden = unread.size === 0;
    $('unread').textContent = unread.size > 99 ? '99+' : String(unread.size);
    $('toggle').setAttribute('aria-label', $('chat').hidden ? `병원 채팅${unread.size ? ', 새 메시지 ' + unread.size + '개' : ''}` : 'GPS 지도');
    $('mark-read').hidden = unread.size === 0;
  }
  function markRead() { if (chatVisible()) { unread.clear(); badge(); } }
  function renderMessages() {
    const follow = atBottom();
    const scrollTop = $('messages').scrollTop;
    const ordered = [...items.values()].sort((a, b) => {
      for (let n = 0; n < 3; n++) { const diff = a.order_key[n] - b.order_key[n]; if (diff) return diff; }
      return 0;
    });
    // Reordering streams must not duplicate nodes or destroy the draft/focus.
    for (const item of ordered) {
      let node = document.getElementById('transport-item-' + item.id);
      if (!node) {
        node = document.createElement('div'); node.id = 'transport-item-' + item.id;
        node.className = 'transport-message'; node.dataset.role = item.sender_role; node.dataset.itemId = item.id;
        const sender = document.createElement('small');
        sender.textContent = `${item.sender_name || '시스템'} · ${new Date(item.at * 1000).toLocaleTimeString('ko-KR')}`;
        const text = document.createElement('span'); text.textContent = item.text || item.body || '';
        node.append(sender, text);
      }
      $('messages').append(node);
    }
    if (chatVisible() && follow) { $('messages').scrollTop = $('messages').scrollHeight; markRead(); }
    else $('messages').scrollTop = scrollTop;
  }
  function merge(incoming, countNew) {
    const following = chatVisible() && atBottom();
    for (const item of incoming) {
      if (items.has(item.id)) continue;
      items.set(item.id, item);
      if (countNew && item.kind === 'message' && item.sender_role === 'hospital' && !following) unread.add(item.id);
    }
    renderMessages(); badge();
  }
  function canCompose() {
    return active() && fresh && snapshot?.can_send && snapshot?.conversation?.state === 'OPEN' && !!snapshot?.active_hospital_session;
  }
  function controls() {
    const staleDraft = draftSession !== null && draftSession !== snapshot?.active_hospital_session?.id && !!$('message').value.trim();
    $('new-case').disabled = operation || !!pid || sending || suspended;
    $('message').disabled = !canCompose() || sending || !!pending;
    $('send').disabled = sending || operation || !active() || (!pending && (!canCompose() || staleDraft));
    $('send').textContent = sending ? '전송 확인 중…' : pending ? '원래 요청으로 재시도' : '메시지 보내기';
    $('discard').hidden = !pending; $('discard').disabled = sending;
    $('review-draft').hidden = !!pending || !staleDraft;
    $('review-draft').disabled = !canCompose() || sending;
    $('pending').textContent = pending ? '전송 결과가 확인되지 않은 요청을 메모리에 보관 중입니다. 원래 병원 참여 구간·내용으로만 재시도합니다.' : '';
    if (!pending && staleDraft) $('pending').textContent = '참여 병원이 변경됐습니다. 현재 병원과 초안을 확인한 뒤 전송을 허용하세요.';
    $('chat-stop').disabled = !pid || stopped || sending || operation;
    $('chat-resume').hidden = !stopped; $('chat-resume').disabled = suspended;
    $('case').textContent = pid ? '가상 이송: ' + pid : '';
    const hospital = snapshot?.active_hospital_session?.hospital_name;
    let state = !pid ? '병원 연결 대기 · 먼저 가상 이송을 시작하세요.'
      : stopped ? '채팅 연결 종료 · GPS 안내는 유지됩니다.'
      : suspended ? '앱 백그라운드 · 복귀 시 대화를 다시 조회합니다.'
      : !fresh ? '병원 연결 확인 중 · 네트워크를 확인하세요.'
      : snapshot?.conversation?.state === 'CLOSED' ? '이송 대화 종료 · 이전 기록을 조회할 수 있습니다.'
      : hospital ? `${hospital} · 채팅 연결됨`
      : '병원 연결 대기 · 수용 문의 → 의료진 수용 → 목적지 확정';
    $('connection').textContent = state; $('chat-status').textContent = state;
    $('message').placeholder = hospital ? '가상 환자의 전달 내용을 입력하세요.' : '목적지 확정 후 입력할 수 있습니다.';
    document.querySelectorAll('[data-transport-confirm],[data-transport-inquire]').forEach(button => {
      button.disabled = operation || sending || !active() || !fresh || !!snapshot?.active_hospital_session || snapshot?.conversation?.state === 'CLOSED';
    });
  }
  function renderHospitals() {
    $('hospitals').replaceChildren();
    for (const hospital of hospitals) {
      const card = document.createElement('div'); card.className = 'transport-hospital';
      const name = document.createElement('strong'); name.textContent = hospital.name;
      const actions = document.createElement('div'); actions.className = 'transport-actions';
      const route = document.createElement('button'); route.type = 'button'; route.textContent = 'GPS 경로 보기';
      route.addEventListener('click', () => document.dispatchEvent(new CustomEvent('quick119:route-select', {detail: {
        name: hospital.name, lat: hospital.lat, lon: hospital.lon, hospital_id: hospital.id}})));
      const inquire = document.createElement('button'); inquire.type = 'button'; inquire.textContent = '수용 문의';
      inquire.dataset.transportInquire = hospital.id;
      inquire.addEventListener('click', () => runOperation(async () => {
        await api(path() + '/inquiries', {hospital_ids: [hospital.id]});
        await pollNow();
      }));
      const link = document.createElement('a'); link.textContent = '의료진 화면 열기 ↗';
      link.href = '/hospital/?hid=' + encodeURIComponent(hospital.id) + (pid ? '&pid=' + encodeURIComponent(pid) : '');
      link.target = '_blank'; link.rel = 'noopener noreferrer';
      actions.append(route, inquire); card.append(name, actions, link); $('hospitals').append(card);
    }
    controls();
  }
  function renderInquiries() {
    const signature = JSON.stringify((snapshot?.inquiries || []).map(q => [q.id, q.state, q.usable]));
    if ($('inquiries').dataset.signature === signature) return;
    $('inquiries').dataset.signature = signature; $('inquiries').replaceChildren();
    const labels = {PENDING: '수용 문의 중', CHECKING: '의료진 확인 중', ACCEPTED: '수용 응답 도착', CONFIRMED: '목적지 확정', DECLINED: '수용 불가', EXPIRED: '수용 응답 만료', CANCELLED: '문의 종료'};
    for (const q of snapshot?.inquiries || []) {
      const card = document.createElement('div'); card.className = 'transport-hospital';
      const title = document.createElement('p'); title.textContent = `${q.hospital_name || q.hospital_id} · ${labels[q.state] || q.state}`; card.append(title);
      if (q.state === 'ACCEPTED' && q.usable) {
        const confirm = document.createElement('button'); confirm.type = 'button'; confirm.textContent = '이 병원으로 목적지 확정'; confirm.dataset.transportConfirm = q.id;
        confirm.addEventListener('click', () => runOperation(async () => {
          await api(path() + '/confirm', {inquiry_id: q.id}); await pollNow();
          const hospital = hospitals.find(h => h.id === q.hospital_id);
          if (hospital) document.dispatchEvent(new CustomEvent('quick119:route-select', {detail: {
            name: hospital.name, lat: hospital.lat, lon: hospital.lon, hospital_id: hospital.id}}));
        }));
        card.append(confirm);
      }
      $('inquiries').append(card);
    }
  }
  function schedule() {
    clearTimeout(timer); timer = null;
    if (active()) timer = setTimeout(() => { timer = null; void pollNow(); }, 3000);
  }
  async function pollNow() {
    if (!active()) return false;
    if (pollTask?.epoch === epoch) return pollTask.promise;
    clearTimeout(timer); timer = null;
    const ticket = {epoch, pid};
    pollTask = ticket;
    ticket.promise = (async () => {
      try {
        let more;
        do {
          const data = await api(path() + `/conversation?event_cursor=${eventCursor}&message_cursor=${messageCursor}&limit=100`);
          if (epoch !== ticket.epoch || !active()) return false;
          snapshot = data; merge(data.items, !initialRead);
          eventCursor = data.cursors.event_id; messageCursor = data.cursors.message_id;
          more = data.has_more;
        } while (more);
        initialRead = false; fresh = true; renderInquiries(); controls(); return true;
      } catch (failure) {
        if (epoch === ticket.epoch && active()) { fresh = false; error(failure.name === 'AbortError' ? '대화 조회 시간이 초과됐습니다. 다시 조회합니다.' : failure.message); controls(); }
        return false;
      } finally {
        if (pollTask === ticket) { pollTask = null; schedule(); }
      }
    })();
    return ticket.promise;
  }
  async function runOperation(fn) {
    if (operation || sending || suspended) return;
    operation = true; error(''); controls();
    try { await fn(); } catch (failure) { if (!suspended) error(failure.message || '연결에 실패했습니다.'); }
    finally { operation = false; controls(); }
  }
  async function loadHospitals() {
    const list = await api('/api/hospitals');
    if (!Array.isArray(list) || !list.length || list.some(h => h.source !== 'synthetic' || !/^H(0[1-9]|10)$/.test(h.id))) throw new Error('가상 병원 목록을 확인할 수 없습니다.');
    hospitals = list; renderHospitals();
  }
  $('new-case').addEventListener('click', () => runOperation(async () => {
    if (!hospitals.length) await loadHospitals();
    if (!creation) creation = {id: 'Q119-TEST-' + crypto.randomUUID(), reporter_role: 'transporter', consent: true,
      age: 30, symptoms: '통합 UI 가상 예제', priority_source: 'unknown', lat: 35.1595, lon: 126.8526,
      required_capabilities: [], resource_demands: {ed_bed: 1}, extra: {synthetic_fixture: true, creation_token: crypto.randomUUID()}};
    let data;
    try { data = await api('/api/patients', creation); }
    catch (failure) {
      if (failure.status !== 409) throw failure;
      data = await api('/api/patients/' + creation.id);
    }
    if (data.id !== creation.id || data.extra?.creation_token !== creation.extra.creation_token) throw new Error('가상 이송 생성 결과를 확인하지 못했습니다.');
    pid = data.id; epoch++; stopped = false;
    const url = new URL(location.href); url.searchParams.set('pid', pid); history.replaceState(null, '', url);
    renderHospitals(); await pollNow();
  }));
  function confirms(result, request) {
    const m = result?.message;
    return typeof result?.duplicate === 'boolean' && m?.kind === 'message' && m.sender_role === 'patient'
      && m.client_message_id === request.client_message_id && m.body === request.body
      && m.hospital_session_id === request.hospital_session_id && m.sender_name === request.sender_name
      && Number.isInteger(m.message_id) && m.id === `message:${m.message_id}` && Array.isArray(m.order_key);
  }
  $('message-form').addEventListener('submit', async event => {
    event.preventDefault(); if (sending || operation || !active() || (!pending && !canCompose())) return;
    if (!pending && draftSession !== null && draftSession !== snapshot?.active_hospital_session?.id) return;
    const composed = $('message').value.trim(); if (!pending && !composed) return;
    const session = snapshot?.active_hospital_session?.id;
    sending = true; error(''); controls();
    try {
      if (!await pollNow()) throw new Error('최신 대화를 확인한 뒤 다시 전송하세요.');
      if (!pending) {
        if (!canCompose() || snapshot.active_hospital_session?.id !== session) throw new Error('참여 병원이 변경됐습니다. 내용을 확인한 뒤 다시 전송하세요.');
        pending = Object.freeze({body: composed, client_message_id: crypto.randomUUID(), hospital_session_id: session, sender_name: '가상 응급대원'});
      }
      controls();
      const request = pending;
      const result = await api(path() + '/messages', request);
      if (!confirms(result, request)) throw new Error('원래 메시지의 저장 결과를 확인하지 못했습니다. 같은 요청으로 재시도하세요.');
      merge([result.message], false); pending = null; draftSession = null; $('message').value = '';
    } catch (failure) { error(suspended ? '앱이 백그라운드로 전환됐습니다. 복귀 후 원래 요청으로 재시도하세요.' : failure.message || '전송 결과를 확인하지 못했습니다.'); }
    finally { sending = false; controls(); }
  });
  $('message').addEventListener('input', () => {
    if (!$('message').value.trim()) draftSession = null;
    else if (draftSession === null) draftSession = snapshot?.active_hospital_session?.id ?? null;
    controls();
  });
  $('review-draft').addEventListener('click', () => { draftSession = snapshot?.active_hospital_session?.id ?? null; controls(); });
  $('discard').addEventListener('click', () => {
    if (sending) return;
    pending = null; controls(); error('재시도 요청만 지웠습니다. 서버에는 이미 저장됐을 수 있으므로 대화를 확인한 뒤 새 메시지를 보내세요.');
  });
  $('chat-stop').addEventListener('click', () => {
    if (sending || operation) return;
    stopped = true; epoch++; clearTimeout(timer); timer = null; abortRequests(); controls();
  });
  $('chat-resume').addEventListener('click', () => { stopped = false; epoch++; controls(); void pollNow(); });
  $('mark-read').addEventListener('click', () => { $('messages').scrollTop = $('messages').scrollHeight; markRead(); });
  $('messages').addEventListener('scroll', () => { if (atBottom()) markRead(); });
  document.addEventListener('quick119:view', () => {
    if (chatVisible()) requestAnimationFrame(() => { if (atBottom()) markRead(); });
    badge();
  });
  function suspend() { suspended = true; epoch++; clearTimeout(timer); timer = null; abortRequests(); controls(); }
  function resume() {
    if (document.hidden) return;
    suspended = false; controls();
    if (pid && !stopped) void pollNow();
    else if (!hospitals.length) void loadHospitals().catch(failure => error(failure.message));
  }
  document.addEventListener('visibilitychange', () => document.hidden ? suspend() : resume());
  window.addEventListener('pagehide', suspend);
  window.addEventListener('pageshow', resume);
  const restored = new URLSearchParams(location.search).get('pid');
  if (restored) { if (idPattern.test(restored)) pid = restored; else error('가상 이송 ID만 열 수 있습니다.'); }
  void loadHospitals().then(() => { if (active()) void pollNow(); }).catch(failure => error(failure.message));
  controls();
})();
