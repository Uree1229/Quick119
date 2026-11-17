import sqlite3
import json
from contextlib import closing
from settings import DB_PATH


# 데이터베이스 생성
def create_database():

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)

    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS patients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            birth TEXT,
            phone TEXT,
            address TEXT,
            guardian TEXT,
            guardian_phone TEXT
        )
    """)

    # 기존 환자 테이블과 독립된 현장 보고 기록. 기존 행/컬럼은 변경하지 않습니다.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS emergency_intakes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT NOT NULL UNIQUE,
            patient_id INTEGER,
            transcript TEXT NOT NULL,
            state_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        )
    """)

    conn.commit()
    conn.close()


# 환자 정보 저장
def save_patient(
    name,
    birth,
    phone,
    address,
    guardian,
    guardian_phone
):

    conn = sqlite3.connect(DB_PATH)

    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO patients
        (name, birth, phone, address, guardian, guardian_phone)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        name,
        birth,
        phone,
        address,
        guardian,
        guardian_phone
    ))

    conn.commit()
    conn.close()


# 환자 목록 가져오기
def get_patients():

    conn = sqlite3.connect(DB_PATH)

    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            id,
            name,
            birth,
            phone,
            address,
            guardian,
            guardian_phone
        FROM patients
        ORDER BY id DESC
    """)

    patients = cursor.fetchall()

    conn.close()

    return patients


# 특정 환자 정보 가져오기
def get_patient(patient_id):

    conn = sqlite3.connect(DB_PATH)

    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            id,
            name,
            birth,
            phone,
            address,
            guardian,
            guardian_phone
        FROM patients
        WHERE id = ?
    """, (patient_id,))

    patient = cursor.fetchone()

    conn.close()

    return patient


# 초기화는 FastAPI lifespan에서만 수행합니다. import는 DB에 접근하지 않습니다.


def save_emergency_intake(request_id, patient_id, transcript, state):
    """같은 요청의 재전송은 기존 저장 결과를 반환합니다. 원문은 별도로 보존합니다."""
    state_json = json.dumps(state, ensure_ascii=False, sort_keys=True)
    with closing(sqlite3.connect(DB_PATH, timeout=10)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT id, patient_id, transcript, state_json, created_at FROM emergency_intakes WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        if existing:
            if existing[1:4] != (patient_id, transcript, state_json):
                raise ValueError("Request payload changed")
            return {"id": existing[0], "created_at": existing[4]}
        if patient_id is not None and connection.execute(
                "SELECT id FROM patients WHERE id = ?", (patient_id,)).fetchone() is None:
            raise LookupError("Unknown patient")
        cursor = connection.execute(
            "INSERT INTO emergency_intakes (request_id, patient_id, transcript, state_json) VALUES (?, ?, ?, ?)",
            (request_id, patient_id, transcript, state_json),
        )
        created_at = connection.execute("SELECT created_at FROM emergency_intakes WHERE id = ?",
                                        (cursor.lastrowid,)).fetchone()[0]
        return {"id": cursor.lastrowid, "created_at": created_at}
