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
                                              stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW))

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
        for path in ("/", "/transport/", "/emergency/hospitals", "/manifest.webmanifest", "/sw.js"):
            self.assertEqual(self.http.get(self.api_url + path).status_code, 200)
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
        mobile.call('Page.navigate', url=self.api_url + '/transport/')
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
        mobile.call('Page.navigate', url=self.api_url + '/transport/')
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
        self.assertEqual(self.call('GET', route)['inquiries'], [])

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

        brand_bar = "getComputedStyle(document.querySelector('.app-header')).display !== 'none'"
        typing = "document.getElementById('transport-workspace').dataset.kb === 'open'"
        ready = "document.querySelector('#transport-workspace[data-connection=live] #transport-message:enabled') && new URLSearchParams(location.search).has('pid')"
        for width, full, reduced, partial, min_log in ((360, 740, 450, 620, 200), (844, 390, 220, 280, 30)):
            with self.subTest(width=width, full=full):
                screen(width, full)
                mobile.call('Page.navigate', url=self.api_url + '/transport/')
                wait(ready)
                self.assertTrue(js(brand_bar))
                screen(width, reduced)
                wait(f"visualViewport.height === {reduced}")
                self.assertFalse(js(typing))
                screen(width, full)
                wait(f"visualViewport.height === {full}")
                js("document.getElementById('transport-message').focus()")
                screen(width, reduced)
                wait(typing)
                self.assertFalse(js(brand_bar))
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
                self.assertTrue(js(brand_bar))

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
        self.assertTrue(js("""(() => {
            const tray = document.querySelector('.tw-updates');
            tray.scrollTop = tray.scrollHeight;
            const clip = tray.getBoundingClientRect();
            return [...tray.querySelectorAll('[data-transport-confirm]')].some(button => {
              const r = button.getBoundingClientRect();
              return r.height > 0 && r.top >= clip.top - 1 && r.bottom <= clip.bottom + 1;
            });
        })()"""))

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
        Storage.prototype.setItem=function(key){window.storageWrites.push(key);throw Error('Permanent storage disabled in UI test');};
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
        wait(mobile, "document.querySelector('[data-transport-inquire=H01]') && !document.getElementById('transport-gps').hidden")
        submitted = self.call('GET', path)
        self.assertEqual(submitted['age'], 20)
        self.assertEqual(submitted['extra']['vitals']['glucose'], 105)
        staff_link = evaluate(mobile, "document.querySelector('[data-candidate-id=H01] a').href")
        self.assertEqual(staff_link, self.api_url + '/hospital/?hid=H01&pid=' + pid)
        evaluate(mobile, "document.querySelector('[data-transport-inquire=H01]').click()")
        wait(mobile, "document.getElementById('transport-inquiries').textContent.includes('문의 중')")
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
        self.assertEqual(evaluate(mobile, 'window.storageWrites'), [])
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
