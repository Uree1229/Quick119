/* Review saved reports explicitly before changing the patient used for hospital search. */
(() => {
  'use strict';
  const definitions = [
    ['age', '나이 (세)', 'number', 150],
    ['consciousness', '의식', {alert: '명료', impaired: '저하', unresponsive: '무반응', unknown: '미확인'}],
    ['breathing', '호흡', {normal: '정상', abnormal: '비정상', absent: '없음', unknown: '미확인'}],
    ['symptoms', '증상 · 확인한 내용', 'textarea'],
    ['systolic', '수축기 혈압 (mmHg)', 'number', 999, 'sbp'],
    ['diastolic', '이완기 혈압 (mmHg)', 'number', 999, 'dbp'],
    ['pulse', '맥박 (회/분)', 'number', 999, 'hr'],
    ['temperature', '체온 (°C)', 'number', 60, 'temp'],
    ['spo2', '산소포화도 (%)', 'number', 100, 'spo2'],
  ];
  const clone = value => JSON.parse(JSON.stringify(value));
  const valueFor = (patient, definition) => definition[4] ? patient.extra?.vitals?.[definition[4]] ?? null : patient[definition[0]] ?? null;
  function proposed(raw, key) {
    const value = raw?.[key];
    if (value === null || value === undefined) return null;
    if (key === 'age') { const match = /^(\d{1,3})\s*(?:세|살)$/.exec(value); return match && Number(match[1]) <= 150 ? Number(match[1]) : null; }
    // Whole-phrase matches only; never turn negated or extended clauses into a normal value.
    if (key === 'consciousness') return {명료: 'alert', 명확: 'alert', 또렷: 'alert', 저하: 'impaired', 혼미: 'impaired', 없음: 'unresponsive', 소실: 'unresponsive'}[value] ?? null;
    if (key === 'breathing') return {정상: 'normal', 비정상: 'abnormal', 불규칙: 'abnormal', 곤란: 'abnormal', 없음: 'absent'}[value] ?? null;
    return value;
  }
  window.Quick119PatientReview = Object.freeze({bind({getState, onSearch}) {
    const $ = name => document.getElementById('transport-' + name);
    if (!$('review-panel')) return null;
    const dialog = $('review-panel');
    let reviewed = null, editing = false, busy = false, returnFocus = null;
    const fields = new Map();
    function update() {
      const state = getState();
      $('patient-info').disabled = !state.patient || busy;
      $('start-search').disabled = !state.patient || !!state.blockedReason || busy;
      $('search-status').textContent = state.searchStatus || state.blockedReason || '전송한 보고와 환자 정보를 확인한 뒤 병원을 탐색할 수 있습니다.';
      if (reviewed) {
        const changed = state.token !== reviewed.token;
        $('review-submit').disabled = busy || !editing || !!state.blockedReason || changed;
        $('review-close').disabled = busy;
        $('review-close-top').disabled = busy;
        for (const field of fields.values()) field.disabled = !editing || busy || changed || !!state.locked;
        if (changed && editing && !busy) $('review-error').textContent = '보고나 저장 정보가 바뀌었습니다. 창을 닫고 최신 정보로 다시 확인해 주세요.';
      }
    }
    function show(edit) {
      const state = getState();
      if (!state.patient || (edit && state.blockedReason)) return;
      returnFocus = document.activeElement;
      document.dispatchEvent(new CustomEvent('quick119:before-view', {detail: {view: 'review'}}));
      reviewed = clone({patient: state.patient, reports: state.reports, token: state.token});
      editing = edit && !state.locked; fields.clear();
      $('review-title').textContent = editing ? '환자 정보 최종 확인 · 병원 탐색' : '환자 정보 · 전송한 보고';
      $('review-note').textContent = editing
        ? '아래 값을 원문과 대조해 확인하세요. 마지막 버튼을 누르면 환자 정보를 저장한 뒤 후보 병원을 조회합니다.'
        : state.locked ? '이미 수용 문의를 보낸 접수입니다. 저장 정보와 보고를 조회하며, 여기서 다시 수정하거나 탐색하지 않습니다.'
        : '저장된 정보와 이미 전송한 보고만 표시합니다. 작성 중인 메시지는 포함하지 않습니다.';
      $('review-error').textContent = '';
      $('review-submit').hidden = !editing;
      const saved = $('saved-patient'); saved.replaceChildren();
      for (const definition of definitions) {
        const value = valueFor(state.patient, definition);
        const row = document.createElement('div'), label = document.createElement('dt'), content = document.createElement('dd');
        label.textContent = definition[1];
        content.textContent = value === null || value === '' ? '미확인' : typeof definition[2] === 'object' ? definition[2][value] || value : String(value);
        row.append(label, content); saved.append(row);
      }
      const context = document.createElement('div'); context.className = 'transport-review-wide';
      const contextLabel = document.createElement('dt'), contextValue = document.createElement('dd');
      contextLabel.textContent = '현재 탐색 조건';
      const resourceLabels = {ed_bed: '응급병상', icu_bed: '중환자 병상', special_team: '전문팀'};
      const resources = Object.entries(state.patient.resource_demands || {}).map(([key, count]) => `${resourceLabels[key] || key} ${count}개`).join(', ');
      contextValue.textContent = '필요 진료 분야: ' + ((state.patient.required_capabilities || []).join(', ') || '미지정')
        + ' · 자원: ' + (resources || '미지정') + ' · 위치: 가상 시연 좌표';
      context.append(contextLabel, contextValue); saved.append(context);
      const reports = $('review-reports'); reports.replaceChildren();
      for (const report of state.reports) {
        const block = document.createElement('div'); block.className = 'transport-review-report';
        const stamp = document.createElement('small'), body = document.createElement('p');
        stamp.textContent = new Date(report.at * 1000).toLocaleString('ko-KR') + ' · 구급대원';
        body.textContent = report.body || report.text || ''; block.append(stamp, body); reports.append(block);
      }
      if (!state.reports.length) { const empty = document.createElement('p'); empty.textContent = '아직 전송한 보고가 없습니다.'; reports.append(empty); }
      const extract = window.Quick119Voice?.extract || (() => ({}));
      const combined = extract(state.reports.map(report => report.body || report.text || '').join('\n'));
      const individual = state.reports.map(report => extract(report.body || report.text || ''));
      $('review-fields').replaceChildren();
      for (const definition of definitions) {
        const [key, title, kind, maximum] = definition, existing = valueFor(state.patient, definition);
        const suggestion = proposed(combined, key);
        const distinct = [...new Set(individual.map(raw => proposed(raw, key)).filter(value => value !== null))];
        const conflict = key !== 'symptoms' && distinct.length > 1;
        const baseline = existing !== null && existing !== '';
        const selected = baseline ? existing : conflict ? null : suggestion;
        const row = document.createElement('div'); if (key === 'symptoms') row.className = 'transport-review-wide';
        const label = document.createElement('label'); label.htmlFor = 'transport-review-' + key; label.textContent = title;
        const field = document.createElement(typeof kind === 'object' ? 'select' : kind === 'textarea' ? 'textarea' : 'input');
        field.id = 'transport-review-' + key; field.name = key;
        if (typeof kind === 'object') {
          for (const [value, text] of [['', '미확인 / 입력 없음'], ...Object.entries(kind)]) {
            const option = document.createElement('option'); option.value = value; option.textContent = text; field.append(option);
          }
        } else if (kind === 'number') {
          field.type = 'number'; field.min = '0'; field.max = String(maximum);
          field.step = ['temperature', 'spo2'].includes(key) ? '0.1' : '1';
        } else { field.rows = 3; field.maxLength = 2000; }
        field.value = selected ?? ''; fields.set(key, field);
        const source = document.createElement('small');
        source.textContent = conflict ? (baseline ? '저장값 유지 · 보고값 충돌, 원문 확인 필요' : '보고값 충돌 · 직접 확인 필요')
          : baseline ? (suggestion !== null && suggestion !== existing ? '저장값 유지 · 보고 추출값과 다름, 원문 확인 필요' : '서버 저장값')
          : suggestion !== null ? '전송한 보고에서 자동 추출 · 확인 필요' : '미확인 · 직접 입력 가능';
        row.append(label, field, source); $('review-fields').append(row);
      }
      $('review-raw').textContent = '원문 추출 참고: 나이 ' + (combined.age || '미확인') + ' · 성별 ' + (combined.sex || '미확인')
        + ' · 의식 ' + (combined.consciousness || '미확인') + ' · 호흡 ' + (combined.breathing || '미확인')
        + ' · 발생 시점 ' + (combined.onset || '미확인') + '. 연령대·상대 시각은 정확한 나이·시각으로 임의 변환하지 않습니다.';
      update();
      if (!dialog.open) dialog.showModal();
      $('review-title').focus({preventScroll: true});
      dialog.querySelector('.transport-review-body').scrollTop = 0;
    }
    function patch() {
      const result = {extra: clone(reviewed.patient.extra || {})};
      const vitals = {...(result.extra.vitals || {})};
      for (const definition of definitions) {
        const [key, , kind, , vital] = definition, raw = fields.get(key).value.trim();
        const value = raw === '' ? null : kind === 'number' ? Number(raw) : raw;
        if (vital) { if (value === null) delete vitals[vital]; else vitals[vital] = value; }
        else result[key] = value;
      }
      if (Object.keys(vitals).length) result.extra.vitals = vitals;
      else delete result.extra.vitals;
      return result;
    }
    $('patient-info').addEventListener('click', () => show(false));
    $('start-search').addEventListener('click', () => show(true));
    function closeReview(force = false) {
      if (busy && !force) return;
      reviewed = null;
      if (dialog.open) dialog.close();
    }
    $('review-close').addEventListener('click', () => closeReview());
    $('review-close-top').addEventListener('click', () => closeReview());
    dialog.addEventListener('cancel', event => {
      event.preventDefault();
      closeReview();
    });
    dialog.addEventListener('click', event => {
      if (event.target !== dialog) return;
      const rect = dialog.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) closeReview();
    });
    dialog.addEventListener('close', () => {
      reviewed = null;
      const target = returnFocus?.isConnected && !returnFocus.disabled && returnFocus.getClientRects().length ? returnFocus : $('toggle');
      target?.focus({preventScroll: true});
      returnFocus = null;
    });
    $('review-form').addEventListener('submit', async event => {
      event.preventDefault();
      const state = getState();
      if (!reviewed || !editing || busy || state.blockedReason || state.token !== reviewed.token) return;
      if (!$('review-form').reportValidity()) return;
      busy = true; $('review-error').textContent = ''; update();
      try {
        await onSearch(patch(), reviewed.token);
        closeReview(true);
      } catch (error) { $('review-error').textContent = error.message || '탐색을 완료하지 못했습니다. 저장 정보와 연결 상태를 확인해 주세요.'; }
      finally { busy = false; update(); }
    });
    update();
    return {update};
  }});
})();
