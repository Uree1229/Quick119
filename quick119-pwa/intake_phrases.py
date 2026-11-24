"""Bounded spoken forms for the crew's intake conversation."""
import json
import re

from server import db, service, voice_followup, voice_input, voice_terms

_REQUEST = r'(?:해)?(?:줘|주세요|주십시오|줄래요?|주시겠어요|줄수있어)?'
_DIRECT = r'(?:줘|주세요|주십시오|줄래요?|주시겠어요|줄수있어)?'
_PLACE = r'(?:(?:근처|주변|가까운|이송할|갈수있는|수용가능한|수용할수있는)?(?:후보)?(?:병원|응급실)(?:후보)?(?:을|를|좀)?)'
_PATTERNS = {
    'submit': [
        rf'(?:{_PLACE})?(?:검색|탐색)(?:을|를)?(?:시작|진행){_REQUEST}',
        rf'{_PLACE}(?:(?:검색|탐색|조회|추천|추천시작){_REQUEST}|(?:찾아(?:봐)?|알아봐){_DIRECT})',
        rf'(?:검색|탐색){_REQUEST}',
    ],
    'review': [rf'(?:병원인계용|인계용|환자정보|접수내용|지금까지입력한내용)(?:전체)?요약(?:을)?(?:보여|읽어){_DIRECT}'],
    'undo': [rf'(?:방금|직전|마지막)(?:거|꺼|것|입력|수정)(?:를|을)?(?:원복{_REQUEST}|되돌려{_DIRECT})'],
    'missing': [r'(?:아직)?(?:안채운|비어있는|남은|빠진)(?:칸|정보|항목)(?:이|은)?(?:뭐야|뭔가요|뭐가있어)',
                r'(?:뭘더입력해야해|더물어볼게뭐야)'],
    'confirm': [rf'(?:지금|이대로|이내용으로|입력한대로|현재내용으로)(?:환자정보)?확정{_REQUEST}'],
}


def extend_commands(dictionary):
    for action, patterns in _PATTERNS.items():
        dictionary['commands']['actions'][action].setdefault('patterns', []).extend(patterns)


# Both sides must refer to the same patient field. Only the replacement goes to
# the team's unit/range validator. The complete utterance remains the evidence.
_SEX = r'(?:남자|여자|남성|여성)'
_NUMBER = r'[0-9]{1,3}'
_BP = rf'{_NUMBER}\s*(?:에|/)\s*{_NUMBER}'
_SHAPES = (
    ('성별', _SEX, {'sex'}),
    ('나이', _NUMBER + r'\s*(?:세|살)', {'age', 'age_category'}),
    ('맥박|심박수', _NUMBER + r'\s*(?:회|번|bpm)?', {'pulse'}),
    ('산소포화도', _NUMBER + r'\s*(?:%|퍼센트)?', {'spo2'}),
    ('체온', _NUMBER + r'(?:\.[0-9])?\s*(?:도)?', {'temperature'}),
    ('호흡수', _NUMBER + r'\s*(?:회|번)?', {'respiratory_rate'}),
    ('혈당', _NUMBER, {'glucose'}),
    ('혈압', _BP, {'systolic_bp', 'diastolic_bp'}),
)
_SEPARATOR = r'\s*(?:이|가|은|는)?\s*(?:아니라|아니고|말고)\s*[,，]?\s*'
_END = rf'\s*(?:이야|야|이에요|예요|에요|입니다|(?:으?로)?\s*(?:정정|수정|변경)\s*(?:해\s*)?(?:줘|주세요)?)?\s*[.!]?'


def correction(text):
    """Return (replacement, expected fields), or an empty replacement to clarify."""
    if not re.search(r'아니라|아니고|말고', text):
        return None
    for label, value, fields in _SHAPES:
        label_part = rf'(?:(?:{label})(?:이|가|은|는)?\s*)'
        if 'age' in fields or 'sex' in fields:
            label_part += '?'
        pattern = (rf'(?:환자(?:의|는|가)?\s*)?{label_part}{value}{_SEPARATOR}'
                   rf'(?:(?:{label})\s*)?(?P<new>{value}){_END}')
        match = re.fullmatch(pattern, text.strip(), re.IGNORECASE)
        if match:
            return label.split('|')[0] + ' ' + match['new'].strip(), fields
    # Do not extract the negated number/sex from a quoted or ambiguous correction.
    if re.search(_SEX + r'|나이|[0-9]\s*(?:세|살)|맥박|심박수|산소포화도|체온|호흡수|혈당|혈압', text):
        return '', set()
    return None


_AGE_OR_SEX = re.compile(r'남자|여자|남성|여성|성별|나이|\d+\s*(?:세|살)|[한두세네다섯여섯일곱여덟아홉열스물서른마흔쉰]+\s*살')
_DEMOGRAPHIC_DENIAL = re.compile(
    r'(?:남자|여자|남성|여성)(?:\s*(?:아이|어린이|환자))?'
    r'(?:이|가|은|는|인\s*것이)?\s*(?:아니|아닙|아님|아닌|아닐|인지(?:는|도)?\s*(?:모|불|미확인))'
    r'|(?:세|살)(?:이|가|은|는)?\s*(?:아니|아닙|아님|아닌|아닐)')


