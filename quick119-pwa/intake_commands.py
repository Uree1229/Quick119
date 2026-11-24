"""Connect the pinned voice parser to each synthetic crew chat in one transaction."""
import hashlib
import json
import re
from functools import lru_cache
from uuid import uuid4

import intake_phrases
from intake_replies import reply_text

from server import app, conversations, db, service, voice_followup, voice_input, voice_lexicon, voice_recognition_context, voice_terms
from speech_eval.asr_correction import Corrector, numeric_flags

PATH = r'/api/patients/(?P<pid>Q119-TEST-[A-Za-z0-9-]{1,80})/intake-draft'
ACTOR = 'quick119-intake'
require = service._require
# The twelve review rows. Derived fields (AVPU, age category, chief complaint) follow these automatically.
SUGGEST_FIELDS = ('age', 'age_group', 'sex', 'symptoms', 'denied_symptoms', 'uncertain_symptoms', 'consciousness',
                  'breathing', 'systolic_bp', 'diastolic_bp', 'pulse', 'spo2', 'temperature', 'respiratory_rate',
                  'glucose', 'onset_at')
LIST_FIELDS = {'symptoms', 'denied_symptoms', 'uncertain_symptoms'}
# Follow-up question whose derived peers (chief complaint, AVPU, age category) a confirmed value settles.
PEER_QUESTION = {'symptoms': 'symptoms', 'denied_symptoms': 'symptoms', 'uncertain_symptoms': 'symptoms',
                 'consciousness': 'consciousness', 'age': 'age', 'age_group': 'age'}
# Derived fields have no row of their own in the review dialog; a dispute on them shows on this row.
ROW_OF = {'chief_complaint': 'symptoms', 'seizure_ongoing': 'symptoms', 'avpu': 'consciousness', 'age_category': 'age'}


@lru_cache(maxsize=1)
def corrector():
    return Corrector()


def latest(ctx, pid):
    row = ctx.conn.execute(
        'SELECT id FROM voice_sessions WHERE patient_id=? AND actor_id=? ORDER BY rowid DESC LIMIT 1',
        (pid, ACTOR)).fetchone()
    return voice_input.get(ctx.conn, ctx.now, row['id'], ACTOR)['session'] if row else None


def _replace_spans(text, spans):
    """Apply (start, end, replacement) from the end so earlier offsets stay valid."""
    for start, end, new in sorted(spans, reverse=True):
        text = text[:start] + new + text[end:]
    return text


def suggest(text, alternatives, fields, metadata, now):
    """Best guesses for values the parser left empty or read one way. A person confirms each in the review dialog.

    Nothing here touches the draft. Sources: a dictionary term near a misheard word, a numeric
    candidate for a merged token (which may replace the parser's own reading, 120고 -> 120 vs 129),
    and the first recognition hypothesis when hypotheses disagree.
    A token with no single plausible reading (혈압 1876) is reported as needing direct input instead.
    """
    disputed = {key for key, meta in metadata.items()
                if meta.get('needs_clarification') and meta.get('recognition_context')}
    variants = []
    terms = corrector().suggest(text)
    if terms:
        variants.append(('term', _replace_spans(text, [(t['start'], t['end'], t['replacement']) for t in terms]),
                         ' · '.join(f"{t['original']} → {t['replacement']}" for t in terms)))
    flags = numeric_flags(text)
    fixed = [f for f in flags if type(f.get('candidate')) in (int, float)]
    if fixed:
        variants.append(('numeric', _replace_spans(text, [(f['start'], f['end'], str(f['candidate'])) for f in fixed]),
                         ' · '.join(f"원문 {f['original']}" for f in fixed)))
    if alternatives and disputed:
        variants.append(('alternative', text, None))
    found = []
    for source, variant, note in variants:
        parsed = voice_input._extract(variant, now)['fields']
        others = {}
        if source == 'alternative':
            for alt in alternatives:
                for key, value in voice_input._extract(alt, now)['fields'].items():
                    others.setdefault(key, []).append(value)
        for key in SUGGEST_FIELDS:
            if key not in parsed:
                continue
            if source == 'alternative' and key not in disputed and not (key in LIST_FIELDS and disputed & LIST_FIELDS):
                continue
            values = parsed[key] if key in LIST_FIELDS else [parsed[key]]
            for value in values:
                replaces = None
                if key in LIST_FIELDS:
                    if value in fields.get(key, []):
                        continue
                elif key in fields:
                    # A merged token may already have been read one way (120고 -> 120); offer the other reading.
                    if source != 'numeric' or fields[key] == value:
                        continue
                    replaces = fields[key]
                item = dict(field=key, value=value, source=source, note=note)
                if replaces is not None:
                    item['replaces'] = replaces
                if source == 'alternative':
                    rest = [v for v in others.get(key, []) if v != parsed[key]]
                    item['note'] = ('다른 후보 ' + ', '.join(map(str, rest[:2]))) if rest else '인식 후보가 서로 다름'
                found.append(item)
    needs = [dict(field='systolic_bp', note=f"원문 {f['original']}") for f in flags
             if f.get('kind') == 'merged_blood_pressure' and 'systolic_bp' not in fields]
    return found, needs


