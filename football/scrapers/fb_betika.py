"""
Betika Ghana football prematch odds scraper.

Uses Betika's public sportsbook JSON endpoints. The list endpoint provides the
current football fixtures and each match detail endpoint provides the full set of
markets exposed by the website.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import re
import sys
import threading
import time
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

from curl_cffi import requests

try:
    from .fb_output_formatter import format_match_text_block
except ImportError:
    from fb_output_formatter import format_match_text_block

BASE_URL = "https://api.betika.com.gh/v1"
SITE_URL = "https://www.betika.com.gh/en-gh/sportsbook/soccer"
TIMEZONE = "Africa/Accra"
REQUEST_TIMEOUT = 20
LIST_LIMIT = int(os.getenv("BETIKA_LIST_LIMIT", "500"))
MAX_WORKERS = int(os.getenv("BETIKA_WORKERS", "24"))
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
_thread_local = threading.local()

_EMPTY_MARKETS = {
    "odds_1x2": {}, "odds_dc": {}, "odds_gg": {}, "odds_gg_2plus": {},
    "odds_1x2_two_up": {}, "odds_1x2_one_up": {}, "odds_ou": {}, "odds_asian_ou": {},
    "odds_fh_1x2": {}, "odds_sh_1x2": {}, "odds_fh_ou": {}, "odds_sh_ou": {},
    "odds_fh_dc": {}, "odds_sh_dc": {}, "odds_corners_1x2": {},
    "odds_bookings_1x2": {}, "odds_bookings_ou": {},
}

MARKET_1X2 = {"1": "odds_1x2", "60": "odds_fh_1x2", "136": "odds_bookings_1x2", "162": "odds_corners_1x2"}
MARKET_DC = {"10": "odds_dc", "63": "odds_fh_dc"}
MARKET_OU = {"18": "odds_ou", "68": "odds_fh_ou", "139": "odds_bookings_ou"}
MARKET_GG = {"29": "odds_gg"}


def _headers(referer: str = SITE_URL) -> Dict[str, str]:
    return {
        "Accept": "application/json, text/plain, */*",
        "Referer": referer,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    }


def _session() -> requests.Session:
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = requests.Session(impersonate="chrome120")
        _thread_local.session = session
    return session


def _price(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 1.0:
        return None
    return round(number, 3)


def _line_key(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        value = value.get("total") or value.get("hcp")
    text = str(value or "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    number = float(match.group(0))
    if number % 1.0 == 0.0:
        return f"{int(number)}.0"
    return str(number)


def _truthy_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on", "locked", "suspended", "inactive", "closed", "blocked"}


def _falsey_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return not value
    if value is None:
        return False
    return str(value).strip().lower() in {"0", "false", "no", "off", "inactive", "disabled", "closed", "blocked"}


def _market_unavailable(market: Dict[str, Any]) -> bool:
    status = str(
        market.get("status") or market.get("market_status") or market.get("trading_status") or market.get("state") or ""
    ).strip().lower()
    if status in {"suspended", "closed", "inactive", "blocked", "locked", "unavailable"}:
        return True
    for key in ("suspended", "locked", "is_suspended", "is_locked", "blocked", "is_blocked"):
        if _truthy_flag(market.get(key)):
            return True
    for key in ("active", "is_active", "enabled", "is_enabled", "visible", "is_visible", "can_bet", "bettable"):
        if _falsey_flag(market.get(key)):
            return True
    return False


def _selection_unavailable(selection: Dict[str, Any]) -> bool:
    status = str(
        selection.get("status")
        or selection.get("trading_status")
        or selection.get("odd_status")
        or selection.get("state")
        or ""
    ).strip().lower()
    if status in {"suspended", "closed", "inactive", "blocked", "locked", "unavailable"}:
        return True
    for key in ("suspended", "locked", "is_suspended", "is_locked", "blocked", "is_blocked"):
        if _truthy_flag(selection.get(key)):
            return True
    for key in ("active", "is_active", "enabled", "is_enabled", "visible", "is_visible", "can_bet", "bettable"):
        if _falsey_flag(selection.get(key)):
            return True
    return False
def _odd_price(selection: Dict[str, Any]) -> Optional[float]:
    if _selection_unavailable(selection):
        return None
    return _price(selection.get("odd_value") or selection.get("odds") or selection.get("price"))


def _is_pseudo_match(home: str, away: str, tournament: str = "") -> bool:
    h = home.strip().lower()
    a = away.strip().lower()
    text = f"{h} {a} {tournament.lower()}"
    if not h or not a or h == a:
        return True
    bad_exact = {"1st team", "2nd team", "first team", "second team", "1st teams", "2nd teams"}
    if h in bad_exact or a in bad_exact:
        return True
    bad_fragments = (
        "goalscorer", "player specials", "shots on target", "to score", "score anytime",
        "fantasy", "statistics", "specials", "srl", "esoccer", "cyber", "virtual",
    )
    return any(fragment in text for fragment in bad_fragments)


def _parse_kickoff(raw: Any, tz: ZoneInfo) -> Optional[datetime]:
    text = str(raw or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(text, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=tz)
            return dt.astimezone(tz)
        except ValueError:
            continue
    return None


def _map_1x2(match: Dict[str, Any], market: Dict[str, Any], key: str) -> None:
    mapped: Dict[str, float] = {}
    for sel in market.get("odds") or []:
        price = _odd_price(sel)
        if price is None:
            continue
        name = str(sel.get("display") or sel.get("odd_key") or "").strip().lower()
        if name == "1":
            mapped["home"] = price
        elif name == "x":
            mapped["draw"] = price
        elif name == "2":
            mapped["away"] = price
    if len(mapped) == 3:
        match[key] = mapped


def _map_dc(match: Dict[str, Any], market: Dict[str, Any], key: str) -> None:
    mapped: Dict[str, float] = {}
    for sel in market.get("odds") or []:
        price = _odd_price(sel)
        if price is None:
            continue
        name = str(sel.get("display") or sel.get("odd_key") or "").lower().replace("/", "").replace(" ", "")
        if name in {"1x", "x2", "12"}:
            mapped[name] = price
    if len(mapped) == 3:
        match[key] = mapped


def _map_gg(match: Dict[str, Any], market: Dict[str, Any], key: str) -> None:
    mapped: Dict[str, float] = {}
    for sel in market.get("odds") or []:
        price = _odd_price(sel)
        if price is None:
            continue
        name = str(sel.get("display") or sel.get("odd_key") or "").strip().lower()
        if name == "yes":
            mapped["yes"] = price
        elif name == "no":
            mapped["no"] = price
    if len(mapped) == 2:
        match[key] = mapped


def _put_ou(target: Dict[str, Dict[str, float]], selections: Iterable[Dict[str, Any]], *, main_line_only: bool = False) -> None:
    parsed_lines: Dict[str, Dict[str, float]] = {}
    for sel in selections:
        price = _odd_price(sel)
        parsed = sel.get("parsed_special_bet_value") or {}
        line = _line_key(parsed.get("total") or sel.get("special_bet_value") or sel.get("display") or sel.get("odd_key"))
        if price is None or not line:
            continue
        name = str(sel.get("display") or sel.get("odd_key") or "").lower()
        side = "over" if "over" in name else "under" if "under" in name else None
        if side:
            parsed_lines.setdefault(line, {})[side] = price

    complete_lines = {line: odds for line, odds in parsed_lines.items() if odds.get("over") is not None and odds.get("under") is not None}
    if main_line_only and len(complete_lines) > 1:
        line, odds = _choose_main_ou_line(complete_lines)
        target[line] = odds
        return
    target.update(complete_lines)


def _choose_main_ou_line(lines: Dict[str, Dict[str, float]]) -> Tuple[str, Dict[str, float]]:
    def score(item: Tuple[str, Dict[str, float]]) -> Tuple[float, float]:
        line, odds = item
        over = float(odds.get("over") or 0)
        under = float(odds.get("under") or 0)
        try:
            line_number = abs(float(line))
        except ValueError:
            line_number = 99.0
        # Betika's detail API exposes alternate totals that may not be placeable on the page.
        # The page-visible total is normally the most balanced over/under pair, so keep that one.
        balance = abs((1.0 / over) - (1.0 / under)) if over > 1 and under > 1 else 99.0
        return (balance, line_number)

    return min(lines.items(), key=score)


def _apply_market(match: Dict[str, Any], market: Dict[str, Any]) -> None:
    if _market_unavailable(market):
        return
    market_id = str(market.get("sub_type_id") or "")
    name = str(market.get("name") or "").upper()
    selections = market.get("odds") or []
    if not selections:
        return
    if market_id in MARKET_1X2:
        _map_1x2(match, market, MARKET_1X2[market_id])
    elif market_id in MARKET_DC:
        _map_dc(match, market, MARKET_DC[market_id])
    elif market_id in MARKET_GG:
        _map_gg(match, market, MARKET_GG[market_id])
    elif "BOTH TEAMS" in name and ("2+" in name or "2 OR MORE" in name) and "SCORE" in name:
        _map_gg(match, market, "odds_gg_2plus")
    elif market_id in MARKET_OU:
        _put_ou(match[MARKET_OU[market_id]], selections, main_line_only=True)


def _fetch_events() -> List[Dict[str, Any]]:
    url = f"{BASE_URL}/uo/matches?page=1&limit={LIST_LIMIT}&sport_id=1"
    response = requests.get(url, headers=_headers(), timeout=REQUEST_TIMEOUT, impersonate="chrome120")
    response.raise_for_status()
    data = response.json().get("data") or []
    return data if isinstance(data, list) else list(data.values())


def _fetch_detail(event: Dict[str, Any]) -> List[Dict[str, Any]]:
    match_id = event.get("parent_match_id") or event.get("match_id")
    if not match_id:
        return event.get("odds") or []
    try:
        response = _session().get(f"{BASE_URL}/match?parent_match_id={match_id}", headers=_headers(), timeout=REQUEST_TIMEOUT)
        if response.status_code == 200:
            data = response.json().get("data") or []
            if isinstance(data, list) and data:
                return data
    except Exception:
        pass
    return event.get("odds") or []


def _convert_event(event: Dict[str, Any], markets: List[Dict[str, Any]], tz: ZoneInfo, target_date) -> Optional[Dict[str, Any]]:
    if str(event.get("sport_id") or "") != "1" or event.get("is_esport") or event.get("is_srl"):
        return None
    home = str(event.get("home_team") or "").strip()
    away = str(event.get("away_team") or "").strip()
    tournament = f"{event.get('category')}. {event.get('competition_name')}" if event.get("category") else str(event.get("competition_name") or "Football")
    if _is_pseudo_match(home, away, tournament):
        return None
    kickoff_dt = _parse_kickoff(event.get("start_time"), tz)
    if kickoff_dt is None or kickoff_dt.date() != target_date:
        return None

    match = {
        "event_id": str(event.get("parent_match_id") or event.get("match_id") or ""),
        "home_team": home,
        "away_team": away,
        "kickoff": kickoff_dt.strftime("%Y-%m-%d %H:%M"),
        "tournament": tournament,
        "is_live": False,
        "source": "betika_gh",
    }
    for key, value in _EMPTY_MARKETS.items():
        match[key] = dict(value)

    for market in markets:
        _apply_market(match, market)

    for key in ("odds_ou", "odds_asian_ou", "odds_fh_ou", "odds_sh_ou", "odds_bookings_ou"):
        match[key] = {line: odds for line, odds in match[key].items() if odds.get("over") is not None and odds.get("under") is not None}
    return match


def collect_today_matches() -> List[Dict[str, Any]]:
    tz = ZoneInfo(TIMEZONE)
    target_date = datetime.now(tz).date()
    events = _fetch_events()

    matches: List[Dict[str, Any]] = []
    seen = set()
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_map = {executor.submit(_fetch_detail, event): event for event in events}
        for future in concurrent.futures.as_completed(future_map):
            event = future_map[future]
            try:
                markets = future.result()
            except Exception:
                markets = event.get("odds") or []
            match = _convert_event(event, markets, tz, target_date)
            if not match:
                continue
            key = match["event_id"] or (match["kickoff"], match["home_team"], match["away_team"])
            if key in seen:
                continue
            seen.add(key)
            matches.append(match)

    matches.sort(key=lambda m: (m["kickoff"], m["home_team"], m["away_team"]))
    return matches


def run() -> List[Dict[str, Any]]:
    os.makedirs(_DATA_DIR, exist_ok=True)
    started = time.perf_counter()
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (OSError, ValueError):
            pass

    tz = ZoneInfo(TIMEZONE)
    now_local = datetime.now(tz)
    print("\n" + "* " * 20)
    print("   BETIKA GHANA SCRAPER (today API + detail markets)")
    print(f"   {now_local.strftime('%A, %d %B %Y %H:%M:%S')}")
    print("* " * 20 + "\n")

    matches = collect_today_matches()
    count = len(matches)
    if count == 0:
        print("WARNING: No prematch matches found for today.")
    else:
        print("BETIKA GHANA")
        print(f"Total matches: {count}")
        print("=" * 50)
        head = min(10, count)
        print(f"\nSample (first {head}):")
        for match in matches[:head]:
            print(f"   {match['home_team']} vs {match['away_team']} | {match['kickoff']} | {match['tournament']}")
        if count > head:
            print(f"  ... and {count - head} more")
        print("=" * 50)

    json_path = os.path.join(_DATA_DIR, "betika_odds.json")
    txt_path = os.path.join(_DATA_DIR, "betika_matches.txt")
    with open(json_path, "w", encoding="utf-8") as jf:
        json.dump(matches, jf, ensure_ascii=False, indent=2)
    with open(txt_path, "w", encoding="utf-8") as tf:
        tf.write("BETIKA GHANA - ALL MATCHES\n")
        tf.write(f"Generated: {now_local.strftime('%A, %d %B %Y %H:%M:%S')}\n")
        tf.write(f"Total: {count} matches\n")
        tf.write("=" * 60 + "\n\n")
        if not matches:
            tf.write(f"No prematch football for today ({TIMEZONE}).\n")
        else:
            for match in matches:
                tf.write(format_match_text_block(match))

    elapsed = time.perf_counter() - started
    print(f"Saved to {json_path}")
    print(f"Full list: {txt_path}")
    if count:
        print(f"   Open the .txt file to see all {count} matches!")
    print(f"Scraping completed in {elapsed:.1f}s")
    return matches


def main() -> int:
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
