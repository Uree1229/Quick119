/* Empty-draft speech sends on recognition end; existing drafts remain editable. */
(() => {
  'use strict';
  if (!document.getElementById('transport-workspace')) return;
  const $ = name => document.getElementById('transport-' + name);
  const input = $('message'), start = $('voice-start'), stop = $('voice-stop');
  const status = $('voice-status'), interim = $('voice-interim'), root = $('workspace');
  const Speech = window.SpeechRecognition || window.webkitSpeechRecognition;
  const available = window.isSecureContext && !!Speech;
  let enabled = false, listening = false, finishing = false, recognition = null, generation = 0;
  const unavailable = !window.isSecureContext
    ? '앱 마이크는 HTTPS 또는 localhost에서 사용할 수 있습니다. 휴대폰 키보드의 받아쓰기로 입력한 뒤 보내기를 누르거나 직접 입력해 주세요.'
    : '이 브라우저는 음성인식을 지원하지 않습니다. 키보드의 받아쓰기를 사용하거나 메시지를 직접 입력해 주세요.';
  const panel = $('voice-panel');
  let showProblem = false;
  // 브라우저 음성 인식에 넘기는 구급 용어 힌트(Web Speech 문맥 힌트, SpeechRecognitionPhrase).
  // config/paramedic_voice_terms.json의 speech_recognition_hints, 증상 대표어, 있음·없음·미확인이다.
  // 지원하는 브라우저에서만 쓰고, 거부되면 힌트 없이 한 번 다시 시작한다. 정확도 효과는 실제 발화로 따로 재야 한다.
  const HINTS = Object.freeze(["혈압", "수축기 혈압", "이완기 혈압", "맥박", "심박수", "산소포화도", "체온", "호흡수", "혈당", "의식 명료", "호흡 정상", "흉통", "호흡곤란", "구토", "출혈", "백이십에 팔십", "삼십칠 점 이 도", "복통", "두통", "어지럼", "오심", "발열", "기침", "객혈", "경련", "실신", "마비", "의식저하", "설사", "요통", "발진", "외상", "부종", "심계항진", "있음", "없음", "미확인"]);
  const HINT_BOOST = 1.0;   // 명세 기본값. 더 세게 주면 애매한 말을 목록 용어로 바꿔 적을 수 있다.
  let hintsRejected = false;
  window.Quick119SpeechHints = HINTS;
  function render() {
    if (panel) panel.hidden = !listening && !showProblem;
    start.disabled = !enabled || listening || document.hidden || $('chat').hidden;
    if (!available) start.setAttribute('aria-expanded', String(showProblem));
    stop.hidden = !listening;
    stop.disabled = finishing;
    // Presentation hook for the listening panel; recognition state itself lives only here.
    if (root) root.dataset.voice = listening ? (finishing ? 'finishing' : 'listening') : available ? 'idle' : 'unavailable';
  }
  function announce() {
    document.dispatchEvent(new CustomEvent('quick119:voice-state', {detail: {listening}}));
  }
  function clearInterim() { interim.textContent = ''; interim.hidden = true; }
  function cancel(message) {
    if (!listening) return;
    const old = recognition;
    generation++;
    recognition = null; listening = false; finishing = false; clearInterim();
    try { if (old?.abort) old.abort(); else old?.stop(); } catch (_) { /* Already ended. */ }
    showProblem = true; status.textContent = message;
    announce(); render();
  }
  if (!available) {
    start.title = '받아쓰기 안내';
    start.setAttribute('aria-label', start.title);
    start.setAttribute('aria-controls', panel.id);
    status.textContent = unavailable;
  }
  document.addEventListener('quick119:composer-state', event => {
    enabled = !!event.detail.enabled;
    if (!enabled && listening) cancel('대화 상태가 바뀌어 음성 입력을 중단했습니다. 확정된 초안을 확인해 주세요.');
    render();
  });
  document.addEventListener('quick119:session-change', () => {
    cancel('대화 참여자가 변경되어 음성 입력을 중단했습니다. 기존 초안을 확인한 뒤 보내 주세요.');
  });
  document.addEventListener('quick119:before-view', event => {
    if (event.detail.view !== 'chat') cancel('화면을 전환해 음성 입력을 중단했습니다. 확정된 초안은 유지됩니다.');
  });
  document.addEventListener('quick119:view', render);
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) cancel('앱이 백그라운드로 전환되어 음성 입력을 중단했습니다. 초안을 확인해 주세요.');
    render();
  });
  window.addEventListener('pagehide', () => cancel('페이지를 벗어나 음성 입력을 중단했습니다.'));

  function begin() {
    if (!enabled || listening || document.hidden || $('chat').hidden) return;
    // No speech support: the tap only shows or hides the explanation; recognition never starts.
    if (!available) { showProblem = !showProblem; render(); return; }
    const ticket = ++generation, processed = new Set();
    const emptyAtStart = !input.value.trim();
    const auto = emptyAtStart;
    showProblem = false;
    const segments = [];
    let failed = false, unresolved = false;
    try {
      const current = new Speech();
      recognition = current;
      current.lang = 'ko-KR'; current.continuous = !auto; current.interimResults = true; current.maxAlternatives = 3;
      let usedHints = false;
      if (!hintsRejected && window.SpeechRecognitionPhrase && 'phrases' in current) {
        try { current.phrases = HINTS.map(phrase => new window.SpeechRecognitionPhrase(phrase, HINT_BOOST)); usedHints = true; }
        catch (_) { hintsRejected = true; }
      }
      if (root) root.dataset.speechHints = usedHints ? 'applied' : hintsRejected ? 'rejected' : 'unsupported';
      current.onstart = () => {
        if (ticket !== generation || !listening) return;
        status.textContent = auto ? '말씀해 주세요. 인식이 끝나면 자동으로 보냅니다.' : '듣고 있습니다. 인식된 내용을 확인한 뒤 보내세요.';
      };
      current.onresult = event => {
        if (ticket !== generation || !listening) return;
        let provisional = '', final = '';
        for (let i = event.resultIndex; i < event.results.length; i++) {
          const result = event.results[i];
          if (result.isFinal) {
            if (!processed.has(i)) {
              processed.add(i);
              const best = result[0].transcript.trim();
              if (best) {
                final += best + ' ';
                segments.push([best, ...Array.from(result).slice(1).map(r => r.transcript.trim()).filter(Boolean)]);
              }
            }
          } else provisional += result[0].transcript;
        }
        if (final.trim()) {
          const appended = input.value + (input.value && !/\s$/.test(input.value) ? ' ' : '') + final.trim();
          // Do not silently truncate a report; keep it editable and block HTML form submission until corrected.
          input.value = appended;
          input.dispatchEvent(new Event('input', {bubbles: true}));
          if (appended.length > input.maxLength) {
            cancel('입력 한도를 넘었습니다. 확정된 내용은 유지했으니 2,000자 이내로 나누어 보내 주세요.');
            return;
          }
        }
        unresolved = !!provisional.trim();
        interim.textContent = provisional ? '인식 중 · 아직 초안에 반영하지 않음: ' + provisional : '';
        interim.hidden = !provisional;
      };
      current.onerror = event => {
        if (ticket !== generation) return;
        if (event.error === 'phrases-not-supported' && usedHints && !hintsRejected) {
          // 명세상 시작할 때 오는 오류다. 힌트 없이 한 번만 다시 시작하고, 다른 오류는 다시 시도하지 않는다.
          hintsRejected = true;
          cancel('이 브라우저는 용어 힌트를 지원하지 않아 일반 음성 인식으로 다시 시작합니다.');
          begin();
          return;
        }
        failed = true;
        const messages = {'not-allowed': '마이크 권한이 거부되었습니다.', 'audio-capture': '마이크를 사용할 수 없습니다.',
          'no-speech': '음성을 인식하지 못했습니다.', network: '음성인식 서비스에 연결하지 못했습니다.'};
        cancel((messages[event.error] || '음성인식을 완료하지 못했습니다.') + ' 확정된 초안은 유지됩니다. 직접 입력하거나 다시 시도해 주세요.');
      };
      current.onend = () => {
        if (ticket !== generation || !listening) return;
        listening = false; finishing = false; recognition = null; clearInterim();
        if (!failed) {
          showProblem = unresolved || !segments.length;
          status.textContent = showProblem ? '인식이 끝난 내용을 확인하거나 다시 말해 주세요.' : '';
        }
        announce(); render();
        if (!failed && segments.length && !unresolved) {
          const alternatives = [];
          for (const [i, segment] of segments.entries()) {
            for (const other of segment.slice(1)) {
              const phrase = segments.map((part, index) => index === i ? other : part[0]).join(' ');
              if (phrase !== input.value.trim() && !alternatives.includes(phrase)) alternatives.push(phrase);
            }
          }
          const contact = emptyAtStart && window.Quick119Intake?.isHospitalCall(input.value.trim());
          document.dispatchEvent(new CustomEvent('quick119:speech-final', {detail: {
            text: input.value.trim(), alternatives: auto || contact ? alternatives : [], submit: auto || !!contact
          }}));
        }
      };
      listening = true; finishing = false; clearInterim(); announce(); render();
      current.start();
    } catch (_) {
      cancel('음성 입력을 시작하지 못했습니다. 직접 입력하거나 다시 시도해 주세요.');
      // Constructor failure precedes listening=true.
      if (!listening) { showProblem = true; status.textContent = '음성 입력을 시작하지 못했습니다. 직접 입력하거나 다시 시도해 주세요.'; render(); }
    }
  }
  start.addEventListener('click', begin);
  stop.addEventListener('click', () => {
    if (!listening || finishing) return;
    finishing = true; status.textContent = '음성 입력 종료 중 · 확정 결과를 기다리고 있습니다.'; render();
    try { recognition.stop(); }
    catch (_) { cancel('음성 입력을 종료했습니다. 확정된 초안을 확인해 주세요.'); }
  });
  render();
})();
