"""Betbooker Ghana football prematch odds scraper."""
from __future__ import annotations

import base64
import copy
import json
import math
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from curl_cffi import requests
from wasmtime import Func, FuncType, Linker, Module, Store, ValType

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

BASE_URL = "https://sport.btbkr1.com"
HOME_URL = f"{BASE_URL}/SportsBook/Home"
PARTNER_ID = 975
PARTNER_UUID = "24863a46-14c5-475d-be9f-f370427b08fe"
LANG_ID = 2
COUNTRY_CODE = "GH"
TIMEZONE = "Africa/Accra"
SOURCE = "betbooker_gh"
REQUEST_TIMEOUT = min(18.0, max(6.0, float(os.getenv("BETBOOKER_REQUEST_TIMEOUT", "12"))))
MAX_CHAMP_WORKERS = min(12, max(1, int(os.getenv("BETBOOKER_CHAMP_WORKERS", "10"))))
MAX_DETAIL_WORKERS = min(24, max(1, int(os.getenv("BETBOOKER_DETAIL_WORKERS", "18"))))
DISCOVERY_STAKE_TYPES = (1, 37, 3, 26, 2533)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA_DIR = os.path.join(_ROOT, "data")
_WASM_PATH = os.path.join(_DATA_DIR, "betbooker_decrypt.wasm")
_WASM_URL = f"{BASE_URL}/Scripts/dec/decrypt.wasm"
_THREAD_LOCAL = threading.local()

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


class _BetbookerDecryptor:
    def __init__(self, wasm_bytes: bytes):
        self.store = Store()
        module = Module(self.store.engine, wasm_bytes)
        linker = Linker(self.store.engine)
        memory_ref: Dict[str, Any] = {"memory": None}

        def timer_func(_callback_id: int, _delay: float) -> int:
            return 0

        def exit_func(_code: int) -> None:
            return None

        def noarg_void() -> None:
            return None

        def abort_func() -> None:
            raise RuntimeError("Betbooker decryptor aborted")

        def grow_heap(size: int) -> int:
            memory = memory_ref["memory"]
            if memory is None:
                return 0
            current = memory.data_len(self.store)
            if size <= current:
                return 1
            try:
                memory.grow(self.store, math.ceil((size - current) / 65536))
                return 1
            except Exception:
                return 0

        imports = (
            ("a", FuncType([ValType.i32(), ValType.f64()], [ValType.i32()]), timer_func),
            ("b", FuncType([ValType.i32()], []), exit_func),
            ("c", FuncType([], []), noarg_void),
            ("d", FuncType([], []), abort_func),
            ("e", FuncType([ValType.i32()], [ValType.i32()]), grow_heap),
        )
        for name, signature, callback in imports:
            linker.define(self.store, "a", name, Func(self.store, signature, callback))

        instance = linker.instantiate(self.store, module)
        exports = instance.exports(self.store)
        memory_ref["memory"] = exports["f"]
        exports["g"](self.store)

        self.memory = exports["f"]
        self.alloc = exports["h"]
        self.free = exports["i"]
        self.decrypt_func = exports["j"]
        self.get_result = exports["k"]
        self.get_result_len = exports["l"]
        self.free_result = exports["m"]

    def decrypt_json(self, payload: str) -> Any:
        raw = base64.b64decode(payload)
        ptr = self.alloc(self.store, len(raw))
        self.memory.write(self.store, raw, ptr)
        code = self.decrypt_func(self.store, ptr, len(raw))
        self.free(self.store, ptr)
        if code != 0:
            raise RuntimeError(f"Betbooker payload decrypt failed with code {code}")
        out_ptr = self.get_result(self.store)
        out_len = self.get_result_len(self.store)
        text = self.memory.read(self.store, out_ptr, out_ptr + out_len).decode("utf-8")
        self.free_result(self.store)
        return json.loads(text)


def _headers(referer: Optional[str] = None) -> Dict[str, str]:
    return {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": referer or f"{BASE_URL}/{PARTNER_UUID}/Tools/RequestHelper?parent=betbooker.com",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
        "X-Requested-With": "XMLHttpRequest",
    }


def _session() -> requests.Session:
    session = requests.Session(impersonate="chrome124")
    session.get(HOME_URL, headers=_headers(HOME_URL), timeout=REQUEST_TIMEOUT)
    return session


def _thread_session() -> requests.Session:
    session = getattr(_THREAD_LOCAL, "betbooker_session", None)
    if session is None:
        session = _session()
        _THREAD_LOCAL.betbooker_session = session
    return session


