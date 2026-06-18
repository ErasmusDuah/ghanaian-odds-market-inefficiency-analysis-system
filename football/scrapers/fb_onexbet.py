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

DEFAULT_SITE = "https://1xbet.com.gh"
REFERRER = "https://1xbet.com.gh/en/line/football"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
TIMEZONE = "Africa/Accra"

OU_TOTALS: Tuple[float, ...] = (1.5, 2.5, 3.5, 4.5, 5.5)
DEFAULT_TF_MS = 172800000
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10, connect=4, sock_read=8)
CHAMP_CONCURRENCY = 40
DETAIL_CONCURRENCY = 24
FAST_BULK_LIMIT = 50
FAST_BULK_PARAM_SETS = [
    {
        "sports": 1,
        "count": FAST_BULK_LIMIT,
        "lng": "en",
        "mode": 4,
        "getEmpty": "true",
    },
    {
        "sports": 1,
        "count": FAST_BULK_LIMIT,
        "lng": "en",
        "mode": 1,
    },
]


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
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
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
        if isinstance(e, dict) and not e.get("B"):
            out.append(e)
    for ae in game.get("AE") or []:
        for me in ae.get("ME") or []:
            if isinstance(me, dict) and not me.get("B"):
                out.append(me)
    for ge in game.get("GE") or []:
        gid = ge.get("G")
        for col in ge.get("E") or []:
            if not isinstance(col, list):
                continue
            for item in col:
                if not isinstance(item, dict):
                    continue
                if item.get("B"):
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
        {"sport": 1, "lng": "en", "tf": tf_ms, "tz": 0, "country": 80},
        referer,
    )
    return list(data.get("Value") or [])


async def fetch_champ_games_async(session: aiohttp.ClientSession, site: str, li: int, tf_ms: int, referer: str) -> Optional[dict]:
    return await async_linefeed_get(
        session, site, "GetChampZip",
        {
            "lng": "en",
            "champ": li,
            "tf": tf_ms,
            "afterDays": 0,
            "tz": 0,
            "sport": 1,
            "country": 80,
        },
        referer,
    )


async def fetch_game_zip_async(session: aiohttp.ClientSession, site: str, game_id: int, referer: str) -> Optional[dict]:
    data = await async_linefeed_get(
        session, site, "GetGameZip",
        {"id": game_id, "lng": "en", "cfview": 0, "isSubGames": "true",
         "GroupEvents": "true", "countevents": 250, "country": 80},
        referer,
    )
    return data.get("Value") if isinstance(data.get("Value"), dict) else None


async def fetch_fast_bulk_async(session: aiohttp.ClientSession, site: str, referer: str) -> List[dict]:
    for params in FAST_BULK_PARAM_SETS:
        data = await async_linefeed_get(
            session,
            site,
            "Get1x2_VZip",
            params,
            referer,
            max_attempts=1,
        )
        value = data.get("Value")
        if isinstance(value, list) and value:
            return value
    return []


def _is_noise_league(league: str) -> bool:
    lower_league = league.lower()
    return any(x in lower_league for x in [
        'alternative', 'matches of the day', 'player props',
        'special bets', 'shots', 'corners', 'cards', 'stats',
        'virtual', 'cyber'
    ])


def collect_from_fast_bulk(
    games: List[dict],
    target: date,
    tz: ZoneInfo,
    now_utc: datetime,
) -> List[dict]:
    matches: List[dict] = []
    seen_game_ids = set()

    for game in games:
        if not isinstance(game, dict):
            continue

        gid = game.get("I")
        if gid is None or gid in seen_game_ids:
            continue

        kick = kickoff_utc_from_game(game)
        if kick is None:
            continue
        if kick.astimezone(tz).date() != target or kick <= now_utc:
            continue

        league = (game.get("LE") or game.get("L") or "").strip()
        if _is_noise_league(league):
            continue

        home_team = (game.get("O1") or "").strip()
        away_team = (game.get("O2") or "").strip()
        if not home_team or not away_team:
            continue
        if '/' in home_team or '/' in away_team:
            continue

        odds = build_odds_block(iter_linefeed_outcomes(game))
        if not odds.get("match_result"):
            continue

        seen_game_ids.add(gid)
        matches.append({
            "event_id": int(gid),
            "kickoff_utc": kick.isoformat().replace("+00:00", "Z"),
            "league": league,
            "home_team": home_team,
            "away_team": away_team,
            "odds": odds,
        })

    matches.sort(key=lambda x: x.get("kickoff_utc") or "")
    return matches


