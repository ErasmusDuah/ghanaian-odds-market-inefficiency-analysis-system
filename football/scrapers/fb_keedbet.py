"""Keedbet Ghana football prematch odds scraper."""
from __future__ import annotations

import ast
import copy
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

from websocket import create_connection

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

BASE_URL = "https://keedbet.com.gh"
FEED_URL = (
    "wss://keedbet.com.gh/direct-feed/feed?brand=GHANA&"
    "x-api-key=562458b8-dd27-488d-af20-6a9293813564"
)
TIMEZONE = "Africa/Accra"
SOURCE = "keedbet_gh"
SPORT_KEY = "F"
PREMATCH_STAGE = 1
REQUEST_TIMEOUT = min(12.0, max(2.0, float(os.getenv("KEEDBET_REQUEST_TIMEOUT", "4"))))
TOURNAMENT_WORKERS = min(48, max(4, int(os.getenv("KEEDBET_TOURNAMENT_WORKERS", "32"))))
MARKET_WORKERS = min(16, max(2, int(os.getenv("KEEDBET_MARKET_WORKERS", "8"))))
MARKET_BATCH_SIZE = min(60, max(5, int(os.getenv("KEEDBET_MARKET_BATCH_SIZE", "35"))))

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA_DIR = os.path.join(_ROOT, "data")

_SETTINGS = {
    "language": "en",
    "channel": "DESKTOP_AIR_PM",
    "brand": "GHANA",
    "user": None,
    "currency": "GHS",
    "xApiKey": "562458b8-dd27-488d-af20-6a9293813564",
}

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
    "ereplays", "replays",
)


def _today_bounds() -> Tuple[datetime, datetime]:
    tz = ZoneInfo(TIMEZONE)
    now = datetime.now(tz)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start.replace(hour=23, minute=59, second=59, microsecond=999999)
    return start, end


def _decode_frame_rows(messages: Iterable[Dict[str, Any]]) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    rows: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for message in messages:
        item = message.get("item") or {}
        for row in item.get("data") or []:
            if row.get("isRemoved"):
                continue
            key = row.get("key")
            if isinstance(key, str):
                raw_key = key
                try:
                    key = ast.literal_eval(key)
                except (SyntaxError, ValueError):
                    key = {"raw": raw_key}
            if not isinstance(key, dict):
                key = {"id": key}
            value = row.get("value") or {}
            rows.append((key, value))
    return rows


def _stream(target: str, args: List[Any], timeout: Optional[float] = None) -> List[Dict[str, Any]]:
    timeout = timeout or REQUEST_TIMEOUT
    ws = create_connection(FEED_URL, timeout=timeout)
    try:
        ws.settimeout(max(1.0, timeout / 2))
        ws.send(json.dumps({"protocol": "json", "version": 1}, separators=(",", ":")) + "\x1e")
        try:
            ws.recv()
        except Exception:
            pass

        payload = {
            "type": 4,
            "invocationId": "1",
            "target": target,
            "arguments": args + [_SETTINGS],
        }
        ws.send(json.dumps(payload, separators=(",", ":")) + "\x1e")
        deadline = time.time() + timeout
        buffer = ""
        messages: List[Dict[str, Any]] = []
        while time.time() < deadline:
            try:
                chunk = ws.recv()
            except Exception:
                break
            if isinstance(chunk, bytes):
                chunk = chunk.decode("utf-8", "replace")
            buffer += chunk
            while "\x1e" in buffer:
                frame, buffer = buffer.split("\x1e", 1)
                if not frame:
                    continue
                try:
                    message = json.loads(frame)
                except json.JSONDecodeError:
                    continue
                if message.get("type") == 3 and message.get("error"):
                    raise RuntimeError(f"{target} failed: {message.get('error')}")
                messages.append(message)
                if (message.get("item") or {}).get("isInitialBatch") is True:
                    return messages
        return messages
    finally:
        try:
            ws.close()
        except Exception:
            pass


