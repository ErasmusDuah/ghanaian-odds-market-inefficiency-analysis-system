"""
BetPawa Ghana football prematch odds scraper.

Uses BetPawa's sportsbook v4 protobuf endpoint with the same market view used by
the website, so hidden/unlisted markets are not invented locally.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import struct
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote
from zoneinfo import ZoneInfo

from curl_cffi import requests

try:
    from .fb_output_formatter import format_match_text_block
except ImportError:
    from fb_output_formatter import format_match_text_block

BASE_URL = "https://www.betpawa.com.gh"
TIMEZONE = "Africa/Accra"
BRAND = "betpawa-ghana"
CATEGORY_FOOTBALL = "2"
TAKE = 100
MAX_PAGES = int(os.getenv("BETPAWA_MAX_PAGES", "5"))
REQUEST_TIMEOUT = 20
MAX_WORKERS = int(os.getenv("BETPAWA_WORKERS", "10"))
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

MARKET_CHUNKS: Tuple[Tuple[str, ...], ...] = (
    ("3743", "4693", "3795", "5000", "28000810", "28000850", "3668"),
    ("3685", "4958", "4976", "4673", "4681"),
)

MARKET_1X2 = {"3743": "odds_1x2", "28000810": "odds_1x2_one_up", "28000850": "odds_1x2_two_up", "3668": "odds_fh_1x2", "3685": "odds_sh_1x2"}
MARKET_DC = {"4693": "odds_dc", "4673": "odds_fh_dc", "4681": "odds_sh_dc"}
MARKET_OU = {"5000": "odds_ou", "4958": "odds_fh_ou", "4976": "odds_sh_ou"}
MARKET_GG = "3795"

_EMPTY_MARKETS = {
    "odds_1x2": {}, "odds_dc": {}, "odds_gg": {}, "odds_gg_2plus": {},
    "odds_1x2_two_up": {}, "odds_1x2_one_up": {}, "odds_ou": {}, "odds_asian_ou": {},
    "odds_fh_1x2": {}, "odds_sh_1x2": {}, "odds_fh_ou": {}, "odds_sh_ou": {},
    "odds_fh_dc": {}, "odds_sh_dc": {}, "odds_corners_1x2": {},
    "odds_bookings_1x2": {}, "odds_bookings_ou": {},
}


def _read_varint(buf: bytes, index: int) -> Tuple[int, int]:
    result = 0
    shift = 0
    while index < len(buf):
        byte = buf[index]
        index += 1
        result |= (byte & 0x7F) << shift
        if byte < 0x80:
            return result, index
        shift += 7
    raise EOFError("unterminated varint")


def _iter_fields(buf: bytes):
    index = 0
    while index < len(buf):
        try:
            key, index = _read_varint(buf, index)
        except EOFError:
            return
        field = key >> 3
        wire_type = key & 7
        try:
            if wire_type == 0:
                value, index = _read_varint(buf, index)
                yield field, wire_type, value, b""
            elif wire_type == 1:
                raw = buf[index:index + 8]
                index += 8
                if len(raw) == 8:
                    yield field, wire_type, struct.unpack("<d", raw)[0], raw
            elif wire_type == 2:
                length, index = _read_varint(buf, index)
                raw = buf[index:index + length]
                index += length
                yield field, wire_type, raw, raw
            elif wire_type == 5:
                raw = buf[index:index + 4]
                index += 4
                if len(raw) == 4:
                    yield field, wire_type, struct.unpack("<f", raw)[0], raw
            else:
                return
        except (EOFError, struct.error):
            return


def _submessages(buf: bytes, field_no: int) -> List[bytes]:
    return [raw for field, wire, _value, raw in _iter_fields(buf) if field == field_no and wire == 2]


def _first_str(buf: bytes, field_no: int) -> Optional[str]:
    for field, wire, _value, raw in _iter_fields(buf):
        if field != field_no or wire != 2:
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if all(ch.isprintable() for ch in text):
            return text
    return None


def _first_varint(buf: bytes, field_no: int) -> Optional[int]:
    for field, wire, value, _raw in _iter_fields(buf):
        if field == field_no and wire == 0:
            return int(value)
    return None


def _first_double(buf: bytes, field_no: int) -> Optional[float]:
    for field, wire, value, _raw in _iter_fields(buf):
        if field == field_no and wire == 1:
            return float(value)
    return None


def _price(value: Optional[float]) -> Optional[float]:
    if value is None or value <= 1.0:
        return None
    return round(value, 3)


def _parse_selection(raw: bytes) -> Dict[str, Any]:
    return {
        "name": _first_str(raw, 2),
        "type_id": _first_str(raw, 3),
        "odds": _price(_first_double(raw, 4)),
        "suspended": bool(_first_varint(raw, 5) or False),
        "handicap": _first_str(raw, 6),
        "display": _first_str(raw, 8),
    }


def _parse_market(raw: bytes) -> Optional[Dict[str, Any]]:
    meta_parts = _submessages(raw, 1)
    if not meta_parts:
        return None
    meta = meta_parts[0]
    market = {"type_id": _first_str(meta, 1), "name": _first_str(meta, 2) or "", "selections": []}
    for row in _submessages(raw, 2):
        for sel_raw in _submessages(row, 4):
            selection = _parse_selection(sel_raw)
            if selection.get("odds") is not None and not selection.get("suspended"):
                market["selections"].append(selection)
    return market


def _parse_event(raw: bytes) -> Optional[Dict[str, Any]]:
    event_id = _first_str(raw, 1)
    if not event_id:
        return None

    participants = []
    for part in _submessages(raw, 5):
        position = _first_varint(part, 3) or 0
        name = _first_str(part, 2)
        if name:
            participants.append((position, name))
    participants.sort(key=lambda item: item[0])
    if len(participants) < 2:
        return None

    start_parts = _submessages(raw, 6)
    start_ts = _first_varint(start_parts[0], 1) if start_parts else None
    if start_ts is None:
        return None

    competition_parts = _submessages(raw, 12)
    region_parts = _submessages(raw, 11)
    competition = _first_str(competition_parts[0], 2) if competition_parts else ""
    region = _first_str(region_parts[0], 2) if region_parts else ""
    tournament = f"{region}. {competition}" if region and competition else (competition or region or "Football")

    return {
        "event_id": event_id,
        "home_team": participants[0][1],
        "away_team": participants[1][1],
        "start_ts": int(start_ts),
        "tournament": tournament,
        "markets": [m for m in (_parse_market(mr) for mr in _submessages(raw, 7)) if m],
    }


def _parse_batch(payload: bytes) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for batch in _submessages(payload, 1):
        for event_raw in _submessages(batch, 2):
            event = _parse_event(event_raw)
            if event:
                events.append(event)
    return events


def _headers() -> Dict[str, str]:
    return {
        "Accept": "application/x-protobuf, application/octet-stream, application/json, */*",
        "Referer": f"{BASE_URL}/sports/football",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
        "X-Pawa-Brand": BRAND,
        "X-Pawa-Language": "en",
    }


def _build_query(market_types: Iterable[str], start_utc: datetime, end_utc: datetime, skip: int) -> str:
    body = {
        "queries": [{
            "query": {
                "eventType": "UPCOMING",
                "categories": [CATEGORY_FOOTBALL],
                "hasOdds": True,
                "startTime": {
                    "from": start_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "to": end_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
            },
            "view": {"marketTypes": list(market_types)},
            "take": TAKE,
            "skip": skip,
        }]
    }
    return quote(json.dumps(body, separators=(",", ":")))


def _fetch_page(market_types: Tuple[str, ...], start_utc: datetime, end_utc: datetime, skip: int) -> List[Dict[str, Any]]:
    url = f"{BASE_URL}/api/sportsbook/v4/events/lists/by-queries?q={_build_query(market_types, start_utc, end_utc, skip)}"
    response = requests.get(url, headers=_headers(), timeout=REQUEST_TIMEOUT, impersonate="chrome120")
    if response.status_code != 200 or not response.headers.get("content-type", "").startswith("application/x-protobuf"):
        return []
    return _parse_batch(response.content)


def _is_pseudo_match(home: str, away: str) -> bool:
    h = home.strip().lower()
    a = away.strip().lower()
    if not h or not a or h == a:
        return True
    bad_exact = {"1st team", "2nd team", "first team", "second team", "1st teams", "2nd teams"}
    if h in bad_exact or a in bad_exact:
        return True
    bad_fragments = ("goalscorer", "player specials", "to score", "fantasy", "statistics")
    return any(fragment in h or fragment in a for fragment in bad_fragments)


def _line_key(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return f"{number:.1f}"


def _apply_market(match: Dict[str, Any], market: Dict[str, Any]) -> None:
    market_id = market.get("type_id")
    selections = market.get("selections") or []

    if market_id in MARKET_1X2:
        mapped = {}
        for sel in selections:
            name = str(sel.get("name") or "").upper()
            if name == "1":
                mapped["home"] = sel["odds"]
            elif name == "X":
                mapped["draw"] = sel["odds"]
            elif name == "2":
                mapped["away"] = sel["odds"]
        if len(mapped) == 3:
            match[MARKET_1X2[market_id]] = mapped
        return

    if market_id in MARKET_DC:
        mapped = {}
        for sel in selections:
            name = str(sel.get("name") or "").lower()
            if name in {"1x", "x2", "12"}:
                mapped[name] = sel["odds"]
        if len(mapped) == 3:
            match[MARKET_DC[market_id]] = mapped
        return

    if market_id == MARKET_GG:
        mapped = {}
        for sel in selections:
            name = str(sel.get("name") or "").lower()
            if name == "yes":
                mapped["yes"] = sel["odds"]
            elif name == "no":
                mapped["no"] = sel["odds"]
        if len(mapped) == 2:
            match["odds_gg"] = mapped
        return

    if market_id in MARKET_OU:
        target = match[MARKET_OU[market_id]]
        for sel in selections:
            line = _line_key(sel.get("handicap"))
            if not line:
                continue
            name = str(sel.get("name") or "").lower()
            side = "over" if name == "over" else "under" if name == "under" else None
            if side:
                target.setdefault(line, {})[side] = sel["odds"]


def _convert_event(event: Dict[str, Any], tz: ZoneInfo, target_date) -> Optional[Dict[str, Any]]:
    home = event["home_team"]
    away = event["away_team"]
    if _is_pseudo_match(home, away):
        return None

    kickoff_dt = datetime.fromtimestamp(event["start_ts"], tz=timezone.utc).astimezone(tz)
    if kickoff_dt.date() != target_date:
        return None
    match = {
        "event_id": event["event_id"],
        "home_team": home,
        "away_team": away,
        "kickoff": kickoff_dt.strftime("%Y-%m-%d %H:%M"),
        "tournament": event.get("tournament") or "Football",
        "is_live": False,
        "source": "betpawa_gh",
    }
    for key, value in _EMPTY_MARKETS.items():
        match[key] = dict(value)

    for market in event.get("markets") or []:
        _apply_market(match, market)

    for key in ("odds_ou", "odds_fh_ou", "odds_sh_ou"):
        match[key] = {line: odds for line, odds in match[key].items() if odds.get("over") is not None and odds.get("under") is not None}

    return match


def collect_today_matches() -> List[Dict[str, Any]]:
    tz = ZoneInfo(TIMEZONE)
    now_local = datetime.now(tz)
    target_date = now_local.date()
    start_local = datetime.combine(target_date, datetime.min.time(), tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    start_utc = start_local.astimezone(timezone.utc)
    end_utc = end_local.astimezone(timezone.utc)

    jobs = []
    for chunk in MARKET_CHUNKS:
        for page in range(MAX_PAGES):
            jobs.append((chunk, start_utc, end_utc, page * TAKE))

    merged: Dict[str, Dict[str, Any]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_map = {executor.submit(_fetch_page, *job): job for job in jobs}
        for future in concurrent.futures.as_completed(future_map):
            try:
                events = future.result()
            except Exception:
                continue
            for event in events:
                existing = merged.setdefault(event["event_id"], {k: v for k, v in event.items() if k != "markets"})
                existing.setdefault("markets", [])
                existing["markets"].extend(event.get("markets") or [])

    matches = []
    for event in merged.values():
        match = _convert_event(event, tz, target_date)
        if match:
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

    print("\n" + "🟢 " * 20)
    print("   BETPAWA GHANA SCRAPER (protobuf bulk)")
    print(f"   {now_local.strftime('%A, %d %B %Y %H:%M:%S')}")
    print("🟢 " * 20 + "\n")

    matches = collect_today_matches()
    count = len(matches)

    if count == 0:
        print("⚠️  No prematch matches found for today.")
    else:
        print("📋 BETPAWA GHANA")
        print(f"⚽ Total matches: {count}")
        print("=" * 50)
        head = min(10, count)
        print(f"\n📝 Sample (first {head}):")
        for match in matches[:head]:
            print(f"   {match['home_team']} vs {match['away_team']} | {match['kickoff']} | {match['tournament']}")
        if count > head:
            print(f"  ... and {count - head} more")
        print("=" * 50)

    json_path = os.path.join(_DATA_DIR, "betpawa_odds.json")
    txt_path = os.path.join(_DATA_DIR, "betpawa_matches.txt")

    with open(json_path, "w", encoding="utf-8") as jf:
        json.dump(matches, jf, ensure_ascii=False, indent=2)

    with open(txt_path, "w", encoding="utf-8") as tf:
        tf.write("BETPAWA GHANA - ALL MATCHES\n")
        tf.write(f"Generated: {now_local.strftime('%A, %d %B %Y %H:%M:%S')}\n")
        tf.write(f"Total: {count} matches\n")
        tf.write("=" * 60 + "\n\n")
        if not matches:
            tf.write(f"No prematch football for today ({TIMEZONE}).\n")
        else:
            for match in matches:
                tf.write(format_match_text_block(match))

    elapsed = time.perf_counter() - started
    print(f"💾 Saved to {json_path}")
    print(f"📄 Full list: {txt_path}")
    if count:
        print(f"   Open the .txt file to see all {count} matches!")
    print(f"⏱️  Scraping completed in {elapsed:.1f}s")
    return matches


def main() -> int:
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())