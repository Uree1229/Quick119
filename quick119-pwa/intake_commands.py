"""Connect the pinned voice parser to each synthetic crew chat in one transaction."""
import json
import re
from functools import lru_cache
from uuid import uuid4

import intake_phrases

from server import app, conversations, db, service, voice_followup, voice_input, voice_lexicon, voice_recognition_context, voice_terms
from speech_eval.asr_correction import Corrector, numeric_flags

PATH = r'/api/patients/(?P<pid>Q119-TEST-[A-Za-z0-9-]{1,80})/intake-draft'
ACTOR = 'quick119-intake'
require = service._require


@lru_cache(maxsize=1)
def corrector():
    return Corrector()


def latest(ctx, pid):
    row = ctx.conn.execute(
        'SELECT id FROM voice_sessions WHERE patient_id=? AND actor_id=? ORDER BY rowid DESC LIMIT 1',
        (pid, ACTOR)).fetchone()
    return voice_input.get(ctx.conn, ctx.now, row['id'], ACTOR)['session'] if row else None


def view(ctx, pid, session):
    if session:
        # Advice is tied to the original utterance and survives reload and a later submit intent.
        rows = ctx.conn.execute("SELECT response FROM voice_requests WHERE actor_id=? AND operation=? "
                                "AND recorded_at>=? ORDER BY rowid DESC LIMIT 100",
                                (ACTOR, 'input:' + pid, session['created_at']))
        hints = []
        for row in rows:
            receipt = json.loads(row['response'])
            if receipt.get('revision', [None])[0] == session['id']:
                hints.extend(receipt.get('hints', []))
        return {'asr_hints': list(dict.fromkeys(hints)), **{key: session[key] for key in ('id', 'state', 'version', 'confirmed', 'fields', 'field_metadata',
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
                if correction is None and any(intake_phrases.correction(alt) for alt in common.get('recognition_alternatives', [])):
                    correction = '', set()
                if correction is None:
                    # Equivalent search/confirm variants still lead to an explicit screen tap.
                    alternatives = transcript.get('recognition_alternatives', [])
                    if alternatives:
                        action = voice_terms.parse_command(text)['action']
                        if action in ('submit', 'confirm') and all(voice_terms.parse_command(alt)['action'] == action for alt in alternatives):
                            del transcript['recognition_alternatives']
                    result = voice_input._utterance(ctx.conn, ctx.now, row, transcript, text, 'app_transcript')
                else:
                    result = intake_phrases.apply_correction(ctx, row, transcript, text, correction)
        draft = result['session']
        command = (result.get('command') or {}).get('action')
        message = None
        if result.get('effective_mode') == 'dictation' or command in {'observe', 'edit', 'add', 'remove', 'undo'} or data.get('question_id'):
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
        return dict(message=message, action=command, effect=result.get('operational_effect'), feedback=feedback,
                    hints=hints, input_id=key, revision=[draft['id'], draft['version']])
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
        return {'intent': data['intent']}
    return write(ctx, pid, 'confirm', apply)


def install_routes():
    app.route('GET', PATH)(read)
    app.route('POST', PATH)(input_report)
    app.route('POST', PATH + '/confirm')(confirm)
