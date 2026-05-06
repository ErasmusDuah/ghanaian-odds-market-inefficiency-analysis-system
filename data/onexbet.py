"""
1xBet Ghana football prematch odds (ASYNC AIOHTTP VERSION).

Fetches all leagues globally and processes odds for all matches occurring today.
Outputs to the standard format required by the arbitrage engine.
"""
from __future__ import annotations

import argparse
import asyncio
import aiohttp
import json
import os
import sys
import time
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

DEFAULT_SITE = "https://1xbet.mobi"
REFERRER = "https://1xbet.mobi/en/line/football"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
TIMEZONE = "Africa/Accra"

OU_TOTALS: Tuple[float, ...] = (1.5, 2.5, 3.5, 4.5, 5.5)
DEFAULT_TF_MS = 172800000


async def async_linefeed_get(
    session: aiohttp.ClientSession,
    site: str,
    method: str,
    params: Dict[str, Any],
    referer: str,
    max_attempts: int = 3,
) -> dict:
    origin = site.rstrip("/")
    # Format query string correctly including array/list parameters
    q_parts = []
    for k, v in params.items():
        if isinstance(v, list):
            for item in v:
                q_parts.append(f"{k}={item}")
        else:
            q_parts.append(f"{k}={v}")
    q = "&".join(q_parts)
    
    url = f"{origin}/service-api/LineFeed/{method}?{q}"
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": referer,
        "Origin": origin,
        "Accept": "application/json",
    }
    
    for attempt in range(max_attempts):
        try:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    if data.get("Success") is False:
                        print(f"  ⚠️ LineFeed error for {method}: {data.get('Error')}")
                        return {}
                    return data
        except Exception as e:
            if attempt == max_attempts - 1:
                print(f"  ⚠️ Request failed for {method} after {max_attempts} attempts: {e}")
            else:
                await asyncio.sleep(0.5 * (attempt + 1))
    return {}


def kickoff_utc_from_game(game: dict) -> Optional[datetime]:
    s = game.get("S")
    if s is None:
        return None
    try:
        return datetime.fromtimestamp(int(s), tz=timezone.utc)
    except (OSError, ValueError, OverflowError, TypeError):
        return None


def ou_json_key(total: float) -> str:
    s = str(total).replace(".", "_")
    return f"over_under_{s}"


def _outcome_price(e: dict) -> Any:
    if e.get("CV") is not None and e.get("CV") != "":
        return e.get("CV")
    return e.get("C")


def iter_linefeed_outcomes(game: dict) -> List[dict]:
    out: List[dict] = []
    for e in game.get("E") or []:
        if isinstance(e, dict):
            out.append(e)
    for ae in game.get("AE") or []:
        for me in ae.get("ME") or []:
            if isinstance(me, dict):
                out.append(me)
    for ge in game.get("GE") or []:
        gid = ge.get("G")
        for col in ge.get("E") or []:
            if not isinstance(col, list):
                continue
            for item in col:
                if not isinstance(item, dict):
                    continue
                e2 = dict(item)
                if e2.get("G") is None and gid is not None:
                    e2["G"] = gid
                out.append(e2)
    return out


def build_odds_block(entries: Iterable[dict]) -> Dict[str, Any]:
    entries = list(entries)
    block: Dict[str, Any] = {}

    x2: Dict[int, Any] = {}
    for e in entries:
        if e.get("G") == 1 and e.get("T") in (1, 2, 3):
            x2[int(e["T"])] = _outcome_price(e)
    if len(x2) == 3:
        block["match_result"] = {"home": x2[1], "draw": x2[2], "away": x2[3]}

    by_p: Dict[float, Dict[int, Any]] = {}
    for e in entries:
        if e.get("G") != 17:
            continue
        t = e.get("T")
        p = e.get("P")
        if t not in (9, 10) or p is None:
            continue
        try:
            pf = round(float(p), 2)
        except (TypeError, ValueError):
            continue
        by_p.setdefault(pf, {})[int(t)] = _outcome_price(e)

    for total in OU_TOTALS:
        row = by_p.get(total)
        if row and 9 in row and 10 in row:
            block[ou_json_key(total)] = {"over": row[9], "under": row[10]}

    gg: Dict[str, Any] = {}
    for e in entries:
        if e.get("G") != 19:
            continue
        t = e.get("T")
        if t == 180:
            gg["gg"] = _outcome_price(e)
        elif t == 181:
            gg["ng"] = _outcome_price(e)
    if gg.get("gg") is not None and gg.get("ng") is not None:
        block["gg_ng"] = gg

    return block


async def fetch_champs_async(session: aiohttp.ClientSession, site: str, tf_ms: int, referer: str) -> List[dict]:
    data = await async_linefeed_get(
        session, site, "GetChampsZip",
        {"sport": 1, "lng": "en", "tf": tf_ms, "tz": 0},
        referer,
    )
    return list(data.get("Value") or [])


