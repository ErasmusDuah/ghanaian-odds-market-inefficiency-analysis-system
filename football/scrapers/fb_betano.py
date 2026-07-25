"""
Betano Ghana football prematch odds scraper.

Uses Betano's own sportsbook JSON endpoints. Events come from the website's
"upcoming matches today" feed, and full market coverage comes from each event's
All tab, so the scraper only records markets exposed by Betano's frontend API.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo

from curl_cffi import requests

try:
    from .fb_output_formatter import format_match_text_block
except ImportError:
    from fb_output_formatter import format_match_text_block

BASE_URL = "https://www.betano.com.gh"
TIMEZONE = "Africa/Accra"
REQUEST_TIMEOUT = 25
DETAIL_TAB = os.getenv("BETANO_DETAIL_TAB", "14")
UPCOMING_PATH = "/sport/football/upcoming-matches-today/"
MAX_WORKERS = int(os.getenv("BETANO_WORKERS", "20"))
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
_thread_local = threading.local()

_EMPTY_MARKETS = {
    "odds_1x2": {}, "odds_dc": {}, "odds_gg": {}, "odds_gg_2plus": {},
    "odds_1x2_two_up": {}, "odds_1x2_one_up": {}, "odds_ou": {}, "odds_asian_ou": {},
    "odds_fh_1x2": {}, "odds_sh_1x2": {}, "odds_fh_ou": {}, "odds_sh_ou": {},
    "odds_fh_dc": {}, "odds_sh_dc": {}, "odds_corners_1x2": {},
    "odds_bookings_1x2": {}, "odds_bookings_ou": {},
}


def _headers(referer: str = f"{BASE_URL}/sport/football/") -> Dict[str, str]:
    return {
        "Accept": "application/json, text/plain, */*",
        "Referer": referer,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
        "X-Requested-With": "XMLHttpRequest",
    }


def _new_session() -> requests.Session:
    session = requests.Session(impersonate="chrome120")
    try:
        session.get(
            f"{BASE_URL}{UPCOMING_PATH}",
            headers=_headers(f"{BASE_URL}{UPCOMING_PATH}"),
            timeout=REQUEST_TIMEOUT,
        )
    except Exception:
        pass
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
    try:
        number = float(value)
    except (TypeError, ValueError):
        match = re.search(r"(\d+(?:\.\d+)?)", str(value or ""))
        if not match:
            return None
        number = float(match.group(1))
    if number % 1.0 == 0.0:
        return f"{int(number)}.0"
    return str(number)


def _selection_price(selection: Dict[str, Any]) -> Optional[float]:
    status = str(selection.get("status") or selection.get("tradingStatus") or "").lower()
    if selection.get("isSuspended") or selection.get("suspended") or status in {"suspended", "closed", "inactive", "blocked"}:
        return None
    return _price(selection.get("price") or selection.get("odd") or selection.get("odds"))


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
        "fantasy", "statistics", "specials", "penalty taker", "player to",
    )
    return any(fragment in text for fragment in bad_fragments)


def _event_kickoff(event: Dict[str, Any], tz: ZoneInfo) -> Optional[datetime]:
    raw = event.get("startTime") or event.get("startTimeMillis") or event.get("start")
    if raw is None:
        return None
    try:
        ts = float(raw)
    except (TypeError, ValueError):
        return None
    if ts > 10_000_000_000:
        ts /= 1000.0
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(tz)


def _participants(event: Dict[str, Any]) -> Optional[tuple[str, str]]:
    parts = event.get("participants") or []
    names = [str(p.get("name") or "").strip() for p in parts if isinstance(p, dict) and p.get("name")]
    if len(names) >= 2:
        return names[0], names[1]
    name = str(event.get("name") or event.get("shortName") or "")
    for sep in (" - ", " vs ", " v "):
        if sep in name:
            left, right = name.split(sep, 1)
            return left.strip(), right.strip()
    return None


def _put_ou(target: Dict[str, Dict[str, float]], selections: Iterable[Dict[str, Any]]) -> None:
    for sel in selections:
        price = _selection_price(sel)
        line = _line_key(sel.get("handicap") or sel.get("line") or sel.get("name"))
        if price is None or not line:
            continue
        name = str(sel.get("name") or sel.get("fullName") or "").lower()
        side = "over" if "over" in name else "under" if "under" in name else None
        if side:
            target.setdefault(line, {})[side] = price


def _map_1x2(match: Dict[str, Any], market: Dict[str, Any], key: str) -> None:
    mapped: Dict[str, float] = {}
    home = match["home_team"].lower()
    away = match["away_team"].lower()
    for sel in market.get("selections") or []:
        price = _selection_price(sel)
        if price is None:
            continue
        name = str(sel.get("name") or sel.get("fullName") or "").strip().lower()
        if name == "1" or name == home:
            mapped["home"] = price
        elif name in {"x", "draw", "tie"}:
            mapped["draw"] = price
        elif name == "2" or name == away:
            mapped["away"] = price
    if len(mapped) == 3:
        match[key] = mapped


def _map_dc(match: Dict[str, Any], market: Dict[str, Any], key: str) -> None:
    mapped: Dict[str, float] = {}
    home = match["home_team"].lower()
    away = match["away_team"].lower()
    for sel in market.get("selections") or []:
        price = _selection_price(sel)
        if price is None:
            continue
        name = str(sel.get("name") or sel.get("fullName") or "").lower()
        compact = name.replace(" ", "")
        if compact == "1x" or (home in name and "draw" in name):
            mapped["1x"] = price
        elif compact == "x2" or (away in name and "draw" in name):
            mapped["x2"] = price
        elif compact == "12" or (home in name and away in name):
            mapped["12"] = price
    if len(mapped) == 3:
        match[key] = mapped


def _map_gg(match: Dict[str, Any], market: Dict[str, Any], key: str) -> None:
    mapped: Dict[str, float] = {}
    for sel in market.get("selections") or []:
        price = _selection_price(sel)
        if price is None:
            continue
        name = str(sel.get("name") or sel.get("fullName") or "").lower()
        if name.startswith("yes"):
            mapped["yes"] = price
        elif name.startswith("no"):
            mapped["no"] = price
    if len(mapped) == 2:
        match[key] = mapped


def _apply_market(match: Dict[str, Any], market: Dict[str, Any]) -> None:
    mtype = str(market.get("type") or "").upper()
    name = str(market.get("name") or "").lower()
    selections = market.get("selections") or []
    if not selections and mtype not in {"COF3", "COH3"}:
        return

    if mtype in {"MRES", "MR12"}:
        _map_1x2(match, market, "odds_1x2")
    elif mtype == "H1RS":
        _map_1x2(match, market, "odds_fh_1x2")
    elif mtype == "H2RS":
        _map_1x2(match, market, "odds_sh_1x2")
    elif mtype == "DBLC":
        _map_dc(match, market, "odds_dc")
    elif mtype == "1DBC":
        _map_dc(match, market, "odds_fh_dc")
    elif mtype == "2DBC":
        _map_dc(match, market, "odds_sh_dc")
    elif mtype == "BTSC":
        _map_gg(match, market, "odds_gg")
    elif "both teams" in name and ("2+" in name or "2 or more" in name) and "score" in name:
        _map_gg(match, market, "odds_gg_2plus")
    elif mtype == "HCTG":
        _put_ou(match["odds_ou"], selections)
    elif mtype == "ASOU":
        _put_ou(match["odds_asian_ou"], selections)
    elif mtype == "OUH1":
        _put_ou(match["odds_fh_ou"], selections)
    elif mtype == "OUH2":
        _put_ou(match["odds_sh_ou"], selections)
    elif mtype == "TCOU":
        _put_ou(match["odds_bookings_ou"], selections)
    elif mtype == "TWMC":
        _map_1x2(match, market, "odds_corners_1x2")
    elif mtype == "NTYC":
        _map_1x2(match, market, "odds_bookings_1x2")


def _convert_event(event: Dict[str, Any], tz: ZoneInfo, target_date) -> Optional[Dict[str, Any]]:
    teams = _participants(event)
    if not teams:
        return None
    home, away = teams
    tournament = event.get("leagueName") or event.get("leagueDescription") or "Football"
    region = event.get("regionName") or ""
    tournament_name = f"{region}. {tournament}" if region and region.lower() not in str(tournament).lower() else str(tournament)
    if _is_pseudo_match(home, away, tournament_name):
        return None

    kickoff_dt = _event_kickoff(event, tz)
    if kickoff_dt is None or kickoff_dt.date() != target_date:
        return None

    match = {
        "event_id": str(event.get("id") or ""),
        "home_team": home,
        "away_team": away,
        "kickoff": kickoff_dt.strftime("%Y-%m-%d %H:%M"),
        "tournament": tournament_name,
        "is_live": False,
        "source": "betano_gh",
    }
    for key, value in _EMPTY_MARKETS.items():
        match[key] = dict(value)

    for market in event.get("markets") or []:
        _apply_market(match, market)

    for key in ("odds_ou", "odds_asian_ou", "odds_fh_ou", "odds_sh_ou", "odds_bookings_ou"):
        match[key] = {line: odds for line, odds in match[key].items() if odds.get("over") is not None and odds.get("under") is not None}
    return match


def _fetch_today_payload(session: requests.Session) -> Dict[str, Any]:
    api_url = f"{BASE_URL}/api{UPCOMING_PATH}"
    referer = f"{BASE_URL}{UPCOMING_PATH}"
    last_error = None
    for attempt in range(3):
        try:
            response = session.get(api_url, headers=_headers(referer), timeout=REQUEST_TIMEOUT)
            if response.status_code == 403 or "text/html" in str(response.headers.get("content-type", "")).lower():
                session.get(referer, headers=_headers(referer), timeout=REQUEST_TIMEOUT)
                response = session.get(api_url, headers=_headers(referer), timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            payload = response.json()
            return payload.get("data") or {}
        except Exception as exc:
            last_error = exc
            time.sleep(0.8 * (attempt + 1))
    raise RuntimeError(f"Betano today API failed after retries: {last_error}")


def _detail_session() -> requests.Session:
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = requests.Session(impersonate="chrome120")
        _thread_local.session = session
    return session


def _fetch_detail(event: Dict[str, Any]) -> Dict[str, Any]:
    url = event.get("url")
    if not url:
        return event
    api_url = f"{BASE_URL}/api{url}?bt={DETAIL_TAB}"
    try:
        response = _detail_session().get(api_url, headers=_headers(f"{BASE_URL}{url}?bt={DETAIL_TAB}"), timeout=REQUEST_TIMEOUT)
        if response.status_code == 200:
            detail = (response.json().get("data") or {}).get("event") or {}
            if detail:
                return detail
    except Exception:
        pass
    return event


def collect_today_matches() -> List[Dict[str, Any]]:
    tz = ZoneInfo(TIMEZONE)
    target_date = datetime.now(tz).date()
    session = _new_session()
    data = _fetch_today_payload(session)
    base_events = [event for block in data.get("blocks") or [] for event in block.get("events") or []]

    detailed_events: List[Dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(_fetch_detail, event) for event in base_events]
        for future in concurrent.futures.as_completed(futures):
            try:
                detailed_events.append(future.result())
            except Exception:
                continue

    matches = []
    seen = set()
    for event in detailed_events:
        match = _convert_event(event, tz, target_date)
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
    print("   BETANO GHANA SCRAPER (today API + all-tab details)")
    print(f"   {now_local.strftime('%A, %d %B %Y %H:%M:%S')}")
    print("* " * 20 + "\n")

    try:
        matches = collect_today_matches()
    except Exception as exc:
        print(f"ERROR Betano fetch failed: {exc}")
        matches = []
    count = len(matches)

    if count == 0:
        print("WARNING  No prematch matches found for today.")
    else:
        print("LIST BETANO GHANA")
        print(f"Total matches: {count}")
        print("=" * 50)
        head = min(10, count)
        print(f"\nSample (first {head}):")
        for match in matches[:head]:
            print(f"   {match['home_team']} vs {match['away_team']} | {match['kickoff']} | {match['tournament']}")
        if count > head:
            print(f"  ... and {count - head} more")
        print("=" * 50)

    json_path = os.path.join(_DATA_DIR, "betano_odds.json")
    txt_path = os.path.join(_DATA_DIR, "betano_matches.txt")

    with open(json_path, "w", encoding="utf-8") as jf:
        json.dump(matches, jf, ensure_ascii=False, indent=2)

    with open(txt_path, "w", encoding="utf-8") as tf:
        tf.write("BETANO GHANA - ALL MATCHES\n")
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