def _thread_decryptor(wasm_bytes: bytes) -> _BetbookerDecryptor:
    decryptor = getattr(_THREAD_LOCAL, "betbooker_decryptor", None)
    if decryptor is None:
        decryptor = _BetbookerDecryptor(wasm_bytes)
        _THREAD_LOCAL.betbooker_decryptor = decryptor
    return decryptor


def _ensure_wasm(session: requests.Session) -> bytes:
    os.makedirs(_DATA_DIR, exist_ok=True)
    try:
        data = open(_WASM_PATH, "rb").read()
        if data.startswith(b"\x00asm"):
            return data
    except OSError:
        pass

    response = session.get(
        _WASM_URL,
        headers={**_headers(f"{BASE_URL}/Scripts/dec/decrypt.js"), "Accept": "*/*"},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    data = response.content
    if not data.startswith(b"\x00asm"):
        raise RuntimeError("Betbooker decrypt.wasm download was not a valid wasm file")
    with open(_WASM_PATH, "wb") as f:
        f.write(data)
    return data


def _clean_params(params: Dict[str, Any]) -> List[tuple[str, Any]]:
    rows: List[tuple[str, Any]] = []
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple, set)):
            rows.extend((key, item) for item in value)
        elif isinstance(value, bool):
            rows.append((key, str(value).lower()))
        else:
            rows.append((key, value))
    return rows


