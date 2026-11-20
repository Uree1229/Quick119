"""Local public hospital identities for the synthetic team runtime."""
from contextlib import closing
from datetime import date
from functools import lru_cache
import json
from pathlib import Path
import sqlite3


CATALOG = Path(__file__).with_name("static") / "seoul-demo-hospitals.json"
ALIASES = tuple(f"H{i:02}" for i in range(1, 11))
MARKER = (1, "team-synthetic-hospitals-20261030-v1", "server-allocation-baseline-20261030-v1")
LEGACY_ORIGINS = ((35.1350, 126.8780), (35.1595, 126.8526))


@lru_cache(maxsize=1)
def load_catalog():
    with CATALOG.open(encoding="utf-8") as file:
        catalog = json.load(file)
    date.fromisoformat(catalog["snapshot_date"])
    hospitals = catalog["hospitals"]
    if sorted(h["id"] for h in hospitals) != list(ALIASES):
        raise ValueError("Expected exactly the H01-H10 Seoul demo aliases.")
    if (len({h["hpid"] for h in hospitals}) != len(hospitals)
            or not all(isinstance(h[k], str) and h[k].strip()
                       for h in hospitals for k in ("name", "address", "hpid"))):
        raise ValueError("Incomplete or duplicate Seoul demo identity.")
    if any("display_name" in h and (not isinstance(h["display_name"], str)
                                    or not h["display_name"].strip()) for h in hospitals):
        raise ValueError("Invalid Seoul demo display name.")
    if len({h.get("display_name", h["name"]) for h in hospitals}) != len(hospitals):
        raise ValueError("Duplicate Seoul demo display name.")
    if any(not h["address"].startswith("서울특별시 ") for h in hospitals):
        raise ValueError("The demo catalog must contain only Seoul hospitals.")
    for point in [catalog["origin"], *hospitals]:
        for key, low, high in (("lat", 37.4, 37.71), ("lon", 126.72, 127.20)):
            if type(point[key]) not in (int, float) or not low <= point[key] <= high:
                raise ValueError(f"Invalid Seoul demo {key}.")
    return catalog


def _move_fixture_origins(conn, origin, hospitals):
    from server.travel import synthetic_estimate

    point = (origin["lat"], origin["lon"])
    patients = conn.execute("""
        SELECT id, state, departed_at, destination_hospital_id, eta_at FROM patients
        WHERE id GLOB 'Q119-TEST-*' AND json_extract(extra, '$.synthetic_fixture') = 1
        AND (lat, lon) IN (VALUES (?, ?), (?, ?))
    """, (*LEGACY_ORIGINS[0], *LEGACY_ORIGINS[1])).fetchall()
    for pid, state, departed, destination, eta in patients:
        if state == "IN_TRANSIT":
            if departed is None or destination not in hospitals:
                raise ValueError(f"Missing synthetic transport details for {pid}.")
            hospital = hospitals[destination]
            travel = synthetic_estimate(point, (hospital["lat"], hospital["lon"]))
            eta = departed + travel["duration_seconds"]
        conn.execute("UPDATE patients SET lat=?, lon=?, eta_at=? WHERE id=?", (*point, eta, pid))

    # A crew may have moved since a session opened. Match the saved point
    # independently; never copy a patient's current location into history.
    conn.execute("""
        UPDATE conversation_hospital_sessions
        SET patient_snapshot=json_set(patient_snapshot, '$.lat', ?, '$.lon', ?)
        WHERE conversation_id IN (SELECT c.id FROM conversations c JOIN patients p ON p.id=c.patient_id
            WHERE p.id GLOB 'Q119-TEST-*' AND json_extract(p.extra, '$.synthetic_fixture') = 1)
        AND (json_extract(patient_snapshot, '$.lat'), json_extract(patient_snapshot, '$.lon'))
            IN (VALUES (?, ?), (?, ?))
    """, (*point, *LEGACY_ORIGINS[0], *LEGACY_ORIGINS[1]))


def apply_seoul_overlay(path):
    """Apply only to an existing, prepared DB; the launcher owns its lease."""
    catalog = load_catalog()
    hospitals = {h["id"]: h for h in catalog["hospitals"]}
    uri = Path(path).resolve().as_uri() + "?mode=rw"
    with closing(sqlite3.connect(uri, uri=True)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        marker = conn.execute("""
            SELECT schema_version, catalog_id, baseline_id FROM team_runtime_meta WHERE id=1
        """).fetchone()
        if marker != MARKER or conn.execute("SELECT id, source FROM hospitals ORDER BY id").fetchall() != [
                (alias, "synthetic") for alias in ALIASES]:
            raise ValueError("Seoul demo requires the pinned synthetic runtime catalog.")
        for alias, h in hospitals.items():
            row = conn.execute("SELECT name, address, lat, lon, phone, source_ids FROM hospitals WHERE id=?",
                               (alias,)).fetchone()
            ids = json.loads(row[-1])
            provenance = dict(nmc=h["hpid"], identity_source="seoul_public", snapshot_date=catalog["snapshot_date"])
            identity = (h.get("display_name", h["name"]), h["address"], h["lat"], h["lon"], "")
            if row[:-1] != identity or not provenance.items() <= ids.items():
                ids.update(provenance)
                conn.execute("UPDATE hospitals SET name=?, address=?, lat=?, lon=?, phone=?, source_ids=? WHERE id=?",
                             (*identity, json.dumps(ids, ensure_ascii=False), alias))
        _move_fixture_origins(conn, catalog["origin"], hospitals)
