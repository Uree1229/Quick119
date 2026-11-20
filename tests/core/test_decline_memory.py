"""최근 수용 불가 사유를 다음 환자의 자동 문의에 반영한다.

실행: python -m unittest discover -s tests/core -p test_decline_memory.py
모든 환자·병원·시각은 임시 DB의 가상 값이다. 외부 API를 호출하지 않는다.
"""
from server import conversations, decline_memory, recommendation_view, service
from test_inquiry_workflow import WorkflowBase
from test_server import EST, T0
from test_transport_reassignment import AlternativeBase


class RecentDeclineSkipsAutomaticInquiry(WorkflowBase):
    def decline(self, pid, hid, reason, now=T0 + 1, **fields):
        self.patient(pid, now=now - 1, **fields)
        service.send_inquiries(self.conn, now - 1, pid, [hid])
        service.respond(self.conn, now, self.inquiry_id(pid, hid), 'decline', 'nurse', reason=reason)

    def contacted(self, pid):
        return [q['hospital_id'] for q in self.inquiries(pid)]

    def skips(self, pid):
        return [dict(r) for r in self.conn.execute(
            'SELECT hospital_id, data FROM events WHERE patient_id=? AND type=? ORDER BY id',
            (pid, decline_memory.SKIP_EVENT))]

    def test_bed_decline_skips_hospital_for_the_next_patient_and_is_noted_once(self):
        self.decline('X', 'H1', '병상')
        self.patient('A', now=T0 + 2, auto_inquiry=True)
        self.run_worker(T0 + 2)
        self.assertEqual(sorted(self.contacted('A')), ['H2', 'H3'])
        self.run_worker(T0 + 3)
        self.assertEqual([s['hospital_id'] for s in self.skips('A')], ['H1'])
        self.assertIn('병상', self.skips('A')[0]['data'])
        texts = [item['text'] for item in conversations.get_conversation(self.conn, T0 + 3, 'A')['items']]
        self.assertTrue(any('자동 문의를 보내지 않았습니다' in text for text in texts), texts)
        # 다른 환자의 거절은 그 병원이 아닌 이 환자의 기록에만 남는다.
        self.assertEqual(self.skips('X'), [])

    def test_first_search_from_the_chat_app_skips_the_recent_decline(self):
        # 구급대 앱의 '병원 탐색'은 워커가 아니라 search_and_inquire로 첫 문의를 보낸다.
        self.add_hospitals(2)
        self.decline('X', 'H1', '병상')
        self.patient('A', now=T0 + 2)
        result = recommendation_view.search_and_inquire(self.conn, T0 + 2, 'A', EST)
        asked = result['automatic_inquiry']['hospital_ids']
        self.assertEqual(len(asked), 3)
        self.assertNotIn('H1', asked)
        self.assertEqual(sorted(self.contacted('A')), sorted(asked))
        self.assertEqual(result['automatic_inquiry']['skipped_recent_declines'],
                         [dict(hospital_id='H1', reason='병상')])
        self.assertEqual([s['hospital_id'] for s in self.skips('A')], ['H1'])
        retry = recommendation_view.search_and_inquire(self.conn, T0 + 3, 'A', EST)
        self.assertTrue(retry['automatic_inquiry']['already_started'])
        self.assertEqual(len(self.skips('A')), 1)

    def test_memory_ends_after_ten_minutes(self):
        self.decline('X', 'H1', '병상')
        later = T0 + 1 + decline_memory.DECLINE_MEMORY_SECONDS
        self.patient('A', now=later, auto_inquiry=True)
        self.run_worker(later)
        self.assertIn('H1', self.contacted('A'))
        self.assertEqual(self.skips('A'), [])

    def test_later_acceptance_of_a_comparable_patient_clears_the_decline(self):
        self.decline('X', 'H1', '병상')
        self.patient('Y', now=T0 + 2)
        self.accepted('Y', 'H1', now=T0 + 2)
        self.patient('A', now=T0 + 10, auto_inquiry=True)
        self.run_worker(T0 + 10)
        self.assertIn('H1', self.contacted('A'))

    def test_bed_freed_after_the_decline_clears_it(self):
        self.decline('X', 'H1', '병상')
        service.set_resource(self.conn, T0 + 3, 'H1', 'ed_bed', 3, 0, source='hospital_input')
        self.patient('A', now=T0 + 4, auto_inquiry=True)
        self.run_worker(T0 + 4)
        self.assertIn('H1', self.contacted('A'))

    def test_freed_bed_must_still_fit_the_new_patient(self):
        self.decline('X', 'H1', '병상')
        service.set_resource(self.conn, T0 + 3, 'H1', 'ed_bed', 1, 1, source='hospital_input')
        self.patient('A', now=T0 + 4, auto_inquiry=True)
        self.run_worker(T0 + 4)
        self.assertNotIn('H1', self.contacted('A'))

    def service_reason_case(self, reason):
        self.decline('X', 'H1', reason, required_capabilities=['소아청소년과'])
        self.patient('A', now=T0 + 2, auto_inquiry=True)
        self.patient('B', now=T0 + 2, auto_inquiry=True, required_capabilities=['소아청소년과'])
        self.run_worker(T0 + 2)
        self.assertIn('H1', self.contacted('A'))
        self.assertNotIn('H1', self.contacted('B'))

    def test_no_care_decline_applies_only_to_patients_needing_the_same_care(self):
        self.service_reason_case('진료 불가')

    def test_staff_decline_applies_only_to_patients_needing_the_same_care(self):
        self.service_reason_case('인력')

    def test_equipment_decline_applies_only_to_patients_needing_the_same_care(self):
        self.service_reason_case('장비')

    def test_service_decline_without_named_care_is_not_generalized(self):
        self.decline('X', 'H1', '진료 불가')
        self.patient('A', now=T0 + 2, auto_inquiry=True, required_capabilities=['소아청소년과'])
        self.run_worker(T0 + 2)
        self.assertIn('H1', self.contacted('A'))

    def test_other_reason_is_not_generalized(self):
        self.decline('X', 'H1', '기타')
        self.patient('A', now=T0 + 2, auto_inquiry=True)
        self.run_worker(T0 + 2)
        self.assertIn('H1', self.contacted('A'))

    def test_manual_inquiry_is_still_allowed(self):
        self.decline('X', 'H1', '병상')
        self.patient('A', now=T0 + 2)
        service.send_inquiries(self.conn, T0 + 2, 'A', ['H1'])
        self.assertEqual(self.contacted('A'), ['H1'])

    def test_own_earlier_decline_is_remembered_without_a_separate_note(self):
        self.decline('X', 'H1', '병상')
        patient = service._patient(self.conn, 'X')
        decline = decline_memory.active_decline(self.conn, T0 + 2, patient, 'H1')
        self.assertEqual((decline['reason'], decline['same_patient']), ('병상', True))
        decline_memory.note_skip(self.conn, T0 + 2, patient, decline)
        self.assertEqual(self.skips('X'), [])


class RecentDeclineSkipsAlternativeInquiry(AlternativeBase):
    def test_alternative_search_skips_a_hospital_that_just_declined_a_comparable_patient(self):
        self.add_alternatives()
        self.setup_transport(required_capabilities=['소아청소년과'])
        expected = [c['hospital_id'] for c in service.candidates(self.conn, T0 + 25, 'A', EST)['candidates']
                    if c['hospital_id'] not in ('H1', 'H3')][0]
        self.patient('X', now=T0 + 24, required_capabilities=['소아청소년과'])
        service.send_inquiries(self.conn, T0 + 24, 'X', [expected])
        service.respond(self.conn, T0 + 25, self.inquiry_id('X', expected), 'decline', 'nurse', reason='병상')
        self.request_adjustment()
        self.worker()
        asked = [q['hospital_id'] for q in self.open_alternatives()]
        self.assertEqual(len(asked), 1)
        self.assertNotEqual(asked[0], expected)
        self.assertIn(decline_memory.SKIP_EVENT, self.event_types())