def _stream_rows(target: str, args: List[Any], timeout: Optional[float] = None) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    return _decode_frame_rows(_stream(target, args, timeout=timeout))


def _price(outcome: Dict[str, Any]) -> Optional[float]:
    if outcome.get("isFrozen") or outcome.get("isRemoved"):
        return None
    raw = outcome.get("odd")
    try:
        value = float(raw) / 100.0
    except (TypeError, ValueError):
        return None
    if value <= 1.0:
        return None
    return round(value, 3)


def _set_price(target: Dict[str, float], key: str, outcome: Dict[str, Any]) -> None:
    value = _price(outcome)
    if value is not None:
        target[key] = value


def _line(item: Dict[str, Any], fallback: Any = None) -> Optional[str]:
    params = (item.get("key") or {}).get("marketParameters") or []
    value = params[0] if params else fallback
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer():
        return str(int(number)) + ".0"
    return str(number).rstrip("0").rstrip(".")


def _is_real_match(match: Dict[str, Any]) -> bool:
    if _engine_is_pseudo_match:
        try:
            if _engine_is_pseudo_match(match):
                return False
        except Exception:
            pass
    text = " ".join(str(match.get(k, "")) for k in ("home_team", "away_team", "tournament", "league")).lower()
    return not any(word in text for word in VIRTUAL_KEYWORDS)


def _parse_markets(rows: List[Tuple[Dict[str, Any], Dict[str, Any]]]) -> Dict[str, Any]:
    markets = copy.deepcopy(_EMPTY_MARKETS)
    for key, market in rows:
        try:
            market_type = int(key.get("marketType"))
            period = int(key.get("period") or 0)
        except (TypeError, ValueError):
            continue
        if market.get("isRemoved"):
            continue
        items = market.get("marketItems") or []
        for item in items:
            if item.get("isRemoved"):
                continue
            outcomes = item.get("outcomes") or []

            if market_type == 2 and period in (0, 1, 2):
                dest = markets["odds_1x2" if period == 0 else "odds_fh_1x2" if period == 1 else "odds_sh_1x2"]
                for outcome in outcomes:
                    otype = (outcome.get("key") or {}).get("type")
                    if otype == 0:
                        _set_price(dest, "home", outcome)
                    elif otype == 1:
                        _set_price(dest, "draw", outcome)
                    elif otype == 3:
                        _set_price(dest, "away", outcome)

            elif market_type == 3 and period in (0, 1, 2):
                dest = markets["odds_dc" if period == 0 else "odds_fh_dc" if period == 1 else "odds_sh_dc"]
                for outcome in outcomes:
                    otype = (outcome.get("key") or {}).get("type")
                    if otype == 10:
                        _set_price(dest, "1x", outcome)
                    elif otype == 8:
                        _set_price(dest, "12", outcome)
                    elif otype == 9:
                        _set_price(dest, "x2", outcome)

            elif market_type == 28 and period == 0:
                for outcome in outcomes:
                    otype = (outcome.get("key") or {}).get("type")
                    if otype == 14:
                        _set_price(markets["odds_gg"], "yes", outcome)
                    elif otype == 15:
                        _set_price(markets["odds_gg"], "no", outcome)

            elif market_type == 204 and period == 0:
                # Localization: "Both teams to score twice".
                for outcome in outcomes:
                    otype = (outcome.get("key") or {}).get("type")
                    if otype in (14, 6):
                        _set_price(markets["odds_gg_2plus"], "yes", outcome)
                    elif otype in (15, 7):
                        _set_price(markets["odds_gg_2plus"], "no", outcome)

            elif market_type == 5 and period in (0, 1, 2):
                line = _line(item)
                if line is None:
                    continue
                dest = markets["odds_ou" if period == 0 else "odds_fh_ou" if period == 1 else "odds_sh_ou"]
                row = dest.setdefault(line, {})
                for outcome in outcomes:
                    otype = (outcome.get("key") or {}).get("type")
                    if otype == 4:
                        _set_price(row, "over", outcome)
                    elif otype == 5:
                        _set_price(row, "under", outcome)
                if not (row.get("over") and row.get("under")):
                    dest.pop(line, None)

            elif market_type == 195 and period == 0:
                line = _line(item)
                if line is None:
                    continue
                row = markets["odds_asian_ou"].setdefault(line, {})
                for outcome in outcomes:
                    otype = (outcome.get("key") or {}).get("type")
                    if otype == 4:
                        _set_price(row, "over", outcome)
                    elif otype == 5:
                        _set_price(row, "under", outcome)
                if not (row.get("over") and row.get("under")):
                    markets["odds_asian_ou"].pop(line, None)

    return markets


