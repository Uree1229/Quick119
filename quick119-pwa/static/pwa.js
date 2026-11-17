(() => {
  const install = document.getElementById('pwa-install');
  const status = document.getElementById('pwa-status');
  const say = text => { if (status) status.textContent = text; };
  const standalone = window.matchMedia('(display-mode: standalone)');
  let prompt = null;
  const isInstalled = () => standalone.matches || navigator.standalone === true;
  if (isInstalled()) say('앱 전용 화면으로 실행 중입니다.');
  else if (!window.isSecureContext) say('설치·GPS·마이크 확인에는 신뢰되는 HTTPS 접속이 필요합니다.');

  window.addEventListener('beforeinstallprompt', event => {
    if (!install || isInstalled()) return;
    event.preventDefault();
    prompt = event;
    install.hidden = false;
  });
  if (install) install.addEventListener('click', async () => {
    if (!prompt) return;
    const pending = prompt;
    prompt = null;
    install.hidden = true;
    try {
      await pending.prompt();
      const result = await pending.userChoice;
      say(result.outcome === 'accepted' ? '설치 요청을 수락했습니다. 설치가 끝나면 홈 화면 아이콘으로 실행해 주세요.' : '설치를 취소했습니다. Chrome 메뉴에서도 설치할 수 있습니다.');
    } catch (_) { say('설치 창을 열지 못했습니다. Chrome 메뉴에서 설치해 주세요.'); }
  });
  window.addEventListener('appinstalled', () => {
    prompt = null;
    if (install) install.hidden = true;
    say('앱 설치 이벤트를 확인했습니다. 홈 화면의 퀵119 아이콘으로 실행해 주세요.');
  });

  if ('serviceWorker' in navigator && window.isSecureContext) {
    navigator.serviceWorker.register('/sw.js', { scope: '/', updateViaCache: 'none' }).then(reg => {
      const updated = () => say('새 버전이 준비됐습니다. 입력을 마친 뒤 퀵119 창을 모두 닫고 다시 실행해 주세요.');
      if (reg.waiting) updated();
      reg.addEventListener('updatefound', () => {
        const worker = reg.installing;
        if (worker) worker.addEventListener('statechange', () => {
          if (worker.state === 'installed' && navigator.serviceWorker.controller) updated();
        });
      });
    }).catch(() => say('오프라인 안내 기능을 준비하지 못했습니다. 인터넷 연결과 Chrome 사이트 설정을 확인해 주세요.'));
  }

  const check = document.getElementById('pwa-check');
  if (!check) return;
  const put = (id, text) => { document.getElementById(id).textContent = text; };
  put('check-secure', window.isSecureContext ? '보안 컨텍스트: 예' : '보안 컨텍스트: 아니오 — HTTPS로 접속해 주세요.');
  put('check-display', isInstalled() ? 'standalone 앱 화면' : '일반 브라우저 탭 — 홈 화면에 설치한 아이콘으로 실행해 주세요.');
  put('check-speech', (window.SpeechRecognition || window.webkitSpeechRecognition) ? '음성인식 API 있음 — 실제 인식 성공은 별도 확인 필요' : '음성인식 API 없음 — 직접 입력을 사용해 주세요.');
  const mic = document.getElementById('check-mic');
  mic.disabled = !window.isSecureContext || !navigator.mediaDevices?.getUserMedia;
  if (mic.disabled) put('check-mic-result', '마이크 장치 접근을 지원하지 않거나 HTTPS가 아닙니다. 직접 입력을 사용해 주세요.');
  mic.addEventListener('click', async () => {
    mic.disabled = true;
    put('check-mic-result', '마이크 권한을 확인하고 있습니다.');
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      stream.getTracks().forEach(track => track.stop());
      put('check-mic-result', '마이크 접근 성공. 스트림을 즉시 종료했습니다. 음성인식은 음성 문진에서 별도로 확인해 주세요.');
    } catch (_) { put('check-mic-result', '마이크에 접근하지 못했습니다. 권한과 장치 상태를 확인하거나 직접 입력을 사용해 주세요.'); }
    finally { mic.disabled = false; }
  });
  const gps = document.getElementById('check-gps');
  gps.disabled = !window.isSecureContext || !navigator.geolocation;
  if (gps.disabled) put('check-gps-result', '위치를 지원하지 않거나 HTTPS가 아닙니다.');
  gps.addEventListener('click', () => {
    gps.disabled = true;
    put('check-gps-result', '위치 권한을 확인하고 있습니다.');
    navigator.geolocation.getCurrentPosition(position => {
      put('check-gps-result', `위치 취득 성공. 정확도 약 ${Math.round(position.coords.accuracy)}m. 좌표를 서버로 보내거나 저장하지 않았습니다.`);
      gps.disabled = false;
    }, error => {
      const message = { 1: '위치 권한이 거부되었습니다.', 2: '위치를 확인할 수 없습니다.', 3: '위치 확인 시간이 초과되었습니다.' };
      put('check-gps-result', message[error.code] || '위치 확인에 실패했습니다.');
      gps.disabled = false;
    }, { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 });
  });
})();
