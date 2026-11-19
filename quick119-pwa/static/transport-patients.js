(() => {
  'use strict';
  const list = document.getElementById('transport-patient-cases');
  if (!list) return;
  const status = document.getElementById('transport-patients-status');
  const idPattern = /^Q119-TEST-[A-Za-z0-9-]{1,80}$/;
  const labels = {PENDING: '문의 대기', CHECKING: '병원 확인 중', ACCEPTED: '수용 가능',
    DECLINED: '수용 불가', CANCELLED: '문의 종료', CONFIRMED: '목적지 확정', EXPIRED: '회신 만료'};
  function addText(parent, tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    node.textContent = text;
    parent.append(node);
    return node;
  }
  function formatTime(value) {
    if (!Number.isFinite(value)) return '';
    return new Intl.DateTimeFormat('ko-KR', {month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit'})
      .format(new Date(value * 1000));
  }
  function render(patient) {
    if (!patient || !idPattern.test(patient.id) || !Number.isSafeInteger(patient.case_number) || patient.case_number < 1) {
      throw new Error('환자 접수 ID 또는 번호를 확인하지 못했습니다.');
    }
    const number = patient.case_number;
    const href = '/transport/?pid=' + encodeURIComponent(patient.id);
    const card = document.createElement('article');
    card.className = 'transport-patient-case';
    card.dataset.active = String(!!patient.active);
    card.dataset.patientId = patient.id;

    const header = document.createElement('div');
    header.className = 'transport-patient-case-header';
    const title = addText(header, 'a', 'transport-patient-case-title', `환자 ${number}`);
    title.href = href;
    title.setAttribute('aria-label', `환자 ${number} 접수 채팅 열기`);
    addText(header, 'span', 'transport-patient-case-stage', patient.stage || '상태 확인 필요');
    card.append(header);

    const details = document.createElement('details');
    details.className = 'transport-patient-case-details';
    addText(details, 'summary', '', '상세 정보');
    const body = document.createElement('div');
    body.className = 'transport-patient-case-body';
    addText(body, 'p', 'transport-patient-case-detail',
      patient.age == null ? '연령: 미입력' : `연령: ${patient.age}세`);
    addText(body, 'p', 'transport-patient-case-detail',
      patient.symptoms ? `증상: ${patient.symptoms}` : '증상: 미입력');
    const inquiries = Array.isArray(patient.inquiries) ? patient.inquiries : [];
    if (inquiries.length) {
      const inquiryList = document.createElement('ul');
      inquiryList.className = 'transport-patient-inquiries';
      for (const inquiry of inquiries) {
        const row = document.createElement('li');
        addText(row, 'span', '', inquiry.hospital_name || inquiry.hospital_id);
        addText(row, 'span', 'transport-patient-inquiry-state', labels[inquiry.state] || inquiry.state);
        inquiryList.append(row);
      }
      body.append(inquiryList);
    } else addText(body, 'p', 'transport-patient-case-detail', '병원 문의: 없음');
    const meta = document.createElement('div');
    meta.className = 'transport-patient-case-meta';
    addText(meta, 'span', '', patient.destination_hospital_name
      ? `목적지: ${patient.destination_hospital_name}` : '목적지 미확정');
    const requested = formatTime(patient.requested_at);
    if (requested) addText(meta, 'span', '', `접수 ${requested}`);
    body.append(meta);
    details.append(body);
    card.append(details);

    const actions = document.createElement('div');
    actions.className = 'transport-patient-case-actions';
    const open = addText(actions, 'a', 'transport-patient-open', '채팅 열기');
    open.href = href;
    open.setAttribute('aria-label', `환자 ${number} 접수 채팅 열기`);
    const remove = addText(actions, 'button', 'transport-patient-delete', '삭제');
    remove.type = 'button';
    remove.disabled = patient.deletable !== true;
    remove.setAttribute('aria-label', `환자 ${number} 접수 삭제`);
    if (remove.disabled) {
      addText(actions, 'span', 'transport-patient-delete-blocked',
        patient.delete_block_reason || '현재 상태에서는 삭제할 수 없습니다.');
    }
    remove.addEventListener('click', () => { void deleteCase(patient, number, remove); });
    card.append(actions);
    return card;
  }
  async function deleteCase(patient, number, button) {
    if (button.disabled || !window.confirm(
      `환자 ${number} 접수를 삭제할까요? 채팅·문의·배정 등 관련 기록은 복구할 수 없습니다.`)) return;
    button.disabled = true;
    button.textContent = '삭제 중...';
    try {
      const response = await fetch('/api/patients/' + encodeURIComponent(patient.id), {
        method: 'DELETE', credentials: 'same-origin', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({confirm: true}),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.message || `서버 응답 ${response.status}`);
      if (result.deleted !== true || result.patient_id !== patient.id) {
        throw new Error('삭제 결과를 확인하지 못했습니다.');
      }
      await load();
    } catch (failure) {
      button.disabled = false;
      button.textContent = '삭제';
      status.textContent = `환자 ${number} 접수를 삭제하지 못했습니다: ${failure.message}`;
    }
  }
  async function load() {
    status.textContent = '접수 목록을 불러오고 있습니다.';
    list.replaceChildren();
    try {
      const response = await fetch('/api/transport/patients', {credentials: 'same-origin'});
      if (!response.ok) throw new Error(`서버 응답 ${response.status}`);
      const data = await response.json();
      if (!Array.isArray(data.patients)) throw new Error('접수 목록 응답 형식이 올바르지 않습니다.');
      if (data.patients.some(patient =>
        !Number.isSafeInteger(patient?.case_number) ||
        typeof patient?.deletable !== 'boolean' ||
        typeof patient?.delete_block_reason !== 'string')) {
        throw new Error('환자 목록 서버 버전이 맞지 않습니다. case_number/deletable 필드를 제공하는 최신 공통 서버를 재시작한 뒤 다시 시도하세요.');
      }
      if (!data.patients.length) {
        addText(list, 'p', 'transport-patient-empty', '아직 접수된 환자가 없습니다. 새 접수를 시작하면 환자별 채팅이 여기에 저장됩니다.');
      } else {
        for (const patient of data.patients) list.append(render(patient));
      }
      status.textContent = `${data.patients.length}건의 접수 기록 · 번호는 접수 순서로 유지되며 삭제 후에도 바뀌지 않습니다.`;
    } catch (failure) {
      status.replaceChildren();
      addText(status, 'span', '', `접수 목록을 불러오지 못했습니다: ${failure.message} `);
      const retry = addText(status, 'button', 'transport-patient-retry', '다시 시도');
      retry.type = 'button';
      retry.addEventListener('click', () => { void load(); });
    }
  }
  void load();
})();