async def fetch_champ_games_async(session: aiohttp.ClientSession, site: str, li: int, tf_ms: int, referer: str) -> Optional[dict]:
    return await async_linefeed_get(
        session, site, "GetChampZip",
        {"lng": "en", "champ": li, "tf": tf_ms, "afterDays": 0, "tz": 0, "sport": 1},
        referer,
    )


async def fetch_game_zip_async(session: aiohttp.ClientSession, site: str, game_id: int, referer: str) -> Optional[dict]:
    data = await async_linefeed_get(
        session, site, "GetGameZip",
        {"id": game_id, "lng": "en", "cfview": 0, "isSubGames": "true",
         "GroupEvents": "true", "countevents": 250},
        referer,
    )
    return data.get("Value") if isinstance(data.get("Value"), dict) else None


async def collect_today_games_async(
    site: str,
    tf_ms: int,
    referer: str,
    target: date,
    tz: ZoneInfo,
    now_utc: datetime,
) -> List[dict]:
    # Lower limits significantly to avoid 1xbet rate-limiting/blocking connections
    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(limit=50),
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    ) as session:
        # 1. Fetch all champs (leagues)
        concurrent = asyncio.Semaphore(25)
        champs = await fetch_champs_async(session, site, tf_ms, referer)
        if not champs:
            return []

        # 2. Concurrently fetch all games within those leagues
        sem_champ = asyncio.Semaphore(50)
        
        async def get_champ_games(li: int) -> List[Tuple[int, dict, str]]:
            async with sem_champ:
                blob = await fetch_champ_games_async(session, site, li, tf_ms, referer)
                if not blob or not isinstance(blob.get("Value"), dict):
                    return []
                val = blob["Value"]
                league_name = (val.get("L") or val.get("LE") or "").strip()
                
                out_local = []
                for g in val.get("G") or []:
                    if not isinstance(g, dict):
                        continue
                    gid = g.get("I")
                    if gid is None:
                        continue
                    kick = kickoff_utc_from_game(g)
                    if kick is None:
                        continue
                    local_d = kick.astimezone(tz).date()
                    if local_d != target or kick <= now_utc:
                        continue
                    out_local.append((int(gid), g, league_name))
                return out_local

        print(f"  ⚡ Fetching games for {len(champs)} leagues...")
        champ_tasks = [get_champ_games(ch.get("LI")) for ch in champs if ch.get("LI")]
        champ_results = await asyncio.gather(*champ_tasks)

        stubs = []
        seen_game_ids = set()
        for batch in champ_results:
            for gid, g, league_name in batch:
                if gid not in seen_game_ids:
                    seen_game_ids.add(gid)
                    stubs.append((gid, g, league_name))

        if not stubs:
            return []

        # 3. Concurrently fetch full odds (GetGameZip) for matching games
        sem_game = asyncio.Semaphore(50)
        
        async def load_odds(item: Tuple[int, dict, str]) -> dict:
            gid, stub, league_fallback = item
            async with sem_game:
                league = (stub.get("LE") or stub.get("L") or league_fallback or "").strip()
                kick = kickoff_utc_from_game(stub)
                assert kick is not None
                
                # Base odds from ChampZip
                odds = build_odds_block(iter_linefeed_outcomes(stub))
                
                # Detailed odds from GameZip
                detail = await fetch_game_zip_async(session, site, gid, referer)
                if detail:
                    odds = {**odds, **build_odds_block(iter_linefeed_outcomes(detail))}
                    
                return {
                    "event_id": gid,
                    "kickoff_utc": kick.isoformat().replace("+00:00", "Z"),
                    "league": league,
                    "home_team": (stub.get("O1") or "").strip(),
                    "away_team": (stub.get("O2") or "").strip(),
                    "odds": odds,
                }

        print(f"  ⚡ Fetching full odds lines for {len(stubs)} matches...")
        game_tasks = [load_odds(stub) for stub in stubs]
        matches = await asyncio.gather(*game_tasks)

        matches.sort(key=lambda x: x.get("kickoff_utc") or "")
        return matches


