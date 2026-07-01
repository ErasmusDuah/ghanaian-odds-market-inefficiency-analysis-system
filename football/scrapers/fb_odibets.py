"""
Odibets Ghana football prematch odds scraper.

Uses Odibets' own sportsbook PAL API.  The scraper fetches the daily
prematch bundle (``/api/sb/pal/daily/mobile/en/…``) which returns all
today's soccer events, then fetches full market details per event.
"""
from __future__ import annotations

import copy
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional
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

API_BASE = "https://gh.api.odibets.com.gh"
SITE_URL = "https://odibets.com.gh/gh/"
TIMEZONE = "Africa/Accra"
SOURCE = "odibets_gh"
REQUEST_TIMEOUT = min(8.0, max(4.0, float(os.getenv('ODIBETS_REQUEST_TIMEOUT', '6'))))
MAX_WORKERS = 1
MAX_DETAIL_WORKERS = min(4, max(1, int(os.getenv('ODIBETS_DETAIL_WORKERS', '4'))))
RATE_LIMIT_COOLDOWN = max(60, int(os.getenv('ODIBETS_RATE_LIMIT_COOLDOWN', '600')))
_RATE_LIMITED_UNTIL = 0.0
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
    "efootball", "e-football", "player", "special",
)


class OdibetsRateLimited(RuntimeError):
    pass


def _mark_rate_limited() -> None:
    global _RATE_LIMITED_UNTIL
    _RATE_LIMITED_UNTIL = max(_RATE_LIMITED_UNTIL, time.time() + RATE_LIMIT_COOLDOWN)


def _rate_limit_remaining() -> int:
    return max(0, int(_RATE_LIMITED_UNTIL - time.time()))


def _headers() -> Dict[str, str]:
    return {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://odibets.com.gh",
        "Referer": SITE_URL,
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
    }


def _url(path: str) -> str:
    return f"{API_BASE}{path}{'&' if '?' in path else '?'}v_=2&src=mobile"


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
    if number <= 1.0:
        return None
    return round(number, 3)


def _line_key(value: Any) -> Optional[str]:
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return None
    if number.is_integer():
        return f"{int(number)}.0"
    return str(number).rstrip("0").rstrip(".")


def _split_teams(name: str) -> tuple[str, str]:
    parts = [p.strip() for p in str(name or "").split("|v|")]
    if len(parts) == 2:
        return parts[0], parts[1]
    parts = [p.strip() for p in re.split(r"\s+v(?:s)?\.?\s+", str(name or ""), flags=re.I)]
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


def _status_from_exception(exc: Exception) -> Optional[int]:
    response = getattr(exc, "response", None)
    return getattr(response, "status_code", None)


def _retry_after_from_exception(exc: Exception) -> Optional[int]:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", {}) or {}
    try:
        return int(headers.get("Retry-After"))
    except (TypeError, ValueError):
        return None


def _get_json(session: Optional[requests.Session], path: str, *, retries: int = 2) -> Dict[str, Any]:
    remaining = _rate_limit_remaining()
    if remaining > 0:
        raise OdibetsRateLimited(f"Odibets rate-limit cooldown active for {remaining}s")

    last_exc: Optional[Exception] = None
    for attempt in range(max(1, retries + 1)):
        if attempt:
            time.sleep(min(2.0, 0.5 * attempt))
        req_session = session or requests.Session(impersonate="chrome120")
        try:
            response = req_session.get(
                _url(path),
                headers=_headers(),
                timeout=REQUEST_TIMEOUT,
                impersonate="chrome120",
            )
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last_exc = exc
            status = _status_from_exception(exc)
            if status == 429:
                retry_after = _retry_after_from_exception(exc)
                if retry_after:
                    global _RATE_LIMITED_UNTIL
                    _RATE_LIMITED_UNTIL = max(_RATE_LIMITED_UNTIL, time.time() + retry_after)
                else:
                    _mark_rate_limited()
                raise OdibetsRateLimited("Odibets API returned HTTP 429 rate limit")
            if status not in {500, 502, 503, 504, None}:
                break
    raise RuntimeError(f"Odibets API request failed after {retries + 1} attempt(s): {last_exc}")


def _odd_group(odds: Dict[str, Any], market_id: int) -> Dict[str, Any]:
    return odds.get(f"1_{market_id}") or {}


def _selection_price(group: Dict[str, Any], market_id: int, selection_id: int) -> Optional[float]:
    item = group.get(f"{market_id}_{selection_id}")
    return _price((item or {}).get("v"))


def _put_3way(target: Dict[str, float], odds: Dict[str, Any], market_id: int) -> None:
    group = _odd_group(odds, market_id)
    values = {
        "home": _selection_price(group, market_id, 1),
        "draw": _selection_price(group, market_id, 2),
        "away": _selection_price(group, market_id, 3),
    }
    if all(v is not None for v in values.values()):
        target.update(values)


