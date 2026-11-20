// 병원 채팅 패널(panel.html)의 화면 상태 계산. DOM과 통신을 쓰지 않는 순수 함수만 둔다.
// 패널은 기존 병원 웹(index.html)과 같은 서버 응답을 읽고, 수용 문의 회신과 이송 대화만 다룬다.

export const RESOURCE = { ed_bed: '응급병상', special_team: '전문팀', operating_room: '수술실', icu_bed: '중환자실 병상',
  nicu_bed: '신생아 중환자실 병상', delivery_bed: '분만실 병상', pediatric_bed: '소아 병상', isolation_bed: '격리 병상' };
// 서버(server/service.py DECLINE_REASONS)가 받는 값 그대로다.
export const DECLINE_REASONS = ['인력', '장비', '병상', '진료 불가', '기타'];
const CLOSED_LABEL = { CANCELLED: '구급대가 문의 종료', EXPIRED: '수용 응답 만료', CONFIRMED: '목적지 확정', DECLINED: '수용 불가 회신', ACCEPTED: '수용 응답 만료' };
const FINISHED = new Set(['arrived', 'declined', 'closed']);
export const MAX_CLOSED = 4;
// 목록에서 쓰는 중증도 구분. 색만으로 구분하지 않도록 짧은 글자를 함께 보여 준다.
export const SEVERITY = { urgent: '긴급', severe: '중증', moderate: '중등', mild: '경증', unknown: '미확인' };
const VITAL_WORDS = { consciousness: { alert: '명료', impaired: '저하', unresponsive: '무반응' },
  breathing: { normal: '정상', abnormal: '비정상', absent: '없음' } };

export function patientTitle(p) {
  const age = p?.age == null ? '나이 미확인' : `${p.age}세`;
  return `${age} · ${p?.symptoms || '증상 미입력'}`;
}

export function inquiryStage(q, now) {
  if (q.state === 'PENDING') return 'new';
  if (q.state === 'CHECKING') return 'checking';
  if (q.state === 'ACCEPTED') return q.expires_at != null && q.expires_at <= now ? 'closed' : 'accepted';
  if (q.state === 'DECLINED') return 'declined';
  return 'closed';
}

// 환자 한 명이 대화 하나다. 목적지가 우리 병원으로 확정된 환자(incoming)가 문의보다 우선한다.
export function buildThreads({ open = [], recent = [], incoming = [] } = {}, now = 0) {
  const byPatient = new Map();
  for (const p of incoming) {
    byPatient.set(p.id, { key: `p:${p.id}`, patientId: p.id, patient: p, inquiry: null,
      stage: p.state === 'ARRIVED' ? 'arrived' : 'confirmed' });
  }
  for (const q of [...open, ...recent]) {
    const pid = q.patient?.id ?? q.patient_id;
    if (!pid) continue;
    const existing = byPatient.get(pid);
    if (existing && !existing.fromInquiry) {
      if (!existing.inquiry || q.id > existing.inquiry.id) existing.inquiry = q;
      continue;
    }
    if (existing && existing.inquiry && existing.inquiry.id > q.id) continue;
    byPatient.set(pid, { key: `p:${pid}`, patientId: pid, patient: q.patient || { id: pid }, inquiry: q,
      stage: inquiryStage(q, now), fromInquiry: true });
  }
  // 순서는 언제나 먼저 온 순서다(문의가 들어온 시각, 같으면 문의 번호). 상태나 중증도로 순서를 바꾸지 않는다.
  // 끝난 대화(도착 확인·수용 불가·종료)는 진행 중인 대화 아래에 최근 몇 건만 둔다.
  const LAST = Number.MAX_SAFE_INTEGER;
  const byArrival = (a, b) => (a.inquiry?.created_at ?? LAST) - (b.inquiry?.created_at ?? LAST)
    || (a.inquiry?.id ?? LAST) - (b.inquiry?.id ?? LAST) || a.patientId.localeCompare(b.patientId);
  const threads = [...byPatient.values()].sort(byArrival);
  const finished = threads.filter((t) => FINISHED.has(t.stage)).slice(-MAX_CLOSED).map((t) => ({ ...t, finished: true }));
  return [...threads.filter((t) => !FINISHED.has(t.stage)), ...finished];
}

export function severityKey(p) {
  if (!p || p.priority_source === 'unknown' || p.effective_priority == null) return 'unknown';
  if (p.effective_priority >= 4) return 'urgent';
  if (p.effective_priority >= 3) return 'severe';
  if (p.effective_priority >= 2) return 'moderate';
  return 'mild';
}

