"""Shared helpers for the live Real Sports web API."""

from __future__ import annotations

import json
import os
import time
from glob import glob
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import requests

try:
    from hashids import Hashids
except ImportError as exc:  # pragma: no cover - import error is environment-specific
    raise RuntimeError(
        "Real Sports scripts require the `hashids` package. "
        "Install it with `python -m pip install hashids`."
    ) from exc


BASE_DIR = Path(__file__).resolve().parent
BASE_URL = "https://web.realsports.io"
DEFAULT_ORIGIN = "https://www.realsports.io"
DEFAULT_REFERER = "https://www.realsports.io/"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/135.0.0.0 Safari/537.36"
)
DEFAULT_DEVICE_UUID = "4311295d-c470-419d-a2b3-bfe30c3565ca"
DEFAULT_DEVICE_TYPE = "desktop_web"
DEFAULT_REAL_VERSION = "23"
DEFAULT_AUTH_CACHE_PATH = BASE_DIR / ".realsports_auth_cache.json"
DEFAULT_BROWSER_SESSION_PATH = BASE_DIR / ".realsports_browser_session.json"
AUTH_CACHE_TTL_SECONDS = 12 * 60 * 60
REQUEST_HASH = Hashids(salt="realwebapp", min_length=16)
DEFAULT_BROWSER_LEVELDB_GLOBS = (
    Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data" / "*" / "Local Storage" / "leveldb",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Edge" / "User Data" / "*" / "Local Storage" / "leveldb",
)
USER_ID_MARKER = b"u\x00s\x00e\x00r\x00I\x00d"
TOKEN_MARKER = b"t\x00o\x00k\x00e\x00n"
DEVICE_MARKER = b"d\x00e\x00v\x00i\x00c\x00e"
TROPHIES_MARKER = b"t\x00r\x00o\x00p\x00h\x00i\x00e\x00s"


class RealSportsError(RuntimeError):
    """Raised when a Real Sports API call fails."""


class RealSportsAuthError(RealSportsError):
    """Raised when login credentials are missing or rejected."""


class RealSportsRateLimitError(RealSportsError):
    """Raised when the Real Sports API responds with HTTP 429."""


@dataclass(frozen=True)
class RealSportsAuthInfo:
    user_id: str
    device_id: str
    token: str

    @property
    def header(self) -> str:
        return f"{self.user_id}!{self.device_id}!{self.token}"

    @classmethod
    def from_header(cls, value: str) -> "RealSportsAuthInfo":
        parts = [part.strip() for part in str(value or "").split("!")]
        if len(parts) != 3 or not all(parts):
            raise RealSportsAuthError(
                "REALSPORTS_AUTH_INFO must look like 'userId!deviceId!token'."
            )
        return cls(user_id=parts[0], device_id=parts[1], token=parts[2])


