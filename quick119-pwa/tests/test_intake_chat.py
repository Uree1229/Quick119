"""Persisted crew reports and confirmed-hospital access; synthetic HTTP only."""
from copy import deepcopy
import importlib
import sys
import time
import unittest
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

import intake_chat
import test_team_chat as team_harness


class IntakePresentation(unittest.TestCase):
    def test_receipt_is_stable_server_text_and_does_not_change_saved_message(self):
        message = {'id': 'message:7', 'kind': 'message', 'message_id': 7,
                   'conversation_id': 2, 'hospital_session_id': None,
                   'sender_role': 'patient', 'body': '<script>untrusted report</script>',
                   'at': 123.5, 'order_key': [4, 1, 7]}
        original = deepcopy(message)
        receipt = intake_chat.report_receipt(message)
        self.assertEqual(message, original)
        self.assertEqual(receipt['id'], 'system-report:7')
        self.assertEqual(receipt['order_key'], [4, 1, 7, 1])
        self.assertNotIn(message['body'], receipt['text'])
        importlib.reload(intake_chat)
        self.assertEqual(intake_chat.report_receipt(message), receipt)
        self.assertIsNone(intake_chat.report_receipt(dict(message, hospital_session_id=3)))
        self.assertIsNone(intake_chat.report_receipt(dict(message, sender_role='hospital')))

    def test_handoff_between_reads_keeps_original_authorized_scope(self):
        hospital = {'active_hospital_session': {'id': 1, 'hospital_id': 'H01'},
                    'items': [], 'cursors': {'event_id': 3, 'message_id': 2}}
        newer = {'active_hospital_session': {'id': 2, 'hospital_id': 'H02'},
                 'items': [{'id': 'private-never-expose'}]}
        self.assertIs(intake_chat.confirmed_hospital_conversation(hospital, newer, 'H01'), hospital)

    def test_event_written_between_reads_is_not_skipped_or_over_shared(self):
        previous = {'id': 1, 'hospital_id': 'H01', 'joined_event_id': 5, 'left_event_id': 7}
        active = {'id': 2, 'hospital_id': 'H01', 'hospital_name': '가상 병원 1',
                  'joined_event_id': 10, 'left_event_id': None}
        authorized = {'active_hospital_session': active, 'hospital_sessions': [previous, active],
                      'items': [], 'cursors': {'event_id': 10, 'message_id': 0}}
        def event(eid, hid):
            return {'id': f'event:{eid}', 'kind': 'system', 'type': 'patient_updated',
                    'event_id': eid, 'hospital_id': hid, 'order_key': [eid, 0, eid]}
        # Event 11 was written after the authorized GET, so its ID is absent
        # there. It must still be returned before advancing the cursor past it.
        # Session gaps and the other hospital's new event remain inaccessible.
        newer = {'active_hospital_session': active,
                 'items': [event(6, 'H01'), event(8, 'H01'), event(11, 'H01'),
                           event(12, 'H02'), event(13, None)],
                 'cursors': {'event_id': 13, 'message_id': 0}, 'has_more': False,
                 'more': {'events': False, 'messages': False}}
        view = intake_chat.confirmed_hospital_conversation(authorized, newer, 'H01')
        self.assertEqual([item['id'] for item in view['items']], ['event:6', 'event:11', 'event:13'])
        self.assertEqual(view['cursors'], newer['cursors'])


