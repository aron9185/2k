from __future__ import annotations

"""Canonical sportsbook / market-source catalog for the poll EV workflow."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SportsbookSource:
    canonical: str
    display_name: str
    category: str
    default_weight: float
    aliases: tuple[str, ...]


TARGET_SOURCES = (
    SportsbookSource("fanduel", "FanDuel", "sportsbook", 1.00, ("fanduel", "fd")),
    SportsbookSource("draftkings", "DraftKings", "sportsbook", 1.00, ("draftkings", "dk")),
    SportsbookSource("prizepicks", "PrizePicks", "pickem", 0.82, ("prizepicks", "pp")),
    SportsbookSource("underdog", "Underdog", "pickem", 0.82, ("underdog", "ud")),
    SportsbookSource("novig", "Novig", "exchange", 1.05, ("novig",)),
    SportsbookSource("prophetx", "ProphetX", "exchange", 1.08, ("prophetx", "prophet x")),
    SportsbookSource("hardrockbet", "Hard Rock Bet", "sportsbook", 0.95, ("hardrockbet", "hard rock bet", "hardrock", "hard rock be")),
    SportsbookSource("thescorebet", "theScore Bet", "sportsbook", 0.92, ("thescorebet", "thescore", "scorebet")),
    SportsbookSource("fanatics", "Fanatics", "sportsbook", 0.90, ("fanatics", "fanaticsbook", "fanatics sportsbook")),
    SportsbookSource("betmgm", "BetMGM", "sportsbook", 0.95, ("betmgm", "mgm")),
    SportsbookSource("caesars", "Caesars", "sportsbook", 0.95, ("caesars", "czr", "williamhill", "william hill")),
    SportsbookSource("draftkingspick6", "DraftKings Pick6", "pickem", 0.84, ("draftkingspick6", "draftkings pick6", "pick6")),
    SportsbookSource("betr", "betr", "pickem", 0.82, ("betr",)),
    SportsbookSource("sleeper", "Sleeper", "pickem", 0.80, ("sleeper",)),
    SportsbookSource("dabble", "Dabble", "pickem", 0.80, ("dabble",)),
    SportsbookSource("parlayplay", "ParlayPlay", "pickem", 0.80, ("parlayplay", "parlay play")),
    SportsbookSource("bet365", "bet365", "sportsbook", 0.98, ("bet365",)),
    SportsbookSource("fliff", "Fliff", "pickem", 0.80, ("fliff",)),
    SportsbookSource("sportsbookrhodeisland", "Sportsbook Rhode Island", "sportsbook", 0.88, ("sportsbookrhodeisland", "sportsbook rhode island", "rhodeisland", "sportsbookri")),
    SportsbookSource("onyxodds", "Onyx Odds", "aggregator", 1.00, ("onyxodds", "onyx odds")),
    SportsbookSource("circa", "Circa", "sportsbook", 1.30, ("circa", "circasports", "circa sports")),
    SportsbookSource("ballybet", "Bally Bet", "sportsbook", 0.90, ("ballybet", "bally bet", "bally")),
    SportsbookSource("betrivers", "BetRivers", "sportsbook", 0.90, ("betrivers", "betrivers sportsbook", "rivers")),
    SportsbookSource("polymarket", "Polymarket", "prediction_market", 1.10, ("polymarket",)),
    SportsbookSource("kalshi", "Kalshi", "prediction_market", 1.10, ("kalshi",)),
    SportsbookSource("coinbase", "Coinbase", "exchange", 0.95, ("coinbase",)),
    SportsbookSource("rebet", "Rebet", "pickem", 0.82, ("rebet",)),
    SportsbookSource("betparx", "betPARX", "sportsbook", 0.90, ("betparx", "bet parx", "parx")),
    SportsbookSource("sugarhouse", "SugarHouse", "sportsbook", 0.88, ("sugarhouse", "sugar house")),
    SportsbookSource("bovada", "Bovada", "sportsbook", 0.92, ("bovada",)),
    SportsbookSource("bodog", "Bodog", "sportsbook", 0.92, ("bodog",)),
)


_ALIAS_TO_CANONICAL = {}
_CANONICAL_TO_SOURCE = {}
for source in TARGET_SOURCES:
    _CANONICAL_TO_SOURCE[source.canonical] = source
    for alias in source.aliases:
        key = "".join(ch for ch in alias.lower() if ch.isalnum())
        _ALIAS_TO_CANONICAL[key] = source.canonical


def canonical_book_name(value: str) -> str:
    key = "".join(ch for ch in str(value or "").lower() if ch.isalnum())
    return _ALIAS_TO_CANONICAL.get(key, key)


def get_source(value: str) -> SportsbookSource | None:
    canonical = canonical_book_name(value)
    return _CANONICAL_TO_SOURCE.get(canonical)


def is_target_source(value: str) -> bool:
    return get_source(value) is not None


def default_book_weights() -> dict[str, float]:
    return {
        source.canonical: source.default_weight
        for source in TARGET_SOURCES
    }
