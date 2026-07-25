"""
MyBet.Africa Ghana football prematch odds scraper.

Uses MyBet's newer feed API for the event list and full event-details payloads
so visible markets such as 1X2, Double Chance, O/U, and GG are captured.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

from curl_cffi import requests

try:
    from .fb_output_formatter import format_match_text_block
except ImportError:
    from fb_output_formatter import format_match_text_block

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (OSError, ValueError):
        pass

BASE_URL = "https://www.mybet.africa"
FEED_API_URL = "https://api-ams.core-ix.com/api/v1"
EVENTS_FILTER_URL = f"{FEED_API_URL}/events/odds-filter"
EVENT_DETAILS_URL = f"{FEED_API_URL}/events-details"
TIMEZONE = "Africa/Accra"
SOURCE = "mybetafrica_gh"
SOCCER_SPORT_ID = 1
SOCCER_1X2_MARKET_ID = 174
REQUEST_TIMEOUT = min(8.0, max(3.0, float(os.getenv('MYBET_REQUEST_TIMEOUT', '8'))))
REQUEST_ATTEMPTS = min(2, max(1, int(os.getenv('MYBET_REQUEST_ATTEMPTS', '2'))))
DETAIL_BATCH_SIZE = max(5, int(os.getenv('MYBET_DETAIL_BATCH_SIZE', '35')))
DETAIL_WORKERS = max(1, int(os.getenv('MYBET_DETAIL_WORKERS', '8')))
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
    "efootball", "e-football", "footballgo",
)


def _headers() -> Dict[str, str]:
    return {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": BASE_URL,
        "Referer": f"{BASE_URL}/",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0 Safari/537.36"
        ),
    }


def _post_json(url: str, payload: Dict[str, Any], *, attempts: int = REQUEST_ATTEMPTS) -> Dict[str, Any]:
    last_error: Optional[Exception] = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            response = requests.post(
                url,
                headers=_headers(),
                json=payload,
                timeout=REQUEST_TIMEOUT,
                impersonate="chrome120",
            )
            response.raise_for_status()
            return response.json().get("data") or {}
        except Exception as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(0.7 * attempt)
    raise RuntimeError(f"MyBet.Africa API request failed after {attempts} attempt(s): {last_error}")


def _price(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 1.0:
        return None
    return round(number, 3)


def _event_dt(raw: Any, tz: ZoneInfo) -> Optional[datetime]:
    text = str(raw or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            parsed = datetime.strptime(text, fmt)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=tz)
            return parsed.astimezone(tz)
        except ValueError:
            continue
    return None


def _norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _line_value(odd: Dict[str, Any], row: Dict[str, Any]) -> Optional[str]:
    value = odd.get("special_value") or row.get("special_value") or row.get("name")
    if value is None:
        name = str(odd.get("name") or "")
        found = re.search(r"(\d+(?:\.\d+)?)", name)
        value = found.group(1) if found else None
    if value is None:
        return None
    text = str(value).strip()
    found = re.search(r"(\d+(?:\.\d+)?)", text)
    return found.group(1) if found else None


def _is_active_odd(odd: Dict[str, Any]) -> bool:
    if odd.get("event_odds_id") is not None:
        return True
    # The feed API supplies id/provider_odd_id for bettable odds rather than event_odds_id.
    return odd.get("id") is not None and odd.get("provider_odd_id") is not None


def _is_pseudo_match(home: str, away: str, tournament: str) -> bool:
    h = home.strip().lower()
    a = away.strip().lower()
    text = f"{h} {a} {tournament.lower()}"
    if not h or not a or h == a:
        return True
    bad_exact = {"1st team", "2nd team", "first team", "second team", "1st teams", "2nd teams"}
    if h in bad_exact or a in bad_exact:
        return True
    bad_fragments = (
        "goalscorer", "player specials", "shots on target", "to score",
        "score anytime", "fantasy", "statistics", "specials", "winner",
    )
    return any(fragment in text for fragment in bad_fragments)


def _is_virtual_event(event: Dict[str, Any], tournament: str) -> bool:
    text = " ".join(str(value or "") for value in (event.get("name"), tournament)).lower()
    return any(keyword in text for keyword in VIRTUAL_KEYWORDS)


def _iter_markets(
    detail: Dict[str, Any],
    main_only: bool = False,
) -> Iterable[Tuple[str, Dict[str, Any]]]:
    for group in detail.get("market_groups") or []:
        group_name = str(group.get("name") or "")
        if main_only and _norm(group_name) != "main":
            continue
        for market in group.get("markets") or []:
            yield group_name, market


def _visible_rows(market: Dict[str, Any], visible_lines_only: bool = False) -> List[Dict[str, Any]]:
    rows = list(market.get("market_odds") or [])
    if not visible_lines_only:
        return rows
    favorite_rows = [row for row in rows if row.get("is_favorite") is True]
    return favorite_rows or rows


def _iter_odds(
    market: Dict[str, Any],
    visible_lines_only: bool = False,
) -> Iterable[Tuple[Dict[str, Any], Dict[str, Any]]]:
    for row in _visible_rows(market, visible_lines_only=visible_lines_only):
        for odd in row.get("odds") or []:
            price = _price(odd.get("value"))
            if price is None or not _is_active_odd(odd):
                continue
            yield row, {**odd, "value": price}


def _parse_1x2_market(detail: Dict[str, Any], market_id: int, main_only: bool = True) -> Dict[str, float]:
    values: Dict[str, float] = {}
    for group_name, market in _iter_markets(detail, main_only=main_only):
        if int(market.get("id") or 0) != market_id:
            continue
        for _, odd in _iter_odds(market):
            name = _norm(odd.get("name"))
            if name in {"draw", "x"}:
                values["draw"] = odd["value"]
            else:
                event = detail.get("event") or {}
                home, away = _teams_from_event(event)
                if home and _norm(home) in name:
                    values["home"] = odd["value"]
                elif away and _norm(away) in name:
                    values["away"] = odd["value"]
    return values if {"home", "draw", "away"} <= values.keys() else {}


def _parse_1x2(detail: Dict[str, Any]) -> Dict[str, float]:
    return _parse_1x2_market(detail, 174)


def _parse_dc_market(detail: Dict[str, Any], market_id: int, main_only: bool = True) -> Dict[str, float]:
    values: Dict[str, float] = {}
    home, away = _teams_from_event(detail.get("event") or {})
    for group_name, market in _iter_markets(detail, main_only=main_only):
        if int(market.get("id") or 0) != market_id:
            continue
        for _, odd in _iter_odds(market):
            name = _norm(odd.get("name"))
            if home and away and _norm(home) in name and _norm(away) in name:
                values["12"] = odd["value"]
            elif home and _norm(home) in name and "draw" in name:
                values["1x"] = odd["value"]
            elif away and _norm(away) in name and "draw" in name:
                values["x2"] = odd["value"]
    return values if {"1x", "12", "x2"} <= values.keys() else values


def _parse_dc(detail: Dict[str, Any]) -> Dict[str, float]:
    return _parse_dc_market(detail, 76)


def _parse_gg(detail: Dict[str, Any]) -> Dict[str, float]:
    values: Dict[str, float] = {}
    for group_name, market in _iter_markets(detail, main_only=True):
        if int(market.get("id") or 0) != 87:
            continue
        for _, odd in _iter_odds(market):
            name = _norm(odd.get("name"))
            if name == "yes":
                values["yes"] = odd["value"]
            elif name == "no":
                values["no"] = odd["value"]
    return values if {"yes", "no"} <= values.keys() else values


def _parse_ou_market(detail: Dict[str, Any], market_id: int, main_only: bool = True) -> Dict[str, Dict[str, float]]:
    values: Dict[str, Dict[str, float]] = {}
    for group_name, market in _iter_markets(detail, main_only=main_only):
        if int(market.get("id") or 0) != market_id:
            continue
        for row, odd in _iter_odds(market, visible_lines_only=True):
            line = _line_value(odd, row)
            if not line:
                continue
            bucket = values.setdefault(line, {})
            name = _norm(odd.get("name"))
            if name.startswith("over"):
                bucket["over"] = odd["value"]
            elif name.startswith("under"):
                bucket["under"] = odd["value"]
    return {line: pair for line, pair in values.items() if {"over", "under"} <= pair.keys()}


def _parse_ou(detail: Dict[str, Any]) -> Dict[str, Dict[str, float]]:
    return _parse_ou_market(detail, 40)


def _teams_from_event(event: Dict[str, Any]) -> Tuple[str, str]:
    competitors = event.get("competitors") or {}
    home = away = ""
    for competitor in competitors.values():
        ctype = str(competitor.get("type") or "").lower()
        if ctype == "home":
            home = str(competitor.get("name") or "").strip()
        elif ctype == "away":
            away = str(competitor.get("name") or "").strip()
    if not home or not away:
        parts = re.split(r"\s+(?:vs\.?|-|v)\s+", str(event.get("name") or ""), maxsplit=1, flags=re.I)
        if len(parts) == 2:
            home = home or parts[0].strip()
            away = away or parts[1].strip()
    return home, away


def _fetch_today_event_summaries() -> List[Dict[str, Any]]:
    payload = {
        "ref": "/events/odds-filter",
        "sport_id": SOCCER_SPORT_ID,
        "market_id": SOCCER_1X2_MARKET_ID,
        "min_odds_value": 1,
        "max_odds_value": 3,
        "sort_type": "date",
        "time_range": "today",
    }
    try:
        data = _post_json(EVENTS_FILTER_URL, payload)
    except Exception as exc:
        print(f"  WARNING: MyBet.Africa event list unavailable; skipping platform this scan: {exc}")
        return []
    events_by_date = data.get("events") or {}
    summaries: List[Dict[str, Any]] = []
    for events in events_by_date.values():
        if isinstance(events, list):
            summaries.extend(events)
    return summaries


def _fetch_detail_batch(event_ids: List[int]) -> Dict[str, Dict[str, Any]]:
    if not event_ids:
        return {}
    data = _post_json(EVENT_DETAILS_URL, {"event_ids": event_ids}, attempts=1)
    return data.get("events") or {}


def _fetch_all_details(event_ids: List[int]) -> Dict[str, Dict[str, Any]]:
    batches = [event_ids[i:i + DETAIL_BATCH_SIZE] for i in range(0, len(event_ids), DETAIL_BATCH_SIZE)]
    details: Dict[str, Dict[str, Any]] = {}
    failed_batches = 0
    with ThreadPoolExecutor(max_workers=min(DETAIL_WORKERS, max(1, len(batches)))) as executor:
        futures = [executor.submit(_fetch_detail_batch, batch) for batch in batches]
        for future in as_completed(futures):
            try:
                details.update(future.result())
            except Exception:
                failed_batches += 1
    if failed_batches:
        print(f"  WARNING: MyBet.Africa skipped {failed_batches} slow detail batch(es) this scan.")
    return details


def _base_match(detail: Dict[str, Any], kickoff: datetime) -> Dict[str, Any]:
    event = detail.get("event") or {}
    category = str((detail.get("category") or {}).get("name") or "").strip()
    competition = str((detail.get("tournament") or {}).get("name") or "").strip()
    tournament_name = f"{category}. {competition}" if category else competition
    home, away = _teams_from_event(event)
    match = {
        "event_id": str(event.get("id") or ""),
        "home_team": home,
        "away_team": away,
        "kickoff": kickoff.strftime("%Y-%m-%d %H:%M"),
        "tournament": tournament_name,
        "is_live": False,
        "status": "Not start",
        "source": SOURCE,
    }
    for key, value in _EMPTY_MARKETS.items():
        match[key] = dict(value)
    return match


def _match_from_detail(detail: Dict[str, Any], now: datetime, today) -> Optional[Dict[str, Any]]:
    event = detail.get("event") or {}
    kickoff = _event_dt(event.get("start_time"), now.tzinfo or ZoneInfo(TIMEZONE))
    if kickoff is None or kickoff.date() != today or kickoff <= now:
        return None
    home, away = _teams_from_event(event)
    category = str((detail.get("category") or {}).get("name") or "").strip()
    competition = str((detail.get("tournament") or {}).get("name") or "").strip()
    tournament = f"{category}. {competition}" if category else competition
    if event.get("event_type_id") not in (1, "1", None):
        return None
    if _is_virtual_event(event, tournament) or _is_pseudo_match(home, away, tournament):
        return None

    match = _base_match(detail, kickoff)
    match["odds_1x2"] = _parse_1x2(detail)
    if not match["odds_1x2"]:
        return None
    match["odds_dc"] = _parse_dc(detail)
    match["odds_gg"] = _parse_gg(detail)
    match["odds_ou"] = _parse_ou(detail)
    match["odds_fh_1x2"] = _parse_1x2_market(detail, 118)
    match["odds_sh_1x2"] = _parse_1x2_market(detail, 39)
    match["odds_fh_dc"] = _parse_dc_market(detail, 86, main_only=False)
    match["odds_sh_dc"] = _parse_dc_market(detail, 108, main_only=False)
    match["odds_fh_ou"] = _parse_ou_market(detail, 78, main_only=False)
    match["odds_sh_ou"] = _parse_ou_market(detail, 41, main_only=False)
    match["odds_corners_1x2"] = _parse_1x2_market(detail, 395, main_only=False)
    match["odds_bookings_1x2"] = _parse_1x2_market(detail, 444, main_only=False)
    match["odds_bookings_ou"] = _parse_ou_market(detail, 446, main_only=False)
    return match


def collect_today_matches() -> List[Dict[str, Any]]:
    tz = ZoneInfo(TIMEZONE)
    now = datetime.now(tz)
    today = now.date()
    summaries = _fetch_today_event_summaries()
    if not summaries:
        return []
    ids = sorted({int(event["id"]) for event in summaries if event.get("id")})
    details = _fetch_all_details(ids)

    matches: List[Dict[str, Any]] = []
    seen = set()
    for event_id in ids:
        detail = details.get(str(event_id)) or details.get(event_id)
        if not detail:
            continue
        match = _match_from_detail(detail, now, today)
        if not match:
            continue
        key = match["event_id"]
        if key in seen:
            continue
        seen.add(key)
        matches.append(match)

    matches.sort(key=lambda item: (item["kickoff"], item["tournament"], item["home_team"], item["away_team"]))
    return matches


def display_matches(matches: List[Dict[str, Any]]) -> None:
    if not matches:
        print("WARNING: No MyBet.Africa matches found")
        return
    print("\nMYBET.AFRICA GHANA")
    print(f"Total matches fetched: {len(matches)}")
    print(f"With 1X2 odds: {sum(1 for m in matches if m.get('odds_1x2'))}")
    print(f"With O/U odds: {sum(1 for m in matches if m.get('odds_ou'))}")
    print(f"With DC odds:  {sum(1 for m in matches if m.get('odds_dc'))}")
    print(f"With GG odds:  {sum(1 for m in matches if m.get('odds_gg'))}")
    print("=" * 50)
    for match in matches[:10]:
        print(f"  {match['home_team']} vs {match['away_team']} | {match['kickoff']} | {match['tournament']}")
    if len(matches) > 10:
        print(f"  ... and {len(matches) - 10} more matches")
    print("=" * 50)


def run() -> List[Dict[str, Any]]:
    start = time.time()
    print("\n" + "MB " * 20)
    print("   MYBET.AFRICA GHANA SCRAPER")
    print(f"   {datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}")
    print("MB " * 20 + "\n")
    os.makedirs(_DATA_DIR, exist_ok=True)

    try:
        matches = collect_today_matches()
    except Exception as exc:
        print(f"WARNING: MyBet.Africa skipped this scan: {exc}")
        return []
    if not matches:
        print("WARNING: No MyBet.Africa matches found")
        return []

    display_matches(matches)
    json_path = os.path.join(_DATA_DIR, "mybetafrica_odds.json")
    txt_path = os.path.join(_DATA_DIR, "mybetafrica_matches.txt")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(matches, f, indent=2, ensure_ascii=False)
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("MYBET.AFRICA GHANA - ALL MATCHES\n")
        f.write(f"Generated: {datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}\n")
        f.write(f"Total: {len(matches)} matches\n")
        f.write("=" * 60 + "\n\n")
        for match in matches:
            f.write(format_match_text_block(match))
    print(f"Saved to {json_path}")
    print(f"Full list saved to {txt_path}")
    print(f"Scraping completed in {time.time() - start:.1f}s")
    return matches


if __name__ == "__main__":
    run()