class IntakeChatIntegration(unittest.TestCase):
    # Reuse only fixture lifecycle/helpers, without inheriting browser tests or
    # rediscovering the original test methods in this module.
    setUpClass = classmethod(team_harness.LiveTeamChat.setUpClass.__func__)
    cleanup = classmethod(team_harness.LiveTeamChat.cleanup.__func__)
    spawn = classmethod(team_harness.LiveTeamChat.spawn.__func__)
    call = team_harness.LiveTeamChat.call
    create_case = team_harness.LiveTeamChat.create_case

    def send_private(self, pid, text='가상 현장 보고'):
        payload = {'body': text, 'sender_name': '가상 구급대',
                   'client_message_id': uuid4().hex, 'hospital_session_id': None}
        return payload, self.call('POST', f'/api/patients/{pid}/messages', payload)

    def accept(self, pid, hospital='H01'):
        inquiry = self.call('POST', f'/api/patients/{pid}/inquiries', {'hospital_ids': [hospital]})
        iid = inquiry['inquiries'][0]['id']
        self.call('POST', f'/api/inquiries/{iid}/respond', {'action': 'accept', 'handled_by': '가상 의료진'})
        return iid

    def test_reports_before_inquiry_and_acceptance_join_same_conversation_after_confirm(self):
        pid = self.create_case()
        path = f'/api/patients/{pid}'
        first = self.call('GET', path + '/conversation')
        self.assertTrue(first['can_send'])
        self.assertEqual([p['role'] for p in first['participants']], ['system', 'crew'])
        self.assertEqual(sum(i.get('text') == intake_chat.WELCOME for i in first['items']), 1)
        payload, sent = self.send_private(pid, '가상 보고 · 병원 문의 전')
        self.assertEqual(len(sent['system_items']), 1)
        staff = f'/api/hospitals/H01/patients/{pid}'
        self.call('GET', staff + '/conversation', status=403)
        self.call('POST', staff + '/messages', dict(payload, client_message_id=uuid4().hex), status=403)
        iid = self.accept(pid)
        self.call('GET', staff + '/conversation', status=403)
        self.assertEqual([p['role'] for p in self.call('GET', path + '/conversation')['participants']], ['system', 'crew'])
        _, accepted_report = self.send_private(pid, '가상 보고 · 수용 응답 후 확정 전')
        self.call('POST', path + '/confirm', {'inquiry_id': iid})
        joined = self.call('GET', path + '/conversation')
        self.assertEqual(joined['conversation']['id'], first['conversation']['id'])
        self.assertEqual([p['role'] for p in joined['participants']], ['system', 'crew', 'hospital'])
        self.assertEqual(joined['participants'][-1]['hospital_id'], 'H01')
        staff_view = self.call('GET', staff + '/conversation')
        self.assertEqual(staff_view['conversation']['id'], first['conversation']['id'])
        for receipt in (sent, accepted_report):
            self.assertIn(receipt['message'], staff_view['items'])
            self.assertIn(receipt['system_items'][0], staff_view['items'])
        self.call('GET', f'/api/hospitals/H02/patients/{pid}/conversation', status=403)
        sid = joined['active_hospital_session']['id']
        shared = self.call('POST', path + '/messages', dict(payload, client_message_id=uuid4().hex,
                                                         hospital_session_id=sid, body='확정 병원과 공유하는 메시지'))
        self.assertEqual(shared['system_items'], [])
        # Retrying an already persisted private report after the join changes
        # neither its session nor the deterministic system receipt.
        retried = self.call('POST', path + '/messages', payload)
        self.assertTrue(retried['duplicate'])
        self.assertEqual(retried['message'], sent['message'])
        self.assertEqual(retried['system_items'], sent['system_items'])

    def test_hospital_pagination_shares_earlier_reports_without_skips_or_other_case_leaks(self):
        pid = self.create_case()
        other = self.create_case()
        _, other_receipt = self.send_private(other, '다른 이송 기록 · 노출 금지')
        receipts = [self.send_private(pid, f'가상 현장 보고 {n}')[1] for n in range(5)]
        iid = self.accept(pid)
        self.call('POST', f'/api/patients/{pid}/confirm', {'inquiry_id': iid})
        path = f'/api/hospitals/H01/patients/{pid}/conversation'
        event_cursor = message_cursor = 0
        items = []
        for _ in range(30):
            query = f'?event_cursor={event_cursor}&message_cursor={message_cursor}&limit=1'
            page = self.call('GET', path + query)
            repeat = self.call('GET', path + query)
            self.assertEqual(page['items'], repeat['items'])
            self.assertEqual(page['cursors'], repeat['cursors'])
            raw = self.call('GET', f'/api/patients/{pid}/conversation' + query, direct=True)
            self.assertEqual(page['cursors'], raw['cursors'])
            self.assertEqual(page['has_more'], raw['has_more'])
            items.extend(page['items'])
            event_cursor, message_cursor = page['cursors']['event_id'], page['cursors']['message_id']
            if not page['has_more']:
                break
        else:
            self.fail('Pagination failed to terminate')
        ids = [i['id'] for i in items]
        self.assertEqual(len(ids), len(set(ids)))
        for receipt in receipts:
            self.assertIn(receipt['message'], items)
            self.assertIn(receipt['system_items'][0], items)
        self.assertNotIn(other_receipt['message']['id'], ids)
        self.assertNotIn(other_receipt['system_items'][0]['id'], ids)
        final_page = self.call('GET', path + f'?event_cursor={event_cursor}&message_cursor={message_cursor}&limit=1')
        self.assertEqual(final_page['items'], [])
        self.assertFalse(final_page['has_more'])

    def test_current_hospital_filter_never_includes_another_hospital_session(self):
        # Direct adapter test with persisted-style records isolates the disclosure
        # boundary without mutating the pinned runtime or forcing a clinical handoff.
        active = {'id': 22, 'hospital_id': 'H02', 'hospital_name': '가상 병원 2', 'joined_event_id': 9}
        authorized = {'active_hospital_session': active, 'hospital_sessions': [active],
                      'items': [{'id': 'event:9', 'kind': 'system'}]}
        def message(mid, sid, watermark, role='patient'):
            return dict(id=f'message:{mid}', kind='message', message_id=mid, conversation_id=1,
                        hospital_session_id=sid, sender_role=role, at=mid, order_key=[watermark, 1, mid])
        private = message(1, None, 1)
        previous_hospital = message(2, 11, 5, 'hospital')
        shared_with_previous = message(3, 11, 6)
        current = message(4, 22, 9, 'hospital')
        later_private = message(5, None, 10)
        crew = {'active_hospital_session': active,
                'items': [private, previous_hospital, shared_with_previous, current, later_private],
                'cursors': {'event_id': 10, 'message_id': 5}, 'has_more': False,
                'more': {'events': False, 'messages': False}}
        view = intake_chat.confirmed_hospital_conversation(authorized, crew, 'H02')
        self.assertEqual([i['id'] for i in view['items']], ['message:1', 'system-report:1', 'message:4'])
        self.assertEqual(view['cursors'], crew['cursors'])

    def test_retry_and_receipts_survive_gateway_restart(self):
        pid = self.create_case()
        payload, saved = self.send_private(pid, '재시작 후에도 남는 가상 보고')
        self.processes[1].terminate()
        self.processes[1].wait(timeout=10)
        env = team_harness.clean_env()
        env.update(QUICK119_ENV='test', QUICK119_DB_PATH=str(self.local_db), QUICK119_LOAD_ENV='0',
                   QUICK119_EXTERNAL_API='0', QUICK119_TEAM_CHAT_ENABLED='1', QUICK119_TEAM_CHAT_URL=self.team_url)
        port = urlsplit(self.api_url).port
        self.spawn([sys.executable, '-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', str(port),
                    '--no-proxy-headers', '--no-access-log'], env, 'restarted-gateway')
        deadline = time.monotonic() + 15
        while True:
            try:
                if self.http.get(self.api_url + '/api/team-runtime').status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                self.fail('Gateway restart failed')
            time.sleep(.05)
        retry = self.call('POST', f'/api/patients/{pid}/messages', payload)
        self.assertTrue(retry['duplicate'])
        self.assertEqual(retry['message'], saved['message'])
        self.assertEqual(retry['system_items'], saved['system_items'])
        view = self.call('GET', f'/api/patients/{pid}/conversation')
        self.assertEqual(sum(i['id'] == saved['system_items'][0]['id'] for i in view['items']), 1)
        self.assertEqual(sum(i['id'] == saved['message']['id'] for i in view['items']), 1)
        direct = self.call('GET', f'/api/patients/{pid}/conversation', direct=True)
        self.assertEqual(view['cursors'], direct['cursors'])
        self.assertEqual(view['conversation'], direct['conversation'])
        self.assertFalse(any(i['id'].startswith('system-report:') for i in direct['items']))


if __name__ == '__main__':
    unittest.main(verbosity=2)
