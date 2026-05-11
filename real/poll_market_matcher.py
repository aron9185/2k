from __future__ import annotations

import argparse
import csv
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fair_odds import MarketQuote, consensus_snapshot
from sportsbook_catalog import canonical_book_name, get_source


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_POLLS_CSV = BASE_DIR / "live_polls.csv"
DEFAULT_MARKETS_CSV = BASE_DIR / "sportsbook_markets.csv"
DEFAULT_MATCHES_CSV = BASE_DIR / "poll_market_matches.csv"
DEFAULT_EVALS_CSV = BASE_DIR / "poll_market_evals.csv"

TEAM_ALIASES = {
    "ana": "ana",
    "anaducks": "ana",
    "ari": "ari",
    "aridiamondbacks": "ari",
    "ars": "ars",
    "arsenal": "ars",
    "arsenalfc": "ars",
    "atl": "atl",
    "atlbraves": "atl",
    "atlhawks": "atl",
    "athletics": "oak",
    "atleticomadrid": "atm",
    "bal": "bal",
    "balorioles": "bal",
    "bos": "bos",
    "bosbruins": "bos",
    "bosceltics": "bos",
    "bosredsox": "bos",
    "bkn": "bkn",
    "brk": "bkn",
    "bk": "bkn",
    "buf": "buf",
    "bufsabres": "buf",
    "car": "car",
    "carhurricanes": "car",
    "cbj": "cbj",
    "cbjbluejackets": "cbj",
    "cgy": "cgy",
    "cgyflames": "cgy",
    "chi": "chi",
    "chiblackhawks": "chi",
    "bayernmunchen": "fcb",
    "bayernmunich": "fcb",
    "cha": "cha",
    "cho": "cha",
    "chahornets": "cha",
    "chc": "chc",
    "chicubs": "chc",
    "chw": "chw",
    "cws": "chw",
    "chiwhitesox": "chw",
    "cin": "cin",
    "cinreds": "cin",
    "cle": "cle",
    "clecavaliers": "cle",
    "cleguardians": "cle",
    "col": "col",
    "colavalanche": "col",
    "colrockies": "col",
    "dal": "dal",
    "dalmavericks": "dal",
    "dalmavs": "dal",
    "dalstars": "dal",
    "den": "den",
    "dennuggets": "den",
    "det": "det",
    "detpistons": "det",
    "detredwings": "det",
    "dettigers": "det",
    "edm": "edm",
    "edmoilers": "edm",
    "fla": "fla",
    "flapanthers": "fla",
    "gs": "gsw",
    "gsw": "gsw",
    "gswarriors": "gsw",
    "fcb": "fcb",
    "fcbayern": "fcb",
    "fcbayernmunchen": "fcb",
    "fcbayernmunich": "fcb",
    "hou": "hou",
    "houastros": "hou",
    "hourockets": "hou",
    "ind": "ind",
    "indpacers": "ind",
    "kc": "kc",
    "kcroyals": "kc",
    "lac": "lac",
    "lacclippers": "lac",
    "la": "lac",
    "laa": "laa",
    "laangels": "laa",
    "lad": "lad",
    "ladodgers": "lad",
    "lak": "lak",
    "lakings": "lak",
    "lal": "lal",
    "lalakers": "lal",
    "las": "las",
    "lva": "lva",
    "lv": "vgk",
    "lvaces": "lva",
    "mia": "mia",
    "miaheat": "mia",
    "miamarlins": "mia",
    "mil": "mil",
    "milbucks": "mil",
    "milbrewers": "mil",
    "min": "min",
    "mintwins": "min",
    "mintimberwolves": "min",
    "minwild": "min",
    "mtl": "mtl",
    "mtlcanadiens": "mtl",
    "nsh": "nsh",
    "nshpredators": "nsh",
    "nyl": "nyl",
    "nyliberty": "nyl",
    "njdevils": "njd",
    "njd": "njd",
    "njddevils": "njd",
    "nyi": "nyi",
    "nyiislanders": "nyi",
    "nyislanders": "nyi",
    "ny": "nyk",
    "nyknicks": "nyk",
    "nym": "nym",
    "nymets": "nym",
    "nyk": "nyk",
    "nyy": "nyy",
    "nyyankees": "nyy",
    "nyr": "nyr",
    "nyrangers": "nyr",
    "oak": "oak",
    "okc": "okc",
    "okcthunder": "okc",
    "ott": "ott",
    "ottsenators": "ott",
    "orl": "orl",
    "orlmagic": "orl",
    "phi": "phi",
    "phi76ers": "phi",
    "phiflyers": "phi",
    "phiphillies": "phi",
    "phisixers": "phi",
    "phl": "phi",
    "pho": "phx",
    "phomercury": "phx",
    "phx": "phx",
    "phxsuns": "phx",
    "pit": "pit",
    "pitpenguins": "pit",
    "pitpirates": "pit",
    "por": "por",
    "portrailblazers": "por",
    "psg": "psg",
    "parisstg": "psg",
    "parissaintgermain": "psg",
    "parisstgermain": "psg",
    "sa": "sas",
    "sd": "sdp",
    "sdp": "sdp",
    "sdpadres": "sdp",
    "sea": "sea",
    "seakraken": "sea",
    "seamariners": "sea",
    "sj": "sj",
    "sjsharks": "sj",
    "sjs": "sj",
    "sjssharks": "sj",
    "sas": "sas",
    "saspurs": "sas",
    "sf": "sfg",
    "sfg": "sfg",
    "sfgiants": "sfg",
    "gsv": "gsv",
    "gsvalkyries": "gsv",
    "gsvvalkyries": "gsv",
    "stl": "stl",
    "stlcardinals": "stl",
    "stlblues": "stl",
    "tb": "tb",
    "tbl": "tb",
    "tblightning": "tb",
    "tbrays": "tb",
    "tex": "tex",
    "texrangers": "tex",
    "tor": "tor",
    "tortempo": "tor",
    "torbluejays": "tor",
    "tormapleleafs": "tor",
    "torraptors": "tor",
    "uta": "uta",
    "utah": "uta",
    "utahjazz": "uta",
    "utamammoth": "uta",
    "van": "van",
    "vancanucks": "van",
    "veg": "vgk",
    "vgk": "vgk",
    "vgkgoldenknights": "vgk",
    "was": "was",
    "wasmystics": "was",
    "wascapitals": "was",
    "wasnationals": "was",
    "wsh": "was",
    "wpg": "wpg",
    "wpgjets": "wpg",
    "con": "con",
    "consun": "con",
}

