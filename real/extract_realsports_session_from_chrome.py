import argparse
from pathlib import Path

from realsports_api import DEFAULT_BROWSER_SESSION_PATH, RealSportsClient


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Extract the current Real Sports auth session from an already logged-in "
            "Chrome or Edge profile and save it for the local scripts to reuse."
        )
    )
    parser.add_argument("--output", default=str(DEFAULT_BROWSER_SESSION_PATH))
    parser.add_argument("--leveldb-dir", default="", help="Optional explicit Local Storage leveldb directory.")
    return parser.parse_args()


def main():
    args = parse_args()
    if args.leveldb_dir:
        session = RealSportsClient._extract_browser_session_from_leveldb_dir(Path(args.leveldb_dir))
    else:
        session = RealSportsClient._extract_browser_session_from_local_storage()

    if not session:
        raise SystemExit(
            "Could not find a reusable Real Sports browser session in Chrome/Edge local storage."
        )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    RealSportsClient._save_browser_session(output_path, session)
    print(f"Saved Real Sports browser session to {output_path}")
    print(f"Auth source: {session.get('source', 'unknown')}")
    print(f"Auth header: {session.get('real_auth_info', '')}")


if __name__ == "__main__":
    main()
