"""Synthetic transport-case registry used by the local PWA."""

from . import service


def cases(conn, limit=100):
    patients = [dict(row) for row in conn.execute(
        "SELECT p.id,p.case_number,p.age,p.symptoms,p.requested_at,p.state,p.destination_hospital_id,"
        "p.needs_adjustment,c.state AS conversation_state,h.name AS destination_hospital_name "
        ",EXISTS(SELECT 1 FROM conversation_hospital_sessions s "
        "WHERE s.conversation_id=c.id AND s.left_at IS NULL) AS has_active_hospital "
        ",EXISTS(SELECT 1 FROM patient_amendments a WHERE a.patient_id=p.id) "
        "OR EXISTS(SELECT 1 FROM call_records r WHERE r.patient_id=p.id) "
        "OR EXISTS(SELECT 1 FROM accounting_reconciliations r "
        "WHERE r.target_kind='patient' AND r.target_id=p.id) AS has_immutable_history "
        "FROM patients p LEFT JOIN conversations c ON c.patient_id=p.id "
        "LEFT JOIN hospitals h ON h.id=p.destination_hospital_id "
        "WHERE p.reporter_role='transporter' AND p.id LIKE 'Q119-TEST-%' "
        "ORDER BY p.requested_at DESC,p.rowid DESC LIMIT ?", (limit,))]
    if not patients:
        return []
    patient_ids = [patient['id'] for patient in patients]
    placeholders = ','.join('?' for _ in patient_ids)
    inquiries = [dict(row) for row in conn.execute(
        "SELECT i.id,i.patient_id,i.hospital_id,h.name AS hospital_name,i.state "
        "FROM inquiries i JOIN hospitals h ON h.id=i.hospital_id "
        f"WHERE i.patient_id IN ({placeholders}) ORDER BY i.patient_id,i.id", patient_ids)]
    by_patient = {}
    for inquiry in inquiries:
        by_patient.setdefault(inquiry['patient_id'], []).append({
            key: inquiry[key] for key in ('id', 'hospital_id', 'hospital_name', 'state')})
    for patient in patients:
        patient['inquiries'] = by_patient.get(patient['id'], [])
        state = patient['state']
        patient['deletable'] = (state in ('WAITING', 'CANCELLED', 'COMPLETED')
                                and not patient['has_active_hospital']
                                and not patient['has_immutable_history'])
        patient['delete_block_reason'] = (
            '감사 이력이 있는 접수는 기록 보존을 위해 삭제할 수 없습니다.'
            if patient['has_immutable_history'] else
            '확정·이송·입원 중인 접수는 삭제할 수 없습니다.'
            if state not in ('WAITING', 'CANCELLED', 'COMPLETED') or patient['has_active_hospital'] else '')
        patient['active'] = (state in ('WAITING', 'CONFIRMED', 'IN_TRANSIT')
                             and patient['conversation_state'] != 'CLOSED')
        states = {inquiry['state'] for inquiry in patient['inquiries']}
        if not patient['active']:
            stage = '종료'
        elif patient['needs_adjustment']:
            stage = '취소 후 재배정'
        elif state == 'IN_TRANSIT':
            stage = '이송 중'
        elif state == 'CONFIRMED':
            stage = '병원 확정'
        elif 'ACCEPTED' in states:
            stage = '수용 응답 · 확정 대기'
        elif states.intersection(service.OPEN_INQUIRY):
            stage = '수용 문의 중'
        else:
            stage = '접수 중'
        patient['stage'] = stage
    return patients
