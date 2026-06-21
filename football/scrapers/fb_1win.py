"""
1win Ghana football prematch odds scraper.

Uses 1win's current sport-frame API plus its Socket.IO odds snapshot stream.
The scraper keeps only today's Ghana-time football matches and records only
markets exposed by the frontend odds stream.
"""
from __future__ import annotations

import json
import os
import re
import socket
import ssl
import base64
import struct
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo

from curl_cffi import requests

try:
    from .fb_output_formatter import format_match_text_block
except ImportError:
    from fb_output_formatter import format_match_text_block

SITE_URL = "https://1win.com"
CONFIG_URL = f"{SITE_URL}/api/web/v1/sdui/templates/by-types"
DEFAULT_API_URL = "https://api-gateway.top-parser.com"
DEFAULT_PARTNER_ID = "44ba10e5-7df2-47ab-a44d-dc93803c7a6e"
TIMEZONE = "Africa/Accra"
REQUEST_TIMEOUT = 20
WS_TIMEOUT = float(os.getenv("ONEWIN_WS_TIMEOUT", "14"))
BATCH_SIZE = int(os.getenv("ONEWIN_WS_BATCH", "20"))
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

_EMPTY_MARKETS = {
    "odds_1x2": {}, "odds_dc": {}, "odds_gg": {}, "odds_gg_2plus": {},
    "odds_1x2_two_up": {}, "odds_1x2_one_up": {}, "odds_ou": {}, "odds_asian_ou": {},
    "odds_fh_1x2": {}, "odds_sh_1x2": {}, "odds_fh_ou": {}, "odds_sh_ou": {},
    "odds_fh_dc": {}, "odds_sh_dc": {}, "odds_corners_1x2": {},
    "odds_bookings_1x2": {}, "odds_bookings_ou": {},
}


def _headers(partner_id: str = DEFAULT_PARTNER_ID) -> Dict[str, str]:
    return {
        "accept": "application/json",
        "content-type": "application/json",
        "x-lang": "en-001",
        "x-user-location": "GH",
        "x-external-partner-id": partner_id,
        "origin": SITE_URL,
        "referer": f"{SITE_URL}/line/football",
    }


def _new_session() -> requests.Session:
    return requests.Session(impersonate="chrome120")


def _extract_initial_data(html: str) -> Dict[str, Any]:
    marker = "window.INITIAL_DATA = "
    start = html.find(marker)
    if start < 0:
        return {}
    start += len(marker)
    level = 0
    in_str = False
    escape = False
    quote = ""
    for idx, ch in enumerate(html[start:], start):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == quote:
                in_str = False
        else:
            if ch in {'"', "'"}:
                in_str = True
                quote = ch
            elif ch == "{":
                level += 1
            elif ch == "}":
                level -= 1
                if level == 0:
                    try:
                        return json.loads(html[start:idx + 1])
                    except json.JSONDecodeError:
                        return {}
    return {}


def _first_params(options: Dict[str, Any], key: str) -> Dict[str, Any]:
    value = options.get(key) or []
    if isinstance(value, list) and value:
        first = value[0]
        if isinstance(first, dict):
            return dict(first.get("params") or {})
    return {}


def fetch_config(session: requests.Session) -> tuple[str, str]:
    try:
        html = session.get(f"{SITE_URL}/line/football", timeout=REQUEST_TIMEOUT).text
        init = _extract_initial_data(html)
        params = {
            "h": (init.get("sduiConfig") or {}).get("hash", "2c37d9e69f4eec29"),
            "country": init.get("country", "GH"),
            "subdivision": init.get("subdivision", "AA"),
        }
        resp = session.get(CONFIG_URL, params=params, headers={"accept": "application/json", "referer": f"{SITE_URL}/line/football"}, timeout=REQUEST_TIMEOUT)
        data = resp.json()
        betting = _first_params(data.get("options") or {}, "moduleBetting")
        return betting.get("apiUrl") or DEFAULT_API_URL, betting.get("partnerId") or DEFAULT_PARTNER_ID
    except Exception:
        return DEFAULT_API_URL, DEFAULT_PARTNER_ID


