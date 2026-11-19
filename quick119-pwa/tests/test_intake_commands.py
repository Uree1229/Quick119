import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP.parent))
sys.path.insert(0, str(APP))

from server import db, service
import intake_commands as intake


class CommandChat(unittest.TestCase):
    def setUp(self):
        self.ctx = SimpleNamespace(conn=db.connect(':memory:'), now=2_000_000_000, body={})
        self.addCleanup(self.ctx.conn.close)
        db.init(self.ctx.conn)
        self.pid = 'Q119-TEST-command'
        service.create_patient(self.ctx.conn, self.ctx.now, self.pid,
            dict(reporter_role='transporter', consent=True, lat=35, lon=127, extra={'synthetic_fixture': True}))
        self.rev = None

    def send(self, text, **extra):
        self.ctx.body = dict(request_id=str(uuid4()), revision=self.rev, text=text, source=extra.pop('source', 'text'), **extra)
        r = intake.input_report(self.ctx, self.pid)
        self.rev = r['revision']
        return r

    def tap(self, intent):
        self.ctx.body = dict(request_id=str(uuid4()), revision=self.rev, intent=intent)
        return intake.confirm(self.ctx, self.pid)

    def test_full_report_confirmation_and_retry(self):
        self.send('20세 남성, 흉통 있음, 의식 명료, 호흡 정상')
        self.send('혈압 120에 80, 맥박 92회, 산소포화도 97퍼센트, 체온 37.2도')
        draft = self.send('호흡수 분당 20회, 혈당 105 밀리그램 퍼 데시리터, 20분 전 발생')['draft']
        self.assertEqual(draft['followup']['items'], [])
        self.assertEqual(draft['fields']['glucose'], 105)
        self.assertEqual(self.send('나이 21세로 수정해 줘')['draft']['fields']['age'], 21)
        r = self.send('방금 입력 취소')
        self.assertEqual(r['draft']['fields']['age'], 20)
        self.assertTrue(intake.input_report(self.ctx, self.pid)['duplicate'])
        self.assertEqual(self.send('확정')['effect'], 'confirmation_requires_tap')
        self.assertTrue(self.tap('confirm')['draft']['confirmed'])
        self.assertEqual(self.send('전송해 줘')['effect'], 'explicit_submit_api_required')
        self.assertIsNone(service._patient(self.ctx.conn, self.pid)['age'])
        self.assertEqual(self.tap('search')['draft']['state'], 'SUBMITTED')
        self.assertTrue(intake.confirm(self.ctx, self.pid)['duplicate'])
        patient = service._patient(self.ctx.conn, self.pid)
        self.assertEqual(patient['age'], 20)
        self.assertEqual(patient['extra']['vitals']['rr'], 20)
        self.assertEqual(patient['extra']['vitals']['glucose'], 105)
        self.assertEqual(self.ctx.conn.execute('select count(*) from inquiries').fetchone()[0], 0)

    def test_catalog_actions_and_separate_patient(self):
        self.assertFalse(self.send('가상 환자 상태 보고')['draft']['fields'])
        self.assertEqual(self.send('맥박 82회 기록해 주세요')['action'], 'add')
        self.assertEqual(self.send('맥박이 95회로 변했어')['action'], 'observe')
        for text, action in [('도움말','help'), ('빠진 항목 알려 줘','missing'), ('다시 질문해 줘','repeat_question'),
                             ('맥박 읽어 줘','review'), ('변경 이력','history'), ('맥박 기록 삭제','remove')]:
            with self.subTest(text=text):
                self.assertEqual(self.send(text)['action'], action)
        self.assertNotIn('pulse', intake.read(self.ctx, self.pid)['draft']['fields'])
        self.ctx.body = dict(request_id=str(uuid4()), revision=None, text='맥박 99회', source='text')
        with self.assertRaises(service.ServiceError):
            intake.input_report(self.ctx, self.pid)

    def test_recognition_context_and_asr_advice(self):
        r = self.send('산소포화도 90이%')
        self.assertTrue(r['hints'])
        self.ctx.body = dict(request_id=str(uuid4()), revision=self.rev, text='산소포화도 90이%', source='speech')
        self.assertTrue(intake.input_report(self.ctx, self.pid)['hints'])
        self.rev = [intake.read(self.ctx,self.pid)['draft'][k] for k in ('id','version')]
        r = self.send('전송해줘', alternatives=['전송하지 마'])
        self.assertEqual(r['effect'], 'voice_command_needs_clarification')


    def test_spoken_corrections_preserve_source_and_undo(self):
        self.send('20세 남성, 맥박 98회')
        for phrase, field, expected in [
            ('남자 아니라 여자야', 'sex', 'female'), ('여성이 아니고 남성입니다', 'sex', 'male'),
            ('남성 말고 여성으로 정정', 'sex', 'female'), ('나이 20살 말고 21살이야', 'age', 21),
            ('맥박 98 말고 88이야', 'pulse', 88), ('혈압 120에 80이 아니라 110에 70이야', 'diastolic_bp', 70),
        ]:
            with self.subTest(phrase=phrase):
                result = self.send(phrase)
                self.assertEqual(result['action'], 'edit')
                draft = result['draft']
                self.assertEqual(draft['fields'][field], expected)
                self.assertEqual(draft['field_metadata'][field]['evidence'], [phrase])
                self.assertEqual(result['message']['body'], phrase)
                self.assertTrue(intake.input_report(self.ctx, self.pid)['duplicate'])
        self.assertNotIn('systolic_bp', self.send('방금 거 원복해줘')['draft']['fields'])

    def test_ambiguous_corrections_do_not_change_patient(self):
        original = self.send('20세 남성')['draft']['fields']
        for phrase, alternatives in [('남자 아니라 여자일 수도 있어', []), ('보호자가 남자 아니라 여자야', []),
                                    ('남자가 아니라 여자야?', []), ('남자 말고 여자야', ['남자 말고 남자야']),
                                    ('20세', ['20세가 아니라 21세야'])]:
            with self.subTest(phrase=phrase):
                result = self.send(phrase, source='speech', alternatives=alternatives)
                self.assertEqual(result['effect'], 'voice_command_needs_clarification')
                self.assertEqual(result['draft']['fields'], original)

    def test_child_demographics_and_ambiguous_reports(self):
        for source in ('text', 'speech'):
            stated = self.send('10살 남자아이, 보호자 동행', source=source)
            fields = stated['draft']['fields']
            self.assertEqual((fields['age'], fields['sex']), (10, 'male'))
        for text, alternatives in [
            ('보호자는 32세 여성', []), ('여자아이가 아닙니다', []),
            ('5살이 아닙니다', []), ('열 살 여자아이인지는 모르겠습니다', []),
            ('8살 여자아이', ['보호자가 8살 여자아이']),
        ]:
            with self.subTest(text=text):
                result = self.send(text, source='speech', alternatives=alternatives)
                self.assertEqual(result['effect'], 'voice_command_needs_clarification')
                self.assertEqual(result['message']['body'], text)
                self.assertEqual(result['draft']['fields'], fields)
        different = self.send('성별 남자 아니라 여자야')
        self.assertEqual(different['draft']['fields']['sex'], 'female')

    def test_unavailable_observations_never_echo_a_prior_value_as_current(self):
        self.send('10살 남자아이, 맥박 88회')
        for label in ('나이', '성별', '증상', '의식', '호흡', '혈압', '맥박', '산소포화도', '체온', '호흡수', '혈당', '발생 시각'):
            for status in ('미확인', '미측정', '확인 불가'):
                with self.subTest(label=label, status=status):
                    text = f'{label} {status}'
                    result = self.send(text)
                    self.assertIsNone(result['action'])
                    reply = result['system_items'][0]['data']['reply']
                    self.assertIn(f'{label}: {status}', reply)
                    self.assertNotIn('88', reply)
                    self.assertNotIn('남성', reply)
                    self.assertEqual(result['message']['body'], text)
                    if label in ('나이', '성별', '맥박'):
                        self.assertIn('이전 기록 유지', reply)
        for text, value in [('성별 읽어 줘', '남성'), ('맥박 읽어 줘', '88')]:
            result = self.send(text)
            self.assertEqual(result['action'], 'review')
            self.assertIn(value, result['system_items'][0]['data']['reply'])
        result = self.send('맥박 95회', source='speech', alternatives=['맥박 미측정'])
        self.assertEqual(result['draft']['fields']['pulse'], 88)
        self.assertIn('다시 알려', result['system_items'][0]['data']['reply'])

    def test_incomplete_commands_record_once_and_preserve_state(self):
        for phrase, action in [('취소', 'undo'), ('수정', 'edit'), ('나이 수정', 'edit')]:
            with self.subTest(phrase=phrase):
                result = self.send(phrase)
                self.assertEqual((result['action'], result['effect']), (action, 'voice_command_needs_clarification'))
                self.assertFalse(result['draft']['fields'])
                self.assertTrue(intake.input_report(self.ctx, self.pid)['duplicate'])
                count = self.ctx.conn.execute('SELECT count(*) FROM voice_utterances WHERE utterance_id=?',
                                              (result['input_id'],)).fetchone()[0]
                self.assertEqual(count, 1)
        self.assertEqual(self.send('전송')['effect'], 'explicit_submit_api_required')
        self.assertEqual(self.ctx.conn.execute('SELECT count(*) FROM inquiries').fetchone()[0], 0)

    def test_search_and_lookup_variants(self):
        self.send('20세 남성')
        for phrase in ('탐색시작해줘', '근처 응급실 찾아봐 주세요', '병원 추천해 주세요', '수용 가능한 병원을 알아봐줄래?',
                       '병원 후보 검색해 줘', '병원 검색을 시작해 주시겠어요?'):
            with self.subTest(phrase=phrase):
                self.assertEqual(self.send(phrase)['effect'], 'explicit_submit_api_required')
        self.assertEqual(self.send('탐색 시작해 줘', source='speech', alternatives=['병원 검색 시작'])['effect'], 'explicit_submit_api_required')
        self.assertEqual(self.send('남자 아니고 여자야', source='speech', alternatives=['남성이 아니라 여성입니다'])['draft']['fields']['sex'], 'female')
        for phrase, action in [('병원 인계용 요약 보여 줘', 'review'), ('남은 항목이 뭐야', 'missing'),
                               ('이 내용으로 확정해줘', 'confirm')]:
            with self.subTest(phrase=phrase):
                self.assertEqual(self.send(phrase)['action'], action)
        for phrase in ('탐색 시작하지 마', '병원 추천해 주면 좋을까?', '보호자가 병원을 찾아봐 달라고 했어'):
            self.assertNotEqual(self.send(phrase)['effect'], 'explicit_submit_api_required')
        self.assertEqual(self.ctx.conn.execute('SELECT count(*) FROM inquiries').fetchone()[0], 0)