def _api_get(session: requests.Session, decryptor: _BetbookerDecryptor, path: str, params: Dict[str, Any]) -> Any:
    base_params = {
        "langId": LANG_ID,
        "partnerId": PARTNER_ID,
        "countryCode": COUNTRY_CODE,
    }
    merged = {**params, **{k: v for k, v in base_params.items() if k not in params}}
    query = urlencode(_clean_params(merged), doseq=True)
    url = f"{BASE_URL}/{PARTNER_UUID}/{path.lstrip('/')}"
    if query:
        url += "?" + query
    response = session.get(url, headers=_headers(), timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    if not response.content:
        return []
    data = response.json()
    if isinstance(data, dict) and data.get("payload"):
        return decryptor.decrypt_json(data["payload"])
    return data


def _day_bounds(tz: ZoneInfo) -> tuple[str, str]:
    now = datetime.now(tz)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    end = now.replace(hour=23, minute=59, second=59, microsecond=999000).astimezone(timezone.utc)
    return (
        start.isoformat(timespec="seconds").replace("+00:00", "Z"),
        end.isoformat(timespec="seconds").replace("+00:00", "Z"),
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


def _active_stakes(market: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    for stake in market.get("Stakes") or []:
        if stake.get("IsA") is False or stake.get("IsC") is False:
            continue
        price = _price(stake.get("F"))
        if price is None:
            continue
        rows.append(stake)
    return rows


def _put_3way(target: Dict[str, float], market: Dict[str, Any]) -> None:
    values: Dict[str, float] = {}
    for stake in _active_stakes(market):
        name = str(stake.get("N") or stake.get("SN") or "").lower()
        sc = stake.get("SC")
        key = None
        if sc == 1 or name in {"win1", "home", "team 1"}:
            key = "home"
        elif sc == 2 or name in {"x", "draw"}:
            key = "draw"
        elif sc == 3 or name in {"win2", "away", "team 2"}:
            key = "away"
        price = _price(stake.get("F"))
        if key and price is not None:
            values[key] = price
    if all(values.get(k) is not None for k in ("home", "draw", "away")):
        target.update({k: values[k] for k in ("home", "draw", "away")})


def _put_dc(target: Dict[str, float], market: Dict[str, Any]) -> None:
    values: Dict[str, float] = {}
    for stake in _active_stakes(market):
        name = str(stake.get("N") or stake.get("SN") or "").lower().replace(" ", "")
        sc = stake.get("SC")
        key = None
        if sc == 1 or name == "1x":
            key = "1x"
        elif sc == 2 or name == "12":
            key = "12"
        elif sc == 3 or name in {"x2", "2x"}:
            key = "x2"
        price = _price(stake.get("F"))
        if key and price is not None:
            values[key] = price
    if all(values.get(k) is not None for k in ("1x", "12", "x2")):
        target.update({k: values[k] for k in ("1x", "12", "x2")})


def _put_gg(target: Dict[str, float], market: Dict[str, Any]) -> None:
    values: Dict[str, float] = {}
    for stake in _active_stakes(market):
        name = str(stake.get("N") or stake.get("SN") or "").lower()
        sc = stake.get("SC")
        key = None
        if sc == 1 or name == "yes":
            key = "yes"
        elif sc == 2 or name == "no":
            key = "no"
        price = _price(stake.get("F"))
        if key and price is not None:
            values[key] = price
    if values.get("yes") is not None and values.get("no") is not None:
        target.update({"yes": values["yes"], "no": values["no"]})


def _put_ou(target: Dict[str, Dict[str, float]], market: Dict[str, Any]) -> None:
    grouped: Dict[str, Dict[str, float]] = {}
    for stake in _active_stakes(market):
        line = _line_key(stake.get("A"))
        if not line:
            continue
        name = str(stake.get("N") or stake.get("SN") or "").lower()
        sc = stake.get("SC")
        key = None
        if sc == 1 or name.startswith("over"):
            key = "over"
        elif sc == 2 or name.startswith("under"):
            key = "under"
        price = _price(stake.get("F"))
        if key and price is not None:
            grouped.setdefault(line, {})[key] = price
    for line, values in grouped.items():
        if values.get("over") is not None and values.get("under") is not None:
            target[line] = {"over": values["over"], "under": values["under"]}


def _norm_market_name(name: Any) -> str:
    return re.sub(r"\s+", " ", str(name or "").strip().lower())


def _is_simple_period_market(name: str) -> bool:
    blocked = (" and ", " or ", "/", "minute", "score first", "highest scoring", "both teams")
    return not any(token in name for token in blocked)


def _apply_market(match: Dict[str, Any], market: Dict[str, Any]) -> None:
    name = _norm_market_name(market.get("N"))
    market_id = market.get("Id")

    if market_id == 1 or name == "result":
        _put_3way(match["odds_1x2"], market)
    elif market_id == 37 or name == "double chance":
        _put_dc(match["odds_dc"], market)
    elif market_id in {3, -3} or name == "total":
        _put_ou(match["odds_ou"], market)
    elif market_id in {2533, -2533} or name == "asian total":
        _put_ou(match["odds_asian_ou"], market)
    elif market_id == 26 or name == "both teams to score":
        _put_gg(match["odds_gg"], market)
    elif "each team" in name and "score" in name and ("2" in name or "two" in name):
        _put_gg(match["odds_gg_2plus"], market)
    elif "two up" in name and "result" in name:
        _put_3way(match["odds_1x2_two_up"], market)
    elif "one up" in name and "result" in name:
        _put_3way(match["odds_1x2_one_up"], market)
    elif name in {"1st half", "1st half result", "first half", "first half result"}:
        _put_3way(match["odds_fh_1x2"], market)
    elif name in {"2nd half", "2nd half result", "second half", "second half result"}:
        _put_3way(match["odds_sh_1x2"], market)
    elif "1st half" in name and "double chance" in name and _is_simple_period_market(name):
        _put_dc(match["odds_fh_dc"], market)
    elif "2nd half" in name and "double chance" in name and _is_simple_period_market(name):
        _put_dc(match["odds_sh_dc"], market)
    elif "1st half" in name and "total" in name and _is_simple_period_market(name):
        _put_ou(match["odds_fh_ou"], market)
    elif "2nd half" in name and "total" in name and _is_simple_period_market(name):
        _put_ou(match["odds_sh_ou"], market)
    elif "corner" in name and ("result" in name or "1x2" in name):
        _put_3way(match["odds_corners_1x2"], market)
    elif any(token in name for token in ("booking", "yellow card", "cards")):
        if "team" in name:
            return
        if "result" in name or "1x2" in name:
            _put_3way(match["odds_bookings_1x2"], market)
        elif "total" in name:
            _put_ou(match["odds_bookings_ou"], market)


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


def _event_is_today_prematch(event: Dict[str, Any], tz: ZoneInfo, today, now: datetime) -> bool:
    if int(event.get("SId") or 0) != 1:
        return False
    if event.get("IsInL") is True or event.get("IsA") is False:
        return False
    kickoff = _dt(event.get("D"), tz)
    if not kickoff or kickoff.date() != today or kickoff <= now:
        return False
    return True


def _parse_event(event: Dict[str, Any], tz: ZoneInfo, today, now: datetime) -> Optional[Dict[str, Any]]:
    if isinstance(event, list):
        event = event[0] if event else {}
    if not isinstance(event, dict) or not _event_is_today_prematch(event, tz, today, now):
        return None

    home = str(event.get("HT") or "").strip()
    away = str(event.get("AT") or "").strip()
    country = str(event.get("CtN") or "").strip()
    champ = str(event.get("CN") or "").strip()
    tournament = ". ".join(part for part in (country, champ) if part) or champ or country or "Football"
    if _is_pseudo(home, away, tournament):
        return None

    kickoff = _dt(event.get("D"), tz)
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
        "source_event_id": str(event.get("Id") or ""),
    }

    for market in event.get("StakeTypes") or []:
        _apply_market(match, market)

    if not any(match.get(k) for k in (
        "odds_1x2", "odds_dc", "odds_ou", "odds_asian_ou", "odds_gg", "odds_fh_1x2"
    )):
        return None
    return match


def _championship_ids(sports: Sequence[Dict[str, Any]]) -> List[int]:
    ids = set()
    for sport in sports:
        if int(sport.get("Id") or 0) != 1 and str(sport.get("N") or "").lower() != "football":
            continue
        for champ in sport.get("CSH") or sport.get("Championships") or []:
            try:
                ids.add(int(champ.get("Id")))
            except (TypeError, ValueError):
                continue
    return sorted(ids)


def _event_id(event: Dict[str, Any]) -> Optional[int]:
    try:
        return int(event.get("Id"))
    except (TypeError, ValueError):
        return None


def _fetch_champ_events(champ_id: int, start: str, end: str, wasm_bytes: bytes) -> List[Dict[str, Any]]:
    session = _thread_session()
    decryptor = _thread_decryptor(wasm_bytes)
    try:
        data = _api_get(session, decryptor, "Prematch/GetEventsList", {
            "champId": champ_id,
            "stakeTypes": DISCOVERY_STAKE_TYPES,
            "timeFilter": 0,
            "tournamentStart": start,
            "tournamentEnd": end,
        })
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _fetch_event_detail(event_id: int, wasm_bytes: bytes) -> Optional[Dict[str, Any]]:
    session = _thread_session()
    decryptor = _thread_decryptor(wasm_bytes)
    try:
        data = _api_get(session, decryptor, "Common/GetEvent", {
            "eventId": event_id,
            "isLive": False,
        })
        if isinstance(data, list):
            return data[0] if data else None
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _collect_matches() -> List[Dict[str, Any]]:
    tz = ZoneInfo(TIMEZONE)
    today = datetime.now(tz).date()
    now = datetime.now(tz)
    start, end = _day_bounds(tz)

    session = _session()
    wasm_bytes = _ensure_wasm(session)
    decryptor = _BetbookerDecryptor(wasm_bytes)
    sports = _api_get(session, decryptor, "Prematch/GetSportsWithChampionships", {
        "stakeTypes": DISCOVERY_STAKE_TYPES,
        "timeFilter": 0,
    })
    champ_ids = _championship_ids(sports if isinstance(sports, list) else [])
    if not champ_ids:
        return []

    candidates: Dict[int, Dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=min(MAX_CHAMP_WORKERS, len(champ_ids))) as executor:
        futures = [executor.submit(_fetch_champ_events, champ_id, start, end, wasm_bytes) for champ_id in champ_ids]
        for future in as_completed(futures):
            for event in future.result() or []:
                if not _event_is_today_prematch(event, tz, today, now):
                    continue
                event_id = _event_id(event)
                if event_id is not None:
                    candidates[event_id] = event

    if not candidates:
        return []

    matches: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(MAX_DETAIL_WORKERS, len(candidates))) as executor:
        futures = [executor.submit(_fetch_event_detail, event_id, wasm_bytes) for event_id in candidates]
        for future in as_completed(futures):
            detail = future.result()
            parsed = _parse_event(detail or {}, tz, today, now)
            if parsed:
                matches.append(parsed)

    matches.sort(key=lambda item: (item["kickoff"], item["tournament"], item["home_team"], item["away_team"]))
    return matches


def _save_outputs(matches: List[Dict[str, Any]], elapsed: float) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    json_path = os.path.join(_DATA_DIR, "betbooker_odds.json")
    txt_path = os.path.join(_DATA_DIR, "betbooker_matches.txt")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(matches, f, indent=2, ensure_ascii=False)
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("BETBOOKER GHANA - ALL MATCHES\n")
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
    print("\nBB BB BB BB BB BB BB BB BB BB BB BB BB BB BB BB BB BB BB BB ")
    print("   BETBOOKER GHANA SCRAPER")
    print(f"   {datetime.now(tz).strftime('%A, %d %B %Y %H:%M:%S')}")
    print("BB BB BB BB BB BB BB BB BB BB BB BB BB BB BB BB BB BB BB BB \n")

    try:
        matches = _collect_matches()
    except Exception as exc:
        print(f"WARNING: Betbooker skipped this scan: {exc}")
        matches = []

    elapsed = time.time() - start
    if not matches:
        print("WARNING: No prematch matches found for today.")
    print("BETBOOKER GHANA")
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



