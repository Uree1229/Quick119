"""최근 수용 불가 사유를 다음 자동 문의 대상 선정에 반영한다.

병원이 같은 조건의 환자를 '병상'·'인력'·'장비'·'진료 불가'로 거절했다면 짧은 시간 안에는
같은 이유로 다시 거절할 가능성이 높다. 그런 병원에는 자동 문의를 보내지 않아 병원이 같은
거절을 반복하지 않게 한다.

- 자동 문의(첫 문의·대체병원 문의) 대상에서만 뺀다. 담당자가 직접 보내는 문의와 이미 보낸
  문의, 수용 응답과 목적지 확정은 바꾸지 않는다.
- '병상'은 필요한 자원 종류가 겹치는 환자에게 적용한다. '인력'·'장비'·'진료 불가'는 거절된
  환자의 필요 진료 분야를 모두 요구하는 환자에게만 적용하며, 필요 진료가 없던 거절은 넓히지 않는다.
- '기타'와 미응답은 사유를 해석할 수 없으므로 반영하지 않는다.
- 이후 같은 조건의 환자를 수용했거나, 거절 뒤 갱신된 최신 자원 정보로 여유가 확인되면 거절을 잊는다.
- 기억 시간(10분)은 시연을 위한 운영 설정값이며 임상 기준이 아니다.
"""
from . import db, service

DECLINE_MEMORY_SECONDS = 600
CAPACITY_REASONS = frozenset({'병상'})
SERVICE_REASONS = frozenset({'인력', '장비', '진료 불가'})
SKIP_EVENT = 'inquiry_skipped_recent_decline'
# 병원이 자원을 직접 갱신했거나, 퇴실·미도착으로 자리가 났을 수 있는 사건.
ROOM_EVENTS = ('resource_updated', 'discharged', 'external_arrival_discharged', 'no_show')


def covers(reason, source, target):
    """`source` 환자에 대한 응답이 `target` 환자에게도 같은 뜻인지."""
    if reason in CAPACITY_REASONS:
        return bool(set(source['resource_demands']) & set(target['resource_demands']))
    if reason in SERVICE_REASONS:
        need = set(source['required_capabilities'])
        return bool(need) and need <= set(target['required_capabilities'])
    return False


def _room_confirmed_since(conn, now, hospital_id, patient, decline_event_id):
    """거절 뒤 자리가 날 만한 사건이 있었고, 지금 자원 정보가 이 환자의 필요량을 채우는지.

    도착 처리처럼 여유가 늘지 않는 갱신은 근거로 쓰지 않는다. 거절이 자원 숫자보다 최신 정보다.
    """
    demands = patient['resource_demands']
    if not demands:
        return False
    marks = ','.join('?' for _ in ROOM_EVENTS)
    if not conn.execute(f'SELECT 1 FROM events WHERE hospital_id=? AND id>? AND type IN ({marks}) LIMIT 1',
                        (hospital_id, decline_event_id, *ROOM_EVENTS)).fetchone():
        return False
    resources = {r['resource_type']: r for r in service.resource_view(conn, hospital_id, now)}
    for kind, need in demands.items():
        r = resources.get(kind)
        if r is None or r['free'] is None or r['stale'] or r['free'] < need:
            return False
    return True


def active_decline(conn, now, patient, hospital_id):
    """이 환자에게도 유효한 최근 거절을 돌려준다. 없으면 None."""
    events = db.rows(conn.execute(
        "SELECT id, at, patient_id, data FROM events WHERE hospital_id=? AND type='inquiry_responded' "
        'AND at>? AND at<=? ORDER BY id DESC', (hospital_id, now - DECLINE_MEMORY_SECONDS, now)))
    patients = {patient['id']: patient}
    accepted = []
    for event in events:
        data = event['data'] if isinstance(event['data'], dict) else {}
        action = data.get('action')
        if action not in ('accept', 'decline'):
            continue
        source = patients.get(event['patient_id'])
        if source is None:
            source = patients[event['patient_id']] = service._patient(conn, event['patient_id'])
        if action == 'accept':
            accepted.append(source)
            continue
        reason = data.get('reason')
        if not covers(reason, source, patient):
            continue
        # 거절 뒤 같은 조건의 환자를 받았다면 그 거절은 지나간 상황이다.
        if any(covers(reason, source, later) for later in accepted):
            continue
        if reason in CAPACITY_REASONS and _room_confirmed_since(conn, now, hospital_id, patient, event['id']):
            continue
        return dict(hospital_id=hospital_id, reason=reason, declined_at=event['at'],
                    same_patient=event['patient_id'] == patient['id'])
    return None


def note_skip(conn, now, patient, decline):
    """다른 환자의 거절로 이 환자의 자동 문의에서 뺀 병원을 한 번만 기록한다."""
    if decline['same_patient']:
        return  # 같은 환자의 이전 문의 결과는 이미 기록에 있다.
    if conn.execute('SELECT 1 FROM inquiries WHERE patient_id=? AND hospital_id=?',
                    (patient['id'], decline['hospital_id'])).fetchone():
        return  # 이미 문의한 병원이다. 제외 안내가 오히려 헷갈린다.
    for event in db.rows(conn.execute('SELECT data FROM events WHERE patient_id=? AND hospital_id=? AND type=?',
                                      (patient['id'], decline['hospital_id'], SKIP_EVENT))):
        if isinstance(event['data'], dict) and event['data'].get('declined_at') == decline['declined_at']:
            return
    service.log(conn, now, 'system', SKIP_EVENT, patient['id'], decline['hospital_id'],
                reason=decline['reason'], declined_at=decline['declined_at'],
                memory_seconds=DECLINE_MEMORY_SECONDS)
