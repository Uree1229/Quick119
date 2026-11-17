/* Existing team contracts. All messages, drafts, cursors and retries live in RAM. */
(() => {
  'use strict';
  if (!document.getElementById('transport-workspace')) return;
  const $ = name => document.getElementById('transport-' + name);
  const idPattern = /^Q119-TEST-[A-Za-z0-9-]{1,80}$/;
  let pid = null, creation = null, hospitals = [], snapshot = null;
  let eventCursor = 0, messageCursor = 0, epoch = 0, timer = null, pollTask = null;
  let stopped = false, suspended = document.hidden, operation = false, sending = false, fresh = false;
  let initialRead = true, pending = null, voiceListening = false, hospitalTask = null;
  let candidates = null, searchRevision = null, reviewUI = null;
  // undefined = no draft; null = private system/crew draft. These are not interchangeable.
  let draftSession;
  const items = new Map(), unread = new Set(), controllers = new Set();
  const active = () => !!pid && !stopped && !suspended;
  const path = () => '/api/patients/' + encodeURIComponent(pid);
  const sessionId = () => snapshot?.active_hospital_session?.id ?? null;
  const chatVisible = () => !$('chat').hidden && !document.hidden;
  const atBottom = () => $('messages').scrollHeight - $('messages').scrollTop - $('messages').clientHeight < 45;
  function error(message) { $('error').textContent = message; $('error').hidden = !message; }
  async function api(url, payload, method = payload === undefined ? 'GET' : 'POST') {
    const controller = new AbortController(); controllers.add(controller);
    const timeout = setTimeout(() => controller.abort(), 12000);
    try {
      const response = await fetch(url, {method,
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
    $('toggle').setAttribute('aria-label', $('chat').hidden ? `접수 채팅${unread.size ? ', 새 메시지 ' + unread.size + '개' : ''}` : 'GPS 지도와 병원 선택');
    $('mark-read').hidden = unread.size === 0;
  }
  function markRead() { if (chatVisible()) { unread.clear(); badge(); } }
  function renderMessages() {
    const follow = atBottom();
    const scrollTop = $('messages').scrollTop;
    const ordered = [...items.values()].sort((a, b) => {
      for (let n = 0; n < 4; n++) { const diff = (a.order_key[n] ?? 0) - (b.order_key[n] ?? 0); if (diff) return diff; }
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
    $('intro').hidden = items.size > 0;
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
    return active() && fresh && snapshot?.can_send && snapshot?.conversation?.state === 'OPEN';
  }
  function sentReports() {
    return [...items.values()].filter(item => item.kind === 'message' && item.sender_role === 'patient')
      .sort((a, b) => a.message_id - b.message_id);
  }
  function reviewToken() {
    return JSON.stringify([pid, snapshot?.patient?.version ?? null,
      sentReports().map(item => [item.id, item.body || item.text || ''])]);
  }
  function searchedCurrent() { return searchRevision !== null && searchRevision === reviewToken(); }
  function reviewState() {
    const locked = !!snapshot?.inquiries?.length || !!snapshot?.active_hospital_session || (snapshot?.patient?.state && snapshot.patient.state !== 'WAITING');
    const blockedReason = locked ? '이미 수용 문의가 시작되어 저장 정보 조회와 기존 문의 관리만 할 수 있습니다.'
      : !active() || !fresh || initialRead ? '최신 접수 대화를 확인한 뒤 탐색할 수 있습니다.'
      : operation || sending ? '진행 중인 요청이 끝난 뒤 확인해 주세요.'
      : voiceListening ? '음성 입력을 마치고 보고를 전송한 뒤 탐색해 주세요.'
      : pending ? '저장 여부가 확인되지 않은 메시지를 먼저 확인해 주세요.'
      : $('message').value.trim() ? '작성 중인 메시지는 환자 정보에 포함되지 않습니다. 먼저 전송하거나 비워 주세요.'
      : null;
    return {patient: snapshot?.patient || null, reports: sentReports(), token: reviewToken(), locked,
      blockedReason, searchStatus: searchRevision !== null && !searchedCurrent()
        ? '새 보고나 환자 정보 변경으로 이전 탐색 결과가 오래됐습니다. 문의 전 다시 최종 확인해 주세요.' : null};
  }
  function renderParticipants() {
    const activeSession = snapshot?.active_hospital_session;
    const people = [{id: 'system', role: 'system', name: '시스템'}, {id: 'crew', role: 'crew', name: '구급대원'}];
    if (activeSession) {
      const participant = snapshot.participants?.find(person => person.role === 'hospital'
        && person.hospital_session_id === activeSession.id);
      people.push({id: 'hospital:' + activeSession.id, role: 'hospital',
        name: participant?.name || activeSession.hospital_name || activeSession.hospital_id});
    }
    const signature = JSON.stringify(people);
    if ($('participants').dataset.signature === signature) return;
    $('participants').dataset.signature = signature;
    $('participants').replaceChildren();
    for (const person of people) {
      const node = document.createElement('li'); node.dataset.role = person.role; node.textContent = person.name;
      $('participants').append(node);
    }
  }
  function controls() {
    const staleDraft = draftSession !== undefined && draftSession !== sessionId() && !!$('message').value.trim();
    $('new-case').hidden = !!pid || operation;
    $('new-case').disabled = operation || !!pid || sending || suspended || invalidRestored;
    $('message').disabled = !canCompose() || sending || operation || !!pending || voiceListening;
    $('send').disabled = sending || operation || voiceListening || !active() || (!pending && (!canCompose() || staleDraft));
    $('send').textContent = sending ? '전송 확인 중…' : pending ? '원래 요청으로 재시도' : '메시지 보내기';
    $('discard').hidden = !pending; $('discard').disabled = sending;
    $('review-draft').hidden = !!pending || !staleDraft;
    $('review-draft').disabled = !canCompose() || sending;
    $('pending').textContent = pending ? '전송 결과가 확인되지 않은 요청을 메모리에 보관 중입니다. 원래 병원 참여 구간·내용으로만 재시도합니다.' : '';
    if (!pending && staleDraft) $('pending').textContent = '대화 참여자가 변경됐습니다. 초안을 현재 참여자에게 보내도 되는지 확인해 주세요.';
    $('chat-stop').disabled = !pid || stopped || sending || operation;
    $('chat-resume').hidden = !stopped; $('chat-resume').disabled = suspended;
    $('case').textContent = pid ? '가상 접수: ' + pid : '';
    const hospital = snapshot?.active_hospital_session?.hospital_name;
    let state = !pid ? operation ? '접수 채팅을 준비하고 있습니다.' : '접수 연결을 확인하지 못했습니다. 다시 시도해 주세요.'
      : stopped ? '채팅 연결 종료 · GPS 안내는 유지됩니다.'
      : suspended ? '앱 백그라운드 · 복귀 시 대화를 다시 조회합니다.'
      : !fresh ? '접수 대화 확인 중 · 네트워크를 확인하세요.'
      : snapshot?.conversation?.state === 'CLOSED' ? '이송 대화 종료 · 이전 기록을 조회할 수 있습니다.'
      : hospital ? `${hospital} 참여 중 · 같은 대화에서 보고와 답장을 이어갑니다.`
      : '시스템 · 구급대원 대화 중 · 환자 상태를 보고해 주세요.';
    $('connection').textContent = state; $('chat-status').textContent = state;
    $('message-label').textContent = hospital ? '대화에 보낼 메시지' : '시스템에 보낼 환자 상태';
    $('message').placeholder = hospital ? '참여 병원에 전달할 내용을 입력하세요.' : '환자 상태를 입력하거나 음성으로 보고해 주세요.';
    $('sharing').textContent = hospital ? '참여 병원은 기존 보고와 현재 대화를 함께 확인합니다.'
      : '목적지를 확정하면 해당 병원이 참여하며, 기존 보고도 함께 공유됩니다.';
    renderParticipants();
    document.querySelectorAll('[data-transport-confirm],[data-transport-inquire]').forEach(button => {
      button.disabled = operation || sending || !active() || !fresh || !!snapshot?.active_hospital_session || snapshot?.conversation?.state === 'CLOSED'
        || (!!button.dataset.transportInquire && (!searchedCurrent() || !!pending || voiceListening || !!$('message').value.trim()));
    });
    reviewUI?.update();
    document.dispatchEvent(new CustomEvent('quick119:composer-state', {detail: {
      enabled: !!canCompose() && !sending && !operation && !pending && !staleDraft,
      session: sessionId(),
    }}));
  }
  function renderHospitals() {
    $('hospitals').replaceChildren();
    for (const hospital of candidates || []) {
      const card = document.createElement('div'); card.className = 'transport-hospital';
      const name = document.createElement('strong'); name.textContent = hospital.name;
      const actions = document.createElement('div'); actions.className = 'transport-actions';
      const route = document.createElement('button'); route.type = 'button'; route.textContent = 'GPS 경로 보기';
      route.addEventListener('click', () => document.dispatchEvent(new CustomEvent('quick119:route-select', {detail: {
        name: hospital.name, lat: hospital.lat, lon: hospital.lon, hospital_id: hospital.id}})));
      const inquire = document.createElement('button'); inquire.type = 'button'; inquire.textContent = '수용 문의';
      inquire.dataset.transportInquire = hospital.id;
      inquire.addEventListener('click', () => runOperation(async () => {
        if (!searchedCurrent() || pending || voiceListening || $('message').value.trim()) throw new Error('최신 보고와 환자 정보를 다시 확인한 뒤 문의해 주세요.');
        if (!await pollNow() || !searchedCurrent()) throw new Error('환자 정보가 변경됐습니다. 다시 최종 확인해 주세요.');
        await api(path() + '/inquiries', {hospital_ids: [hospital.id]});
        await pollNow();
      }));
      const link = document.createElement('a'); link.textContent = '의료진 화면 열기 ↗';
      link.href = '/hospital/?hid=' + encodeURIComponent(hospital.id) + (pid ? '&pid=' + encodeURIComponent(pid) : '');
      link.target = '_blank'; link.rel = 'noopener noreferrer';
      const explanation = document.createElement('p'); explanation.className = 'transport-note';
      explanation.textContent = [...(hospital.reasons || []), ...(hospital.checks || [])].join(' · ')
        .replace(/\bed_bed\b/g, '응급병상').replace(/\bsynthetic\b/g, '가상 이동시간');
      actions.append(route, inquire); card.append(name, explanation, actions, link); $('hospitals').append(card);
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
          const nextSession = data.active_hospital_session?.id ?? null;
          if (sessionId() !== nextSession) document.dispatchEvent(new CustomEvent('quick119:session-change', {
            detail: {previous: sessionId(), session: nextSession}}));
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
  function loadHospitals() {
    if (hospitalTask) return hospitalTask;
    $('reload-hospitals').disabled = true;
    hospitalTask = (async () => {
      try {
        const list = await api('/api/hospitals');
        if (!Array.isArray(list) || !list.length || list.some(h => h.source !== 'synthetic' || !/^H(0[1-9]|10)$/.test(h.id))) throw new Error('가상 병원 목록을 확인할 수 없습니다.');
        hospitals = list; renderHospitals();
        $('reload-hospitals').hidden = true;
      } catch (failure) {
        if (!candidates) $('hospital-status').textContent = '병원 기본 목록을 불러오지 못했습니다. 접수 대화와 최종 확인 후 후보 탐색은 계속할 수 있습니다.';
        $('reload-hospitals').hidden = false;
      } finally { hospitalTask = null; $('reload-hospitals').disabled = false; }
    })();
    return hospitalTask;
  }
  async function searchHospitals(patch, token) {
    const state = reviewState();
    if (state.blockedReason || token !== state.token) throw new Error(state.blockedReason || '보고가 변경됐습니다. 최신 내용을 다시 확인해 주세요.');
    const ticket = epoch, expectedVersion = snapshot.patient.version;
    operation = true; error(''); controls();
    try {
      if (!await pollNow() || token !== reviewToken() || ticket !== epoch) throw new Error('보고나 환자 정보가 변경됐습니다. 최신 내용으로 다시 확인해 주세요.');
      const latest = await api(path());
      if (latest.version !== expectedVersion || latest.inquiries?.length || latest.state !== 'WAITING') throw new Error('환자 정보나 문의 상태가 변경됐습니다. 다시 조회해 주세요.');
      if (ticket !== epoch || !active() || token !== reviewToken()) throw new Error('대화나 보고가 변경됐습니다. 최신 내용을 다시 확인한 뒤 탐색해 주세요.');
      // The reviewed token covers the patient version; preserve all non-edited extra fields.
      const updated = await api(path(), patch, 'PATCH');
      if (ticket !== epoch || !active()) throw new Error('환자 정보 저장 결과를 다시 확인해 주세요.');
      snapshot = {...snapshot, patient: updated};
      candidates = null; searchRevision = null; renderHospitals();
      const revision = reviewToken();
      const result = await api(path() + '/candidates');
      if (!await pollNow() || ticket !== epoch || revision !== reviewToken()) throw new Error('탐색 중 보고가 변경됐습니다. 저장된 정보를 다시 확인해 주세요.');
      if (!Array.isArray(result.candidates)) throw new Error('후보 병원 응답을 확인하지 못했습니다.');
      candidates = result.candidates.slice(0, 3).map(hospital => ({...hospital, id: hospital.hospital_id}));
      searchRevision = revision; renderHospitals();
      $('hospital-status').textContent = candidates.length
        ? `확인한 환자 정보에 따른 상위 후보 ${candidates.length}곳입니다. ${result.note || '병원의 수용 확인이 필요합니다.'}`
        : '현재 조건의 후보 병원이 없습니다. 환자 정보와 자원·진료 분야 조건을 확인해 주세요.';
      document.dispatchEvent(new CustomEvent('quick119:show-view', {detail: {view: 'gps'}}));
    } finally { operation = false; controls(); }
  }
  async function createCase() {
    if (pid || invalidRestored) return;
    const ticket = epoch;
    if (!creation) creation = {id: 'Q119-TEST-' + crypto.randomUUID(), reporter_role: 'transporter', consent: true,
      age: null, symptoms: null, priority_source: 'unknown', lat: 35.1595, lon: 126.8526,
      extra: {synthetic_fixture: true, creation_token: crypto.randomUUID()}};
    let data;
    try { data = await api('/api/patients', creation); }
    catch (failure) {
      if (failure.status !== 409) throw failure;
      data = await api('/api/patients/' + creation.id);
    }
    if (data.id !== creation.id || data.extra?.creation_token !== creation.extra.creation_token) throw new Error('가상 이송 생성 결과를 확인하지 못했습니다.');
    if (ticket !== epoch) return;
    pid = data.id; epoch++; stopped = false;
    const url = new URL(location.href); url.searchParams.set('pid', pid); history.replaceState(null, '', url);
    renderHospitals(); await pollNow();
  }
  $('new-case').addEventListener('click', () => runOperation(createCase));
  $('reload-hospitals').addEventListener('click', () => { void loadHospitals(); });
  function confirms(result, request) {
    const m = result?.message;
    return typeof result?.duplicate === 'boolean' && m?.kind === 'message' && m.sender_role === 'patient'
      && m.client_message_id === request.client_message_id && m.body === request.body
      && m.hospital_session_id === request.hospital_session_id && m.sender_name === request.sender_name
      && Number.isInteger(m.message_id) && m.id === `message:${m.message_id}` && Array.isArray(m.order_key);
  }
  $('message-form').addEventListener('submit', async event => {
    event.preventDefault(); if (sending || operation || voiceListening || !active() || (!pending && !canCompose())) return;
    if (!pending && draftSession !== undefined && draftSession !== sessionId()) return;
    const composed = $('message').value.trim(); if (!pending && !composed) return;
    if (!pending && composed.length > 2000) { error('메시지는 2,000자 이내로 나누어 보내 주세요.'); return; }
    const session = sessionId(), ticket = epoch;
    sending = true; error(''); controls();
    try {
      if (!await pollNow()) throw new Error('최신 대화를 확인한 뒤 다시 전송하세요.');
      if (ticket !== epoch || !active()) throw new Error('대화 연결을 다시 확인한 뒤 전송하세요.');
      if (!pending) {
        if (!canCompose() || sessionId() !== session) throw new Error('대화 참여자가 변경됐습니다. 내용을 확인한 뒤 다시 전송하세요.');
        pending = Object.freeze({body: composed, client_message_id: crypto.randomUUID(), hospital_session_id: session, sender_name: '가상 응급대원'});
      }
      controls();
      const request = pending;
      const result = await api(path() + '/messages', request);
      if (!confirms(result, request)) throw new Error('원래 메시지의 저장 결과를 확인하지 못했습니다. 같은 요청으로 재시도하세요.');
      if (ticket !== epoch || !active()) throw new Error('전송 결과를 다시 확인해야 합니다. 원래 요청으로 재시도해 주세요.');
      merge([result.message, ...(result.system_items || [])], false); pending = null; draftSession = undefined; $('message').value = '';
    } catch (failure) { error(suspended ? '앱이 백그라운드로 전환됐습니다. 복귀 후 원래 요청으로 재시도하세요.' : failure.message || '전송 결과를 확인하지 못했습니다.'); }
    finally { sending = false; controls(); }
  });
  $('message').addEventListener('input', () => {
    if (!$('message').value.trim()) draftSession = undefined;
    else if (draftSession === undefined) draftSession = sessionId();
    controls();
  });
  $('review-draft').addEventListener('click', () => { draftSession = sessionId(); controls(); });
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
  document.addEventListener('quick119:voice-state', event => { voiceListening = !!event.detail.listening; controls(); });
  function suspend() { suspended = true; epoch++; clearTimeout(timer); timer = null; abortRequests(); controls(); }
  function resume() {
    if (document.hidden) return;
    suspended = false; controls();
    if (pid && !stopped) void pollNow();
    else if (!pid && !invalidRestored) void runOperation(createCase);
    if (!hospitals.length) void loadHospitals();
  }
  document.addEventListener('visibilitychange', () => document.hidden ? suspend() : resume());
  window.addEventListener('pagehide', suspend);
  window.addEventListener('pageshow', resume);
  const restored = new URLSearchParams(location.search).get('pid');
  const invalidRestored = !!restored && !idPattern.test(restored);
  if (restored) { if (idPattern.test(restored)) pid = restored; else error('가상 이송 ID만 열 수 있습니다.'); }
  reviewUI = window.Quick119PatientReview?.bind({getState: reviewState, onSearch: searchHospitals}) || null;
  void loadHospitals();
  if (pid) void pollNow();
  else if (!invalidRestored && !suspended) void runOperation(createCase);
  controls();
})();
