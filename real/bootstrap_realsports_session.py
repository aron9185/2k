import argparse
import json
import os
import subprocess
import time
from pathlib import Path
from urllib.parse import quote

import requests
import websocket

from realsports_api import DEFAULT_BROWSER_SESSION_PATH


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_PORT = 9222
DEFAULT_PROFILE_DIR = str(
    Path(os.environ.get("LOCALAPPDATA", ".")) / "realsports_codex_chrome_profile"
)
DEFAULT_ENV_OUTPUT = BASE_DIR / ".realsports_env.ps1"
DEFAULT_TIMEOUT = 600
REALSPORTS_URL = "https://realsports.io/"
API_HOST_MARKERS = ("web.realsports.io", "web.realapp.com")
CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
)


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Launch a persistent Chrome/Edge profile with remote debugging, "
            "capture a live Real Sports request header after login, and save it "
            "for the other local scripts to reuse."
        )
    )
    parser.add_argument("--chrome-path", default="", help="Optional explicit browser executable path.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--profile-dir", default=DEFAULT_PROFILE_DIR)
    parser.add_argument("--session-output", default=str(DEFAULT_BROWSER_SESSION_PATH))
    parser.add_argument("--env-output", default=str(DEFAULT_ENV_OUTPUT))
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--no-launch", action="store_true", help="Attach to an existing browser on the chosen debug port.")
    return parser.parse_args()


def find_browser(explicit_path):
    if explicit_path:
        path = Path(explicit_path)
        if path.exists():
            return path
        raise FileNotFoundError(f"Browser executable not found: {explicit_path}")

    for candidate in CHROME_CANDIDATES:
        path = Path(candidate)
        if path.exists():
            return path
    raise FileNotFoundError("Could not find Chrome or Edge automatically.")


def debugger_base_url(port):
    return f"http://127.0.0.1:{port}"


def wait_for_debugger(port, timeout=20, process=None, profile_dir=None):
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        if process is not None and process.poll() is not None:
            profile_note = f" Profile dir: {profile_dir}." if profile_dir else ""
            raise RuntimeError(
                "Browser exited before the remote debugger became available."
                f"{profile_note} Try a normal local profile path like "
                r"%LOCALAPPDATA%\realsports_codex_chrome_profile."
            )
        try:
            response = requests.get(f"{debugger_base_url(port)}/json/version", timeout=2)
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # pragma: no cover - depends on local browser timing
            last_error = exc
            time.sleep(0.5)
    raise RuntimeError(f"Remote debugger did not come up on port {port}: {last_error}")


def launch_browser(browser_path, port, profile_dir):
    profile_dir.mkdir(parents=True, exist_ok=True)
    command = [
        str(browser_path),
        f"--remote-debugging-port={port}",
        "--remote-debugging-address=127.0.0.1",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--new-window",
        REALSPORTS_URL,
    ]
    return subprocess.Popen(command)


def list_targets(port):
    response = requests.get(f"{debugger_base_url(port)}/json/list", timeout=5)
    response.raise_for_status()
    return response.json()


def open_realsports_tab(port):
    url = f"{debugger_base_url(port)}/json/new?{quote(REALSPORTS_URL, safe=':/?=&')}"
    response = requests.put(url, timeout=5)
    response.raise_for_status()
    return response.json()


def choose_target(port):
    targets = list_targets(port)
    pages = [target for target in targets if target.get("type") == "page"]
    for target in pages:
        url = (target.get("url") or "").lower()
        if "realsports.io" in url:
            return target
    return open_realsports_tab(port)


def normalize_headers(headers):
    normalized = {}
    for key, value in (headers or {}).items():
        normalized[str(key).lower()] = str(value)
    return normalized


def extract_session_from_headers(headers):
    normalized = normalize_headers(headers)
    auth = normalized.get("real-auth-info", "").strip()
    if not auth or "!" not in auth:
        return None

    host = normalized.get(":authority") or normalized.get("host", "")
    if host and not any(marker in host for marker in API_HOST_MARKERS):
        return None

    return {
        "captured_at": str(int(time.time())),
        "real_auth_info": auth,
        "device_uuid": normalized.get("real-device-uuid", ""),
        "device_type": normalized.get("real-device-type", ""),
        "real_version": normalized.get("real-version", ""),
        "user_agent": normalized.get("user-agent", ""),
        "device_name": normalized.get("real-device-name", ""),
        "origin": normalized.get("origin", ""),
        "referer": normalized.get("referer", ""),
        "host": host,
    }


class CdpConnection:
    def __init__(self, websocket_url):
        self.ws = websocket.create_connection(websocket_url, timeout=1)
        self.message_id = 0

    def send(self, method, params=None):
        self.message_id += 1
        self.ws.send(
            json.dumps(
                {
                    "id": self.message_id,
                    "method": method,
                    "params": params or {},
                }
            )
        )

    def recv(self):
        return json.loads(self.ws.recv())

    def close(self):
        self.ws.close()


def capture_realsports_session(target, timeout):
    ws_url = target.get("webSocketDebuggerUrl")
    if not ws_url:
        raise RuntimeError("Target did not include a webSocketDebuggerUrl.")

    connection = CdpConnection(ws_url)
    try:
        connection.send("Network.enable")
        connection.send("Page.enable")
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                message = connection.recv()
            except websocket.WebSocketTimeoutException:
                continue

            if message.get("method") != "Network.requestWillBeSentExtraInfo":
                continue

            session_data = extract_session_from_headers(message.get("params", {}).get("headers", {}))
            if session_data:
                return session_data
    finally:
        connection.close()

    raise TimeoutError(
        "Timed out waiting for a Real Sports API request with real-auth-info. "
        "Log in in the opened browser window and leave the page open."
    )


def write_env_file(path, session_data):
    lines = [
        f"$env:REALSPORTS_AUTH_INFO='{session_data['real_auth_info']}'",
        f"$env:REALSPORTS_DEVICE_UUID='{session_data['device_uuid']}'",
        f"$env:REALSPORTS_DEVICE_TYPE='{session_data['device_type']}'",
        f"$env:REALSPORTS_REAL_VERSION='{session_data['real_version']}'",
        f"$env:REALSPORTS_USER_AGENT='{session_data['user_agent']}'",
        f"$env:REALSPORTS_DEVICE_NAME='{session_data['device_name']}'",
    ]
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf8")


def main():
    args = parse_args()
    process = None
    if not args.no_launch:
        browser_path = find_browser(args.chrome_path)
        process = launch_browser(browser_path, args.port, Path(args.profile_dir))
        print(f"Launched {browser_path}")
    else:
        print(f"Attaching to an existing browser on port {args.port}")

    wait_for_debugger(args.port, process=process, profile_dir=args.profile_dir)
    target = choose_target(args.port)
    print("Waiting for a logged-in Real Sports API request...")
    print("If the browser window is open, log in once and leave the Real Sports page open.")
    session_data = capture_realsports_session(target, args.timeout)

    session_output = Path(args.session_output)
    session_output.parent.mkdir(parents=True, exist_ok=True)
    session_output.write_text(
        json.dumps(session_data, indent=2, ensure_ascii=False),
        encoding="utf8",
    )
    write_env_file(args.env_output, session_data)

    print(f"Saved browser session to {session_output}")
    print(f"Saved PowerShell env helper to {args.env_output}")
    print("Future scripts can now reuse the browser session without calling /login first.")

    if process is not None:
        print("You can keep that browser profile for future reuse, or close it when you are done.")


if __name__ == "__main__":
    main()
