from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from market_csv import build_public_session

try:
    from curl_cffi import requests as curl_requests
except Exception:  # pragma: no cover - optional dependency
    curl_requests = None


BASE_DIR = Path(__file__).resolve().parent
REQUESTS_DIR = BASE_DIR / ".cache" / "sportsbook_requests"
PAYLOADS_DIR = BASE_DIR / ".cache" / "sportsbook_payloads"


class SportsbookFetchBlocked(RuntimeError):
    pass


def _clear_proxy_env() -> None:
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(key, None)


def _proxy_map(proxy_url: str | None) -> dict[str, str] | None:
    if not proxy_url:
        return None
    return {"http": proxy_url, "https": proxy_url}


def get_browser_like_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
    proxy_url: str | None = None,
    impersonate: str = "chrome136",
) -> Any:
    _clear_proxy_env()
    effective_headers = dict(headers or {})
    last_error: Exception | None = None
    if curl_requests is not None:
        try:
            curl_kwargs: dict[str, Any] = {
                "headers": effective_headers,
                "impersonate": impersonate,
                "timeout": timeout,
            }
            proxies = _proxy_map(proxy_url)
            if proxies:
                curl_kwargs["proxies"] = proxies
            response = curl_requests.get(url, **curl_kwargs)
            if response.status_code >= 400:
                raise SportsbookFetchBlocked(f"{response.status_code} from {url}")
            return response.json()
        except Exception as exc:
            last_error = exc

    session = build_public_session("c2k-sportsbook-browser-like/1.0")
    if effective_headers:
        session.headers.update(effective_headers)
    proxies = _proxy_map(proxy_url)
    if proxies:
        session.proxies.update(proxies)
    try:
        response = session.get(url, timeout=timeout)
        if response.status_code >= 400:
            raise SportsbookFetchBlocked(f"{response.status_code} from {url}")
        return response.json()
    except Exception as exc:
        if last_error is not None:
            raise SportsbookFetchBlocked(
                f"{last_error}; fallback failed: {exc}"
            ) from exc
        raise


def load_request_config(provider: str) -> dict[str, Any]:
    path = REQUESTS_DIR / f"{provider}.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf8"))
    except Exception:
        return {}


def save_payload(provider: str, sport: str, payload: Any) -> Path:
    PAYLOADS_DIR.mkdir(parents=True, exist_ok=True)
    path = PAYLOADS_DIR / f"{provider}_{sport}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf8")
    return path


def load_saved_payload(provider: str, sport: str) -> Any:
    path = PAYLOADS_DIR / f"{provider}_{sport}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf8"))
    except Exception:
        return None