def _suggestion_id(item):
    return hashlib.sha1(json.dumps([item['field'], item['value']], ensure_ascii=False).encode()).hexdigest()[:12]


def view(ctx, pid, session):
    if session:
        # Advice is tied to the original utterance and survives reload and a later submit intent.
        rows = ctx.conn.execute("SELECT response FROM voice_requests WHERE actor_id=? AND operation=? "
                                "AND recorded_at>=? ORDER BY rowid DESC LIMIT 100",
                                (ACTOR, 'input:' + pid, session['created_at']))
        hints, suggested, needs = [], [], []
        for row in rows:
            receipt = json.loads(row['response'])
            if receipt.get('revision', [None])[0] == session['id']:
                hints.extend(receipt.get('hints', []))
                suggested.extend(receipt.get('suggestions', []))
                needs.extend(receipt.get('needs_input', []))
        fields, live, seen = session['fields'], [], set()
        for item in suggested:
            sid = _suggestion_id(item)
            if item['field'] in LIST_FIELDS:
                settled = item['value'] in fields.get(item['field'], [])
            elif 'replaces' in item:
                settled = fields.get(item['field']) != item['replaces']   # accepted, or changed another way
            else:
                settled = item['field'] in fields
            if sid in seen or settled:
                continue
            seen.add(sid)
            live.append(dict(item, id=sid))
        needs = list({n['note']: n for n in needs if n['field'] not in fields}.values())
        # Values the server will not confirm until a person re-says or edits them (VOICE_RECOGNITION_REVIEW_REQUIRED).
        for key, meta in session['field_metadata'].items():
            if meta.get('needs_clarification') and meta.get('recognition_context'):
                needs.append(dict(field=ROW_OF.get(key, key), note='인식 후보가 서로 다름 · 다시 말하거나 직접 수정',
                                  blocking=True))
        needs = list({(n['field'], n['note']): n for n in needs}.values())
        return {'asr_hints': list(dict.fromkeys(hints)), 'suggestions': live, 'needs_input': needs,
                **{key: session[key] for key in ('id', 'state', 'version', 'confirmed', 'fields', 'field_metadata',
                                              'warnings', 'followup', 'speak_text', 'latest_changes')}}


def scope(ctx, pid, *, editable=False):
    p = service._patient(ctx.conn, pid)
    require(p.get('extra', {}).get('synthetic_fixture') is True, 'INVALID', '가상 접수만 지원합니다.', 403)
    if editable:
        require(p['state'] == 'WAITING' and not ctx.conn.execute(
            'SELECT 1 FROM inquiries WHERE patient_id=? LIMIT 1', (pid,)).fetchone(),
            'INTAKE_LOCKED', '병원 문의가 시작됐습니다. 현재 대화를 다시 확인하세요.', 409)


def read(ctx, pid):
    scope(ctx, pid)
    return {'draft': view(ctx, pid, latest(ctx, pid))}


def write(ctx, pid, operation, action):
    data = ctx.body
    key = data.get('request_id')
    require(isinstance(key, str) and re.fullmatch(r'[a-zA-Z0-9-]{1,64}', key),
            'INVALID', '입력 요청 ID를 확인하세요.')
    body = dict(data, actor_id=ACTOR, client_request_id='intake-' + key)
    with db.transaction(ctx.conn):
        result = voice_input._operate(ctx.conn, ctx.now, operation + ':' + pid, body, action)
        return {**result, 'draft': view(ctx, pid, latest(ctx, pid))}


def check_version(data, session):
    current = [session['id'], session['version']] if session else None
    require('revision' in data and data['revision'] == current, 'INTAKE_STALE', '다른 입력이 반영됐습니다. 최신 초안을 확인하세요.', 409)


