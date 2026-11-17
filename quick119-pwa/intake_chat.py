"""Deterministic chat presentation derived only from persisted runtime records.

No clinical interpretation, new database, message write, or cursor is introduced.
Hospital access must be authorized by the existing runtime before this adapter runs.
"""

WELCOME = (
    "시스템과 구급대의 현장 접수를 시작했습니다. 확인한 환자 상태와 활력징후를 남겨 주세요. "
    "병원 수용 후 목적지를 확정하면 병원이 이 대화에 참여하고 앞선 보고도 함께 확인합니다."
)
REPORT_RECEIVED = (
    "현장 보고가 이송 기록에 저장됐습니다. 확인하거나 수정할 내용이 있으면 이어서 남겨 주세요."
)


def _private_report(item):
    return (item.get('kind') == 'message' and item.get('sender_role') == 'patient'
            and 'hospital_session_id' in item and item['hospital_session_id'] is None)


def report_receipt(item):
    """A stable receipt for an upstream-saved private crew message, never its content."""
    if not _private_report(item):
        return None
    return {
        'id': f"system-report:{item['message_id']}",
        'kind': 'system', 'type': 'intake_report_received',
        'sender_role': 'system', 'sender_name': '시스템',
        'conversation_id': item['conversation_id'],
        'source_message_id': item['message_id'],
        'hospital_session_id': None,
        'at': item['at'], 'text': REPORT_RECEIVED,
        # Upstream messages retain their original three components. Consumers
        # compare an absent fourth component as zero, placing this just after it.
        'order_key': [*item['order_key'][:3], 1],
    }


def participants(snapshot):
    result = [
        {'id': 'system', 'role': 'system', 'name': '시스템'},
        {'id': 'crew', 'role': 'crew', 'name': '구급대'},
    ]
    active = snapshot.get('active_hospital_session')
    if active:
        result.append({
            'id': 'hospital:' + active['hospital_id'], 'role': 'hospital',
            'name': active.get('hospital_name') or active['hospital_id'],
            'hospital_id': active['hospital_id'], 'hospital_session_id': active['id'],
        })
    return result


def _present_items(items):
    result = []
    for item in items:
        if item.get('kind') == 'system' and item.get('type') == 'patient_created':
            result.append({**item, 'text': WELCOME})
        else:
            result.append(item)
        receipt = report_receipt(item)
        if receipt:
            result.append(receipt)
    result.sort(key=lambda item: tuple(item['order_key'][:3]) + (item['order_key'][3] if len(item['order_key']) > 3 else 0,))
    return result


def crew_conversation(snapshot):
    return {**snapshot, 'participants': participants(snapshot),
            'items': _present_items(snapshot['items'])}


def crew_message_response(response):
    receipt = report_receipt(response['message'])
    return {**response, 'system_items': [receipt] if receipt else []}


def confirmed_hospital_conversation(authorized, crew_page, hospital_id):
    """Share pre-join crew reports with the current confirmed hospital only.

    Both pages must use identical cursors/limits. We walk the crew page's global
    streams, filtering by the authorized hospital sessions and their event ranges.
    Do not intersect event IDs from the two reads: a new event can be persisted
    between them and would otherwise be skipped when the global cursor advances.
    Session ranges plus hospital_id mirror the runtime's access rule, including
    new events without leaking another hospital's events or session gaps.
    """
    active = authorized.get('active_hospital_session')
    current = crew_page.get('active_hospital_session')
    if (not active or not current or active.get('hospital_id') != hospital_id
            or current.get('hospital_id') != hospital_id or active['id'] != current['id']):
        # A former hospital, or a handoff between the two reads: retain upstream's
        # restricted view/cursors. Never expand private history on a stale grant.
        return authorized
    sessions = authorized.get('hospital_sessions', [])
    session_ids = {session['id'] for session in sessions}
    visible = []
    for item in crew_page['items']:
        if item.get('kind') == 'message':
            own_session = item.get('hospital_session_id') in session_ids
            earlier_report = (_private_report(item)
                              and item['order_key'][0] < active['joined_event_id'])
            if own_session or earlier_report:
                visible.append(item)
        elif item.get('kind') == 'system':
            event_id = item['event_id']
            own_event = item.get('hospital_id') in {None, hospital_id} and any(
                event_id >= session['joined_event_id']
                and (session.get('left_event_id') is None or event_id <= session['left_event_id'])
                for session in sessions)
            welcome = item.get('type') == 'patient_created' and event_id < active['joined_event_id']
            if own_event or welcome:
                visible.append(item)
    return {
        **authorized, 'items': _present_items(visible),
        'participants': participants(crew_page),
        'cursors': crew_page['cursors'], 'has_more': crew_page['has_more'],
        'more': crew_page['more'],
    }