def _fetch_tournaments() -> Dict[str, Dict[str, Any]]:
    rows = _stream_rows("GetTournamentsBySport", [SPORT_KEY, PREMATCH_STAGE], timeout=REQUEST_TIMEOUT)
    tournaments: Dict[str, Dict[str, Any]] = {}
    for key, value in rows:
        tid = str(key.get("id") or key.get("raw") or key)
        if not tid or value.get("isCybersport"):
            continue
        if int(value.get("prematchEventsCount") or 0) <= 0:
            continue
        tournaments[tid] = value
    return tournaments


def _fetch_categories() -> Dict[str, Dict[str, Any]]:
    rows = _stream_rows("GetCategoriesBySport", [SPORT_KEY], timeout=REQUEST_TIMEOUT)
    return {str(key.get("id") or key.get("raw") or key): value for key, value in rows if not value.get("isCybersport")}


def _fetch_tournament_events(tournament_id: str) -> List[Tuple[str, Dict[str, Any]]]:
    try:
        rows = _stream_rows("GetEventsByTournamentIdAndStage", [tournament_id, PREMATCH_STAGE], timeout=REQUEST_TIMEOUT)
    except Exception as exc:
        print(f"  WARNING: Keedbet tournament {tournament_id} skipped: {exc}")
        return []
    events: List[Tuple[str, Dict[str, Any]]] = []
    for key, value in rows:
        event_id = str(key.get("id") or key.get("raw") or key)
        if event_id:
            events.append((event_id, value))
    return events


def _fetch_market_batch(event_ids: List[str]) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    if not event_ids:
        return []
    try:
        return _stream_rows("GetMarketsByEventIds", [event_ids, ""], timeout=max(REQUEST_TIMEOUT, 8.0))
    except Exception as exc:
        print(f"  WARNING: Keedbet market batch skipped ({len(event_ids)} events): {exc}")
        return []


def _chunked(values: List[str], size: int) -> Iterable[List[str]]:
    for idx in range(0, len(values), size):
        yield values[idx:idx + size]