TEAM_ALIASES.update(
    {
        "anaheimducks": "ana",
        "arizonadiamondbacks": "ari",
        "atlantabraves": "atl",
        "atlantadream": "atl",
        "atlantahawks": "atl",
        "baltimoreorioles": "bal",
        "bostonbruins": "bos",
        "bostonceltics": "bos",
        "bostonredsox": "bos",
        "brooklynnets": "bkn",
        "buffalosabres": "buf",
        "calgaryflames": "cgy",
        "carolinahurricanes": "car",
        "charlottehornets": "cha",
        "chicagoblackhawks": "chi",
        "chicagobulls": "chi",
        "chicagocubs": "chc",
        "chicagosky": "chi",
        "chicagowhitesox": "chw",
        "cincinnatireds": "cin",
        "clevelandcavaliers": "cle",
        "clevelandguardians": "cle",
        "coloradoavalanche": "col",
        "coloradorockies": "col",
        "columbusbluejackets": "cbj",
        "dallasmavericks": "dal",
        "dallasstars": "dal",
        "dallaswings": "dal",
        "denvernuggets": "den",
        "detroitpistons": "det",
        "detroitredwings": "det",
        "detroittigers": "det",
        "edmontonoilers": "edm",
        "floridapanthers": "fla",
        "goldenstatewarriors": "gsw",
        "houstonastros": "hou",
        "houstonrockets": "hou",
        "indianapacers": "ind",
        "indianafever": "ind",
        "kansascityroyals": "kc",
        "losangelesangels": "laa",
        "losangelesclippers": "lac",
        "losangelesdodgers": "lad",
        "losangeleskings": "lak",
        "losangeleslakers": "lal",
        "lasvegasaces": "lva",
        "losangelessparks": "las",
        "lasparks": "las",
        "lassparks": "las",
        "memphisgrizzlies": "mem",
        "miamiheat": "mia",
        "miamimarlins": "mia",
        "milwaukeebrewers": "mil",
        "milwaukeebucks": "mil",
        "minnesotatimberwolves": "min",
        "minnesotatwins": "min",
        "minnesotawild": "min",
        "minnesotalynx": "min",
        "montrealcanadiens": "mtl",
        "nashvillepredators": "nsh",
        "newjerseydevils": "njd",
        "neworleanspelicans": "nop",
        "newyorkislanders": "nyi",
        "newyorkknicks": "nyk",
        "newyorkliberty": "nyl",
        "newyorkmets": "nym",
        "newyorkrangers": "nyr",
        "newyorkyankees": "nyy",
        "oklahomacitythunder": "okc",
        "orlandomagic": "orl",
        "philadelphia76ers": "phi",
        "philadelphiaflyers": "phi",
        "philadelphiaphillies": "phi",
        "phoenixsuns": "phx",
        "phoenixmercury": "phx",
        "pittsburghpenguins": "pit",
        "pittsburghpirates": "pit",
        "portlandtrailblazers": "por",
        "portlandfire": "por",
        "sacramentokings": "sac",
        "sandiegopadres": "sdp",
        "sanantoniospurs": "sas",
        "sanfranciscogiants": "sfg",
        "sanjosesharks": "sj",
        "seattlekraken": "sea",
        "seattlemariners": "sea",
        "seattlestorm": "sea",
        "stlouisblues": "stl",
        "stlouiscardinals": "stl",
        "tampabaylightning": "tb",
        "tampabayrays": "tb",
        "texasrangers": "tex",
        "torontobluejays": "tor",
        "torontomapleleafs": "tor",
        "torontotempo": "tor",
        "torontoraptors": "tor",
        "utahjazz": "uta",
        "utahmammoth": "uta",
        "utahhockeyclub": "uta",
        "vancouvercanucks": "van",
        "vegasgoldenknights": "vgk",
        "washingtoncapitals": "was",
        "washingtonmystics": "was",
        "washingtonnationals": "was",
        "washingtonwizards": "was",
        "connecticutsun": "con",
        "goldenstatevalkyries": "gsv",
        "winnipegjets": "wpg",
        "che": "che",
        "chelsea": "che",
        "hsv": "hsv",
        "hamburg": "hsv",
        "hamburgsv": "hsv",
        "hamburgersv": "hsv",
        "scf": "scf",
        "freiburg": "scf",
        "scfreiburg": "scf",
        "sportclubfreiburg": "scf",
        "hdh": "hdh",
        "heidenheim": "hdh",
        "fcheidenheim": "hdh",
        "fcheidenheim1846": "hdh",
        "koe": "koe",
        "koln": "koe",
        "cologne": "koe",
        "fccologne": "koe",
        "fckoln": "koe",
        "rom": "rom",
        "asroma": "rom",
        "par": "par",
        "parma": "par",
        "parmacalcio": "par",
        "parisfc": "par",
        "get": "get",
        "getafe": "get",
        "ovi": "ovi",
        "oviedo": "ovi",
        "realoviedo": "ovi",
        "fcu": "fcu",
        "unionberlin": "fcu",
        "fcunionberlin": "fcu",
        "1fcunionberlin": "fcu",
        "m05": "m05",
        "mainz": "m05",
        "mainz05": "m05",
        "fsvmainz05": "m05",
        "1fsvmainz05": "m05",
        "bre": "bre",
        "brest": "bre",
        "stadebrest": "bre",
        "atx": "atx",
        "aus": "atx",
        "austin": "atx",
        "austinfc": "atx",
        "houstondynamo": "hou",
        "houdynamo": "hou",
        "laf": "laf",
        "losangelesfc": "laf",
        "losangelesfootballclub": "laf",
        "lafc": "laf",
        "ath": "ath",
        "athleticclub": "ath",
        "athleticbilbao": "ath",
        "athleticclubbilbao": "ath",
        "val": "val",
        "valencia": "val",
        "valenciacf": "val",
        "ata": "ata",
        "atalanta": "ata",
        "milan": "mil",
        "acmilan": "mil",
        "cre": "cre",
        "cremonese": "cre",
        "uscremonese": "cre",
        "eve": "eve",
        "everton": "eve",
        "fio": "fio",
        "fiorentina": "fio",
        "laz": "laz",
        "lazio": "laz",
        "mancity": "mci",
        "manchestercity": "mci",
        "mci": "mci",
        "bre": "bre",
        "brentford": "bre",
        "nfo": "nfo",
        "nottinghamforest": "nfo",
        "nottmforest": "nfo",
        "mun": "mun",
        "manutd": "mun",
        "manchesterunited": "mun",
        "int": "int",
        "inter": "int",
        "intermilan": "int",
        "internazionale": "int",
        "internazionalemilano": "int",
        "juv": "juv",
        "juventus": "juv",
        "lec": "lec",
        "lecce": "lec",
        "bet": "bet",
        "betis": "bet",
        "realbetis": "bet",
        "cel": "cel",
        "celta": "cel",
        "celtavigo": "cel",
        "realsociedad": "rso",
        "rom": "rom",
        "roma": "rom",
        "rso": "rso",
        "sev": "sev",
        "sevilla": "sev",
        "tot": "tot",
        "tottenham": "tot",
        "tottenhamhotspur": "tot",
        "tottenhamhotspurfc": "tot",
        "lee": "lee",
        "leeds": "lee",
        "leedsunited": "lee",
        "leedsunitedfc": "lee",
        "gir": "gir",
        "girona": "gir",
        "gironafc": "gir",
        "ray": "ray",
        "rayo": "ray",
        "vallecano": "ray",
        "rayovallecano": "ray",
        "rayovallecanodemadrid": "ray",
        "ren": "ren",
        "rennes": "ren",
        "staderennes": "ren",
        "wob": "wob",
        "wolfsburg": "wob",
        "vflwolfsburg": "wob",
        "clt": "clt",
        "charlottefc": "clt",
        "fccincinnati": "cin",
        "cincinnatifc": "cin",
        "atlantaunited": "atl",
        "atlantautd": "atl",
        "lag": "lag",
        "lagalaxy": "lag",
        "losangelesgalaxy": "lag",
        "rsl": "rsl",
        "realsaltlake": "rsl",
        "fcdallas": "dal",
        "ne": "ne",
        "newengland": "ne",
        "newenglandrevolution": "ne",
        "newenglandrevs": "ne",
        "philadelphia": "phi",
        "philadelphiaunion": "phi",
        "dc": "dc",
        "dcunited": "dc",
        "dcunitedfc": "dc",
        "dcutd": "dc",
        "nashvillesc": "nsh",
        "orlandocity": "orl",
        "orlandocitysc": "orl",
        "portlandtimbers": "por",
        "portimbers": "por",
        "seattlesounders": "sea",
        "seattlesoundersfc": "sea",
        "sanjoseearthquakes": "sj",
        "vancouverwhitecaps": "van",
        "vancouverwhitecapsfc": "van",
        "skc": "skc",
        "sportingkc": "skc",
        "sportingkansascity": "skc",
        "sdg": "sdg",
        "sandiegofc": "sdg",
        "intermiami": "mia",
        "intermiamicf": "mia",
        "torontofc": "tor",
        "kansascity": "skc",
        "colrapids": "col",
        "coloradorapids": "col",
        "colorado": "col",
        "austinfc": "atx",
        "minnesotautd": "min",
        "newyorkcityfc": "nyc",
        "nycfc": "nyc",
        "nyr": "nyr",
        "nyrb": "nyr",
        "newyorkredbulls": "nyr",
        "chicagofire": "chi",
        "clb": "clb",
        "columbuscrew": "clb",
        "columbuscrewsc": "clb",
        "stlouiscity": "stl",
        "stlouiscitysc": "stl",
        "stlouissc": "stl",
        "minnesotaunited": "min",
        "cfmontreal": "mtl",
        "lafc": "laf",
    }
)

