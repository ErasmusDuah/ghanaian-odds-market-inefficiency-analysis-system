"""Betfox Ghana football prematch odds scraper."""
from __future__ import annotations

import copy
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

from curl_cffi import requests

try:
    from .fb_output_formatter import format_match_text_block
except ImportError:
    from fb_output_formatter import format_match_text_block

try:
    from engine.fb_intensive_engine import is_pseudo_match as _engine_is_pseudo_match
except Exception:
    _engine_is_pseudo_match = None

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (OSError, ValueError):
        pass

BASE_URL = "https://www.betfox.com.gh"
TIMEZONE = "Africa/Accra"
SOURCE = "betfox_gh"
REQUEST_TIMEOUT = min(14.0, max(6.0, float(os.getenv("BETFOX_REQUEST_TIMEOUT", "10"))))
MAX_COMPETITION_WORKERS = min(8, max(1, int(os.getenv("BETFOX_COMPETITION_WORKERS", "6"))))
MAX_DETAIL_WORKERS = min(8, max(1, int(os.getenv("BETFOX_DETAIL_WORKERS", "6"))))
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

_EMPTY_MARKETS = {
    "odds_1x2": {}, "odds_dc": {}, "odds_gg": {}, "odds_gg_2plus": {},
    "odds_1x2_two_up": {}, "odds_1x2_one_up": {}, "odds_ou": {}, "odds_asian_ou": {},
    "odds_fh_1x2": {}, "odds_sh_1x2": {}, "odds_fh_ou": {}, "odds_sh_ou": {},
    "odds_fh_dc": {}, "odds_sh_dc": {}, "odds_corners_1x2": {},
    "odds_bookings_1x2": {}, "odds_bookings_ou": {},
}

VIRTUAL_KEYWORDS = (
    "srl", "simulated", "virtual", "esoccer", "e-soccer", "cyber",
    "efootball", "e-football", "fantasy", "player props", "player specials",
)


def _session() -> requests.Session:
    return requests.Session(impersonate="chrome101")


def _headers(referer: str = "/sportsbook/football/today") -> Dict[str, str]:
    return {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": f"{BASE_URL}{referer}",
        "User-Agent": "Mozilla/5.0",
        "x-betr-brand": "betfox.com.gh",
        "x-betr-operator": "bf-group",
    }


def _get_json(session: requests.Session, path: str, *, referer: str = "/sportsbook/football/today", retries: int = 2) -> Any:
    last_exc: Optional[Exception] = None
    url = path if path.startswith("http") else f"{BASE_URL}{path}"
    for attempt in range(max(1, retries + 1)):
        if attempt:
            time.sleep(min(1.5, 0.35 * attempt))
        try:
            response = session.get(url, headers=_headers(referer), timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last_exc = exc
    raise RuntimeError(f"Betfox request failed for {path}: {last_exc}")


def _day_bounds(tz: ZoneInfo) -> tuple[str, str]:
    now = datetime.now(tz)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    end = now.replace(hour=23, minute=59, second=59, microsecond=999000).astimezone(timezone.utc)
    return (
        start.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        end.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
    )


def _dt(raw: Any, tz: ZoneInfo) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(raw or "").replace("Z", "+00:00")).astimezone(tz)
    except ValueError:
        return None


