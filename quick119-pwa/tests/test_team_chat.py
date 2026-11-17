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
        payload = Item(origin=Item(lat=35.1595, lon=126.8526), destination=Item(lat=35.1715, lon=126.8326))
        with patch.dict(os.environ, QUICK119_SYNTHETIC_ROUTES='0'):
            self.assertIsNone(demo_route(request, payload))
        with patch.dict(os.environ, QUICK119_SYNTHETIC_ROUTES='1'):
            self.assertEqual(demo_route(request, payload)['provider'], 'synthetic')
            with self.assertRaises(HTTPException):
                demo_route(Item(client=Item(host='192.168.0.50')), payload)
            payload.destination.lat = 37.5
            with self.assertRaises(HTTPException):
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
        team_python = ROOT / ".local/team-venv/Scripts/python.exe"
        cls.spawn([str(team_python), str(ROOT / "scripts/run-team-chat.py"), "--port", "0",
                   "--db", str(cls.team_db), "--ready-file", str(ready)], clean_env(), "team")
        deadline = time.monotonic() + 30
        while not ready.exists():
            if cls.processes[0].poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("Team runtime failed. See " + str(cls.run_dir / "team.log"))
            time.sleep(.05)
        cls.team_url = json.loads(ready.read_text(encoding="utf-8"))["base_url"]
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
        for path in ("/", "/voice-intake", "/emergency/hospitals", "/manifest.webmanifest", "/sw.js"):
            self.assertEqual(self.http.get(self.api_url + path).status_code, 200)
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
            return evaluate(browser, "new Promise((resolve,reject)=>{const start=Date.now();const t=setInterval(()=>{try{if(" + condition + "){clearInterval(t);resolve(true);}else if(Date.now()-start>16000){clearInterval(t);reject(Error('Timed out: '+document.body.innerText.slice(-1600)));}}catch(e){clearInterval(t);reject(e);}},80);})")

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
          getCurrentPosition(ok){ok({coords:{latitude:35.1595,longitude:126.8526,accuracy:10},timestamp:Date.now()});},
          watchPosition(ok){window.gpsWatchCount++;window.mockGPS=ok;return window.gpsWatchCount;},
          clearWatch(){window.gpsClears++;}
        }});
        const realFetch=window.fetch;
        window.fetch=(url,options)=>{
          if(url==='/api/routes/car')return Promise.resolve(new Response(JSON.stringify({code:'ok',distance_m:2500,duration_sec:300,
            coordinates:[[126.8526,35.1595],[126.8326,35.1715]],cached:false}),{headers:{'Content-Type':'application/json'}}));
          return realFetch(url,options);
        };
        """)
        staff.call('Page.addScriptToEvaluateOnNewDocument', source=guard)
        mobile.call('Page.navigate', url=self.api_url + '/transport/')
        wait(mobile, "document.querySelector('[data-transport-inquire]')")
        evaluate(mobile, "document.getElementById('transport-new-case').click()")
        wait(mobile, "document.getElementById('transport-case').textContent.includes('Q119-TEST-') && !document.querySelector('[data-transport-inquire]').disabled")
        pid = evaluate(mobile, "new URLSearchParams(location.search).get('pid')")
        evaluate(mobile, "document.querySelector('[data-transport-inquire=H01]').click()")
        wait(mobile, "document.getElementById('transport-inquiries').textContent.includes('문의 중')")
        staff.call('Page.navigate', url=self.api_url + '/hospital/?hid=H01&pid=' + pid)
        wait(staff, "document.querySelector('[data-act=accept]')")
        evaluate(staff, "document.getElementById('handler').value='가상 의료진';document.getElementById('handler').dispatchEvent(new Event('input'));document.querySelector('[data-act=accept]').click()")
        wait(mobile, "document.querySelector('[data-transport-confirm]')")
        self.assertTrue(evaluate(mobile, "document.getElementById('transport-send').disabled"))
        evaluate(mobile, "document.querySelector('[data-transport-confirm]').click()")
        wait(mobile, "!document.getElementById('transport-send').disabled && !document.getElementById('car-route-start').disabled")
        evaluate(mobile, "document.getElementById('car-route-start').click()")
        self.assertEqual(evaluate(mobile, "window.gpsWatchCount"), 1)
        evaluate(mobile, "document.getElementById('transport-toggle').click();document.getElementById('transport-message').value='가상 대원 실제 HTTP 메시지';document.getElementById('transport-message-form').requestSubmit()")
        wait(mobile, "document.getElementById('transport-message').value==='' && !document.getElementById('transport-send').disabled")
        evaluate(staff, "document.querySelector('[data-tab=incoming]').click()")
        wait(staff, "document.querySelector('[data-patient=" + pid + "]')")
        # Select by the team's original incoming-card action, without replacing its chat implementation.
        evaluate(staff, "document.querySelector('[data-patient=" + pid + "]').click()")
        wait(staff, "document.getElementById('chatBody') && !document.getElementById('chatSend').disabled && document.getElementById('chatLog').textContent.includes('가상 대원 실제 HTTP 메시지')")
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
        (run / 'browser-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print('Combined GPS/chat browser checks:', report['checks'])


if __name__ == "__main__":
    unittest.main(verbosity=2)
