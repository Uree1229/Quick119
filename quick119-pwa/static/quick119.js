// 기존 음성 문진 저장 형식을 유지합니다. 다른 환자의 정보인지 확인할 수 있게 합니다.
(() => {
  const summary = document.getElementById('voice-summary');
  if (!summary) return;
  try {
    const data = JSON.parse(sessionStorage.getItem('emergencyVoiceIntake') || 'null');
    if (!data || typeof data !== 'object') return;
    const entries = [['기존 질환', data.conditions], ['통증 부위', data.areas],
      ['현재 증상', data.symptoms], ['발생 시점', data.onset], ['환자 진술', data.transcript]];
    const lines = entries.filter(([, value]) => typeof value === 'string' && value.trim());
    if (!lines.length) return;
    document.getElementById('voice-content').textContent = lines.map(([key, value]) => `${key}: ${value}`).join('\n');
    summary.hidden = false;
    document.getElementById('clear-voice-summary').addEventListener('click', () => {
      try {
        sessionStorage.removeItem('emergencyVoiceIntake');
        document.getElementById('voice-content').textContent = '';
        summary.hidden = true;
      } catch (_) { /* 저장소 차단 시 원래 결과를 유지합니다. */ }
    });
  } catch (_) { /* 저장소 차단 또는 손상된 값이 있어도 접수는 가능합니다. */ }
})();