STAT_ALIASES = {
    "pitchingstrikeouts": "strikeouts",
    "strikeouts": "strikeouts",
    "ks": "strikeouts",
    "playerstrikeouts": "strikeouts",
    "hitsrunsrunsbattedin": "hitsrunsrbis",
    "hitsrunsrbis": "hitsrunsrbis",
    "hrrbi": "hitsrunsrbis",
    "runsbattedin": "rbis",
    "rbi": "rbis",
    "rbis": "rbis",
    "totalbases": "totalbases",
    "saves": "saves",
    "shots": "shots",
    "shotsongoal": "shots",
    "threepointersmade": "madethrees",
    "threepointsmade": "madethrees",
    "3pointersmade": "madethrees",
    "madethrees": "madethrees",
    "chancescreated": "chancescreated",
    "chancecreated": "chancescreated",
    "keypasses": "chancescreated",
    "shotsassisted": "chancescreated",
    "total": "total",
    "totalpoints": "total",
    "totalruns": "total",
    "totalgoals": "total",
}

MARKET_TYPE_ALIASES = {
    "player": "player_over_under",
    "player_over_under": "player_over_under",
    "playerprop": "player_over_under",
    "prop": "player_over_under",
    "firstbasket": "first_basket",
    "first_basket": "first_basket",
    "gamespread": "game_spread",
    "spread": "game_spread",
    "gamewinner": "game_winner",
    "winner": "game_winner",
    "moneyline": "game_winner",
    "halftimeresult": "halftime_result",
    "halftimewinner": "halftime_result",
    "bothteamsscore": "both_teams_score",
    "bothteamstoscore": "both_teams_score",
    "doublechance": "double_chance",
    "totaloverunder": "game_total",
    "gametotal": "game_total",
    "total": "game_total",
}


