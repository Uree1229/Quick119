"""Verify the Seoul demo catalog and upgrades of an existing team DB, offline."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP.parent))
sys.path.insert(0, str(APP))

from seoul_demo import apply_seoul_overlay, load_catalog
from server import conversations, db, service, team_runtime
from server.travel import TravelEstimator, synthetic_estimate


class SeoulHospitalDemo(unittest.TestCase):
    campus = (37.5051, 126.9573)

    def test_catalog_matches_published_hospital_identities(self):
        public_path = APP.parent / "datasets/demo/seoul_public_catalog.json"
        source = {h["source_ids"]["nmc"]: h for h in json.loads(public_path.read_text("utf-8"))["hospitals"]}
        catalog = load_catalog()
        self.assertEqual(catalog["source_url"], "https://www.data.go.kr/data/15000563/openapi.do")
        self.assertEqual(catalog["snapshot_date"], "2026-10-13")
        self.assertEqual(catalog["crosscheck_url"], "https://data.seoul.go.kr/dataList/OA-20338/S/1/datasetView.do")
        self.assertEqual(catalog["crosscheck_date"], "2026-11-08")
        self.assertEqual((catalog["origin"]["lat"], catalog["origin"]["lon"]), self.campus)
        expected_ids = ["A1100003", "A1100040", "A1100011", "A1100004", "A1100012", "A1100041",
                        "A1100055", "A1100054", "A1100038", "A1100037"]
        hospitals = sorted(catalog["hospitals"], key=lambda h: h["id"])
        self.assertEqual([h["id"] for h in hospitals], [f"H{i:02}" for i in range(1, 11)])
        self.assertEqual([h["hpid"] for h in hospitals], expected_ids)
        for hospital in hospitals:
            with self.subTest(hospital=hospital["id"]):
                public = source[hospital["hpid"]]
                self.assertEqual(hospital["name"].replace(" ", ""), public["name"].replace(" ", ""))
                self.assertTrue(hospital["address"].startswith("서울특별시 "))
                for key in ["address", "lat", "lon"]:
                    self.assertEqual(hospital[key], public[key])
                self.assertFalse(hospital.get("phone"))

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "team.db"
        self.now = 1_800_000_000
        team_runtime.prepare_database(self.path, self.now)
        self.conn = db.connect(str(self.path))
        self.addCleanup(self.conn.close)

    def test_upgrade_preserves_records_and_recalculates_active_eta_once(self):
        seed_point, old_mobile = (35.135, 126.878), (35.1595, 126.8526)
        user_point = (37.511, 126.954)
        cases = [("Q119-TEST-DEMO-01", 34, seed_point), ("Q119-TEST-DEMO-02", 8, seed_point),
                 ("Q119-TEST-DEMO-03", 67, seed_point), ("Q119-TEST-mobile", None, old_mobile),
                 ("Q119-TEST-user-position", 41, user_point), ("OTHER", 42, old_mobile),
                 ("Q119-TEST-unmarked", 24, old_mobile)]
        for pid, age, (lat, lon) in cases:
            service.create_patient(self.conn, self.now, pid, dict(reporter_role="transporter", consent=True,
                age=age, symptoms="시연 보고", lat=lat, lon=lon,
                extra={} if pid == "Q119-TEST-unmarked" else {"synthetic_fixture": True}))
        pid = "Q119-TEST-DEMO-03"
        sent = service.send_inquiries(self.conn, self.now, pid, ["H01"])
        iid = sent["inquiries"][0]["id"]
        service.respond(self.conn, self.now, iid, "accept", "시연 담당자")
        service.confirm_destination(self.conn, self.now, pid, iid)
        service.depart(self.conn, self.now + 9, pid, TravelEstimator(use_kakao=False))
        session = self.conn.execute("SELECT id FROM conversation_hospital_sessions").fetchone()[0]
        conversations.send_message(self.conn, self.now + 10, pid, dict(
            body="이송 중입니다.", hospital_session_id=session, client_message_id="demo-crew"))
        conversations.send_message(self.conn, self.now + 11, pid, dict(
            body="확인했습니다.", hospital_session_id=session, client_message_id="demo-staff"), hospital_id="H01")

        def records(table):
            return [tuple(r) for r in self.conn.execute("SELECT * FROM " + table + " ORDER BY rowid")]

        protected = {t: records(t) for t in ["resources", "inquiries", "chat_messages", "conversations"]}
        apply_seoul_overlay(self.path)
        for t, expected in protected.items():
            self.assertEqual(records(t), expected, t)
        for case_id, age, point in cases:
            patient = service.patient_view(self.conn, case_id, self.now + 12)
            self.assertEqual(patient["age"], age)
            self.assertEqual(patient["symptoms"], "시연 보고")
            expected = point if case_id in ("OTHER", "Q119-TEST-unmarked", "Q119-TEST-user-position") else self.campus
            self.assertEqual((patient["lat"], patient["lon"]), expected)
        for row, fixture in zip(self.conn.execute("SELECT * FROM hospitals ORDER BY id"),
                                sorted(load_catalog()["hospitals"], key=lambda h: h["id"]), strict=True):
            self.assertEqual((row["name"], row["address"], row["lat"], row["lon"]),
                             tuple(fixture[k] for k in ["name", "address", "lat", "lon"]))
            self.assertFalse(row["phone"])
            ids = json.loads(row["source_ids"])
            self.assertEqual((ids["nmc"], ids["identity_source"]), (fixture["hpid"], "seoul_public"))
        patient = service.patient_view(self.conn, pid, self.now + 12)
        hospital = patient["destination"]
        duration = synthetic_estimate(self.campus, (hospital["lat"], hospital["lon"]))["duration_seconds"]
        self.assertEqual(patient["state"], "IN_TRANSIT")
        self.assertEqual(patient["departed_at"], self.now + 9)
        self.assertEqual(patient["eta_at"], patient["departed_at"] + duration)
        row = self.conn.execute("SELECT * FROM conversation_hospital_sessions").fetchone()
        snapshot = json.loads(row["patient_snapshot"])
        self.assertEqual((row["id"], row["inquiry_id"], snapshot["age"]), (session, iid, 67))
        self.assertEqual((snapshot["lat"], snapshot["lon"]), self.campus)
        before_repeat = list(self.conn.iterdump())
        apply_seoul_overlay(self.path)
        self.assertEqual(list(self.conn.iterdump()), before_repeat)

    def test_unrelated_database_is_preserved(self):
        self.conn.execute("UPDATE team_runtime_meta SET catalog_id='another-catalog'")
        self.conn.commit()
        before = list(self.conn.iterdump())
        with self.assertRaises(ValueError):
            apply_seoul_overlay(self.path)
        self.assertEqual(list(self.conn.iterdump()), before)