def _today_bounds(tz: ZoneInfo) -> tuple[datetime, datetime]:
    now = datetime.now(tz)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


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
        "player specials", "goalscorer", "to score", "shots on target", "fantasy",
        "statistics", "specials", "penalty taker", "player to", "duel of the players",
    )
    return any(fragment in text for fragment in bad_fragments)


def fetch_matches(session: requests.Session, api_url: str, partner_id: str) -> List[Dict[str, Any]]:
    tz = ZoneInfo(TIMEZONE)
    start, end = _today_bounds(tz)
    body = {
        "service": "prematch",
        "sportId": 18,
        "limit": 500,
        "excludeSportType": "polybet",
        "startBefore": int(end.astimezone(timezone.utc).timestamp()),
    }
    resp = session.post(f"{api_url}/matches/get-many", headers=_headers(partner_id), data=json.dumps(body), timeout=REQUEST_TIMEOUT)
    items = ((resp.json().get("result") or {}).get("items") or [])
    out: List[Dict[str, Any]] = []
    for item in items:
        try:
            kickoff = datetime.fromtimestamp(int(item.get("startAt")), tz=timezone.utc).astimezone(tz)
        except Exception:
            continue
        if not (start <= kickoff < end):
            continue
        home = str((item.get("homeTeam") or {}).get("name") or "").strip()
        away = str((item.get("awayTeam") or {}).get("name") or "").strip()
        tournament = str((item.get("tournament") or {}).get("slug") or "").replace("-", " ").title()
        if _is_pseudo_match(home, away, tournament):
            continue
        out.append({
            "id": int(item["id"]),
            "source": "1win_gh",
            "home_team": home,
            "away_team": away,
            "tournament": tournament,
            "kickoff": kickoff.strftime("%Y-%m-%d %H:%M"),
            **{k: dict(v) for k, v in _EMPTY_MARKETS.items()},
        })
    return out


class _SocketIOWebSocket:
    def __init__(self, api_url: str, partner_id: str):
        api = api_url.replace("https://", "").replace("http://", "").split("/", 1)[0]
        self.host = api
        self.partner_id = partner_id
        self.sock: Optional[ssl.SSLSocket] = None

    def connect(self) -> None:
        path = f"/push-server-v2/?Language=en-001&externalPartnerId={self.partner_id}&EIO=4&transport=websocket"
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {self.host}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            f"Origin: {SITE_URL}\r\n"
            "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36\r\n\r\n"
        )
        raw = socket.create_connection((self.host, 443), timeout=REQUEST_TIMEOUT)
        self.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=self.host)
        self.sock.settimeout(WS_TIMEOUT)
        self.sock.sendall(req.encode())
        head = b""
        while b"\r\n\r\n" not in head:
            head += self.sock.recv(4096)
        if b"101 Switching Protocols" not in head:
            raise RuntimeError("1win WebSocket upgrade failed")
        self.recv_text()
        self.send_text("40")
        self.recv_text()

    def close(self) -> None:
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass

    def send_text(self, text: str, opcode: int = 1) -> None:
        if not self.sock:
            raise RuntimeError("socket not connected")
        data = text.encode() if isinstance(text, str) else text
        mask = os.urandom(4)
        length = len(data)
        first = 0x80 | opcode
        if length < 126:
            header = bytes([first, 0x80 | length])
        elif length < 65536:
            header = bytes([first, 0x80 | 126]) + struct.pack("!H", length)
        else:
            header = bytes([first, 0x80 | 127]) + struct.pack("!Q", length)
        masked = bytes(data[i] ^ mask[i % 4] for i in range(length))
        self.sock.sendall(header + mask + masked)

    def recv_text(self, timeout: Optional[float] = None) -> str:
        if not self.sock:
            raise RuntimeError("socket not connected")
        old_timeout = self.sock.gettimeout()
        if timeout is not None:
            self.sock.settimeout(timeout)
        try:
            header = self.sock.recv(2)
            if not header:
                return ""
            first, second = header
            opcode = first & 0x0F
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self.sock.recv(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self.sock.recv(8))[0]
            mask = self.sock.recv(4) if second & 0x80 else b""
            data = b""
            while len(data) < length:
                data += self.sock.recv(length - len(data))
            if mask:
                data = bytes(data[i] ^ mask[i % 4] for i in range(length))
            if opcode == 9:
                self.send_text(data, opcode=10)
                return self.recv_text(timeout)
            if opcode == 8:
                return ""
            return data.decode(errors="replace") if opcode == 1 else ""
        finally:
            if timeout is not None:
                self.sock.settimeout(old_timeout)