async def collect_today_games_async(
    site: str,
    tf_ms: int,
    referer: str,
    target: date,
    tz: ZoneInfo,
    now_utc: datetime,
) -> List[dict]:
    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(limit=80, ttl_dns_cache=300),
        timeout=REQUEST_TIMEOUT,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    ) as session:
        # 1. Fetch all champs (leagues)
        champs = await fetch_champs_async(session, site, tf_ms, referer)
        if not champs:
            return []

        # 2. Concurrently fetch all games within those leagues
        sem_champ = asyncio.Semaphore(CHAMP_CONCURRENCY)
        
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

        # 3. Concurrently fetch main game details and subgames
        sem_detail = asyncio.Semaphore(DETAIL_CONCURRENCY)

        def extract_1x2(ents, g):
            x = {}
            for e in ents:
                if e.get("G") == g and e.get("T") in (1, 2, 3):
                    x[int(e["T"])] = _outcome_price(e)
            if len(x) == 3:
                return {"home": float(x[1]), "draw": float(x[2]), "away": float(x[3])}
            return {}
            
        def extract_dc(ents, g):
            x = {}
            for e in ents:
                if e.get("G") == g and e.get("T") in (4, 5, 6):
                    x[int(e["T"])] = _outcome_price(e)
            if len(x) == 3:
                return {"1x": float(x[4]), "12": float(x[5]), "x2": float(x[6])}
            return {}
            
        def extract_ou(ents, g):
            by_p = {}
            for e in ents:
                if e.get("G") == g and e.get("T") in (9, 10):
                    p = e.get("P")
                    if p is None:
                        continue
                    try:
                        pf = round(float(p), 2)
                        by_p.setdefault(pf, {})[int(e["T"])] = _outcome_price(e)
                    except (TypeError, ValueError):
                        continue
            res = {}
            for line, row in by_p.items():
                if 9 in row and 10 in row:
                    res[str(line)] = {"over": float(row[9]), "under": float(row[10])}
            return res
            
        def extract_gg(ents, g):
            x = {}
            for e in ents:
                if e.get("G") == g:
                    if e.get("T") == 180:
                        x["yes"] = _outcome_price(e)
                    elif e.get("T") == 181:
                        x["no"] = _outcome_price(e)
            if "yes" in x and "no" in x:
                return {"yes": float(x["yes"]), "no": float(x["no"])}
            return {}

        async def process_stub(item: Tuple[int, dict, str]) -> Optional[dict]:
            gid, stub, league_fallback = item
            async with sem_detail:
                detail = await fetch_game_zip_async(session, site, gid, referer)
            if not detail:
                return None
                
            home_team = (detail.get("O1") or "").strip()
            away_team = (detail.get("O2") or "").strip()
            if not home_team or not away_team:
                return None
                
            league = (detail.get("LE") or detail.get("L") or league_fallback or "").strip()
            
            # Parse main markets
            entries = iter_linefeed_outcomes(detail)
            
            odds_1x2 = extract_1x2(entries, 1)
            if not odds_1x2:
                return None
                
            odds_1x2_two_up = extract_1x2(entries, 11581)
            odds_dc = extract_dc(entries, 8)
            odds_ou = extract_ou(entries, 17)
            odds_gg = extract_gg(entries, 19)
            
            # Find subgames
            sg = detail.get("SG") or []
            fh_id, sh_id, corners_id, bookings_id = None, None, None, None
            for game in sg:
                tg = game.get("TG")
                pn = game.get("PN") or ""
                p_val = game.get("P")
                sub_id = game.get("I")
                if not sub_id:
                    continue
                    
                if not tg:
                    if pn == "1st half" or p_val == 1:
                        fh_id = sub_id
                    elif pn == "2nd half" or p_val == 2:
                        sh_id = sub_id
                elif tg == "Corners":
                    if not pn and p_val is None:
                        corners_id = sub_id
                elif tg in ("Yellow Cards", "Cards"):
                    if not pn and p_val is None:
                        if tg == "Yellow Cards" or not bookings_id:
                            bookings_id = sub_id
                            
            subgame_tasks = []
            subgame_keys = []
            if fh_id:
                subgame_tasks.append(fetch_game_zip_async(session, site, fh_id, referer))
                subgame_keys.append("fh")
            if sh_id:
                subgame_tasks.append(fetch_game_zip_async(session, site, sh_id, referer))
                subgame_keys.append("sh")
            if corners_id:
                subgame_tasks.append(fetch_game_zip_async(session, site, corners_id, referer))
                subgame_keys.append("corners")
            if bookings_id:
                subgame_tasks.append(fetch_game_zip_async(session, site, bookings_id, referer))
                subgame_keys.append("bookings")
                
            subgame_results = []
            if subgame_tasks:
                async with sem_detail:
                    subgame_results = await asyncio.gather(*subgame_tasks)
                    
            subgames_data = dict(zip(subgame_keys, subgame_results))
            
            odds_fh_1x2 = {}
            odds_fh_dc = {}
            odds_fh_ou = {}
            fh_detail = subgames_data.get("fh")
            if fh_detail:
                fh_ents = iter_linefeed_outcomes(fh_detail)
                odds_fh_1x2 = extract_1x2(fh_ents, 1)
                odds_fh_dc = extract_dc(fh_ents, 8)
                odds_fh_ou = extract_ou(fh_ents, 17)
                
            odds_sh_1x2 = {}
            odds_sh_dc = {}
            odds_sh_ou = {}
            sh_detail = subgames_data.get("sh")
            if sh_detail:
                sh_ents = iter_linefeed_outcomes(sh_detail)
                odds_sh_1x2 = extract_1x2(sh_ents, 1)
                odds_sh_dc = extract_dc(sh_ents, 8)
                odds_sh_ou = extract_ou(sh_ents, 17)
                
            odds_corners_1x2 = {}
            corners_detail = subgames_data.get("corners")
            if corners_detail:
                c_ents = iter_linefeed_outcomes(corners_detail)
                odds_corners_1x2 = extract_1x2(c_ents, 1)
                
            odds_bookings_1x2 = {}
            odds_bookings_ou = {}
            bookings_detail = subgames_data.get("bookings")
            if bookings_detail:
                b_ents = iter_linefeed_outcomes(bookings_detail)
                odds_bookings_1x2 = extract_1x2(b_ents, 1)
                odds_bookings_ou = extract_ou(b_ents, 17)

            kick = kickoff_utc_from_game(detail)
            if not kick:
                kick = kickoff_utc_from_game(stub)
            kick_str = kick.isoformat().replace("+00:00", "Z") if kick else ""
            
            return {
                "event_id": gid,
                "kickoff_utc": kick_str,
                "league": league,
                "home_team": home_team,
                "away_team": away_team,
                "odds": {
                    "match_result": odds_1x2,
                    "odds_1x2_two_up": odds_1x2_two_up,
                    "odds_fh_1x2": odds_fh_1x2,
                    "odds_sh_1x2": odds_sh_1x2,
                    "odds_corners_1x2": odds_corners_1x2,
                    "odds_bookings_1x2": odds_bookings_1x2,
                    "odds_ou": odds_ou,
                    "odds_fh_ou": odds_fh_ou,
                    "odds_sh_ou": odds_sh_ou,
                    "odds_bookings_ou": odds_bookings_ou,
                    "gg_ng": odds_gg,
                    "odds_gg_2plus": {},
                    "odds_dc": odds_dc,
                    "odds_fh_dc": odds_fh_dc,
                    "odds_sh_dc": odds_sh_dc,
                }
            }

        print(f"  ⚡ Fetching details & subgames for {len(stubs)} matches...")
        tasks = [process_stub(stub) for stub in stubs]
        results = await asyncio.gather(*tasks)
        matches = [r for r in results if r is not None]
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
            
        # Skip "fantasy" matches (e.g. Aston Villa/Crystal Palace)
        if '/' in home_team or '/' in away_team:
            continue
            
        # Skip Alternative Matches, Shots, Corners, etc.
        league = (m.get("league") or "").strip()
        lower_league = league.lower()
        if any(x in lower_league for x in [
            'alternative', 'matches of the day', 'player props', 
            'special bets', 'shots', 'corners', 'cards', 'stats'
        ]):
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
                pass

        # --- 1X2 Two Up ---
        two_up_raw = odds_raw.get("odds_1x2_two_up") or {}
        odds_1x2_two_up = {}
        if two_up_raw.get("home") and two_up_raw.get("draw") and two_up_raw.get("away"):
            try:
                odds_1x2_two_up = {
                    "home": float(two_up_raw["home"]),
                    "draw": float(two_up_raw["draw"]),
                    "away": float(two_up_raw["away"]),
                }
            except (TypeError, ValueError):
                pass

        # --- 1st Half 1X2 ---
        fh_1x2_raw = odds_raw.get("odds_fh_1x2") or {}
        odds_fh_1x2 = {}
        if fh_1x2_raw.get("home") and fh_1x2_raw.get("draw") and fh_1x2_raw.get("away"):
            try:
                odds_fh_1x2 = {
                    "home": float(fh_1x2_raw["home"]),
                    "draw": float(fh_1x2_raw["draw"]),
                    "away": float(fh_1x2_raw["away"]),
                }
            except (TypeError, ValueError):
                pass

        # --- 2nd Half 1X2 ---
        sh_1x2_raw = odds_raw.get("odds_sh_1x2") or {}
        odds_sh_1x2 = {}
        if sh_1x2_raw.get("home") and sh_1x2_raw.get("draw") and sh_1x2_raw.get("away"):
            try:
                odds_sh_1x2 = {
                    "home": float(sh_1x2_raw["home"]),
                    "draw": float(sh_1x2_raw["draw"]),
                    "away": float(sh_1x2_raw["away"]),
                }
            except (TypeError, ValueError):
                pass

        # --- Corners 1X2 ---
        corners_1x2_raw = odds_raw.get("odds_corners_1x2") or {}
        odds_corners_1x2 = {}
        if corners_1x2_raw.get("home") and corners_1x2_raw.get("draw") and corners_1x2_raw.get("away"):
            try:
                odds_corners_1x2 = {
                    "home": float(corners_1x2_raw["home"]),
                    "draw": float(corners_1x2_raw["draw"]),
                    "away": float(corners_1x2_raw["away"]),
                }
            except (TypeError, ValueError):
                pass

        # --- Bookings 1X2 ---
        bookings_1x2_raw = odds_raw.get("odds_bookings_1x2") or {}
        odds_bookings_1x2 = {}
        if bookings_1x2_raw.get("home") and bookings_1x2_raw.get("draw") and bookings_1x2_raw.get("away"):
            try:
                odds_bookings_1x2 = {
                    "home": float(bookings_1x2_raw["home"]),
                    "draw": float(bookings_1x2_raw["draw"]),
                    "away": float(bookings_1x2_raw["away"]),
                }
            except (TypeError, ValueError):
                pass

        # --- Double Chance ---
        dc_raw = odds_raw.get("odds_dc") or {}
        odds_dc = {}
        if dc_raw.get("1x") and dc_raw.get("12") and dc_raw.get("x2"):
            try:
                odds_dc = {
                    "1x": float(dc_raw["1x"]),
                    "12": float(dc_raw["12"]),
                    "x2": float(dc_raw["x2"]),
                }
            except (TypeError, ValueError):
                pass

        # --- 1st Half Double Chance ---
        fh_dc_raw = odds_raw.get("odds_fh_dc") or {}
        odds_fh_dc = {}
        if fh_dc_raw.get("1x") and fh_dc_raw.get("12") and fh_dc_raw.get("x2"):
            try:
                odds_fh_dc = {
                    "1x": float(fh_dc_raw["1x"]),
                    "12": float(fh_dc_raw["12"]),
                    "x2": float(fh_dc_raw["x2"]),
                }
            except (TypeError, ValueError):
                pass

        # --- 2nd Half Double Chance ---
        sh_dc_raw = odds_raw.get("odds_sh_dc") or {}
        odds_sh_dc = {}
        if sh_dc_raw.get("1x") and sh_dc_raw.get("12") and sh_dc_raw.get("x2"):
            try:
                odds_sh_dc = {
                    "1x": float(sh_dc_raw["1x"]),
                    "12": float(sh_dc_raw["12"]),
                    "x2": float(sh_dc_raw["x2"]),
                }
            except (TypeError, ValueError):
                pass

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
                pass

        # Helper to convert nested over/under lines and partition standard vs asian
        def convert_nested_ou(raw_ou_dict, partition_asian=False):
            out_ou = {}
            out_asian = {}
            for line_str, val in (raw_ou_dict or {}).items():
                if isinstance(val, dict) and "over" in val and "under" in val:
                    try:
                        line_val = float(line_str)
                        std_line_str = str(line_val)
                        record = {
                            "over": float(val["over"]),
                            "under": float(val["under"])
                        }
                        if partition_asian:
                            if line_val % 1.0 == 0.5:
                                out_ou[std_line_str] = record
                            else:
                                out_asian[std_line_str] = record
                        else:
                            out_ou[std_line_str] = record
                    except (TypeError, ValueError):
                        pass
            if partition_asian:
                return out_ou, out_asian
            return out_ou

        odds_ou, odds_asian_ou = convert_nested_ou(odds_raw.get("odds_ou"), partition_asian=True)
        odds_fh_ou = convert_nested_ou(odds_raw.get("odds_fh_ou"))
        odds_sh_ou = convert_nested_ou(odds_raw.get("odds_sh_ou"))
        odds_bookings_ou = convert_nested_ou(odds_raw.get("odds_bookings_ou"))

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
            "odds_asian_ou": odds_asian_ou,
            "odds_1x2_one_up": {},
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
            "odds_gg":    odds_gg,
            "odds_gg_2plus": {},
            "odds_dc":    odds_dc,
        })

    return standard


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

