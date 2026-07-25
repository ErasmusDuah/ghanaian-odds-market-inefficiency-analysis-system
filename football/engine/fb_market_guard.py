"""
Shared market visibility and sanity guard for football scraper output.

This is intentionally local-only: it does not call bookmaker sites. Scrapers still
own source-specific visibility parsing, while this guard blocks incomplete,
inactive, suspended, hidden, or malformed markets before arb calculation.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any
import re

MIN_DECIMAL_ODDS = 1.01
MAX_DECIMAL_ODDS = 101.0
MAX_LINE_VALUE = 30.0

THREE_WAY_MARKETS = {
    "odds_1x2",
    "odds_1x2_one_up",
    "odds_1x2_two_up",
    "odds_fh_1x2",
    "odds_sh_1x2",
    "odds_corners_1x2",
    "odds_bookings_1x2",
}

TWO_WAY_MARKETS = {
    "odds_gg",
    "odds_gg_2plus",
}

DC_MARKETS = {
    "odds_dc",
    "odds_fh_dc",
    "odds_sh_dc",
}

NESTED_OU_MARKETS = {
    "odds_ou",
    "odds_asian_ou",
    "odds_fh_ou",
    "odds_sh_ou",
    "odds_bookings_ou",
}

REQUIRE_1X2_ANCHOR_PLATFORMS = {"betway", "sportybet"}

REQUIRED_KEYS = {
    **{key: ("home", "draw", "away") for key in THREE_WAY_MARKETS},
    **{key: ("yes", "no") for key in TWO_WAY_MARKETS},
    **{key: ("1x", "12", "x2") for key in DC_MARKETS},
}

FALSEY_VISIBILITY_VALUES = {"0", "false", "no", "n", "hidden", "inactive", "disabled", "closed", "locked", "suspended", "unavailable"}
TRUTHY_BAD_VALUES = {"1", "true", "yes", "y", "hidden", "inactive", "disabled", "closed", "locked", "suspended", "unavailable"}
BAD_STATUS_VALUES = {"hidden", "inactive", "disabled", "closed", "locked", "suspended", "unavailable", "settled", "resulted"}


COUNTRY_TEAM_NAMES = {
    "afghanistan", "albania", "algeria", "andorra", "angola", "argentina", "armenia",
    "australia", "austria", "azerbaijan", "bahrain", "belarus", "belgium", "benin",
    "bolivia", "bosnia", "bosnia and herzegovina", "brazil", "bulgaria", "cameroon",
    "canada", "chile", "china", "colombia", "costa rica", "croatia", "cyprus",
    "czech republic", "denmark", "ecuador", "egypt", "england", "estonia", "finland",
    "france", "georgia", "germany", "ghana", "greece", "guatemala", "honduras",
    "hungary", "iceland", "india", "indonesia", "iran", "iraq", "ireland", "israel",
    "italy", "ivory coast", "japan", "kazakhstan", "kenya", "kosovo", "latvia",
    "lithuania", "luxembourg", "malaysia", "mali", "mexico", "morocco", "netherlands",
    "new zealand", "nigeria", "north macedonia", "northern ireland", "norway", "panama",
    "paraguay", "peru", "poland", "portugal", "qatar", "romania", "saudi arabia",
    "scotland", "senegal", "serbia", "slovakia", "slovenia", "south africa",
    "south korea", "spain", "sweden", "switzerland", "tunisia", "turkey", "ukraine",
    "uruguay", "usa", "united states", "venezuela", "vietnam", "wales", "zambia",
}

TEAM_NAME_HINTS = {
    "academy", "afc", "athletic", "athletico", "b", "club", "city", "county", "fc",
    "fk", "ii", "reserve", "reserves", "sc", "sporting", "town", "u19", "u20", "u21",
    "u23", "united", "women", "youth", "wanderers", "rovers", "rangers", "olympic",
}

PSEUDO_MATCH_PHRASES = {
    "alternative", "duel of the players", "fantasy", "goalscorer", "matches of the day",
    "player duel", "player props", "player specials", "player statistics", "player to",
    "player v team", "player vs team", "score anytime", "shots on target", "specials",
    "team v player", "team vs player", "to score", "penalty taker", "cards by player",
}

GENERIC_SIDE_PATTERNS = [
    re.compile(r"^(?:1st|first|home)\s+teams?$"),
    re.compile(r"^(?:2nd|second|away)\s+teams?$"),
    re.compile(r"^team\s+[ab12]$"),
]

VIRTUAL_KEYWORDS = {
    "srl", "simulated reality", "esport", "e-soccer", "esoccer", "cyber", "virtual",
    "sim match", "eadriatic", "gt league", "efootball", "e-football", "fifa", "pes ",
}


@dataclass
class GuardReport:
    platform: str
    input_matches: int = 0
    output_matches: int = 0
    dropped_matches: int = 0
    dropped_markets: int = 0
    dropped_lines: int = 0
    reasons: dict[str, int] = field(default_factory=dict)

    def add(self, reason: str, amount: int = 1) -> None:
        self.reasons[reason] = self.reasons.get(reason, 0) + amount


def _as_text(value: Any) -> str:
    return str(value).strip().lower()


def _flag_is_bad(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    return _as_text(value) in TRUTHY_BAD_VALUES


def _flag_is_false(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return not value
    return _as_text(value) in FALSEY_VISIBILITY_VALUES


def _object_marked_unavailable(obj: Any) -> bool:
    if not isinstance(obj, dict):
        return False

    for key in ("hidden", "isHidden", "suspended", "isSuspended", "locked", "isLocked", "disabled", "isDisabled"):
        if _flag_is_bad(obj.get(key)):
            return True

    for key in ("active", "isActive", "visible", "isVisible", "available", "isAvailable", "enabled", "isEnabled", "shouldDisplay"):
        if _flag_is_false(obj.get(key)):
            return True

    status = _as_text(obj.get("status") or obj.get("marketStatus") or obj.get("tradingStatus") or obj.get("state") or "")
    return status in BAD_STATUS_VALUES


def _decimal_odds(value: Any) -> float | None:
    if isinstance(value, dict):
        if _object_marked_unavailable(value):
            return None
        for key in ("odds", "price", "decimal", "value"):
            if key in value:
                value = value.get(key)
                break
        else:
            return None
    try:
        odds = float(value)
    except (TypeError, ValueError):
        return None
    if MIN_DECIMAL_ODDS < odds <= MAX_DECIMAL_ODDS:
        return odds
    return None


def _line_key(value: Any) -> str | None:
    try:
        line = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if line < 0 or line > MAX_LINE_VALUE:
        return None
    if line.is_integer():
        return f"{int(line)}.0"
    return str(line)


def _clean_flat_market(market: Any, required: tuple[str, ...]) -> tuple[dict[str, float], str | None]:
    if _object_marked_unavailable(market):
        return {}, "market_unavailable"
    if not isinstance(market, dict):
        return {}, "market_not_dict"

    cleaned: dict[str, float] = {}
    for key in required:
        value = market.get(key)
        odds = _decimal_odds(value)
        if odds is None:
            return {}, f"missing_or_bad_{key}"
        cleaned[key] = odds
    return cleaned, None


def _clean_nested_market(market: Any) -> tuple[dict[str, dict[str, float]], int, int]:
    if _object_marked_unavailable(market) or not isinstance(market, dict):
        return {}, len(market) if isinstance(market, dict) else 0, 0

    cleaned: dict[str, dict[str, float]] = {}
    dropped = 0
    for raw_line, row in market.items():
        line = _line_key(raw_line)
        if line is None or _object_marked_unavailable(row) or not isinstance(row, dict):
            dropped += 1
            continue
        over = _decimal_odds(row.get("over"))
        under = _decimal_odds(row.get("under"))
        if over is None or under is None:
            dropped += 1
            continue
        cleaned[line] = {"over": over, "under": under}
    return cleaned, dropped, len(cleaned)


def _dc_market_is_consistent(dc: dict[str, float], x2: dict[str, float]) -> tuple[bool, str | None]:
    pairs = {
        "1x": ("home", "draw"),
        "12": ("home", "away"),
        "x2": ("draw", "away"),
    }
    for dc_key, (left, right) in pairs.items():
        dc_odds = dc.get(dc_key)
        left_odds = x2.get(left)
        right_odds = x2.get(right)
        if dc_odds is None or left_odds is None or right_odds is None:
            continue
        # A double-chance outcome covers two single outcomes, so its decimal odds
        # must not exceed either corresponding single-outcome price on the same book.
        if dc_odds > min(left_odds, right_odds) + 1e-9:
            return False, f"{dc_key}_exceeds_single_odds"
    return True, None


def _drop_inconsistent_dc_markets(clean: dict[str, Any], report: GuardReport | None = None) -> None:
    anchors = {
        "odds_dc": "odds_1x2",
        "odds_fh_dc": "odds_fh_1x2",
        "odds_sh_dc": "odds_sh_1x2",
    }
    for dc_key, anchor_key in anchors.items():
        dc = clean.get(dc_key) or {}
        anchor = clean.get(anchor_key) or {}
        if not dc or not anchor:
            continue
        ok, reason = _dc_market_is_consistent(dc, anchor)
        if ok:
            continue
        clean[dc_key] = {}
        if report:
            report.dropped_markets += 1
            report.add(f"{dc_key}:{reason}")


def _has_any_market(match: dict[str, Any]) -> bool:
    for key in THREE_WAY_MARKETS | TWO_WAY_MARKETS | DC_MARKETS | NESTED_OU_MARKETS:
        if match.get(key):
            return True
    return False




def _norm_side_name(name: Any) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", str(name).lower())).strip()


def _is_country_side(name: Any) -> bool:
    return _norm_side_name(name) in COUNTRY_TEAM_NAMES


def _is_generic_side(name: Any) -> bool:
    normalized = _norm_side_name(name)
    return any(pattern.match(normalized) for pattern in GENERIC_SIDE_PATTERNS)


def _looks_like_player_name(name: Any) -> bool:
    raw = re.sub(r"\([^)]*\)", " ", str(name)).strip()
    if not raw or any(ch.isdigit() for ch in raw) or "/" in raw:
        return False
    normalized = _norm_side_name(raw)
    if normalized in COUNTRY_TEAM_NAMES:
        return False
    tokens = normalized.split()
    if len(tokens) not in (2, 3):
        return False
    if any(token in TEAM_NAME_HINTS for token in tokens):
        return False
    if any(len(token) <= 1 for token in tokens):
        return False
    return True


def is_pseudo_or_virtual_match(match: dict[str, Any]) -> bool:
    home = match.get("home_team", "")
    away = match.get("away_team", "")
    tournament = match.get("tournament", "")
    combined = f"{home} {away} {tournament}".lower()
    if any(keyword in combined for keyword in VIRTUAL_KEYWORDS):
        return True
    if any(phrase in combined for phrase in PSEUDO_MATCH_PHRASES):
        return True
    if _is_generic_side(home) or _is_generic_side(away):
        return True
    if _looks_like_player_name(home) and _is_country_side(away):
        return True
    if _looks_like_player_name(away) and _is_country_side(home):
        return True
    return False

def sanitize_match(
    match: dict[str, Any],
    report: GuardReport | None = None,
    platform: str | None = None,
) -> dict[str, Any] | None:
    clean = deepcopy(match)

    if _object_marked_unavailable(clean):
        if report:
            report.add("match_unavailable")
        return None

    if is_pseudo_or_virtual_match(clean):
        if report:
            report.dropped_matches += 1
            report.add("match_pseudo_or_virtual")
        return None

    for key, required in REQUIRED_KEYS.items():
        if key not in clean:
            continue
        cleaned_market, reason = _clean_flat_market(clean.get(key), required)
        if cleaned_market:
            clean[key] = cleaned_market
        else:
            if clean.get(key):
                if report:
                    report.dropped_markets += 1
                    report.add(f"{key}:{reason or 'invalid'}")
            clean[key] = {}

    for key in NESTED_OU_MARKETS:
        if key not in clean:
            continue
        cleaned_nested, dropped_lines, kept_lines = _clean_nested_market(clean.get(key))
        if dropped_lines and report:
            report.dropped_lines += dropped_lines
            report.add(f"{key}:line_invalid", dropped_lines)
        if clean.get(key) and not kept_lines and report:
            report.dropped_markets += 1
            report.add(f"{key}:no_complete_lines")
        clean[key] = cleaned_nested

    _drop_inconsistent_dc_markets(clean, report)

    if not _has_any_market(clean):
        if report:
            report.dropped_matches += 1
            report.add("match_no_valid_markets")
        return None

    platform_key = (platform or "").lower()
    if platform_key in REQUIRE_1X2_ANCHOR_PLATFORMS and not clean.get("odds_1x2"):
        if report:
            report.dropped_matches += 1
            report.add("match_missing_1x2_anchor")
        return None

    return clean


def sanitize_platform_matches(platform: str, matches: list[dict[str, Any]] | None) -> tuple[list[dict[str, Any]], GuardReport]:
    report = GuardReport(platform=platform, input_matches=len(matches or []))
    cleaned: list[dict[str, Any]] = []
    for match in matches or []:
        if not isinstance(match, dict):
            report.dropped_matches += 1
            report.add("match_not_dict")
            continue
        clean = sanitize_match(match, report, platform)
        if clean is not None:
            cleaned.append(clean)
    report.output_matches = len(cleaned)
    return cleaned, report


def sanitize_all_platform_matches(raw: dict[str, list[dict[str, Any]] | None]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, GuardReport]]:
    cleaned: dict[str, list[dict[str, Any]]] = {}
    reports: dict[str, GuardReport] = {}
    for platform, matches in raw.items():
        clean, report = sanitize_platform_matches(platform, matches)
        cleaned[platform] = clean
        reports[platform] = report
    return cleaned, reports


def report_has_changes(report: GuardReport) -> bool:
    return bool(report.dropped_matches or report.dropped_markets or report.dropped_lines)


def compact_report_line(report: GuardReport) -> str:
    return (
        f"{report.platform}: {report.input_matches}->{report.output_matches} matches, "
        f"dropped {report.dropped_markets} market(s), {report.dropped_lines} line(s)"
    )
