"""
Odibets Ghana football prematch odds scraper.

Uses Odibets' own sportsbook PAL API. The scraper fetches market-level weekly
bundles and filters them down to today's visible prematch football events.
"""
from __future__ import annotations

import copy
import json
import os
import re
import sys
import threading
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
REQUEST_TIMEOUT = min(12.0, max(5.0, float(os.getenv('ODIBETS_REQUEST_TIMEOUT', '8'))))
MAX_BUNDLE_WORKERS = min(8, max(1, int(os.getenv('ODIBETS_BUNDLE_WORKERS', '6'))))
BUNDLE_MARKET_IDS = (1, 10, 27, 1043, 1044, 17, 53, 60)
RATE_LIMIT_COOLDOWN = max(30, int(os.getenv('ODIBETS_RATE_LIMIT_COOLDOWN', '90')))
_RATE_LIMITED_UNTIL = 0.0
_THREAD_LOCAL = threading.local()
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


def _thread_session() -> requests.Session:
    session = getattr(_THREAD_LOCAL, "odibets_session", None)
    if session is None:
        session = requests.Session(impersonate="chrome120")
        _THREAD_LOCAL.odibets_session = session
    return session

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
    nested = odds.get(f"1_{market_id}")
    prefix = f"{market_id}_"
    if isinstance(nested, dict) and any(str(key).startswith(prefix) for key in nested):
        return nested
    return {
        str(key): value
        for key, value in (odds or {}).items()
        if str(key).startswith(prefix)
    }


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


def _event_list(raw: Any) -> List[Dict[str, Any]]:
    if isinstance(raw, dict):
        raw = list(raw.values())
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _load_tournaments(session: requests.Session) -> Dict[int, str]:
    root = _get_json(session, "/api/sb/pal/sports/en/0", retries=2)
    return {
        int(k): str(v.get("d") or "Football")
        for k, v in (root.get("TOUR") or {}).items()
        if str(k).isdigit()
    }


def _bundle_path(market_id: int, tz: ZoneInfo) -> str:
    now = datetime.now(tz)
    tz_offset = int(-now.utcoffset().total_seconds() // 3600) if now.utcoffset() else 0
    return (
        "/api/sb/pal/weekly-bundle/"
        f"?language=en&timezone={tz_offset}&sportId=1&timefilter=0&marketId={market_id}"
    )


def _fetch_market_bundle(market_id: int, tz: ZoneInfo) -> tuple[int, Dict[str, Any]]:
    return market_id, _get_json(_thread_session(), _bundle_path(market_id, tz), retries=1)


def _fetch_matches() -> List[Dict[str, Any]]:
    tz = ZoneInfo(TIMEZONE)
    today = datetime.now(tz).date()
    session = requests.Session(impersonate="chrome120")
    tournaments = _load_tournaments(session)

    events_by_id: Dict[int, Dict[str, Any]] = {}
    odds_by_id: Dict[int, Dict[str, Any]] = {}
    request_errors = 0
    error_samples: List[str] = []

    worker_count = min(MAX_BUNDLE_WORKERS, len(BUNDLE_MARKET_IDS))
    print(
        f"  Fetching Odibets market bundles: {len(BUNDLE_MARKET_IDS)} market(s) "
        f"with {worker_count} worker(s)..."
    )

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(_fetch_market_bundle, market_id, tz): market_id
            for market_id in BUNDLE_MARKET_IDS
        }
        for future in as_completed(futures):
            market_id = futures[future]
            try:
                _, data = future.result()
            except OdibetsRateLimited:
                for pending in futures:
                    if not pending.done():
                        pending.cancel()
                raise
            except Exception as exc:
                request_errors += 1
                if len(error_samples) < 5:
                    error_samples.append(f"market {market_id}: {exc}")
                continue

            for event in _event_list(data.get("E") or []):
                event_id = event.get("id") or event.get("i")
                if event_id is not None:
                    try:
                        events_by_id[int(event_id)] = event
                    except (TypeError, ValueError):
                        pass

            odds_table = data.get("O") or {}
            if isinstance(odds_table, dict):
                for event_id, odds in odds_table.items():
                    if not isinstance(odds, dict):
                        continue
                    try:
                        numeric_id = int(event_id)
                    except (TypeError, ValueError):
                        continue
                    odds_by_id.setdefault(numeric_id, {}).update(odds)

    if request_errors:
        print(f"  WARNING: Odibets bundle request errors: {request_errors}/{len(BUNDLE_MARKET_IDS)}")
        for sample in error_samples:
            print(f"    - {sample}")

    if request_errors > max(1, int(len(BUNDLE_MARKET_IDS) * 0.25)):
        raise RuntimeError(
            f"Odibets bundle feed too incomplete ({request_errors}/{len(BUNDLE_MARKET_IDS)} bundle errors); "
            "skipping this platform for the scan instead of using partial odds."
        )

    matches: List[Dict[str, Any]] = []
    parse_rejects = 0
    for event_id, odds in odds_by_id.items():
        event = events_by_id.get(event_id)
        if not event:
            parse_rejects += 1
            continue
        parsed = _parse_event_detail({"E": event, "O": odds}, tournaments, tz, today)
        if parsed:
            matches.append(parsed)
        else:
            parse_rejects += 1

    if parse_rejects:
        print(f"  INFO: Odibets bundle events ignored after today/pseudo/visibility filters: {parse_rejects}")

    if not matches and odds_by_id:
        raise RuntimeError(
            "Odibets bundle feed returned odds but none passed today's visibility filters; "
            "skipping this platform for the scan."
        )

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
    print(f"Saved to {json_path}")
    print(f"Full list: {txt_path}")


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
        print("WARNING: No prematch matches found for today.")
    print(f"ODIBETS GHANA\nTotal matches fetched: {len(matches)}")
    print(f"With 1X2 odds: {sum(1 for m in matches if m.get('odds_1x2'))}")
    print(f"With O/U odds: {sum(1 for m in matches if m.get('odds_ou'))}")
    print(f"With DC odds:  {sum(1 for m in matches if m.get('odds_dc'))}")
    print(f"With GG odds:  {sum(1 for m in matches if m.get('odds_gg'))}")
    print(f"Scraping completed in {elapsed:.1f}s")
    _save_outputs(matches, elapsed)
    return matches


if __name__ == "__main__":
    run()