def record_reply(ctx, pid, text, *, command_text=None):
    # Only a private pre-inquiry draft can reach this point. Reuse the conversation
    # event cursor and the enclosing request's transaction and idempotency key.
    kind = 'crew_intake_reply'
    data = {'reply': text}
    if command_text is not None:
        data['command_text'] = command_text
    event_id = service.log(ctx.conn, ctx.now, ACTOR, kind, pid, **data)
    return conversations._event_item(dict(id=event_id, at=ctx.now, type=kind,
                                          hospital_id=None, data=data), None)


def input_report(ctx, pid):
    data = ctx.body
    text = data.get('text')
    require(isinstance(text, str) and 0 < len(text.strip()) <= 2000 and len(text) <= 2000, 'INVALID', '입력은 1~2,000자입니다.')
    require(data.get('source') in ('text', 'speech'), 'INVALID', '입력 종류를 확인하세요.')

    def apply(actor):
        scope(ctx, pid, editable=True)
        session = latest(ctx, pid)
        check_version(data, session)
        if not session or session['state'] == 'SUBMITTED':
            session = voice_input.create(ctx.conn, ctx.now, {
                'actor_id': actor, 'client_request_id': str(uuid4()), 'patient_id': pid})['session']
        key = data['request_id']
        common = dict(actor_id=actor, client_request_id=str(uuid4()), expected_version=session['version'],
                      text=text, utterance_id=key)
        if data.get('alternatives'):
            common['recognition_alternatives'] = data['alternatives']
        if data.get('question_id'):
            result = voice_input.answer_followup(ctx.conn, ctx.now, session['id'],
                dict(common, question_id=data['question_id'], profile=voice_followup.DEFAULT_PROFILE))
        else:
            # The outer idempotent operation already holds the voice transaction and pins this version.
            # R1 removes spaces for matching: without a longer neutral phrase, 환자 상태 contains 자상.
            transcript = dict(common, mode='auto', is_final=True)
            if 'recognition_alternatives' in transcript:
                transcript['recognition_alternatives'] = voice_recognition_context.validate(text, transcript['recognition_alternatives'])
            dictionary = voice_lexicon.snapshot(ctx.conn)
            dictionary['r1_terms'] = [dict(sets=[], expr=['환자 상태', '보호자 상태', '대상자 상태']), *dictionary['r1_terms']]
            intake_phrases.extend_commands(dictionary)
            with voice_terms.use_dictionary(dictionary):
                row = voice_input._row(ctx.conn, session['id'], actor, ctx.now)
                voice_input._mutable(row, session['version'])
                correction = intake_phrases.correction(text)
                if correction is None:
                    # The leading recognition hypothesis selects a command; the screen still
                    # handles confirmation, and observation values keep their review hypotheses.
                    alternatives = transcript.get('recognition_alternatives', [])
                    action = voice_terms.parse_command(text)['action']
                    if alternatives and action in ('submit', 'confirm'):
                        del transcript['recognition_alternatives']
                    result = intake_phrases.handle_input(ctx, row, transcript, text)
                else:
                    result = intake_phrases.apply_correction(ctx, row, transcript, text, correction)
        draft = result['session']
        command = (result.get('command') or {}).get('action')
        message = None
        applied_command = (command in {'observe', 'edit', 'add', 'remove', 'undo'}
                           and result.get('operational_effect') != 'voice_command_needs_clarification')
        if result.get('effective_mode') == 'dictation' or applied_command or data.get('question_id'):
            message = conversations.send_message(ctx.conn, ctx.now, pid, {
                'body': text, 'client_message_id': key, 'hospital_session_id': None, 'sender_name': '가상 응급대원'})['message']
        feedback = result.get('speak_text') or draft['speak_text']
        if command == 'history':
            rows = []
            for rev in result.get('revisions', []):
                old, new = rev['before_fields'], rev['after_fields']
                for field in sorted(old.keys() | new.keys()):
                    if old.get(field) != new.get(field):
                        rows.append(f"{voice_terms._LABELS.get(field, field)} {old.get(field, '미입력')} → {new.get(field, '삭제')}")
            feedback += ' ' + ' / '.join(rows)
        hints = []
        # Phone keyboard dictation arrives through the normal text input too.
        hints.extend(f"인식 원문 ‘{x['original']}’ · 교정 후보 ‘{x['replacement']}’" for x in corrector().suggest(text))
        hints.extend(f"수치 원문 ‘{x['original']}’ 확인 필요" for x in numeric_flags(text))
        suggestions, needs_input = [], []
        if result.get('effective_mode') == 'dictation' or data.get('question_id'):
            # Reports only: an explicit edit command already says the value the crew wants.
            alternatives = voice_recognition_context.validate(text, data['alternatives']) if data.get('alternatives') else []
            with voice_terms.use_dictionary(voice_lexicon.snapshot(ctx.conn)):
                suggestions, needs_input = suggest(text, alternatives, draft['fields'], draft['field_metadata'], ctx.now)
        reply = reply_text(result, session['fields'], question_id=data.get('question_id'))
        system_item = record_reply(ctx, pid, reply, command_text=text if message is None else None)
        return dict(message=message, system_items=[system_item], action=command,
                    effect=result.get('operational_effect'), feedback=feedback,
                    hints=hints, suggestions=suggestions, needs_input=needs_input,
                    input_id=key, revision=[draft['id'], draft['version']])
    return write(ctx, pid, 'input', apply)