def ambiguous_demographics(text):
    # A caregiver's own age or sex and an explicitly negated demographic
    # cannot be credited as observations of the patient.
    if _DEMOGRAPHIC_DENIAL.search(text):
        return True
    caregiver = False
    for segment in re.split(r'[,，.;!?\n]', text):
        if '환자' in segment or '본인' in segment:
            caregiver = False
        if '보호자' in segment:
            caregiver = True
        if caregiver and _AGE_OR_SEX.search(segment):
            return True
    return False


def bare_edit(text):
    return bool(re.fullmatch(r'(?:이거|이것|내용|정보)?\s*(?:수정|정정)(?:\s*해\s*(?:줘|주세요)?)?[.!]?', text.strip()))


def record_utterance(ctx, row, body, text, mode):
    conn, now = ctx.conn, ctx.now
    sid, uid = row['id'], body['utterance_id']
    service._require(not conn.execute(
        'SELECT 1 FROM voice_utterances WHERE session_id=? AND utterance_id=?', (sid, uid)).fetchone(),
        'VOICE_UTTERANCE_FINALIZED', '이미 처리한 발화입니다.', 409)
    service._require(conn.execute(
        'SELECT count(*) FROM voice_utterances WHERE session_id=?', (sid,)).fetchone()[0] < 100,
        'VOICE_SESSION_FULL', '세션당 발화 100개를 초과했습니다.', 409)
    conn.execute('INSERT INTO voice_utterances VALUES(?,?,?,?,?,?,?)', (sid, uid, text, mode, 'app_transcript', now, now))


def clarification(ctx, row, mode, prompt, action=None):
    return dict(session=voice_input._public(ctx.conn, row), operational_effect='voice_command_needs_clarification',
                effective_mode=mode, speak_text=prompt, command={'action': action} if action else None)


def unavailable_field(text):
    # Full statements only. A request to read a saved value stays a command.
    compact = re.sub(r'\s+', '', text)
    match = re.fullmatch(r'(?:환자의?)?(.+?)[:：]?(미확인|미측정|확인불가)(?:입니다|이에요|예요)?[.!]?', compact)
    if match:
        names = {name.replace(' ', ''): key for key, name in voice_input._ANSWER_LABELS.items()}
        names.update(심박수='pulse', 발생시간='onset_at', 의식상태='consciousness', 호흡상태='breathing')
        # 나이 ends in 이, so resolve the whole name before stripping a subject particle.
        key = names.get(match[1]) or names.get(re.sub(r'[은는이가]$', '', match[1]))
        if key:
            return key, voice_input._ANSWER_LABELS[key], '확인 불가' if match[2] == '확인불가' else match[2]
    return None


def handle_input(ctx, row, body, text):
    top_command = voice_terms.parse_command(text)
    variants = ([text, *body.get('recognition_alternatives', [])]
                if top_command['action'] in ('observe', 'unknown') else [text])
    action = None
    mode = 'command'
    unavailable = [unavailable_field(phrase) for phrase in variants]
    if any(unavailable):
        mode = 'dictation'
        if all(item == unavailable[0] for item in unavailable):
            question, label, status = unavailable[0]
            prompt = f'이번 보고 · {label}: {status}'
            fields = json.loads(row['fields'])
            if any(key in fields for key in voice_followup.lookup(question)['fields']):
                prompt += ' · 이전 기록 유지'
        else:
            prompt = '인식 후보가 서로 다릅니다. 해당 항목을 다시 알려 주세요.'
    elif any(ambiguous_demographics(phrase) for phrase in variants):
        mode = 'dictation'
        prompt = '환자 본인에게 확인한 내용만 따로 알려 주세요.'
    elif bare_edit(text):
        action, prompt = 'edit', '수정할 항목과 새 값을 알려 주세요.'
    else:
        try:
            # The runtime saves the utterance before resolving commands. Undo
            # that tentative write before recording a recoverable prompt once.
            with db.transaction(ctx.conn):
                return voice_input._utterance(ctx.conn, ctx.now, row, body, text, 'app_transcript')
        except service.ServiceError as error:
            if error.code not in ('VOICE_NOTHING_TO_UNDO', 'VOICE_COMMAND_UNRESOLVED'):
                raise
            action = voice_terms.parse_command(text)['action']
            prompt = {'undo': '취소할 입력이 없습니다.', 'edit': '수정할 항목과 새 값을 알려 주세요.',
                      'add': '추가할 항목과 값을 알려 주세요.', 'remove': '삭제할 항목을 알려 주세요.'}.get(action, error.message)
    record_utterance(ctx, row, body, text, mode)
    return clarification(ctx, row, mode, prompt, action)


def apply_correction(ctx, row, body, text, parsed):
    conn, now = ctx.conn, ctx.now
    record_utterance(ctx, row, body, text, 'command')
    replacement, fields = parsed
    if replacement:
        extracted = voice_input._extract(replacement, now, row)
        if set(extracted['fields']) == fields:
            result = voice_input._edit(conn, now, row, extracted['fields'], [], 'correction',
                                       'voice_command', {key: [text] for key in fields}, extracted['warnings'])
            return dict(result, command={'action': 'edit'}, effective_mode='command', extraction=extracted,
                        speak_text=voice_terms.readback(extracted['fields']))
    return clarification(ctx, row, 'command', '어느 값으로 고칠지 확인해 주세요. 항목과 새 값을 한 번 더 말해 주세요.')
