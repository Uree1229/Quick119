// 새로 들어온 수용 문의를 어느 탭에서든 알린다. 알림은 문의로 안내만 하고, 수용·불가 응답은 문의 상세에서 한다.
export const MAX_SHOWN = 3;

export class InquiryAlerts {
  constructor() { this.reset(); }
  reset() { this.known = null; this.items = new Map(); }

  // 서버의 대기 문의 목록을 반영한다. 화면에 보일 알림이 달라졌으면 true.
  sync(open) {
    const ids = new Set(open.map((q) => q.id));
    let changed = false;
    // 첫 조회는 기준선이다. 페이지를 열 때 이미 있던 문의는 목록에서 보이므로 알리지 않는다.
    if (this.known !== null) {
      for (const q of open) {
        if (!this.known.has(q.id)) {
          this.items.set(q.id, q);
          changed = true;
          continue;
        }
        const previous = this.items.get(q.id);
        if (!previous) continue;
        const before = previous.patient, after = q.patient;
        if (before?.age !== after?.age ||
            before?.symptoms !== after?.symptoms ||
            before?.priority_source !== after?.priority_source ||
            before?.effective_priority !== after?.effective_priority) changed = true;
        this.items.set(q.id, q);
      }
    }
    // 대기 목록에서 빠진 문의(다른 담당자 응답·만료·취소)의 알림은 남기지 않는다.
    for (const id of [...this.items.keys()]) {
      if (!ids.has(id)) { this.items.delete(id); changed = true; }
    }
    this.known = ids;
    return changed;
  }

  dismiss(id) { return this.items.delete(id); }

  // 급한 문의가 위로 오도록 우선순위가 높은 순, 같으면 먼저 온 순.
  list() {
    return [...this.items.values()].sort((a, b) =>
      (b.patient?.effective_priority ?? 0) - (a.patient?.effective_priority ?? 0) || a.id - b.id);
  }
}

export function alertsMarkup(items, { esc, severity }) {
  if (!items.length) return '';
  const shown = items.slice(0, MAX_SHOWN), rest = items.length - shown.length;
  const cards = shown.map((q) => {
    const p = q.patient || {}, [label, cls] = severity(p);
    return `<div class="inq-alert" data-alert-id="${esc(q.id)}">
      <div class="inq-alert-head"><span class="sev-dot ${esc(cls)}" aria-hidden="true"></span><span class="sr-only">${esc(label)}</span><b>새 수용 문의</b></div>
      <div class="inq-alert-title">${esc(p.age ?? '?')}세 · ${esc(p.symptoms)}</div>
      <div class="row"><button type="button" class="btn-small inq-alert-view" data-alert-view="${esc(q.id)}">문의 보기</button><button type="button" class="btn-small" data-alert-dismiss="${esc(q.id)}">닫기</button></div>
    </div>`;
  }).join('');
  const more = rest > 0 ? `<div class="inq-alert inq-alert-more"><span>새 문의가 ${rest}건 더 있습니다.</span><button type="button" class="btn-small" data-alert-view-all>문의 목록 열기</button></div>` : '';
  return cards + more;
}