def fetch_odds_snapshots(api_url: str, partner_id: str, match_ids: List[int]) -> Dict[int, Dict[str, Any]]:
    snapshots: Dict[int, Dict[str, Any]] = {}
    for idx in range(0, len(match_ids), BATCH_SIZE):
        batch = match_ids[idx:idx + BATCH_SIZE]
        ws = _SocketIOWebSocket(api_url, partner_id)
        try:
            ws.connect()
            payload = ["subscribe", {"messageType": "subscribe-match-odds", "data": {"matchIds": batch, "isBaseOddsGroups": False}}]
            ws.send_text("42" + json.dumps(payload, separators=(",", ":")))
            deadline = time.time() + WS_TIMEOUT
            while time.time() < deadline and not all(mid in snapshots for mid in batch):
                try:
                    msg = ws.recv_text(max(0.5, deadline - time.time()))
                except (TimeoutError, socket.timeout):
                    break
                if not msg:
                    break
                if msg == "2":
                    ws.send_text("3")
                    continue
                if not msg.startswith("42"):
                    continue
                try:
                    event = json.loads(msg[2:])[1]
                except Exception:
                    continue
                if event.get("messageType") == "match-odds-snapshot" and isinstance(event.get("data"), dict):
                    data = event["data"]
                    mid = data.get("matchId")
                    if mid in batch:
                        snapshots[int(mid)] = data
        except Exception as exc:
            print(f"  ⚠️ 1win odds batch failed ({len(batch)} matches): {exc}")
        finally:
            ws.close()
    return snapshots


def _price(odd: Dict[str, Any]) -> Optional[float]:
    if odd.get("status") != 1:
        return None
    try:
        value = float(odd.get("cf"))
    except (TypeError, ValueError):
        return None
    if value <= 1.0:
        return None
    return round(value, 3)


def _line_key(raw: Any) -> Optional[str]:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value.is_integer():
        return f"{int(value)}.0"
    return str(value).rstrip("0").rstrip(".")


def _is_asian_line(line: str) -> bool:
    return not line.endswith(".5")


def _put_1x2(match: Dict[str, Any], group: Dict[str, Any], key: str) -> None:
    mapped: Dict[str, float] = {}
    for odd in group.get("oddsList") or []:
        price = _price(odd)
        if price is None:
            continue
        outcome = str(odd.get("outcome") or "").lower()
        if outcome == "1":
            mapped["home"] = price
        elif outcome == "x":
            mapped["draw"] = price
        elif outcome == "2":
            mapped["away"] = price
    if len(mapped) == 3:
        match[key] = mapped


def _put_dc(match: Dict[str, Any], group: Dict[str, Any], key: str) -> None:
    mapped: Dict[str, float] = {}
    for odd in group.get("oddsList") or []:
        price = _price(odd)
        outcome = str(odd.get("outcome") or "").lower()
        if price is not None and outcome in {"1x", "12", "x2"}:
            mapped[outcome] = price
    if len(mapped) == 3:
        match[key] = mapped


def _put_gg(match: Dict[str, Any], group: Dict[str, Any]) -> None:
    mapped: Dict[str, float] = {}
    for odd in group.get("oddsList") or []:
        price = _price(odd)
        outcome = str(odd.get("outcome") or "").lower()
        if price is None:
            continue
        if outcome == "yes":
            mapped["yes"] = price
        elif outcome == "no":
            mapped["no"] = price
    if len(mapped) == 2:
        match["odds_gg"] = mapped


