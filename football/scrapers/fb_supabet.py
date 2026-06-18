"""
Supabet Ghana football prematch odds (async API version).

Uses the adv.bet distributor API behind Supabet's sportsbook iframe
(sportsbook-eu01-backend.advbet.com), not browser scraping.

Fast path:
  1) List today's football matches with filter.closesAt (partial view)
  2) Fetch full odds for upcoming fixtures in parallel UUID batches
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import urllib.parse
from datetime import date, datetime, time as dt_time, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

import aiohttp

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (OSError, ValueError):
        pass

SOURCE = "supabet_gh"
ORG_UUID = "17e5b1b7-bb38-d332-4e2f-f1fde542b6b9"
API_BASE = "https://sportsbook-eu01-backend.advbet.com/distributor/api/organizations"
REFERER = f"https://eu01.sportsbook.adv.bet/?orgUuid={ORG_UUID}"
TIMEZONE = "Africa/Accra"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=90, connect=8, sock_read=75)
CHUNK_SIZE = 8
MAX_CONCURRENT_CHUNKS = 12

VIRTUAL_KEYWORDS = (
    "srl",
    "simulated",
    "esport",
    "e-soccer",
    "esoccer",
    "cyber",
    "virtual",
    "efootball",
    "e-football",
)

DC_KEY_MAP = {
    "home_or_draw": "1x",
    "home_or_away": "12",
    "draw_or_away": "x2",
}


def is_virtual(home: str, away: str) -> bool:
    text = f"{home} {away}".lower()
    return any(kw in text for kw in VIRTUAL_KEYWORDS)


def _headers() -> Dict[str, str]:
    return {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Origin": "https://eu01.sportsbook.adv.bet",
        "Referer": REFERER,
    }


def day_bounds_utc(target: date, tz: ZoneInfo) -> Tuple[str, str]:
    start_local = datetime.combine(target, dt_time.min, tzinfo=tz)
    end_local = datetime.combine(target, dt_time.max, tzinfo=tz)
    start_utc = start_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    end_utc = end_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return start_utc, end_utc


async def _get_json(session: aiohttp.ClientSession, params: Dict[str, Any]) -> dict:
    url = f"{API_BASE}/{ORG_UUID}/events?" + urllib.parse.urlencode(params, doseq=True)
    for attempt in range(3):
        try:
            async with session.get(url, headers=_headers(), timeout=REQUEST_TIMEOUT) as resp:
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            if attempt == 2:
                raise
            await asyncio.sleep(0.2 * (attempt + 1))
    return {}


async def fetch_today_stubs(
    session: aiohttp.ClientSession,
    target: date,
    tz: ZoneInfo,
    now_utc: datetime,
) -> List[dict]:
    start_utc, end_utc = day_bounds_utc(target, tz)
    stubs: List[dict] = []
    marker: Optional[str] = None

    while True:
        params: Dict[str, Any] = {
            "sortBy": ["closes_at", "tournament"],
            "cursor.limit": "100",
            "filter.sports": "football",
            "filter.types": "match",
            "filter.closesAt.from": start_utc,
            "filter.closesAt.to": end_utc,
            "view": "partial",
        }
        if marker:
            params["cursor.marker"] = marker

        data = await _get_json(session, params)
        batch = data.get("result") or []
        if not batch:
            break

        for stub in batch:
            if not isinstance(stub, dict):
                continue
            closes = stub.get("closesAt")
            if not closes:
                continue
            kick = datetime.fromisoformat(str(closes).replace("Z", "+00:00"))
            if kick <= now_utc:
                continue
            stubs.append(stub)

        marker = batch[-1].get("uuid")
        if len(batch) < 100 or not marker:
            break

    return stubs


async def fetch_full_chunk(session: aiohttp.ClientSession, uuids: List[str]) -> List[dict]:
    if not uuids:
        return []
    params = {
        "filter.uuids": uuids,
        "view": "full",
        "cursor.limit": str(len(uuids)),
    }
    data = await _get_json(session, params)
    return list(data.get("result") or [])


def _translation_map(fixture: dict) -> Dict[str, Any]:
    translations = ((fixture.get("translations") or {}).get("en") or [])
    out: Dict[str, Any] = {}
    for block in translations:
        if not isinstance(block, dict):
            continue
        out[str(block.get("type") or "")] = block.get("value") or block.get("values")
    return out


def _teams_from_fixture(fixture: dict) -> Tuple[str, str]:
    trans = _translation_map(fixture)
    teams = trans.get("team")
    if isinstance(teams, dict):
        home = str(teams.get("home") or "").strip()
        away = str(teams.get("away") or "").strip()
        if home and away:
            return home, away

    competitors = trans.get("competitor")
    if isinstance(competitors, dict):
        vals = list(competitors.values())
        if len(vals) >= 2:
            return str(vals[0]).strip(), str(vals[1]).strip()
    return "", ""


def _tournament_from_fixture(fixture: dict) -> str:
    trans = _translation_map(fixture)
    season = trans.get("season")
    if isinstance(season, str) and season.strip():
        return season.strip()
    return "Football"


def _active_option_odds(market: Optional[dict]) -> Dict[str, float]:
    if not market or market.get("hidden") or not market.get("open"):
        return {}
    out: Dict[str, float] = {}
    for key, opt in (market.get("options") or {}).items():
        if not isinstance(opt, dict):
            continue
        if opt.get("state") != "active":
            continue
        odd = opt.get("odds")
        if odd is None:
            continue
        try:
            out[str(key)] = float(odd)
        except (TypeError, ValueError):
            continue
    return out


def _pick_3way(raw: Dict[str, float]) -> Dict[str, float]:
    need = ("home", "draw", "away")
    if all(k in raw for k in need):
        return {k: raw[k] for k in need}
    return {}


def _pick_dc(raw: Dict[str, float]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for src, dst in DC_KEY_MAP.items():
        if src in raw:
            out[dst] = raw[src]
    return out if len(out) == 3 else {}


def _pick_gg(raw: Dict[str, float]) -> Dict[str, float]:
    if "yes" in raw and "no" in raw:
        return {"yes": raw["yes"], "no": raw["no"]}
    return {}


def _pick_ou(markets: Dict[str, dict], prefix: str) -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = {}
    for key, market in markets.items():
        if not key.startswith(prefix):
            continue
        line = key[len(prefix) :]
        odds = _active_option_odds(market)
        if "over" in odds and "under" in odds:
            out[line] = {"over": odds["over"], "under": odds["under"]}
    return out


def _partition_ou(all_ou: Dict[str, Dict[str, float]]) -> Tuple[Dict[str, Dict[str, float]], Dict[str, Dict[str, float]]]:
    standard: Dict[str, Dict[str, float]] = {}
    asian: Dict[str, Dict[str, float]] = {}
    for line, row in all_ou.items():
        try:
            val = float(line)
        except ValueError:
            standard[line] = row
            continue
        if val % 1.0 == 0.5:
            standard[line] = row
        else:
            asian[line] = row
    return standard, asian


def parse_markets(markets: Dict[str, dict]) -> Dict[str, Any]:
    odds_1x2 = _pick_3way(_active_option_odds(markets.get("winner3")))
    odds_dc = _pick_dc(_active_option_odds(markets.get("double-chance")))
    odds_gg = _pick_gg(_active_option_odds(markets.get("both-teams-score")))
    odds_1x2_one_up = _pick_3way(_active_option_odds(markets.get("winner3-x-up;1")))
    odds_1x2_two_up = _pick_3way(_active_option_odds(markets.get("winner3-x-up;2")))
    odds_fh_1x2 = _pick_3way(_active_option_odds(markets.get("half-winner;1")))
    odds_sh_1x2 = _pick_3way(_active_option_odds(markets.get("half-winner;2")))
    odds_fh_dc = _pick_dc(_active_option_odds(markets.get("half-double-chance;1")))
    odds_sh_dc = _pick_dc(_active_option_odds(markets.get("half-double-chance;2")))
    odds_corners_1x2 = _pick_3way(_active_option_odds(markets.get("corner-winner")))
    odds_bookings_1x2 = _pick_3way(_active_option_odds(markets.get("booking-winner")))

    all_ou = _pick_ou(markets, "score-over-under;")
    odds_ou, odds_asian_ou = _partition_ou(all_ou)
    odds_fh_ou = _pick_ou(markets, "half-score-over-under;1;")
    odds_sh_ou = _pick_ou(markets, "half-score-over-under;2;")
    odds_bookings_ou = _pick_ou(markets, "booking-over-under;")

    return {
        "odds_1x2": odds_1x2,
        "odds_ou": odds_ou,
        "odds_asian_ou": odds_asian_ou,
        "odds_dc": odds_dc,
        "odds_gg": odds_gg,
        "odds_1x2_one_up": odds_1x2_one_up,
        "odds_1x2_two_up": odds_1x2_two_up,
        "odds_fh_1x2": odds_fh_1x2,
        "odds_sh_1x2": odds_sh_1x2,
        "odds_fh_ou": odds_fh_ou,
        "odds_sh_ou": odds_sh_ou,
        "odds_fh_dc": odds_fh_dc,
        "odds_sh_dc": odds_sh_dc,
        "odds_corners_1x2": odds_corners_1x2,
        "odds_bookings_1x2": odds_bookings_1x2,
        "odds_bookings_ou": odds_bookings_ou,
        "odds_gg_2plus": {},
    }


def base_match(home: str, away: str, kickoff: str, tournament: str) -> dict[str, Any]:
    return {
        "home_team": home,
        "away_team": away,
        "kickoff": kickoff,
        "tournament": tournament,
        "is_live": False,
        "status": "Not start",
        "source": SOURCE,
        "odds_1x2": {},
        "odds_ou": {},
        "odds_asian_ou": {},
        "odds_dc": {},
        "odds_gg": {},
        "odds_1x2_one_up": {},
        "odds_1x2_two_up": {},
        "odds_fh_1x2": {},
        "odds_sh_1x2": {},
        "odds_fh_ou": {},
        "odds_sh_ou": {},
        "odds_fh_dc": {},
        "odds_sh_dc": {},
        "odds_corners_1x2": {},
        "odds_bookings_1x2": {},
        "odds_bookings_ou": {},
        "odds_gg_2plus": {},
    }


def convert_event(item: dict, tz: ZoneInfo) -> Optional[dict]:
    fixture = item.get("fixture") if isinstance(item.get("fixture"), dict) else {}
    betting = item.get("betting") if isinstance(item.get("betting"), dict) else {}
    if fixture.get("state") not in (None, "active"):
        return None
    if betting.get("hidden") or betting.get("suspended"):
        return None

    home, away = _teams_from_fixture(fixture)
    if not home or not away or is_virtual(home, away):
        return None

    closes = fixture.get("closesAt") or fixture.get("metadata", {}).get("startedAt")
    if not closes:
        return None
    kick_utc = datetime.fromisoformat(str(closes).replace("Z", "+00:00"))
    kick_local = kick_utc.astimezone(tz)

    markets = betting.get("markets") if isinstance(betting.get("markets"), dict) else {}
    row = base_match(home, away, kick_local.strftime("%Y-%m-%d %H:%M"), _tournament_from_fixture(fixture))
    row.update(parse_markets(markets))

    if not row.get("odds_1x2"):
        return None
    return row


async def scrape_today_async(target: date, tz: ZoneInfo, now_utc: datetime) -> List[dict]:
    async with aiohttp.ClientSession() as session:
        stubs = await fetch_today_stubs(session, target, tz, now_utc)
        uuids = [str(s["uuid"]) for s in stubs if s.get("uuid")]
        if not uuids:
            return []

        chunks = [uuids[i : i + CHUNK_SIZE] for i in range(0, len(uuids), CHUNK_SIZE)]
        sem = asyncio.Semaphore(MAX_CONCURRENT_CHUNKS)

        async def work(chunk: List[str]) -> List[dict]:
            async with sem:
                return await fetch_full_chunk(session, chunk)

        batches = await asyncio.gather(*(work(chunk) for chunk in chunks))

    matches: List[dict] = []
    for batch in batches:
        for item in batch:
            if not isinstance(item, dict):
                continue
            row = convert_event(item, tz)
            if row:
                matches.append(row)

    matches.sort(key=lambda x: (x["kickoff"], x["tournament"], x["home_team"], x["away_team"]))
    return matches


# ── FORMATTING HELPERS ─────────────────────────────────────────────────────────

def fmt_row(label, val):
    prefix = f"│ {label:<16} "
    val_width = 80 - len(prefix) - 2
    return f"{prefix}{val:<{val_width}} │"


def fmt_box_top(title):
    prefix = f"┌── {title} "
    dash_count = 80 - len(prefix) - 1
    return prefix + "─" * dash_count + "┐"


def fmt_box_bottom():
    return "└" + "─" * 78 + "┘"


def fmt_3way(o):
    if not o or o.get("home") is None or o.get("draw") is None or o.get("away") is None:
        return "N/A"
    return f"Home: {o['home']:<7} │ Draw: {o['draw']:<7} │ Away: {o['away']}"


def fmt_dc(o):
    if not o or o.get("1x") is None or o.get("12") is None or o.get("x2") is None:
        return "N/A"
    return f"1X: {o['1x']:<8} │ 12: {o['12']:<8} │ X2: {o['x2']}"


def fmt_gg(o):
    if not o or o.get("yes") is None or o.get("no") is None:
        return "N/A"
    return f"GG (Yes): {o['yes']:<6} │ NG (No): {o['no']}"


def fmt_ou_section(ou_dict):
    if not ou_dict:
        return fmt_row("", "(No Over/Under lines available)")
    try:
        sorted_keys = sorted(ou_dict.keys(), key=lambda x: float(x))
    except Exception:
        return fmt_row("", "(No Over/Under lines available)")
    rows = []
    for line in sorted_keys:
        try:
            if float(line) % 1.0 != 0.5:
                continue
        except ValueError:
            continue
        ou = ou_dict[line]
        over = ou.get("over")
        under = ou.get("under")
        if over is not None and under is not None:
            rows.append(fmt_row(f"Line {line}", f"Over: {over:<8} │ Under: {under:<8}"))
    if not rows:
        return fmt_row("", "(No Over/Under lines available)")
    return "\n".join(rows)


def fmt_asian_ou_section(ou_dict):
    if not ou_dict:
        return fmt_row("", "(No Asian Over/Under lines available)")
    try:
        sorted_keys = sorted(ou_dict.keys(), key=lambda x: float(x))
    except Exception:
        return fmt_row("", "(No Asian Over/Under lines available)")
    rows = []
    for line in sorted_keys:
        ou = ou_dict[line]
        over = ou.get("over")
        under = ou.get("under")
        if over is not None and under is not None:
            rows.append(fmt_row(f"Line {line}", f"Over: {over:<8} │ Under: {under:<8}"))
    if not rows:
        return fmt_row("", "(No Asian Over/Under lines available)")
    return "\n".join(rows)


def format_match_text_block(m):
    title = f"⚽ {m['home_team']} vs {m['away_team']}"
    if m.get("is_live"):
        title += " (🔴 LIVE)"
    meta = f"🏆 {m['tournament']} │ 🕐 {m['kickoff']}"
    w = 80

    lines = [
        "═" * w,
        title,
        meta,
        "═" * w,
        fmt_box_top("MAIN MARKETS"),
        fmt_row("1X2 (Result)", fmt_3way(m.get("odds_1x2"))),
        fmt_row("Double Chance", fmt_dc(m.get("odds_dc"))),
        fmt_row("GG/NG", fmt_gg(m.get("odds_gg"))),
        fmt_row("1X2 Two Up", fmt_3way(m.get("odds_1x2_two_up"))),
        fmt_row("1X2 One Up", fmt_3way(m.get("odds_1x2_one_up"))),
        fmt_box_bottom(),
        fmt_box_top("OVER/UNDER LINES"),
        fmt_ou_section(m.get("odds_ou")),
        fmt_box_bottom(),
        fmt_box_top("ASIAN OVER/UNDER LINES"),
        fmt_asian_ou_section(m.get("odds_asian_ou")),
        fmt_box_bottom(),
        "",
    ]
    return "\n".join(lines)


def run() -> List[dict]:
    output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    os.makedirs(output_dir, exist_ok=True)
    started = time.perf_counter()

    tz = ZoneInfo(TIMEZONE)
    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(tz)
    today = now_local.date()

    print("\n" + "🟡 " * 20)
    print("   SUPABET GHANA SCRAPER (ASYNC API)")
    print(f"   {now_local.strftime('%A, %d %B %Y %H:%M:%S')}")
    print("🟡 " * 20 + "\n")
    print("  ⚡ Fetching today's football fixtures via adv.bet API...")

    matches = asyncio.run(scrape_today_async(today, tz, now_utc))
    n = len(matches)

    json_path = os.path.join(output_dir, "supabet_odds.json")
    txt_path = os.path.join(output_dir, "supabet_matches.txt")

    with open(json_path, "w", encoding="utf-8") as jf:
        json.dump(matches, jf, ensure_ascii=False, indent=2)
        jf.write("\n")

    with open(txt_path, "w", encoding="utf-8") as tf:
        tf.write("SUPABET GHANA - ALL MATCHES\n")
        tf.write(f"Generated: {now_local.strftime('%A, %d %B %Y %H:%M:%S')}\n")
        tf.write(f"Total: {n} matches\n")
        tf.write("=" * 60 + "\n\n")
        if not matches:
            tf.write(f"No prematch football for today ({TIMEZONE}).\n")
        else:
            for m in matches:
                tf.write(format_match_text_block(m))

    if n == 0:
        print(f"⚠️  No prematch matches found for {today} ({TIMEZONE}).")
    else:
        print(f"\n📋 SUPABET GHANA")
        print(f"⚽ Total matches: {n}")
        print(f"With 1X2 odds: {sum(1 for m in matches if m.get('odds_1x2'))}")
        print(f"With O/U odds:   {sum(1 for m in matches if m.get('odds_ou'))}")
        print(f"With GG odds:    {sum(1 for m in matches if m.get('odds_gg'))}")
        print("=" * 50)
        head = min(10, n)
        print(f"\n📝 Sample (first {head}):")
        for m in matches[:head]:
            print(f"   {m['home_team']} vs {m['away_team']} | {m['kickoff']} | {m['tournament']}")
        if n > head:
            print(f"  ... and {n - head} more")

    elapsed = time.perf_counter() - started
    print("=" * 50)
    print(f"💾 Saved to {json_path}")
    print(f"📄 Full list: {txt_path}")
    if n > 0:
        print(f"   Open the .txt file to see all {n} matches!")
    print(f"⏱️  Scraping completed in {elapsed:.1f}s")
    return matches


if __name__ == "__main__":
    run()