MARKET_COLUMN_ALIASES = {
    "book": ["book", "sportsbook", "operator"],
    "sport": ["sport"],
    "market_type": ["market_type", "market", "bet_type", "market_name"],
    "stat": ["stat", "market_stat", "prop_stat"],
    "player_name": ["player_name", "player", "name", "description"],
    "line": ["line", "point", "value", "over_under_amount"],
    "home_team": ["home_team", "home", "home_team_key"],
    "away_team": ["away_team", "away", "away_team_key"],
    "over_odds": ["over_odds", "over_price", "price_over"],
    "under_odds": ["under_odds", "under_price", "price_under"],
    "updated_at": ["updated_at", "last_update", "timestamp"],
    "period": ["period", "segment"],
}


@dataclass(frozen=True)
class PollContext:
    poll_id: str
    sport: str
    market_family: str
    stat_key: str
    player_name: str
    line: float | None
    home_team: str
    away_team: str
    period: str
    content_text: str
    over_odds: int | None
    under_odds: int | None
    has_explicit_odds: bool


@dataclass(frozen=True)
class MarketRow:
    raw: dict[str, str]
    book: str
    sport: str
    market_family: str
    stat_key: str
    player_name: str
    line: float | None
    home_team: str
    away_team: str
    over_odds: int | None
    under_odds: int | None
    updated_at: datetime | None
    period: str


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Match Real Sports live polls to sportsbook markets and evaluate each "
            "poll against a fair consensus line."
        )
    )
    parser.add_argument("--polls-csv", default=str(DEFAULT_POLLS_CSV))
    parser.add_argument("--markets-csv", default=str(DEFAULT_MARKETS_CSV))
    parser.add_argument("--matches-output", default=str(DEFAULT_MATCHES_CSV))
    parser.add_argument("--evals-output", default=str(DEFAULT_EVALS_CSV))
    parser.add_argument("--min-score", type=float, default=70.0)
    return parser.parse_args()


def normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    cleaned = []
    for char in text.lower():
        if char.isalnum() or char.isspace():
            cleaned.append(char)
        else:
            cleaned.append(" ")
    return " ".join("".join(cleaned).split())


def clean_player_name(value: str) -> str:
    text = re.sub(r"\s+\([A-Za-z0-9 .'-]{2,20}\)\s*$", "", str(value or "").strip())
    return text


def normalize_player_name(value: str) -> str:
    return normalize_text(clean_player_name(value))


def normalize_team(value: str) -> str:
    key = normalize_text(value).replace(" ", "")
    return TEAM_ALIASES.get(key, key)


def normalize_stat(value: str) -> str:
    key = normalize_text(value).replace(" ", "")
    return STAT_ALIASES.get(key, key)


def normalize_market_family(value: str, *, has_player: bool = False) -> str:
    key = normalize_text(value).replace(" ", "").replace("_", "")
    family = MARKET_TYPE_ALIASES.get(key, key)
    if family == key and has_player:
        return "player_over_under"
    return family


def parse_float(value: Any) -> float | None:
    if value in (None, "", "None"):
        return None
    try:
        return float(value)
    except Exception:
        return None


def parse_int(value: Any) -> int | None:
    if value in (None, "", "None"):
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
    try:
        return int(value)
    except Exception:
        try:
            return int(float(value))
        except Exception:
            return None