function minutes(seconds) { return Math.max(1, Math.ceil(seconds / 60)); }

export function etaLabel(p, now) {
  if (p.state === 'ARRIVED') return '도착 확인';
  if (p.eta_at == null) return p.state === 'IN_TRANSIT' ? '이송 중' : '출발 전';
  const remaining = p.eta_at - now;
  if (remaining <= 0) return '곧 도착';
  const total = minutes(remaining);
  return total < 90 ? `${total}분 뒤 도착` : `${Math.floor(total / 60)}시간 ${total % 60}분 뒤 도착`;
}

// 목록 칩과 헤더에 쓰는 한 줄 상태. tone: alert(답해야 함) · wait · ok · muted
export function threadStatus(thread, now) {
  const q = thread.inquiry, p = thread.patient;
  if (thread.stage === 'new') {
    const waited = Math.max(0, now - (q.created_at ?? now));
    return { text: q.overdue ? '새 문의 · 응답 지연' : waited >= 60 ? `새 문의 · ${Math.floor(waited / 60)}분 경과` : '새 문의', tone: 'alert' };
  }
  if (thread.stage === 'checking') {
    if (q.question && q.answer) return { text: '답변 도착 · 회신 필요', tone: 'alert' };
    if (q.question) return { text: '구급대 답변 대기', tone: 'wait' };
    return { text: '확인 중으로 회신', tone: 'wait' };
  }
  if (thread.stage === 'accepted') return { text: '수용 가능 회신 · 확정 대기', tone: 'wait' };
  if (thread.stage === 'confirmed') {
    if (p.needs_adjustment) return { text: '수용 취소 요청함', tone: 'wait' };
    if (needsReview(p)) return { text: '상태 변경 · 확인 필요', tone: 'alert' };
    return { text: `우리 병원 확정 · ${etaLabel(p, now)}`, tone: 'ok' };
  }
  if (thread.stage === 'arrived') return { text: '도착 확인', tone: 'muted' };
  return { text: CLOSED_LABEL[q?.state] || '문의 종료', tone: 'muted' };
}

export function needsReview(p) {
  const review = p?.transport_review;
  return !!review?.enabled && review.risk_signature !== review.acknowledged_signature;
}

// 환자 요약 카드의 칸. 값이 없는 항목은 만들지 않는다(없는 정보를 지어내지 않는다).
export function summaryFields(p, hospital) {
  const fields = [];
  // 서버 코드값을 구급대 화면과 같은 말로 보여 준다. '미확인'으로 온 값은 정보가 아니므로 칸을 만들지 않는다.
  for (const [key, label] of [['consciousness', '의식'], ['breathing', '호흡']]) {
    const value = p[key];
    if (!value || value === 'unknown') continue;
    const worse = value === 'impaired' || value === 'unresponsive' || value === 'abnormal' || value === 'absent';
    fields.push({ label, value: VITAL_WORDS[key][value] || value, ...(worse ? { tone: 'alert' } : {}) });
  }
  if (p.danger_signs?.length) fields.push({ label: '위험 징후', value: p.danger_signs.join(', '), tone: 'alert' });
  if (p.required_capabilities?.length) fields.push({ label: '필요 진료', value: p.required_capabilities.join(', ') });
  const demands = Object.entries(p.resource_demands || {});
  if (demands.length) {
    fields.push({ label: '필요 자원', value: demands.map(([type, n]) => `${RESOURCE[type] || type} ${n}`).join(' · ') });
    if (hospital) {
      let tone = 'ok';
      const value = demands.map(([type, need]) => {
        const r = (hospital.resources || []).find((x) => x.resource_type === type), label = RESOURCE[type] || type;
        if (!r || r.status === 'unknown' || r.free == null) { if (tone !== 'alert') tone = 'wait'; return `${label} 미확인`; }
        if (r.free < need) tone = 'alert'; else if (r.stale && tone === 'ok') tone = 'wait';
        return `${label} ${Math.max(0, r.free)}`;
      }).join(' · ');
      fields.push({ label: '우리 병원 여유', value, tone });
    }
  }
  return fields;
}

export function severityLabel(p) {
  if (p.priority_source === 'unknown') return '중증도 미확인';
  if (p.effective_priority >= 4) return '긴급';
  if (p.effective_priority >= 3) return '중증';
  if (p.effective_priority >= 2) return '중등증';
  return '경증';
}

