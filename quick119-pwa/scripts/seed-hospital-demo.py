"""Populate H01 on the loopback Quick119 synthetic team runtime."""
import argparse
from pathlib import Path
import sys
import time

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from seoul_demo import load_catalog


HOSPITAL = "H01"
STAFF = "시연 담당자"
FIXTURE = "hospital-demo-v1"
CASES = [
    dict(number="01", stage="pending", age=34, symptoms="계단에서 발을 접질린 뒤 오른쪽 발목 통증",
         minutes=25, vitals=dict(sbp=124, dbp=78, hr=86, temp=36.6, spo2=98)),
    dict(number="02", stage="question", age=8, symptoms="오후부터 발열과 기침, 보호자 동행",
         minutes=90, vitals=dict(sbp=105, dbp=68, hr=108, temp=38.4, spo2=97)),
    dict(number="03", stage="transport", age=67, symptoms="보행 중 어지럼과 메스꺼움",
         minutes=35, vitals=dict(sbp=142, dbp=86, hr=94, temp=36.7, spo2=97)),
]
QUESTION = "마지막으로 측정한 체온과 측정 시각을 알려 주세요."
HANDOFF = "가상 구급대입니다. 병원으로 출발했습니다. 67세 환자는 의식이 명료하며 어지럼과 메스꺼움을 호소합니다."
REPLY = "이송 보고를 확인했습니다. 도착하면 응급실 입구에서 인계받겠습니다."


def seed_case(client, case, origin):
    pid = "Q119-TEST-DEMO-" + case["number"]
    path = "/api/patients/" + pid

    def post(endpoint, **payload):
        return client.post(endpoint, json=payload).raise_for_status().json()

    current = client.get(path)
    if current.status_code == 404:
        vitals = case["vitals"]
        current = client.post("/api/patients", json=dict(
            id=pid, reporter_role="transporter", consent=True,
            age=case["age"], symptoms="[시연 " + case["number"] + "] " + case["symptoms"],
            consciousness="alert", breathing="normal", onset_at=int(time.time()) - 60 * case["minutes"],
            lat=origin["lat"], lon=origin["lon"],
            extra=dict(synthetic_fixture=True, demo_set=FIXTURE, vitals=vitals),
        ))
        if current.status_code == 409 and current.json().get("error") == "DUPLICATE":
            current = client.get(path)
    patient = current.raise_for_status().json()
    if patient.get("extra", {}).get("demo_set") != FIXTURE:
        raise ValueError(f"{pid}를 다른 기록이 사용하고 있습니다.")

    if not patient["inquiries"] and patient["state"] == "WAITING":
        patient = post(path + "/inquiries", hospital_ids=[HOSPITAL])
    inquiry = next((q for q in patient["inquiries"] if q["hospital_id"] == HOSPITAL), None)
    if not inquiry:
        print(pid, patient["state"], "H01 문의 없음")
        return
    response_path = f"/api/inquiries/{inquiry['id']}/respond"
    if patient["state"] == "WAITING" and inquiry["state"] == "PENDING":
        if case["stage"] == "question":
            post(response_path, action="question", question=QUESTION, handled_by=STAFF,
                 expected_version=inquiry["version"])
        elif case["stage"] == "transport":
            inquiry = post(response_path, action="accept", handled_by=STAFF,
                           expected_version=inquiry["version"])["inquiry"]
    if case["stage"] == "transport":
        if patient["state"] == "WAITING" and inquiry["state"] == "ACCEPTED" and inquiry["handled_by"] == STAFF:
            patient = post(path + "/confirm", inquiry_id=inquiry["id"])
        if patient["destination_hospital_id"] == HOSPITAL and patient["state"] == "CONFIRMED":
            patient = post(path + "/depart")
        if patient["destination_hospital_id"] == HOSPITAL and patient["state"] == "IN_TRANSIT":
            staff_path = f"/api/hospitals/{HOSPITAL}/patients/{pid}"
            chat = client.get(staff_path + "/conversation").raise_for_status().json()
            active = chat.get("active_hospital_session")
            if active and active["hospital_id"] == HOSPITAL and active["inquiry_id"] == inquiry["id"]:
                for base, key, sender, text in [(path, "crew", "시연 구급대", HANDOFF),
                                                (staff_path, "hospital", STAFF, REPLY)]:
                    post(base + "/messages", client_message_id=f"{pid}-{key}", hospital_session_id=active["id"],
                         sender_name=sender, body=text)
    end = client.get(path).raise_for_status().json()
    status = next(q["state"] for q in end["inquiries"] if q["hospital_id"] == HOSPITAL)
    print(pid, end["state"], status, end["symptoms"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18033, help="합성 팀 서버의 로컬 포트")
    args = parser.parse_args()
    with httpx.Client(base_url=f"http://127.0.0.1:{args.port}", timeout=10, trust_env=False) as client:
        info = client.get("/api/team-runtime").raise_for_status().json()
        if not (info.get("synthetic") is True and info.get("travel_provider") == "synthetic"
                and info.get("external_hospital_messages") is False and info.get("database", {}).get("valid")):
            raise ValueError("가상 병원 시연 서버를 확인할 수 없습니다.")
        catalog = load_catalog()
        expected = next(h for h in catalog["hospitals"] if h["id"] == HOSPITAL)
        hospital = client.get(f"/api/hospitals/{HOSPITAL}").raise_for_status().json()
        source_ids = hospital.get("source_ids", {})
        if not (hospital.get("source") == "synthetic"
                and all(hospital.get(k) == expected[k] for k in ("id", "name", "address", "lat", "lon"))
                and source_ids.get("nmc") == expected["hpid"]
                and source_ids.get("identity_source") == "seoul_public"
                and source_ids.get("snapshot_date") == catalog["snapshot_date"]):
            raise ValueError("H01 시연 병원을 확인할 수 없습니다.")
        for case in CASES:
            seed_case(client, case, catalog["origin"])


if __name__ == "__main__":
    main()