def _put_ou(match: Dict[str, Any], group: Dict[str, Any], key: str, asian_key: Optional[str] = None) -> None:
    target = match[key]
    asian = match[asian_key] if asian_key else None
    for odd in group.get("oddsList") or []:
        price = _price(odd)
        outcome = str(odd.get("outcome") or "").lower()
        line = _line_key((odd.get("vars") or {}).get("v1"))
        if price is None or outcome not in {"over", "under"} or not line:
            continue
        dest = asian if asian is not None and _is_asian_line(line) else target
        dest.setdefault(line, {})[outcome] = price


def apply_snapshot(match: Dict[str, Any], snapshot: Optional[Dict[str, Any]]) -> None:
    if not snapshot:
        return
    for group in snapshot.get("oddsGroups") or []:
        name = str(group.get("name") or "").strip().lower()
        if not name:
            continue
        if name == "full time result":
            _put_1x2(match, group, "odds_1x2")
        elif name.startswith("full time result") and "early payout 2" in name:
            _put_1x2(match, group, "odds_1x2_two_up")
        elif name.startswith("full time result") and "early payout 1" in name:
            _put_1x2(match, group, "odds_1x2_one_up")
        elif name == "double chance":
            _put_dc(match, group, "odds_dc")
        elif name == "both teams to score":
            _put_gg(match, group)
        elif name == "total":
            _put_ou(match, group, "odds_ou", "odds_asian_ou")
        elif name == "1st half. result":
            _put_1x2(match, group, "odds_fh_1x2")
        elif name == "2nd half. result":
            _put_1x2(match, group, "odds_sh_1x2")
        elif name == "1st half. double chance":
            _put_dc(match, group, "odds_fh_dc")
        elif name == "2nd half. double chance":
            _put_dc(match, group, "odds_sh_dc")
        elif name == "1st half. total":
            _put_ou(match, group, "odds_fh_ou")
        elif name == "2nd half. total":
            _put_ou(match, group, "odds_sh_ou")
        elif name == "corners. result":
            _put_1x2(match, group, "odds_corners_1x2")
        elif name in {"yellow cards. result", "cards. result"}:
            _put_1x2(match, group, "odds_bookings_1x2")
        elif name in {"yellow cards. total", "cards. total"}:
            _put_ou(match, group, "odds_bookings_ou")


def save_outputs(matches: List[Dict[str, Any]]) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    json_path = os.path.join(_DATA_DIR, "onewin_odds.json")
    txt_path = os.path.join(_DATA_DIR, "onewin_matches.txt")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(matches, f, ensure_ascii=False, indent=2)
    generated = datetime.now(ZoneInfo(TIMEZONE)).strftime("%A, %d %B %Y %H:%M:%S")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(f"1WIN GHANA - ALL MATCHES\nGenerated: {generated}\nTotal: {len(matches)} matches\n")
        f.write("=" * 60 + "\n\n")
        for match in matches:
            f.write(format_match_text_block(match))
            f.write("\n")
    print(f"💾 Saved to {json_path}")
    print(f"📄 Full list: {txt_path}")


def run() -> List[Dict[str, Any]]:
    start = time.time()
    session = _new_session()
    print("🔵 1win Ghana scraper starting...")
    api_url, partner_id = fetch_config(session)
    matches = fetch_matches(session, api_url, partner_id)
    if not matches:
        print("⚠️ No 1win prematch matches found for today.")
        save_outputs([])
        return []
    snapshots = fetch_odds_snapshots(api_url, partner_id, [m["id"] for m in matches])
    for match in matches:
        apply_snapshot(match, snapshots.get(match["id"]))
        match.pop("id", None)
    save_outputs(matches)
    print(f"✅ 1win: {len(matches)} matches ({len(snapshots)} with odds) in {time.time() - start:.1f}s")
    return matches


if __name__ == "__main__":
    if sys_encoding := getattr(__import__("sys").stdout, "encoding", None):
        if sys_encoding.lower() != "utf-8":
            __import__("sys").stdout.reconfigure(encoding="utf-8")
    run()
