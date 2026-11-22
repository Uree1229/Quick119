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
  const activePatientKey = 'quick119.activeTransportPatientId';
  const pendingCreationKey = 'quick119.pendingTransportCreation';
  const speechPreferenceKey = 'quick119.transportTts';
  function localStore() {
    try { return window.localStorage; } catch { return null; }
  }
  function savedPatientId() {
    const value = localStore()?.getItem(activePatientKey);
    return typeof value === 'string' && idPattern.test(value) ? value : null;
  }
  function savePatientId(value) {
    try { localStore()?.setItem(activePatientKey, value); } catch {}
  }
  function clearSavedPatientId() {
    try { localStore()?.removeItem(activePatientKey); } catch {}
  }
  function readPendingCreation() {
    try {
      const value = JSON.parse(localStore()?.getItem(pendingCreationKey) || 'null');
      if (value?.reporter_role === 'transporter' && value?.consent === true
          && idPattern.test(value?.id || '') && value?.extra?.synthetic_fixture === true
          && typeof value?.extra?.creation_token === 'string') return value;
    } catch {}
    return null;
  }
  function storePendingCreation(value) {
    try { localStore()?.setItem(pendingCreationKey, JSON.stringify(value)); } catch {}
  }
  function clearPendingCreation() {
    try { localStore()?.removeItem(pendingCreationKey); } catch {}
  }
  function readSpeechPreference() {
    try { return localStore()?.getItem(speechPreferenceKey) !== 'off'; } catch { return true; }
  }
  const seoulIdentity = hospital => hospital?.source_ids?.identity_source === 'seoul_public';
  let seoulOrigin = null;
  const intakeOrigin = () => seoulOrigin || (hospitals.length ? {lat: 35.1595, lon: 126.8526} : null);
  let pid = null, creation = null, hospitals = [], patientCases = [], snapshot = null;
  let eventCursor = 0, messageCursor = 0, epoch = 0, timer = null, pollTask = null;
  let suspended = document.hidden, operation = false, sending = false, fresh = false;
  let patientListLoaded = false, patientListFailed = false, bootstrapTask = null;
  let initialRead = true, catchingUp = false, newWelcome = false, pending = null, voiceListening = false, hospitalTask = null;
  let candidates = null, searchRevision = null, reviewUI = null, intake = null;
  let recognition = null;
  let speechEnabled = readSpeechPreference(), speechAudio = null, speechAbort = null, finishAudio = null;
  let speechAudioReady = false;
  let speechGeneration = 0, speechBusy = false;
  let finishDevice = null;
  const consideredSpeech = new Set(), speechQueue = [];
  const cloudSpeech = root.dataset.ttsCloud === 'enabled';
  // Routine workflow bookkeeping stays silent. Patient changes, clinical questions, replies and transitions are spoken.
  const spokenNotices = new Set(['patient_updated', 'patient_information_amended',
    'inquiry_responded', 'inquiry_expired', 'inquiry_cancelled', 'inquiry_closed',
    'inquiry_question',
    'destination_confirmed', 'destination_changed', 'departed', 'adjustment_needed', 'hospital_keeps',
    'transport_inquiry_started', 'transport_inquiry_candidates_preserved',
    'transport_inquiry_ready', 'transport_inquiry_exhausted', 'transport_recheck_required',
    'transport_alternative_requested',
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
  function blockedSpeech(text) {
    speechAudioReady = false;
    if (text) speechQueue.unshift(text);
    speechStatus('화면을 한 번 터치하면 시스템 메시지 낭독이 시작됩니다.');
  }
  function speak(text) {
    if (!speechEnabled || !active() || voiceListening
        || typeof text !== 'string' || !text.trim()) return;
    speechQueue.push(text.trim());
    if (speechAudioReady && !speechBusy) drainSpeech();
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
      if (!speechAudioReady) {
        speechBusy = false;
        speechStatus('화면을 한 번 터치하면 시스템 메시지 낭독이 시작됩니다.');
        return;
      }
      const text = speechQueue.shift();
      const played = serverSpeech() && await speakServer(text, generation);
      if (!played && generation === speechGeneration) await speakDevice(text);
      if (!speechAudioReady) { speechBusy = false; return; }
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
      if (error?.name === 'NotAllowedError') { blockedSpeech(text); return true; }
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
        if (error === 'not-allowed') blockedSpeech(text);
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
  function unlockSpeech() {
    if (!speechEnabled || speechAudioReady) return;
    if (serverSpeech()) {
      speechAudio ||= new window.Audio();
      speechAudio.src = silentWav;
      try {
        const play = speechAudio.play();
        Promise.resolve(play).then(() => {
          speechAudio.pause();
          speechAudio.currentTime = 0;
          speechAudioReady = true;
          if (speechQueue.length && !speechBusy) drainSpeech();
        }).catch(() => {
          speechStatus('화면을 한 번 더 터치하면 시스템 메시지 낭독이 시작됩니다.');
        });
      } catch {
        speechStatus('화면을 한 번 더 터치하면 시스템 메시지 낭독이 시작됩니다.');
      }
    } else if (deviceSpeech()) {
      try {
        const unlock = new window.SpeechSynthesisUtterance('');
        unlock.lang = 'ko-KR';
        window.speechSynthesis.speak(unlock);
        speechAudioReady = true;
        if (speechQueue.length && !speechBusy) drainSpeech();
      } catch {
        speechStatus('기기에서 시스템 메시지 낭독을 시작하지 못했습니다.', true);
      }
    }
  }
  document.addEventListener('pointerdown', unlockSpeech, {capture: true});
  $('tts-toggle').addEventListener('click', () => {
    speechEnabled = !speechEnabled;
    try { localStore()?.setItem(speechPreferenceKey, speechEnabled ? 'on' : 'off'); } catch {}
    $('tts-toggle').setAttribute('aria-pressed', String(speechEnabled));
    if (!speechEnabled) {
      stopSpeech();
      speechStatus('시스템 메시지 낭독이 꺼져 있습니다.');
    } else if (serverSpeech()) {
      unlockSpeech();
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
  $('progress-log-toggle').addEventListener('click', () => {
    const dialog = $('progress-log');
    if (typeof dialog.showModal !== 'function') {
      error('이 브라우저에서는 진행 기록을 열 수 없습니다.');
      return;
    }
    renderProgressLog();
    dialog.showModal();
    $('progress-log-toggle').setAttribute('aria-expanded', 'true');
    $('progress-log-close-top').focus();
  });
  $('progress-log-close-top').addEventListener('click', () => $('progress-log').close());
  $('progress-log-close').addEventListener('click', () => $('progress-log').close());
  $('progress-log').addEventListener('close', () => {
    $('progress-log-toggle').setAttribute('aria-expanded', 'false');
  });
  if (window.speechSynthesis?.addEventListener) {
    window.speechSynthesis.addEventListener('voiceschanged', updateSpeechVoices);
  } else if (window.speechSynthesis) window.speechSynthesis.onvoiceschanged = updateSpeechVoices;
  $('tts-toggle').setAttribute('aria-pressed', String(speechEnabled));
  speechStatus(speechEnabled ? '새 시스템 메시지를 읽습니다.' : '시스템 메시지 낭독이 꺼져 있습니다.');
  if (speechEnabled && !serverSpeech() && !deviceSpeech()) {
    speechStatus('이 브라우저는 시스템 메시지 낭독을 지원하지 않습니다.', true);
  }
  function intakeEnabled() {
    return intake && snapshot?.intake_commands && !reviewState().locked;
  }
  // undefined = no draft; null = private system/crew draft. These are not interchangeable.
  let draftSession;
  const items = new Map(), unread = new Set(), controllers = new Set();
  // Presentation only: rendered order, map-pin signature and candidate cards (never sent anywhere).
  let renderedEntryKeys = [], candidateSignature = '[]', hospitalRenderSignature = '', caseOrigin = null, overviewShown = false;
  const candidateCards = new Map();
  const inquiryLabels = {PENDING: '수용 문의 중', CHECKING: '의료진 확인 중', ACCEPTED: '수용 가능 응답', CONFIRMED: '목적지 확정',
    DECLINED: '수용 불가', EXPIRED: '수용 응답 만료', CANCELLED: '문의 종료'};
  const inquiryPriority = {CONFIRMED: 0, ACCEPTED: 1};
  const inquiryNoticeTypes = new Set(['inquiry_sent', 'inquiry_created', 'inquiry_retried',
    'inquiry_responded', 'inquiry_expired', 'inquiry_cancelled', 'inquiry_closed',
    'inquiry_status_changed', 'inquiry_workflow_batch', 'inquiry_workflow_ready',
    'inquiry_workflow_waiting', 'inquiry_workflow_exhausted', 'transport_inquiry_started',
    'transport_inquiry_candidates_preserved', 'transport_inquiry_ready', 'transport_inquiry_exhausted',
    'transport_alternative_requested', 'transport_inquiry_batch', 'transport_inquiry_decision',
    'transport_inquiry_waiting', 'inquiry_skipped_recent_decline']);
  const adjustmentStartTypes = new Set(['adjustment_needed', 'transport_alternative_requested']);
  const adjustmentEndTypes = new Set(['destination_changed', 'hospital_keeps', 'patient_cancelled']);
  const outcomeTypes = new Set(['destination_changed', 'destination_confirmed', 'departed',
    'arrival_confirmed', 'patient_cancelled', 'hospital_keeps']);
  const protectedNoticeTypes = new Set(['inquiry_question',
    'enroute_patient_change_requested', 'enroute_patient_change_decided']);
  const routineReplies = new Set(['확인한 환자 정보를 반영했습니다.', '현재 버전의 환자 정보를 확정했습니다.']);
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
  async function loadTransportPatients() {
    const result = await api('/api/transport/patients');
    if (!Array.isArray(result.patients) || result.patients.some(patient => !idPattern.test(patient?.id || ''))) {
      throw new Error('환자 접수 목록 형식이 올바르지 않습니다.');
    }
    patientCases = result.patients;
    patientListLoaded = true;
    patientListFailed = false;
  }
  function setCurrentPatient(id) {
    pid = id;
    savePatientId(id);
    const url = new URL(location.href);
    url.searchParams.delete('new');
    url.searchParams.set('pid', id);
    history.replaceState(null, '', url);
  }
  async function restoreTransport() {
    if (invalidRestored) return;
    if (!patientListLoaded) {
      patientListFailed = false;
      controls();
      await loadTransportPatients();
    }
    if (pid) {
      if (!patientCases.some(patient => patient.id === pid)) {
        pid = null;
        clearSavedPatientId();
        window.location.assign('/patients');
        return;
      }
      savePatientId(pid);
      await pollNow();
      return;
    }
    const pendingCreation = readPendingCreation();
    if (pendingCreation && patientCases.some(patient => patient.id === pendingCreation.id)) {
      clearPendingCreation();
      setCurrentPatient(pendingCreation.id);
      await pollNow();
      return;
    }
    if (pendingCreation) {
      creation = pendingCreation;
      await runOperation(createCase);
      return;
    }
    if (new URLSearchParams(location.search).get('new') === '1') {
      await runOperation(createCase);
      return;
    }
    const remembered = savedPatientId();
    const patient = patientCases.find(item => item.id === remembered && item.active)
      || patientCases.find(item => item.active)
      || patientCases.find(item => item.id === remembered);
    if (patient) {
      setCurrentPatient(patient.id);
      await pollNow();
    } else {
      renderHospitals();
    }
  }
  function bootstrapTransport() {
    if (bootstrapTask) return bootstrapTask;
    bootstrapTask = restoreTransport().catch(failure => {
      patientListFailed = true;
      error(`접수 기록을 불러오지 못했습니다: ${failure.message}`);
      controls();
    }).finally(() => { bootstrapTask = null; });
    return bootstrapTask;
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
  function isAlternativeAcceptance(inquiry) {
    const currentHospital = snapshot?.active_hospital_session?.hospital_id;
    return !!currentHospital && inquiry?.state === 'ACCEPTED' && inquiry.usable
      && inquiry.hospital_id !== currentHospital;
  }
  function isInquiryProgress(item) {
    return item.kind === 'system' && item.sender_role === 'system' && inquiryNoticeTypes.has(item.type);
  }
  function isSystemNotice(item) {
    return item.kind === 'system' && item.sender_role === 'system' && !!item.type
      && !protectedNoticeTypes.has(item.type) && !inquiryNoticeTypes.has(item.type);
  }
  function eventOrder(item) {
    const value = Number(item.order_key?.[0]);
    return Number.isFinite(value) ? value : Number(item.at) || 0;
  }
  function latestOpenAdjustmentStart() {
    const lifecycle = [...items.values()].filter(item =>
      adjustmentStartTypes.has(item.type) || adjustmentEndTypes.has(item.type));
    const latestStart = Math.max(0, ...lifecycle.filter(item => adjustmentStartTypes.has(item.type)).map(eventOrder));
    const latestEnd = Math.max(0, ...lifecycle.filter(item => adjustmentEndTypes.has(item.type)).map(eventOrder));
    return latestStart > latestEnd ? latestStart : 0;
  }
  function adjustmentIsOpen() {
    return latestOpenAdjustmentStart() > 0;
  }
  function currentAdjustmentReason() {
    const lastEnd = Math.max(0, ...[...items.values()]
      .filter(item => adjustmentEndTypes.has(item.type)).map(eventOrder));
    const notice = [...items.values()].reverse()
      .find(item => item.type === 'adjustment_needed' && eventOrder(item) > lastEnd);
    return typeof notice?.data?.note === 'string' ? notice.data.note.trim() : '';
  }
  function inquirySummary(group) {
    const seen = new Set();
    const updates = [];
    for (const item of group) {
      const hospitalId = item.hospital_id;
      if (!hospitalId || seen.has(hospitalId)) continue;
      seen.add(hospitalId);
      const inquiry = snapshot?.inquiries?.find(entry => entry.hospital_id === hospitalId);
      const name = inquiry?.hospital_name || item.hospital_name || hospitalId;
      const status = inquiry ? inquiryLabels[inquiry.state] || inquiry.state
        : item.type === 'inquiry_skipped_recent_decline' ? `제외(최근 '${item.data?.reason || '수용 불가'}' 거절)` : '';
      updates.push(status ? `${name} ${status}` : item.text);
    }
    if (!updates.length) return group[group.length - 1]?.text || '병원 문의 진행 상황이 갱신됐습니다.';
    const shown = updates.slice(0, 4);
    if (updates.length > shown.length) shown.push(`외 ${updates.length - shown.length}곳`);
    return `${group.some(item => adjustmentStartTypes.has(item.type)) ? '대체병원 문의' : '병원 문의'}: ${shown.join(' · ')}`;
  }
  // Each band describes its own events; only the newest band may carry the live "confirm now" prompt.
  function noticeHeadline(group, isLatest) {
    const activeAdjustmentStart = isLatest ? latestOpenAdjustmentStart() : 0;
    if (activeAdjustmentStart) {
      const hasCandidate = (snapshot?.inquiries || []).some(isAlternativeAcceptance);
      if (hasCandidate) return '수용 가능한 대체병원 확보 · 새 목적지를 확정하세요';
      const activeEvents = group.filter(item => eventOrder(item) >= activeAdjustmentStart);
      if (activeEvents.length) {
        if (activeEvents.some(item => item.type === 'transport_inquiry_ready')) {
          return '수용 가능한 대체병원 확보 · 새 목적지를 확정하세요';
        }
        if (activeEvents.some(item => item.type === 'transport_inquiry_exhausted')) {
          const exhausted = activeEvents.filter(item => item.type === 'transport_inquiry_exhausted');
          return exhausted[exhausted.length - 1].text;
        }
        if (activeEvents.some(item => inquiryNoticeTypes.has(item.type))) return inquirySummary(activeEvents);
        return activeEvents[activeEvents.length - 1].text;
      }
      return group[group.length - 1].text;
    }
    const lastOf = type => [...group].reverse().find(item => item.type === type);
    const outcomes = group.filter(item => outcomeTypes.has(item.type));
    if (outcomes.length) return outcomes[outcomes.length - 1].text;
    if (lastOf('transport_inquiry_ready')) return lastOf('transport_inquiry_ready').text || '대체병원의 수용 응답을 확보했습니다.';
    if (lastOf('transport_inquiry_exhausted')) return lastOf('transport_inquiry_exhausted').text;
    const start = group.find(item => adjustmentStartTypes.has(item.type));
    if (start) return start.text;
    if (group.some(item => inquiryNoticeTypes.has(item.type))) return inquirySummary(group);
    return group[group.length - 1].text;
  }
  function noticeEmphasis(group, prompt) {
    if (prompt) return 'action';
    if (group.some(item => outcomeTypes.has(item.type) || item.type === 'transport_inquiry_ready')) return 'decision';
    if (group.some(item => adjustmentStartTypes.has(item.type))) return 'adjustment';
    if (group.some(item => item.type === 'transport_recheck_required')) return 'attention';
    return 'progress';
  }
  // A run of system events is one thin centred band, like single notices; the full trail lives in 진행 기록.
  function renderNoticeGroup(group, isLatest) {
    const nodeId = 'transport-notices-' + group[0].id;
    const headlineText = noticeHeadline(group, isLatest);
    const prompt = headlineText.endsWith('새 목적지를 확정하세요');
    const reasonNotice = [...group].reverse().find(item => item.type === 'adjustment_needed');
    const reason = prompt ? currentAdjustmentReason()
      : typeof reasonNotice?.data?.note === 'string' ? reasonNotice.data.note.trim() : '';
    const last = group[group.length - 1];
    const signature = JSON.stringify([group.map(item => [item.id, item.text, item.type]), headlineText, reason]);
    let node = document.getElementById(nodeId);
    if (!node) {
      node = el('div', 'tw-system-group');
      node.id = nodeId;
      node.dataset.kind = 'notice-group';
    } else if (node.dataset.signature === signature) return node;
    node.dataset.signature = signature;
    const children = [el('p', 'tw-system-group-headline', headlineText)];
    if (reason) children.push(el('p', 'tw-system-group-reason', `사유: ${reason}`));
    if (last?.at) children.push(el('small', 'transport-time', clock(last.at)));
    node.dataset.emphasis = noticeEmphasis(group, prompt);
    node.replaceChildren(...children);
    return node;
  }
  function progressLogEvents() {
    return [...items.values()].filter(item => item.kind === 'system' && item.sender_role === 'system' && !!item.type)
      .sort((a, b) => eventOrder(a) - eventOrder(b) || String(a.id).localeCompare(String(b.id)));
  }
  function updateAssignmentStripVisibility() {
    const hasDestination = !$('current-destination').hidden;
    const hasInquiries = !!$('inquiries').querySelector('.transport-inquiry');
    $('assignment-strip').hidden = !(hasDestination || hasInquiries);
  }
  function renderProgressLog() {
    const events = progressLogEvents();
    const button = $('progress-log-toggle');
    const count = $('progress-log-count');
    const list = $('progress-log-list');
    const scrollTop = list.scrollTop;
    button.hidden = events.length === 0;
    count.hidden = events.length === 0;
    count.textContent = events.length > 99 ? '99+' : String(events.length);
    const rows = events.map(item => {
      const row = el('li', '', '');
      row.dataset.eventType = item.type;
      const text = item.text || item.type;
      row.append(el('p', '', item.hospital_name ? `${item.hospital_name} · ${text}` : text));
      if (item.at) row.append(el('time', '', clock(item.at)));
      return row;
    });
    if (!rows.length) rows.push(el('li', 'tw-progress-log-empty', '아직 진행 기록이 없습니다.'));
    list.replaceChildren(...rows);
    list.scrollTop = scrollTop;
    updateAssignmentStripVisibility();
  }
  function changeDestinationButton(inquiry, hospital, label = '새 목적지로 확정') {
    const button = el('button', 'tw-confirm-control tw-btn-confirm', label); button.type = 'button';
    button.dataset.transportConfirm = String(inquiry.id); button.dataset.hospitalId = inquiry.hospital_id;
    const name = inquiry.hospital_name || hospital?.name || inquiry.hospital_id;
    const accessibleLabel = label === '새 목적지로 확정' ? `${name}을(를) 새 목적지로 확정` : `${name}으로 목적지 확정`;
    button.setAttribute('aria-label', accessibleLabel);
    button.title = accessibleLabel;
    button.addEventListener('click', () => runOperation(async () => {
      const currentHospital = snapshot?.active_hospital_session?.hospital_id;
      const action = currentHospital && currentHospital !== inquiry.hospital_id ? '/change-destination' : '/confirm';
      await api(path() + action, {inquiry_id: inquiry.id}); await pollNow();
      if (hospital) selectRoute(hospital);
    }));
    return button;
  }
  function confirmedDestinationControl(name) {
    const control = el('span', 'tw-confirm-control tw-confirm-control--checked');
    control.setAttribute('role', 'img');
    control.setAttribute('aria-label', `${name}으로 목적지 확정됨`);
    return control;
  }
  function renderMessages() {
    const follow = atBottom();
    const scrollTop = $('messages').scrollTop;
    const ordered = [...items.values()].sort((a, b) => {
      for (let n = 0; n < 4; n++) { const diff = (a.order_key[n] ?? 0) - (b.order_key[n] ?? 0); if (diff) return diff; }
      return 0;
    });
    const entries = [];
    for (let index = 0; index < ordered.length;) {
      if (hiddenSystemEvents.has(ordered[index].type) || isInquiryProgress(ordered[index])) { index++; continue; }
      if (isSystemNotice(ordered[index])) {
        const group = [];
        while (index < ordered.length) {
          if (hiddenSystemEvents.has(ordered[index].type) || isInquiryProgress(ordered[index])) {
            index++; continue;
          }
          if (!isSystemNotice(ordered[index])) break;
          // A new adjustment or a new destination starts its own band, so earlier outcomes keep their own line.
          if (group.length && (adjustmentStartTypes.has(ordered[index].type)
            || ['destination_confirmed', 'destination_changed', 'hospital_keeps', 'patient_cancelled'].includes(ordered[index].type))) break;
          group.push(ordered[index++]);
        }
        entries.push({key: 'notice:' + group[0].id, group});
      } else {
        entries.push({key: 'item:' + ordered[index].id, item: ordered[index++]});
      }
    }
    // Keep existing nodes in place; a run of notices updates its summary in place as events arrive.
    const keys = entries.map(entry => entry.key);
    const kept = renderedEntryKeys.length <= keys.length && renderedEntryKeys.every((key, index) => keys[index] === key)
      ? renderedEntryKeys.length : 0;
    let previous = null;
    const latestGroup = entries.filter(entry => entry.group).at(-1);
    const visibleNodes = new Set();
    entries.forEach((entry, index) => {
      const node = entry.group
        ? renderNoticeGroup(entry.group, entry === latestGroup)
        : (document.getElementById('transport-item-' + entry.item.id) || messageNode(entry.item));
      visibleNodes.add(node);
      if (entry.item) {
        const group = `${entry.item.sender_role}|${entry.item.sender_name || ''}`;
        node.dataset.cont = String(!!group && previous?.group === group && entry.item.at - previous.at < 300);
        previous = {group, at: entry.item.at};
      } else previous = null;
      const parent = node.parentNode || node.parent;
      if (index >= kept || parent !== $('messages')) $('messages').append(node);
    });
    for (const node of [...$('messages').children]) if (!visibleNodes.has(node)) node.remove();
    renderedEntryKeys = keys;
    if (chatVisible() && follow) { $('messages').scrollTop = $('messages').scrollHeight; markRead(); }
    else $('messages').scrollTop = scrollTop;
  }
  function merge(incoming, countNew, live = false) {
    const following = chatVisible() && atBottom();
    const inquiryResponses = [];
    for (const item of incoming) {
      if (items.has(item.id)) continue;
      if (item.kind === 'system' && item.sender_role === 'system') {
        if (item.type === 'crew_intake_reply') readSystem(item.id, item.data?.reply, live);
        else if (item.type === 'inquiry_responded') inquiryResponses.push(item);
        else if (spokenNotices.has(item.type)) readSystem(item.id, item.text, live);
      }
      if (item.kind === 'system' && item.type === 'crew_intake_reply') {
        // Commands are private UI turns, separate from the clinical reports shared with a hospital.
        if (item.data?.command_text) {
          const id = item.id + '-command';
          items.set(id, {id, kind: 'command', sender_role: 'patient', sender_name: '구급대원',
            text: item.data.command_text, at: item.at, order_key: item.order_key});
        }
        items.set(item.id, {...item, type: routineReplies.has(item.data?.reply) ? item.type : null,
          text: item.data.reply, order_key: [...item.order_key, 1]});
      } else items.set(item.id, item);
      if (countNew && item.kind === 'message' && item.sender_role === 'hospital' && !following) unread.add(item.id);
    }
    if (inquiryResponses.length) {
      const text = inquiryResponses.map(item => {
        const name = item.hospital_name || item.hospital_id || '병원';
        const action = item.data?.action;
        const status = action === 'accept' ? '수용 가능' : action === 'decline' ? '수용 불가'
          : action === 'checking' ? '확인 중' : '응답 등록';
        return `${name} ${status}`;
      }).join(' · ');
      readSystem('inquiry-responses:' + inquiryResponses.map(item => item.id).join(','), `병원 회신: ${text}`, live);
    }
    renderMessages(); renderProgressLog(); badge();
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
    const inquiriesStarted = (snapshot?.inquiries || []).length > 0;
    if ($('next-title')) $('next-title').textContent = name === 'candidates'
      ? inquiriesStarted ? '상위 후보 병원에 자동 문의했습니다' : '서버가 후보 병원에 자동 문의합니다'
      : name === 'review' ? '보고 완료 · 병원 탐색' : '병원 탐색';
    if ($('start-search')) $('start-search').textContent = name === 'candidates'
      ? inquiriesStarted ? '자동 문의 진행 중' : '병원 탐색 시작' : '병원 탐색 시작';
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
    $('new-case').textContent = patientListLoaded ? '새 접수 시작'
      : patientListFailed ? '접수 목록 다시 확인' : '접수 기록 불러오는 중…';
    $('new-case').disabled = operation || !!pid || sending || suspended || invalidRestored
      || (!patientListLoaded && !patientListFailed);
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
    let state = !pid ? operation ? '접수 채팅을 준비하고 있습니다.'
      : patientListLoaded ? '환자를 선택하거나 새 접수를 시작해 주세요.'
      : patientListFailed ? '접수 기록을 불러오지 못했습니다. 다시 시도해 주세요.'
      : '접수 기록을 확인하고 있습니다.'
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
    document.querySelectorAll('[data-transport-confirm]').forEach(button => {
      button.disabled = operation || sending || !active() || !fresh
        || (!!snapshot?.active_hospital_session
          && snapshot.active_hospital_session.hospital_id === button.dataset.hospitalId)
        || snapshot?.conversation?.state === 'CLOSED';
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
  function displayedHospitals() {
    const displayed = [...(candidates || [])], ids = new Set(displayed.map(hospital => hospital.id));
    for (const inquiry of snapshot?.inquiries || []) {
      if (!isAlternativeAcceptance(inquiry) || ids.has(inquiry.hospital_id)) continue;
      const hospital = hospitals.find(item => item.id === inquiry.hospital_id);
      if (hospital) { displayed.push({...hospital, hospital_id: hospital.id, id: hospital.id}); ids.add(hospital.id); }
    }
    if (!displayed.length && patientListLoaded && !snapshot?.active_hospital_session)
      return hospitals.slice(0, 3).map(hospital => ({...hospital, overview: true, reference: true}));
    return displayed;
  }
  function renderHospitals() {
    const displayed = displayedHospitals();
    const overview = displayed.some(hospital => hospital.reference);
    $('hospitals-title').textContent = overview ? '시연 병원' : '후보 병원';
    if (overview) $('hospital-status').textContent = '환자 조건과 무관한 시연 병원입니다. 수용 여부는 확인되지 않았습니다.';
    else if (overviewShown) $('hospital-status').textContent = snapshot?.active_hospital_session
      ? '목적지가 확정됐습니다.' : '환자 정보를 확인한 뒤 병원 탐색을 시작해 주세요.';
    overviewShown = overview;
    const signature = JSON.stringify([overview, snapshot?.active_hospital_session?.hospital_id || '',
      displayed.map(hospital => {
        const inquiry = (snapshot?.inquiries || []).find(q => q.hospital_id === hospital.id);
        return [hospital.id, hospital.lat, hospital.lon, hospital.travel?.duration_seconds,
          inquiry?.state || '', !!inquiry?.usable, !!hospital.overview];
      })]);
    if (signature === hospitalRenderSignature) { renderCandidateStates(); return; }
    hospitalRenderSignature = signature;
    $('hospitals').replaceChildren(); candidateCards.clear();
    displayed.forEach((hospital, index) => {
      const card = el('article', 'transport-hospital'); card.id = 'transport-cand-' + hospital.id;
      if (!hospital.reference) card.dataset.candidateId = hospital.id;
      const rank = el('span', 'tw-rank', String(index + 1)); rank.setAttribute('aria-hidden', 'true');
      const titles = el('div', 'tw-cand-titles');
      titles.append(el('span', 'tw-sr', hospital.overview ? '병원 ' : `${index + 1}순위 후보 `));
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
      const route = el('button', 'tw-btn tw-btn-ghost tw-cand-route', '경로'); route.type = 'button';
      route.setAttribute('aria-label', `${hospital.name} 경로`);
      route.addEventListener('click', () => selectRoute(hospital));
      const side = el('div', 'tw-cand-side'); side.append(eta, route);
      const head = el('div', 'tw-cand-head'); head.append(rank, titles, side);
      const notes = [...(hospital.reasons || []), ...(hospital.checks || [])].map(readable);
      const why = el('details', 'tw-cand-why');
      const list = el('ul');
      for (const note of notes) list.append(el('li', '', note));
      why.append(el('summary', '', `판단 근거 · 확인 사항 ${notes.length}건`), list);
      why.hidden = !notes.length;
      const actions = el('div', 'tw-cand-actions');
      card.append(head, why, actions); $('hospitals').append(card);
      candidateCards.set(hospital.id, {card, state, actions, hospital, actionSignature: ''});
    });
    renderCandidateStates();
    announceCandidates();
    controls();
  }
  // After confirmation (also after a reload, when candidates are gone) the map sheet offers the destination route.
  function renderDestination() {
    const box = $('destination');
    const session = snapshot?.active_hospital_session;
    const hospital = session ? hospitals.find(h => h.id === session.hospital_id) : null;
    if (box) {
      box.hidden = !hospital;
      if (box.dataset.key !== (hospital?.id || '')) {
        box.dataset.key = hospital?.id || ''; box.replaceChildren();
        if (hospital) {
          const copy = el('div', 'tw-dest-copy');
          copy.append(el('span', 'tw-dest-label', '이송 목적지'), el('strong', '', hospital.name));
          const go = el('button', 'tw-btn tw-btn-cta tw-dest-go', '경로 보기'); go.type = 'button';
          // No hospital_id on purpose: car-routes.js ignores repeat selection of the same hospital.
          go.addEventListener('click', () => selectRoute(hospital, true));
          const icon = el('span', 'tw-inquiry-icon'); icon.setAttribute('aria-hidden', 'true');
          box.append(icon, copy, confirmedDestinationControl(hospital.name), go);
        }
      }
    }
    const current = $('current-destination');
    if (!current) return;
    const show = root.dataset.reassignmentActive === 'true' && !!hospital;
    current.hidden = !show;
    const key = show ? hospital.id : '';
    if (current.dataset.key === key) return;
    current.dataset.key = key; current.replaceChildren();
    if (!show) return;
    const copy = el('div', 'tw-current-destination-copy');
    copy.append(el('span', '', '현재 목적지'), el('strong', '', hospital.name));
    current.append(el('span', 'tw-inquiry-icon'), copy);
  }
  // Inquiry state also shows on the matching candidate card.
  function renderCandidateStates() {
    const latest = new Map((snapshot?.inquiries || []).map(q => [q.hospital_id, q]));
    for (const [id, entry] of candidateCards) {
      const {card, state, actions, hospital} = entry;
      const q = latest.get(id);
      card.dataset.inquiry = q?.state || '';
      state.hidden = !q; state.dataset.state = q?.state || ''; state.textContent = q ? inquiryLabels[q.state] || q.state : '';
      const currentDestination = q?.state === 'CONFIRMED'
        && q.hospital_id === snapshot?.active_hospital_session?.hospital_id;
      const actionSignature = isAlternativeAcceptance(q) ? `confirm:${q.id}`
        : currentDestination ? `checked:${q.id}` : '';
      if (actionSignature !== entry.actionSignature) {
        const control = isAlternativeAcceptance(q)
          ? changeDestinationButton(q, hospital, '새 목적지로 확정')
          : currentDestination ? confirmedDestinationControl(hospital.name) : null;
        actions.replaceChildren(...(control ? [control] : []));
        entry.actionSignature = actionSignature;
      }
    }
  }
  // Map pins follow the displayed candidates (top 3) and the case's saved location; no request is made.
  function announceCandidates() {
    const displayed = displayedHospitals();
    if (!displayed.length && snapshot?.active_hospital_session) {
      const hospital = hospitals.find(item => item.id === snapshot.active_hospital_session.hospital_id);
      if (hospital) displayed.push({...hospital, overview: true});
    }
    const shown = displayed.map((h, index) => ({id: h.id, name: h.name, lat: Number(h.lat), lon: Number(h.lon), rank: index + 1, overview: !!h.overview}))
      .filter(h => Number.isFinite(h.lat) && Number.isFinite(h.lon));
    const signature = JSON.stringify(shown.map(h => [h.id, h.lat, h.lon, h.overview]));
    if (signature === candidateSignature) return;
    candidateSignature = signature;
    document.dispatchEvent(new CustomEvent('quick119:candidates', {detail: {origin: shown.length ? caseOrigin : null, hospitals: shown}}));
  }
  function renderInquiries() {
    const allInquiries = snapshot?.inquiries || [];
    const currentHospital = snapshot?.active_hospital_session?.hospital_id;
    const latestByHospital = new Map([...allInquiries]
      .sort((a, b) => Number(a.id) - Number(b.id))
      .filter(q => q.hospital_id)
      .map(q => [q.hospital_id, q]));
    const latestInquiries = [...latestByHospital.values()];
    const alternatives = latestInquiries.filter(q => q.hospital_id !== currentHospital
      && (['PENDING', 'CHECKING'].includes(q.state) || (q.state === 'ACCEPTED' && q.usable)));
    const reassignmentActive = !!currentHospital && adjustmentIsOpen() && alternatives.length > 0;
    const actionNeeded = reassignmentActive && alternatives.some(isAlternativeAcceptance);
    const actionAlert = $('action-alert');
    const reason = currentAdjustmentReason();
    actionAlert.hidden = !actionNeeded;
    actionAlert.textContent = actionNeeded ? `대체병원 수용 가능 · 새 목적지를 확정하세요${reason ? ` · 사유: ${reason}` : ''}` : '';
    if (root) root.dataset.reassignmentActive = String(reassignmentActive);
    const currentDestination = $('current-destination');
    if (currentDestination) currentDestination.hidden = !reassignmentActive;
    $('inquiries').setAttribute('aria-label', reassignmentActive
      ? '현재 목적지와 대체 병원 문의 및 확정' : '수용 문의 및 목적지 확정');
    const currentVersion = snapshot?.patient?.version;
    const currentRound = latestInquiries.filter(q => currentVersion === undefined
      || q.patient_version === undefined || q.patient_version === null || q.patient_version === currentVersion);
    const inquiries = [...(reassignmentActive ? alternatives : currentHospital ? [] : currentRound)].sort((a, b) =>
      (inquiryPriority[a.state] ?? 2) - (inquiryPriority[b.state] ?? 2) || Number(a.id) - Number(b.id));
    const signature = JSON.stringify([reassignmentActive, snapshot?.patient?.state, currentHospital,
      inquiries.map(q => [q.id, q.state, q.usable, q.hospital_name || q.hospital_id])]);
    if ($('inquiries').dataset.signature === signature) {
      renderDestination(); updateAssignmentStripVisibility(); return;
    }
    const focusedId = document.activeElement?.dataset.transportConfirm;
    let restoreFocus;
    $('inquiries').dataset.signature = signature; $('inquiries').replaceChildren();
    const firstHasAction = reassignmentActive && inquiries[0]?.state === 'ACCEPTED' && inquiries[0]?.usable;
    const actionKey = firstHasAction ? String(inquiries[0].id) : '';
    const revealAction = actionKey && $('inquiries').dataset.actionFirst !== actionKey;
    $('inquiries').dataset.actionFirst = actionKey;
    if (reassignmentActive && !firstHasAction) $('inquiries').append(currentDestination);
    else $('assignment-meta').append(currentDestination);
    for (const q of inquiries) {
      const name = q.hospital_name || q.hospital_id;
      const row = el('div', 'transport-inquiry'); row.dataset.state = q.state;
      const icon = el('span', 'tw-inquiry-icon'); icon.setAttribute('aria-hidden', 'true');
      const copy = el('p', 'tw-inquiry-copy');
      const status = el('span', 'tw-pill', inquiryLabels[q.state] || q.state); status.dataset.state = q.state;
      copy.append(el('strong', '', name), status);
      row.append(icon, copy);
      if (q.state === 'ACCEPTED' && q.usable) {
        const changingDestination = isAlternativeAcceptance(q);
        const confirm = changeDestinationButton(q, hospitals.find(h => h.id === q.hospital_id),
          changingDestination ? '새 목적지로 확정' : '목적지 확정');
        if (confirm.dataset.transportConfirm === focusedId) restoreFocus = confirm;
        row.append(confirm);
      }
      $('inquiries').append(row);
      if (firstHasAction && q === inquiries[0]) $('inquiries').append(currentDestination);
    }
    if (revealAction) $('inquiries').scrollLeft = 0;
    restoreFocus?.focus({preventScroll: true});
    renderHospitals(); renderDestination(); updateAssignmentStripVisibility();
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
        if (epoch === ticket.epoch && active()) {
          if (failure.status === 404) {
            try { await loadTransportPatients(); }
            catch (listFailure) {
              fresh = false;
              error(`대화 기록이 없고 접수 목록도 새로 확인하지 못했습니다: ${listFailure.message}`);
              controls();
              return false;
            }
            if (epoch === ticket.epoch && pid === ticket.pid && !patientCases.some(patient => patient.id === ticket.pid)) {
              pid = null; clearSavedPatientId(); clearPendingCreation(); stopSpeech();
              window.location.assign('/patients');
              return false;
            }
          }
          fresh = false;
          error(failure.name === 'AbortError' ? '대화 조회 시간이 초과됐습니다. 다시 조회합니다.' : failure.message);
          controls();
        }
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
        hospitalRenderSignature = '';
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
      const result = await api(path() + '/candidates', {}, 'POST');
      if (!await pollNow() || ticket !== epoch || revision !== reviewToken()) throw new Error('탐색 중 보고가 변경됐습니다. 저장된 정보를 다시 확인해 주세요.');
      if (!Array.isArray(result.candidates)) throw new Error('후보 병원 응답을 확인하지 못했습니다.');
      candidates = result.candidates.slice(0, 3).map(hospital => ({...hospital, id: hospital.hospital_id}));
      searchRevision = revision; renderHospitals();
      $('hospital-status').textContent = candidates.length
        ? '서버가 상위 후보 병원에 수용 문의를 보냈습니다. 회신과 진행 상황은 이송 채팅에 표시됩니다.'
        : '현재 조건에서 자동 문의할 수 있는 후보 병원이 없습니다. 환자 정보와 자원·진료 분야 조건을 확인해 주세요.';
      document.dispatchEvent(new CustomEvent('quick119:show-view', {detail: {view: 'gps'}}));
    } finally { operation = false; controls(); }
  }
  async function createCase() {
    if (pid || invalidRestored) return;
    const ticket = epoch;
    if (!hospitals.length && !creation) await loadHospitals();
    if (!creation) {
      creation = {id: 'Q119-TEST-' + newMessageId(), reporter_role: 'transporter', consent: true,
        age: null, symptoms: null, priority_source: 'unknown', ...intakeOrigin(),
        extra: {synthetic_fixture: true, creation_token: newMessageId()}};
      storePendingCreation(creation);
    }
    let data;
    try { data = await api('/api/patients', creation); newWelcome = true; }
    catch (failure) {
      if (failure.status !== 409) throw failure;
      data = await api('/api/patients/' + creation.id); newWelcome = false;
    }
    if (data.id !== creation.id || data.extra?.creation_token !== creation.extra.creation_token) throw new Error('가상 이송 생성 결과를 확인하지 못했습니다.');
    if (ticket !== epoch) return;
    const createdId = data.id;
    creation = null;
    clearPendingCreation();
    epoch++;
    setCurrentPatient(createdId);
    renderHospitals(); await pollNow();
    void loadTransportPatients().catch(failure => error(`환자 목록을 새로 고치지 못했습니다: ${failure.message}`));
  }
  $('new-case').addEventListener('click', () => {
    if (patientListLoaded) void runOperation(createCase);
    else void bootstrapTransport();
  });
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
    if (chatVisible()) requestAnimationFrame(() => { if (atBottom()) markRead(); });
    badge();
  });
  // 실시간 안내 중 걸러진 GPS 위치를 확정·이송 중 환자의 위치로 서버에 남긴다. 병원 패널의 '위치 조회'가 이 값을 보여 준다.
  // 15초보다 자주 보내지 않고, 30m 넘게 움직였거나 1분이 지났을 때만 보낸다. 실패해도 안내는 계속하고 다음 위치에서 다시 보낸다.
  const POSITION = {MIN_MS: 15000, MIN_MOVE_M: 30, MAX_MS: 60000};
  let positionSent = null, positionSending = false;
  function metersBetween(a, b) {
    const rad = Math.PI / 180, h = Math.sin((b.lat - a.lat) * rad / 2) ** 2 +
      Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin((b.lon - a.lon) * rad / 2) ** 2;
    return 2 * 6371000 * Math.asin(Math.sqrt(h));
  }
  function shouldSendPosition(fix, now) {
    if (!active() || positionSending || !fix || !Number.isFinite(fix.lat) || !Number.isFinite(fix.lon)) return false;
    if (!['CONFIRMED', 'IN_TRANSIT'].includes(snapshot?.patient?.state)) return false;
    if (!positionSent || positionSent.pid !== pid) return true;
    const waited = now - positionSent.at;
    return waited >= POSITION.MIN_MS && (waited >= POSITION.MAX_MS || metersBetween(positionSent, fix) >= POSITION.MIN_MOVE_M);
  }
  document.addEventListener('quick119:gps-fix', event => {
    const fix = event.detail, now = Date.now();
    if (!shouldSendPosition(fix, now)) return;
    const target = pid;
    positionSending = true;
    void api(path() + '/transport-position', {lat: fix.lat, lon: fix.lon}, 'PATCH')
      .then(() => { if (target === pid) positionSent = {pid: target, lat: fix.lat, lon: fix.lon, at: now}; })
      .catch(() => {})
      .finally(() => { positionSending = false; });
  });
  document.addEventListener('quick119:voice-state', event => {
    voiceListening = !!event.detail.listening;
    if (voiceListening) stopSpeech();
    controls();
  });
  function suspend() {
    suspended = true; catchingUp = true; newWelcome = false; stopSpeech();
    epoch++; clearTimeout(timer); timer = null; abortRequests(); controls();
  }
  function resume() {
    if (document.hidden) return;
    suspended = false; catchingUp = true; controls();
    if (!patientListLoaded && !invalidRestored) void bootstrapTransport();
    else if (pid) void pollNow();
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
    if (event.detail.submit && canCompose()) $('message-form').requestSubmit();
  });
  reviewUI = window.Quick119PatientReview?.bind({getState: reviewState, onSearch: searchHospitals,
    openDraft(edit, fallback) {
      if (!snapshot?.intake_commands || !intake) return false;
      void intake.open(edit && !!intakeEnabled(), fallback); return true;
    }}) || null;
  void loadHospitals();
  void bootstrapTransport();
  controls();
})();