class RealSportsClient:
    def __init__(
        self,
        login: str,
        password: str,
        *,
        base_url: str = BASE_URL,
        origin: str = DEFAULT_ORIGIN,
        referer: str = DEFAULT_REFERER,
        user_agent: str = DEFAULT_USER_AGENT,
        device_name: str | None = None,
        device_uuid: str = DEFAULT_DEVICE_UUID,
        device_type: str = DEFAULT_DEVICE_TYPE,
        real_version: str = DEFAULT_REAL_VERSION,
        auth_cache_path: str | os.PathLike[str] = DEFAULT_AUTH_CACHE_PATH,
        browser_session_path: str | os.PathLike[str] = DEFAULT_BROWSER_SESSION_PATH,
        timeout: int = 20,
        session: requests.Session | None = None,
        seed_auth_info: RealSportsAuthInfo | None = None,
    ) -> None:
        if (not login or not password) and seed_auth_info is None:
            raise RealSportsAuthError(
                "Missing Real Sports credentials. Set REALSPORTS_LOGIN and "
                "REALSPORTS_PASSWORD, or provide REALSPORTS_AUTH_INFO / "
                f"{DEFAULT_BROWSER_SESSION_PATH} before running these scripts."
            )

        self.login_name = login
        self.password = password
        self.base_url = base_url.rstrip("/")
        self.origin = origin.rstrip("/")
        self.referer = referer
        self.user_agent = user_agent
        self.device_name = device_name or user_agent
        self.device_uuid = device_uuid
        self.device_type = device_type
        self.real_version = str(real_version)
        self.auth_cache_path = Path(auth_cache_path)
        self.browser_session_path = Path(browser_session_path)
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.auth_info: RealSportsAuthInfo | None = seed_auth_info
        self.auth_error: RealSportsAuthError | None = None

    @classmethod
    def from_env(cls) -> "RealSportsClient":
        seed_auth_raw = os.environ.get("REALSPORTS_AUTH_INFO", "").strip()
        browser_session_path = os.environ.get(
            "REALSPORTS_BROWSER_SESSION",
            str(DEFAULT_BROWSER_SESSION_PATH),
        )
        browser_session = cls._load_browser_session(Path(browser_session_path))
        if not browser_session:
            browser_session = cls._extract_browser_session_from_local_storage()
            if browser_session:
                cls._save_browser_session(Path(browser_session_path), browser_session)
        seed_auth_info = RealSportsAuthInfo.from_header(seed_auth_raw) if seed_auth_raw else None
        if seed_auth_info is None and browser_session.get("real_auth_info"):
            seed_auth_info = RealSportsAuthInfo.from_header(browser_session["real_auth_info"])
        return cls(
            login=os.environ.get("REALSPORTS_LOGIN", ""),
            password=os.environ.get("REALSPORTS_PASSWORD", ""),
            user_agent=os.environ.get(
                "REALSPORTS_USER_AGENT",
                browser_session.get("user_agent") or DEFAULT_USER_AGENT,
            ),
            device_name=os.environ.get(
                "REALSPORTS_DEVICE_NAME",
                browser_session.get("device_name"),
            ),
            device_uuid=os.environ.get(
                "REALSPORTS_DEVICE_UUID",
                browser_session.get("device_uuid") or DEFAULT_DEVICE_UUID,
            ),
            device_type=os.environ.get(
                "REALSPORTS_DEVICE_TYPE",
                browser_session.get("device_type") or DEFAULT_DEVICE_TYPE,
            ),
            real_version=os.environ.get(
                "REALSPORTS_REAL_VERSION",
                browser_session.get("real_version") or DEFAULT_REAL_VERSION,
            ),
            auth_cache_path=os.environ.get("REALSPORTS_AUTH_CACHE", str(DEFAULT_AUTH_CACHE_PATH)),
            browser_session_path=browser_session_path,
            seed_auth_info=seed_auth_info,
        )

    def _apply_browser_session(self, browser_session: Mapping[str, Any]) -> RealSportsAuthInfo | None:
        if not browser_session:
            return None
        auth_header = str(browser_session.get("real_auth_info", "")).strip()
        if not auth_header:
            return None

        auth_info = RealSportsAuthInfo.from_header(auth_header)
        self.auth_info = auth_info
        self.auth_error = None
        self.device_uuid = str(browser_session.get("device_uuid") or self.device_uuid)
        self.device_type = str(browser_session.get("device_type") or self.device_type)
        self.real_version = str(browser_session.get("real_version") or self.real_version)
        self.user_agent = str(browser_session.get("user_agent") or self.user_agent)
        self.device_name = str(browser_session.get("device_name") or self.device_name)
        return auth_info

    def _load_or_refresh_browser_session(self, *, refresh_from_storage: bool = False) -> RealSportsAuthInfo | None:
        browser_session: dict[str, str] = {}
        if not refresh_from_storage:
            browser_session = self._load_browser_session(self.browser_session_path)
        if not browser_session:
            browser_session = self._extract_browser_session_from_local_storage()
            if browser_session:
                self._save_browser_session(self.browser_session_path, browser_session)
        return self._apply_browser_session(browser_session)

    @staticmethod
    def _load_browser_session(path: Path) -> dict[str, str]:
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        return {str(key): str(value) for key, value in payload.items() if value is not None}

    @staticmethod
    def _save_browser_session(path: Path, payload: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(dict(payload), indent=2, ensure_ascii=False),
            encoding="utf8",
        )

    @staticmethod
    def _extract_soft_wide_value(region: bytes, allowed: str) -> str:
        output: list[str] = []
        allowed_set = set(allowed)
        for index, byte in enumerate(region):
            char = chr(byte)
            if char not in allowed_set:
                continue
            prev_byte = region[index - 1] if index > 0 else 0
            next_byte = region[index + 1] if index + 1 < len(region) else 0
            if prev_byte == 0 or next_byte == 0:
                output.append(char)
        return "".join(output)

    @classmethod
    def _extract_auth_info_from_chunk(cls, chunk: bytes) -> RealSportsAuthInfo | None:
        user_index = chunk.find(USER_ID_MARKER)
        token_index = chunk.find(TOKEN_MARKER)
        device_index = chunk.find(DEVICE_MARKER)
        if min(user_index, token_index, device_index) < 0:
            return None
        if not (user_index < token_index < device_index):
            return None

        trophies_index = chunk.find(TROPHIES_MARKER, device_index)
        if trophies_index < 0:
            trophies_index = min(len(chunk), device_index + 160)

        user_id = cls._extract_soft_wide_value(
            chunk[user_index + len(USER_ID_MARKER):token_index],
            allowed="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-",
        )
        token = cls._extract_soft_wide_value(
            chunk[token_index + len(TOKEN_MARKER):device_index],
            allowed="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-",
        )
        device_id = cls._extract_soft_wide_value(
            chunk[device_index + len(DEVICE_MARKER):trophies_index],
            allowed="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-",
        )
        if not user_id or not token or not device_id:
            return None
        return RealSportsAuthInfo(
            user_id=user_id,
            device_id=device_id,
            token=token,
        )

    @classmethod
    def _extract_browser_session_from_leveldb_dir(cls, leveldb_dir: Path) -> dict[str, str]:
        if not leveldb_dir.exists():
            return {}

        for path in sorted(leveldb_dir.glob("*.ldb"), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                data = path.read_bytes()
            except OSError:
                continue

            start = 0
            while True:
                accounts_index = data.find(b"e-accounts", start)
                if accounts_index < 0:
                    break
                chunk = data[max(0, accounts_index - 96):accounts_index + 1600]
                if b"realsports.io" not in chunk:
                    start = accounts_index + 1
                    continue

                auth_info = cls._extract_auth_info_from_chunk(chunk)
                if auth_info is not None:
                    return {
                        "captured_at": str(int(time.time())),
                        "real_auth_info": auth_info.header,
                        "device_uuid": auth_info.device_id,
                        "device_type": DEFAULT_DEVICE_TYPE,
                        "real_version": DEFAULT_REAL_VERSION,
                        "user_agent": os.environ.get("REALSPORTS_USER_AGENT", DEFAULT_USER_AGENT),
                        "device_name": os.environ.get("REALSPORTS_DEVICE_NAME", DEFAULT_USER_AGENT),
                        "source": str(path),
                    }
                start = accounts_index + 1
        return {}

    @classmethod
    def _extract_browser_session_from_local_storage(cls) -> dict[str, str]:
        explicit_dir = os.environ.get("REALSPORTS_CHROME_LEVELDB_DIR", "").strip()
        if explicit_dir:
            return cls._extract_browser_session_from_leveldb_dir(Path(explicit_dir))

        for pattern in DEFAULT_BROWSER_LEVELDB_GLOBS:
            for matched_dir in sorted(
                glob(str(pattern)),
                key=lambda item: (Path(item).parts[-3] == "Default", item),
                reverse=True,
            ):
                leveldb_dir = Path(matched_dir)
                session = cls._extract_browser_session_from_leveldb_dir(leveldb_dir)
                if session:
                    return session
        return {}

    @staticmethod
    def build_request_token() -> str:
        return REQUEST_HASH.encode(int(time.time() * 1000))

    def _auth_cache_key(self) -> str:
        return f"{self.base_url}|{self.login_name}|{self.device_uuid}|{self.device_type}"

    def _load_cached_auth(self) -> RealSportsAuthInfo | None:
        if not self.auth_cache_path.exists():
            return None

        try:
            payload = json.loads(self.auth_cache_path.read_text(encoding="utf8"))
        except (OSError, json.JSONDecodeError):
            return None

        entry = payload.get(self._auth_cache_key())
        if not isinstance(entry, dict):
            return None

        cached_at = entry.get("cached_at", 0)
        if not isinstance(cached_at, (int, float)):
            return None
        if time.time() - float(cached_at) > AUTH_CACHE_TTL_SECONDS:
            return None

        user_id = entry.get("user_id")
        device_id = entry.get("device_id")
        token = entry.get("token")
        if not user_id or not device_id or not token:
            return None

        return RealSportsAuthInfo(
            user_id=str(user_id),
            device_id=str(device_id),
            token=str(token),
        )

    def _save_cached_auth(self, auth: RealSportsAuthInfo) -> None:
        payload: dict[str, Any] = {}
        if self.auth_cache_path.exists():
            try:
                payload = json.loads(self.auth_cache_path.read_text(encoding="utf8"))
            except (OSError, json.JSONDecodeError):
                payload = {}

        payload[self._auth_cache_key()] = {
            "user_id": auth.user_id,
            "device_id": auth.device_id,
            "token": auth.token,
            "cached_at": int(time.time()),
        }

        self.auth_cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.auth_cache_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf8",
        )

    def _clear_cached_auth(self) -> None:
        if not self.auth_cache_path.exists():
            return

        try:
            payload = json.loads(self.auth_cache_path.read_text(encoding="utf8"))
        except (OSError, json.JSONDecodeError):
            return

        if self._auth_cache_key() not in payload:
            return

        payload.pop(self._auth_cache_key(), None)
        if payload:
            self.auth_cache_path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf8",
            )
        else:
            try:
                self.auth_cache_path.unlink()
            except OSError:
                pass

    def _build_headers(
        self,
        *,
        include_auth: bool,
        content_type: str | None = "application/json",
        extra_headers: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        headers = {
            "accept": "application/json",
            "origin": self.origin,
            "real-device-name": self.device_name,
            "real-device-type": self.device_type,
            "real-device-uuid": self.device_uuid,
            "real-request-token": self.build_request_token(),
            "real-version": self.real_version,
            "referer": self.referer,
            "user-agent": self.user_agent,
        }
        if content_type:
            headers["content-type"] = content_type
        if include_auth:
            headers["real-auth-info"] = self.ensure_login().header
        if extra_headers:
            headers.update(extra_headers)
        return headers

    def ensure_login(self, *, force: bool = False) -> RealSportsAuthInfo:
        if self.auth_info is not None and not force:
            if not self._load_cached_auth():
                self._save_cached_auth(self.auth_info)
            return self.auth_info
        if self.auth_error is not None and not force:
            raise self.auth_error
        if not force:
            cached_auth = self._load_cached_auth()
            if cached_auth is not None:
                self.auth_error = None
                self.auth_info = cached_auth
                return cached_auth
            browser_auth = self._load_or_refresh_browser_session()
            if browser_auth is not None:
                self._save_cached_auth(browser_auth)
                return browser_auth

        payload = {
            "login": self.login_name,
            "password": self.password,
            "tfaAuthCode": None,
            "attestationToken": None,
            "attestChallenge": None,
        }
        try:
            response = self.request(
                "POST",
                "/login",
                json=payload,
                include_auth=False,
            )
        except RealSportsError as exc:
            self.auth_error = RealSportsAuthError(str(exc))
            raise self.auth_error from exc
        data = response.json()
        if not data.get("success"):
            self.auth_error = RealSportsAuthError(data.get("message") or "Real Sports login failed.")
            raise self.auth_error

        user = data.get("user") or {}
        user_id = user.get("id")
        device_id = data.get("deviceId")
        token = data.get("token")
        if not user_id or not device_id or not token:
            self.auth_error = RealSportsAuthError(
                "Real Sports login succeeded but auth fields were incomplete."
            )
            raise self.auth_error

        self.auth_info = RealSportsAuthInfo(
            user_id=str(user_id),
            device_id=str(device_id),
            token=str(token),
        )
        self.auth_error = None
        self._save_cached_auth(self.auth_info)
        return self.auth_info

    def request(
        self,
        method: str,
        path_or_url: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any | None = None,
        data: Any | None = None,
        include_auth: bool = True,
        content_type: str | None = "application/json",
        headers: Mapping[str, str] | None = None,
        _retry_after_auth_reset: bool = False,
    ) -> requests.Response:
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            url = path_or_url
        else:
            url = f"{self.base_url}{path_or_url}"

        response = self.session.request(
            method=method,
            url=url,
            params=params,
            json=json,
            data=data,
            headers=self._build_headers(
                include_auth=include_auth,
                content_type=content_type,
                extra_headers=headers,
            ),
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            if response.status_code == 429:
                message = response.text.strip()
                retry_after = str(response.headers.get("Retry-After", "")).strip()
                if retry_after:
                    message = f"{message} (Retry-After: {retry_after}s)"
                raise RealSportsRateLimitError(
                    f"{method} {url} failed with {response.status_code}: {message}"
                )
            if include_auth and not _retry_after_auth_reset and response.status_code in {401, 403}:
                self.auth_info = None
                self.auth_error = None
                self._clear_cached_auth()
                self._load_or_refresh_browser_session(refresh_from_storage=True)
                return self.request(
                    method,
                    path_or_url,
                    params=params,
                    json=json,
                    data=data,
                    include_auth=include_auth,
                    content_type=content_type,
                    headers=headers,
                    _retry_after_auth_reset=True,
                )
            message = response.text.strip()
            raise RealSportsError(f"{method} {url} failed with {response.status_code}: {message}")
        return response

    def get_json(
        self,
        path_or_url: str,
        *,
        params: Mapping[str, Any] | None = None,
        include_auth: bool = True,
    ) -> dict[str, Any]:
        return self.request(
            "GET",
            path_or_url,
            params=params,
            include_auth=include_auth,
        ).json()

    def get_rankings(
        self,
        sport: str,
        season: str | int,
        *,
        ranking: str = "tertiary",
        entity: str = "player",
        before: int | None = None,
        position: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"season": str(season)}
        if before:
            params["before"] = before
        if position:
            params["position"] = position
        return self.get_json(
            f"/rankings/sport/{sport}/entity/{entity}/ranking/{ranking}",
            params=params,
        )

    def search_players(
        self,
        sport: str,
        *,
        query: str,
        day: str,
        search_type: str = "ratingLineup",
        include_no_one_option: bool = False,
    ) -> dict[str, Any]:
        return self.get_json(
            f"/players/sport/{sport}/search",
            params={
                "day": day,
                "includeNoOneOption": str(include_no_one_option).lower(),
                "query": query,
                "searchType": search_type,
            },
        )

    def get_player_boxscore_splits(
        self,
        *,
        entity_id: int | str,
        sport: str,
        stat_type: int | str,
        value: int | str,
        entity_type: str = "player",
    ) -> dict[str, Any]:
        return self.get_json(
            "/getplayerboxscoresplits",
            params={
                "entityId": entity_id,
                "entityType": entity_type,
                "sport": sport,
                "statType": stat_type,
                "value": value,
            },
        )

    def get_livefeed_posts(
        self,
        feed: str = "all",
        *,
        before: int | str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if before not in (None, ""):
            params["before"] = before
        return self.get_json(
            f"https://web.realapp.com/livefeed/{feed}/posts",
            params=params or None,
        )

    def get_prediction_game_markets(self, sport: str) -> dict[str, Any]:
        sport_key = str(sport or "").strip().lower()
        if not sport_key:
            raise RealSportsError("sport is required for prediction game-markets requests")
        return self.get_json(f"https://web.realapp.com/predictions/gamemarkets/{sport_key}")

    def get_prediction_market_order(
        self,
        market_id: int | str,
        *,
        mode: str = "buy",
    ) -> dict[str, Any]:
        market_key = str(market_id or "").strip()
        if not market_key:
            raise RealSportsError("market_id is required for prediction market-order requests")
        mode_key = str(mode or "").strip().lower()
        if mode_key not in {"buy", "sell"}:
            raise RealSportsError("mode must be 'buy' or 'sell' for prediction market-order requests")
        return self.get_json(
            f"https://web.realapp.com/predictions/marketorder/{market_key}/mode/{mode_key}"
        )

    def get_prediction_position(self, position_id: int | str) -> dict[str, Any]:
        position_key = str(position_id or "").strip()
        if not position_key:
            raise RealSportsError("position_id is required for prediction position requests")
        return self.get_json(f"https://web.realapp.com/predictions/position/{position_key}")

    def get_prediction_open_positions(self) -> dict[str, Any]:
        return self.get_json("https://web.realapp.com/predictions/openpositions")

    def get_home_tab(self, sport: str, *, page: str = "next", cohort: int = 0) -> dict[str, Any]:
        sport_key = str(sport or "").strip().lower()
        if not sport_key:
            raise RealSportsError("sport is required for home-tab requests")
        return self.get_json(f"https://web.realapp.com/home/{sport_key}/{page}?cohort={int(cohort)}")

    def get_game_feed(
        self,
        game_id: int | str,
        *,
        sport: str,
        version: int | str = 2,
        view: str = "recent",
        view_frame: str = "default",
    ) -> dict[str, Any]:
        sport_key = str(sport or "").strip().lower()
        if not sport_key:
            raise RealSportsError("sport is required for game-feed requests")
        game_key = str(game_id or "").strip()
        if not game_key:
            raise RealSportsError("game_id is required for game-feed requests")
        return self.get_json(
            f"https://web.realapp.com/games/{game_key}/sport/{sport_key}/feed",
            params={
                "version": str(version),
                "view": str(view),
                "viewFrame": str(view_frame),
            },
        )

    def get_team_compare(
        self,
        sport: str,
        *,
        first_team_id: int | str,
        first_team_season: int | str,
        first_team_season_type: str,
        second_team_id: int | str,
        second_team_season: int | str,
        second_team_season_type: str,
    ) -> dict[str, Any]:
        sport_key = str(sport or "").strip().lower()
        if not sport_key:
            raise RealSportsError("sport is required for team compare requests")
        return self.get_json(
            f"https://web.realapp.com/teams/sport/{sport_key}/compare",
            params={
                "firstTeamId": str(first_team_id),
                "firstTeamSeason": str(first_team_season),
                "firstTeamSeasonType": str(first_team_season_type),
                "secondTeamId": str(second_team_id),
                "secondTeamSeason": str(second_team_season),
                "secondTeamSeasonType": str(second_team_season_type),
            },
        )

    def get_polls_info_for_sport(self, sport: str) -> dict[str, Any]:
        sport_key = str(sport or "").strip().lower()
        if not sport_key:
            raise RealSportsError("sport is required for sport-polls info requests")
        return self.get_json(f"https://web.realapp.com/polls/sport/{sport_key}/info")

    def get_polls_for_sport_day(
        self,
        sport: str,
        *,
        day: str,
        poll_type: str = "all",
        before: int | str | None = None,
    ) -> dict[str, Any]:
        sport_key = str(sport or "").strip().lower()
        if not sport_key:
            raise RealSportsError("sport is required for sport-polls day requests")
        if not day:
            raise RealSportsError("day is required for sport-polls day requests")
        params: dict[str, Any] = {"day": day, "type": poll_type}
        if before not in (None, ""):
            params["before"] = before
        return self.get_json(f"https://web.realapp.com/polls/sport/{sport_key}/day", params=params)

    def get_post(self, post_id: int | str) -> dict[str, Any]:
        return self.get_json(f"https://web.realapp.com/posts/{post_id}")

    def get_poll(self, poll_id: int | str) -> dict[str, Any]:
        return self.get_json(f"https://web.realapp.com/polls/{poll_id}")

    def get_poll_options(self, poll_id: int | str, *, query: str = "") -> dict[str, Any]:
        poll_key = str(poll_id or "").strip()
        if not poll_key:
            raise RealSportsError("poll_id is required for poll option requests")
        return self.get_json(
            f"https://web.realapp.com/polls/{poll_key}/options",
            params={"query": query},
        )

    def submit_poll_response(
        self,
        poll_id: int | str,
        *,
        poll_option_id: int | str,
        user_pass_ids: list[int | str] | None = None,
        remove_user_pass_ids: list[int | str] | None = None,
        user_pass_sport: str | None = None,
        wager: int | None = None,
        label: str | None = None,
        avatar_source: str | None = None,
        is_clear: bool = False,
    ) -> dict[str, Any]:
        poll_key = str(poll_id or "").strip()
        option_key = str(poll_option_id or "").strip()
        if not poll_key:
            raise RealSportsError("poll_id is required to submit a poll response")
        if not option_key:
            raise RealSportsError("poll_option_id is required to submit a poll response")

        payload: dict[str, Any] = {
            "pollOptionId": poll_option_id,
            "userPassIds": list(user_pass_ids or []),
            "removeUserPassIds": list(remove_user_pass_ids or []),
            "isClear": bool(is_clear),
        }
        if user_pass_sport:
            payload["userPassSport"] = user_pass_sport
        if wager is not None:
            payload["wager"] = int(wager)
        if label is not None:
            payload["label"] = label
        if avatar_source is not None:
            payload["avatarSource"] = avatar_source

        return self.request(
            "PUT",
            f"https://web.realapp.com/polls/{poll_key}",
            json=payload,
        ).json()

    def add_post_comment(
        self,
        post_id: int | str,
        *,
        text: str,
        group_id: int | str | None = 777777777,
        parent_comment_id: int | str | None = None,
    ) -> dict[str, Any]:
        post_key = str(post_id or "").strip()
        comment_text = str(text or "").strip()
        if not post_key:
            raise RealSportsError("post_id is required to add a post comment")
        if not comment_text:
            raise RealSportsError("text is required to add a post comment")
        payload: dict[str, Any] = {
            "groupId": group_id,
            "text": comment_text,
        }
        if parent_comment_id is not None:
            payload["parentCommentId"] = parent_comment_id
        return self.request(
            "POST",
            f"https://web.realapp.com/comments/posts/{post_key}",
            json=payload,
        ).json()


def build_realsports_client() -> RealSportsClient:
    return RealSportsClient.from_env()
