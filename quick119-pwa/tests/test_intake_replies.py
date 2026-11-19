import sys
import unittest
from pathlib import Path
from uuid import uuid4

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP.parent))
sys.path.insert(0, str(APP))

from server import db, service, voice_input
from intake_replies import reply_text


class ReplyTest(unittest.TestCase):
    def setUp(self):
        self.now = 1_800_000_000
        self.conn = db.connect(':memory:')
        self.addCleanup(self.conn.close)
        db.init(self.conn)
        pid = 'Q119-TEST-replies'
        service.create_patient(self.conn, self.now, pid, dict(reporter_role='transporter', consent=True))
        self.session = voice_input.create(self.conn, self.now, dict(actor_id='crew', patient_id=pid,
                                          client_request_id=str(uuid4())))['session']

    def send(self, text, *, question=None, **extra):
        before = self.session['fields']
        body = dict(actor_id='crew', client_request_id=str(uuid4()), expected_version=self.session['version'],
                    text=text, utterance_id=str(uuid4()), **extra)
        if question:
            result = voice_input.answer_followup(self.conn, self.now, self.session['id'],
                                                dict(body, question_id=question))
        else:
            result = voice_input.transcript(self.conn, self.now, self.session['id'],
                                            dict(body, mode='auto', is_final=True))
        self.session = result['session']
        return reply_text(result, before, question_id=question)

    def test_report_contains_only_this_input_and_repeats_only_mentioned_value(self):
        self.assertEqual(self.send('10세 남성'), '나이: 10세\n성별: 남성')
        self.assertEqual(self.send('맥박 88회'), '맥박: 88회/분')
        self.assertEqual(self.send('맥박 88회'), '맥박: 88회/분')
        self.assertEqual(self.send('흉통 있음'), '증상: 흉통')
        self.assertEqual(self.send('구토 있음'), '증상: 구토')
        self.assertEqual(self.send('흉통 없음'), '없는 증상: 흉통')
        self.assertNotIn('남성', self.send('증상이 무엇인지 아직 모르겠습니다'))

    def test_units_partial_bp_and_kst(self):
        self.assertEqual(self.send('수축기 혈압 120'), '수축기 혈압: 120 mmHg')
        self.assertEqual(self.send('이완기 혈압 80'), '이완기 혈압: 80 mmHg')
        self.assertEqual(self.send('혈압 120에 80, 산소포화도 97퍼센트, 체온 37.2도'),
                         '혈압: 120/80 mmHg\n산소포화도: 97%\n체온: 37.2°C')
        self.assertEqual(self.send('호흡수 분당 20회, 혈당 105 밀리그램 퍼 데시리터, 20분 전 발생'),
                         '호흡수: 20회/분\n혈당: 105 mg/dL\n발생 시각: 2027-01-15 16:40 (한국시간)')

    def test_corrections_and_command_feedback(self):
        self.assertEqual(self.send('맥박 82회 기록해 주세요'), '맥박: 82회/분')
        self.assertEqual(self.send('맥박 82회 기록해 주세요'), '맥박: 82회/분')
        self.assertEqual(self.send('맥박 88회로 수정해 줘'), '맥박: 88회/분')
        self.assertEqual(self.send('맥박 읽어 줘'), '맥박: 88회/분')
        self.assertEqual(self.send('변경 이력'), '최근 변경\n맥박: 82회/분 → 88회/분')
        self.assertEqual(self.send('방금 입력 취소'), '직전 변경을 되돌렸습니다.\n맥박: 82회/분')
        self.assertEqual(self.send('맥박 기록 삭제'), '맥박: 기록 없음')
        self.assertIn('수정', self.send('도움말'))
        self.assertIn('환자정보 확정', self.send('확정'))
        self.assertIn('확인하고 병원 탐색', self.send('전송해 줘'))

    def test_guided_unavailable_and_disputed_observation(self):
        question = self.send('빠진 항목 알려 줘')
        self.assertIn('12개', question)
        self.assertIn(self.send('다시 질문해 줘'), question)
        self.assertEqual(self.send('미확인', question='age'), '나이: 미확인')
        self.assertEqual(self.send('여성', question='sex'), '성별: 여성')
        self.assertEqual(self.send('정상', question='breathing'), '호흡: 정상')
        self.assertIn('재확인', self.send('맥박 88회', recognition_alternatives=['맥박 98회']))
        self.assertNotIn('88', self.send('맥박이 몇인지 모르겠어요'))

    def test_unavailable_and_r1_fields_never_claim_a_measurement(self):
        self.assertEqual(self.send('미확인', question='sex'), '성별: 미확인')
        self.assertEqual(self.send('미측정', question='blood_pressure'), '혈압: 미확인')
        self.assertEqual(self.send('남은 항목 모두 미확인', question='age'), '남은 항목은 미확인으로 남겼습니다.')
        self.assertIn('미확인', self.send('빠진 항목 알려 줘'))
        # A saved observation pending recheck must not become a definite date or pressure reading.
        result = {'session': {'fields': {'onset_at': self.now, 'systolic_bp': 121, 'diastolic_bp': 78,
                                          'age_category': 'adult'},
                              'field_metadata': {'onset_at': {'needs_clarification': True},
                                                 'systolic_bp': {'needs_clarification': True}}},
                  'command': {'action': 'review'}}
        self.assertEqual(reply_text(result, {}), '수축기 혈압: 재확인 필요\n이완기 혈압: 78 mmHg\n발생 시각: 재확인 필요')

    def test_contextual_clarifications_retain_the_servers_prompt(self):
        for prompt in ['수정할 항목과 새 값을 알려 주세요.', '취소할 입력이 없습니다.']:
            result = {'session': {'fields': {'age': 10}}, 'command': None,
                      'operational_effect': 'voice_command_needs_clarification', 'speak_text': prompt}
            self.assertEqual(reply_text(result, {'age': 10}), prompt)
