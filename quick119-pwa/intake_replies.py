"""Short crew-chat replies based on the server's processed intake, never on a prediction."""
from datetime import datetime, timedelta, timezone


# These are the twelve groups shown in the crew's review dialog.
_FIELD_LABELS = {
    'age': '나이', 'age_group': '연령대', 'sex': '성별',
    'symptoms': '증상', 'denied_symptoms': '없는 증상', 'uncertain_symptoms': '미확인 증상',
    'consciousness': '의식', 'breathing': '호흡', 'systolic_bp': '수축기 혈압',
    'diastolic_bp': '이완기 혈압', 'pulse': '맥박', 'spo2': '산소포화도',
    'temperature': '체온', 'respiratory_rate': '호흡수', 'glucose': '혈당', 'onset_at': '발생 시각',
}
_BP = ('systolic_bp', 'diastolic_bp')
_UNITS = dict(age='세', systolic_bp=' mmHg', diastolic_bp=' mmHg', pulse='회/분',
              spo2='%', temperature='°C', respiratory_rate='회/분', glucose=' mg/dL')
_VALUES = dict(male='남성', female='여성', unknown='미확인', alert='명료',
               impaired='저하', unresponsive='무반응', normal='정상', abnormal='비정상', absent='없음')
_KST = timezone(timedelta(hours=9))
_CLARIFY = '항목과 값을 다시 알려주세요.'


def _format(key, value):
    if value is None or value == []:
        return '기록 없음'
    if isinstance(value, list):
        return ', '.join(value)
    if isinstance(value, str):
        return _VALUES.get(value, value)
    if key == 'onset_at':
        return datetime.fromtimestamp(value, _KST).strftime('%Y-%m-%d %H:%M (한국시간)')
    return f'{value}{_UNITS.get(key, "")}'


def _lines(values):
    paired = all(type(values.get(key)) in (int, float) for key in _BP)
    lines = []
    for key, label in _FIELD_LABELS.items():
        if key not in values:
            continue
        if paired and key in _BP:
            if key == _BP[0]:
                lines.append(f"혈압: {values['systolic_bp']}/{values['diastolic_bp']} mmHg")
        else:
            lines.append(f'{label}: {_format(key, values[key])}')
    return lines


def _targets(command):
    keys = command.get('fields') or ([command['field']] if command.get('field') else [])
    return [key for name in keys for key in (_BP if name == 'blood_pressure' else (name,))]


def _delta(before, after):
    return {key: after.get(key) for key in _FIELD_LABELS if before.get(key) != after.get(key)}


def _received(result, before_fields):
    session = result['session']
    fields = session['fields']
    extraction = result.get('extraction', {})
    evidence = extraction.get('fields', {})
    answer = result.get('answer', {})
    action = (result.get('command') or {}).get('action')
    if 'extraction' in result:
        keys = evidence
    elif answer:
        keys = answer['applied_fields']
    elif action in ('edit', 'add'):
        # Those command results do not expose an extraction. Repeated values still have
        # field evidence from the latest utterance even when there is no value change.
        utterances = session.get('utterances', [])
        latest = utterances[-1] if utterances else {}
        meta = session.get('field_metadata', {})
        keys = {key for key, entry in meta.items() if entry.get('source') == 'voice_command'
                and entry.get('observed_at') == latest.get('observed_at')
                and any(quote and quote in latest.get('transcript', '') for quote in entry.get('evidence', []))}
        keys.update(key for key in _delta(before_fields, fields) if key in fields)
    else:
        return _delta(before_fields, fields)
    values = {}
    for key in keys:
        if key not in _FIELD_LABELS or key not in fields:
            continue
        saved = fields[key]
        if isinstance(saved, list):
            if key in evidence:
                selected = [item for item in evidence[key] if item in saved]
            else:
                selected = [item for item in saved if item not in (before_fields.get(key) or [])]
                if not selected and len(saved) == 1:
                    selected = saved
            if selected:
                values[key] = selected
        elif key not in evidence or saved == evidence[key]:
            values[key] = saved
    return values


