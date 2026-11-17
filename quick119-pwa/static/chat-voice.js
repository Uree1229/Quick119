/* Final speech recognition results become a draft; sending always stays explicit. */
(() => {
  'use strict';
  if (!document.getElementById('transport-workspace')) return;
  const $ = name => document.getElementById('transport-' + name);
  const input = $('message'), start = $('voice-start'), stop = $('voice-stop');
  const status = $('voice-status'), interim = $('voice-interim');
  const Speech = window.SpeechRecognition || window.webkitSpeechRecognition;
  const available = window.isSecureContext && !!Speech;
  let enabled = false, listening = false, finishing = false, recognition = null, generation = 0;
  const unavailable = !window.isSecureContext
    ? '음성 입력에는 HTTPS 또는 localhost 접속이 필요합니다. 메시지를 직접 입력해 주세요.'
    : '이 브라우저는 음성인식을 지원하지 않습니다. 메시지를 직접 입력해 주세요.';
  function render() {
    start.disabled = !available || !enabled || listening || document.hidden || $('chat').hidden;
    stop.hidden = !listening;
    stop.disabled = finishing;
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
    status.textContent = message;
    announce(); render();
  }
  if (!available) status.textContent = unavailable;
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

  start.addEventListener('click', () => {
    if (!available || !enabled || listening || document.hidden || $('chat').hidden) return;
    const ticket = ++generation, processed = new Set();
    let failed = false;
    try {
      const current = new Speech();
      recognition = current;
      current.lang = 'ko-KR'; current.continuous = true; current.interimResults = true;
      current.onstart = () => {
        if (ticket !== generation || !listening) return;
        status.textContent = '듣고 있습니다. 환자 상태를 보고해 주세요. 확인된 내용만 초안에 넣습니다.';
      };
      current.onresult = event => {
        if (ticket !== generation || !listening) return;
        let provisional = '', final = '';
        for (let i = event.resultIndex; i < event.results.length; i++) {
          const result = event.results[i];
          if (result.isFinal) {
            if (!processed.has(i)) { processed.add(i); final += result[0].transcript.trim() + ' '; }
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
        interim.textContent = provisional ? '인식 중 · 아직 초안에 반영하지 않음: ' + provisional : '';
        interim.hidden = !provisional;
      };
      current.onerror = event => {
        if (ticket !== generation) return;
        failed = true;
        const messages = {'not-allowed': '마이크 권한이 거부되었습니다.', 'audio-capture': '마이크를 사용할 수 없습니다.',
          'no-speech': '음성을 인식하지 못했습니다.', network: '음성인식 서비스에 연결하지 못했습니다.'};
        cancel((messages[event.error] || '음성인식을 완료하지 못했습니다.') + ' 확정된 초안은 유지됩니다. 직접 입력하거나 다시 시도해 주세요.');
      };
      current.onend = () => {
        if (ticket !== generation) return;
        listening = false; finishing = false; recognition = null; clearInterim();
        if (!failed) status.textContent = '음성 입력을 마쳤습니다. 초안을 확인하고 메시지 보내기를 눌러 주세요.';
        announce(); render();
      };
      listening = true; finishing = false; clearInterim(); announce(); render();
      current.start();
    } catch (_) {
      cancel('음성 입력을 시작하지 못했습니다. 직접 입력하거나 다시 시도해 주세요.');
      // Constructor failure precedes listening=true.
      if (!listening) { status.textContent = '음성 입력을 시작하지 못했습니다. 직접 입력하거나 다시 시도해 주세요.'; render(); }
    }
  });
  stop.addEventListener('click', () => {
    if (!listening || finishing) return;
    finishing = true; status.textContent = '음성 입력 종료 중 · 확정 결과를 기다리고 있습니다.'; render();
    try { recognition.stop(); }
    catch (_) { cancel('음성 입력을 종료했습니다. 확정된 초안을 확인해 주세요.'); }
  });
  render();
})();
