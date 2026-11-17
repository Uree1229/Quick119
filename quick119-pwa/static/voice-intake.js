/* 현장 보고: 명시된 표현만 추출합니다. 진단/정상값 추정/외부 API 호출 없음. */
(() => {
  'use strict';
  const keys = ['age', 'sex', 'consciousness', 'breathing', 'symptoms', 'onset',
    'systolic', 'diastolic', 'pulse', 'temperature', 'spo2'];
  function extract(raw) {
    const text = raw.replace(/(\d)\s*점\s*(\d)/g, '$1.$2');
    const state = Object.fromEntries(keys.map(key => [key, null]));
    // 서로 다른 값이 함께 보고되면 자동으로 하나를 고르지 않습니다.
    const unique = values => {
      const all = [...new Set(values)];
      return all.length === 1 ? all[0] : null;
    };
    const pick = (regex, group = 1) => unique([...text.matchAll(regex)].map(m => m[group].trim()));
    state.age = pick(/(?<!\d)(\d{1,3}\s*(?:세|살|대))(?!\d)/g);
    state.sex = pick(/(남성|여성|남자|여자)/g);
    // 술어 전체를 보존해 '저하 없음', '정상이지 않음'을 반대로 해석하지 않습니다.
    state.consciousness = pick(/의식(?:은|이)?\s*((?:명료|명확|있|없|저하|혼미|불명|소실|또렷)[^,.;\n]*?)(?=\s*(?:호흡|증상|혈압|맥박|심박|체온|산소|SpO2)|[,.;\n]|$)/g);
    state.breathing = pick(/호흡(?:은|이)?\s*((?:정상|자발\s*호흡|있|없|불규칙|비정상|가쁨|느림|빠름|곤란)[^,.;\n]*?)(?=\s*(?:의식|증상|혈압|맥박|심박|체온|산소|SpO2)|[,.;\n]|$)/g);
    const onset = /(?:약\s*)?\d+\s*(?:분|시간|일|주)\s*전(?:부터)?|(?:오늘|어제)\s*(?:(?:오전|오후|새벽|아침|저녁)\s*)?(?:\d{1,2}\s*시(?:\s*\d{1,2}\s*분)?)?|(?:오전|오후)\s*\d{1,2}\s*시(?:\s*\d{1,2}\s*분)?|방금|조금\s*전/g;
    state.onset = unique([...text.matchAll(onset)].map(m => m[0]));
    const bp = [...text.matchAll(/(?<!고)혈압(?:은|이)?\s*[:：]?\s*(\d{1,3})\s*(?:mmHg\s*)?(?:\/|에|대|,|그리고)\s*(\d{1,3})(?![\d.])/gi)];
    if (bp.length) {
      const pair = unique(bp.map(m => `${Number(m[1])}/${Number(m[2])}`));
      if (pair) [state.systolic, state.diastolic] = pair.split('/').map(Number);
    } else {
      const sys = pick(/수축기(?:\s*혈압)?(?:는|가|은|이)?\s*[:：]?\s*(\d{1,3})(?![\d.])/g);
      const dia = pick(/이완기(?:\s*혈압)?(?:는|가|은|이)?\s*[:：]?\s*(\d{1,3})(?![\d.])/g);
      if (sys !== null) state.systolic = Number(sys);
      if (dia !== null) state.diastolic = Number(dia);
    }
    const pulse = pick(/(?:맥박|심박수|심박)(?:은|이|는|가)?\s*[:：]?\s*(\d{1,3})(?![\d.])/g);
    const temp = pick(/체온(?:은|이)?\s*[:：]?\s*(\d{1,2}(?:\.\d+)?)(?![\d.])/g);
    const oxygen = pick(/(?:산소\s*포화도|SpO2|에스피오투)(?:는|가|은|이)?\s*[:：]?\s*(\d{1,3}(?:\.\d+)?)(?![\d.])/gi);
    if (pulse !== null) state.pulse = Number(pulse);
    if (temp !== null && Number(temp) <= 60) state.temperature = Number(temp);
    if (oxygen !== null && Number(oxygen) <= 100) state.spo2 = Number(oxygen);
    // 부정 표현을 포함한 원문 구절을 보존합니다. '흉통 없음'을 흉통으로 바꾸지 않습니다.
    const clauses = raw.split(/[,，。\n;]|(?<!\d)\.(?!\d)|\.(?!\d)/).map(s => s.trim()).filter(Boolean);
    const symptomWords = /흉통|가슴.{0,8}(?:통증|아프|답답)|호흡\s*곤란|숨.{0,8}(?:차|힘들|막히|못\s*쉬)|복통|배.{0,5}아프|두통|머리.{0,5}아프|출혈|피가\s*나|어지|구토|구역|오심|발열|열이\s*나|경련|발작|마비|통증|기침|설사|화상|골절|두근|실신|청색증/;
    const symptoms = clauses.filter(s => symptomWords.test(s) || /^증상(?:은|이)?\s*[:：]/.test(s))
      .map(s => s.replace(/^증상(?:은|이)?\s*[:：]\s*/, ''));
    state.symptoms = symptoms.length ? [...new Set(symptoms)].join(', ') : null;
    return state;
  }
  window.Quick119Voice = Object.freeze({ extract });
  const root = document.getElementById('voice-intake');
  if (!root) return;
  const $ = id => document.getElementById(id);
  const input = $('transcript'), start = $('start'), stop = $('stop'), status = $('status');
  const fields = [...root.querySelectorAll('[data-state]')];
  const touched = new Set();
  let recognition = null, listening = false, pending = false, saved = false, finalText = '';
  let requestId = null, lastPayload = null;
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const available = window.isSecureContext && !!SR;
  // 이전 버전의 환자/보호자 문진 결과를 현장 보고와 혼합하지 않습니다.
  try { sessionStorage.removeItem('emergencyVoiceIntake'); } catch (_) { /* 저장소 차단 */ }
  function controls() {
    start.disabled = !available || listening || pending || saved;
    stop.disabled = !listening || pending;
    input.disabled = listening || pending || saved;
    fields.forEach(field => { field.disabled = pending || saved; });
    $('report-patient').disabled = pending || saved;
    $('save-report').disabled = listening || pending || saved;
    for (const id of ['auto-extract', 'clear-report', 'new-report']) $(id).disabled = listening || pending;
    $('auto-extract').disabled ||= saved;
    $('report-form').setAttribute('aria-busy', String(pending));
  }
  function update() {
    $('report-bubble').textContent = input.value || '마이크를 눌러 환자 상태를 보고해 주세요.';
    const data = extract(input.value);
    fields.forEach(field => {
      if (!touched.has(field.name)) field.value = data[field.name] ?? '';
      field.nextElementSibling.textContent = touched.has(field.name) ? '직접 수정' :
        field.value !== '' ? (listening ? '인식 중 · 확인 필요' : '자동 추출 · 확인 필요') :
          (field.type === 'number' ? '미측정' : '미확인');
    });
  }
  fields.forEach(field => field.addEventListener('input', () => { touched.add(field.name); update(); }));
  input.addEventListener('input', update);
  $('auto-extract').onclick = () => {
    if (touched.size && !window.confirm('직접 수정한 항목을 지우고 현재 보고 원문에서 다시 추출할까요?')) return;
    touched.clear(); update();
  };
  if(!window.isSecureContext) {
    status.textContent = '마이크 사용에는 HTTPS 또는 localhost 접속이 필요합니다. 보고 원문을 직접 입력해 주세요.';
  } else if (!SR) {
    status.textContent = '이 브라우저는 음성인식을 지원하지 않습니다. 보고 원문을 직접 입력해 주세요.';
  } else {
    recognition = new SR();
    recognition.lang = 'ko-KR'; recognition.continuous = true; recognition.interimResults = true;
    recognition.onstart = () => { status.textContent = '듣고 있습니다. 환자 상태를 한 번에 보고해 주세요.'; };
    recognition.onresult = event => {
      if (!listening) return;
      let interim = '';
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const text = event.results[i][0].transcript;
        if (event.results[i].isFinal) finalText += text + ' ';
        else interim += text;
      }
      input.value = (finalText + interim).trim(); update();
    };
    recognition.onerror = event => {
      const errors = {'not-allowed': '마이크 권한이 거부되었습니다.', 'audio-capture': '마이크를 사용할 수 없습니다.',
        'no-speech': '음성을 인식하지 못했습니다.', network: '음성인식 서비스에 연결하지 못했습니다.'};
      status.textContent = (errors[event.error] || '음성인식 오류: ' + event.error) + ' 보고 원문을 직접 입력하거나 재시도해 주세요.';
    };
    recognition.onend = () => {
      listening = false;
      // 저장은 확정된 인식 결과만 사용합니다. 중간 인식값을 확정값으로 간주하지 않습니다.
      input.value = finalText.trim(); update(); controls();
      if (status.textContent.startsWith('듣고')) status.textContent = '음성인식이 종료되었습니다. 원문과 요약을 확인해 주세요.';
    };
    start.onclick = () => {
      finalText = input.value.trim() ? input.value.trim() + ' ' : '';
      listening = true; controls();
      try { recognition.start(); }
      catch (_) { listening = false; controls(); status.textContent = '음성인식을 시작하지 못했습니다. 다시 시도하거나 직접 입력해 주세요.'; }
    };
    stop.onclick = () => { recognition.stop(); status.textContent = '인식 종료 중입니다. 확정 결과를 기다려 주세요.'; };
  }
  function reset() {
    if (input.value.trim() || touched.size || saved) {
      if (!window.confirm('현재 화면의 보고를 비우고 새로 시작할까요? 서버에 저장된 기록은 유지됩니다.')) return;
    }
    input.value = ''; finalText = ''; touched.clear(); saved = false; requestId = null; lastPayload = null;
    $('report-patient').value = '';
    // 이전 접수의 임시 결과가 다른 환자 화면에 표시되지 않게 합니다.
    try { sessionStorage.removeItem('emergencyVoiceIntake'); } catch (_) { /* 저장소 차단 */ }
    $('save-status').textContent = '아직 서버에 저장하지 않았습니다.';
    $('save-status').removeAttribute('data-result');
    if (available) status.textContent = '보고 대기 중';
    update(); controls();
  }
  $('new-report').onclick = reset; $('clear-report').onclick = reset;
  const uuid = () => {
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
    const hex = [...bytes].map(b => b.toString(16).padStart(2, '0')).join('');
    return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
  };
  $('report-form').onsubmit = async event => {
    event.preventDefault();
    if (listening || pending || saved) return;
    const result = $('save-status');
    if (!input.value.trim() || input.value.length > 20000) {
      result.textContent = '저장하지 않았습니다. 1~20,000자의 보고 원문을 입력해 주세요.';
      result.dataset.result = 'error'; return;
    }
    if (!$('report-form').reportValidity()) return;
    const state = Object.fromEntries(fields.map(field => [field.name,
      field.value.trim() === '' ? null : field.type === 'number' ? Number(field.value) : field.value.trim()]));
    const payload = {patient_id: Number($('report-patient').value) || null, transcript: input.value.trim(), state};
    const serialized = JSON.stringify(payload);
    if (lastPayload !== serialized) { requestId = uuid(); lastPayload = serialized; }
    pending = true; controls(); result.textContent = '서버에 저장 중입니다…'; result.removeAttribute('data-result');
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch('/api/emergency-intakes', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({...payload, request_id: requestId}), cache: 'no-store', signal: controller.signal});
      const data = await response.json();
      if (!response.ok || data.saved !== true || !Number.isInteger(data.id)) {
        throw new Error(typeof data.detail === 'string' ? data.detail : '입력값을 확인한 뒤 다시 시도해 주세요.');
      }
      saved = true;
      result.textContent = `서버 저장 완료 · 접수번호 ${data.id}. 새 환자는 ‘새 환자 보고’를 눌러 주세요.`;
      result.dataset.result = 'success';
    } catch (error) {
      result.textContent = `저장 확인 실패 · ${error.name === 'AbortError' ? '응답 시간이 초과되었습니다.' : error.message} 입력 내용은 유지됩니다. 같은 내용으로 재시도해 저장 여부를 확인할 수 있습니다.`;
      result.dataset.result = 'error';
    } finally { clearTimeout(timer); pending = false; controls(); }
  };
  update(); controls();
})();
