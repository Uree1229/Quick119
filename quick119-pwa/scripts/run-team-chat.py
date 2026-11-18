"""Start unchanged team CommonRuntime against a dedicated synthetic database."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "37492e19240490d656e7f4f85cb43f52e986bd0b"
SOURCE = ROOT / ".local" / "team-source" / ("Quick119-" + COMMIT)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18033)
    parser.add_argument("--db", type=Path, default=ROOT / ".local/team-chat/chat-synthetic.db")
    parser.add_argument("--ready-file", type=Path)
    parser.add_argument("--refresh-synthetic-resources", action="store_true",
                        help="Explicitly refresh timestamps of the existing synthetic fixture resources only")
    args = parser.parse_args()
    db_path = args.db.resolve()
    allowed = [ROOT / ".local/team-chat", ROOT / ".local/test-runs"]
    if not any(db_path.is_relative_to(p.resolve()) for p in allowed):
        parser.error("Only dedicated .local/team-chat or .local/test-runs databases are allowed.")
    if not (SOURCE / "server/team_runtime.py").is_file():
        parser.error("Pinned team source is missing. See TEAM_CHAT_SETUP.md.")
    if args.ready_file:
        ready = args.ready_file.resolve()
        if ready.parent != db_path.parent or ready.exists():
            parser.error("Readiness file must be a new file beside the synthetic DB.")
    # Standalone team runtime uses only the stdlib: no FastAPI/solver dependency changes.
    sys.path.insert(0, str(SOURCE))
    sys.path.insert(1, str(ROOT))
    from intake_commands import install_routes
    install_routes()
    from server.team_runtime import CommonRuntime, refresh_fixture_resources
    if args.refresh_synthetic_resources and db_path.exists():
        refresh_fixture_resources(db_path)
    try:
        with CommonRuntime(db_path, port=args.port, mode="voice-only",
                           allow_environment_difference=True) as runtime:
            info = {"base_url": runtime.base_url, "pid": os.getpid(), "commit": COMMIT,
                    "synthetic": True, "mode": "voice-only", "database": str(db_path)}
            if args.ready_file:
                with ready.open("x", encoding="utf-8") as file:
                    json.dump(info, file)
            print(json.dumps(info, ensure_ascii=False), flush=True)
            print("Synthetic chat only; no planner, no automatic inquiry worker, no external APIs. Ctrl+C to stop.", flush=True)
            while runtime.thread.is_alive():
                runtime.thread.join(timeout=0.5)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