def _put_dc(target: Dict[str, float], odds: Dict[str, Any], market_id: int) -> None:
    group = _odd_group(odds, market_id)
    values = {
        "1x": _selection_price(group, market_id, 9),
        "12": _selection_price(group, market_id, 10),
        "x2": _selection_price(group, market_id, 11),
    }
    if all(v is not None for v in values.values()):
        target.update(values)


def _put_gg(target: Dict[str, float], odds: Dict[str, Any], market_id: int,
            yes_id: int = 74, no_id: int = 76) -> None:
    group = _odd_group(odds, market_id)
    values = {
        "yes": _selection_price(group, market_id, yes_id),
        "no": _selection_price(group, market_id, no_id),
    }
    if all(v is not None for v in values.values()):
        target.update(values)


def _put_ou(target: Dict[str, Dict[str, float]], odds: Dict[str, Any], market_id: int) -> None:
    group = _odd_group(odds, market_id)
    rows: Dict[str, Dict[str, float]] = {}
    for key, item in group.items():
        match = re.match(rf"^{market_id}_(12|13)@([^_]+)", str(key))
        if not match:
            continue
        side = "over" if match.group(1) == "12" else "under"
        line = _line_key(match.group(2))
        price = _price((item or {}).get("v"))
        if line and price is not None:
            rows.setdefault(line, {})[side] = price
    for line, row in rows.items():
        if row.get("over") is not None and row.get("under") is not None:
            target[line] = row


def _parse_event_detail(detail: Dict[str, Any], tournaments: Dict[int, str],
                        tz: ZoneInfo, today) -> Optional[Dict[str, Any]]:
    event = detail.get("E") or {}
    status = event.get("s", event.get("st"))
    if str(status) != "1" or event.get("l"):
        return None
    kickoff_dt = _dt(event.get("std") or event.get("evd"), tz)
    now = datetime.now(tz)
    if not kickoff_dt or kickoff_dt.date() != today or kickoff_dt <= now:
        return None

    home, away = _split_teams(event.get("n", ""))
    tournament = tournaments.get(int(event.get("t") or 0), "Football")
    if _is_pseudo(home, away, tournament):
        return None

    odds = detail.get("O") or {}
    match = {
        **copy.deepcopy(_EMPTY_MARKETS),
        "home_team": home,
        "away_team": away,
        "tournament": tournament,
        "kickoff": kickoff_dt.strftime("%Y-%m-%d %H:%M"),
        "commence_time": kickoff_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "source": SOURCE,
        "source_event_id": str(event.get("id") or ""),
    }

    _put_3way(match["odds_1x2"], odds, 1)
    _put_dc(match["odds_dc"], odds, 10)
    _put_gg(match["odds_gg"], odds, 27)
    _put_gg(match["odds_gg_2plus"], odds, 1002, yes_id=39, no_id=40)
    _put_3way(match["odds_1x2_two_up"], odds, 1043)
    _put_3way(match["odds_1x2_one_up"], odds, 1044)
    _put_ou(match["odds_ou"], odds, 17)
    _put_ou(match["odds_asian_ou"], odds, 990)
    _put_3way(match["odds_fh_1x2"], odds, 53)
    _put_dc(match["odds_fh_dc"], odds, 56)
    _put_ou(match["odds_fh_ou"], odds, 60)
    _put_3way(match["odds_sh_1x2"], odds, 73)
    _put_dc(match["odds_sh_dc"], odds, 75)
    _put_ou(match["odds_sh_ou"], odds, 79)
    _put_3way(match["odds_corners_1x2"], odds, 145)
    _put_3way(match["odds_bookings_1x2"], odds, 123)
    _put_ou(match["odds_bookings_ou"], odds, 125)
    return match


