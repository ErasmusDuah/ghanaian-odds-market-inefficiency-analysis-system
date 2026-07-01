"""
MulaSport Ghana football prematch odds scraper.

Uses MulaSport's Neogen sportsbook API and keeps only active/open market
instances returned by event details. This prevents hidden or incomplete lines
from entering the intensive arb engine.
"""
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
from zoneinfo import ZoneInfo

import requests

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

API_BASE = "https://api.neogengaming.com"
SITE_URL = "https://mulasport.com.gh/sports"
TENANT_ID = "019db1c6-cd21-72f2-b07f-43bdcda223c9"
FOOTBALL_SPORT_ID = "fac90b5a-04ee-42ff-87ba-5c6c9eab8d81"
TIMEZONE = "Africa/Accra"
SOURCE = "mulasport_gh"
REQUEST_TIMEOUT = min(12.0, max(5.0, float(os.getenv("MULASPORT_REQUEST_TIMEOUT", "8"))))
DETAIL_BATCH_SIZE = min(80, max(10, int(os.getenv("MULASPORT_DETAIL_BATCH_SIZE", "50"))))
MAX_DETAIL_WORKERS = min(5, max(1, int(os.getenv("MULASPORT_DETAIL_WORKERS", "4"))))
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
    "efootball", "e-football", "fantasy", "special", "player", "winner of",
)


def _headers() -> Dict[str, str]:
    return {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": "https://mulasport.com.gh",
        "Referer": SITE_URL,
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "x-tenant-id": TENANT_ID,
    }


def _as_list(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]
    if isinstance(value, dict):
        for key in ("sport_events", "data", "items", "events"):
            if key in value:
                return _as_list(value[key])
        return [x for x in value.values() if isinstance(x, dict)]
    return []