def collect_today_matches() -> List[Dict[str, Any]]:
    start, end = _today_bounds()
    categories = _fetch_categories()
    tournaments = _fetch_tournaments()

    raw_events: List[Tuple[str, Dict[str, Any]]] = []
    with ThreadPoolExecutor(max_workers=TOURNAMENT_WORKERS) as executor:
        futures = [executor.submit(_fetch_tournament_events, tid) for tid in tournaments]
        for future in as_completed(futures):
            raw_events.extend(future.result())

    today_events: Dict[str, Dict[str, Any]] = {}
    for event_id, event in raw_events:
        if event.get("sport") != SPORT_KEY or int(event.get("stage") or 0) != PREMATCH_STAGE:
            continue
        if event.get("isCybersport") or int(event.get("tradingStatus") or 0) != 1:
            continue
        competitors = event.get("competitors") or []
        if len(competitors) < 2:
            continue
        try:
            kickoff = datetime.fromtimestamp(int(event.get("startTime")), timezone.utc).astimezone(ZoneInfo(TIMEZONE))
        except (TypeError, ValueError, OSError):
            continue
        if not (start <= kickoff <= end):
            continue
        event["_kickoff_dt"] = kickoff
        today_events[event_id] = event

    market_rows: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    event_ids = list(today_events.keys())
    with ThreadPoolExecutor(max_workers=MARKET_WORKERS) as executor:
        futures = [executor.submit(_fetch_market_batch, batch) for batch in _chunked(event_ids, MARKET_BATCH_SIZE)]
        for future in as_completed(futures):
            market_rows.extend(future.result())

    markets_by_event: Dict[str, List[Tuple[Dict[str, Any], Dict[str, Any]]]] = {}
    for key, value in market_rows:
        event_id = str(key.get("eventId") or "")
        if event_id in today_events:
            markets_by_event.setdefault(event_id, []).append((key, value))

    matches: List[Dict[str, Any]] = []
    scraped_at = datetime.now(ZoneInfo(TIMEZONE)).isoformat(timespec="seconds")
    for event_id, event in today_events.items():
        competitors = event.get("competitors") or []
        home = competitors[0].get("name") or ""
        away = competitors[1].get("name") or ""
        tournament = tournaments.get(str(event.get("tournamentId")), {})
        category = categories.get(str(event.get("categoryId")), {})
        league_parts = [category.get("name"), tournament.get("name")]
        league = ". ".join(part for part in league_parts if part)
        kickoff_dt = event["_kickoff_dt"]
        match = {
            "home_team": home,
            "away_team": away,
            "league": league,
            "tournament": league,
            "start_time": kickoff_dt.strftime("%Y-%m-%d %H:%M"),
            "kickoff": kickoff_dt.strftime("%Y-%m-%d %H:%M"),
            "source": SOURCE,
            "match_id": event_id,
            "scraped_at": scraped_at,
            "is_live": False,
            **_parse_markets(markets_by_event.get(event_id, [])),
        }
        if _is_real_match(match):
            matches.append(match)

    matches.sort(key=lambda m: (m.get("kickoff", ""), m.get("tournament", ""), m.get("home_team", "")))
    return matches


def _write_outputs(matches: List[Dict[str, Any]]) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    json_path = os.path.join(_DATA_DIR, "keedbet_odds.json")
    txt_path = os.path.join(_DATA_DIR, "keedbet_matches.txt")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(matches, f, ensure_ascii=False, indent=2)
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("KEEDBET GHANA - ALL MATCHES\n")
        f.write(f"Generated: {datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}\n")
        f.write(f"Total: {len(matches)} matches\n")
        f.write("=" * 60 + "\n\n")
        for match in matches:
            f.write(format_match_text_block(match))
            f.write("\n\n")
    print(f"Saved to {json_path}")
    print(f"Full list: {txt_path}")


def run() -> List[Dict[str, Any]]:
    print("\n" + "KB " * 20)
    print("   KEEDBET GHANA SCRAPER")
    print("   " + datetime.now().strftime("%A, %d %B %Y %H:%M:%S"))
    print("KB " * 20 + "\n")
    start = time.time()
    matches = collect_today_matches()
    if not matches:
        print("No prematch matches found for today.")
    _write_outputs(matches)
    print("KEEDBET GHANA")
    print(f"Total matches fetched: {len(matches)}")
    print(f"With 1X2 odds: {sum(1 for m in matches if m.get('odds_1x2'))}")
    print(f"With O/U odds: {sum(1 for m in matches if m.get('odds_ou'))}")
    print(f"With DC odds:  {sum(1 for m in matches if m.get('odds_dc'))}")
    print(f"With GG odds:  {sum(1 for m in matches if m.get('odds_gg'))}")
    print(f"Scraping completed in {time.time() - start:.1f}s")
    return matches


if __name__ == "__main__":
    run()
