"""실제 .env/DB/외부 API에 접근하지 않는 회귀 테스트. unittest로 실행."""
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import json
import re
import struct
import socket
import threading
import time
import asyncio
import io
from html.parser import HTMLParser
from html import unescape
from uuid import uuid4
from pathlib import Path
from unittest.mock import patch
from contextlib import closing


class Quick119Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="quick119-test-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.test_db = Path(cls.temp.name) / "patients.db"
        env = patch.dict(os.environ, {
            "QUICK119_ENV": "test", "QUICK119_DB_PATH": str(cls.test_db),
            "QUICK119_LOAD_ENV": "1", "QUICK119_EXTERNAL_API": "1",
        })
        env.start()
        cls.addClassCleanup(env.stop)
        cls.network = patch("urllib.request.urlopen", side_effect=AssertionError("External network forbidden"))
        cls.urlopen = cls.network.start()
        cls.addClassCleanup(cls.network.stop)
        read_text = Path.read_text

        def guarded_read(path, *args, **kwargs):
            if path.name == ".env":
                raise AssertionError("Actual .env must never be read")
            return read_text(path, *args, **kwargs)

        guard = patch.object(Path, "read_text", guarded_read)
        guard.start()
        cls.addClassCleanup(guard.stop)
        # 모듈 import 시 어떤 DB에도 연결하지 않는지 확인합니다.
        with patch("sqlite3.connect", side_effect=AssertionError("Import must not open any DB")):
            import main
        cls.main = main
        if cls.test_db.exists():
            raise AssertionError("Import created a database")
        original_connect = sqlite3.connect

        def isolated_connect(database, *args, **kwargs):
            if Path(database).resolve() != cls.test_db.resolve():
                raise AssertionError("Only the temporary test DB may be opened")
            return original_connect(database, *args, **kwargs)

        db_guard = patch("sqlite3.connect", isolated_connect)
        db_guard.start()
        cls.addClassCleanup(db_guard.stop)
        from fastapi.testclient import TestClient
        cls.client_context = TestClient(main.app)
        cls.client = cls.client_context.__enter__()
        cls.addClassCleanup(cls.client_context.__exit__, None, None, None)

    def setUp(self):
        with closing(sqlite3.connect(self.test_db)) as conn, conn:
            conn.execute("DELETE FROM emergency_intakes")
            conn.execute("DELETE FROM patients")

    def tearDown(self):
        self.urlopen.assert_not_called()

    def register(self, name="테스트 전용 환자"):
        response = self.client.post("/register", data={"name": name, "birth": "2000-01-01", "phone": "000-0000-0000"})
        self.assertEqual(response.status_code, 200)
        with closing(sqlite3.connect(self.test_db)) as conn, conn:
            return conn.execute("SELECT id FROM patients ORDER BY id DESC LIMIT 1").fetchone()[0]

    def test_import_and_schema_isolation(self):
        self.assertEqual(self.main.DB_PATH, self.test_db)
        with closing(sqlite3.connect(self.test_db)) as conn, conn:
            columns = [r[1] for r in conn.execute("PRAGMA table_info(patients)")]
        self.assertEqual(columns, ["id", "name", "birth", "phone", "address", "guardian", "guardian_phone"])

    def test_home_template_and_navigation(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("퀵119", response.text)
        self.assertIn("응급상황 접수 시작", response.text)
        for target in ["/transport/", "/patients", "/emergency/hospitals"]:
            self.assertIn(f'href="{target}"', response.text)
        self.assertIn('aria-current="page"', response.text)
        self.assertNotIn('class="med-header"', response.text)

    def test_standalone_voice_report_removed_from_home(self):
        response = self.client.get("/")
        self.assertNotIn('href="/voice-intake"', response.text)
        self.assertNotIn('음성 문진', response.text)
        self.assertIn('class="emergency-cta" href="/transport/"', response.text)

    def test_empty_patient_selection(self):
        response = self.client.get("/emergency?step=patient")
        self.assertEqual(response.status_code, 200)
        self.assertIn("아직 등록된 환자가 없습니다", response.text)
        self.assertIn('href="/register"', response.text)
        self.assertIn('href="/emergency/check?patient_id=0"', response.text)
        self.assertIn('href="/transport/"', response.text)

    def test_registered_selection_and_escaping(self):
        patient_id = self.register('<script>alert("synthetic")</script>')
        response = self.client.get("/emergency?step=patient")
        self.assertIn(f'<option value="{patient_id}">', response.text)
        self.assertIn("&lt;script&gt;", response.text)
        self.assertNotIn('<script>alert("synthetic")', response.text)
        self.assertIn('action="/emergency/check" method="get"', response.text)

    def test_emergency_and_old_voice_bookmark_enter_chat(self):
        response = self.client.get("/emergency", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/transport/")
        voice = self.client.get('/voice-intake', follow_redirects=False)
        self.assertEqual(voice.status_code, 303)
        self.assertEqual(voice.headers['location'], '/transport/')
        selection = self.client.get("/emergency?step=patient", follow_redirects=False)
        self.assertEqual(selection.status_code, 200)
        self.assertIn("등록 환자 접수", selection.text)
        self.assertIn("미등록 환자 접수", selection.text)

    def report_payload(self, **changes):
        payload = {"request_id": str(uuid4()), "patient_id": None,
                   "transcript": '60세 남성, 흉통 없음. 혈압 120에 80. <script>synthetic</script>',
                   "state": {"age": "60세", "sex": "남성", "symptoms": "흉통 없음",
                             "systolic": 120, "diastolic": 80, "pulse": 0}}
        payload.update(changes)
        return payload

    def test_field_report_save_unknowns_and_raw_transcript(self):
        patient_id = self.register()
        before = self.main.get_patient(patient_id)
        payload = self.report_payload(patient_id=patient_id)
        response = self.client.post("/api/emergency-intakes", json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["saved"])
        self.assertEqual(response.headers["cache-control"], "no-store")
        with closing(sqlite3.connect(self.test_db)) as conn:
            record = conn.execute("SELECT patient_id, transcript, state_json, created_at FROM emergency_intakes WHERE id = ?",
                                  (response.json()["id"],)).fetchone()
        self.assertEqual(record[0], patient_id)
        self.assertEqual(record[1], payload["transcript"])
        state = json.loads(record[2])
        self.assertEqual(state["symptoms"], "흉통 없음")
        self.assertEqual(state["pulse"], 0)
        self.assertIsNone(state["spo2"])
        self.assertIsNone(state["consciousness"])
        self.assertTrue(record[3].endswith("Z"))
        self.assertEqual(self.main.get_patient(patient_id), before)
        # 미등록 환자도 별도의 환자 등록 없이 저장합니다.
        response = self.client.post("/api/emergency-intakes", json=self.report_payload(state={}))
        self.assertEqual(response.status_code, 200)

    def test_field_report_retry_is_idempotent(self):
        payload = self.report_payload()
        first = self.client.post("/api/emergency-intakes", json=payload)
        retry = self.client.post("/api/emergency-intakes", json=payload)
        self.assertEqual(first.json(), retry.json())
        changed = self.client.post("/api/emergency-intakes", json={**payload, "state": {"pulse": 91}})
        self.assertEqual(changed.status_code, 409)
        with closing(sqlite3.connect(self.test_db)) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM emergency_intakes").fetchone()[0], 1)

    def test_field_report_validation_and_storage_failure(self):
        for changes in [{"transcript": " "}, {"transcript": "x" * 20001}, {"request_id": "bad"},
                        {"state": {"spo2": 101}}, {"state": {"pulse": -1}},
                        {"state": {"pulse": 90.5}}, {"state": {"temperature": 70}},
                        {"state": {"age": "x" * 41}}, {"state": {"unrecognized": "value"}}]:
            with self.subTest(changes=changes):
                self.assertEqual(self.client.post("/api/emergency-intakes", json=self.report_payload(**changes)).status_code, 422)
        self.assertEqual(self.client.post("/api/emergency-intakes", json=self.report_payload(patient_id=999999)).status_code, 404)
        with patch.object(self.main, "save_emergency_intake", side_effect=sqlite3.OperationalError("synthetic secret path")):
            response = self.client.post("/api/emergency-intakes", json=self.report_payload())
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("synthetic secret path", response.text)
        with closing(sqlite3.connect(self.test_db)) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM emergency_intakes").fetchone()[0], 0)

    def test_chat_voice_and_participant_scope(self):
        # Synthetic recognition events exercise the real composer; no microphone or browser driver.
        result = subprocess.run(['node', '--test', 'tests/test_chat_intake.mjs'],
                                capture_output=True, timeout=40)
        self.assertEqual(result.returncode, 0,
                         (result.stdout + result.stderr).decode('utf-8', errors='replace')[-5000:])

    def test_registration_list_detail(self):
        patient_id = self.register()
        self.assertIn("테스트 전용 환자", self.client.get("/patients").text)
        self.assertIn("테스트 전용 환자", self.client.get(f"/patients/{patient_id}").text)
        self.assertEqual(self.client.post("/register", data={}).status_code, 422)

    def test_delete_uses_test_database(self):
        patient_id = self.register()
        self.assertEqual(self.client.get(f"/patients/{patient_id}/delete").status_code, 405)
        response = self.client.post(f"/patients/{patient_id}/delete", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/patients")
        self.assertIsNone(self.main.get_patient(patient_id))
        self.assertEqual(self.client.post(f"/patients/{patient_id}/delete").status_code, 404)

    def test_intake_routes_preserved(self):
        patient_id = self.register()
        for target in [
            "/register", "/emergency/check", "/emergency/check?patient_id=",
            f"/emergency/check?patient_id={patient_id}",
            f"/emergency/breathing?patient_id={patient_id}&consciousness=yes",
            f"/emergency/symptoms?patient_id={patient_id}&consciousness=yes&breathing=yes",
            f"/emergency/bodymap?patient_id={patient_id}&symptom=가슴 통증",
            "/emergency/hospitals",
        ]:
            with self.subTest(target=target):
                self.assertEqual(self.client.get(target).status_code, 200)

    def test_report_data_flow(self):
        patient_id = self.register()
        response = self.client.get("/emergency/report", params={
            "patient_id": patient_id, "consciousness": "yes", "breathing": "no",
            "symptom": ["가슴 통증", "호흡 곤란"], "body_part": "가슴",
        })
        self.assertEqual(response.status_code, 200)
        for text in ["테스트 전용 환자", "가슴 통증, 호흡 곤란", "비정상", 'action="/emergency/hospitals"']:
            self.assertIn(text, response.text)

    def test_coordinates_without_accuracy_and_unknown_state(self):
        response = self.client.get("/emergency/hospitals?latitude=37.5&longitude=127.0")
        self.assertEqual(response.status_code, 200)
        self.assertIn("정확도: 미확인", response.text)
        self.assertIn("실제 병원 API를 조회하지 않습니다", response.text)
        self.assertIn("미확인", response.text)
        self.assertNotIn("도로거리:</strong>", response.text)

    def test_external_api_guards_even_with_flags(self):
        import api_integrations as integrations
        from settings import external_api_enabled
        self.assertFalse(external_api_enabled())
        self.assertEqual(self.main.reverse_geocode(37.5, 127), (None, None))
        self.assertEqual(self.main.fetch_emergency_hospitals(37.5, 127)[0], [])
        for call in [lambda: self.main.api_xml("dummy", {}),
                     lambda: integrations._request("https://example.invalid"),
                     lambda: integrations._hira_xml("https://example.invalid", {})]:
            with self.assertRaises(RuntimeError):
                call()
        self.assertIn("대기", integrations.kakao_route(37, 127, 37, 127)["status"])

    def test_external_api_opt_in_modes(self):
        import settings
        for mode in ("development", "production", "test"):
            for flag in ("0", "1"):
                with self.subTest(mode=mode, flag=flag), patch.object(settings, "APP_ENV", mode), \
                     patch.dict(os.environ, {"QUICK119_EXTERNAL_API": flag}):
                    self.assertEqual(settings.external_api_enabled(), mode != "test" and flag == "1")
            with patch.object(settings, "APP_ENV", mode), patch.dict(os.environ, {}, clear=True):
                self.assertFalse(settings.external_api_enabled())

    def test_kakao_single_route_wire_format_and_geometry(self):
        import api_integrations as api
        import settings
        from unittest.mock import MagicMock
        api._KAKAO_CACHE.clear()
        body = {'routes': [{'result_code': 0, 'summary': {'distance': 1234, 'duration': 61},
                            'sections': [{'roads': [{'vertexes': [127.0, 37.5, 127.1, 37.6]}]}]}]}
        remote = MagicMock()
        remote.__enter__.return_value.read.return_value = json.dumps(body).encode()
        with patch.object(settings, 'APP_ENV', 'development'), \
             patch.dict(os.environ, {'KAKAO_REST_API_KEY': 'synthetic-kakao-key'}), \
             patch('urllib.request.urlopen', return_value=remote) as network:
            payload = {'origin': {'lat': 37.5, 'lon': 127.0}, 'destination': {'lat': 37.6, 'lon': 127.1}}
            response = self.client.post('/api/routes/car', json=payload)
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(data['coordinates'], [[127.0, 37.5], [127.1, 37.6]])
            self.assertEqual((data['distance_m'], data['duration_sec'], data['duration_min']), (1234, 61, 2))
            self.assertEqual(response.headers['cache-control'], 'no-store')
            request = network.call_args.args[0]
            from urllib.parse import urlparse, parse_qs
            url = urlparse(request.full_url)
            self.assertEqual(url.netloc, 'apis-navi.kakaomobility.com')
            query = parse_qs(url.query)
            self.assertEqual(query['origin'], ['127.0,37.5'])
            self.assertEqual(query['destination'], ['127.1,37.6'])
            self.assertEqual(query['summary'], ['false'])
            self.assertEqual(request.get_header('Authorization'), 'KakaoAK synthetic-kakao-key')
            self.assertNotIn('synthetic-kakao-key', response.text)
            self.assertTrue(self.client.post('/api/routes/car', json=payload).json()['cached'])
            self.assertEqual(network.call_count, 1)

    def test_kakao_multi_destination_mapping_and_partial_failure(self):
        import api_integrations as api
        import settings
        api._KAKAO_CACHE.clear()
        points = [{'key': str(i), 'lat': 37.5 + i / 1000, 'lon': 127.0} for i in range(5)]
        routes = [{'key': str(i), 'result_code': 0, 'summary': {'distance': 1000 + i, 'duration': 60 + i}}
                  for i in (4, 2, 0)]
        routes.append({'key': '1', 'result_code': 104, 'result_msg': 'synthetic-secret'})
        with patch.object(settings, 'APP_ENV', 'development'), \
             patch.dict(os.environ, {'KAKAO_REST_API_KEY': 'synthetic-key'}), \
             patch.object(api, '_request', return_value={'routes': routes}) as network:
            response = self.client.post('/api/routes/car/compare', json={
                'origin': {'lat': 37.5, 'lon': 127}, 'destinations': points})
            self.assertEqual(response.status_code, 200)
            data = response.json()['routes']
            self.assertEqual([r['key'] for r in data], ['0', '1', '2', '3', '4'])
            self.assertEqual(data[1]['code'], 'no_route')
            self.assertEqual(data[3]['code'], 'missing_route')
            self.assertEqual(data[4]['distance_m'], 1004)
            self.assertNotIn('synthetic-secret', response.text)
            self.assertEqual(network.call_count, 1)
            self.assertEqual(network.call_args.args[0], 'https://apis-navi.kakaomobility.com/v1/destinations/directions')
            self.assertEqual(network.call_args.kwargs['method'], 'POST')
            body = network.call_args.kwargs['body']
            self.assertEqual(body['origin'], {'x': 127, 'y': 37.5})
            self.assertEqual(body['destinations'][4], {'key': '4', 'x': 127, 'y': 37.504})
            self.assertEqual(body['radius'], 10000)

    def test_kakao_api_validation_and_disabled_mode(self):
        point = {'lat': 37.5, 'lon': 127}
        for bad in ({'lat': 127, 'lon': 37.5}, {'lat': 91, 'lon': 127},
                    {'lat': 37, 'lon': 181}, {'lat': 'NaN', 'lon': 127}):
            self.assertEqual(self.client.post('/api/routes/car', json={
                'origin': bad, 'destination': point}).status_code, 422)
        for points in ([], [{'key': 'a', **point}] * 6, [{'key': 'a', **point}] * 2):
            self.assertEqual(self.client.post('/api/routes/car/compare', json={
                'origin': point, 'destinations': points}).status_code, 422)
        response = self.client.post('/api/routes/car', json={'origin': point, 'destination': point})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['code'], 'disabled')

    def test_kakao_upstream_errors_are_sanitized(self):
        import api_integrations as api
        import settings
        from urllib.error import HTTPError, URLError
        secret = 'synthetic-secret-do-not-echo'
        point = {'lat': 37.5, 'lon': 127}
        cases = [(HTTPError('https://example.invalid/' + secret, code, secret, {}, io.BytesIO(secret.encode())),
                  expected, status) for code, expected, status in (
                      (401, 'authentication_failed', 502), (403, 'permission_denied', 502),
                      (429, 'quota_exceeded', 429), (500, 'upstream_error', 502))]
        cases += [(TimeoutError(secret), 'timeout', 504), (URLError(secret), 'network_error', 502),
                  (URLError(TimeoutError(secret)), 'timeout', 504), (ValueError(secret), 'invalid_response', 502)]
        for error, code, status in cases:
            with self.subTest(code=code), patch.object(settings, 'APP_ENV', 'development'), \
                 patch.dict(os.environ, {'KAKAO_REST_API_KEY': secret}), \
                 patch.object(api, '_request', side_effect=error):
                api._KAKAO_CACHE.clear()
                response = self.client.post('/api/routes/car', json={'origin': point, 'destination': point})
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.json()['code'], code)
                self.assertNotIn(secret, response.text)

    def test_kakao_missing_key_no_route_and_malformed_response(self):
        import api_integrations as api
        import settings
        with patch.object(settings, 'APP_ENV', 'development'), \
             patch.dict(os.environ, {'KAKAO_REST_API_KEY': ''}), patch.object(api, '_request') as network:
            self.assertEqual(api.kakao_route(37, 127, 37.1, 127.1)['code'], 'missing_key')
            network.assert_not_called()
        for routes, expected in (([{'result_code': 104}], 'no_route'), ([], 'invalid_response'),
                                 ([{'result_code': 0, 'summary': {'distance': -1, 'duration': 20}}], 'invalid_response'),
                                 ([{'result_code': 0, 'summary': {'distance': 1}}], 'invalid_response')):
            with patch.object(settings, 'APP_ENV', 'development'), \
                 patch.dict(os.environ, {'KAKAO_REST_API_KEY': 'synthetic-key'}), \
                 patch.object(api, '_request', return_value={'routes': routes}):
                api._KAKAO_CACHE.clear()
                self.assertEqual(api.kakao_route(37, 127, 37.1, 127.1)['code'], expected)

    def test_kakao_cache_expiry_and_mutation_isolation(self):
        import api_integrations as api
        import settings
        api._KAKAO_CACHE.clear()
        sample = {'routes': [{'result_code': 0, 'summary': {'distance': 100, 'duration': 60}}]}
        with patch.object(settings, 'APP_ENV', 'development'), \
             patch.dict(os.environ, {'KAKAO_REST_API_KEY': 'synthetic-key'}), \
             patch.object(api, '_request', return_value=sample) as network, \
             patch.object(api.time, 'monotonic', return_value=100) as timer:
            result = api.kakao_route(37, 127, 37.1, 127.1)
            result['distance_m'] = 9999
            self.assertEqual(api.kakao_route(37, 127, 37.1, 127.1)['distance_m'], 100)
            timer.return_value = 131
            self.assertFalse(api.kakao_route(37, 127, 37.1, 127.1)['cached'])
            self.assertEqual(network.call_count, 2)

    def test_hospital_route_comparison_uses_one_batch_and_handles_radius(self):
        import api_integrations as api
        hospitals = [{'lat': 37.5 + i / 1000, 'lon': 127} for i in range(4)]
        hospitals.append({'lat': 38, 'lon': 127})
        batch = {'code': 'ok', 'routes': [
            {'key': str(i), 'code': 'ok', 'status': '조회됨', 'duration_min': 5 + i, 'distance_km': 1 + i}
            for i in reversed(range(4))]}
        with patch.object(api, '_key', return_value='synthetic'), \
             patch.object(api, 'kakao_routes', return_value=batch) as request, \
             patch.object(api, 'kakao_route') as single:
            enriched = api.enrich_hospitals(hospitals, 37.5, 127)
            request.assert_called_once()
            self.assertEqual(len(request.call_args.args[2]), 4)
            self.assertEqual(enriched[2]['road']['duration_min'], 7)
            self.assertEqual(enriched[4]['road']['code'], 'outside_radius')
            single.assert_not_called()

    def test_car_route_ui_in_desktop_chrome_with_synthetic_gps(self):
        chrome = Path('C:/Program Files/Google/Chrome/Application/chrome.exe')
        if not chrome.exists():
            self.skipTest('Desktop Chrome unavailable; real Galaxy GPS remains unverified')
        import uvicorn
        from chrome_driver import Chrome
        hospitals = [{'name': '가상 응급실 <테스트>', 'lat': 37.6, 'lon': 127.1,
                      'phone': '000-0000-0000', 'address': '가상 주소', 'emergency_beds': '미확인',
                      'road': {'status': '조회됨', 'distance_km': 2.3, 'duration_min': 8}}]
        route = {'code': 'ok', 'status': '조회됨', 'distance_m': 2300, 'duration_sec': 480,
                 'coordinates': [[127, 37.5], [127.05, 37.55], [127.1, 37.6]], 'cached': False}
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(self.main.app, log_level='error', access_log=False, lifespan='off'))
        thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue(server.started)
            with patch.object(self.main, 'fetch_emergency_hospitals', return_value=(hospitals, None)), \
                 patch.object(self.main, 'enrich_hospitals', side_effect=lambda h, *args: h), \
                 patch.object(self.main, 'enrich_hospitals_with_hira', side_effect=lambda h: h), \
                 patch.object(self.main, 'enrich_hospitals_with_severe', side_effect=lambda h, *args: h), \
                 patch.object(self.main, 'reverse_geocode', return_value=(None, None)), \
                 patch.object(self.main, 'kakao_route', return_value=route) as request, \
                 tempfile.TemporaryDirectory(prefix='quick119-route-ui-') as folder:
                browser = Chrome(chrome, Path(folder) / 'profile')
                try:
                    browser.call('Page.enable')
                    browser.call('Network.enable')
                    browser.call('Network.setBlockedURLs', urls=['https://tile.openstreetmap.org/*'])
                    browser.call('Page.addScriptToEvaluateOnNewDocument', source='''
                      window.gpsCount = 0; window.gpsDenied = false;
                      Object.defineProperty(navigator, 'geolocation', {value: {
                        getCurrentPosition(ok, fail, options) {
                          window.gpsCount++;
                          if (options.maximumAge !== 0 || !options.enableHighAccuracy) throw new Error('GPS options');
                          setTimeout(() => window.gpsDenied ? fail({code: 1}) : ok({coords: {
                            latitude: 37.5 + (window.gpsCount - 1) / 1000,
                            longitude: 127, accuracy: 12}}), 0);
                        }
                      }});
                    ''')
                    browser.call('Page.navigate', url=f'http://127.0.0.1:{port}/emergency/hospitals?latitude=37.5&longitude=127')
                    outcome = browser.call('Runtime.evaluate', expression='''(async () => {
                      const assert = (condition, message) => { if (!condition) throw new Error(message); };
                      const wait = async predicate => {
                        const end = Date.now() + 5000;
                        while (!predicate()) { if (Date.now() > end) throw new Error('UI timeout');
                          await new Promise(resolve => setTimeout(resolve, 20)); }
                      };
                      await wait(() => document.querySelector('.car-route-button') && window.L);
                      assert(window.gpsCount === 0, 'GPS must wait for a click: ' + window.gpsCount);
                      assert(document.querySelectorAll('#car-route-panel').length === 1, 'One route panel');
                      assert(document.querySelector('.hospital-card summary').textContent.includes('8분'), 'Comparison in card');
                      document.querySelector('.hospital-card').open = true;
                      document.querySelector('.car-route-button').click();
                      await wait(() => !document.getElementById('car-route-result').hidden);
                      assert(document.getElementById('car-route-distance').textContent === '2.3 km', 'Distance');
                      assert(document.getElementById('car-route-duration').textContent === '약 8분', 'Duration');
                      assert(document.getElementById('car-route-destination').textContent.includes('<테스트>'), 'Name is text');
                      assert(document.querySelectorAll('#car-route-map path').length >= 3, 'Polyline and endpoint markers');
                      assert(!document.querySelector('.car-route-button').disabled, 'Button restored');
                      assert(new URL(document.getElementById('car-route-external').href).searchParams.get('sp') === '37.5,127', 'External origin');
                      document.getElementById('car-route-refresh').click();
                      await wait(() => window.gpsCount === 2 && !document.getElementById('car-route-result').hidden);
                      assert(document.getElementById('car-route-origin').textContent.includes('37.50100'), 'Fresh GPS');
                      window.gpsDenied = true;
                      document.getElementById('car-route-refresh').click();
                      await wait(() => document.getElementById('car-route-status').textContent.includes('거부'));
                      assert(document.getElementById('car-route-result').hidden, 'Hide stale route on failure');
                      assert(!document.querySelector('.car-route-button').disabled, 'Retry after GPS failure');
                      window.gpsDenied = false;
                      document.querySelector('.car-route-button').click();
                      await wait(() => window.gpsCount === 4 && !document.getElementById('car-route-result').hidden);
                      return 'PASS: GPS, route, map, refresh, denied permission';
                    })()''', awaitPromise=True, returnByValue=True)
                    self.assertNotIn('exceptionDetails', outcome, str(outcome))
                    self.assertEqual(outcome['result'].get('value'), 'PASS: GPS, route, map, refresh, denied permission')
                    self.assertEqual(request.call_count, 3)
                    self.assertEqual(request.call_args.args, (37.503, 127, 37.6, 127.1))
                    for width in (360, 412, 1024):
                        browser.call('Emulation.setDeviceMetricsOverride', width=width, height=850,
                                     deviceScaleFactor=1, mobile=width < 500)
                        layout = browser.call('Runtime.evaluate', expression='''({
                          fits: document.documentElement.scrollWidth <= window.innerWidth,
                          mapVisible: document.getElementById('car-route-map').clientWidth > 0
                        })''', returnByValue=True)['result']['value']
                        self.assertEqual(layout, {'fits': True, 'mapVisible': True}, str(width))
                finally:
                    browser.close()
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            listener.close()

    def test_live_car_navigation_with_synthetic_gps(self):
        chrome = Path('C:/Program Files/Google/Chrome/Application/chrome.exe')
        if not chrome.exists():
            self.skipTest('Desktop Chrome unavailable; synthetic navigation not run')
        from chrome_driver import Chrome
        sources = {
            'PANEL_HTML': 'templates/car_route_panel.html',
            'PANEL_CSS': 'static/car-routes.css',
            'LEAFLET_CSS': 'static/vendor/leaflet/leaflet.css',
            'LEAFLET_SOURCE': 'static/vendor/leaflet/leaflet.js',
            'NAV_SOURCE': 'static/car-routes.js',
        }
        with tempfile.TemporaryDirectory(prefix='quick119-navigation-') as folder:
            browser = Chrome(chrome, Path(folder) / 'profile')
            try:
                browser.call('Network.enable')
                browser.call('Network.setBlockedURLs', urls=['http://*', 'https://*'])
                # Keep each message below the existing CDP helper's 64KiB frame limit.
                for key, path in sources.items():
                    browser.call('Runtime.evaluate', expression='var ' + key + ' = "";')
                    source = Path(path).read_text(encoding='utf-8-sig')
                    for offset in range(0, len(source), 4000):
                        browser.call('Runtime.evaluate', expression=key + ' += ' + json.dumps(source[offset:offset + 4000]))
                harness = Path('tests/car_navigation_harness.js').read_text(encoding='utf-8-sig')
                outcome = browser.call('Runtime.evaluate', expression=harness,
                                       awaitPromise=True, returnByValue=True)
                self.assertNotIn('exceptionDetails', outcome, str(outcome))
                result = outcome['result']['value']
                self.assertGreaterEqual(result['checks'], 35, str(result))
                print(f"Synthetic navigation: {result['checks']} checks passed")
            finally:
                browser.close()

    def test_env_loading_requires_explicit_allowed_mode(self):
        import settings

        async def start():
            async with self.main.lifespan(self.main.app):
                pass

        for mode, api, load, expected in (
            ("test", "1", "1", False), ("development", "0", "1", False),
            ("development", "1", "0", False), ("development", "1", "1", True),
            ("production", "0", "1", True),
        ):
            with self.subTest(mode=mode, api=api, load=load), \
                 patch.object(self.main, "APP_ENV", mode), patch.object(settings, "APP_ENV", mode), \
                 patch.dict(os.environ, {"QUICK119_EXTERNAL_API": api, "QUICK119_LOAD_ENV": load}), \
                 patch.object(self.main, "create_database"), patch.object(self.main, "load_local_env") as loader:
                asyncio.run(start())
                self.assertEqual(loader.call_count, int(expected))

    def test_development_startup_reads_mock_env_without_overriding_settings(self):
        import settings
        # Synthetic credentials only; never read the workspace .env or database.
        content = ('EMERGENCY_API_KEY="synthetic-file-key"\nQUICK119_ENV=production\n'
                   'QUICK119_DB_PATH=emergency.db\nQUICK119_EXTERNAL_API=0\nQUICK119_LOAD_ENV=0\n')

        async def start():
            async with self.main.lifespan(self.main.app):
                pass

        for inherited_key in (None, "synthetic-inherited-key"):
            env = {"QUICK119_ENV": "development", "QUICK119_DB_PATH": str(self.test_db),
                   "QUICK119_EXTERNAL_API": "1", "QUICK119_LOAD_ENV": "1"}
            if inherited_key is not None:
                env["EMERGENCY_API_KEY"] = inherited_key
            with patch.dict(os.environ, env, clear=True), \
                 patch.object(self.main, "APP_ENV", "development"), patch.object(settings, "APP_ENV", "development"), \
                 patch.object(self.main, "create_database") as create_db, \
                 patch.object(Path, "exists", return_value=True), \
                 patch.object(Path, "read_text", return_value=content) as read_env:
                asyncio.run(start())
                read_env.assert_called_once_with(encoding="utf-8-sig")
                create_db.assert_called_once_with()
                self.assertEqual(os.environ["EMERGENCY_API_KEY"], inherited_key or "synthetic-file-key")
                for name, value in env.items():
                    self.assertEqual(os.environ[name], value)
                self.assertEqual(self.main.DB_PATH, self.test_db)

    def test_public_api_errors_do_not_echo_remote_secrets(self):
        secret = "synthetic-secret-must-not-be-displayed"
        payloads = (
            f"<response><header><resultCode>30</resultCode><resultMsg>{secret}</resultMsg></header></response>",
            f"<OpenAPI_ServiceResponse><cmmMsgHeader><returnReasonCode>30</returnReasonCode>"
            f"<errMsg>{secret}</errMsg></cmmMsgHeader></OpenAPI_ServiceResponse>",
            f"<response><header><resultCode>{secret}</resultCode></header></response>",
        )
        for payload, expected_code in zip(payloads, ("30", "30", "미확인")):
            with patch.object(self.main, "require_external_api"), \
                 patch("urllib.request.urlopen", return_value=io.BytesIO(payload.encode())):
                with self.assertRaises(self.main.EmergencyAPIResponseError) as caught:
                    self.main.api_xml("synthetic", {"serviceKey": secret})
                self.assertNotIn(secret, str(caught.exception))
                self.assertIn("코드 " + expected_code, str(caught.exception))

        errors = (
            (self.main.EmergencyAPIResponseError("30"), "코드 30"),
            (self.main.urllib.error.HTTPError("https://example.invalid/?serviceKey=" + secret, 403, secret, {}, None), "403"),
            (self.main.urllib.error.URLError(secret), "네트워크 연결"),
            (TimeoutError(secret), "시간이 초과"),
            (ValueError(secret), "응답 처리 오류"),
        )
        for error, expected in errors:
            with patch.object(self.main, "external_api_enabled", return_value=True), \
                 patch.dict(os.environ, {"EMERGENCY_API_KEY": secret}), \
                 patch.object(self.main, "reverse_geocode", return_value=("서울특별시", "중구")), \
                 patch.object(self.main, "api_xml", side_effect=error):
                response = self.client.get("/emergency/hospitals?latitude=37.5&longitude=127")
                self.assertEqual(response.status_code, 200)
                self.assertIn(expected, response.text)
                self.assertNotIn(secret, response.text)

    def test_static_assets_and_template_not_exposed(self):
        for name in ["quick119.css", "quick119.js", "medical-ui.css", "icons.svg"]:
            with self.subTest(name=name):
                self.assertEqual(self.client.get("/static/" + name).status_code, 200)
        self.assertEqual(self.client.get("/static/index.html").status_code, 404)
        self.assertEqual(self.client.get("/static/../.env").status_code, 404)

    def test_original_route_contract(self):
        expected = {("/", "GET"), ("/register", "GET"), ("/register", "POST"),
                    ("/patients", "GET"), ("/patients/{patient_id}", "GET"),
                    ("/patients/{patient_id}/delete", "POST"), ("/voice-intake", "GET"),
                    ("/emergency", "GET"), ("/emergency/check", "GET"),
                    ("/emergency/breathing", "GET"), ("/emergency/symptoms", "GET"),
                    ("/emergency/bodymap", "GET"), ("/emergency/report", "GET"),
                    ("/emergency/hospitals", "GET")}
        routes = {(r.path, method) for r in self.main.app.routes for method in getattr(r, "methods", [])}
        self.assertTrue(expected <= routes)

    def test_nonproduction_rejects_original_db(self):
        env = dict(os.environ, QUICK119_ENV="test", QUICK119_DB_PATH=str(Path.cwd() / "emergency.db"))
        result = subprocess.run([sys.executable, "-c", "import settings"], env=env, capture_output=True)
        self.assertNotEqual(result.returncode, 0)

    def test_legacy_styles_unchanged(self):
        self.assertEqual(Path("templates/medical-ui.css").read_bytes(), Path("static/medical-ui.css").read_bytes())

    def test_pwa_manifest_and_png_icons(self):
        response = self.client.get("/manifest.webmanifest")
        self.assertEqual(response.status_code, 200)
        self.assertIn("application/manifest+json", response.headers["content-type"])
        manifest = response.json()
        self.assertEqual(manifest["name"], "QUICK119")
        self.assertEqual(manifest["short_name"], "퀵119")
        self.assertEqual(manifest["display"], "standalone")
        self.assertEqual(manifest["scope"], "/")
        self.assertEqual(manifest["background_color"], "#17375E")
        self.assertEqual(self.client.get(manifest["start_url"]).status_code, 200)
        for icon in manifest["icons"]:
            png = self.client.get(icon["src"])
            self.assertEqual(png.status_code, 200)
            self.assertEqual(png.content[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(struct.unpack(">II", png.content[16:24]), tuple(map(int, icon["sizes"].split("x"))))

    def test_pwa_resources_on_new_and_legacy_pages(self):
        for url in ["/", "/emergency?step=patient", "/register", "/patients", "/emergency/check", "/pwa-check"]:
            with self.subTest(url=url):
                html = self.client.get(url).text
                self.assertEqual(html.count('rel="manifest"'), 1)
                self.assertEqual(html.count('src="/static/pwa.js"'), 1)
                self.assertEqual(html.count('id="pwa-install"'), 1)

    def test_sensitive_responses_no_store(self):
        patient_id = self.register()
        for url in ["/", "/patients", f"/patients/{patient_id}", "/register", "/voice-intake", "/emergency",
                    "/emergency/report", "/emergency/hospitals?latitude=37.5&longitude=127", "/pwa-check"]:
            with self.subTest(url=url):
                response = self.client.get(url, follow_redirects=False)
                self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertEqual(response.headers["referrer-policy"], "no-referrer")
                self.assertIn("microphone=(self)", response.headers["permissions-policy"])

    def test_service_worker_scope_and_precache_resources(self):
        response = self.client.get("/sw.js")
        self.assertEqual(response.status_code, 200)
        self.assertIn("application/javascript", response.headers["content-type"])
        self.assertEqual(response.headers["service-worker-allowed"], "/")
        self.assertEqual(response.headers["cache-control"], "no-store")
        assets = json.loads(re.search(r"const STATIC_ASSETS = (\[.*?\]);", response.text, re.S).group(1))
        self.assertEqual(len(assets), len(set(assets)))
        for asset in assets:
            self.assertTrue(asset.startswith("/static/"))
            self.assertNotIn("?", asset)
            self.assertEqual(self.client.get(asset).status_code, 200)
        offline = self.client.get("/static/offline.html")
        self.assertEqual(offline.content, Path("static/offline.html").read_bytes())
        self.assertNotIn('class="med-header"', offline.text)
        self.assertIn('href="tel:119"', offline.text)

    def test_permission_checks_and_manual_fallback(self):
        response = self.client.get("/pwa-check")
        self.assertEqual(response.status_code, 200)
        for name in ["check-mic", "check-gps", "check-secure", "check-display"]:
            self.assertIn(f'id="{name}"', response.text)
        self.assertIn('href="/transport/"', response.text)
        voice = Path("static/chat-voice.js").read_text(encoding="utf-8")
        self.assertIn("window.isSecureContext", voice)
        self.assertIn("메시지를 직접 입력해 주세요.", voice)
        workspace = Path("templates/transport_workspace.html").read_text(encoding="utf-8")
        self.assertIn('id="transport-message"', workspace)
        hospitals = self.client.get("/emergency/hospitals").text
        self.assertIn('onclick="getCurrentLocation()"', hospitals)
        self.assertNotIn('document.addEventListener("DOMContentLoaded"', hospitals)

    def test_service_worker_logic_in_desktop_chrome(self):
        # 실제 SW 설치나 Android 검증과 구분되는, 브라우저 JS 엔진에서의 모의 이벤트 테스트.
        chrome = Path("C:/Program Files/Google/Chrome/Application/chrome.exe")
        if not chrome.exists():
            self.skipTest("Desktop Chrome executable unavailable; SW logic browser test not run")
        source = Path("static/sw.js").read_text(encoding="utf-8")
        harness = Path("tests/sw_harness.js").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory(prefix="quick119-js-") as folder:
            root = Path(folder).resolve()
            page = root / "worker-test.html"
            page.write_text('<!doctype html><meta charset="utf-8"><pre id="result">PENDING</pre><script>\n'
                            + 'const WORKER_SOURCE = ' + json.dumps(source) + ';\n' + harness + '\n</script>', encoding="utf-8")
            result = subprocess.run([str(chrome), "--headless=new", "--disable-gpu", "--no-first-run",
                                     "--no-default-browser-check", "--disable-background-networking",
                                     "--disable-component-update", "--disable-sync", "--disable-extensions",
                                     "--metrics-recording-only", "--dump-dom", "--virtual-time-budget=3000",
                                     "--user-data-dir=" + str(root / "profile"), page.as_uri()],
                                    capture_output=True, timeout=35)
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace")[-1000:])

            class ResultParser(HTMLParser):
                active = False
                result = ""

                def handle_starttag(self, tag, attrs):
                    if tag == "pre" and dict(attrs).get("id") == "result":
                        self.active = True

                def handle_endtag(self, tag):
                    if tag == "pre":
                        self.active = False

                def handle_data(self, data):
                    if self.active:
                        self.result += data

            parser = ResultParser()
            parser.feed(result.stdout.decode("utf-8", errors="replace"))
            self.assertEqual(parser.result, "PASS: 10 worker checks", parser.result)

    def test_pwa_live_registration_and_mobile_widths_desktop_only(self):
        chrome = Path("C:/Program Files/Google/Chrome/Application/chrome.exe")
        if not chrome.exists():
            self.skipTest("Desktop Chrome unavailable; Android still unverified")
        import uvicorn
        from chrome_driver import Chrome
        from fastapi.responses import HTMLResponse
        harness = Path("tests/pwa_live_harness.js").read_text(encoding="utf-8")

        def smoke_page():
            return HTMLResponse('<!doctype html><meta charset="utf-8"><pre id="result">PENDING</pre><script>' + harness + '</script>',
                                headers={"Cache-Control": "no-store"})

        previous = list(self.main.app.routes)
        self.main.app.add_api_route("/__pwa-smoke", smoke_page, include_in_schema=False)
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(self.main.app, log_level="error", access_log=False, lifespan="off"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue(server.started, "Loopback test server failed to start")
            with tempfile.TemporaryDirectory(prefix="quick119-live-") as folder:
                profile = Path(folder).resolve() / "profile"
                browser = Chrome(chrome, profile)
                try:
                    browser.call("Page.navigate", url=f"http://127.0.0.1:{port}/__pwa-smoke")
                    outcome = browser.call("Runtime.evaluate", expression="""new Promise((resolve, reject) => {
                      const started = Date.now();
                      const timer = setInterval(() => {
                        const value = document.getElementById('result')?.textContent;
                        if (value && value !== 'PENDING') { clearInterval(timer); resolve(value); }
                        else if (Date.now() - started > 20000) { clearInterval(timer); reject(new Error('Browser check timed out')); }
                      }, 50);
                    })""", awaitPromise=True, returnByValue=True, timeout=22000)
                    self.assertNotIn("exceptionDetails", outcome, str(outcome))
                    self.assertEqual(outcome["result"].get("value"), "PASS: live worker, private cache exclusion, 20 layouts")
                finally:
                    browser.close()
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            listener.close()
            self.main.app.router.routes[:] = previous


if __name__ == "__main__":
    unittest.main()