def fmt_box_subheading(sub_title):
    content = f"[{sub_title}]"
    return f"│ {content:<76} │"

def fmt_box_divider():
    line = "─" * 76
    return f"│ {line} │"

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

def fmt_nested_ou_inline(ou_dict):
    if not ou_dict:
        return "N/A"
    try:
        sorted_keys = sorted(ou_dict.keys(), key=lambda x: float(x))
    except Exception:
        return "N/A"
    parts = []
    for line in sorted_keys:
        ou = ou_dict[line]
        over = ou.get("over")
        under = ou.get("under")
        if over is not None and under is not None:
            parts.append(f"[{line}: O {over}/U {under}]")
    return "  ".join(parts)

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
            line_label = f"Line {line}"
            line_val = f"Over: {over:<8} │ Under: {under:<8}"
            rows.append(fmt_row(line_label, line_val))
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
            line_label = f"Line {line}"
            line_val = f"Over: {over:<8} │ Under: {under:<8}"
            rows.append(fmt_row(line_label, line_val))
    return "\n".join(rows)

def fmt_ou_section_all(ou_dict, empty_msg="(No Over/Under lines available)"):
    """Like fmt_ou_section but shows ALL lines (no .5 filter). Used for half-time markets."""
    if not ou_dict:
        return fmt_row("", empty_msg)
    try:
        sorted_keys = sorted(ou_dict.keys(), key=lambda x: float(x))
    except Exception:
        return fmt_row("", empty_msg)
    rows = []
    for line in sorted_keys:
        ou = ou_dict[line]
        over = ou.get("over")
        under = ou.get("under")
        if over is not None and under is not None:
            line_label = f"Line {line}"
            line_val = f"Over: {over:<8} │ Under: {under:<8}"
            rows.append(fmt_row(line_label, line_val))
    if not rows:
        return fmt_row("", empty_msg)
    return "\n".join(rows)