def parse_datetime(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        return datetime.fromisoformat(text)
    except Exception:
        return None


def infer_poll_player_name(content_text: str, market_family: str, line_value: float | None) -> str:
    if market_family != "player_over_under":
        return ""
    text = str(content_text or "").strip()
    if not text:
        return ""
    match = re.match(r"^(.*?)\s+[0-9]+(?:\.[0-9]+)?\s", text)
    if match:
        return match.group(1).strip(" .·•-")
    if "·" in text:
        return text.split("·", 1)[0].strip()
    return text


def load_csv_rows(path: str | Path) -> list[dict[str, str]]:
    csv_path = Path(path)
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")
    with csv_path.open("r", encoding="utf8", newline="") as handle:
        return list(csv.DictReader(handle))


def get_first(row: dict[str, str], aliases: list[str]) -> str:
    lowered = {str(key).strip().lower(): value for key, value in row.items()}
    for alias in aliases:
        if alias.lower() in lowered and lowered[alias.lower()] not in (None, ""):
            return str(lowered[alias.lower()]).strip()
    return ""


def build_poll_context(row: dict[str, str]) -> PollContext:
    market_family = normalize_market_family(
        row.get("market_type", ""),
        has_player=bool(row.get("player_id")),
    )
    line_value = parse_float(row.get("line"))
    player_name = infer_poll_player_name(row.get("content_text", ""), market_family, line_value)
    return PollContext(
        poll_id=str(row.get("poll_id", "")).strip(),
        sport=normalize_text(row.get("sport", "")).replace(" ", ""),
        market_family=market_family,
        stat_key=normalize_stat(row.get("stat", "")),
        player_name=normalize_player_name(player_name),
        line=line_value,
        home_team=normalize_team(row.get("home_team", "")),
        away_team=normalize_team(row.get("away_team", "")),
        period=str(row.get("period", "")).strip(),
        content_text=row.get("content_text", ""),
        over_odds=parse_int(row.get("over_odds")),
        under_odds=parse_int(row.get("under_odds")),
        has_explicit_odds=str(row.get("has_explicit_odds", "")).strip().lower() == "true",
    )


def build_market_row(row: dict[str, str]) -> MarketRow:
    player_name_raw = get_first(row, MARKET_COLUMN_ALIASES["player_name"])
    sport = normalize_text(get_first(row, MARKET_COLUMN_ALIASES["sport"])).replace(" ", "")
    stat = normalize_stat(get_first(row, MARKET_COLUMN_ALIASES["stat"]))
    market_family = normalize_market_family(
        get_first(row, MARKET_COLUMN_ALIASES["market_type"]),
        has_player=bool(player_name_raw),
    )
    return MarketRow(
        raw=row,
        book=canonical_book_name(get_first(row, MARKET_COLUMN_ALIASES["book"])),
        sport=sport,
        market_family=market_family,
        stat_key=stat,
        player_name=normalize_player_name(player_name_raw),
        line=parse_float(get_first(row, MARKET_COLUMN_ALIASES["line"])),
        home_team=normalize_team(get_first(row, MARKET_COLUMN_ALIASES["home_team"])),
        away_team=normalize_team(get_first(row, MARKET_COLUMN_ALIASES["away_team"])),
        over_odds=parse_int(get_first(row, MARKET_COLUMN_ALIASES["over_odds"])),
        under_odds=parse_int(get_first(row, MARKET_COLUMN_ALIASES["under_odds"])),
        updated_at=parse_datetime(get_first(row, MARKET_COLUMN_ALIASES["updated_at"])),
        period=str(get_first(row, MARKET_COLUMN_ALIASES["period"])).strip(),
    )


def team_pair(a: str, b: str) -> tuple[str, str]:
    values = sorted([normalize_team(a), normalize_team(b)])
    return values[0], values[1]


def score_match(poll: PollContext, market: MarketRow) -> tuple[float, list[str]]:
    reasons: list[str] = []
    if poll.sport != market.sport:
        return 0.0, ["sport mismatch"]
    if poll.market_family != market.market_family:
        return 0.0, ["market family mismatch"]

    score = 20.0
    reasons.append("sport+market")

    if team_pair(poll.home_team, poll.away_team) == team_pair(market.home_team, market.away_team):
        score += 25.0
        reasons.append("same game")

    if poll.period and market.period and poll.period == market.period:
        score += 5.0
        reasons.append("same period")

    if poll.market_family == "player_over_under":
        if poll.player_name and market.player_name:
            if poll.player_name == market.player_name:
                score += 35.0
                reasons.append("same player")
            elif poll.player_name in market.player_name or market.player_name in poll.player_name:
                score += 20.0
                reasons.append("player partial")
            else:
                return 0.0, ["player mismatch"]
        if poll.stat_key and market.stat_key:
            if poll.stat_key == market.stat_key:
                score += 20.0
                reasons.append("same stat")
            else:
                return 0.0, ["stat mismatch"]
    elif poll.market_family == "game_total":
        score += 20.0
        reasons.append("game total")
    else:
        return 0.0, ["unsupported market family"]

    if poll.line is not None and market.line is not None:
        line_diff = abs(poll.line - market.line)
        score += max(0.0, 15.0 - (line_diff * 10.0))
        reasons.append(f"line diff={line_diff:.2f}")

    if market.over_odds is not None and market.under_odds is not None:
        score += 5.0
        reasons.append("two-way odds")

    return score, reasons


def to_market_quote(row: MarketRow) -> MarketQuote | None:
    if row.line is None or row.over_odds is None or row.under_odds is None:
        return None
    return MarketQuote(
        book=row.book or "unknown",
        line=row.line,
        over_odds=row.over_odds,
        under_odds=row.under_odds,
        updated_at=row.updated_at,
    )


def write_csv(path: str | Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def match_polls_to_markets(
    polls_csv: str | Path,
    markets_csv: str | Path,
    *,
    min_score: float = 70.0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    poll_rows = load_csv_rows(polls_csv)
    market_rows = [build_market_row(row) for row in load_csv_rows(markets_csv)]

    match_rows: list[dict[str, Any]] = []
    eval_rows: list[dict[str, Any]] = []

    for poll_row in poll_rows:
        poll = build_poll_context(poll_row)
        candidates: list[tuple[float, list[str], MarketRow]] = []
        for market in market_rows:
            score, reasons = score_match(poll, market)
            if score >= min_score:
                candidates.append((score, reasons, market))

        candidates.sort(
            key=lambda item: (
                -item[0],
                item[2].book,
                999 if item[2].line is None or poll.line is None else abs(item[2].line - poll.line),
            )
        )

        for score, reasons, market in candidates:
            source_info = get_source(market.book)
            match_rows.append(
                {
                    "poll_id": poll.poll_id,
                    "sport": poll.sport,
                    "content_text": poll.content_text,
                    "market_family": poll.market_family,
                    "poll_player_name": poll.player_name,
                    "poll_stat": poll.stat_key,
                    "poll_line": poll.line,
                    "poll_home_team": poll.home_team,
                    "poll_away_team": poll.away_team,
                    "book": market.book,
                    "book_display_name": source_info.display_name if source_info else market.book,
                    "book_category": source_info.category if source_info else "",
                    "book_player_name": market.player_name,
                    "book_stat": market.stat_key,
                    "book_line": market.line,
                    "book_home_team": market.home_team,
                    "book_away_team": market.away_team,
                    "book_over_odds": market.over_odds,
                    "book_under_odds": market.under_odds,
                    "updated_at": market.updated_at.isoformat() if market.updated_at else "",
                    "match_score": round(score, 3),
                    "match_reasons": " | ".join(reasons),
                }
            )

        quotes = [to_market_quote(candidate[2]) for candidate in candidates]
        quotes = [quote for quote in quotes if quote is not None]
        if not quotes or poll.line is None or poll.over_odds is None or poll.under_odds is None:
            eval_rows.append(
                {
                    "poll_id": poll.poll_id,
                    "sport": poll.sport,
                    "content_text": poll.content_text,
                    "market_family": poll.market_family,
                    "poll_player_name": poll.player_name,
                    "poll_stat": poll.stat_key,
                    "poll_line": poll.line,
                    "poll_over_odds": poll.over_odds,
                    "poll_under_odds": poll.under_odds,
                    "poll_has_explicit_odds": poll.has_explicit_odds,
                    "matched_books": 0,
                    "books": "",
                    "fair_line": "",
                    "fair_over_prob": "",
                    "fair_under_prob": "",
                    "fair_over_odds": "",
                    "fair_under_odds": "",
                    "over_ev_percent": "",
                    "under_ev_percent": "",
                    "recommended_side": "",
                    "recommended_ev_percent": "",
                    "recommended_kelly_quarter": "",
                    "estimate_source": "",
                }
            )
            continue

        snapshot = consensus_snapshot(
            quotes,
            target_line=poll.line,
            over_odds=poll.over_odds,
            under_odds=poll.under_odds,
        )
        estimate = snapshot["estimate"]
        over_eval = snapshot["over"]
        under_eval = snapshot["under"]
        recommended = over_eval if over_eval.ev_per_unit >= under_eval.ev_per_unit else under_eval

        eval_rows.append(
            {
                "poll_id": poll.poll_id,
                "sport": poll.sport,
                "content_text": poll.content_text,
                "market_family": poll.market_family,
                "poll_player_name": poll.player_name,
                "poll_stat": poll.stat_key,
                "poll_line": poll.line,
                    "poll_over_odds": poll.over_odds,
                    "poll_under_odds": poll.under_odds,
                    "poll_has_explicit_odds": poll.has_explicit_odds,
                    "matched_books": len(quotes),
                    "books": " | ".join(sorted({quote.book for quote in quotes})),
                    "book_categories": " | ".join(
                        sorted(
                            {
                                source.category
                                for source in (get_source(quote.book) for quote in quotes)
                                if source is not None
                            }
                        )
                    ),
                    "fair_line": round(estimate.fair_line, 4),
                "fair_over_prob": round(estimate.fair_over_prob, 6),
                "fair_under_prob": round(estimate.fair_under_prob, 6),
                "fair_over_odds": estimate.fair_over_odds,
                "fair_under_odds": estimate.fair_under_odds,
                "over_ev_percent": round(over_eval.ev_percent, 4),
                "under_ev_percent": round(under_eval.ev_percent, 4),
                "recommended_side": recommended.side,
                "recommended_ev_percent": round(recommended.ev_percent, 4),
                "recommended_kelly_quarter": round(recommended.kelly_fraction_quarter, 6),
                "estimate_source": estimate.source,
            }
        )

    return match_rows, eval_rows


def main():
    args = parse_args()
    match_rows, eval_rows = match_polls_to_markets(
        args.polls_csv,
        args.markets_csv,
        min_score=args.min_score,
    )

    write_csv(
        args.matches_output,
        match_rows,
        fieldnames=[
            "poll_id",
            "sport",
            "content_text",
            "market_family",
            "poll_player_name",
            "poll_stat",
            "poll_line",
            "poll_home_team",
            "poll_away_team",
            "book",
            "book_display_name",
            "book_category",
            "book_player_name",
            "book_stat",
            "book_line",
            "book_home_team",
            "book_away_team",
            "book_over_odds",
            "book_under_odds",
            "updated_at",
            "match_score",
            "match_reasons",
        ],
    )
    write_csv(
        args.evals_output,
        eval_rows,
        fieldnames=[
            "poll_id",
            "sport",
            "content_text",
            "market_family",
            "poll_player_name",
            "poll_stat",
            "poll_line",
            "poll_over_odds",
            "poll_under_odds",
            "poll_has_explicit_odds",
            "matched_books",
            "books",
            "book_categories",
            "fair_line",
            "fair_over_prob",
            "fair_under_prob",
            "fair_over_odds",
            "fair_under_odds",
            "over_ev_percent",
            "under_ev_percent",
            "recommended_side",
            "recommended_ev_percent",
            "recommended_kelly_quarter",
            "estimate_source",
        ],
    )
    print(f"Saved {len(match_rows)} raw match rows to {args.matches_output}")
    print(f"Saved {len(eval_rows)} poll evaluations to {args.evals_output}")


if __name__ == "__main__":
    main()
