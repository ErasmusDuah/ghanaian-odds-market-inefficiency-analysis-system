"""
Scrape today's not-started Soccabet Ghana football odds.

ULTRAFAST - Direct WebSocket connection to Soccabet's real-time feed.
No browser, no Playwright, no DOM parsing. Pure data.

Outputs:
  data/soccabet_odds.json
  data/soccabet_matches.txt
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp

try:
    from scrapers.lzstring import LZString
except ImportError:
    try:
        from lzstring import LZString
    except ImportError:
        import sys
        import os
        sys.path.append(os.path.dirname(os.path.abspath(__file__)))
        from lzstring import LZString


if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

SOURCE = "soccabet_gh"
WS_URL = "wss://www.soccabet.com/ws/"
SPORT_ID_SOCCER = "77"
OU_LINES = ("0.5", "1.5", "2.5", "3.5", "4.5", "5.5")
MIN_GOOD_MATCHES = int(os.getenv("SOCCABET_MIN_GOOD_MATCHES", "50"))
MAX_WS_ATTEMPTS = int(os.getenv("SOCCABET_WS_ATTEMPTS", "3"))
GOOD_SNAPSHOT = "soccabet_last_good.json"

# Soccabet marketTypeId -> our internal market name
# Discovered via WebSocket frame inspection:
#   5521 = 1X2 (3-way)
#   5054 = Over/Under
#   5030 = GG/NG (Both Teams to Score)
#   4978 = unknown (possibly live-specific, skip)
MARKET_TYPE_1X2_LIVE = 5521
MARKET_TYPE_1X2_PRE  = 4102
MARKET_TYPE_OU  = 5054
MARKET_TYPE_GG  = 5030


def banner(now: datetime) -> str:
    line = "Football " * 20
    return (
        f"{line}\n"
        "   SOCCABET GHANA SCRAPER\n"
        f"   {now.strftime('%A, %d %B %Y %H:%M:%S')}\n"
        f"{line}\n"
    )


async def ws_fetch_all(today_str: str, timeout_secs: float = 12.0) -> tuple[dict, dict, dict, dict, dict]:
    """
    Connect to Soccabet WebSocket, subscribe for today's football,
    collect all match and market messages until the stream goes idle.
    
    Returns:
        (matches_by_id, markets_by_match_id, market_types_by_id, tournaments_by_id, categories_by_id)
    """
    matches: dict[int, dict] = {}
    markets: dict[int, list[dict]] = {}  # matchId -> list of market dicts
    market_types: dict[int, dict] = {}
    tournaments: dict[int, dict] = {}
    categories: dict[int, dict] = {}

    headers = {
        "Origin": "https://www.soccabet.com",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        ),
    }

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(
            WS_URL,
            headers=headers,
            timeout=aiohttp.ClientWSTimeout(**{"ws_close": 5.0}),
            heartbeat=20.0,
        ) as ws:
            # Subscribe for today's football with all markets
            subscribe_msg = json.dumps({
                "subscribe": {
                    "object": "sport",
                    "ids": SPORT_ID_SOCCER,
                    "marketfilter": "all",
                    "timerange": today_str,
                }
            })
            await ws.send_str(subscribe_msg)

            # Collect messages until we receive the "init" signal
            # that marks end of the initial data dump, then wait
            # a brief moment for any trailing messages.
            got_init = False
            idle_deadline = None

            while True:
                try:
                    msg = await asyncio.wait_for(ws.receive(), timeout=timeout_secs)
                except (asyncio.TimeoutError, TimeoutError):
                    break

                if msg.type == aiohttp.WSMsgType.TEXT:
                    data_str = msg.data
                    if not data_str.startswith("{"):
                        try:
                            data_str = LZString.decompressFromUTF16(data_str)
                        except Exception:
                            continue
                    try:
                        payload = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    # Process messages array
                    for item in payload.get("messages", []):
                        obj_type = item.get("object")
                        if obj_type == "match":
                            match_id = item.get("id")
                            if match_id is not None:
                                if match_id in matches:
                                    matches[match_id].update(item)
                                else:
                                    matches[match_id] = item
                        elif obj_type == "market_type":
                            market_type_id = item.get("id")
                            if market_type_id is not None:
                                market_types[int(market_type_id)] = item
                        elif obj_type == "tournament":
                            tournament_id = item.get("id")
                            if tournament_id is not None:
                                tournaments[int(tournament_id)] = item
                        elif obj_type == "category":
                            category_id = item.get("id")
                            if category_id is not None:
                                categories[int(category_id)] = item
                        elif obj_type == "market":
                            match_id = item.get("matchId")
                            if match_id is not None:
                                if match_id not in markets:
                                    markets[match_id] = []
                                markets[match_id].append(item)
                        elif obj_type == "init":
                            got_init = True
                            idle_deadline = time.time() + 0.3

                    # Check subscription confirmations
                    sub = payload.get("subscription", {})
                    req = sub.get("request", {})
                    if req.get("object") == "sport":
                        pass  # subscription confirmed

                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break

                # After init signal, give a short window for trailing data
                if got_init and idle_deadline and time.time() > idle_deadline:
                    break

    return matches, markets, market_types, tournaments, categories


def _market_type_name(market_type: dict | None) -> str:
    if not market_type:
        return ""
    return f"{market_type.get('name') or ''} {market_type.get('shortName') or ''}".strip().lower()


def _market_type_is_live(market_type: dict | None) -> bool:
    return bool((market_type or {}).get("isLive"))


def _is_fulltime_1x2_market(mtype: int, market_type: dict | None) -> bool:
    name = _market_type_name(market_type)
    if market_type and not _market_type_is_live(market_type):
        return name in {"1x2 1x2", "1x2"}
    return mtype in (MARKET_TYPE_1X2_LIVE, MARKET_TYPE_1X2_PRE)


def _is_fulltime_dc_market(mtype: int, market_type: dict | None) -> bool:
    name = _market_type_name(market_type)
    if market_type and not _market_type_is_live(market_type):
        return name in {"double chance double chance", "double chance"}
    return mtype == 4216


def _is_fulltime_ou_market(mtype: int, market_type: dict | None) -> bool:
    name = _market_type_name(market_type)
    if market_type and not _market_type_is_live(market_type):
        return name in {"under/over over/under", "under/over"}
    return mtype in (MARKET_TYPE_OU, 4113)


def _is_fulltime_asian_ou_market(mtype: int, market_type: dict | None) -> bool:
    name = _market_type_name(market_type)
    if market_type and not _market_type_is_live(market_type):
        return name == "under/over asian"
    return mtype == 6539


def _is_fulltime_gg_market(mtype: int, market_type: dict | None) -> bool:
    name = _market_type_name(market_type)
    if market_type and not _market_type_is_live(market_type):
        return name in {"both teams to score both teams to score", "both teams to score"}
    return mtype in (MARKET_TYPE_GG, 4105)





def _market_name_contains(market_type: dict | None, *needles: str) -> bool:
    name = _market_type_name(market_type)
    return all(needle.lower() in name for needle in needles)


def _market_name_exact(market_type: dict | None, *names: str) -> bool:
    name = _market_type_name(market_type)
    return name in {candidate.lower() for candidate in names}


def _parse_3way_selections(selections: list[dict]) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for sel in selections:
        outcome = str(sel.get("outcome", "")).strip()
        odds = _decimal_odds(sel)
        if odds <= 1.0:
            continue
        if outcome == "1":
            parsed["home"] = odds
        elif outcome.upper() == "X":
            parsed["draw"] = odds
        elif outcome == "2":
            parsed["away"] = odds
    return parsed if {"home", "draw", "away"} <= parsed.keys() else {}


def _parse_dc_selections(selections: list[dict]) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for sel in selections:
        outcome = str(sel.get("outcome", "")).upper().strip()
        odds = _decimal_odds(sel)
        if odds <= 1.0:
            continue
        if outcome == "1X":
            parsed["1x"] = odds
        elif outcome == "12":
            parsed["12"] = odds
        elif outcome == "X2":
            parsed["x2"] = odds
    return parsed if {"1x", "12", "x2"} <= parsed.keys() else {}


def _line_from_market(mkt: dict, selections: list[dict]) -> str:
    line = str(mkt.get("special", "")).strip()
    if line:
        return line
    for sel in selections:
        desc = str(sel.get("description") or sel.get("name") or sel.get("outcome") or "")
        if "over" in desc.lower() or "under" in desc.lower():
            found = re.search(r"(\d+(?:\.\d+)?)", desc)
            if found:
                return found.group(1)
    return ""


def _put_ou_from_selections(target: dict[str, dict[str, Any]], mkt: dict, selections: list[dict], *, standard_only: bool = False) -> None:
    line = _line_from_market(mkt, selections)
    if not line:
        return
    try:
        line_f = float(line)
        line_str = str(line_f)
    except ValueError:
        return
    if standard_only and line_str not in OU_LINES:
        return
    row: dict[str, Any] = {}
    for sel in selections:
        outcome = str(sel.get("outcome", "")).strip().lower()
        odds = _decimal_odds(sel)
        if odds <= 1.0:
            continue
        if outcome in {"1", "over", "o"}:
            row["over"] = odds
        elif outcome in {"2", "under", "u"}:
            row["under"] = odds
    if {"over", "under"} <= row.keys():
        target[line_str] = row


def _parse_yes_no_selections(selections: list[dict]) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for sel in selections:
        outcome = str(sel.get("outcome", "")).strip().lower()
        odds = _decimal_odds(sel)
        if odds <= 1.0:
            continue
        if outcome in {"1", "yes"}:
            parsed["yes"] = odds
        elif outcome in {"2", "no"}:
            parsed["no"] = odds
    return parsed if {"yes", "no"} <= parsed.keys() else {}

def _flag_text(value: Any) -> str:
    return str(value).strip().lower()


def _flag_true(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    return _flag_text(value) in {"1", "true", "yes", "y", "locked", "suspended", "disabled", "inactive", "closed"}


def _flag_false(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return not value
    return _flag_text(value) in {"0", "false", "no", "n", "locked", "suspended", "disabled", "inactive", "closed"}


def _unavailable(obj: dict | None) -> bool:
    if not isinstance(obj, dict):
        return False
    for key in ("isSuspended", "suspended", "isLocked", "locked", "isDisabled", "disabled", "blocked", "isBlocked"):
        if _flag_true(obj.get(key)):
            return True
    for key in ("isActive", "active", "isVisible", "visible", "isAvailable", "available", "enabled", "isEnabled", "canBet", "bettable"):
        if _flag_false(obj.get(key)):
            return True
    status = _flag_text(obj.get("status") or obj.get("state") or obj.get("tradingStatus") or obj.get("selectionStatus") or "")
    return status in {"locked", "suspended", "disabled", "inactive", "closed", "unavailable", "blocked"}


def _active_selections(selections: list[dict]) -> list[dict]:
    return [sel for sel in selections or [] if not _unavailable(sel)]


def _decimal_odds(sel: dict) -> float:
    try:
        return float(sel.get("odds") or 0)
    except (TypeError, ValueError):
        return 0.0

def _resolve_tournament_name(match_data: dict, tournaments: dict[int, dict] | None, categories: dict[int, dict] | None) -> str:
    tournament_id = match_data.get("tournamentId")
    tournament = (tournaments or {}).get(int(tournament_id or 0), {}) if tournament_id is not None else {}
    category_id = tournament.get("categoryId") or match_data.get("categoryId")
    category = (categories or {}).get(int(category_id or 0), {}) if category_id is not None else {}

    tournament_name = (
        match_data.get("tournamentName")
        or tournament.get("name")
        or ""
    )
    category_name = (
        match_data.get("categoryName")
        or category.get("name")
        or ""
    )

    tournament_name = str(tournament_name).strip()
    category_name = str(category_name).strip()
    if category_name and tournament_name:
        return f"{category_name}. {tournament_name}"
    if tournament_name:
        return tournament_name
    return str(tournament_id or "")


def parse_matches(
    raw_matches: dict[int, dict],
    raw_markets: dict[int, list[dict]],
    today_str: str,
    market_types: dict[int, dict] | None = None,
    tournaments: dict[int, dict] | None = None,
    categories: dict[int, dict] | None = None,
) -> list[dict[str, Any]]:
    """
    Parse raw WebSocket data into normalized match dicts.
    """
    now = datetime.now()
    results: list[dict[str, Any]] = []

    for match_id, match_data in raw_matches.items():
        # Skip live matches
        if match_data.get("isLive"):
            continue
        # Skip suspended matches
        if match_data.get("isSuspended"):
            continue

        # Parse kickoff
        start_ts = match_data.get("startTs")
        if not start_ts:
            continue
        kickoff_dt = datetime.fromtimestamp(start_ts / 1000)
        
        # Only today's matches
        if kickoff_dt.strftime("%Y-%m-%d") != today_str:
            continue
        # Only future matches
        if kickoff_dt <= now:
            continue

        # Team names
        competitors = match_data.get("competitors", [])
        if len(competitors) < 2:
            continue
        home_team = competitors[0]
        away_team = competitors[1]

        # Skip virtual / simulated matches
        combined = f"{home_team} {away_team}".lower()
        virtual_kws = ['srl', 'simulated', 'esport', 'e-soccer', 'esoccer',
                        'cyber', 'virtual', 'efootball', 'e-football']
        if any(kw in combined for kw in virtual_kws):
            continue

        # Tournament metadata is delivered as separate WebSocket objects.
        tournament = _resolve_tournament_name(match_data, tournaments, categories)

        # Parse markets for this match
        match_markets = raw_markets.get(match_id, [])
        
        odds_1x2 = {}
        odds_dc = {}
        odds_ou: dict[str, dict[str, Any]] = {}
        odds_asian_ou: dict[str, dict[str, Any]] = {}
        odds_gg = {}
        odds_gg_2plus = {}
        odds_1x2_one_up = {}
        odds_1x2_two_up = {}
        odds_fh_1x2 = {}
        odds_fh_dc = {}
        odds_fh_ou: dict[str, dict[str, Any]] = {}
        odds_sh_1x2 = {}
        odds_sh_dc = {}
        odds_sh_ou: dict[str, dict[str, Any]] = {}
        odds_corners_1x2 = {}
        odds_bookings_1x2 = {}
        odds_bookings_ou: dict[str, dict[str, Any]] = {}

        for mkt in match_markets:
            if _unavailable(mkt):
                continue
            mtype = int(mkt.get("marketTypeId") or 0)
            market_type = (market_types or {}).get(mtype)
            selections = _active_selections(mkt.get("selections", []))
            if not selections:
                continue
            special = mkt.get("special", "")

            if _market_name_exact(market_type, "1st half - 1x2", "1st half - 1x2 "):
                odds_fh_1x2 = _parse_3way_selections(selections) or odds_fh_1x2
            elif _market_name_exact(market_type, "2nd half - 1x2", "2nd half - 1x2 "):
                odds_sh_1x2 = _parse_3way_selections(selections) or odds_sh_1x2
            elif _market_name_exact(market_type, "1st half - double chance", "1st half - double chance "):
                odds_fh_dc = _parse_dc_selections(selections) or odds_fh_dc
            elif _market_name_exact(market_type, "2nd half - double chance", "2nd half - double chance "):
                odds_sh_dc = _parse_dc_selections(selections) or odds_sh_dc
            elif _market_name_exact(market_type, "1st half - under/over", "1st half - total", "1st half - under/over asian"):
                _put_ou_from_selections(odds_fh_ou, mkt, selections)
            elif _market_name_exact(market_type, "2nd half - under/over", "2nd half - total", "2nd half - under/over asian"):
                _put_ou_from_selections(odds_sh_ou, mkt, selections)
            elif _market_name_exact(market_type, "corner 1x2"):
                odds_corners_1x2 = _parse_3way_selections(selections) or odds_corners_1x2
            elif _market_name_exact(market_type, "booking 1x2"):
                odds_bookings_1x2 = _parse_3way_selections(selections) or odds_bookings_1x2
            elif _market_name_exact(market_type, "total bookings"):
                _put_ou_from_selections(odds_bookings_ou, mkt, selections)
            elif _market_name_contains(market_type, "both teams", "score 2", "yes/no"):
                odds_gg_2plus = _parse_yes_no_selections(selections) or odds_gg_2plus

            elif _is_fulltime_1x2_market(mtype, market_type):
                # 1X2: selections have outcome "1" (home), "X" (draw), "2" (away)
                parsed = {}
                for sel in selections:
                    outcome = str(sel.get("outcome", "")).strip()
                    odds = _decimal_odds(sel)
                    if odds > 1.0:
                        if outcome == "1":
                            parsed["home"] = odds
                        elif outcome in ("X", "x"):
                            parsed["draw"] = odds
                        elif outcome == "2":
                            parsed["away"] = odds
                if {"home", "draw", "away"} <= parsed.keys():
                    odds_1x2 = parsed

            elif _is_fulltime_dc_market(mtype, market_type):
                parsed_dc = {}
                for sel in selections:
                    outcome = str(sel.get("outcome", "")).upper().strip()
                    odds = _decimal_odds(sel)
                    if odds > 1.0:
                        if outcome == "1X":
                            parsed_dc["1x"] = odds
                        elif outcome == "12":
                            parsed_dc["12"] = odds
                        elif outcome == "X2":
                            parsed_dc["x2"] = odds
                if {"1x", "12", "x2"} <= parsed_dc.keys():
                    odds_dc = parsed_dc

            elif _is_fulltime_ou_market(mtype, market_type) or _is_fulltime_asian_ou_market(mtype, market_type):
                # Over/Under: special field contains the line e.g. "2.5"
                target_ou = odds_asian_ou if _is_fulltime_asian_ou_market(mtype, market_type) else odds_ou
                line = special.strip()
                if not line:
                    # Try extracting from selections description
                    for sel in selections:
                        desc = str(sel.get("description", ""))
                        if "over" in desc.lower() or "under" in desc.lower():
                            import re
                            m = re.search(r'(\d+\.?\d*)', desc)
                            if m:
                                line = m.group(1)
                                break
                if not line:
                    continue
                    
                try:
                    line_f = float(line)
                    line_str = str(line_f)
                except ValueError:
                    continue

                # Only standard .5 lines for arb scanning
                if line_str not in OU_LINES:
                    continue

                row: dict[str, Any] = {}
                for sel in selections:
                    outcome = str(sel.get("outcome", "")).strip().lower()
                    odds = _decimal_odds(sel)
                    if odds > 1.0:
                        if outcome in {"1", "over", "o"}:
                            row["over"] = odds
                        elif outcome in {"2", "under", "u"}:
                            row["under"] = odds
                if {"over", "under"} <= row.keys():
                    target_ou[line_str] = row

            elif _is_fulltime_gg_market(mtype, market_type):
                # GG/NG: outcome "1" = Yes, "2" = No
                parsed_gg: dict[str, Any] = {}
                for sel in selections:
                    outcome = str(sel.get("outcome", "")).strip()
                    odds = _decimal_odds(sel)
                    if odds > 1.0:
                        if outcome == "1":
                            parsed_gg["yes"] = odds
                        elif outcome == "2":
                            parsed_gg["no"] = odds
                        elif outcome.lower() == "yes":
                            parsed_gg["yes"] = odds
                        elif outcome.lower() == "no":
                            parsed_gg["no"] = odds
                if {"yes", "no"} <= parsed_gg.keys():
                    odds_gg = parsed_gg

        # Must have at least 1X2 odds
        if not odds_1x2:
            continue

        results.append({
            "home_team": home_team,
            "away_team": away_team,
            "kickoff": kickoff_dt.strftime("%Y-%m-%d %H:%M"),
            "tournament": tournament,
            "is_live": False,
            "status": "Not start",
            "source": SOURCE,
            "odds_1x2": odds_1x2,
            "odds_dc": odds_dc,
            "odds_ou": odds_ou,
            "odds_asian_ou": odds_asian_ou,
            "odds_gg": odds_gg,
            "odds_gg_2plus": odds_gg_2plus,
            "odds_1x2_one_up": odds_1x2_one_up,
            "odds_1x2_two_up": odds_1x2_two_up,
            "odds_fh_1x2": odds_fh_1x2,
            "odds_fh_dc": odds_fh_dc,
            "odds_fh_ou": odds_fh_ou,
            "odds_sh_1x2": odds_sh_1x2,
            "odds_sh_dc": odds_sh_dc,
            "odds_sh_ou": odds_sh_ou,
            "odds_corners_1x2": odds_corners_1x2,
            "odds_bookings_1x2": odds_bookings_1x2,
            "odds_bookings_ou": odds_bookings_ou,
        })

    results.sort(key=lambda x: (x["kickoff"], x["tournament"], x["home_team"]))
    return results


# Use the shared formatter so every football scraper has the same text output.
try:
    from .fb_output_formatter import format_match_text_block
except ImportError:
    from fb_output_formatter import format_match_text_block

def format_txt(matches: list[dict[str, Any]]) -> str:
    return "".join(format_match_text_block(match) for match in matches)


def write_outputs(matches: list[dict[str, Any]], output_dir: Path) -> tuple[Path, Path]:
    os.makedirs(output_dir, exist_ok=True)
    json_path = output_dir / "soccabet_odds.json"
    txt_path = output_dir / "soccabet_matches.txt"
    with open(str(json_path), "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(matches, indent=2, ensure_ascii=False))
        f.write("\n")
    with open(str(txt_path), "w", encoding="utf-8", newline="\n") as f:
        f.write(format_txt(matches))
    return json_path, txt_path


def _snapshot_path(output_dir: Path) -> Path:
    return output_dir / GOOD_SNAPSHOT


def _load_last_good(output_dir: Path, today_str: str) -> list[dict[str, Any]]:
    candidates = [_snapshot_path(output_dir), output_dir / "soccabet_odds.json"]
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, list) or len(data) < MIN_GOOD_MATCHES:
            continue
        same_day = [m for m in data if str(m.get("kickoff", "")).startswith(today_str)]
        if len(same_day) >= MIN_GOOD_MATCHES:
            return same_day
    return []


def _save_last_good(output_dir: Path, matches: list[dict[str, Any]]) -> None:
    if len(matches) < MIN_GOOD_MATCHES:
        return
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        _snapshot_path(output_dir).write_text(json.dumps(matches, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"[WARN] Could not save Soccabet last-good snapshot: {exc}")


def _is_suspiciously_low(matches: list[dict[str, Any]], baseline: list[dict[str, Any]]) -> bool:
    if len(matches) >= MIN_GOOD_MATCHES:
        return False
    if baseline and len(matches) < max(MIN_GOOD_MATCHES, int(len(baseline) * 0.5)):
        return True
    return len(matches) == 0


def save_outputs(matches: list[dict[str, Any]], preferred_dir: Path) -> tuple[Path, Path]:
    try:
        return write_outputs(matches, preferred_dir)
    except OSError:
        fallback = Path(tempfile.gettempdir()) / "soccabet-output"
        print(f"[WARN] Could not write to {preferred_dir}")
        print(f"[WARN] Saved to fallback folder instead: {fallback}")
        return write_outputs(matches, fallback)


def print_summary(matches: list[dict], json_path: Path, txt_path: Path,
                   elapsed: float, n_raw_matches: int, n_raw_markets: int) -> None:
    print()
    print(f"[INFO] WebSocket received: {n_raw_matches} match objects, {n_raw_markets} market objects")
    print()
    print("SOCCABET GHANA")
    print(f"Total matches fetched: {len(matches)}")
    print(f"With 1X2 odds: {sum(1 for m in matches if m.get('odds_1x2'))}")
    print(f"With O/U odds: {sum(1 for m in matches if m.get('odds_ou'))}")
    print(f"With DC odds:  {sum(1 for m in matches if m.get('odds_dc'))}")
    print(f"With GG odds:  {sum(1 for m in matches if m.get('odds_gg'))}")
    print("=" * 50)
    print()
    print("Sample (first 10 matches):")
    for match in matches[:10]:
        print(f"   {match['home_team']} vs {match['away_team']} | {match['kickoff']} | {match['tournament']}")
    if len(matches) > 10:
        print(f"\n  ... and {len(matches) - 10} more matches")
    print("=" * 50)
    print(f"Saved to {json_path.as_posix()}")
    print(f"Full list saved to {txt_path.as_posix()}")
    print(f"   Open the .txt file to see all {len(matches)} matches!")
    print(f"Scraping completed in {elapsed:.1f}s")


async def _async_scrape() -> list[dict]:
    """Core async scraper - connects to WS, fetches, parses, saves."""
    started = time.perf_counter()
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")

    print(banner(now))
    print(f"  [Soccabet] Connecting to WebSocket feed...")

    output_dir = Path(__file__).resolve().parent.parent / "data"
    baseline = _load_last_good(output_dir, today_str)
    if baseline:
        print(f"  [Soccabet] Last-good snapshot available ({len(baseline)} matches)")

    best_matches: list[dict[str, Any]] = []
    best_raw_count = 0
    best_market_count = 0

    for attempt in range(1, max(1, MAX_WS_ATTEMPTS) + 1):
        timeout = 12.0 + (attempt - 1) * 6.0
        raw_matches, raw_markets, market_types, tournaments, categories = await ws_fetch_all(today_str, timeout_secs=timeout)
        n_raw_markets = sum(len(v) for v in raw_markets.values())

        print(f"  [Soccabet] Attempt {attempt}: received {len(raw_matches)} match objects, {n_raw_markets} market updates")
        print(f"  [Soccabet] Parsing and filtering today's prematch football...")

        matches = parse_matches(raw_matches, raw_markets, today_str, market_types, tournaments, categories)
        if len(matches) > len(best_matches):
            best_matches = matches
            best_raw_count = len(raw_matches)
            best_market_count = n_raw_markets
        if not _is_suspiciously_low(matches, baseline):
            break
        print(f"  [Soccabet] Partial feed suspected ({len(matches)} matches); retrying...")

    matches = best_matches
    if _is_suspiciously_low(matches, baseline):
        print(f"  [Soccabet] WARNING: partial fresh feed kept ({len(matches)} matches); stale snapshot ignored to avoid locked/old odds.")
    else:
        _save_last_good(output_dir, matches)
    json_path, txt_path = save_outputs(matches, output_dir)

    elapsed = time.perf_counter() - started
    print_summary(matches, json_path, txt_path, elapsed, best_raw_count, best_market_count)

    return matches


def run() -> list[dict]:
    """Entry point for the intensive/experimental engine - returns match list."""
    return asyncio.run(_async_scrape())


if __name__ == "__main__":
    run()
