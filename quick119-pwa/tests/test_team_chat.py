"""Real loopback HTTP integration; new synthetic DBs, no existing DB or .env reads."""
import json
from contextlib import closing
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

import team_chat_gateway as gateway

ROOT = Path(__file__).resolve().parents[1]


def clean_env():
    result = {name: os.environ[name] for name in ("SystemRoot", "WINDIR", "TEMP", "TMP", "PATH")
              if name in os.environ}
    result.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    return result


class GatewayGuards(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(gateway.router)
        self.client = TestClient(app, client=("127.0.0.1", 51000))
        env = patch.dict(os.environ, QUICK119_ENV="test", QUICK119_TEAM_CHAT_ENABLED="1",
                         QUICK119_TEAM_CHAT_URL="http://127.0.0.1:18033")
        env.start()
        self.addCleanup(env.stop)

    def test_disabled_and_production_never_connect(self):
        with patch.object(gateway.httpx, "AsyncClient") as upstream:
            with patch.dict(os.environ, QUICK119_TEAM_CHAT_ENABLED="0"):
                self.assertEqual(self.client.get("/api/team-runtime").status_code, 503)
            with patch.dict(os.environ, QUICK119_ENV="production"):
                self.assertEqual(self.client.get("/api/team-runtime").status_code, 503)
            upstream.assert_not_called()

    def test_explicit_synthetic_routes_are_local_and_fixture_only(self):
        from types import SimpleNamespace as Item
        from fastapi import HTTPException
        from synthetic_routes import demo_route
        request = Item(client=Item(host='127.0.0.1'))
        payload = Item(origin=Item(lat=37.5051, lon=126.9573),
                       destination=Item(lat=37.50707428493414, lon=126.96079378447554))
        with patch.dict(os.environ, QUICK119_SYNTHETIC_ROUTES='0'):
            self.assertIsNone(demo_route(request, payload))
        with patch.dict(os.environ, QUICK119_SYNTHETIC_ROUTES='1'):
            result = demo_route(request, payload)
            self.assertEqual(result['provider'], 'synthetic')
            self.assertEqual(result['coordinates'], [[payload.origin.lon, payload.origin.lat],
                                                     [payload.destination.lon, payload.destination.lat]])
            with self.assertRaises(HTTPException):
                demo_route(Item(client=Item(host='192.168.0.50')), payload)
            with self.assertRaises(HTTPException):
                demo_route(request, Item(origin=Item(lat=35.1595, lon=126.8526), destination=payload.destination))
            for lat, lon in [(35.1715, 126.8326), (37.5, 126.95)]:
                with self.subTest(lat=lat, lon=lon), self.assertRaises(HTTPException):
                    payload.destination = Item(lat=lat, lon=lon)
                    demo_route(request, payload)

    def test_remote_url_credentials_paths_and_redirect_targets_rejected(self):
        for url in ("http://example.com:80", "http://127.0.0.1:80/private", "http://user:pass@127.0.0.1:80",
                    "https://127.0.0.1:80", "http://127.0.0.1:99999", "http://127.0.0.1:80?key=x"):
            with self.subTest(url=url), patch.dict(os.environ, QUICK119_TEAM_CHAT_URL=url):
                self.assertEqual(self.client.get("/api/team-runtime").status_code, 503)

    def test_lan_and_cross_origin_rejected(self):
        with TestClient(self.client.app, client=("192.168.0.50", 51000)) as lan:
            self.assertEqual(lan.get("/api/team-runtime").status_code, 403)
        for headers in ({"Origin": "http://other.invalid"}, {"Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(self.client.get("/api/team-runtime", headers=headers).status_code, 403)

    def test_real_ids_and_nonfixture_creations_rejected(self):
        for path in ("/api/patients/1/conversation", "/api/hospitals/NMC-REAL/inquiries"):
            self.assertEqual(self.client.get(path).status_code, 403)
        self.assertEqual(self.client.post("/api/patients", json={"id": "REAL"}).status_code, 403)
        self.assertEqual(self.client.post("/api/patients", json=[]).status_code, 400)
        self.assertEqual(self.client.post("/api/patients", content=b"x" * (gateway.MAX_BYTES + 1)).status_code, 413)

    def test_non_synthetic_runtime_and_upstream_failure_fail_closed(self):
        with patch.object(gateway, "_json_response", AsyncMock(return_value=(200, {"synthetic": False}))):
            self.assertEqual(self.client.get("/api/team-runtime").status_code, 503)
        for error, expected in ((httpx.ConnectError("secret upstream text"), 502),
                                (httpx.ReadTimeout("secret upstream text"), 504)):
            with patch.object(gateway, "_json_response", AsyncMock(side_effect=error)):
                response = self.client.get("/api/team-runtime")
                self.assertEqual(response.status_code, expected)
                self.assertNotIn("secret", response.text)


class TransportSpeech(unittest.TestCase):
    def setUp(self):
        import team_chat_ui
        self.ui = team_chat_ui
        app = FastAPI()
        app.include_router(team_chat_ui.router)
        self.client = TestClient(app, client=("127.0.0.1", 51000))
        env = patch.dict(os.environ, QUICK119_ENV="test", QUICK119_TEAM_CHAT_ENABLED="1",
                         QUICK119_TEAM_CHAT_URL="http://127.0.0.1:18033")
        env.start()
        self.addCleanup(env.stop)

    def test_pc_voice_is_local_same_origin_and_bounded(self):
        speech = self.ui.tts_audio
        with patch.dict(speech._cache, clear=True), patch.object(speech, "neural_enabled", return_value=False), \
                patch.object(speech, "_render", return_value=b"offline-wave") as render:
            response = self.client.post("/api/transport-tts", json={"text": "  가상   안내  "})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["content-type"], "audio/wav")
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(response.content, b"offline-wave")
            with TestClient(self.client.app, client=("192.168.0.50", 51000)) as lan:
                self.assertEqual(lan.post("/api/transport-tts", json={"text": "안내"}).status_code, 403)
            self.assertEqual(self.client.post("/api/transport-tts", json={"text": "안내"},
                                              headers={"Origin": "http://other.invalid"}).status_code, 403)
            for payload in ({"text": " \n "}, {"text": "가" * (speech.MAX_CHARS + 1)}, {"text": 3}, []):
                with self.subTest(payload=str(payload)[:20]):
                    self.assertEqual(self.client.post("/api/transport-tts", json=payload).status_code, 400)
            body = json.dumps({"text": "가상 안내"}).encode().ljust(4096)
            self.assertEqual(self.client.post("/api/transport-tts", content=body).status_code, 200)
            self.assertEqual(self.client.post("/api/transport-tts", content=iter((body, b" "))).status_code, 413)
            render.assert_called_once_with("가상 안내")
            with speech._lock:
                response = self.client.post("/api/transport-tts", json={"text": "다른 문장"})
                self.assertEqual((response.status_code, response.headers["retry-after"]), (503, "1"))
        with patch.object(speech, "synthesize", side_effect=speech.SpeechUnavailable()):
            self.assertEqual(self.client.post("/api/transport-tts", json={"text": "안내"}).status_code, 503)

    def test_neural_audio_requires_both_settings_and_cannot_be_reused_offline(self):
        import settings
        speech = self.ui.tts_audio
        phrase = "가상 안내"
        with patch.dict(speech._cache, clear=True), \
                patch.object(speech, "_render", return_value=b"wave") as offline, \
                patch.object(speech, "_render_neural", return_value=b"mp3") as online:
            configs = [
                ("development", "1", "neural", True), ("development", "0", "neural", False),
                ("development", "1", "", False), ("development", "1", "local", False),
                ("test", "1", "neural", False), ("development", "1", "neural", True),
            ]
            for mode, allowed, voice, enabled in configs:
                with self.subTest(mode=mode, allowed=allowed, voice=voice), \
                        patch.object(settings, "APP_ENV", mode), \
                        patch.dict(os.environ, QUICK119_EXTERNAL_API=allowed, QUICK119_TTS_VOICE=voice):
                    self.assertIs(speech.neural_enabled(), enabled)
                    expected = (b"mp3", "audio/mpeg") if enabled else (b"wave", "audio/wav")
                    self.assertEqual(speech.synthesize(phrase), expected)
            online.assert_called_once_with(phrase)
            offline.assert_called_once_with(phrase)


class LiveTeamChat(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.run_dir = ROOT / ".local/test-runs" / uuid4().hex
        cls.run_dir.mkdir(parents=True)
        cls.processes = []
        cls.handles = []
        cls.addClassCleanup(cls.cleanup)
        cls.team_db = cls.run_dir / "team.db"
        cls.local_db = cls.run_dir / "quick119.db"
        ready = cls.run_dir / "ready.json"
        cls.spawn([sys.executable, str(ROOT / "scripts/run-team-chat.py"), "--port", "0",
                   "--db", str(cls.team_db), "--ready-file", str(ready)], clean_env(), "team")
        deadline = time.monotonic() + 30
        while not ready.exists():
            if cls.processes[0].poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("Team runtime failed. See " + str(cls.run_dir / "team.log"))
            time.sleep(.05)
        readiness = json.loads(ready.read_text(encoding="utf-8"))
        if Path(readiness["source_root"]).resolve() != ROOT.parent:
            raise AssertionError("Team API must run from the same checkout as the PWA and hospital UI.")
        cls.team_url = readiness["base_url"]
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = clean_env()
        env.update(QUICK119_ENV="test", QUICK119_DB_PATH=str(cls.local_db), QUICK119_LOAD_ENV="0",
                   QUICK119_EXTERNAL_API="0", QUICK119_TEAM_CHAT_ENABLED="1", QUICK119_TEAM_CHAT_URL=cls.team_url)
        cls.spawn([sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(port),
                   "--no-proxy-headers", "--no-access-log"], env, "fastapi")
        cls.api_url = f"http://127.0.0.1:{port}"
        cls.http = httpx.Client(trust_env=False, timeout=10)
        cls.addClassCleanup(cls.http.close)
        deadline = time.monotonic() + 30
        while True:
            try:
                response = cls.http.get(cls.api_url + "/api/team-runtime")
                if response.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if cls.processes[-1].poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("FastAPI connection failed. See " + str(cls.run_dir / "fastapi.log"))
            time.sleep(.05)

    @classmethod
    def spawn(cls, command, env, name):
        handle = (cls.run_dir / (name + ".log")).open("w", encoding="utf-8")
        cls.handles.append(handle)
        cls.processes.append(subprocess.Popen(command, cwd=ROOT, env=env, stdout=handle,
                                              stderr=subprocess.STDOUT,
                                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)))

    @classmethod
    def cleanup(cls):
        for process in reversed(cls.processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        for handle in cls.handles:
            handle.close()

    def call(self, method, path, body=None, *, direct=False, status=200):
        response = self.http.request(method, (self.team_url if direct else self.api_url) + path,
                                     **({"json": body} if body is not None else {}))
        self.assertEqual(response.status_code, status, response.text)
        if not direct:
            self.assertEqual(response.headers.get("cache-control"), "no-store")
        return response.json()

    def create_case(self):
        pid = "Q119-TEST-" + uuid4().hex
        self.call("POST", "/api/patients", {
            "id": pid, "reporter_role": "transporter", "consent": True,
            "age": 30, "symptoms": "가상 테스트", "priority_source": "unknown",
            "lat": 35.1595, "lon": 126.8526, "required_capabilities": [],
            "resource_demands": {"ed_bed": 1}, "extra": {"synthetic_fixture": True}})
        return pid

    def test_runtime_and_existing_fastapi_pages(self):
        info = self.call("GET", "/api/team-runtime")
        self.assertTrue(info["synthetic"])
        self.assertEqual(info["mode"], "voice-only")
        self.assertTrue(info["profile"]["ok"])
        self.assertFalse(info["workers"]["planning"]["enabled"])
        self.assertEqual(info["database"]["hospital_count"], 10)
        for path in ("/", "/transport/", "/patients", "/emergency/hospitals", "/manifest.webmanifest", "/sw.js"):
            self.assertEqual(self.http.get(self.api_url + path).status_code, 200)
        patients = self.http.get(self.api_url + "/api/transport/patients")
        self.assertEqual(patients.status_code, 200)
        self.assertIsInstance(patients.json()["patients"], list)
        self.assertIn('href="/transport/?new=1"', self.http.get(self.api_url + "/").text)
        self.assertEqual(self.http.get(self.api_url + "/?source=pwa", follow_redirects=False).headers["location"], "/transport/")
        self.assertEqual(self.http.get(self.api_url + "/manifest.webmanifest").json()["start_url"], "/transport/")
        patient_page = self.http.get(self.api_url + "/patients")
        self.assertIn("transport-patient-cases", patient_page.text)
        self.assertIn("/static/transport-patients.js?v=3", patient_page.text)
        for path in ('/emergency', '/voice-intake'):
            response = self.http.get(self.api_url + path)
            self.assertEqual(response.status_code, 303)
            self.assertEqual(response.headers['location'], '/transport/')
        self.assertEqual(self.http.post(self.api_url + "/api/routes/car", json={
            "origin": {"lat": 35.15, "lon": 126.85},
            "destination": {"lat": 35.16, "lon": 126.86}}).status_code, 503)

    def test_workspace_shell_and_hospital_memory_adapter(self):
        response = self.http.get(self.api_url + '/transport/')
        self.assertEqual(response.status_code, 200)
        for element in ('transport-toggle', 'transport-gps', 'transport-chat', 'car-route-map'):
            self.assertEqual(response.text.count('id="' + element + '"'), 1)
        hospital = self.http.get(self.api_url + '/hospital/?hid=H01')
        self.assertEqual(hospital.status_code, 200)
        self.assertNotIn('localStorage', hospital.text)
        self.assertIn('/static/hospital-memory.js', hospital.text)
        self.assertNotIn("params.get('server')", hospital.text)
        self.assertIn("serverBase = location.origin;", hospital.text)
        self.assertEqual(hospital.headers['cache-control'], 'no-store')
        self.assertIn("connect-src 'self'", hospital.headers['content-security-policy'])
        self.assertEqual(self.http.get(self.api_url + '/hospital/chat-recovery.mjs').status_code, 200)
        panel = self.http.get(self.api_url + '/hospital/panel.html?hid=H01')
        self.assertEqual(panel.status_code, 200)
        self.assertIn('/static/hospital-memory.js', panel.text)
        self.assertIn('실제 환자정보 입력 금지', panel.text)
        demo = self.http.get(self.api_url + '/hospital/emr-demo.html?hid=H01')
        self.assertEqual(demo.status_code, 200)
        self.assertIn('가상 EMR · 시연용 화면', demo.text)
        self.assertIn('id="panelFrame"', demo.text)
        for asset in ('panel.css', 'panel.mjs', 'panel-state.mjs'):
            served = self.http.get(self.api_url + '/hospital/' + asset)
            self.assertEqual(served.status_code, 200, asset)
            self.assertNotIn('localStorage', served.text)
            self.assertEqual(served.headers['cache-control'], 'no-store')

    def test_patient_list_numbers_details_and_confirmed_delete(self):
        first = self.create_case()
        second = self.create_case()
        self.call("POST", f"/api/patients/{second}/inquiries", {"hospital_ids": ["H01"]})
        hospital_cases = self.call("GET", "/api/hospitals/H01/inquiries", direct=True)
        self.assertTrue(any(item["patient_id"] == second for item in hospital_cases["open"]))
        cases = {item["id"]: item for item in self.call("GET", "/api/transport/patients")["patients"]}
        first_number = cases[first]["case_number"]
        second_number = cases[second]["case_number"]
        self.assertNotEqual(first_number, second_number)

        from chrome_driver import Chrome
        mobile = Chrome(Path('C:/Program Files/Google/Chrome/Application/chrome.exe'),
                        self.run_dir / ('patients-profile-' + uuid4().hex))
        self.addCleanup(mobile.close)
        mobile.call('Page.enable')
        mobile.call('Emulation.setDeviceMetricsOverride', width=412, height=915, deviceScaleFactor=1, mobile=True)
        mobile.call('Page.addScriptToEvaluateOnNewDocument', source="""
          window.confirmMessages = [];
          window.confirm = message => { window.confirmMessages.push(message); return true; };
        """)

        def js(code):
            result = mobile.call('Runtime.evaluate', expression=code, awaitPromise=True, returnByValue=True)
            self.assertNotIn('exceptionDetails', result)
            return result['result'].get('value')

        def wait(condition):
            end = time.monotonic() + 12
            while time.monotonic() < end:
                if js('Boolean(' + condition + ')'):
                    return
                time.sleep(.08)
            self.fail(condition)

        mobile.call('Page.navigate', url=self.api_url + '/patients')
        wait("document.querySelector('[data-patient-id]')")
        first_selector = f'[data-patient-id="{first}"]'
        second_selector = f'[data-patient-id="{second}"]'
        wait(f"document.querySelector('{first_selector}') && document.querySelector('{second_selector}')")
        self.assertEqual(js(f"document.querySelector('{first_selector} .transport-patient-case-title').textContent"),
                         f'환자 {first_number}')
        self.assertEqual(js(f"document.querySelector('{second_selector} .transport-patient-case-title').textContent"),
                         f'환자 {second_number}')
        self.assertFalse(js(f"document.querySelector('{second_selector} details').open"))
        self.assertTrue(js(f"document.querySelector('{second_selector}').innerText.includes('수용 문의 중')"))
        js(f"document.querySelector('{second_selector} .transport-patient-delete').click()")
        wait(f"!document.querySelector('{second_selector}')")
        self.assertIn(f'환자 {second_number}', js('window.confirmMessages[0]'))
        self.assertEqual(self.call('GET', f'/api/patients/{second}', status=404)['error'], 'NOT_FOUND')
        remaining_hospital_cases = self.call("GET", "/api/hospitals/H01/inquiries", direct=True)
        self.assertFalse(any(item["patient_id"] == second for item in
                             remaining_hospital_cases["open"] + remaining_hospital_cases["recent"]))

        third = self.create_case()
        updated = {item["id"]: item for item in self.call("GET", "/api/transport/patients")["patients"]}
        self.assertEqual(updated[first]["case_number"], first_number)
        self.assertEqual(updated[third]["case_number"], second_number + 1)

    def test_mobile_header_keeps_title_and_all_participant_labels_visible(self):
        import base64
        from chrome_driver import Chrome

        pid = self.create_case()
        inquiry = self.call("POST", f"/api/patients/{pid}/inquiries", {"hospital_ids": ["H01"]})["inquiries"][0]
        self.call("POST", f"/api/inquiries/{inquiry['id']}/respond",
                  {"action": "accept", "handled_by": "가상 의료진"})
        self.call("POST", f"/api/patients/{pid}/confirm", {"inquiry_id": inquiry["id"]})
        mobile = Chrome(Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
                        self.run_dir / ("header-profile-" + uuid4().hex))
        self.addCleanup(mobile.close)
        mobile.call("Page.enable")
        mobile.call("Emulation.setDeviceMetricsOverride", width=360, height=844,
                    deviceScaleFactor=1, mobile=True)
        mobile.call("Page.navigate", url=f"{self.api_url}/transport/?pid={pid}")

        def evaluate(source):
            result = mobile.call("Runtime.evaluate", expression=source, awaitPromise=True, returnByValue=True)
            self.assertNotIn("exceptionDetails", result, str(result))
            return result.get("result", {}).get("value")

        def wait(condition):
            deadline = time.monotonic() + 16
            while time.monotonic() < deadline:
                try:
                    if evaluate("Boolean(" + condition + ")"):
                        return
                except RuntimeError as failure:
                    if "context" not in str(failure).lower():
                        raise
                time.sleep(.08)
            self.fail("Timed out: " + condition)

        wait("document.querySelector('#transport-workspace[data-connection=live]')")

        def assert_header_fits(width):
            metrics = json.loads(evaluate("""JSON.stringify((() => {
              const workspace=document.getElementById('transport-workspace');
              const head=workspace.querySelector('.tw-head');
              const title=workspace.querySelector('.tw-title');
              const titleLine=workspace.querySelector('.tw-title-line').getBoundingClientRect();
              const people=workspace.querySelector('.tw-people');
              const peopleBounds=people.getBoundingClientRect();
              const actionsBounds=workspace.querySelector('.tw-head-actions').getBoundingClientRect();
              const headBounds=head.getBoundingClientRect();
              const visible=element => getComputedStyle(element).display !== 'none';
              const participants=[...people.children].map(item => ({
                role:item.dataset.role,text:item.textContent.trim(),visible:visible(item),
                fits:item.scrollWidth<=item.clientWidth,
                left:item.getBoundingClientRect().left,right:item.getBoundingClientRect().right
              }));
              const actions=[...workspace.querySelector('.tw-head-actions').children]
                .filter(visible).map(item=>({left:item.getBoundingClientRect().left,
                  right:item.getBoundingClientRect().right}));
              const separated=titleLine.bottom<=peopleBounds.top || peopleBounds.bottom<=titleLine.top
                || titleLine.right<=peopleBounds.left || peopleBounds.right<=titleLine.left;
              return {title:title.textContent.trim(),titleFits:title.scrollWidth<=title.clientWidth,
                view:workspace.dataset.view,width:innerWidth,headHeight:headBounds.height,
                headerBlocksSeparated:separated,actionsBounds:{left:actionsBounds.left,right:actionsBounds.right},
                participants,actions};
            })())"""))
            self.assertEqual(metrics["width"], width, metrics)
            self.assertTrue(metrics["title"], metrics)
            self.assertTrue(metrics["titleFits"], metrics)
            self.assertLessEqual(metrics["headHeight"], 116, metrics)
            self.assertTrue(metrics["headerBlocksSeparated"], metrics)
            self.assertLessEqual(metrics["actionsBounds"]["left"], 12, metrics)
            self.assertGreaterEqual(metrics["actionsBounds"]["right"], width - 12, metrics)
            self.assertTrue({"system", "crew", "hospital"}.issubset(
                {item["role"] for item in metrics["participants"]}), metrics)
            for item in metrics["participants"]:
                self.assertTrue(item["visible"], metrics)
                self.assertTrue(item["fits"], metrics)
                self.assertGreaterEqual(item["left"], 0, metrics)
                self.assertLessEqual(item["right"], width, metrics)
            for item in metrics["actions"]:
                self.assertGreaterEqual(item["left"], 0, metrics)
                self.assertLessEqual(item["right"], width, metrics)
            return metrics

        for width in (360, 409, 412):
            with self.subTest(width=width, view="chat"):
                mobile.call("Emulation.setDeviceMetricsOverride", width=width, height=844,
                            deviceScaleFactor=1, mobile=True)
                wait(f"innerWidth === {width}")
                assert_header_fits(width)
                image = mobile.call("Page.captureScreenshot", format="png")
                (self.run_dir / f"mobile-header-chat-{width}.png").write_bytes(
                    base64.b64decode(image["data"]))
            with self.subTest(width=width, view="map"):
                evaluate("document.getElementById('transport-toggle').click()")
                wait("document.getElementById('transport-workspace').dataset.view === 'gps'")
                assert_header_fits(width)
                image = mobile.call("Page.captureScreenshot", format="png")
                (self.run_dir / f"mobile-header-map-{width}.png").write_bytes(
                    base64.b64decode(image["data"]))
                evaluate("document.getElementById('transport-toggle').click()")
                wait("document.getElementById('transport-workspace').dataset.view === 'chat'")

    def test_inquiry_statuses_stay_in_tray_and_history_opens_separately(self):
        import base64
        from chrome_driver import Chrome

        pid = self.create_case()
        inquiries = self.call("POST", f"/api/patients/{pid}/inquiries",
                              {"hospital_ids": ["H01", "H02", "H03"]})["inquiries"]
        mobile = Chrome(Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
                        self.run_dir / ("inquiry-log-profile-" + uuid4().hex))
        self.addCleanup(mobile.close)
        mobile.call("Page.enable")
        mobile.call("Emulation.setDeviceMetricsOverride", width=412, height=844,
                    deviceScaleFactor=1, mobile=True)
        mobile.call("Page.navigate", url=f"{self.api_url}/transport/?pid={pid}")

        def evaluate(source):
            result = mobile.call("Runtime.evaluate", expression=source, awaitPromise=True, returnByValue=True)
            self.assertNotIn("exceptionDetails", result, str(result))
            return result.get("result", {}).get("value")

        def wait(condition):
            deadline = time.monotonic() + 16
            while time.monotonic() < deadline:
                try:
                    if evaluate("Boolean(" + condition + ")"):
                        return
                except RuntimeError as failure:
                    if "context" not in str(failure).lower():
                        raise
                time.sleep(.08)
            self.fail("Timed out: " + condition)

        wait("document.querySelector('#transport-workspace[data-connection=live]')")
        wait("document.querySelectorAll('#transport-inquiries .transport-inquiry').length === 3")
        self.assertEqual(evaluate("document.getElementById('transport-tts-toggle').getAttribute('aria-pressed')"),
                         "true")
        for inquiry, action, handler in zip(inquiries, ("accept", "decline"), ("가상 수용", "가상 거절")):
            response = {"action": action, "handled_by": handler}
            if action == "decline":
                response["reason"] = "기타"
            self.call("POST", f"/api/inquiries/{inquiry['id']}/respond", response)
        history_items = self.call("GET", f"/api/patients/{pid}/conversation")["items"]
        expected_log_count = sum(item.get("kind") == "system" and item.get("sender_role") == "system"
                                 and bool(item.get("type")) for item in history_items)
        wait("""(() => {
          const states=[...document.querySelectorAll('#transport-inquiries .transport-inquiry')]
            .map(row=>row.dataset.state);
          return states.includes('ACCEPTED') && states.includes('DECLINED') && states.includes('PENDING');
        })()""")
        wait("document.querySelector('[data-transport-confirm]') && !document.querySelector('[data-transport-confirm]').disabled")
        rows = json.loads(evaluate("""JSON.stringify([...document.querySelectorAll(
          '#transport-inquiries .transport-inquiry')].map(row=>({
            name:row.querySelector('.tw-inquiry-copy strong').textContent.trim(),
            state:row.dataset.state,confirm:!!row.querySelector('[data-transport-confirm]'),
            enabled:!!row.querySelector('[data-transport-confirm]:not(:disabled)')})))"""))
        self.assertEqual(len(rows), 3, rows)
        self.assertEqual({row["state"] for row in rows}, {"ACCEPTED", "DECLINED", "PENDING"}, rows)
        self.assertEqual(sum(row["confirm"] for row in rows), 1, rows)
        self.assertTrue(any(row["enabled"] for row in rows), rows)
        wait("""[...document.querySelectorAll('#transport-inquiries .transport-inquiry')]
          .every(row=>row.getAnimations().every(animation=>animation.playState==='finished'))""")
        chat_text = evaluate("document.getElementById('transport-messages').innerText")
        for inquiry in inquiries:
            self.assertNotIn(inquiry["hospital_name"], chat_text)
        history_button = json.loads(evaluate("""JSON.stringify((() => {
          const button=document.getElementById('transport-progress-log-toggle');
          const rect=button.getBoundingClientRect();
          return {inHeader:button.parentElement.classList.contains('tw-head-actions'),
            width:rect.width,height:rect.height,label:button.getAttribute('aria-label'),
            title:button.title,count:document.getElementById('transport-progress-log-count').textContent};
        })())"""))
        self.assertTrue(history_button["inHeader"], history_button)
        self.assertGreaterEqual(history_button["width"], 44, history_button)
        self.assertGreaterEqual(history_button["height"], 44, history_button)
        self.assertEqual(history_button["label"], "진행 기록", history_button)
        self.assertEqual(history_button["title"], "진행 기록", history_button)
        self.assertEqual(history_button["count"], str(expected_log_count), history_button)

        def assert_horizontal_tray():
            layout = json.loads(evaluate("""JSON.stringify((() => {
              const rail=document.getElementById('transport-inquiries');
              const row=rail.querySelector('.transport-inquiry');
              const control=row.querySelector('[data-transport-confirm]');
              const action=control.getBoundingClientRect();
              const card=row.getBoundingClientRect(), viewport=rail.getBoundingClientRect();
              return {direction:getComputedStyle(rail).flexDirection,scrolls:rail.scrollWidth>rail.clientWidth,
                actionFits:action.right<=card.right+1 && action.left>=card.left-1,
                actionVisible:action.right<=viewport.right+1 && action.left>=viewport.left-1,
                width:action.width,height:action.height,label:control.getAttribute('aria-label'),
                button:control.tagName};
            })())"""))
            self.assertEqual(layout["direction"], "row", layout)
            self.assertTrue(layout["scrolls"], layout)
            self.assertTrue(layout["actionFits"], layout)
            self.assertTrue(layout["actionVisible"], layout)
            self.assertEqual((layout["width"], layout["height"]), (44, 44), layout)
            self.assertEqual(layout["button"], "BUTTON", layout)
            self.assertIn("목적지 확정", layout["label"], layout)

        assert_horizontal_tray()

        output = self.run_dir
        (output / "inquiry-tray-412.png").write_bytes(
            base64.b64decode(mobile.call("Page.captureScreenshot", format="png")["data"]))
        mobile.call("Emulation.setDeviceMetricsOverride", width=360, height=844,
                    deviceScaleFactor=1, mobile=True)
        wait("innerWidth === 360")
        self.assertTrue(evaluate("document.documentElement.scrollWidth<=innerWidth"))
        assert_horizontal_tray()
        (output / "inquiry-tray-360.png").write_bytes(
            base64.b64decode(mobile.call("Page.captureScreenshot", format="png")["data"]))
        evaluate("document.getElementById('transport-progress-log-toggle').click()")
        wait("document.getElementById('transport-progress-log').open")
        wait("""document.getElementById('transport-progress-log').getAnimations()
          .every(animation=>animation.playState==='finished')""")
        self.assertTrue(evaluate("document.documentElement.scrollWidth<=innerWidth"))
        (output / "inquiry-log-360.png").write_bytes(
            base64.b64decode(mobile.call("Page.captureScreenshot", format="png")["data"]))
        evaluate("document.getElementById('transport-progress-log-close').click()")
        wait("!document.getElementById('transport-progress-log').open")
        evaluate("""window.__originalFetch=window.fetch;window.__confirmCalls=0;
          window.fetch=(url,options)=>{
            if(String(url).endsWith('/confirm')){
              window.__confirmCalls++;
              if(window.__confirmCalls===1)
                return Promise.resolve(Response.json({error:'SYNTHETIC_FAILURE',message:'가상 실패'}, {status:503}));
            }
            return window.__originalFetch(url,options);
          };
          document.addEventListener('quick119:route-select',event=>event.stopImmediatePropagation(),true);""")
        evaluate("document.querySelector('[data-transport-confirm]').click()")
        wait("!document.getElementById('transport-error').hidden")
        self.assertIn("가상 실패", evaluate("document.getElementById('transport-error').textContent"))
        self.assertIsNone(self.call("GET", f"/api/patients/{pid}/conversation")["active_hospital_session"])
        self.assertTrue(evaluate("!document.querySelector('#transport-destination .tw-confirm-control--checked')"))
        wait("document.querySelector('[data-transport-confirm]:not(:disabled)')")

        evaluate("""const control=document.querySelector('[data-transport-confirm]');
          control.click();control.click();""")
        wait("""document.querySelector('#transport-workspace[data-stage=joined]')
          && window.__confirmCalls===2""")
        room = self.call("GET", f"/api/patients/{pid}/conversation")
        self.assertEqual(room["active_hospital_session"]["hospital_id"], inquiries[0]["hospital_id"])
        evaluate("document.getElementById('transport-toggle').click()")
        wait("document.getElementById('transport-workspace').dataset.view==='gps'")
        wait("document.querySelector('#transport-destination .tw-confirm-control--checked')")
        checked = evaluate("document.querySelector('#transport-destination .tw-confirm-control--checked').getAttribute('aria-label')")
        self.assertIn("목적지 확정됨", checked)
        self.assertEqual(evaluate("document.querySelector('#transport-destination .tw-confirm-control--checked').getBoundingClientRect().width"), 44)

        mobile.call("Emulation.setDeviceMetricsOverride", width=360, height=844,
                    deviceScaleFactor=1, mobile=True)
        wait("innerWidth===360")
        self.assertTrue(evaluate("document.documentElement.scrollWidth<=innerWidth"))
        self.assertTrue(evaluate("document.querySelector('#transport-destination .tw-confirm-control--checked').getBoundingClientRect().right<=innerWidth"))
        (output / "after-confirm-360.png").write_bytes(
            base64.b64decode(mobile.call("Page.captureScreenshot", format="png")["data"]))

        mobile.call("Emulation.setDeviceMetricsOverride", width=412, height=844,
                    deviceScaleFactor=1, mobile=True)
        wait("innerWidth === 412")
        self.assertTrue(evaluate("document.documentElement.scrollWidth<=innerWidth"))
        self.assertTrue(evaluate("document.querySelector('#transport-destination .tw-confirm-control--checked').getBoundingClientRect().right<=innerWidth"))
        (output / "after-confirm-412.png").write_bytes(
            base64.b64decode(mobile.call("Page.captureScreenshot", format="png")["data"]))
        evaluate("document.getElementById('transport-progress-log-toggle').click()")
        wait("document.getElementById('transport-progress-log').open")
        wait("""document.getElementById('transport-progress-log').getAnimations()
          .every(animation=>animation.playState==='finished')""")
        log_text = evaluate("document.getElementById('transport-progress-log-list').innerText")
        final_history_items = self.call("GET", f"/api/patients/{pid}/conversation")["items"]
        final_log_count = sum(item.get("kind") == "system" and item.get("sender_role") == "system"
                              and bool(item.get("type")) for item in final_history_items)
        self.assertEqual(evaluate("document.querySelectorAll('#transport-progress-log-list li').length"),
                         final_log_count)
        for inquiry in inquiries:
            self.assertIn(inquiry["hospital_name"], log_text)
        self.assertEqual(evaluate("document.getElementById('transport-tts-toggle').getAttribute('aria-pressed')"),
                         "true")
        (output / "inquiry-log-412.png").write_bytes(
            base64.b64decode(mobile.call("Page.captureScreenshot", format="png")["data"]))
        evaluate("document.getElementById('transport-progress-log-close').click()")
        wait("!document.getElementById('transport-progress-log').open")

    def test_patient_list_explains_legacy_server_metadata_mismatch(self):
        from chrome_driver import Chrome

        mobile = Chrome(Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
                        self.run_dir / ("legacy-list-profile-" + uuid4().hex))
        self.addCleanup(mobile.close)
        mobile.call("Page.enable")
        mobile.call("Emulation.setDeviceMetricsOverride", width=390, height=844,
                    deviceScaleFactor=1, mobile=True)
        mobile.call("Page.addScriptToEvaluateOnNewDocument", source="""
          const nativeFetch=window.fetch.bind(window);
          window.fetch=(input,init)=>new URL(input,location.href).pathname==='/api/transport/patients'
            ?Promise.resolve(Response.json({patients:[{id:'Q119-TEST-LEGACY'}]}))
            :nativeFetch(input,init);
        """)
        mobile.call("Page.navigate", url=self.api_url + "/patients")

        def evaluate(source):
            result = mobile.call("Runtime.evaluate", expression=source, awaitPromise=True, returnByValue=True)
            self.assertNotIn("exceptionDetails", result, str(result))
            return result.get("result", {}).get("value")

        deadline = time.monotonic() + 12
        expected = "최신 공통 서버를 재시작한 뒤 다시 시도하세요"
        while time.monotonic() < deadline:
            if expected in (evaluate("document.getElementById('transport-patients-status').textContent") or ""):
                break
            time.sleep(.08)
        else:
            self.fail("Legacy list response did not produce the version-mismatch guidance.")
        self.assertIsNone(evaluate("document.querySelector('.transport-patient-case')"))

    def test_voice_commands_share_the_chat_draft_and_require_touch(self):
        from chrome_driver import Chrome
        mobile = Chrome(Path('C:/Program Files/Google/Chrome/Application/chrome.exe'), self.run_dir / 'command-profile')
        self.addCleanup(mobile.close)
        mobile.call('Page.enable')
        mobile.call('Emulation.setDeviceMetricsOverride', width=390, height=844, deviceScaleFactor=1, mobile=True)
        mobile.call('Page.addScriptToEvaluateOnNewDocument', source="""
          window.SpeechRecognition = class {
            start(){window.mic=this; this.onstart();}
            stop(){this.onend();}
            abort(){}
            emit(text,final,alternatives=[]){
              const row=[text,...alternatives].map(transcript=>({transcript}));
              row.isFinal=final; this.onresult({resultIndex:0,results:[row]});
            }
          };
        """)

        def js(code):
            result = mobile.call('Runtime.evaluate', expression=code, awaitPromise=True, returnByValue=True)
            self.assertNotIn('exceptionDetails', result)
            return result['result'].get('value')

        def wait(condition):
            end = time.monotonic() + 12
            while time.monotonic() < end:
                if js('Boolean(' + condition + ')'):
                    return
                time.sleep(.08)
            self.fail(condition)

        def say(text, alternatives=()):
            js("document.querySelector('#transport-voice-start').click()")
            js(f"mic.emit({json.dumps(text)},true,{json.dumps(alternatives)}); mic.stop()")
            wait("!document.querySelector('#transport-message').value && !document.querySelector('#transport-send').disabled")

        ready = "document.querySelector('#transport-voice-start:enabled') && new URLSearchParams(location.search).has('pid')"
        bubble_texts = "Array.from(document.querySelectorAll('#transport-messages .transport-bubble'), el => el.innerText)"
        welcome = '환자 상태를 알려주세요.'
        report = '10살 남자아이'
        receipt = '나이: 10세\n성별: 남성'

        # Exercise separate cases so speech cannot reuse the age and sex from typed input.
        mobile.call('Page.navigate', url=self.api_url + '/transport/?new=1')
        wait(ready)
        self.assertEqual(js(bubble_texts), [welcome])
        typed_pid = js("new URLSearchParams(location.search).get('pid')")
        js("document.querySelector('#transport-message').value=" + json.dumps(report) + ";"
           "document.querySelector('#transport-message').dispatchEvent(new Event('input'));"
           "document.querySelector('#transport-message-form').requestSubmit()")
        wait(bubble_texts + '.includes(' + json.dumps(receipt) + ')')
        self.assertEqual(js(bubble_texts), [welcome, report, receipt])
        js('window.beforeReceiptReload = true')
        mobile.call('Page.reload')
        wait('!window.beforeReceiptReload && ' + ready)
        self.assertEqual(js(bubble_texts), [welcome, report, receipt])

        js('window.beforeNextCase = true')
        mobile.call('Page.navigate', url=self.api_url + '/transport/?new=1')
        wait('!window.beforeNextCase && ' + ready)
        pid = js("new URLSearchParams(location.search).get('pid')")
        self.assertNotEqual(pid, typed_pid)
        route = '/api/patients/' + pid
        def draft():
            return self.call('GET', route + '/intake-draft')['draft']

        js("document.querySelector('#transport-voice-start').click(); mic.emit('전송해줘',false); mic.stop()")
        self.assertIsNone(draft())
        say(report)
        wait(bubble_texts + '.includes(' + json.dumps(receipt) + ')')
        self.assertEqual(js(bubble_texts), [welcome, report, receipt])
        self.assertEqual(draft()['fields']['age'], 10)
        self.assertEqual(draft()['fields']['sex'], 'male')
        say('나이 21세로 수정해줘')
        self.assertEqual(draft()['fields']['age'], 21)
        say('전송해줘', ['전송하지 마'])
        self.assertFalse(js("document.querySelector('#transport-command-panel').open"))
        say('전송해줘')
        wait("document.querySelector('#transport-command-panel').open")
        self.assertGreaterEqual(js("document.querySelector('#transport-command-confirm').getBoundingClientRect().height"), 48)
        self.assertIsNone(self.call('GET', route)['age'])
        self.assertEqual(js("document.querySelectorAll('[data-candidate-id]').length"), 0)
        js("document.querySelector('#transport-command-confirm').click()")
        wait("!document.querySelector('#transport-gps').hidden && document.querySelectorAll('[data-candidate-id]').length === 3")
        self.assertEqual(self.call('GET', route)['age'], 21)
        inquiries = self.call('GET', route)['inquiries']
        self.assertEqual(len(inquiries), 3)
        self.assertTrue(all(q['state'] == 'PENDING' for q in inquiries))
        self.assertIsNone(js("document.querySelector('[data-transport-inquire]')"))

    def test_typing_mode_keeps_the_chat_visible_on_a_short_phone(self):
        from chrome_driver import Chrome
        mobile = Chrome(Path('C:/Program Files/Google/Chrome/Application/chrome.exe'), self.run_dir / ('typing-profile-' + uuid4().hex))
        self.addCleanup(mobile.close)
        mobile.call('Page.enable')

        def screen(width, height):
            # Model the layout viewport resizing in Android's interactive-widget=resizes-content mode.
            mobile.call('Emulation.setDeviceMetricsOverride', width=width, height=height, deviceScaleFactor=1, mobile=True)

        def js(code):
            result = mobile.call('Runtime.evaluate', expression=code, awaitPromise=True, returnByValue=True)
            self.assertNotIn('exceptionDetails', result)
            return result['result'].get('value')

        def wait(condition):
            end = time.monotonic() + 12
            while time.monotonic() < end:
                if js('Boolean(' + condition + ')'):
                    return
                time.sleep(.08)
            self.fail(condition)

        home_link = "document.querySelector('.tw-home-link') && getComputedStyle(document.querySelector('.tw-home-link')).display !== 'none'"
        typing = "document.getElementById('transport-workspace').dataset.kb === 'open'"
        ready = "document.querySelector('#transport-workspace[data-connection=live] #transport-message:enabled') && new URLSearchParams(location.search).has('pid')"
        for width, full, reduced, partial, min_log in ((360, 740, 450, 620, 200), (844, 390, 220, 280, 30)):
            with self.subTest(width=width, full=full):
                screen(width, full)
                mobile.call('Page.navigate', url=self.api_url + '/transport/?new=1')
                wait(ready)
                self.assertTrue(js(home_link))
                screen(width, reduced)
                wait(f"visualViewport.height === {reduced}")
                self.assertFalse(js(typing))
                screen(width, full)
                wait(f"visualViewport.height === {full}")
                js("document.getElementById('transport-message').focus()")
                screen(width, reduced)
                wait(typing)
                self.assertTrue(js(home_link))
                self.assertGreater(js("document.getElementById('transport-messages').getBoundingClientRect().height"), min_log)
                for height in (reduced, partial):
                    screen(width, height)
                    # Partial dismissal stays in typing mode, but the shell must follow the new visible height.
                    wait(f"Math.abs(document.querySelector('.app-shell--workspace').getBoundingClientRect().height - {height}) < 2")
                    self.assertTrue(js(typing))
                    for selector in ('#transport-send', '#transport-message'):
                        top, bottom = js(f"((r) => [r.top, r.bottom])(document.querySelector('{selector}').getBoundingClientRect())")
                        self.assertGreaterEqual(top, -1, selector)
                        self.assertLessEqual(bottom, height + 1, selector)
                screen(width, full)
                wait("!(" + typing + ")")
                self.assertTrue(js(home_link))

        # Two acceptance actions and a failed poll compete for space above the active landscape composer.
        pid = js("new URLSearchParams(location.search).get('pid')")
        replies = self.call('POST', f'/api/patients/{pid}/inquiries', {'hospital_ids': ['H01', 'H02']})
        for inquiry in replies['inquiries']:
            self.call('POST', f"/api/inquiries/{inquiry['id']}/respond", {'action': 'accept', 'handled_by': '가상 검토자'})
        wait("document.querySelectorAll('[data-transport-confirm]').length === 2")
        js("document.getElementById('transport-message').focus()")
        screen(844, 220)
        wait(typing)
        js("""const normalFetch = window.fetch;
            window.fetch = (url, options) => String(url).includes('/conversation?')
              ? Promise.resolve(Response.json({detail:'가상 조회 오류'}, {status:503})) : normalFetch(url, options);
        """)
        wait("!document.getElementById('transport-error').hidden && document.getElementById('transport-workspace').dataset.connection === 'pending'")
        self.assertGreater(js("document.getElementById('transport-messages').clientHeight"), 0)
        for selector in ('#transport-message', '#transport-send'):
            top, bottom = js(f"((r) => [r.top, r.bottom])(document.querySelector('{selector}').getBoundingClientRect())")
            self.assertGreaterEqual(top, -1, selector)
            self.assertLessEqual(bottom, 221, selector)
        tray_state = js("""(() => {
            const tray = document.querySelector('.tw-updates');
            tray.scrollTop = tray.scrollHeight;
            const clip = tray.getBoundingClientRect();
            const buttons = [...tray.querySelectorAll('[data-transport-confirm]')];
            const visible = buttons.some(button => {
              const r = button.getBoundingClientRect();
              return r.height > 0 && r.top >= clip.top - 1 && r.bottom <= clip.bottom + 1;
            });
            return JSON.stringify({visible, display: getComputedStyle(tray).display, clip: {
              top: clip.top, bottom: clip.bottom, height: clip.height, scrollHeight: tray.scrollHeight,
              clientHeight: tray.clientHeight}, buttons: buttons.map(button => {
                const r = button.getBoundingClientRect();
                return {top: r.top, bottom: r.bottom, height: r.height, text: button.textContent};
              }), stage: document.getElementById('transport-workspace').dataset.stage,
              connection: document.getElementById('transport-workspace').dataset.connection});
        })()""")
        self.assertTrue(json.loads(tray_state)['visible'], tray_state)

    def test_hospital_panel_answers_inquiries_and_chats_in_a_narrow_window(self):
        from chrome_driver import Chrome
        staff = Chrome(Path('C:/Program Files/Google/Chrome/Application/chrome.exe'), self.run_dir / ('panel-profile-' + uuid4().hex))
        self.addCleanup(staff.close)
        staff.call('Page.enable')
        # The width of a browser side window docked beside the hospital's own system.
        staff.call('Emulation.setDeviceMetricsOverride', width=388, height=760, deviceScaleFactor=1, mobile=False)

        def js(code):
            result = staff.call('Runtime.evaluate', expression=code, awaitPromise=True, returnByValue=True)
            self.assertNotIn('exceptionDetails', result)
            return result['result'].get('value')

        def wait(condition):
            end = time.monotonic() + 15
            while time.monotonic() < end:
                if js('Boolean(' + condition + ')'):
                    return
                time.sleep(.1)
            self.fail(condition)

        def click(selector):
            wait(f"document.querySelector({json.dumps(selector)}) && !document.querySelector({json.dumps(selector)}).disabled")
            js(f"document.querySelector({json.dumps(selector)}).click()")

        def say(text):
            wait("!document.getElementById('composer').hidden && !document.getElementById('message').disabled")
            js(f"document.getElementById('message').value = {json.dumps(text)}; document.getElementById('composer').requestSubmit()")

        def inquiry(pid, iid):
            return next(q for q in self.call('GET', f'/api/patients/{pid}')['inquiries'] if q['id'] == iid)

        hospital = 'H07'  # A hospital no other test in this class asks, so its lists start empty.
        pid = self.create_case()
        path = f'/api/patients/{pid}'
        iid = self.call('POST', path + '/inquiries', {'hospital_ids': [hospital]})['inquiries'][0]['id']
        staff.call('Page.navigate', url=self.api_url + f'/hospital/panel.html?hid={hospital}')
        thread = f'[data-thread="p:{pid}"]'
        wait(f"document.querySelector('{thread}') && document.querySelector('[data-action=accept]')")
        # A narrow window opens on the conversation list, like a messenger; a row opens its chat and Back returns.
        self.assertEqual(js("document.getElementById('panel').dataset.view"), 'list')
        self.assertEqual(js("document.querySelector('[data-action=accept]').getBoundingClientRect().height"), 0)
        self.assertEqual(js(f"document.querySelector('{thread} .qp-sev').textContent"), '미확인')
        self.assertEqual(js(f"document.querySelector('{thread} .qp-unread').textContent"), '1')
        click(thread)
        wait("document.getElementById('panel').dataset.view === 'chat'")
        js("document.getElementById('back').click()")
        self.assertGreater(js(f"document.querySelector('{thread}').getBoundingClientRect().height"), 60)
        click(thread)
        self.assertEqual(js("document.getElementById('handler').value"), '시연 담당자')
        self.assertIn('새 문의', js(f"document.querySelector('{thread}').textContent"))
        self.assertEqual(js("document.title"), '(1) 퀵119 수용 문의')
        self.assertIn('가상 테스트', js("document.querySelector('.qp-card').textContent"))
        # Everything needed to answer fits the narrow window: no sideways scroll, reply buttons and the warning in view.
        self.assertLessEqual(js("document.documentElement.scrollWidth"), 388)
        for selector in ('[data-action=accept]', '[data-action=checking]', '[data-action=decline]', '.qp-synthetic'):
            box = js(f"(() => {{ const r = document.querySelector('{selector}').getBoundingClientRect(); return [r.bottom, r.height]; }})()")
            self.assertLessEqual(box[0], 760, selector)
            if selector.startswith('['):
                self.assertGreaterEqual(box[1], 44, selector)

        say('항응고제를 복용 중인가요?')
        wait("document.querySelector('.qp-msg[data-kind=me]')")
        self.assertEqual(inquiry(pid, iid)['question'], '항응고제를 복용 중인가요?')
        self.call('POST', path + f'/inquiries/{iid}/answer', {'answer': '복용하지 않습니다.'}, direct=True)
        wait("document.querySelector('.qp-msg[data-kind=crew]')?.textContent.includes('복용하지 않습니다.')")
        self.assertIn('답변 도착', js(f"document.querySelector('{thread}').textContent"))

        click('[data-action=accept]')
        wait("document.querySelector('.qp-done')?.textContent.includes('목적지 확정을 기다리는 중')")
        self.assertEqual(inquiry(pid, iid)['state'], 'ACCEPTED')
        self.assertEqual(inquiry(pid, iid)['handled_by'], '시연 담당자')
        self.assertEqual(js("document.title"), '퀵119 수용 문의')

        self.call('POST', path + '/confirm', {'inquiry_id': iid})
        wait("document.querySelector('[data-action=arrive]') && !document.getElementById('composer').hidden")
        sid = self.call('GET', path + '/conversation')['active_hospital_session']['id']
        self.call('POST', path + '/messages', {'body': '가상 대원: 5분 뒤 도착합니다.', 'client_message_id': uuid4().hex, 'hospital_session_id': sid})
        wait("[...document.querySelectorAll('.qp-msg[data-kind=crew]')].some(n => n.textContent.includes('5분 뒤 도착합니다.'))")
        say('응급실 2번 베드로 오세요.')
        wait("[...document.querySelectorAll('.qp-msg[data-kind=me]')].some(n => n.textContent.includes('2번 베드로'))")
        room = self.call('GET', path + '/conversation')
        self.assertIn('응급실 2번 베드로 오세요.', [item.get('body') for item in room['items']])
        self.assertEqual(js("document.getElementById('message').value"), '')

        self.call('POST', path + '/depart', {})
        click('[data-action=arrive]')
        wait("document.querySelector('.qp-done')?.textContent.includes('환자 도착을 확인했습니다')")
        self.assertEqual(self.call('GET', path)['state'], 'ARRIVED')

        # A second patient is declined with a reason; the send button stays off until one is picked.
        other = self.create_case()
        other_iid = self.call('POST', f'/api/patients/{other}/inquiries', {'hospital_ids': [hospital]})['inquiries'][0]['id']
        click(f'[data-thread="p:{other}"]')
        click('[data-action=decline]')
        wait("document.getElementById('declineSend')")
        self.assertTrue(js("document.getElementById('declineSend').disabled"))
        js("[...document.querySelectorAll('.qp-reasons button')].find(b => b.textContent === '병상').click()")
        click('#declineSend')
        wait("document.querySelector('.qp-done')?.textContent.includes('수용 불가로 회신했습니다')")
        declined = inquiry(other, other_iid)
        self.assertEqual((declined['state'], declined['reason']), ('DECLINED', '병상'))
        self.assertLessEqual(js("document.documentElement.scrollWidth"), 388)

        # Finished conversations (arrived, declined) fold into one line and come back on a second press.
        js("document.getElementById('back').click()")
        wait("document.getElementById('finishedToggle')")
        finished = "document.querySelectorAll('.qp-row[data-finished=true]').length"
        self.assertEqual(js(finished), 2)
        self.assertEqual(js("document.getElementById('finishedToggle').textContent"), '끝난 대화 2')
        self.assertGreaterEqual(js("document.getElementById('finishedToggle').getBoundingClientRect().height"), 44)
        js("document.getElementById('finishedToggle').click()")
        self.assertEqual(js(finished), 0)
        self.assertEqual(js("document.getElementById('finishedToggle').getAttribute('aria-expanded')"), 'false')
        self.assertEqual(js("document.activeElement.id"), 'finishedToggle')
        time.sleep(3.5)  # a refresh keeps it folded
        self.assertEqual(js(finished), 0)
        js("document.getElementById('finishedToggle').click()")
        self.assertEqual(js(finished), 2)

    def test_emr_demo_embeds_the_panel_and_shows_its_pending_count(self):
        from chrome_driver import Chrome
        desk = Chrome(Path('C:/Program Files/Google/Chrome/Application/chrome.exe'), self.run_dir / ('emr-profile-' + uuid4().hex))
        self.addCleanup(desk.close)
        desk.call('Page.enable')
        desk.call('Emulation.setDeviceMetricsOverride', width=1280, height=800, deviceScaleFactor=1, mobile=False)

        def js(code):
            result = desk.call('Runtime.evaluate', expression=code, awaitPromise=True, returnByValue=True)
            self.assertNotIn('exceptionDetails', result)
            return result['result'].get('value')

        def wait(condition):
            end = time.monotonic() + 15
            while time.monotonic() < end:
                if js('Boolean(' + condition + ')'):
                    return
                time.sleep(.1)
            self.fail(condition)

        hospital = 'H09'  # Not asked by any other test in this class.
        panel = "document.getElementById('panelFrame')?.contentDocument"
        desk.call('Page.navigate', url=self.api_url + f'/hospital/emr-demo.html?hid={hospital}')
        wait(f"{panel}?.getElementById('panel')?.dataset.conn === 'live'")
        self.assertEqual(js("document.getElementById('panelFrame').getBoundingClientRect().width"), 619)
        # Wide enough for the messenger layout: the list and the chat sit side by side inside the panel.
        self.assertGreater(js(f"{panel}.getElementById('threads').getBoundingClientRect().width"), 200)
        self.assertGreater(js(f"{panel}.querySelector('.qp-chat').getBoundingClientRect().width"), 350)
        # The list row already names the open conversation, so the chat does not repeat it in a header line.
        self.assertLessEqual(js(f"{panel}.querySelector('.qp-chat-head').getBoundingClientRect().height"), 1)
        self.assertGreater(js("document.querySelectorAll('#rows tr').length"), 10)
        self.assertLessEqual(js("document.documentElement.scrollWidth"), 1280)
        self.assertTrue(js("document.getElementById('dock').hidden && document.getElementById('toast').hidden"))

        pid = self.create_case()
        self.call('POST', f'/api/patients/{pid}/inquiries', {'hospital_ids': [hospital]})
        wait("document.getElementById('badge').textContent === '1'")
        self.assertTrue(js("document.getElementById('toast').hidden"), 'no toast while the panel is open')
        js("document.getElementById('collapse').click()")
        wait("!document.getElementById('toast').hidden && !document.getElementById('dock').hidden")
        self.assertEqual(js("getComputedStyle(document.getElementById('side')).display"), 'none')
        self.assertIn('1건', js("document.getElementById('toastText').textContent"))
        # A message from anywhere but the embedded panel is ignored.
        js("window.postMessage({source: 'quick119-hospital-panel', pending: 9}, '*')")
        time.sleep(.3)
        self.assertEqual(js("document.getElementById('badge').textContent"), '1')
        js("document.getElementById('toastOpen').click()")
        wait("getComputedStyle(document.getElementById('side')).display !== 'none' && document.getElementById('toast').hidden")
        wait(f"{panel}.querySelector('[data-action=accept]')")

        # Data flows both ways. Panel -> EMR: an accepted patient shows up as expected, then lands in a bed on arrival.
        # EMR -> panel: assigning a bed makes the panel tell the crew which bed to come to.
        path = f'/api/patients/{pid}'
        iid = self.call('GET', path)['inquiries'][0]['id']
        js(f"{panel}.querySelector('[data-action=accept]').click()")
        expected = f"document.querySelector('[data-expected=\"{pid}\"]')"
        wait(f"{expected}?.textContent.includes('구급대 확정 대기')")
        self.assertEqual(js("document.getElementById('tabExpected').textContent"), '도착 예정 1')
        self.call('POST', path + '/confirm', {'inquiry_id': iid})
        assign = f"document.querySelector('[data-assign=\"{pid}\"]')"
        wait(f"{assign} && !{assign}.disabled")
        free = js("document.querySelectorAll('#rows tr[data-bed=clean]').length")
        js(f"{expected}.querySelector('select').value = 'C-04'; {expected}.querySelector('select').dispatchEvent(new Event('change')); {assign}.click()")
        wait("document.querySelector('#rows tr[data-bed-id=\"C-04\"]').dataset.bed === 'reserved'")
        self.assertEqual(js("document.querySelectorAll('#rows tr[data-bed=clean]').length"), free - 1)
        deadline = time.monotonic() + 10
        while '응급실 C-04 베드로 오세요.' not in [item.get('body') for item in self.call('GET', path + '/conversation')['items']]:
            self.assertLess(time.monotonic(), deadline, 'the panel did not send the bed message')
            time.sleep(.2)
        wait(f"{expected}?.textContent.includes('C-04 배정')")
        self.call('POST', path + '/depart', {})
        self.call('POST', f'/api/hospitals/{hospital}/patients/{pid}/arrive', {'handled_by': '가상 의료진'})
        wait("document.querySelector('#rows tr[data-bed-id=\"C-04\"]').dataset.fresh === 'true'")
        self.assertIn('가상 테스트', js("document.querySelector('#rows tr[data-bed-id=\"C-04\"]').textContent"))
        self.assertEqual(js("document.getElementById('tabExpected').textContent"), '도착 예정 0')
        self.assertIsNone(js(f"{expected}"))

    def test_real_browser_staff_acceptance_and_chat(self):
        import base64
        from chrome_driver import Chrome
        chrome_path = Path('C:/Program Files/Google/Chrome/Application/chrome.exe')
        mobile = Chrome(chrome_path, self.run_dir / 'mobile-profile')
        self.addCleanup(mobile.close)
        staff = Chrome(chrome_path, self.run_dir / 'staff-profile')
        self.addCleanup(staff.close)

        def evaluate(browser, source):
            result = browser.call('Runtime.evaluate', expression=source, awaitPromise=True, returnByValue=True)
            self.assertNotIn('exceptionDetails', result, str(result))
            return result.get('result', {}).get('value')

        def wait(browser, condition):
            # The home CTA navigates to a new document. Poll from Python so a
            # destroyed old execution context does not invalidate the wait.
            deadline = time.monotonic() + 16
            while time.monotonic() < deadline:
                try:
                    result = browser.call('Runtime.evaluate', expression='Boolean(' + condition + ')', returnByValue=True)
                except RuntimeError as failure:
                    if 'context' not in str(failure).lower():
                        raise
                else:
                    if not result.get('exceptionDetails') and result.get('result', {}).get('value'):
                        return True
                time.sleep(.08)
            self.fail('Timed out: ' + condition)

        guard = """
        window.storageWrites=[];
        const storageSetItem=Storage.prototype.setItem;
        Storage.prototype.setItem=function(key,value){
          window.storageWrites.push([key,value]);
          if(key!=='quick119.activeTransportPatientId')throw Error('Persistent patient data disabled in UI test');
          return storageSetItem.call(this,key,value);
        };
        """
        mobile.call('Page.enable'); staff.call('Page.enable')
        mobile.call('Network.enable'); staff.call('Network.enable')
        for browser in (mobile, staff):
            browser.call('Network.setBlockedURLs', urls=['https://*', 'http://localhost/*'])
        mobile.call('Emulation.setDeviceMetricsOverride', width=390, height=844, deviceScaleFactor=1, mobile=True)
        mobile.call('Page.addScriptToEvaluateOnNewDocument', source=guard + """
        window.gpsWatchCount=0;window.gpsClears=0;
        Object.defineProperty(navigator,'geolocation',{value:{
          getCurrentPosition(ok){ok({coords:{latitude:37.5051,longitude:126.9573,accuracy:10},timestamp:Date.now()});},
          watchPosition(ok){window.gpsWatchCount++;window.mockGPS=ok;return window.gpsWatchCount;},
          clearWatch(){window.gpsClears++;}
        }});
        """)
        staff.call('Page.addScriptToEvaluateOnNewDocument', source=guard)
        mobile.call('Page.navigate', url=self.api_url + '/')
        wait(mobile, "document.querySelector('.emergency-cta')")
        evaluate(mobile, "document.querySelector('.emergency-cta').click()")
        ready = "document.querySelector('#transport-workspace[data-connection=live] #transport-send:enabled') && new URLSearchParams(location.search).has('pid')"
        wait(mobile, ready)
        pid = evaluate(mobile, "new URLSearchParams(location.search).get('pid')")
        self.assertTrue(pid.startswith('Q119-TEST-'))
        first_screen = evaluate(mobile, 'document.body.innerText')
        for removed in ('시스템과 구급대의 현장 접수를 시작했습니다', '가상 접수:', pid,
                        '대화 안내 · 연결 관리', '실제 환자정보 입력 금지', '개발·테스트 환경'):
            self.assertNotIn(removed, first_screen)
        self.assertFalse(evaluate(mobile, "document.getElementById('transport-chat').hidden"))
        self.assertTrue(evaluate(mobile, "document.getElementById('transport-gps').hidden"))
        self.assertEqual(evaluate(mobile, "document.getElementById('transport-participants').children.length"), 2)
        evaluate(mobile, "document.getElementById('transport-message').value='병원 확정 전 가상 환자 상태 보고';document.getElementById('transport-message').dispatchEvent(new Event('input'));document.getElementById('transport-message-form').requestSubmit()")
        wait(mobile, "document.getElementById('transport-message').value==='' && document.getElementById('transport-messages').textContent.includes('병원 확정 전 가상 환자 상태 보고')")
        evaluate(mobile, 'window.beforeReload = true')
        mobile.call('Page.reload')
        wait(mobile, "!window.beforeReload && " + ready)
        self.assertEqual(evaluate(mobile, "new URLSearchParams(location.search).get('pid')"), pid)
        restored = evaluate(mobile, "document.getElementById('transport-messages').innerText")
        self.assertIn('병원 확정 전 가상 환자 상태 보고', restored)
        self.assertNotIn('시스템과 구급대의 현장 접수를 시작했습니다', restored)
        mobile.call('Page.navigate', url=self.api_url + '/transport/')
        wait(mobile, ready)
        self.assertEqual(evaluate(mobile, "new URLSearchParams(location.search).get('pid')"), pid)
        self.assertIn('병원 확정 전 가상 환자 상태 보고',
                      evaluate(mobile, "document.getElementById('transport-messages').innerText"))
        self.call('GET', f'/api/hospitals/H01/patients/{pid}/conversation', status=403)
        self.assertIsNone(evaluate(mobile, "document.querySelector('[data-transport-inquire]')"))
        evaluate(mobile, "document.getElementById('transport-patient-info').click()")
        wait(mobile, "document.getElementById('transport-review-panel').open")
        evaluate(mobile, """
          document.getElementById('transport-review-panel').addEventListener('close',
            () => { window.receivedReviewClose = true; }, {once: true});
          document.getElementById('transport-review-close').click();
          document.getElementById('transport-start-search').click();
        """)
        wait(mobile, "window.receivedReviewClose && document.getElementById('transport-review-panel').open")
        evaluate(mobile, "document.getElementById('transport-review-close').click()")
        wait(mobile, "!document.getElementById('transport-review-panel').open")
        reports = (
            ('20세 남성, 흉통 있음, 의식 명료, 호흡 정상', ['나이: 20세', '성별: 남성', '증상: 흉통', '의식: 명료', '호흡: 정상']),
            ('혈압 120에 80, 맥박 92회, 산소포화도 97퍼센트, 체온 37.2도', ['혈압: 120/80 mmHg', '맥박: 92회/분', '산소포화도: 97%', '체온: 37.2°C']),
            ('호흡수 분당 20회, 혈당 105 밀리그램 퍼 데시리터, 20분 전 발생', ['호흡수: 20회/분', '혈당: 105 mg/dL', '발생 시각:']),
        )
        for report, receipt_lines in reports:
            evaluate(mobile, "document.getElementById('transport-message').value=" + json.dumps(report) + ";"
                     "document.getElementById('transport-message').dispatchEvent(new Event('input'));"
                     "document.getElementById('transport-message-form').requestSubmit()")
            wait(mobile, "Array.from(document.querySelectorAll('#transport-messages .transport-bubble')).some("
                 "node => " + json.dumps(receipt_lines) + ".every(line => node.innerText.includes(line)))")
        path = '/api/patients/' + pid
        self.assertEqual(self.call('GET', path + '/intake-draft')['draft']['followup']['items'], [])
        self.assertIsNone(self.call('GET', path)['age'])
        evaluate(mobile, "document.getElementById('transport-start-search').click()")
        wait(mobile, "document.getElementById('transport-command-panel').open")
        evaluate(mobile, "document.getElementById('transport-command-confirm').click()")
        wait(mobile, "document.querySelector('[data-candidate-id=H01]') && document.getElementById('transport-inquiries').textContent.includes('수용 문의 중') && !document.getElementById('transport-gps').hidden")
        submitted = self.call('GET', path)
        self.assertEqual(submitted['age'], 20)
        self.assertEqual(submitted['extra']['vitals']['glucose'], 105)
        staff_link = evaluate(mobile, "document.querySelector('[data-candidate-id=H01] a').href")
        self.assertEqual(staff_link, self.api_url + '/hospital/?hid=H01&pid=' + pid)
        self.assertIsNone(evaluate(mobile, "document.querySelector('[data-transport-inquire]')"))
        staff.call('Page.navigate', url=staff_link)
        wait(staff, "document.querySelector('[data-act=accept]')")
        evaluate(staff, "document.getElementById('handler').value='가상 의료진';document.getElementById('handler').dispatchEvent(new Event('input'));document.querySelector('[data-act=accept]').click()")
        wait(mobile, "document.querySelector('[data-transport-confirm]')")
        self.assertFalse(evaluate(mobile, "document.getElementById('transport-send').disabled"))
        self.assertEqual(evaluate(mobile, "document.getElementById('transport-participants').children.length"), 2)
        self.call('GET', f'/api/hospitals/H01/patients/{pid}/conversation', status=403)
        evaluate(mobile, "document.querySelector('[data-transport-confirm]').click()")
        wait(mobile, "!document.getElementById('transport-send').disabled && !document.getElementById('car-route-start').disabled")
        self.assertEqual(evaluate(mobile, "document.getElementById('transport-participants').children.length"), 3)
        self.assertIn('목적지가 확정됐습니다', evaluate(mobile, "document.getElementById('transport-messages').textContent"))
        evaluate(mobile, "document.getElementById('car-route-start').click()")
        wait(mobile, "window.gpsWatchCount === 1")
        confirmed = self.call('GET', path)
        self.assertEqual(confirmed['state'], 'CONFIRMED')
        self.call('POST', path + '/depart', {'expected_version': confirmed['version'] + 1}, status=409)
        self.assertEqual(self.call('GET', path)['state'], 'CONFIRMED')
        wait(mobile, "document.querySelector('#transport-depart:enabled')?.checkVisibility()")
        evaluate(mobile, "const depart=document.getElementById('transport-depart'); depart.click(); depart.click()")
        wait(mobile, "document.getElementById('transport-depart').hidden && document.getElementById('transport-messages').textContent.includes('출발했습니다')")
        moving = self.call('GET', path)
        self.assertEqual(moving['state'], 'IN_TRANSIT')
        self.assertGreater(moving['eta_at'], moving['departed_at'])
        self.call('POST', path + '/depart', {'expected_version': confirmed['version']}, status=409)
        self.assertEqual(self.call('GET', path)['departed_at'], moving['departed_at'])
        evaluate(mobile, "document.getElementById('transport-toggle').click();document.getElementById('transport-message').value='가상 대원 실제 HTTP 메시지';document.getElementById('transport-message').dispatchEvent(new Event('input'));document.getElementById('transport-message-form').requestSubmit()")
        wait(mobile, "document.getElementById('transport-message').value==='' && !document.getElementById('transport-send').disabled")
        evaluate(staff, "document.querySelector('[data-tab=incoming]').click()")
        wait(staff, "document.querySelector('[data-patient=" + pid + "]')?.innerText.includes('이송 중')")
        self.assertIn('20세', evaluate(staff, "document.querySelector('[data-patient=" + pid + "]').innerText"))
        # Select by the team's original incoming-card action, without replacing its chat implementation.
        evaluate(staff, "document.querySelector('[data-patient=" + pid + "]').click()")
        wait(staff, "document.getElementById('chatBody') && !document.getElementById('chatSend').disabled && document.getElementById('chatLog').textContent.includes('가상 대원 실제 HTTP 메시지')")
        self.assertTrue(evaluate(staff, "document.getElementById('chatLog').textContent.includes('병원 확정 전 가상 환자 상태 보고')"))
        evaluate(mobile, "document.getElementById('transport-toggle').click()")
        evaluate(staff, "document.getElementById('chatBody').value='가상 의료진 실제 HTTP 답장';document.getElementById('chatForm').requestSubmit()")
        wait(mobile, "!document.getElementById('transport-unread').hidden")
        self.assertEqual(evaluate(mobile, "window.gpsWatchCount"), 1)
        self.assertEqual(evaluate(mobile, "window.gpsClears"), 0)
        evaluate(mobile, "document.getElementById('transport-toggle').click()")
        wait(mobile, "document.getElementById('transport-messages').textContent.includes('가상 의료진 실제 HTTP 답장')")
        for width in (360, 375, 390, 412, 430):
            mobile.call('Emulation.setDeviceMetricsOverride', width=width, height=844, deviceScaleFactor=1, mobile=True)
            self.assertTrue(evaluate(mobile, "document.documentElement.scrollWidth<=innerWidth"), str(width))
        mobile.call('Emulation.setDeviceMetricsOverride', width=390, height=844, deviceScaleFactor=1, mobile=True)
        (self.run_dir / 'mobile-chat.png').write_bytes(base64.b64decode(mobile.call('Page.captureScreenshot')['data']))
        evaluate(mobile, "document.getElementById('transport-toggle').click();window.scrollTo(0,0)")
        (self.run_dir / 'mobile-map.png').write_bytes(base64.b64decode(mobile.call('Page.captureScreenshot')['data']))
        (self.run_dir / 'hospital-chat.png').write_bytes(base64.b64decode(staff.call('Page.captureScreenshot')['data']))
        self.assertTrue(evaluate(mobile, "window.storageWrites.length > 0 && window.storageWrites.every(([key,value]) => key === 'quick119.activeTransportPatientId' && value === " + json.dumps(pid) + ")"))
        self.assertEqual(evaluate(staff, 'window.storageWrites'), [])
        for width in (768, 1024):
            staff.call('Emulation.setDeviceMetricsOverride', width=width, height=900, deviceScaleFactor=1, mobile=False)
            self.assertTrue(evaluate(staff, 'document.documentElement.scrollWidth<=innerWidth'), 'Hospital tablet width ' + str(width))
        # Exercise the fetched hospital screen through the same gateway as the crew.
        evaluate(staff, "document.getElementById('hospitalToolToggle').click()")
        for name in ('resources', 'restrictions', 'operations'):
            self.assertTrue(evaluate(staff, f"document.querySelector('[data-tab={name}]').checkVisibility()"), name)
        evaluate(staff, "document.getElementById('hospitalToolToggle').click()")
        self.assertTrue(evaluate(staff, "document.getElementById('incomingPlanningToggle').checkVisibility()"))
        evaluate(staff, "document.getElementById('incomingPlanningToggle').click()")
        wait(staff, "document.getElementById('incomingPlanningToggle').ariaExpanded === 'true'")
        evaluate(staff, "document.getElementById('incomingPlanningToggle').click()")

        actions = "#incomingActions [data-patient-action="
        adjust = f"document.querySelector('{actions}adjust]')"
        self.assertTrue(evaluate(staff, adjust + ".checkVisibility()"))
        evaluate(staff, adjust + ".click()")
        self.assertIn('사유', evaluate(staff, "document.getElementById('notice').textContent"))
        evaluate(mobile, "window.preDepartureReload = true")
        mobile.call('Page.reload')
        wait(mobile, "!window.preDepartureReload && " + ready)
        self.assertTrue(evaluate(mobile, "document.getElementById('transport-depart').hidden"))
        self.assertIn('출발했습니다', evaluate(mobile, "document.getElementById('transport-messages').innerText"))
        self.assertFalse(self.call('GET', path)['needs_adjustment'])

        evaluate(staff, "const note=document.getElementById('reviewNote');note.value='가상 병상 점검';"
                        "note.dispatchEvent(new Event('input',{bubbles:true}));" + adjust + ".click()")
        wait(staff, "document.getElementById('notice').textContent.includes('수용 취소 요청을 이송자에게 보냈습니다')")
        after_request = self.call('GET', path)
        self.assertTrue(after_request['needs_adjustment'])
        self.assertEqual(after_request['adjustment_note'], '가상 병상 점검')
        self.assertEqual(after_request['destination_hospital_id'], 'H01')

        wait(mobile, "Array.from(document.querySelectorAll('#transport-inquiries .transport-inquiry')).some(row => row.dataset.state === 'PENDING')")
        room = self.call('GET', path + '/conversation')
        alternative = next(q for q in room['inquiries'] if q['hospital_id'] != 'H01' and q['state'] == 'PENDING')
        staff.call('Page.navigate', url=self.api_url + '/hospital/?hid=' + alternative['hospital_id'] + '&pid=' + pid)
        wait(staff, "document.querySelector('[data-act=accept]')")
        evaluate(staff, "document.getElementById('handler').value='가상 대체 의료진';document.getElementById('handler').dispatchEvent(new Event('input'));document.querySelector('[data-act=accept]').click()")
        wait(mobile, "document.querySelector('[data-transport-confirm]')?.getAttribute('aria-label')==='새 목적지로 확정'")
        evaluate(mobile, "document.querySelector('[data-transport-confirm]').click()")
        alternative_name = json.dumps(alternative['hospital_name'])
        wait(mobile, "Array.from(document.querySelectorAll('.tw-system-group-headline')).some(headline => "
                     "headline.textContent.includes(" + json.dumps(alternative['hospital_name'] +
                     '으로 목적지가 변경됐습니다.') + "))")
        self.assertEqual(evaluate(mobile, "document.getElementById('transport-error').textContent"), '')
        reassigned = self.call('GET', path)
        self.assertEqual(reassigned['destination_hospital_id'], alternative['hospital_id'])
        self.assertEqual(reassigned['state'], 'IN_TRANSIT')
        self.assertFalse(reassigned['needs_adjustment'])
        confirmed_label = evaluate(mobile, "document.querySelector('#transport-destination .tw-confirm-control--checked')?.getAttribute('aria-label')")
        self.assertIn(alternative['hospital_name'], confirmed_label)
        self.assertIn('목적지 확정됨', confirmed_label)
        self.call('POST', path + '/change-destination', {'inquiry_id': alternative['id']}, status=409)
        self.call('GET', f"/api/hospitals/H01/patients/{pid}/conversation", status=403)

        staff.call('Page.navigate', url=self.api_url + '/hospital/?hid=' + alternative['hospital_id'] + '&pid=' + pid)
        wait(staff, "document.querySelector('[data-tab=incoming]')")
        evaluate(staff, "document.querySelector('[data-tab=incoming]').click()")
        wait(staff, "document.querySelector('[data-patient=" + pid + "]')")
        evaluate(staff, "document.querySelector('[data-patient=" + pid + "]').click()")
        wait(staff, f"document.querySelector('{actions}arrive]:enabled')?.checkVisibility()")
        evaluate(staff, f"document.querySelector('{actions}arrive]').click()")
        wait(staff, f"document.querySelector('{actions}discharge]:enabled')?.checkVisibility()")
        self.assertEqual(self.call('GET', path)['state'], 'ARRIVED')
        wait(mobile, "document.getElementById('transport-workspace').dataset.stage === 'closed'")
        self.assertTrue(evaluate(mobile, "document.getElementById('transport-depart').hidden"))
        self.assertIsNone(evaluate(staff, adjust))
        evaluate(staff, f"document.querySelector('{actions}discharge]').click()")
        wait(staff, "document.getElementById('notice').textContent.includes('퇴실을 확인')")
        self.assertEqual(self.call('GET', path)['state'], 'COMPLETED')
        print('Real mobile/staff chat screenshots:', self.run_dir)

    def test_inquiry_accept_confirm_and_two_way_messages(self):
        pid = self.create_case()
        path = f"/api/patients/{pid}"
        self.call("GET", f"/api/hospitals/H01/patients/{pid}/conversation", status=403)
        pending = self.call("GET", path + "/conversation")
        self.assertIsNone(pending["active_hospital_session"])
        result = self.call("POST", path + "/inquiries", {"hospital_ids": ["H01"]})
        iid = result["inquiries"][0]["id"]
        self.call("POST", f"/api/inquiries/{iid}/respond", {"action": "accept", "handled_by": "가상 의료진"})
        self.call("GET", f"/api/hospitals/H01/patients/{pid}/conversation", status=403)
        self.call("POST", path + "/confirm", {"inquiry_id": iid})
        room = self.call("GET", path + "/conversation")
        sid = room["active_hospital_session"]["id"]
        first = {"body": "가상 대원 메시지", "client_message_id": uuid4().hex, "hospital_session_id": sid}
        sent = self.call("POST", path + "/messages", first)
        self.assertFalse(sent["duplicate"])
        retry = self.call("POST", path + "/messages", first)
        self.assertTrue(retry["duplicate"])
        self.assertEqual(sent["message"]["id"], retry["message"]["id"])
        self.call("POST", path + "/messages", dict(first, body="다른 내용"), status=409)
        self.call("POST", path + "/messages", dict(first, client_message_id=uuid4().hex,
                                                     hospital_session_id=None), status=409)
        hospital = f"/api/hospitals/H01/patients/{pid}"
        view = self.call("GET", hospital + "/conversation", direct=True)
        self.assertIn(first["body"], [item.get("body") for item in view["items"]])
        answer = {"body": "가상 의료진 답장", "client_message_id": uuid4().hex, "hospital_session_id": sid}
        self.call("POST", hospital + "/messages", answer)
        view = self.call("GET", path + "/conversation", direct=True)
        self.assertIn(answer["body"], [item.get("body") for item in view["items"]])
        self.assertEqual(view["poll_interval_seconds"], 3)
        self.call("GET", f"/api/hospitals/H02/patients/{pid}/conversation", status=403)
        self.call("POST", f"/api/hospitals/H02/patients/{pid}/messages", answer, status=403)
        second_pid = self.create_case()
        other = self.call("GET", f"/api/patients/{second_pid}/conversation")
        self.assertNotEqual(other["conversation"]["id"], room["conversation"]["id"])
        self.assertFalse(any(item["kind"] == "message" for item in other["items"]))

    def test_cursor_and_database_separation(self):
        pid = self.create_case()
        path = f"/api/patients/{pid}"
        for index in range(3):
            self.call("POST", path + "/messages", {"body": f"가상 기록 {index}",
                "client_message_id": uuid4().hex, "hospital_session_id": None})
        event_cursor = message_cursor = 0
        ids = []
        for _ in range(10):
            page = self.call("GET", path + f"/conversation?event_cursor={event_cursor}&message_cursor={message_cursor}&limit=1")
            ids.extend(item["id"] for item in page["items"])
            event_cursor, message_cursor = page["cursors"]["event_id"], page["cursors"]["message_id"]
            if not page["has_more"]:
                break
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(sum(item.startswith("message:") for item in ids), 3)
        self.call("GET", path + "/conversation?limit=0", status=400)
        with closing(sqlite3.connect(self.local_db)) as local, closing(sqlite3.connect(self.team_db)) as team:
            self.assertEqual(local.execute("SELECT COUNT(*) FROM patients").fetchone()[0], 0)
            self.assertEqual(local.execute("PRAGMA table_info(patients)").fetchone()[2], "INTEGER")
            self.assertEqual(team.execute("PRAGMA table_info(patients)").fetchone()[2], "TEXT")
            self.assertIsNone(local.execute("SELECT name FROM sqlite_master WHERE name='chat_messages'").fetchone())


class TransportBrowser(unittest.TestCase):
    def test_simultaneous_navigation_and_chat(self):
        from chrome_driver import Chrome
        from jinja2 import Environment, FileSystemLoader
        chrome_path = Path('C:/Program Files/Google/Chrome/Application/chrome.exe')
        self.assertTrue(chrome_path.is_file(), 'Desktop Chrome is required for this combined browser check')
        run = ROOT / '.local/test-runs' / uuid4().hex
        run.mkdir(parents=True)
        browser = Chrome(chrome_path, run / 'profile')
        self.addCleanup(browser.close)
        browser.call('Network.enable')
        browser.call('Network.setBlockedURLs', urls=['http://*', 'https://*'])
        browser.call('Emulation.setDeviceMetricsOverride', width=390, height=844, deviceScaleFactor=1, mobile=True)
        sources = {
            'WORKSPACE_HTML': Environment(loader=FileSystemLoader(ROOT / 'templates')).get_template('transport_workspace.html').render(),
            'WORKSPACE_CSS': (ROOT / 'static/transport-workspace.css').read_text(encoding='utf-8'),
            'PANEL_CSS': (ROOT / 'static/car-routes.css').read_text(encoding='utf-8'),
            'LEAFLET_CSS': (ROOT / 'static/vendor/leaflet/leaflet.css').read_text(encoding='utf-8'),
            'LEAFLET_SOURCE': (ROOT / 'static/vendor/leaflet/leaflet.js').read_text(encoding='utf-8'),
            'NAV_SOURCE': (ROOT / 'static/car-routes.js').read_text(encoding='utf-8'),
            'WORKSPACE_SOURCE': (ROOT / 'static/transport-workspace.js').read_text(encoding='utf-8'),
            'VOICE_SOURCE': (ROOT / 'static/chat-voice.js').read_text(encoding='utf-8'),
            'REVIEW_SOURCE': (ROOT / 'static/patient-review.js').read_text(encoding='utf-8'),
            'CHAT_SOURCE': (ROOT / 'static/hospital-chat.js').read_text(encoding='utf-8'),
        }
        for key, source in sources.items():
            browser.call('Runtime.evaluate', expression='var ' + key + ' = "";')
            for offset in range(0, len(source), 4000):
                browser.call('Runtime.evaluate', expression=key + ' += ' + json.dumps(source[offset:offset + 4000]))
        result = browser.call('Runtime.evaluate', expression=(ROOT / 'tests/transport_harness.js').read_text(encoding='utf-8'),
                              awaitPromise=True, returnByValue=True)
        self.assertNotIn('exceptionDetails', result, str(result))
        report = result['result']['value']
        self.assertGreaterEqual(report['checks'], 40)

        def evaluate(expression):
            return browser.call('Runtime.evaluate', expression=expression, returnByValue=True)['result'].get('value')

        evaluate("document.dispatchEvent(new CustomEvent('quick119:show-view',{detail:{view:'gps'}}))")
        handle = "document.getElementById('transport-sheet-handle')"
        expanded = handle + ".getAttribute('aria-expanded')"
        evaluate(handle + ".focus()")
        for key, code, vk, expected in [('Enter', 'Enter', 13, 'false'), (' ', 'Space', 32, 'true')]:
            for phase in ('keyDown', 'keyUp'):
                browser.call('Input.dispatchKeyEvent', type=phase, key=key, code=code, windowsVirtualKeyCode=vk,
                             text=('\r' if key == 'Enter' else key) if phase == 'keyDown' else '')
            self.assertEqual(evaluate(expanded), expected)

        browser.call('Emulation.setTouchEmulationEnabled', enabled=True)
        for distance, expected in [(45, 'false'), (-45, 'true'), (0, 'false'), (0, 'true')]:
            evaluate(handle + ".scrollIntoView({block:'nearest'})")
            x, y = evaluate(f"(() => {{ const r={handle}.getBoundingClientRect(); return [r.x+r.width/2, r.y+r.height/2]; }})()")
            browser.call('Input.dispatchTouchEvent', type='touchStart', touchPoints=[{'x': x, 'y': y}])
            if distance:
                browser.call('Input.dispatchTouchEvent', type='touchMove', touchPoints=[{'x': x, 'y': y + distance}])
            browser.call('Input.dispatchTouchEvent', type='touchEnd', touchPoints=[])
            # Chromium may deliver the compatibility click after touchEnd.
            deadline = time.monotonic() + 1
            while evaluate(expanded) != expected and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertEqual(evaluate(expanded), expected, f'touch distance {distance} at {x}, {y}')
        (run / 'browser-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print('Combined GPS/chat browser checks:', report['checks'])


if __name__ == "__main__":
    unittest.main(verbosity=2)