def _get_json(path: str, params: Optional[Dict[str, Any]] = None, retries: int = 2) -> Any:
    last_exc: Optional[Exception] = None
    for attempt in range(max(1, retries + 1)):
        if attempt:
            time.sleep(min(1.5, 0.4 * attempt))
        try:
            response = requests.get(
                f"{API_BASE}{path}",
                params=params,
                headers=_headers(),
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last_exc = exc
    raise RuntimeError(f"MulaSport GET {path} failed: {last_exc}")


def _post_json(path: str, payload: Dict[str, Any], retries: int = 2) -> Any:
    last_exc: Optional[Exception] = None
    for attempt in range(max(1, retries + 1)):
        if attempt:
            time.sleep(min(1.5, 0.4 * attempt))
        try:
            response = requests.post(
                f"{API_BASE}{path}",
                headers=_headers(),
                json=payload,
                timeout=max(REQUEST_TIMEOUT, 12.0),
            )
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last_exc = exc
    raise RuntimeError(f"MulaSport POST {path} failed: {last_exc}")


def _day_bounds_utc(tz: ZoneInfo) -> tuple[str, str]:
    now = datetime.now(tz)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    end = now.replace(hour=23, minute=59, second=59, microsecond=999000).astimezone(timezone.utc)
    return (
        start.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        end.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
    )


def _dt(raw: Any, tz: ZoneInfo) -> Optional[datetime]:
    if not raw:
        return None
    text = str(raw).strip()
    for fmt in ("%m/%d/%Y %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(text, fmt).astimezone(tz)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(tz)
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
        number = abs(float(str(value)))
    except (TypeError, ValueError):
        return None
    if number.is_integer():
        return f"{int(number)}.0"
    return (f"{number:.2f}" if number % 0.25 == 0 else str(number)).rstrip("0").rstrip(".")


def _extract_line(*parts: Any) -> Optional[str]:
    text = " ".join(str(p or "") for p in parts)
    match = re.search(r"(?:hcp=|[+\-])\s*([0-9]+(?:\.[0-9]+)?)", text, flags=re.I)
    if not match:
        return None
    return _line_key(match.group(1))


def _split_teams(event: Dict[str, Any]) -> tuple[str, str]:
    home = str(event.get("hom") or event.get("home") or event.get("home_team") or "").strip()
    away = str(event.get("awy") or event.get("away") or event.get("away_team") or "").strip()
    if home and away:
        return home, away
    name = str(event.get("name") or "")
    parts = [p.strip() for p in re.split(r"\s+v(?:s)?\.?\s+|\s+-\s+", name, flags=re.I) if p.strip()]
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


def _active(row: Dict[str, Any]) -> bool:
    if row.get("is_active") is False or row.get("is_enabled") is False:
        return False
    status = str(row.get("market_status_label") or "open").lower()
    return status in {"", "open", "active"} and _price(row.get("odds")) is not None


def _norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").lower()).strip()


def _outcome_key(row: Dict[str, Any], market_name: str) -> Optional[str]:
    outcome = _norm(row.get("outcome_name"))
    if outcome in {"home", "1"}:
        return "home"
    if outcome in {"draw", "x"}:
        return "draw"
    if outcome in {"away", "2"}:
        return "away"
    if outcome in {"yes", "y"}:
        return "yes"
    if outcome in {"no", "n"}:
        return "no"
    if outcome == "over" or outcome.startswith("over "):
        return "over"
    if outcome == "under" or outcome.startswith("under "):
        return "under"
    if outcome in {"home or draw", "1x"}:
        return "1x"
    if outcome in {"home or away", "12"}:
        return "12"
    if outcome in {"draw or away", "x2", "2x"}:
        return "x2"
    return None


def _put_complete_3way(target: Dict[str, float], rows: Iterable[Dict[str, Any]]) -> None:
    values: Dict[str, float] = {}
    for row in rows:
        key = _outcome_key(row, str(row.get("market_name") or ""))
        val = _price(row.get("odds"))
        if key in {"home", "draw", "away"} and val is not None:
            values[key] = val
    if all(values.get(k) is not None for k in ("home", "draw", "away")):
        target.update({k: values[k] for k in ("home", "draw", "away")})


def _put_complete_dc(target: Dict[str, float], rows: Iterable[Dict[str, Any]]) -> None:
    values: Dict[str, float] = {}
    for row in rows:
        key = _outcome_key(row, str(row.get("market_name") or ""))
        val = _price(row.get("odds"))
        if key in {"1x", "12", "x2"} and val is not None:
            values[key] = val
    if all(values.get(k) is not None for k in ("1x", "12", "x2")):
        target.update({k: values[k] for k in ("1x", "12", "x2")})


def _put_complete_gg(target: Dict[str, float], rows: Iterable[Dict[str, Any]]) -> None:
    values: Dict[str, float] = {}
    for row in rows:
        key = _outcome_key(row, str(row.get("market_name") or ""))
        val = _price(row.get("odds"))
        if key in {"yes", "no"} and val is not None:
            values[key] = val
    if values.get("yes") is not None and values.get("no") is not None:
        target.update({"yes": values["yes"], "no": values["no"]})


def _put_complete_ou(target: Dict[str, Dict[str, float]], line: str, rows: Iterable[Dict[str, Any]]) -> None:
    values: Dict[str, float] = {}
    for row in rows:
        key = _outcome_key(row, str(row.get("market_name") or ""))
        val = _price(row.get("odds"))
        if key in {"over", "under"} and val is not None:
            values[key] = val
    if values.get("over") is not None and values.get("under") is not None:
        target[line] = {"over": values["over"], "under": values["under"]}


def _group_markets(rows: List[Dict[str, Any]]) -> Dict[tuple[str, str], List[Dict[str, Any]]]:
    grouped: Dict[tuple[str, str], List[Dict[str, Any]]] = {}
    for row in rows:
        if not _active(row):
            continue
        key = (str(row.get("market_name") or ""), str(row.get("mark_ins_id") or row.get("specifiers") or ""))
        grouped.setdefault(key, []).append(row)
    return grouped


def _parse_markets(match: Dict[str, Any], rows: List[Dict[str, Any]]) -> None:
    for (market_name, _instance), market_rows in _group_markets(rows).items():
        name = _norm(market_name)
        line = _extract_line(market_name, (market_rows[0] or {}).get("specifiers") if market_rows else "")

        if name == "match result":
            _put_complete_3way(match["odds_1x2"], market_rows)
        elif name == "double chance":
            _put_complete_dc(match["odds_dc"], market_rows)
        elif name == "both teams to score":
            _put_complete_gg(match["odds_gg"], market_rows)
        elif name == "both teams to score 2 or more goals yes/no":
            _put_complete_gg(match["odds_gg_2plus"], market_rows)
        elif name.startswith("over/under") and line:
            if line.endswith(".25") or line.endswith(".75"):
                _put_complete_ou(match["odds_asian_ou"], line, market_rows)
            else:
                _put_complete_ou(match["odds_ou"], line, market_rows)
        elif name == "half-time result":
            _put_complete_3way(match["odds_fh_1x2"], market_rows)
        elif name == "half-time double chance":
            _put_complete_dc(match["odds_fh_dc"], market_rows)
        elif name.startswith("half-time totals over/under") and line:
            _put_complete_ou(match["odds_fh_ou"], line, market_rows)
        elif name == "second half result":
            _put_complete_3way(match["odds_sh_1x2"], market_rows)
        elif name == "second half double chance":
            _put_complete_dc(match["odds_sh_dc"], market_rows)
        elif name.startswith("second half total goals over/under") and line:
            _put_complete_ou(match["odds_sh_ou"], line, market_rows)
        elif name == "team with most corners (with draw)":
            _put_complete_3way(match["odds_corners_1x2"], market_rows)
        elif name == "team with most booking points":
            _put_complete_3way(match["odds_bookings_1x2"], market_rows)
        elif name.startswith("booking points over/under") and line:
            _put_complete_ou(match["odds_bookings_ou"], line, market_rows)


def _metadata(tz: ZoneInfo) -> tuple[Dict[str, str], Dict[str, str]]:
    start, end = _day_bounds_utc(tz)
    data = _get_json("/api/v1/sportsbook/data/coredata", {
        "scheduled_from": start,
        "scheduled_to": end,
    }, retries=2)
    root = data.get("data", data) if isinstance(data, dict) else {}
    categories = {
        str(item.get("public_id")): str(item.get("name") or "")
        for item in _as_list(root.get("categories") if isinstance(root, dict) else [])
    }
    competitions = {
        str(item.get("public_id")): str(item.get("name") or "")
        for item in _as_list(root.get("competitions") if isinstance(root, dict) else [])
    }
    return categories, competitions


def _event_tournament(event: Dict[str, Any], categories: Dict[str, str], competitions: Dict[str, str]) -> str:
    competition = competitions.get(str(event.get("competition_id") or ""), "")
    category = categories.get(str(event.get("category_id") or ""), "")
    if category and competition:
        return f"{category}. {competition}"
    return competition or category or "Football"


def _fetch_event_summaries(tz: ZoneInfo, categories: Dict[str, str], competitions: Dict[str, str]) -> List[Dict[str, Any]]:
    today = datetime.now(tz).date()
    now = datetime.now(tz)
    start, end = _day_bounds_utc(tz)
    data = _get_json("/api/v1/sportsbook/data/sportevents", {
        "scheduled_from": start,
        "scheduled_to": end,
    }, retries=3)
    summaries = []
    for event in _as_list(data):
        if str(event.get("sport_id") or "") != FOOTBALL_SPORT_ID:
            continue
        if event.get("expired") is True or str(event.get("status_label") or "").lower() not in {"not_started", "not started", "prematch"}:
            continue
        kickoff_dt = _dt(event.get("scheduled"), tz)
        if not kickoff_dt or kickoff_dt.date() != today or kickoff_dt <= now:
            continue
        home, away = _split_teams(event)
        tournament = _event_tournament(event, categories, competitions)
        if _is_pseudo(home, away, tournament):
            continue
        public_id = str(event.get("public_id") or "")
        if public_id:
            summaries.append({
                "public_id": public_id,
                "home_team": home,
                "away_team": away,
                "tournament": tournament,
                "kickoff_dt": kickoff_dt,
            })
    summaries.sort(key=lambda item: (item["kickoff_dt"], item["tournament"], item["home_team"], item["away_team"]))
    return summaries


def _chunks(values: List[str], size: int) -> Iterable[List[str]]:
    for index in range(0, len(values), size):
        yield values[index:index + size]


def _fetch_details(ids: List[str]) -> List[Dict[str, Any]]:
    if not ids:
        return []
    details: List[Dict[str, Any]] = []
    batches = list(_chunks(ids, DETAIL_BATCH_SIZE))

    def fetch_batch(batch: List[str]) -> List[Dict[str, Any]]:
        data = _post_json("/api/v1/sportsbook/data/sportevents/details", {"sport_event_ids": batch}, retries=2)
        return _as_list(data)

    with ThreadPoolExecutor(max_workers=min(MAX_DETAIL_WORKERS, len(batches))) as executor:
        futures = [executor.submit(fetch_batch, batch) for batch in batches]
        for future in as_completed(futures):
            try:
                details.extend(future.result())
            except Exception as exc:
                print(f"  WARNING: MulaSport skipped a detail batch: {exc}")
    return details


def _parse_detail(detail: Dict[str, Any], summary: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if detail.get("expired") is True or str(detail.get("status_label") or "").lower() not in {"not_started", "not started", "prematch"}:
        return None
    match = {
        **copy.deepcopy(_EMPTY_MARKETS),
        "home_team": summary["home_team"],
        "away_team": summary["away_team"],
        "tournament": summary["tournament"],
        "kickoff": summary["kickoff_dt"].strftime("%Y-%m-%d %H:%M"),
        "commence_time": summary["kickoff_dt"].strftime("%Y-%m-%d %H:%M:%S"),
        "source": SOURCE,
        "source_event_id": str(detail.get("public_id") or summary["public_id"]),
    }
    _parse_markets(match, detail.get("featured_market_instances") or [])
    if not any(match.get(k) for k in ("odds_1x2", "odds_dc", "odds_ou", "odds_gg", "odds_fh_1x2")):
        return None
    return match


def _fetch_matches() -> List[Dict[str, Any]]:
    tz = ZoneInfo(TIMEZONE)
    categories, competitions = _metadata(tz)
    summaries = _fetch_event_summaries(tz, categories, competitions)
    if not summaries:
        return []
    by_id = {item["public_id"]: item for item in summaries}
    matches: List[Dict[str, Any]] = []
    for detail in _fetch_details(list(by_id.keys())):
        public_id = str(detail.get("public_id") or "")
        summary = by_id.get(public_id)
        if not summary:
            continue
        parsed = _parse_detail(detail, summary)
        if parsed:
            matches.append(parsed)
    matches.sort(key=lambda item: (item["kickoff"], item["tournament"], item["home_team"], item["away_team"]))
    return matches


def _save_outputs(matches: List[Dict[str, Any]], elapsed: float) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    json_path = os.path.join(_DATA_DIR, "mulasport_odds.json")
    txt_path = os.path.join(_DATA_DIR, "mulasport_matches.txt")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(matches, f, indent=2, ensure_ascii=False)
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("MULASPORT GHANA - ALL MATCHES\n")
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
    print("\nMU MU MU MU MU MU MU MU MU MU MU MU MU MU MU MU MU MU MU MU ")
    print("   MULASPORT GHANA SCRAPER")
    print(f"   {datetime.now(tz).strftime('%A, %d %B %Y %H:%M:%S')}")
    print("MU MU MU MU MU MU MU MU MU MU MU MU MU MU MU MU MU MU MU MU \n")

    try:
        matches = _fetch_matches()
    except Exception as exc:
        print(f"WARNING: MulaSport skipped this scan: {exc}")
        matches = []

    elapsed = time.time() - start
    if not matches:
        print("WARNING: No prematch matches found for today.")
    print("MULASPORT GHANA")
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
