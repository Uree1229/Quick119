"""화면 연결용 추천 응답. 계산 결과 신선도와 워커 가동 상태를 분리한다."""
from contextlib import contextmanager
import uuid

from . import planning, service, allocation_input_review, db, decline_memory


@contextmanager
def _read_snapshot(conn):
    """One deferred read snapshot; do not acquire an IMMEDIATE write lock."""
    name = 'allocation_input_view_' + uuid.uuid4().hex
    conn.execute('SAVEPOINT ' + name)
    try:
        yield
    except BaseException:
        conn.execute('ROLLBACK TO ' + name)
        raise
    finally:
        conn.execute('RELEASE ' + name)


def patient_input_review(conn, now, pid):
    with _read_snapshot(conn):
        patient = service._patient(conn, pid)
        return dict(patient_id=pid, patient_version=patient['version'], patient_state=patient['state'],
                    input_review=allocation_input_review.assess(patient, now))


def candidates(conn, now, pid, estimator):
    with _read_snapshot(conn):
        result = service.candidates(conn, now, pid, estimator)
        patient = service._patient(conn, pid)
        return dict(result, input_review=allocation_input_review.assess(patient, now))


def search_and_inquire(conn, now, pid, estimator):
    """Select and contact the first three eligible candidates atomically and idempotently."""
    with db.transaction(conn):
        patient = service._patient(conn, pid)
        service._require(patient['state'] == 'WAITING', 'INVALID_STATE',
                         '수용 문의 전 상태의 환자만 후보를 탐색할 수 있습니다', 409, state=patient['state'])
        result = service.candidates(conn, now, pid, estimator)
        eligible, skipped = [], []
        for candidate in result['candidates']:
            if len(eligible) == 3:
                break
            if not candidate['auto_inquiry_eligible']:
                continue
            # 같은 조건의 환자를 최근 같은 사유로 거절한 병원은 첫 자동 문의에서도 뺀다.
            decline = decline_memory.active_decline(conn, now, patient, candidate['hospital_id'])
            if decline:
                skipped.append(decline)
                continue
            eligible.append(candidate)
        existing = conn.execute('SELECT 1 FROM inquiries WHERE patient_id=? LIMIT 1', (pid,)).fetchone()
        hospital_ids = [candidate['hospital_id'] for candidate in eligible]
        if hospital_ids and not existing:
            for decline in skipped:
                decline_memory.note_skip(conn, now, patient, decline)
            service._send_inquiries(conn, now, pid, hospital_ids, actor='automatic_candidate_search')
        return dict(result, candidates=eligible,
                    automatic_inquiry=dict(hospital_ids=hospital_ids, already_started=bool(existing),
                                           skipped_recent_declines=[dict(hospital_id=d['hospital_id'], reason=d['reason'])
                                                                    for d in skipped]),
                    input_review=allocation_input_review.assess(patient, now))


def availability(runtime=None):
    state = runtime.snapshot()['workers']['planning']['state'] if runtime is not None else 'UNKNOWN'
    reasons = {'DISABLED': 'PLANNER_DISABLED', 'STOPPED': 'PLANNER_STOPPED', 'STOPPING': 'PLANNER_STOPPING',
               'ERROR': 'PLANNER_ERROR', 'STALLED': 'PLANNER_STALLED', 'UNKNOWN': 'PLANNER_UNOBSERVED'}
    accepting = None if state == 'UNKNOWN' else state not in ('DISABLED', 'STOPPED', 'STOPPING')
    return dict(worker_state=state, accepting_requests=accepting, reason_code=reasons.get(state))


def patient_result(conn, now, pid, runtime=None):
    with _read_snapshot(conn):
        result = planning.patient_result(conn, now, pid)
        patient = service._patient(conn, pid)
        # Top-level is current input guidance, including when no fresh plan is
        # available. The saved worker result stays an unmodified snapshot.
        return dict(result, input_review=allocation_input_review.assess(patient, now),
                    availability=availability(runtime))


def hospital_results(conn, now, hid, runtime=None, observed=None):
    observed = availability(runtime) if observed is None else observed
    with _read_snapshot(conn):
        return [dict(result, availability=dict(observed),
                     input_review=allocation_input_review.assess(service._patient(conn, result['patient_id']), now))
                for result in planning.hospital_results(conn, now, hid)]


def request(conn, now, pid, runtime=None):
    patient = service._patient(conn, pid)
    service._require(patient['state'] in ('WAITING',) + service.ACTIVE_TRANSPORT,
                     'INVALID_STATE', '도착 전 환자만 재계산할 수 있습니다', 409)
    observed = availability(runtime)
    service._require(observed['accepting_requests'] is not False, observed['reason_code'],
                     '현재 서버의 배정 계산이 비활성 또는 중단 상태입니다. 서버 담당자가 실행 상태를 확인해야 합니다.',
                     409, availability=observed)
    planning.request(conn, now, pid)
    return patient_result(conn, now, pid, runtime)