def _collect_event_ids(session: requests.Session, tz: ZoneInfo, today) -> tuple[List[int], Dict[int, str]]:
    """Collect today's prematch soccer event IDs."""
    root = _get_json(session, "/api/sb/pal/sports/en/0", retries=3)
    tournaments = {
        int(k): str(v.get("d") or "Football")
        for k, v in (root.get("TOUR") or {}).items()
        if str(k).isdigit()
    }

    now = datetime.now(tz)
    event_ids: Dict[int, None] = {}
    daily_seen = 0
    root_seen = 0

    try:
        tz_offset = int(-now.utcoffset().total_seconds() // 3600) if now.utcoffset() else 0
        daily = _get_json(session, f"/api/sb/pal/daily/mobile/en/1/0/{tz_offset}", retries=1)
        daily_events = daily.get("E") or []
        if isinstance(daily_events, dict):
            daily_events = list(daily_events.values())
        daily_seen = len(daily_events)
        for event in daily_events:
            _maybe_add_event_id(event_ids, event, tz, today, now)
    except OdibetsRateLimited:
        print("  WARNING: Odibets daily bundle rate-limited; using root fallback events.")
    except Exception as exc:
        print(f"  WARNING: Odibets daily bundle unavailable: {exc}")

    # The root endpoint is only featured events, but it can rescue matches
    # when the daily bundle is unavailable.
    if not event_ids:
        root_events = root.get("E") or {}
        if isinstance(root_events, dict):
            root_events = list(root_events.values())
        root_seen = len(root_events)
        for event in root_events:
            _maybe_add_event_id(event_ids, event, tz, today, now)

    if not event_ids and (daily_seen or root_seen):
        print(
            f"  WARNING: Odibets saw daily={daily_seen}, root={root_seen}, "
            "but none passed today's prematch filter."
        )

    return list(event_ids.keys()), tournaments


def _maybe_add_event_id(event_ids: Dict[int, None], event: Dict[str, Any],
                        tz: ZoneInfo, today, now: datetime) -> None:
    kickoff_dt = _dt(event.get("std") or event.get("evd"), tz)
    event_id = event.get("id") or event.get("i")
    home = event.get("h") or event.get("hn") or event.get("home") or ""
    away = event.get("a") or event.get("an") or event.get("away") or ""
    if not home or not away:
        home, away = _split_teams(event.get("n", ""))
    tournament = event.get("cn") or event.get("tn") or event.get("tournament") or ""
    if (
        event_id
        and str(event.get("s", event.get("st"))) == "1"
        and not event.get("l")
        and kickoff_dt
        and kickoff_dt.date() == today
        and kickoff_dt > now
        and not _is_pseudo(str(home), str(away), str(tournament))
    ):
        event_ids[int(event_id)] = None


def _fetch_matches() -> List[Dict[str, Any]]:
    tz = ZoneInfo(TIMEZONE)
    today = datetime.now(tz).date()
    session = requests.Session(impersonate="chrome120")
    event_ids, tournaments = _collect_event_ids(session, tz, today)
    if not event_ids:
        return []
    matches: List[Dict[str, Any]] = []
    failed_details = 0

    def fetch_detail(event_id: int) -> Optional[Dict[str, Any]]:
        try:
            detail = _get_json(None, f"/api/sb/pal/event/en/{event_id}", retries=0)
            return _parse_event_detail(detail, tournaments, tz, today)
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=MAX_DETAIL_WORKERS) as executor:
        futures = [executor.submit(fetch_detail, event_id) for event_id in event_ids]
        for future in as_completed(futures):
            parsed = future.result()
            if parsed:
                matches.append(parsed)
            else:
                failed_details += 1

    if failed_details:
        print(f"  WARNING: Odibets skipped {failed_details} unavailable detail event(s) this scan.")
    matches.sort(key=lambda item: (item["kickoff"], item["tournament"], item["home_team"], item["away_team"]))
    return matches


def _save_outputs(matches: List[Dict[str, Any]], elapsed: float) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    json_path = os.path.join(_DATA_DIR, "odibets_odds.json")
    txt_path = os.path.join(_DATA_DIR, "odibets_matches.txt")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(matches, f, indent=2, ensure_ascii=False)
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("ODIBETS GHANA - ALL MATCHES\n")
        f.write(f"Generated: {datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}\n")
        f.write(f"Total: {len(matches)} matches\n")
        f.write("=" * 60 + "\n\n")
        for match in matches:
            f.write(format_match_text_block(match))
            f.write("\n")
        f.write(f"\nScraping completed in {elapsed:.1f}s\n")
    print(f"💾 Saved to {json_path}")
    print(f"📄 Full list: {txt_path}")


def run() -> List[Dict[str, Any]]:
    start = time.time()
    tz = ZoneInfo(TIMEZONE)
    print("\nOD OD OD OD OD OD OD OD OD OD OD OD OD OD OD OD OD OD OD OD ")
    print("   ODIBETS GHANA SCRAPER")
    print(f"   {datetime.now(tz).strftime('%A, %d %B %Y %H:%M:%S')}")
    print("OD OD OD OD OD OD OD OD OD OD OD OD OD OD OD OD OD OD OD OD \n")

    try:
        matches = _fetch_matches()
    except OdibetsRateLimited as exc:
        print(f"WARNING: Odibets skipped this scan: {exc}")
        matches = []
    except Exception as exc:
        print(f"WARNING: Odibets skipped this scan: {exc}")
        matches = []

    elapsed = time.time() - start
    if not matches:
        print("⚠️  No prematch matches found for today.")
    print(f"ODIBETS GHANA\nTotal matches fetched: {len(matches)}")
    print(f"With 1X2 odds: {sum(1 for m in matches if m.get('odds_1x2'))}")
    print(f"With O/U odds: {sum(1 for m in matches if m.get('odds_ou'))}")
    print(f"With DC odds:  {sum(1 for m in matches if m.get('odds_dc'))}")
    print(f"With GG odds:  {sum(1 for m in matches if m.get('odds_gg'))}")
    print(f"⏱️  Scraping completed in {elapsed:.1f}s")
    _save_outputs(matches, elapsed)
    return matches


if __name__ == "__main__":
    run()
