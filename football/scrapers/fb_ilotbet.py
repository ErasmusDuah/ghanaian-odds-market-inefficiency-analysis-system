"""Ilotbet Ghana football prematch odds scraper."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
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

BASE_URL = "https://www.ilotbet.com"
API_BASE = f"{BASE_URL}/api"
TIMEZONE = "Africa/Accra"
SOURCE = "ilotbet_gh"
REQUEST_TIMEOUT = min(16.0, max(6.0, float(os.getenv("ILOTBET_REQUEST_TIMEOUT", "10"))))
PAGE_SIZE = min(100, max(20, int(os.getenv("ILOTBET_PAGE_SIZE", "100"))))
MAX_PAGES = min(12, max(1, int(os.getenv("ILOTBET_MAX_PAGES", "8"))))
LOW_TODAY_FALLBACK_THRESHOLD = min(80, max(0, int(os.getenv("ILOTBET_LOW_TODAY_FALLBACK_THRESHOLD", "50"))))
DEVICE_SOFT_VERSION = "202607021702"

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA_DIR = os.path.join(_ROOT, "data")

_PRIVATE_KEY_B64 = (
    "MIICdwIBADANBgkqhkiG9w0BAQEFAASCAmEwggJdAgEAAoGBAMMRQOevHKZo7dIM"
    "zB0oKEtijePYOOgI1Z7lrdOMJumjJt7CCeByS40AvdjcQe2PskgkIQclfiIempQo"
    "KoYMIW5UsrzNSf9PB61sPEyTvVZzRC8xARIbuPemFiZX6ywtHFUrVwnuRqLsMPZm"
    "s/+yyyXbB+WNc7Y0OUWkmUWc1QDHAgMBAAECgYAhhtWg/HfwIhi+AXUTjdNfIZFB"
    "l+gv+VS9+rvloDEP9vq3TqJj8UEK+xWmMDUkn44E2DDVCZykQJ5Q2JZ2c59LCjjh"
    "VPr2Krek0aFkGD1DsXU4NDkGVQyBxW1R9O2o//4OS1gwjIG57Q0n+VNscObCR5cv"
    "n/zXHtYPkfodDD0gAQJBAPFDALgqebsWBtkJ5+8Of+ClA0+R3QsXYigz1PH73ar9"
    "OoXd+TuVnYA/AtwR/Jze0dVpjVu8kh6tysbO8MgnKCMCQQDO+9XzBoqKLO8Xa1qb"
    "MpElsSI2Ij147if+RLITCM7s3seGDYWuo8i44ATZSG0rThL38uhlEYZdPsn0187e"
    "RB0NAkBHVhp2WgjYarDnp+guZUkmcWRDOMv1JZrebEUAsAphLrMJNhMlrR1++CKu"
    "U5sv/ypoQeeMQnuqGpUkp7fGVt2lAkEAy064j1bse965FoLfY7QeuCwuU4f8Y61i"
    "YTIuy92KC0akKvtbRPghr95zRM4MVU4B+cSCGsxE85A6JSJZUx8KfQJBAJ7mNB+a"
    "nm7+EsajuoIY6L7SyQM3or/2qgl+R/W7l/xA7/3mJAYIYLoz9il5FBdLhhNUPyHc"
    "Ek9DmeRjKOLRUu4="
)

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


def _der_tlv(data: bytes, index: int) -> tuple[int, bytes, int]:
    tag = data[index]
    index += 1
    first = data[index]
    index += 1
    if first < 128:
        length = first
    else:
        size = first & 0x7F
        length = int.from_bytes(data[index:index + size], "big")
        index += size
    return tag, data[index:index + length], index + length


def _rsa_key() -> tuple[int, int]:
    der = base64.b64decode(_PRIVATE_KEY_B64)
    _, seq, _ = _der_tlv(der, 0)
    values = []
    index = 0
    while index < len(seq):
        tag, content, index = _der_tlv(seq, index)
        values.append((tag, content))
    _, rsa_seq, _ = _der_tlv(values[2][1], 0)
    ints: List[int] = []
    index = 0
    while index < len(rsa_seq):
        tag, content, index = _der_tlv(rsa_seq, index)
        if tag == 2:
            ints.append(int.from_bytes(content.lstrip(b"\x00"), "big"))
    return ints[1], ints[3]

_RSA_N, _RSA_D = _rsa_key()


def _json_compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _sort_params(params: Dict[str, Any]) -> str:
    rows: List[str] = []
    for key in sorted(params):
        value = params[key]
        if value is None:
            continue
        try:
            if len(value) == 0:  # matches frontend signature exclusion for empty strings/lists
                continue
        except TypeError:
            pass
        if isinstance(value, (dict, list)):
            value = _json_compact(value)
        rows.append(f"{key}={value}")
    return "&".join(rows)


def _sign(params: Dict[str, Any]) -> str:
    base = _sort_params(params)
    first_md5 = hashlib.md5(base.encode("utf-8")).hexdigest()
    digest = hashlib.md5(first_md5.encode("utf-8")).digest()
    digest_info = bytes.fromhex("3020300c06082a864886f70d020505000410") + digest
    size = (_RSA_N.bit_length() + 7) // 8
    block = b"\x00\x01" + b"\xff" * (size - len(digest_info) - 3) + b"\x00" + digest_info
    signature = pow(int.from_bytes(block, "big"), _RSA_D, _RSA_N).to_bytes(size, "big")
    return base64.b64encode(signature).decode("ascii")


def _headers(signature: str, timestamp: int) -> Dict[str, str]:
    return {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en",
        "Content-Type": "application/json;charset=UTF-8",
        "Country-Code": "gh",
        "Lang": "en",
        "Referer": f"{BASE_URL}/gh/pc/sports/list/NotStarted/sr:sport:1/null/null/null",
        "Sign": signature,
        "TimeStamp": str(timestamp),
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
        "Version": DEVICE_SOFT_VERSION,
    }


def _session() -> requests.Session:
    session = requests.Session(impersonate="chrome124")
    session.get(f"{BASE_URL}/gh/pc/sports/list/NotStarted/sr:sport:1/null/null/null", timeout=REQUEST_TIMEOUT)
    return session


def _api_get(session: requests.Session, path: str, data: Dict[str, Any]) -> Any:
    timestamp = int(time.time() * 1000)
    common = {
        "platform": 3,
        "platformModel": "1.0",
        "loginChannel": "ilot",
        "deviceCode": timestamp,
        "deviceSoftVersion": DEVICE_SOFT_VERSION,
        "timestamp": timestamp,
    }
    params = {**common, **data}
    signature = _sign(params)
    response = session.get(
        f"{API_BASE}{path}",
        params=params,
        headers=_headers(signature, timestamp),
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 0:
        raise RuntimeError(f"Ilotbet API error for {path}: {payload}")
    return payload.get("data")


def _today_bounds(tz: ZoneInfo) -> tuple[str, str]:
    now = datetime.now(tz)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = now.replace(hour=23, minute=59, second=59, microsecond=0)
    return start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")


def _parse_dt(raw: Any, tz: ZoneInfo) -> Optional[datetime]:
    if not raw:
        return None
    text = str(raw)
    if re.search(r"[+-]\d{4}$", text):
        text = text[:-5] + text[-5:-2] + ":" + text[-2:]
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(tz)
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


def _line_from_text(*values: Any) -> Optional[str]:
    for value in values:
        text = str(value or "")
        match = re.search(r"(?<!\d)(\d+(?:\.\d+)?)(?!\d)", text)
        if not match:
            continue
        number = float(match.group(1))
        if number.is_integer():
            return f"{int(number)}.0"
        return str(number).rstrip("0").rstrip(".")
    return None


def _is_active_odd(odd: Dict[str, Any]) -> bool:
    return str(odd.get("active")) == "1" and _price(odd.get("odds")) is not None


def _active_odds(market: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [odd for odd in (market.get("odds") or []) if _is_active_odd(odd)]


def _put_3way(target: Dict[str, float], market: Dict[str, Any]) -> None:
    values: Dict[str, float] = {}
    for odd in _active_odds(market):
        label = f"{odd.get('hname') or ''} {odd.get('id') or ''}".lower()
        price = _price(odd.get("odds"))
        if price is None:
            continue
        if "home" in label or re.search(r"\b1\b", label):
            values["home"] = price
        elif "draw" in label or re.search(r"\b2\b", label):
            values["draw"] = price
        elif "away" in label or re.search(r"\b3\b", label):
            values["away"] = price
    if all(k in values for k in ("home", "draw", "away")):
        target.update({k: values[k] for k in ("home", "draw", "away")})


def _put_dc(target: Dict[str, float], market: Dict[str, Any]) -> None:
    values: Dict[str, float] = {}
    for odd in _active_odds(market):
        label = str(odd.get("hname") or odd.get("name") or "").lower().replace("/", "")
        price = _price(odd.get("odds"))
        if price is None:
            continue
        if "1x" in label or "1x" == label or "1orx" in label:
            values["1x"] = price
        elif "12" in label or "1or2" in label:
            values["12"] = price
        elif "x2" in label or "xor2" in label:
            values["x2"] = price
    if all(k in values for k in ("1x", "12", "x2")):
        target.update({k: values[k] for k in ("1x", "12", "x2")})


def _put_yes_no(target: Dict[str, float], market: Dict[str, Any]) -> None:
    values: Dict[str, float] = {}
    for odd in _active_odds(market):
        label = str(odd.get("hname") or odd.get("name") or "").lower()
        price = _price(odd.get("odds"))
        if price is None:
            continue
        if "yes" in label:
            values["yes"] = price
        elif "no" in label:
            values["no"] = price
    if "yes" in values and "no" in values:
        target.update({"yes": values["yes"], "no": values["no"]})


def _put_ou(target: Dict[str, Dict[str, float]], market: Dict[str, Any]) -> None:
    line: Optional[str] = None
    values: Dict[str, float] = {}
    for odd in _active_odds(market):
        label = str(odd.get("hname") or odd.get("name") or "").lower()
        price = _price(odd.get("odds"))
        line = line or _line_from_text(odd.get("name"), market.get("name"), market.get("nameAlias"))
        if price is None:
            continue
        if "over" in label:
            values["over"] = price
        elif "under" in label:
            values["under"] = price
    if line and "over" in values and "under" in values:
        target[line] = {"over": values["over"], "under": values["under"]}


def _is_pseudo(local: Dict[str, Any]) -> bool:
    combined = " ".join(str(local.get(k) or "") for k in ("home_team", "away_team", "league"))
    low = combined.lower()
    if any(word in low for word in VIRTUAL_KEYWORDS):
        return True
    if _engine_is_pseudo_match:
        try:
            return bool(_engine_is_pseudo_match(local))
        except Exception:
            return False
    return False


def _parse_markets(raw_markets: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    markets = copy.deepcopy(_EMPTY_MARKETS)
    for market in raw_markets or []:
        odds = _active_odds(market)
        if not odds:
            continue
        alias = str(market.get("nameAlias") or "").lower()
        name = str(market.get("name") or "").lower()
        group = str(market.get("groupType") or "").lower()
        market_id = str(market.get("marketId") or "")
        combined = f"{alias} {name} {group}"

        is_first_half = "1st half" in combined or "first half" in combined
        is_second_half = "2nd half" in combined or "second half" in combined

        if market_id == "1601" and "1up" in combined:
            _put_3way(markets["odds_1x2_one_up"], market)
        elif market_id == "1601" and "2up" in combined:
            _put_3way(markets["odds_1x2_two_up"], market)
        elif market_id == "1" or alias == "1x2":
            if is_first_half:
                _put_3way(markets["odds_fh_1x2"], market)
            elif is_second_half:
                _put_3way(markets["odds_sh_1x2"], market)
            else:
                _put_3way(markets["odds_1x2"], market)
        elif market_id == "10" or "double chance" in combined:
            if is_first_half:
                _put_dc(markets["odds_fh_dc"], market)
            elif is_second_half:
                _put_dc(markets["odds_sh_dc"], market)
            else:
                _put_dc(markets["odds_dc"], market)
        elif market_id == "29" or "both teams to score" in combined or "gg|ng" in combined:
            _put_yes_no(markets["odds_gg"], market)
        elif "each team to score 2" in combined or "score 2 or more" in combined:
            _put_yes_no(markets["odds_gg_2plus"], market)
        elif (market_id == "18" or alias == "total") and "corner" not in combined and "booking" not in combined:
            if is_first_half:
                _put_ou(markets["odds_fh_ou"], market)
            elif is_second_half:
                _put_ou(markets["odds_sh_ou"], market)
            else:
                _put_ou(markets["odds_ou"], market)
        elif "total bookings" in combined or market_id == "139":
            _put_ou(markets["odds_bookings_ou"], market)
    return markets


def _fetch_today_raw_matches() -> List[Dict[str, Any]]:
    tz = ZoneInfo(TIMEZONE)
    today = datetime.now(tz).date()
    start, end = _today_bounds(tz)
    session = _session()
    raw_matches: List[Dict[str, Any]] = []
    seen: set[str] = set()

    def add_match(match: Dict[str, Any]) -> bool:
        match_id = str(match.get("matchId") or match.get("id") or match.get("eventId") or "")
        if match_id and match_id in seen:
            return False
        if match_id:
            seen.add(match_id)
        raw_matches.append(match)
        return True

    def fetch_pages(base_params: Dict[str, Any], stop_after_today_window: bool = False) -> None:
        for page in range(1, MAX_PAGES + 1):
            params = {
                "sportId": "sr:sport:1",
                "pageSize": PAGE_SIZE,
                "pageNum": page,
                **base_params,
            }
            data = _api_get(session, "/sbu/un/m/pre/matches", params) or {}
            page_matches: List[Dict[str, Any]] = []
            for group in data.get("list") or []:
                page_matches.extend(group.get("matchList") or [])

            page_new = 0
            has_today = False
            for match in page_matches:
                kickoff = _parse_dt(match.get("scheduledTime"), tz)
                if kickoff and kickoff.date() == today:
                    has_today = True
                if add_match(match):
                    page_new += 1

            if len(page_matches) < PAGE_SIZE or page_new == 0:
                break
            # The undated feed is ordered by kickoff. Once a full page has no
            # today games, later pages are future fixtures and can be skipped.
            if stop_after_today_window and not has_today:
                break

    fetch_pages({"st": start, "et": end})

    today_count = sum(
        1 for match in raw_matches
        if (kickoff := _parse_dt(match.get("scheduledTime"), tz)) and kickoff.date() == today
    )
    if today_count < LOW_TODAY_FALLBACK_THRESHOLD:
        fetch_pages({}, stop_after_today_window=True)

    return raw_matches


def collect_today_matches() -> List[Dict[str, Any]]:
    tz = ZoneInfo(TIMEZONE)
    today = datetime.now(tz).date()
    output: List[Dict[str, Any]] = []
    for raw in _fetch_today_raw_matches():
        kickoff = _parse_dt(raw.get("scheduledTime"), tz)
        if not kickoff or kickoff.date() != today:
            continue
        home = str(raw.get("homeName") or "").strip()
        away = str(raw.get("awayName") or "").strip()
        league = " - ".join(part for part in [str(raw.get("categoryName") or "").strip(), str(raw.get("tournamentName") or "").strip()] if part)
        if not home or not away:
            continue
        match = {
            "home_team": home,
            "away_team": away,
            "league": league,
            "tournament": league,
            "start_time": kickoff.strftime("%Y-%m-%d %H:%M"),
            "kickoff": kickoff.strftime("%Y-%m-%d %H:%M"),
            "source": SOURCE,
            "match_id": raw.get("matchId") or raw.get("eventId"),
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        }
        if _is_pseudo(match):
            continue
        parsed_markets = _parse_markets(raw.get("markets") or [])
        if not any(parsed_markets.get(key) for key in _EMPTY_MARKETS):
            continue
        match.update(parsed_markets)
        output.append(match)
    output.sort(key=lambda item: (item.get("start_time") or "", item.get("league") or "", item.get("home_team") or ""))
    return output


def _write_outputs(matches: List[Dict[str, Any]]) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    json_path = os.path.join(_DATA_DIR, "ilotbet_odds.json")
    txt_path = os.path.join(_DATA_DIR, "ilotbet_matches.txt")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(matches, f, ensure_ascii=False, indent=2)
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("ILOTBET GHANA - ALL MATCHES\n")
        f.write(f"Generated: {datetime.now(ZoneInfo(TIMEZONE)).strftime('%A, %d %B %Y %H:%M:%S')}\n")
        f.write(f"Total: {len(matches)} matches\n")
        f.write("=" * 60 + "\n\n")
        for match in matches:
            f.write(format_match_text_block(match))
            f.write("\n")


def run() -> List[Dict[str, Any]]:
    start = time.time()
    print("\n" + "IL " * 20)
    print("   ILOTBET GHANA SCRAPER")
    print(f"   {datetime.now(ZoneInfo(TIMEZONE)).strftime('%A, %d %B %Y %H:%M:%S')}")
    print("IL " * 20 + "\n")
    matches = collect_today_matches()
    if not matches:
        print("WARNING: No prematch matches found for today.")
    _write_outputs(matches)
    print("ILOTBET GHANA")
    print(f"Total matches fetched: {len(matches)}")
    print(f"With 1X2 odds: {sum(1 for m in matches if m.get('odds_1x2'))}")
    print(f"With O/U odds: {sum(1 for m in matches if m.get('odds_ou'))}")
    print(f"With DC odds:  {sum(1 for m in matches if m.get('odds_dc'))}")
    print(f"With GG odds:  {sum(1 for m in matches if m.get('odds_gg'))}")
    print(f"Scraping completed in {time.time() - start:.1f}s")
    print(f"Saved to {os.path.join(_DATA_DIR, 'ilotbet_odds.json')}")
    print(f"Full list: {os.path.join(_DATA_DIR, 'ilotbet_matches.txt')}")
    return matches


if __name__ == "__main__":
    run()