def reply_text(result, before_fields, *, question_id=None):
    """Return the private chat reply to one persisted utterance or command."""
    session = result['session']
    fields = session['fields']
    command = result.get('command') or {}
    action = command.get('action')
    effect = result.get('operational_effect')
    if effect == 'voice_command_needs_clarification':
        return result.get('speak_text') or '명령을 확인하기 어렵습니다. 한 가지 요청을 다시 알려주세요.'
    if action == 'submit':
        return '내용을 확인한 뒤 ‘확인하고 병원 탐색’을 눌러주세요.'
    if action == 'confirm':
        return '내용을 확인한 뒤 ‘환자정보 확정’을 눌러주세요.'
    if action == 'help':
        return '“맥박 88회로 수정”, “빠진 항목 알려줘”, “변경 이력”처럼 입력하세요.'
    if action in ('missing', 'repeat_question'):
        plan = result.get('followup', {})
        question = plan.get('next_question')
        if action == 'repeat_question':
            return question['question'] if question else '남은 확인 질문이 없습니다.'
        askable = sum(item['askable'] for item in plan.get('items', []))
        if question:
            return f"확인할 항목 {askable}개. {question['question']}"
        return '남은 항목은 미확인으로 남겨뒀습니다.' if plan.get('items') else '남은 확인 항목이 없습니다.'
    if action == 'history':
        revisions = result.get('revisions', [])
        targets = _targets(command)
        for rev in reversed(revisions):
            delta = _delta(rev['before_fields'], rev['after_fields'])
            if targets:
                delta = {key: val for key, val in delta.items() if key in targets}
            if delta:
                rows = [f"{_FIELD_LABELS[key]}: {_format(key, rev['before_fields'].get(key))} → {_format(key, value)}"
                        for key, value in delta.items()]
                return '최근 변경\n' + '\n'.join(rows)
        return '표시할 변경 이력이 없습니다.'
    if action == 'review':
        keys = _targets(command) or fields.keys()
        metadata = session.get('field_metadata', {})
        values = {key: '재확인 필요' if metadata.get(key, {}).get('needs_clarification') else fields.get(key)
                  for key in keys if key in _FIELD_LABELS}
        return '\n'.join(_lines(values)) or '확인할 환자 정보가 없습니다.'

    answer = result.get('answer') or {}
    if answer.get('unavailable'):
        if answer.get('unavailable_scope') == 'remaining':
            return '남은 항목은 미확인으로 남겼습니다.'
        qid = answer.get('question_id') or question_id
        return f"{'혈압' if qid == 'blood_pressure' else _FIELD_LABELS.get(qid, '해당 항목')}: 미확인"
    if answer and not answer['accepted']:
        return _CLARIFY
    if action == 'remove' and effect == 'draft_unchanged':
        return '삭제할 기록이 없습니다.'
    values = _received(result, before_fields)
    lines = _lines(values)
    if action == 'undo':
        return '직전 변경을 되돌렸습니다.' + ('\n' + '\n'.join(lines) if lines else '')
    extraction = result.get('extraction', {})
    unclear = set(extraction.get('clarification_fields', ()))
    unclear.update((result.get('recognition_context') or {}).get('disputed_fields', ()))
    labels = [_FIELD_LABELS[key] for key in _FIELD_LABELS if key in unclear and key not in values]
    if labels:
        lines.append('재확인 필요: ' + ', '.join(labels))
    elif not lines and (extraction.get('warnings') or result.get('recognition_context', {}).get('status') == 'needs_clarification'):
        return _CLARIFY
    if answer.get('review_required'):
        lines.append('해석한 값이 맞는지 확인해주세요.')
    return '\n'.join(lines) or ('변경된 항목이 없습니다.' if action in ('add', 'edit', 'remove') else '보고를 받았습니다.')
