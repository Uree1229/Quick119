"""Verify the downloaded commit archive and every extracted source file."""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "37492e19240490d656e7f4f85cb43f52e986bd0b"
EXPECTED = "d817a5c7926e6ebafb42bb2a86d4c940139608282311fccbfd93ba025de8de9b"
archive = ROOT / ".local/downloads" / ("Quick119-" + COMMIT + ".zip")
sources = ROOT / ".local/team-source"
if hashlib.sha256(archive.read_bytes()).hexdigest() != EXPECTED:
    raise SystemExit("Archive checksum mismatch")
count = 0
with zipfile.ZipFile(archive) as zip_file:
    for entry in zip_file.infolist():
        if entry.is_dir():
            continue
        path = (sources / entry.filename).resolve()
        if not path.is_relative_to((sources / ("Quick119-" + COMMIT)).resolve()):
            raise SystemExit("Unexpected archive path")
        if path.name.startswith(".env") or path.suffix.lower() in {
                ".db", ".sqlite", ".sqlite3", ".pem", ".key", ".crt", ".pfx", ".p12"}:
            raise SystemExit("Disallowed archive file")
        if path.read_bytes() != zip_file.read(entry):
            raise SystemExit("Source differs from pinned archive: " + entry.filename)
        count += 1
print(json.dumps({"commit": COMMIT, "archive_sha256": EXPECTED, "files_verified": count,
                  "source_unchanged": True}))