def confirm(ctx, pid):
    data = ctx.body
    require(data.get('intent') in ('confirm', 'search'), 'INVALID', '확인할 작업을 선택하세요.')

    def apply(actor):
        scope(ctx, pid, editable=True)
        session = latest(ctx, pid)
        check_version(data, session)
        require(session is not None, 'VOICE_EMPTY_DRAFT', '확인할 초안이 없습니다.', 409)
        if session['state'] == 'SUBMITTED':
            require(data['intent'] == 'search', 'INVALID_STATE', '이미 반영된 초안입니다.', 409)
        else:
            common = dict(actor_id=actor, expected_version=session['version'])
            voice_input.confirm(ctx.conn, ctx.now, session['id'], dict(common, confirmed=True, client_request_id=str(uuid4())))
            if data['intent'] == 'search':
                voice_input.submit(ctx.conn, ctx.now, session['id'], dict(common, start_inquiry=False, client_request_id=str(uuid4())))
        reply = ('확인한 환자 정보를 반영했습니다.' if data['intent'] == 'search'
                 else '현재 버전의 환자 정보를 확정했습니다.')
        return {'intent': data['intent'], 'system_items': [record_reply(ctx, pid, reply)]}
    return write(ctx, pid, 'confirm', apply)


def accept_suggestion(ctx, pid):
    """A person taps 확정 on one suggested value. Only a value the server currently suggests is accepted."""
    data = ctx.body
    require(isinstance(data.get('suggestion_id'), str), 'INVALID', '확정할 제안값을 선택하세요.')

    def apply(actor):
        scope(ctx, pid, editable=True)
        session = latest(ctx, pid)
        require(session is not None, 'VOICE_EMPTY_DRAFT', '확인할 초안이 없습니다.', 409)
        check_version(data, session)
        chosen = next((s for s in view(ctx, pid, session)['suggestions'] if s['id'] == data['suggestion_id']), None)
        require(chosen is not None, 'INTAKE_SUGGESTION_GONE', '제안값이 바뀌었습니다. 확인창을 다시 확인하세요.', 409)
        field, value = chosen['field'], chosen['value']
        change = list(dict.fromkeys(session['fields'].get(field, []) + [value])) if field in LIST_FIELDS else value
        evidence = {field: ['제안값 확정' + (f" ({chosen['note']})" if chosen.get('note') else '')]}
        with voice_terms.use_dictionary(voice_lexicon.snapshot(ctx.conn)):
            row = voice_input._row(ctx.conn, session['id'], actor, ctx.now)
            voice_input._mutable(row, session['version'])
            # Same steps as a manual correction (voice_input._edit), plus the peer reset a follow-up answer
            # applies, so a confirmed symptom also settles the derived chief complaint it was disputed with.
            fields, meta = voice_input._merge(row, voice_terms.normalize_fields({field: change}), evidence,
                                              ctx.now, 'manual', 'correction', replace=True)
            if PEER_QUESTION.get(field):
                voice_input._recognition_peer_reset(fields, meta, PEER_QUESTION[field], ctx.now)
            voice_input._advance(ctx.conn, ctx.now, row, fields, meta, json.loads(row['warnings']), 'correction')
        current = latest(ctx, pid)
        return {'accepted': {key: chosen[key] for key in ('id', 'field', 'value')},
                'revision': [current['id'], current['version']]}
    return write(ctx, pid, 'suggestion', apply)


def install_routes():
    app.route('GET', PATH)(read)
    app.route('POST', PATH)(input_report)
    app.route('POST', PATH + '/confirm')(confirm)
    app.route('POST', PATH + '/suggestion')(accept_suggestion)