// 기존 병원 웹의 acceptAvailability와 같은 기준: 수용 제한 중이거나 서버 기준 여유가 모자라면 수용 가능을 막는다.
export function acceptBlock(patient, hospital, now) {
  if (!hospital) return null;
  const capabilities = new Set(patient.required_capabilities || []);
  for (const r of hospital.restrictions || []) {
    if (r.scope !== 'all' && !capabilities.has(r.scope)) continue;
    if (r.cleared_at != null) continue;
    if (r.ends_at != null && r.ends_at <= now) continue;
    if (r.starts_at != null && r.starts_at > now) continue;
    if ((r.status || 'active') === 'active') return `수용 제한 중(${r.scope === 'all' ? '기관 전체' : r.scope} · ${r.reason})이라 수용 가능으로 답할 수 없습니다.`;
  }
  for (const [type, need] of Object.entries(patient.resource_demands || {})) {
    const r = (hospital.resources || []).find((x) => x.resource_type === type);
    if (r && r.status !== 'unknown' && r.free != null && r.free < need) {
      return `${RESOURCE[type] || type} 여유가 서버 기준 ${Math.max(0, r.free)}이라 수용 가능으로 답할 수 없습니다.`;
    }
  }
  return null;
}

// 확정 전 대화: 문의 한 건이 가진 질문·답변과 회신 상태를 시간순 말풍선으로 편다.
export function inquiryLog(q) {
  const log = [];
  if (!q) return log;
  if (q.question) log.push({ kind: 'me', text: q.question, id: `q:${q.id}:question` });
  if (q.answer) log.push({ kind: 'crew', text: q.answer, id: `q:${q.id}:answer` });
  const notice = {
    CHECKING: q.question ? null : '확인 중으로 회신했습니다.',
    ACCEPTED: '수용 가능으로 회신했습니다.',
    DECLINED: `수용 불가(${q.reason || '사유 없음'})로 회신했습니다.`,
    CANCELLED: '구급대가 이 문의를 종료했습니다.',
    EXPIRED: '수용 응답 유효시간이 지났습니다.',
    CONFIRMED: '구급대가 우리 병원으로 목적지를 확정했습니다.',
  }[q.state];
  if (notice) log.push({ kind: 'notice', text: notice, id: `q:${q.id}:${q.state}` });
  return log;
}

// 확정 후 대화: 서버 대화 항목을 말풍선으로 바꾼다. 병원이 보낸 것만 내 말풍선이다.
export function chatLog(items) {
  return [...items].sort((a, b) => {
    for (let i = 0; i < 3; i++) { const d = (a.order_key?.[i] ?? 0) - (b.order_key?.[i] ?? 0); if (d) return d; }
    return 0;
  }).map((item) => {
    if (item.kind === 'system') return { kind: 'notice', text: item.text || '', id: item.id, at: item.at,
      routine: ['patient_updated', 'patient_information_amended'].includes(item.type)
        || item.text === '이송 관련 기록이 등록됐습니다.' };
    return { kind: item.sender_role === 'hospital' ? 'me' : 'crew', text: item.body ?? item.text ?? '', id: item.id, at: item.at, sender: item.sender_name || '' };
  });
}

export function compactChatLog(entries) {
  const result = [];
  for (const entry of entries) {
    const last = result[result.length - 1];
    if (entry.routine && last?.kind === 'routine-group') last.entries.push(entry);
    else if (entry.routine) result.push({kind: 'routine-group', id: entry.id, entries: [entry]});
    else result.push(entry);
  }
  return result;
}

export function actionCount(threads, now) {
  return threads.filter((t) => threadStatus(t, now).tone === 'alert').length;
}

// 목록 한 줄에 필요한 값: 안 읽은 구급대 메시지 수, 마지막 활동 시각, 미리 보기.
// seenId는 이 화면에서 마지막으로 본 메시지 번호다. 내가 보낸 메시지는 세지 않는다.
export function chatSummary(items, seenId = 0) {
  let unread = 0, last = null, maxId = 0;
  for (const item of items) {
    if (item.kind !== 'message') continue;
    maxId = Math.max(maxId, item.message_id ?? 0);
    if (item.sender_role !== 'hospital' && (item.message_id ?? 0) > seenId) unread++;
    if (!last || (item.message_id ?? 0) > (last.message_id ?? 0)) last = item;
  }
  return { unread, maxId, lastAt: last?.at ?? null,
    preview: last ? (last.sender_role === 'hospital' ? '나: ' : '') + (last.body ?? last.text ?? '') : '' };
}

// 목록 배지 숫자: 답해야 하는 일(새 문의, 답변 도착, 상태 변경) 1건 + 안 읽은 구급대 메시지.
export function threadBadge(thread, now, unread = 0) {
  return unread + (threadStatus(thread, now).tone === 'alert' ? 1 : 0);
}