def _convert_to_standard_format(raw_matches: List[dict], tz: ZoneInfo) -> List[dict]:
    standard = []
    for m in raw_matches:
        home_team = (m.get("home_team") or "").strip()
        away_team = (m.get("away_team") or "").strip()
        
        # Skip outrights / special markets that don't have both teams
        if not home_team or not away_team:
            continue

        odds_raw = m.get("odds") or {}

        # --- 1X2 ---
        mr = odds_raw.get("match_result") or {}
        odds_1x2 = {}
        if mr.get("home") and mr.get("draw") and mr.get("away"):
            try:
                odds_1x2 = {
                    "home": float(mr["home"]),
                    "draw": float(mr["draw"]),
                    "away": float(mr["away"]),
                }
            except (TypeError, ValueError):
                odds_1x2 = {}

        # --- Over/Under ---
        odds_ou = {}
        for total in OU_TOTALS:
            internal_key = ou_json_key(total)
            line = odds_raw.get(internal_key)
            if not line:
                continue
            try:
                odds_ou[str(total)] = {
                    "over": float(line["over"]),
                    "under": float(line["under"])
                }
            except (TypeError, ValueError, KeyError):
                continue

        # --- GG/NG ---
        gn = odds_raw.get("gg_ng") or {}
        odds_gg = {}
        if gn.get("gg") and gn.get("ng"):
            try:
                odds_gg = {
                    "yes": float(gn["gg"]),
                    "no":  float(gn["ng"]),
                }
            except (TypeError, ValueError):
                odds_gg = {}

        # --- Kickoff ---
        ku = m.get("kickoff_utc") or ""
        kickoff_str = ""
        if ku:
            try:
                dt = datetime.fromisoformat(ku.replace("Z", "+00:00"))
                kickoff_str = dt.astimezone(tz).strftime("%Y-%m-%d %H:%M")
            except ValueError:
                kickoff_str = ku[:16].replace("T", " ")

        standard.append({
            "home_team":  (m.get("home_team") or "").strip(),
            "away_team":  (m.get("away_team") or "").strip(),
            "kickoff":    kickoff_str,
            "tournament": (m.get("league") or "").strip(),
            "is_live":    False,
            "source":     "1xbet_gh",
            "odds_1x2":   odds_1x2,
            "odds_ou":    odds_ou,
            "odds_gg":    odds_gg,
        })

    return standard


def run() -> List[dict]:
    started = time.perf_counter()

    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (OSError, ValueError):
            pass

    tz = ZoneInfo(TIMEZONE)
    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(tz)
    today = now_local.date()

    print("\n" + "🔵 " * 20)
    print("   1XBET GHANA SCRAPER (ASYNC AIOHTTP)")
    print(f"   {now_local.strftime('%A, %d %B %Y %H:%M:%S')}")
    print("🔵 " * 20 + "\n")

    raw_matches = asyncio.run(collect_today_games_async(
        site=DEFAULT_SITE,
        tf_ms=DEFAULT_TF_MS,
        referer=REFERRER,
        target=today,
        tz=tz,
        now_utc=now_utc,
    ))

    # Convert to standard format
    matches = _convert_to_standard_format(raw_matches, tz)

    n = len(matches)

    if n == 0:
        print(f"⚠️  No prematch matches found for today.")
    else:
        print(f"\n📋 1XBET GHANA")
        print(f"⚽ Total matches: {n}")
        print("=" * 50)
        head = min(10, n)
        print(f"\n📝 Sample (first {head}):")
        for m in matches[:head]:
            print(f"   {m['home_team']} vs {m['away_team']} | {m['kickoff']} | {m['tournament']}")
        if n > head:
            print(f"  ... and {n - head} more")
        print("=" * 50)

    # Save files
    os.makedirs("data", exist_ok=True)
    json_path = os.path.join("data", "onexbet_odds.json")
    txt_path  = os.path.join("data", "onexbet_matches.txt")

    with open(json_path, "w", encoding="utf-8") as jf:
        json.dump(matches, jf, ensure_ascii=False, indent=2)

    with open(txt_path, "w", encoding="utf-8") as tf:
        tf.write("1XBET GHANA - ALL MATCHES\n")
        tf.write(f"Generated: {now_local.strftime('%A, %d %B %Y %H:%M:%S')}\n")
        tf.write(f"Total: {n} matches\n")
        tf.write("=" * 60 + "\n\n")
        
        if not matches:
            tf.write(f"No prematch football for today ({TIMEZONE}).\n")
        else:
            for m in matches:
                tf.write(f"⚽ {m['home_team']} vs {m['away_team']}\n")
                tf.write(f"🏆 {m['tournament']}\n")
                tf.write(f"🕐 {m['kickoff']}\n")
                if m["odds_1x2"]:
                    o = m["odds_1x2"]
                    tf.write(f"1X2: {o['home']} | {o['draw']} | {o['away']}\n")
                for line_str, ou in (m["odds_ou"] or {}).items():
                    tf.write(f"O/U {line_str}: Over {ou['over']} | Under {ou['under']}\n")
                if m["odds_gg"]:
                    gg = m["odds_gg"]
                    tf.write(f"GG/NG: Yes {gg.get('yes', '?')} | No {gg.get('no', '?')}\n")
                tf.write("\n")

    elapsed = time.perf_counter() - started
    print(f"💾 Saved to {json_path}")
    print(f"📄 Full list: {txt_path}")
    if n > 0:
        print(f"   Open the .txt file to see all {n} matches!")
    print(f"⏱️  Scraping completed in {elapsed:.1f}s")

    return matches


def main() -> int:
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())