def format_match_text_block(m):
    # Header
    title = f"⚽ {m['home_team']} vs {m['away_team']}"
    if m.get("is_live"):
        title += " (🔴 LIVE)"
    meta = f"🏆 {m['tournament']} │ 🕐 {m['kickoff']}"
    
    # Border width
    w = 80
    
    # Formatting markets
    m_1x2 = fmt_3way(m.get("odds_1x2"))
    m_dc = fmt_dc(m.get("odds_dc"))
    m_gg = fmt_gg(m.get("odds_gg"))
    m_2up = fmt_3way(m.get("odds_1x2_two_up"))
    m_1up = fmt_3way(m.get("odds_1x2_one_up"))
    
    # 1st Half / 2nd Half
    fh_1x2 = fmt_3way(m.get("odds_fh_1x2"))
    fh_dc = fmt_dc(m.get("odds_fh_dc"))
    
    sh_1x2 = fmt_3way(m.get("odds_sh_1x2"))
    sh_dc = fmt_dc(m.get("odds_sh_dc"))
    
    # Specials
    c_1x2 = fmt_3way(m.get("odds_corners_1x2"))
    b_1x2 = fmt_3way(m.get("odds_bookings_1x2"))
    gg_2plus = fmt_gg(m.get("odds_gg_2plus"))

    # Construct the block
    lines = []
    lines.append("═" * w)
    lines.append(f"{title}")
    lines.append(f"{meta}")
    lines.append("═" * w)
    
    # Main Markets
    lines.append(fmt_box_top("MAIN MARKETS"))
    lines.append(fmt_row("1X2 (Result)", m_1x2))
    lines.append(fmt_row("Double Chance", m_dc))
    lines.append(fmt_row("GG/NG", m_gg))
    lines.append(fmt_row("1X2 Two Up", m_2up))
    lines.append(fmt_row("1X2 One Up", m_1up))
    lines.append(fmt_box_bottom())
    
    # Over/Under Lines
    lines.append(fmt_box_top("OVER/UNDER LINES"))
    lines.append(fmt_ou_section(m.get("odds_ou")))
    lines.append(fmt_box_bottom())
    
    # Asian Over/Under Lines
    lines.append(fmt_box_top("ASIAN OVER/UNDER LINES"))
    lines.append(fmt_asian_ou_section(m.get("odds_asian_ou")))
    lines.append(fmt_box_bottom())
    
    # Half Time Markets
    lines.append(fmt_box_top("HALF TIME MARKETS"))
    lines.append(fmt_box_subheading("1ST HALF"))
    lines.append(fmt_row("1X2 (Result)", fh_1x2))
    lines.append(fmt_row("Double Chance", fh_dc))
    lines.append(fmt_box_subheading("1ST HALF OVER/UNDER"))
    lines.append(fmt_ou_section_all(m.get("odds_fh_ou")))
    lines.append(fmt_box_divider())
    lines.append(fmt_box_subheading("2ND HALF"))
    lines.append(fmt_row("1X2 (Result)", sh_1x2))
    lines.append(fmt_row("Double Chance", sh_dc))
    lines.append(fmt_box_subheading("2ND HALF OVER/UNDER"))
    lines.append(fmt_ou_section_all(m.get("odds_sh_ou")))
    lines.append(fmt_box_bottom())
    
    # Specials & Stats
    lines.append(fmt_box_top("CORNERS, BOOKINGS & SPECIALS"))
    lines.append(fmt_row("Corners 1X2", c_1x2))
    lines.append(fmt_row("Bookings 1X2", b_1x2))
    lines.append(fmt_box_subheading("BOOKINGS OVER/UNDER"))
    lines.append(fmt_ou_section_all(m.get("odds_bookings_ou"), empty_msg="(No Bookings O/U lines available)"))
    lines.append(fmt_row("GG/NG 2+", gg_2plus))
    lines.append(fmt_box_bottom())
    lines.append("") # Blank line after match block
    
    return "\n".join(lines)


def run() -> List[dict]:
    output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
    os.makedirs(output_dir, exist_ok=True)
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
    output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
    os.makedirs(output_dir, exist_ok=True)
    json_path = os.path.join(output_dir, "onexbet_odds.json")
    txt_path  = os.path.join(output_dir, "onexbet_matches.txt")

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
                tf.write(format_match_text_block(m))

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
