"""Start team CommonRuntime against a dedicated synthetic database."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT.parent


def checkout_commit():
    try:
        return subprocess.check_output(
            ["git", "-C", str(SOURCE), "rev-parse", "--verify", "HEAD"],
            text=True, stderr=subprocess.DEVNULL, timeout=3,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None


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
        parser.error("Run from the Quick119 repository with server/team_runtime.py beside quick119-pwa/.")
    if args.ready_file:
        ready = args.ready_file.resolve()
        if ready.parent != db_path.parent or ready.exists():
            parser.error("Readiness file must be a new file beside the synthetic DB.")
    # Standalone team runtime uses only the stdlib: no FastAPI/solver dependency changes.
    sys.path.insert(0, str(SOURCE))
    sys.path.insert(1, str(ROOT))
    from intake_commands import install_routes
    install_routes()
    from server import team_runtime
    from seoul_demo import apply_seoul_overlay
    if args.refresh_synthetic_resources and db_path.exists():
        team_runtime.refresh_fixture_resources(db_path)
    # The team runtime calls this after reserving its port and DB lease,
    # before accepting requests. Keep those guarantees for the Seoul fixture.
    prepare_base = team_runtime._prepare_database
    def prepare_seoul(path, now=None):
        result = prepare_base(path, now)
        apply_seoul_overlay(path)
        return result
    team_runtime._prepare_database = prepare_seoul
    try:
        with team_runtime.CommonRuntime(db_path, port=args.port, mode="voice-only",
                           allow_environment_difference=True) as runtime:
            info = {"base_url": runtime.base_url, "pid": os.getpid(), "commit": checkout_commit(),
                    "source_root": str(Path(team_runtime.__file__).resolve().parents[1]),
                    "synthetic": True, "mode": "voice-only", "database": str(db_path)}
            if args.ready_file:
                with ready.open("x", encoding="utf-8") as file:
                    json.dump(info, file)
            print(json.dumps(info, ensure_ascii=False), flush=True)
            print("Synthetic chat only; no planner, automatic hospital inquiries enabled, no external APIs. Ctrl+C to stop.", flush=True)
            while runtime.thread.is_alive():
                runtime.thread.join(timeout=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        team_runtime._prepare_database = prepare_base


if __name__ == "__main__":
    main()