def _price(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 1.0 or number > 101.0:
        return None
    return round(number, 3)


def _line_key(value: Any) -> Optional[str]:
    try:
        number = abs(float(str(value)))
    except (TypeError, ValueError):
        return None
    if number.is_integer():
        return f"{int(number)}.0"
    return str(number).rstrip("0").rstrip(".")


def _is_active_market(market: Dict[str, Any]) -> bool:
    return str(market.get("status") or "").lower() == "active"


def _active_outcomes(market: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    for outcome in market.get("outcomes") or []:
        if str(outcome.get("status") or "").lower() != "active":
            continue
        if _price(outcome.get("odds")) is None:
            continue
        rows.append(outcome)
    return rows


def _put_3way(target: Dict[str, float], market: Dict[str, Any]) -> None:
    values: Dict[str, float] = {}
    for outcome in _active_outcomes(market):
        value = str(outcome.get("value") or "").upper()
        key = {"HOME": "home", "DRAW": "draw", "AWAY": "away"}.get(value)
        price = _price(outcome.get("odds"))
        if key and price is not None:
            values[key] = price
    if all(values.get(k) is not None for k in ("home", "draw", "away")):
        target.update({k: values[k] for k in ("home", "draw", "away")})


def _put_dc(target: Dict[str, float], market: Dict[str, Any]) -> None:
    values: Dict[str, float] = {}
    for outcome in _active_outcomes(market):
        value = str(outcome.get("value") or "").upper()
        key = {
            "HOME_OR_DRAW": "1x",
            "HOME_OR_AWAY": "12",
            "AWAY_OR_DRAW": "x2",
        }.get(value)
        price = _price(outcome.get("odds"))
        if key and price is not None:
            values[key] = price
    if all(values.get(k) is not None for k in ("1x", "12", "x2")):
        target.update({k: values[k] for k in ("1x", "12", "x2")})


def _put_gg(target: Dict[str, float], market: Dict[str, Any]) -> None:
    values: Dict[str, float] = {}
    for outcome in _active_outcomes(market):
        value = str(outcome.get("value") or "").upper()
        key = {"YES": "yes", "NO": "no"}.get(value)
        price = _price(outcome.get("odds"))
        if key and price is not None:
            values[key] = price
    if values.get("yes") is not None and values.get("no") is not None:
        target.update({"yes": values["yes"], "no": values["no"]})


def _put_ou(target: Dict[str, Dict[str, float]], market: Dict[str, Any]) -> None:
    line = _line_key((market.get("properties") or {}).get("boundary"))
    if not line:
        return
    values: Dict[str, float] = {}
    for outcome in _active_outcomes(market):
        value = str(outcome.get("value") or "").upper()
        key = {"OVER": "over", "UNDER": "under"}.get(value)
        price = _price(outcome.get("odds"))
        if key and price is not None:
            values[key] = price
    if values.get("over") is not None and values.get("under") is not None:
        target[line] = {"over": values["over"], "under": values["under"]}


def _split_teams(fixture: Dict[str, Any]) -> tuple[str, str]:
    competitors = fixture.get("competitors") or []
    if len(competitors) >= 2:
        home = str((competitors[0] or {}).get("name") or "").strip()
        away = str((competitors[1] or {}).get("name") or "").strip()
        if home and away:
            return home, away
    parts = [p.strip() for p in re.split(r"\s+v(?:s)?\.?\s+|\s+-\s+", str(fixture.get("name") or ""), flags=re.I) if p.strip()]
    if len(parts) >= 2:
        return parts[0], parts[1]
    return "", ""


def _is_pseudo(home: str, away: str, tournament: str) -> bool:
    text = f"{home} {away} {tournament}".lower()
    if not home or not away or home.lower() == away.lower():
        return True
    if any(keyword in text for keyword in VIRTUAL_KEYWORDS):
        return True
    if _engine_is_pseudo_match is not None:
        return bool(_engine_is_pseudo_match({
            "home_team": home,
            "away_team": away,
            "tournament": tournament,
        }))
    return False


def _competition_name(fixture: Dict[str, Any]) -> str:
    category = ((fixture.get("category") or {}).get("name") or "").strip()
    competition = ((fixture.get("competition") or {}).get("name") or "").strip()
    if category and competition:
        return f"{category}. {competition}"
    return competition or category or "Football"


def _fixture_is_today_prematch(fixture: Dict[str, Any], tz: ZoneInfo, today, now: datetime) -> bool:
    if fixture.get("sport") != "Football" or fixture.get("live") is True:
        return False
    if str(fixture.get("status") or "").lower() != "active":
        return False
    kickoff = _dt(fixture.get("startTime"), tz)
    if not kickoff or kickoff.date() != today or kickoff <= now:
        return False
    scoreboard = fixture.get("scoreboard") or {}
    period = str(((scoreboard.get("properties") or {}).get("period") or "NOT_STARTED")).upper()
    return period in {"NOT_STARTED", ""}


def _parse_fixture(detail: Dict[str, Any], tz: ZoneInfo, today, now: datetime) -> Optional[Dict[str, Any]]:
    if not _fixture_is_today_prematch(detail, tz, today, now):
        return None
    home, away = _split_teams(detail)
    tournament = _competition_name(detail)
    if _is_pseudo(home, away, tournament):
        return None
    kickoff = _dt(detail.get("startTime"), tz)
    if not kickoff:
        return None

    match = {
        **copy.deepcopy(_EMPTY_MARKETS),
        "home_team": home,
        "away_team": away,
        "tournament": tournament,
        "kickoff": kickoff.strftime("%Y-%m-%d %H:%M"),
        "commence_time": kickoff.strftime("%Y-%m-%d %H:%M:%S"),
        "source": SOURCE,
        "source_event_id": str(detail.get("id") or ""),
    }

    for market in detail.get("markets") or []:
        if not _is_active_market(market):
            continue
        market_type = str(market.get("type") or "").upper()
        props = market.get("properties") or {}
        if market_type == "FOOTBALL_WINNER":
            _put_3way(match["odds_1x2"], market)
        elif market_type == "FOOTBALL_DOUBLE_CHANCE":
            _put_dc(match["odds_dc"], market)
        elif market_type == "FOOTBALL_BOTH_TEAMS_TO_SCORE":
            _put_gg(match["odds_gg"], market)
        elif market_type == "FOOTBALL_WINNER_X_UP":
            if str(props.get("xUp")) == "2":
                _put_3way(match["odds_1x2_two_up"], market)
            elif str(props.get("xUp")) == "1":
                _put_3way(match["odds_1x2_one_up"], market)
        elif market_type == "FOOTBALL_OVER_UNDER_GOALS":
            _put_ou(match["odds_ou"], market)
        elif market_type == "FOOTBALL_WINNER_FIRST_HALF":
            _put_3way(match["odds_fh_1x2"], market)
        elif market_type == "FOOTBALL_DOUBLE_CHANCE_FIRST_HALF":
            _put_dc(match["odds_fh_dc"], market)
        elif market_type == "FOOTBALL_OVER_UNDER_GOALS_FIRST_HALF":
            _put_ou(match["odds_fh_ou"], market)
        elif market_type == "FOOTBALL_WINNER_SECOND_HALF":
            _put_3way(match["odds_sh_1x2"], market)
        elif market_type == "FOOTBALL_DOUBLE_CHANCE_SECOND_HALF":
            _put_dc(match["odds_sh_dc"], market)
        elif market_type == "FOOTBALL_OVER_UNDER_GOALS_SECOND_HALF":
            _put_ou(match["odds_sh_ou"], market)
        elif market_type == "FOOTBALL_WINNER_CORNERS":
            _put_3way(match["odds_corners_1x2"], market)
        elif market_type == "FOOTBALL_WINNER_BOOKINGS":
            _put_3way(match["odds_bookings_1x2"], market)
        elif market_type == "FOOTBALL_OVER_UNDER_BOOKINGS":
            _put_ou(match["odds_bookings_ou"], market)

    if not any(match.get(k) for k in ("odds_1x2", "odds_dc", "odds_ou", "odds_gg", "odds_fh_1x2")):
        return None
    return match


def _collect_competitions(session: requests.Session, tz: ZoneInfo) -> tuple[List[str], List[Dict[str, Any]]]:
    start, end = _day_bounds(tz)
    params = urlencode({"ids": "", "enriched": "2", "fromStartTime": start, "toStartTime": end, "sport": "Football"})
    data = _get_json(session, f"/api/offer/v4/competitions?{params}", retries=3)
    competition_ids = []
    fixtures = []
    for comp in data.get("enriched") or []:
        comp_id = comp.get("id")
        if comp_id:
            competition_ids.append(str(comp_id))
        fixtures.extend(comp.get("fixtures") or [])
    for comp in data.get("minimal") or []:
        comp_id = comp.get("id")
        if comp_id:
            competition_ids.append(str(comp_id))
    return sorted(set(competition_ids)), fixtures


def _fetch_competition_fixtures(session: requests.Session, competition_id: str, tz: ZoneInfo) -> List[Dict[str, Any]]:
    start, end = _day_bounds(tz)
    params = urlencode({"fromStartTime": start, "toStartTime": end})
    path = f"/api/offer/v4/competitions/{quote(competition_id, safe='')}?{params}"
    try:
        data = _get_json(session, path, retries=1)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _fetch_fixture_detail(fixture_id: str) -> Optional[Dict[str, Any]]:
    session = _session()
    try:
        return _get_json(session, f"/api/offer/v3/fixtures/{fixture_id}", referer=f"/sportsbook/fixture/{fixture_id}", retries=1)
    except Exception:
        return None


def _fetch_matches() -> List[Dict[str, Any]]:
    tz = ZoneInfo(TIMEZONE)
    today = datetime.now(tz).date()
    now = datetime.now(tz)
    session = _session()

    competition_ids, seeded_fixtures = _collect_competitions(session, tz)
    fixture_map: Dict[str, Dict[str, Any]] = {}
    for fixture in seeded_fixtures:
        fid = str(fixture.get("id") or "")
        if fid:
            fixture_map[fid] = fixture

    def fetch_comp(comp_id: str) -> List[Dict[str, Any]]:
        return _fetch_competition_fixtures(_session(), comp_id, tz)

    with ThreadPoolExecutor(max_workers=min(MAX_COMPETITION_WORKERS, max(1, len(competition_ids)))) as executor:
        futures = [executor.submit(fetch_comp, comp_id) for comp_id in competition_ids]
        for future in as_completed(futures):
            for fixture in future.result() or []:
                fid = str(fixture.get("id") or "")
                if fid:
                    fixture_map[fid] = fixture

    candidate_ids = [
        fid for fid, fixture in fixture_map.items()
        if _fixture_is_today_prematch(fixture, tz, today, now)
    ]
    if not candidate_ids:
        return []

    matches: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(MAX_DETAIL_WORKERS, len(candidate_ids))) as executor:
        futures = [executor.submit(_fetch_fixture_detail, fid) for fid in candidate_ids]
        for future in as_completed(futures):
            detail = future.result()
            if not detail:
                continue
            parsed = _parse_fixture(detail, tz, today, now)
            if parsed:
                matches.append(parsed)

    matches.sort(key=lambda item: (item["kickoff"], item["tournament"], item["home_team"], item["away_team"]))
    return matches


def _save_outputs(matches: List[Dict[str, Any]], elapsed: float) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    json_path = os.path.join(_DATA_DIR, "betfox_odds.json")
    txt_path = os.path.join(_DATA_DIR, "betfox_matches.txt")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(matches, f, indent=2, ensure_ascii=False)
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("BETFOX GHANA - ALL MATCHES\n")
        f.write(f"Generated: {datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}\n")
        f.write(f"Total: {len(matches)} matches\n")
        f.write("=" * 60 + "\n\n")
        for match in matches:
            f.write(format_match_text_block(match))
            f.write("\n")
        f.write(f"\nScraping completed in {elapsed:.1f}s\n")
    print(f"Saved to {json_path}")
    print(f"Full list: {txt_path}")


def run() -> List[Dict[str, Any]]:
    start = time.time()
    tz = ZoneInfo(TIMEZONE)
    print("\nBF BF BF BF BF BF BF BF BF BF BF BF BF BF BF BF BF BF BF BF ")
    print("   BETFOX GHANA SCRAPER")
    print(f"   {datetime.now(tz).strftime('%A, %d %B %Y %H:%M:%S')}")
    print("BF BF BF BF BF BF BF BF BF BF BF BF BF BF BF BF BF BF BF BF \n")

    try:
        matches = _fetch_matches()
    except Exception as exc:
        print(f"WARNING: Betfox skipped this scan: {exc}")
        matches = []

    elapsed = time.time() - start
    if not matches:
        print("WARNING: No prematch matches found for today.")
    print("BETFOX GHANA")
    print(f"Total matches fetched: {len(matches)}")
    print(f"With 1X2 odds: {sum(1 for m in matches if m.get('odds_1x2'))}")
    print(f"With O/U odds: {sum(1 for m in matches if m.get('odds_ou'))}")
    print(f"With DC odds:  {sum(1 for m in matches if m.get('odds_dc'))}")
    print(f"With GG odds:  {sum(1 for m in matches if m.get('odds_gg'))}")
    print(f"Scraping completed in {elapsed:.1f}s")
    _save_outputs(matches, elapsed)
    return matches


if __name__ == "__main__":
    run()
