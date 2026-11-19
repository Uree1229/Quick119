/* Existing team contracts. All messages, drafts, cursors and retries live in RAM. */
(() => {
  'use strict';
  if (!document.getElementById('transport-workspace')) return;
  function newMessageId() {
    if (crypto.randomUUID) return crypto.randomUUID();
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64;
    bytes[8] = (bytes[8] & 63) | 128;
    const hex = Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
    return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join('-');
  }
  const $ = name => document.getElementById('transport-' + name);
  const root = document.getElementById('transport-workspace');
  const idPattern = /^Q119-TEST-[A-Za-z0-9-]{1,80}$/;
  const seoulIdentity = hospital => hospital?.source_ids?.identity_source === 'seoul_public';
  let seoulOrigin = null;
  const intakeOrigin = () => seoulOrigin || (hospitals.length ? {lat: 35.1595, lon: 126.8526} : null);
  let pid = null, creation = null, hospitals = [], snapshot = null;
  let eventCursor = 0, messageCursor = 0, epoch = 0, timer = null, pollTask = null;
  let suspended = document.hidden, operation = false, sending = false, fresh = false;
  let initialRead = true, catchingUp = false, newWelcome = false, pending = null, voiceListening = false, hospitalTask = null;
  let candidates = null, searchRevision = null, reviewUI = null, intake = null;
  let recognition = null;
  let speechEnabled = false, speechAudio = null, speechAbort = null, finishAudio = null;
  let speechGeneration = 0, speechBusy = false;
  let finishDevice = null;
  const consideredSpeech = new Set(), speechQueue = [];
  const cloudSpeech = root.dataset.ttsCloud === 'enabled';
  // Read the opening question, intake replies, edit notices, and hospital inquiry/transport status changes.
  // Automatic-workflow, policy, monitor, and position bookkeeping stays silent.
  const spokenNotices = new Set(['patient_updated', 'patient_information_amended',
    'inquiry_sent', 'inquiry_created', 'inquiry_retried', 'inquiry_responded', 'inquiry_expired', 'inquiry_cancelled', 'inquiry_question',
    'destination_confirmed', 'destination_changed', 'departed', 'adjustment_needed', 'hospital_keeps',
    'transport_recheck_required', 'transport_recheck_acknowledged', 'transport_alternative_requested',
    'arrival_confirmed', 'patient_cancelled']);
  // 10 ms of silence played inside the toggle tap so later audio may start without a gesture (iOS/Android).
  const silentWav = 'data:audio/wav;base64,UklGRsQAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YaAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA';
  const serverSpeech = () => typeof window.Audio === 'function' && typeof URL.createObjectURL === 'function';
  const deviceSpeech = () => !!window.speechSynthesis && typeof window.SpeechSynthesisUtterance === 'function';
  function koreanVoice() {
    const voices = window.speechSynthesis?.getVoices?.() || [];
    const korean = voices.filter(voice => /^ko(?:-|$)/i.test(voice.lang || ''));
    const pick = list => list.find(voice => /^ko-KR$/i.test(voice.lang) && voice.default)
      || list.find(voice => /^ko-KR$/i.test(voice.lang))
      || list.find(voice => voice.default) || list[0];
    return pick(korean.filter(voice => voice.localService === true)) || (cloudSpeech && pick(korean)) || null;
  }
  const missingKoreanVoice = '기기에서 한국어 음성을 찾지 못했습니다. 기기 설정의 텍스트 음성 변환에서 한국어 음성을 설치해 주세요.';
  // Routine on/off states stay screen-reader only; problems the user must act on are shown.
  function speechStatus(message, alert = false) {
    $('tts-status').textContent = message;
    $('tts-status').dataset.alert = String(alert);
  }
  function stopSpeech() {
    speechGeneration++; speechBusy = false; speechQueue.length = 0;
    speechAbort?.abort(); speechAbort = null;
    const finish = finishAudio; finishAudio = null;
    if (finish) { speechAudio?.pause?.(); finish(); }
    if (finishDevice) {
      // Some engines never emit an end/error event on cancel.
      finishDevice();
      window.speechSynthesis.cancel();
    }
  }
  function blockedSpeech() {
    speechQueue.length = 0;
    speechEnabled = false; $('tts-toggle').setAttribute('aria-pressed', 'false');
    speechStatus('자동 재생이 차단됐습니다. 스피커 버튼을 다시 눌러 활성화해 주세요.', true);
  }
  function speak(text) {
    if (!speechEnabled || !active() || !chatVisible() || voiceListening
        || typeof text !== 'string' || !text.trim()) return;
    speechQueue.push(text.trim());
    if (!speechBusy) drainSpeech();
  }
  function readSystem(id, text, live) {
    if (consideredSpeech.has(id)) return;
    consideredSpeech.add(id);
    if (live) speak(text);
  }
  async function drainSpeech() {
    speechBusy = true;
    const generation = speechGeneration;
    while (speechQueue.length && generation === speechGeneration) {
      const text = speechQueue.shift();
      const played = serverSpeech() && await speakServer(text, generation);
      if (!played && generation === speechGeneration) await speakDevice(text);
    }
    if (generation === speechGeneration) speechBusy = false;
  }
  // The PC renders Korean audio, so phones need no installed voice; false means fall back to the device.
  async function speakServer(text, generation) {
    let url = null, done = null;
    try {
      speechAbort = new AbortController();
      const response = await fetch('/api/transport-tts', {method: 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify({text}), signal: speechAbort.signal});
      if (!response.ok) return false;
      const audio = await response.blob();
      if (generation !== speechGeneration) return true;
      url = URL.createObjectURL(audio);
      speechAudio ||= new window.Audio();
      await new Promise((resolve, reject) => {
        done = finishAudio = resolve;
        speechAudio.onended = resolve;
        speechAudio.onerror = () => reject(new Error('audio playback failed'));
        speechAudio.src = url;
        Promise.resolve(speechAudio.play()).catch(reject);
      });
      return true;
    } catch (error) {
      if (generation !== speechGeneration) return true;
      if (error?.name === 'NotAllowedError') { blockedSpeech(); return true; }
      return false;
    } finally {
      if (finishAudio === done) finishAudio = null;
      if (url) URL.revokeObjectURL(url);
    }
  }
  function speakDevice(text) {
    const synth = window.speechSynthesis, Utterance = window.SpeechSynthesisUtterance;
    if (!deviceSpeech()) {
      speechStatus('이 브라우저는 시스템 메시지 낭독을 지원하지 않습니다.', true);
      return;
    }
    const voice = koreanVoice();
    if (!voice && !cloudSpeech) {
      speechStatus(missingKoreanVoice, true);
      return;
    }
    const utterance = new Utterance(text);
    if (voice) utterance.voice = voice;
    utterance.lang = voice?.lang || 'ko-KR'; utterance.rate = 1.15;
    return new Promise(resolve => {
      function finish(event) {
        if (finishDevice !== finish) return;
        finishDevice = null;
        utterance.onend = utterance.onerror = null;
        const error = event?.error;
        if (error === 'not-allowed') blockedSpeech();
        else if (error === 'language-unavailable' || error === 'voice-unavailable') speechStatus(missingKoreanVoice, true);
        else if (error && error !== 'canceled' && error !== 'interrupted') speechStatus('시스템 메시지를 읽지 못했습니다.', true);
        resolve();
      }
      finishDevice = finish;
      utterance.onend = utterance.onerror = finish;
      try { synth.speak(utterance); }
      catch { finish({error: 'synthesis-failed'}); }
    });
  }
  function updateSpeechVoices() {
    if (!speechEnabled || !koreanVoice()) return;
    if ($('tts-status').textContent === missingKoreanVoice) {
      speechStatus('기기 한국어 음성을 사용할 수 있습니다.');
    }
  }
  $('tts-toggle').addEventListener('click', () => {
    speechEnabled = !speechEnabled;
    $('tts-toggle').setAttribute('aria-pressed', String(speechEnabled));
    if (!speechEnabled) {
      stopSpeech();
      speechStatus('시스템 메시지 낭독이 꺼져 있습니다.');
    } else if (serverSpeech()) {
      speechAudio ||= new window.Audio();
      speechAudio.src = silentWav;
      try { Promise.resolve(speechAudio.play()).catch(() => {}); } catch {}
      speechStatus('새 시스템 메시지를 읽습니다.');
    } else if (!deviceSpeech()) {
      speechStatus('이 브라우저는 시스템 메시지 낭독을 지원하지 않습니다.', true);
    } else if (!cloudSpeech && !koreanVoice()) {
      speechStatus(missingKoreanVoice, true);
    } else speechStatus('새 시스템 메시지를 읽습니다.');
    // Before the first report the opening question is still pending, so turning speech on asks it.
    const greeting = items.get('intake-welcome');
    if (speechEnabled && greeting && ![...items.values()].some(item => item.kind === 'message' || item.kind === 'command')) {
      speak(greeting.text);
    }
  });
  if (window.speechSynthesis?.addEventListener) {
    window.speechSynthesis.addEventListener('voiceschanged', updateSpeechVoices);
  } else if (window.speechSynthesis) window.speechSynthesis.onvoiceschanged = updateSpeechVoices;
  speechStatus('시스템 메시지 낭독이 꺼져 있습니다.');
  function intakeEnabled() {
    return intake && snapshot?.intake_commands && !reviewState().locked;
  }
  // undefined = no draft; null = private system/crew draft. These are not interchangeable.
  let draftSession;
  const items = new Map(), unread = new Set(), controllers = new Set();
  // Presentation only: rendered order, map-pin signature and candidate cards (never sent anywhere).
  let renderedIds = [], candidateSignature = '[]', caseOrigin = null;
  const candidateCards = new Map();
  const inquiryLabels = {PENDING: '수용 문의 중', CHECKING: '의료진 확인 중', ACCEPTED: '수용 가능 응답', CONFIRMED: '목적지 확정',
    DECLINED: '수용 불가', EXPIRED: '수용 응답 만료', CANCELLED: '문의 종료'};
  const inquiryPriority = {CONFIRMED: 0, ACCEPTED: 1};
  const openInquiryStates = new Set(['PENDING', 'CHECKING', 'ACCEPTED', 'CONFIRMED']);
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  const active = () => !!pid && !suspended;
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
    $('toggle').setAttribute('aria-label', $('chat').hidden ? `채팅으로 전환${unread.size ? ', 새 의료진 메시지 ' + unread.size + '개' : ''}` : '지도로 전환 · 후보 병원과 경로');
    $('mark-read').hidden = unread.size === 0;
  }
  function markRead() { if (chatVisible()) { unread.clear(); badge(); } }
  const hiddenSystemEvents = new Set(['patient_created', 'intake_report_received']);
  function clock(at) {
    const date = new Date(at * 1000);
    return Number.isFinite(date.getTime()) ? date.toLocaleTimeString('ko-KR', {hour: 'numeric', minute: '2-digit'}) : '';
  }
  function includeIntakeGreeting(page) {
    const id = 'intake-welcome';
    if (items.has(id)) return;
    const created = page.items.find(item => item.kind === 'system' && item.type === 'patient_created');
    // Older private rooms may have no creation event. Wait for the last page before adding their opening line.
    if (!created && (page.has_more || page.active_hospital_session || page.conversation?.state !== 'OPEN')) return;
    items.set(id, {id, kind: 'system', sender_role: 'system', sender_name: '시스템',
      text: '환자 상태를 알려주세요.', at: created?.at, order_key: [-1]});
    readSystem(id, items.get(id).text, newWelcome);
  }
  function messageNode(item) {
    const node = el('div', 'transport-message'); node.id = 'transport-item-' + item.id;
    node.dataset.role = item.sender_role; node.dataset.itemId = item.id;
    node.dataset.kind = item.kind === 'system' && item.type ? 'notice' : item.kind;
    // Plain text only: server text never becomes markup.
    node.append(el('small', 'transport-sender', item.sender_name || '시스템'),
      el('p', 'transport-bubble', item.text || item.body || ''), el('small', 'transport-time', clock(item.at)));
    return node;
  }
  function renderMessages() {
    const follow = atBottom();
    const scrollTop = $('messages').scrollTop;
    const ordered = [...items.values()].sort((a, b) => {
      for (let n = 0; n < 4; n++) { const diff = (a.order_key[n] ?? 0) - (b.order_key[n] ?? 0); if (diff) return diff; }
      return 0;
    });
    // Reordering streams must not duplicate nodes or destroy the draft/focus. Only a new tail is appended,
    // so existing bubbles are not moved (moving would replay their entrance animation).
    const ids = ordered.map(item => item.id);
    const kept = renderedIds.length <= ids.length && renderedIds.every((id, index) => ids[index] === id) ? renderedIds.length : 0;
    let previous = null;
    ordered.forEach((item, index) => {
      const node = document.getElementById('transport-item-' + item.id) || messageNode(item);
      const group = node.dataset.kind === 'notice' ? null : `${item.sender_role}|${item.sender_name || ''}`;
      node.dataset.cont = String(!!group && previous?.group === group && item.at - previous.at < 300);
      previous = {group, at: item.at};
      if (index >= kept) $('messages').append(node);
    });
    renderedIds = ids;
    if (chatVisible() && follow) { $('messages').scrollTop = $('messages').scrollHeight; markRead(); }
    else $('messages').scrollTop = scrollTop;
  }
  function merge(incoming, countNew, live = false) {
    const following = chatVisible() && atBottom();
    for (const item of incoming) {
      if (items.has(item.id)) continue;
      if (item.kind === 'system' && item.sender_role === 'system') {
        if (item.type === 'crew_intake_reply') readSystem(item.id, item.data?.reply, live);
        else if (spokenNotices.has(item.type)) readSystem(item.id, item.text, live);
      }
      if (item.kind === 'system' && hiddenSystemEvents.has(item.type)) continue;
      if (item.kind === 'system' && item.type === 'crew_intake_reply') {
        // Commands are private UI turns, separate from the clinical reports shared with a hospital.
        if (item.data?.command_text) {
          const id = item.id + '-command';
          items.set(id, {id, kind: 'command', sender_role: 'patient', sender_name: '구급대원',
            text: item.data.command_text, at: item.at, order_key: item.order_key});
        }
        items.set(item.id, {...item, type: null, text: item.data.reply, order_key: [...item.order_key, 1]});
      } else items.set(item.id, item);
      if (countNew && item.kind === 'message' && item.sender_role === 'hospital' && !following) unread.add(item.id);
    }
    renderMessages(); badge();
  }
  // Typing needs an open conversation; sending also needs the latest refresh. A missed 3-second refresh therefore
  // blocks sending only: the draft stays editable and the soft keyboard is not dropped mid-sentence.
  function canDraft() {
    return active() && snapshot?.can_send && snapshot?.conversation?.state === 'OPEN';
  }
  function canCompose() {
    return canDraft() && fresh;
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
    // 'busy' reasons last only while a refresh or request runs; the next-step bar keeps them for screen readers.
    const busy = !locked && (!active() || !fresh || initialRead || operation || sending);
    return {patient: snapshot?.patient || null, reports: sentReports(), token: reviewToken(), locked,
      blockedReason, blockedKind: blockedReason ? (busy ? 'busy' : 'blocked') : null, searchStatus: searchRevision !== null && !searchedCurrent()
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
      node.title = person.name;
      $('participants').append(node);
    }
  }
  // Progress shown in the header (보고 → 탐색 → 문의 → 확정), derived only from state already on this page.
  function stage() {
    if (snapshot?.conversation?.state === 'CLOSED') return ['closed', 4];
    if (snapshot?.active_hospital_session) return ['joined', 4];
    const inquiries = snapshot?.inquiries || [];
    if (inquiries.some(q => q.state === 'ACCEPTED' && q.usable)) return ['accepted', 3];
    if (inquiries.length) return ['inquiry', 2];
    if (candidates) return ['candidates', 2];
    if (sentReports().length) return ['review', 1];
    return ['report', 0];
  }
  function renderStage(connection) {
    const [name, step] = stage();
    if (root) { root.dataset.stage = name; root.dataset.connection = connection; root.dataset.pending = String(!!pending); }
    [...($('steps')?.children || [])].forEach((node, index) => {
      node.dataset.state = index < step ? 'done' : index === step ? 'current' : 'todo';
      if (index === step) node.setAttribute('aria-current', 'step'); else node.removeAttribute('aria-current');
    });
    if ($('next-title')) $('next-title').textContent = name === 'candidates' ? '후보 병원에 수용 문의하세요'
      : name === 'review' ? '보고 완료 · 병원 탐색' : '병원 탐색';
    if ($('start-search')) $('start-search').textContent = name === 'candidates' ? '정보 확인 후 다시 탐색' : '병원 탐색 시작';
  }
  let contactSession;
  function showHospitalContact() {
    const session = snapshot.active_hospital_session;
    const hospital = hospitals.find(item => item.id === session?.hospital_id);
    const number = seoulIdentity(hospital) ? null : window.Quick119Intake.dialNumber(hospital?.phone);
    const name = session?.hospital_name || hospital?.name || session?.hospital_id;
    const box = $('hospital-contact');
    box.replaceChildren();
    if (session && number) {
      const link = el('a', 'tw-btn tw-btn-cta', name + ' 전화 걸기');
      link.href = 'tel:' + number;
      link.addEventListener('click', event => {
        if (!fresh || !active() || session.id !== sessionId()) event.preventDefault();
      });
      box.append(link);
    } else {
      box.textContent = seoulIdentity(hospital) ? '시연 담당자와 이송 채팅을 사용할 수 있습니다.'
        : session ? name + '에 등록된 통화 가능 번호가 없습니다.' : '목적지로 확정된 병원이 아직 없습니다.';
    }
    contactSession = sessionId();
    box.hidden = false;
    if ($('intake-feedback')) $('intake-feedback').hidden = true;
  }
  function controls() {
    if ($('hospital-contact') && (!fresh || !active() || contactSession !== sessionId())) $('hospital-contact').hidden = true;
    const staleDraft = draftSession !== undefined && draftSession !== sessionId() && !!$('message').value.trim();
    $('new-case').hidden = !!pid || operation;
    $('new-case').disabled = operation || !!pid || sending || suspended || invalidRestored;
    $('message').disabled = !canDraft() || sending || operation || !!pending || voiceListening;
    $('send').disabled = sending || operation || voiceListening || !active() || (!pending && (!canCompose() || staleDraft));
    $('send').textContent = sending ? '확인 중…' : pending ? '재시도' : '보내기';
    $('discard').hidden = !pending; $('discard').disabled = sending;
    $('review-draft').hidden = !!pending || !staleDraft;
    $('review-draft').disabled = !canCompose() || sending;
    $('pending').textContent = pending ? '전송 결과가 확인되지 않은 요청을 메모리에 보관 중입니다. 원래 병원 참여 구간·내용으로만 재시도합니다.' : '';
    if (!pending && staleDraft) $('pending').textContent = '대화 참여자가 변경됐습니다. 초안을 현재 참여자에게 보내도 되는지 확인해 주세요.';
    const hospital = snapshot?.active_hospital_session?.hospital_name;
    const depart = $('depart');
    depart.hidden = !sessionId() || snapshot?.conversation?.state !== 'OPEN' || snapshot.patient?.state !== 'CONFIRMED';
    depart.disabled = depart.hidden || !active() || !fresh || operation || sending || voiceListening;
    let state = !pid ? operation ? '접수 채팅을 준비하고 있습니다.' : '접수 연결을 확인하지 못했습니다. 다시 시도해 주세요.'
      : suspended ? '앱 백그라운드 · 복귀 시 대화를 다시 조회합니다.'
      : !fresh ? '접수 대화 확인 중 · 네트워크를 확인하세요.'
      : snapshot?.conversation?.state === 'CLOSED' ? '이송 대화 종료 · 이전 기록을 조회할 수 있습니다.'
      : hospital ? `${hospital} 참여 중 · 같은 대화에서 보고와 답장을 이어갑니다.`
      : '시스템 · 구급대원 대화 중 · 환자 상태를 보고해 주세요.';
    $('connection').textContent = state; $('chat-status').textContent = state;
    $('message-label').textContent = hospital ? '대화에 보낼 메시지' : '시스템에 보낼 환자 상태';
    // Short enough for one line beside the mic and send buttons; the label above names the recipient.
    $('message').placeholder = hospital ? '병원에 메시지 입력' : '환자 상태 입력';
    renderParticipants();
    // A hospital that already has an open inquiry cannot be asked twice from its card.
    const asked = new Set((snapshot?.inquiries || []).filter(q => openInquiryStates.has(q.state)).map(q => q.hospital_id));
    document.querySelectorAll('[data-transport-confirm],[data-transport-inquire]').forEach(button => {
      button.disabled = operation || sending || !active() || !fresh || !!snapshot?.active_hospital_session || snapshot?.conversation?.state === 'CLOSED'
        || (!!button.dataset.transportInquire && (!searchedCurrent() || !!pending || voiceListening || !!$('message').value.trim()
          || asked.has(button.dataset.transportInquire)));
    });
    renderStage(!pid ? (operation ? 'pending' : 'error') : suspended ? 'paused'
      : !fresh ? 'pending' : snapshot?.conversation?.state === 'CLOSED' ? 'closed' : 'live');
    renderDestination();
    reviewUI?.update();
    document.dispatchEvent(new CustomEvent('quick119:composer-state', {detail: {
      enabled: !!canCompose() && !sending && !operation && !pending && !staleDraft,
      session: sessionId(), intake: !!intakeEnabled(),
    }}));
  }
  const resourceNames = {ed_bed: '응급병상', icu_bed: '중환자 병상', special_team: '전문팀'};
  const readable = text => String(text).replace(/\b(ed_bed|icu_bed|special_team)\b/g, key => resourceNames[key])
    .replace(/\bsynthetic(_matrix)?\b/g, '가상 이동시간');
  function bedFor(id) {
    const bed = hospitals.find(h => h.id === id)?.resources?.find?.(r => r.resource_type === 'ed_bed');
    return bed && Number.isFinite(bed.free) ? bed : null;
  }
  function selectRoute(hospital, retry = false) {
    const identity = hospitals.find(h => h.id === hospital.id);
    const detail = {name: hospital.name, lat: hospital.lat, lon: hospital.lon};
    if (!retry) detail.hospital_id = hospital.id;
    if (seoulIdentity(identity)) detail.demoOrigin = seoulOrigin;
    document.dispatchEvent(new CustomEvent('quick119:route-select', {detail}));
  }
  function renderHospitals() {
    $('hospitals').replaceChildren(); candidateCards.clear();
    (candidates || []).forEach((hospital, index) => {
      const card = el('article', 'transport-hospital'); card.id = 'transport-cand-' + hospital.id;
      card.dataset.candidateId = hospital.id;
      const rank = el('span', 'tw-rank', String(index + 1)); rank.setAttribute('aria-hidden', 'true');
      const titles = el('div', 'tw-cand-titles');
      titles.append(el('span', 'tw-sr', `${index + 1}순위 후보 `));
      const bed = bedFor(hospital.id), meters = Number(hospital.travel?.distance_meters);
      const facts = [bed ? `시연 병상 여유 ${bed.free}${Number.isFinite(bed.capacity) ? '/' + bed.capacity : ''}` : null,
        meters > 0 ? `${(meters / 1000).toFixed(1)} km` : null, hospital.resources_enough === false ? '자원 확인 필요' : null].filter(Boolean);
      const state = el('span', 'tw-pill'); state.hidden = true;
      titles.append(el('strong', 'tw-cand-name', hospital.name),
        el('p', 'tw-cand-facts', facts.join(' · ') || '자원 정보는 병원 확인 필요'), state);
      const seconds = Number(hospital.travel?.duration_seconds);
      const eta = el('p', 'tw-eta');
      eta.append(el('strong', '', Number.isFinite(seconds) && seconds >= 0 ? `${Math.max(1, Math.floor(seconds / 60))}분` : '—'),
        el('small', '', '가상 이동'));
      const head = el('div', 'tw-cand-head'); head.append(rank, titles, eta);
      const notes = [...(hospital.reasons || []), ...(hospital.checks || [])].map(readable);
      const why = el('details', 'tw-cand-why');
      const list = el('ul');
      for (const note of notes) list.append(el('li', '', note));
      why.append(el('summary', '', `판단 근거 · 확인 사항 ${notes.length}건`), list);
      why.hidden = !notes.length;
      const route = el('button', 'tw-btn tw-btn-ghost tw-cand-route', '경로'); route.type = 'button';
      route.setAttribute('aria-label', `${hospital.name} 경로`);
      route.addEventListener('click', () => selectRoute(hospital));
      const inquire = el('button', 'tw-btn tw-btn-primary tw-cand-inquire', '수용 문의'); inquire.type = 'button';
      inquire.setAttribute('aria-label', `${hospital.name} 수용 문의`);
      inquire.dataset.transportInquire = hospital.id;
      inquire.addEventListener('click', () => runOperation(async () => {
        if (!searchedCurrent() || pending || voiceListening || $('message').value.trim()) throw new Error('최신 보고와 환자 정보를 다시 확인한 뒤 문의해 주세요.');
        if (!await pollNow() || !searchedCurrent()) throw new Error('환자 정보가 변경됐습니다. 다시 최종 확인해 주세요.');
        await api(path() + '/inquiries', {hospital_ids: [hospital.id]});
        await pollNow();
      }));
      const actions = el('div', 'tw-cand-actions'); actions.append(route, inquire);
      const link = el('a', 'tw-cand-link', '의료진 화면 열기 ↗');
      link.href = '/hospital/?hid=' + encodeURIComponent(hospital.id) + (pid ? '&pid=' + encodeURIComponent(pid) : '');
      link.target = '_blank'; link.rel = 'noopener noreferrer';
      card.append(head, why, actions, link); $('hospitals').append(card);
      candidateCards.set(hospital.id, {card, state});
    });
    renderCandidateStates();
    announceCandidates();
    controls();
  }
  // After confirmation (also after a reload, when candidates are gone) the map sheet offers the destination route.
  function renderDestination() {
    const box = $('destination');
    if (!box) return;
    const session = snapshot?.active_hospital_session;
    const hospital = session ? hospitals.find(h => h.id === session.hospital_id) : null;
    box.hidden = !hospital;
    if (box.dataset.key === (hospital?.id || '')) return;
    box.dataset.key = hospital?.id || ''; box.replaceChildren();
    if (!hospital) return;
    const copy = el('div', 'tw-dest-copy');
    copy.append(el('span', 'tw-dest-label', '이송 목적지'), el('strong', '', hospital.name));
    const go = el('button', 'tw-btn tw-btn-cta tw-dest-go', '경로 보기'); go.type = 'button';
    // No hospital_id on purpose: this card is shown only while no route is displayed (e.g. after a GPS failure),
    // and car-routes.js ignores a repeat selection of the same hospital_id, which would make this a no-op.
    go.addEventListener('click', () => selectRoute(hospital, true));
    const icon = el('span', 'tw-inquiry-icon'); icon.setAttribute('aria-hidden', 'true');
    box.append(icon, copy, go);
  }
  // Inquiry state also shows on the matching candidate card.
  function renderCandidateStates() {
    const latest = new Map((snapshot?.inquiries || []).map(q => [q.hospital_id, q]));
    for (const [id, {card, state}] of candidateCards) {
      const q = latest.get(id);
      card.dataset.inquiry = q?.state || '';
      state.hidden = !q; state.dataset.state = q?.state || ''; state.textContent = q ? inquiryLabels[q.state] || q.state : '';
    }
  }
  // Map pins follow the displayed candidates (top 3) and the case's saved location; no request is made.
  function announceCandidates() {
    const shown = (candidates || []).map((h, index) => ({id: h.id, name: h.name, lat: Number(h.lat), lon: Number(h.lon), rank: index + 1}))
      .filter(h => Number.isFinite(h.lat) && Number.isFinite(h.lon));
    const signature = JSON.stringify(shown.map(h => [h.id, h.lat, h.lon]));
    if (signature === candidateSignature) return;
    candidateSignature = signature;
    document.dispatchEvent(new CustomEvent('quick119:candidates', {detail: {origin: shown.length ? caseOrigin : null, hospitals: shown}}));
  }
  function renderInquiries() {
    const inquiries = [...(snapshot?.inquiries || [])].sort((a, b) =>
      (inquiryPriority[a.state] ?? 2) - (inquiryPriority[b.state] ?? 2));
    const signature = JSON.stringify(inquiries.map(q => [q.id, q.state, q.usable, q.hospital_name || q.hospital_id]));
    if ($('inquiries').dataset.signature === signature) return;
    const focusedId = document.activeElement?.dataset.transportConfirm;
    let restoreFocus;
    $('inquiries').dataset.signature = signature; $('inquiries').replaceChildren();
    for (const q of inquiries) {
      const name = q.hospital_name || q.hospital_id;
      const row = el('div', 'transport-inquiry'); row.dataset.state = q.state;
      const icon = el('span', 'tw-inquiry-icon'); icon.setAttribute('aria-hidden', 'true');
      const copy = el('p', 'tw-inquiry-copy');
      const status = el('span', 'tw-pill', inquiryLabels[q.state] || q.state); status.dataset.state = q.state;
      copy.append(el('strong', '', name), status);
      row.append(icon, copy);
      if (q.state === 'ACCEPTED' && q.usable) {
        const confirm = el('button', 'tw-btn tw-btn-confirm', '목적지 확정'); confirm.type = 'button'; confirm.dataset.transportConfirm = q.id;
        confirm.setAttribute('aria-label', `${name}(으)로 목적지 확정`);
        confirm.addEventListener('click', () => runOperation(async () => {
          await api(path() + '/confirm', {inquiry_id: q.id}); await pollNow();
          const hospital = hospitals.find(h => h.id === q.hospital_id);
          if (hospital) selectRoute(hospital);
        }));
        if (confirm.dataset.transportConfirm === focusedId) restoreFocus = confirm;
        row.append(confirm);
      }
      $('inquiries').append(row);
    }
    restoreFocus?.focus({preventScroll: true});
    renderCandidateStates();
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
          snapshot = data;
          if (initialRead) includeIntakeGreeting(data);
          merge(data.items, !initialRead, !initialRead && !catchingUp);
          eventCursor = data.cursors.event_id; messageCursor = data.cursors.message_id;
          more = data.has_more;
        } while (more);
        initialRead = false; catchingUp = false; newWelcome = false;
        fresh = true; renderInquiries(); controls(); return true;
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
        if (!Array.isArray(list) || !list.length || list.some(h => h.source !== 'synthetic' || !/^H(0[1-9]|10)$/.test(h.id))) throw new Error('시연 병원 목록을 확인할 수 없습니다.');
        if (list.some(seoulIdentity)) {
          const catalog = await api('/static/seoul-demo-hospitals.json');
          if (!Number.isFinite(catalog.origin?.lat) || !Number.isFinite(catalog.origin?.lon)) throw new Error('서울 시연 출발 위치를 확인할 수 없습니다.');
          seoulOrigin = catalog.origin;
        } else {
          seoulOrigin = null;
        }
        hospitals = list;
        renderHospitals();
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
      // Map-only: the conversation view omits coordinates, so keep the saved search origin for the overview pin.
      caseOrigin = Number.isFinite(latest.lat) && Number.isFinite(latest.lon) ? {lat: latest.lat, lon: latest.lon} : null;
      if (!caseOrigin) {
        if (!hospitals.length) await loadHospitals();
        caseOrigin = intakeOrigin();
        if (caseOrigin) patch = {...patch, ...caseOrigin};
      }
      if (ticket !== epoch || !active() || token !== reviewToken()) throw new Error('대화나 보고가 변경됐습니다. 최신 내용을 다시 확인한 뒤 탐색해 주세요.');
      // The reviewed token covers the patient version; preserve all non-edited extra fields.
      const updated = patch === null ? latest : await api(path(), patch, 'PATCH');
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
        ? '' : '현재 조건의 후보 병원이 없습니다. 환자 정보와 자원·진료 분야 조건을 확인해 주세요.';
      document.dispatchEvent(new CustomEvent('quick119:show-view', {detail: {view: 'gps'}}));
    } finally { operation = false; controls(); }
  }
  async function createCase() {
    if (pid || invalidRestored) return;
    const ticket = epoch;
    if (!hospitals.length && !creation) await loadHospitals();
    if (!creation) creation = {id: 'Q119-TEST-' + newMessageId(), reporter_role: 'transporter', consent: true,
      age: null, symptoms: null, priority_source: 'unknown', ...intakeOrigin(),
      extra: {synthetic_fixture: true, creation_token: newMessageId()}};
    let data;
    try { data = await api('/api/patients', creation); newWelcome = true; }
    catch (failure) {
      if (failure.status !== 409) throw failure;
      data = await api('/api/patients/' + creation.id); newWelcome = false;
    }
    if (data.id !== creation.id || data.extra?.creation_token !== creation.extra.creation_token) throw new Error('가상 이송 생성 결과를 확인하지 못했습니다.');
    if (ticket !== epoch) return;
    pid = data.id; epoch++;
    const url = new URL(location.href); url.searchParams.set('pid', pid); history.replaceState(null, '', url);
    renderHospitals(); await pollNow();
  }
  $('new-case').addEventListener('click', () => runOperation(createCase));
  $('reload-hospitals').addEventListener('click', () => { void loadHospitals(); });
  $('depart').addEventListener('click', () => {
    if ($('depart').disabled) return;
    const session = sessionId(), version = snapshot.patient.version, ticket = epoch;
    void runOperation(async () => {
      if (!await pollNow() || ticket !== epoch || !active()) throw new Error('최신 이송 상태를 확인해 주세요.');
      if (session !== sessionId() || version !== snapshot.patient.version || snapshot.patient.state !== 'CONFIRMED')
        throw new Error('환자 상태가 바뀌었습니다. 목적지를 다시 확인해 주세요.');
      try {
        await api(path() + '/depart', {expected_version: version});
      } catch (failure) {
        // A response can be lost after the server records departure. Re-read before offering a retry.
        if (await pollNow() && session === sessionId() && snapshot.patient.state === 'IN_TRANSIT') return;
        throw failure;
      }
      await pollNow();
    });
  });
  function confirms(result, request) {
    const m = result?.message;
    return typeof result?.duplicate === 'boolean' && m?.kind === 'message' && m.sender_role === 'patient'
      && m.client_message_id === request.client_message_id && m.body === request.body
      && m.hospital_session_id === request.hospital_session_id && m.sender_name === request.sender_name
      && Number.isInteger(m.message_id) && m.id === `message:${m.message_id}` && Array.isArray(m.order_key);
  }
  $('message-form').addEventListener('submit', async event => {
    event.preventDefault(); stopSpeech();
    if (sending || operation || voiceListening || !active() || (!pending && !canCompose())) return;
    if (!pending && draftSession !== undefined && draftSession !== sessionId()) return;
    const composed = $('message').value.trim(); if (!pending && !composed) return;
    if (!pending && composed.length > 2000) { error('메시지는 2,000자 이내로 나누어 보내 주세요.'); return; }
    const session = sessionId(), ticket = epoch;
    let receipt;
    sending = true; error(''); controls();
    try {
      if (!await pollNow()) throw new Error('최신 대화를 확인한 뒤 다시 전송하세요.');
      if (ticket !== epoch || !active()) throw new Error('대화 연결을 다시 확인한 뒤 전송하세요.');
      if (!pending) {
        if (!canCompose() || sessionId() !== session) throw new Error('대화 참여자가 변경됐습니다. 내용을 확인한 뒤 다시 전송하세요.');
        if (window.Quick119Intake?.isHospitalCall(composed)) {
          if (recognition?.text === composed && recognition.alternatives?.some(alt => !window.Quick119Intake.isHospitalCall(alt))) {
            recognition = null;
            throw new Error('전화 명령의 인식 후보가 다릅니다. 내용을 확인한 뒤 다시 눌러 주세요.');
          }
          if (session && !hospitals.length) {
            await loadHospitals();
            if (ticket !== epoch || !active() || !fresh || session !== sessionId()) throw new Error('대화 참여자를 다시 확인해 주세요.');
            if (!hospitals.length) throw new Error('병원 연락처를 불러올 수 없습니다. 잠시 후 다시 시도해 주세요.');
          }
          showHospitalContact();
          draftSession = undefined; recognition = null; $('message').value = '';
          return;
        }
        if ($('hospital-contact')) $('hospital-contact').hidden = true;
        const id = newMessageId();
        const input = intakeEnabled() ? await intake.request(composed, id, recognition?.text === composed ? recognition : null) : null;
        pending = Object.freeze({body: composed, client_message_id:id, hospital_session_id:session, sender_name:'가상 응급대원', ...(input ? {input} : {})});
      }
      controls();
      const request = pending;
      const result = request.input ? await api(path() + '/intake-draft', request.input) : await api(path() + '/messages', request);
      if (request.input ? result.input_id !== request.client_message_id : !confirms(result, request)) {
        throw new Error('저장 결과를 확인하지 못했습니다. 같은 요청으로 재시도하세요.');
      }
      if (ticket !== epoch || !active()) throw new Error('전송 결과를 다시 확인해야 합니다. 원래 요청으로 재시도해 주세요.');
      merge([...(result.message ? [result.message] : []), ...(result.system_items || [])], false, !result.duplicate);
      if (request.input) { receipt = result; searchRevision = null; }
      pending = null; draftSession = undefined; recognition = null; $('message').value = '';
    } catch (failure) {
      if (pending?.input && failure.status >= 400 && failure.status < 500) {
        pending = null;
        await intake.refresh().catch(() => {});
      }
      error(suspended ? '앱이 백그라운드로 전환됐습니다. 복귀 후 원래 요청으로 재시도하세요.' : failure.message || '전송 결과를 확인하지 못했습니다.');
    } finally { sending = false; controls(); }
    if (receipt) intake.handle(receipt);
  });
  $('message').addEventListener('input', () => {
    recognition = null;
    if (!$('message').value.trim()) draftSession = undefined;
    else if (draftSession === undefined) draftSession = sessionId();
    controls();
  });
  $('review-draft').addEventListener('click', () => { draftSession = sessionId(); controls(); });
  $('discard').addEventListener('click', () => {
    if (sending) return;
    pending = null; controls(); error('재시도 요청만 지웠습니다. 서버에는 이미 저장됐을 수 있으므로 대화를 확인한 뒤 새 메시지를 보내세요.');
  });
  $('mark-read').addEventListener('click', () => { $('messages').scrollTop = $('messages').scrollHeight; markRead(); });
  $('messages').addEventListener('scroll', () => { if (atBottom()) markRead(); });
  document.addEventListener('quick119:view', event => {
    if (event.detail.view === 'gps') stopSpeech();
    if (chatVisible()) requestAnimationFrame(() => { if (atBottom()) markRead(); });
    badge();
  });
  document.addEventListener('quick119:voice-state', event => {
    voiceListening = !!event.detail.listening;
    if (voiceListening) stopSpeech();
    controls();
  });
  document.addEventListener('quick119:before-view', event => { if (event.detail.view === 'gps') stopSpeech(); });
  function suspend() {
    suspended = true; catchingUp = true; newWelcome = false; stopSpeech();
    epoch++; clearTimeout(timer); timer = null; abortRequests(); controls();
  }
  function resume() {
    if (document.hidden) return;
    suspended = false; catchingUp = true; controls();
    if (pid) void pollNow();
    else if (!invalidRestored) void runOperation(createCase);
    if (!hospitals.length) void loadHospitals();
  }
  document.addEventListener('visibilitychange', () => document.hidden ? suspend() : resume());
  window.addEventListener('pagehide', suspend);
  window.addEventListener('pageshow', resume);
  const restored = new URLSearchParams(location.search).get('pid');
  const invalidRestored = !!restored && !idPattern.test(restored);
  if (restored) { if (idPattern.test(restored)) pid = restored; else error('가상 이송 ID만 열 수 있습니다.'); }
  intake = window.Quick119Intake?.bind({$, api, path, uuid: () => newMessageId(), error, reports: sentReports,
    showItems: entries => merge(entries, false, true),
    canEdit: () => !!intakeEnabled() && !reviewState().blockedReason,
    search: async () => {
      if (!await pollNow()) throw new Error('저장 정보를 다시 조회해 주세요.');
      await searchHospitals(null, reviewToken());
    }}) || null;
  document.addEventListener('quick119:speech-final', event => {
    recognition = event.detail;
    if (event.detail.submit && (intakeEnabled() || window.Quick119Intake?.isHospitalCall(event.detail.text)) && canCompose()) $('message-form').requestSubmit();
  });
  reviewUI = window.Quick119PatientReview?.bind({getState: reviewState, onSearch: searchHospitals,
    openDraft(edit, fallback) {
      if (!snapshot?.intake_commands || !intake) return false;
      void intake.open(edit && !!intakeEnabled(), fallback); return true;
    }}) || null;
  void loadHospitals();
  if (pid) void pollNow();
  else if (!invalidRestored && !suspended) void runOperation(createCase);
  controls();
})();
