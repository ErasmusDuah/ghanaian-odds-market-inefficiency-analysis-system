"""
Betwinner football prematch odds (ASYNC curl_cffi VERSION).

Fetches all leagues globally and processes odds for all matches occurring today.
Uses curl_cffi with Chrome TLS impersonation to bypass Cloudflare Bot Management.
Falls back through multiple Betwinner domains automatically.
Outputs to the standard format required by the arbitrage engine.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import os
import re
import sys
import time
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

from curl_cffi.requests import AsyncSession

# Domain fallback list — tried in order until one succeeds
# Betwinner uses the same LineFeed family as 1xBet, with its own public domain.
DOMAIN_FALLBACKS = [
    "https://betwinner.com.gh",
    "https://betwinner.com",
]
DEFAULT_SITE = DOMAIN_FALLBACKS[0]
TIMEZONE = "Africa/Accra"
IMPERSONATE = "chrome120"   # curl_cffi TLS fingerprint to impersonate
FETCH_HALVES = os.getenv("BETWINNER_FETCH_HALVES", "1").strip().lower() not in {"0", "false", "no", "off"}
FETCH_SUBGAMES = os.getenv("BETWINNER_FETCH_SUBGAMES", "0").strip().lower() in {"1", "true", "yes", "on"}
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
EVENT_CACHE_PATH = os.path.join(_DATA_DIR, "betwinner_event_cache.json")

OU_TOTALS: Tuple[float, ...] = (1.5, 2.5, 3.5, 4.5, 5.5)
DEFAULT_TF_MS = 172800000
REQUEST_TIMEOUT = 12        # seconds (curl_cffi scalar timeout)
CHAMP_CONCURRENCY = int(os.getenv("LINEFEED_CHAMP_CONCURRENCY", "40"))
DETAIL_CONCURRENCY = int(os.getenv("BETWINNER_DETAIL_CONCURRENCY", os.getenv("LINEFEED_DETAIL_CONCURRENCY", "40")))
SESSION_MAX_CLIENTS = max(CHAMP_CONCURRENCY, DETAIL_CONCURRENCY)
FAST_BULK_LIMIT = 50
MIN_CACHE_STUBS = int(os.getenv("BETWINNER_MIN_CACHE_STUBS", "100"))
# Public Ghana frontend params observed from betwinner.com.gh. These must match
# the visible website feed; generic LineFeed params can return stale/different odds.
FRONTEND_COUNTRY_ID = 48
FRONTEND_PARTNER_ID = 152
FRONTEND_GROUP_ID = 541
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

_REQUEST_NONCE = itertools.count()
_REQUEST_FAILURE_COUNTS: Dict[str, int] = {}


async def async_linefeed_get(
    session: AsyncSession,
    site: str,
    method: str,
    params: Dict[str, Any],
    referer: str,
    max_attempts: int = 3,
) -> dict:
    origin = site.rstrip("/")
    request_nonce = next(_REQUEST_NONCE)
    cache_buster = f"{int(time.time() * 1000)}{request_nonce:04d}"
    # Format query string correctly including array/list parameters
    q_parts = []
    for k, v in params.items():
        if isinstance(v, list):
            for item in v:
                q_parts.append(f"{k}={item}")
        else:
            q_parts.append(f"{k}={v}")
    if method != "GetChampsZip":
        q_parts.append(f"_={cache_buster}")
    q = "&".join(q_parts)

    url = f"{origin}/service-api/LineFeed/{method}?{q}"
    headers = {
        "Referer": referer,
        "Origin": origin,
        "Accept": "application/json",
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
        "If-Modified-Since": "Sat, 01 Jan 2000 00:00:00 GMT",
    }

    for attempt in range(max_attempts):
        try:
            resp = await session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("Success") is False:
                    error_msg = str(data.get("Error") or "")
                    if not (method == "GetGameZip" and "Game is not found in Sports" in error_msg):
                        print(f"  \u26a0\ufe0f LineFeed error for {method}: {data.get('Error')}")
                    return {}
                return data
        except Exception as e:
            if attempt == max_attempts - 1:
                _REQUEST_FAILURE_COUNTS[method] = _REQUEST_FAILURE_COUNTS.get(method, 0) + 1
                if method != "GetGameZip":
                    print(f"  \u26a0\ufe0f Request failed for {method} after {max_attempts} attempts: {e}")
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
    if e.get("C") is not None and e.get("C") != "":
        return e.get("C")
    return e.get("CV")


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


async def fetch_champs_async(session: AsyncSession, site: str, tf_ms: int, referer: str) -> List[dict]:
    data = await async_linefeed_get(
        session, site, "GetChampsZip",
        {
            "sport": 1,
            "lng": "en",
            "tf": tf_ms,
            "tz": 0,
            "country": FRONTEND_COUNTRY_ID,
            "partner": FRONTEND_PARTNER_ID,
            "gr": FRONTEND_GROUP_ID,
        },
        referer,
    )
    return list(data.get("Value") or [])

async def fetch_champ_games_async(session: AsyncSession, site: str, li: int, tf_ms: int, referer: str) -> Optional[dict]:
    return await async_linefeed_get(
        session, site, "GetChampZip",
        {
            "lng": "en",
            "champ": li,
            "tf": tf_ms,
            "afterDays": 0,
            "tz": 0,
            "sport": 1,
            "country": FRONTEND_COUNTRY_ID,
            "partner": FRONTEND_PARTNER_ID,
            "gr": FRONTEND_GROUP_ID,
        },
        referer,
    )


async def fetch_game_zip_async(session: AsyncSession, site: str, game_id: int, referer: str) -> Optional[dict]:
    data = await async_linefeed_get(
        session, site, "GetGameZip",
        {
            "id": game_id,
            "lng": "en",
            "isSubGames": "true",
            "GroupEvents": "true",
            "countevents": 250,
            "grMode": 4,
            "partner": FRONTEND_PARTNER_ID,
            "topGroups": "",
            "country": FRONTEND_COUNTRY_ID,
            "marketType": 1,
            "isNewBuilder": "true",
        },
        referer,
    )
    return data.get("Value") if isinstance(data.get("Value"), dict) else None


async def fetch_fast_bulk_async(session: AsyncSession, site: str, referer: str) -> List[dict]:
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
    noise_fragments = (
        'alternative', 'matches of the day', 'player props',
        'team vs player', 'player vs team', 'team v player', 'player v team',
        'special bets', 'shots', 'corners', 'cards', 'stats', 'statistics',
        'goalscorer', 'player specials', 'to score', 'virtual', 'cyber',
        'duel of the players', 'player duel', 'duel.', 'goals. statistics',
    )
    return any(fragment in lower_league for fragment in noise_fragments)


def _looks_like_person_vs_team_or_person(home: str, away: str, league: str) -> bool:
    text = f"{home} {away} {league}".lower()
    if any(marker in text for marker in (
        'duel of the players', 'goals. statistics', 'player statistics',
        'goalscorer', 'player specials', 'shots on target', 'to score',
    )):
        return True

    team_words = (
        'fc', 'fk', 'sc', 'cf', 'afc', 'bk', 'if', 'sk', 'club', 'united', 'city',
        'town', 'women', 'u19', 'u20', 'u21', 'u23', 'ii', 'reserve', 'reserves',
        'national', 'sporting', 'athletic', 'academy', 'calcio', 'deportivo',
    )

    def personish(name: str) -> bool:
        clean = re.sub(r'[^a-z\s-]', ' ', name.lower()).strip()
        parts = [p for p in clean.replace('-', ' ').split() if p]
        if len(parts) < 2 or len(parts) > 4:
            return False
        if any(part in team_words for part in parts):
            return False
        return all(len(part) > 1 for part in parts)

    countries = {
        'argentina', 'australia', 'austria', 'belgium', 'brazil', 'canada', 'chile',
        'china', 'colombia', 'croatia', 'denmark', 'egypt', 'england', 'finland',
        'france', 'germany', 'ghana', 'greece', 'iran', 'ireland', 'italy', 'japan',
        'mexico', 'morocco', 'netherlands', 'nigeria', 'norway', 'poland',
        'portugal', 'saudi arabia', 'scotland', 'senegal', 'serbia', 'spain',
        'sweden', 'switzerland', 'turkey', 'ukraine', 'uruguay', 'usa', 'wales',
    }
    home_person = personish(home)
    away_person = personish(away)
    if home_person and away in countries:
        return True
    if away_person and home in countries:
        return True
    return False


def _is_noise_match(home_team: str, away_team: str, league: str = "") -> bool:
    lower_home = home_team.strip().lower()
    lower_away = away_team.strip().lower()
    lower_league = league.strip().lower()

    if _is_noise_league(lower_league):
        return True

    generic_sides = {
        '1st team', '1st teams', 'first team', 'first teams',
        '2nd team', '2nd teams', 'second team', 'second teams',
        'home team', 'away team', 'team 1', 'team 2',
        'home', 'away', 'draw', 'yes', 'no',
    }
    if lower_home in generic_sides or lower_away in generic_sides:
        return True

    ordinal_team_pattern = re.compile(r'^\d+(st|nd|rd|th)?\s+teams?$')
    if ordinal_team_pattern.match(lower_home) or ordinal_team_pattern.match(lower_away):
        return True

    return _looks_like_person_vs_team_or_person(lower_home, lower_away, lower_league)


def _load_event_cache(target: date) -> List[Tuple[int, dict, str]]:
    # Event-list caches can silently cap match counts when saved from a partial feed.
    # Keep fresh discovery as the default; enable only for emergency speed.
    use_cache = os.getenv("BETWINNER_USE_EVENT_CACHE", "0").strip().lower() in {"1", "true", "yes", "on"}
    if not use_cache:
        return []
    try:
        with open(EVENT_CACHE_PATH, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []

    if payload.get("date") != target.isoformat():
        return []

    stubs: List[Tuple[int, dict, str]] = []
    for item in payload.get("events") or []:
        try:
            gid = int(item["event_id"])
        except (KeyError, TypeError, ValueError):
            continue
        league = item.get("league", "")
        if _is_noise_league(str(league)):
            continue
        stub = {"I": gid, "S": item.get("kickoff_ts")}
        stubs.append((gid, stub, league))
    if 0 < len(stubs) < MIN_CACHE_STUBS:
        print(f"  WARNING: Betwinner cache has only {len(stubs)} events; refreshing full event list...")
        return []
    return stubs


def _save_event_cache(target: date, stubs: List[Tuple[int, dict, str]]) -> None:
    events = []
    for gid, stub, league in stubs:
        if _is_noise_league(str(league)):
            continue
        events.append({
            "event_id": gid,
            "kickoff_ts": stub.get("S"),
            "league": league,
        })

    if 0 < len(events) < MIN_CACHE_STUBS:
        print(f"  WARNING: Not saving suspiciously small Betwinner cache ({len(events)} events)")
        return

    try:
        os.makedirs(os.path.dirname(EVENT_CACHE_PATH), exist_ok=True)
        with open(EVENT_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump({"date": target.isoformat(), "events": events}, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print(f"  ⚠️ Could not save Betwinner event cache: {e}")


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
        home_team = (game.get("O1") or "").strip()
        away_team = (game.get("O2") or "").strip()
        if not home_team or not away_team:
            continue
        if '/' in home_team or '/' in away_team:
            continue
        if _is_noise_match(home_team, away_team, league):
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


async def _probe_working_domain(tf_ms: int) -> Tuple[str, str, List[dict]]:
    """Try each domain in DOMAIN_FALLBACKS and return the first that works.
    Returns (domain, referer, champs) so the caller can reuse the already-fetched
    league list without making a second GetChampsZip request.
    """
    async with AsyncSession(impersonate=IMPERSONATE, max_clients=SESSION_MAX_CLIENTS) as probe_session:
        for domain in DOMAIN_FALLBACKS:
            referer = f"{domain}/en/line/football"
            try:
                # Pre-fetch landing page to initialize session cookies
                try:
                    await probe_session.get(referer, headers={"Referer": domain}, timeout=REQUEST_TIMEOUT)
                except Exception as ce:
                    print(f"  ⚠️ Pre-fetch failed for {domain}: {ce}")
                champs = await fetch_champs_async(probe_session, domain, tf_ms, referer)
                if champs:
                    print(f"  \u2705 Using domain: {domain} ({len(champs)} leagues)")
                    return domain, referer, champs
                else:
                    print(f"  \u26a0\ufe0f {domain}: connected but no leagues returned")
            except Exception as e:
                print(f"  \u274c {domain}: {type(e).__name__}")
    # Return the first as last-resort fallback
    print(f"  \u26a0\ufe0f All domains failed, defaulting to {DOMAIN_FALLBACKS[0]}")
    return DOMAIN_FALLBACKS[0], f"{DOMAIN_FALLBACKS[0]}/en/line/football", []


async def collect_today_games_async(
    site: str,
    tf_ms: int,
    referer: str,
    target: date,
    tz: ZoneInfo,
    now_utc: datetime,
) -> List[dict]:
    cached_stubs = _load_event_cache(target)
    if cached_stubs:
        print(f"  ⚡ Using cached Betwinner event list ({len(cached_stubs)} matches) - refreshing odds directly...")
        site = site or DEFAULT_SITE
        referer = referer or f"{site}/en/line/football"
        champs = []
    else:
        # Auto-detect working domain and league list.
        print("  \U0001f50d Auto-detecting working Betwinner domain...")
        site, referer, champs = await _probe_working_domain(tf_ms)

    async with AsyncSession(impersonate=IMPERSONATE, max_clients=SESSION_MAX_CLIENTS) as session:
        # 1. Champs already fetched during probe — skip redundant GetChampsZip
        if not cached_stubs and not champs:
            return []

        # Pre-fetch the landing page to initialize session cookies to bypass CDN cache
        try:
            landing_url = f"{site}/en/line/football"
            await session.get(landing_url, headers={"Referer": site}, timeout=REQUEST_TIMEOUT)
            print("  🍪 Session cookies initialized successfully.")
        except Exception as e:
            print(f"  ⚠️ Failed to initialize session cookies: {e}")

        if cached_stubs:
            stubs = cached_stubs
        else:
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
            _save_event_cache(target, stubs)

        if not stubs:
            return []

        # 3. Concurrently fetch main game details and optional subgames
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
                    t = e.get("T")
                    if t in (180, 182):
                        x["gg"] = _outcome_price(e)
                    elif t in (181, 183):
                        x["ng"] = _outcome_price(e)
            if "gg" in x and "ng" in x:
                return {"gg": float(x["gg"]), "ng": float(x["ng"])}
            return {}

        def extract_gg_2plus(ents):
            x = {}
            for e in ents:
                if e.get("G") != 19:
                    continue
                try:
                    p_val = float(e.get("P"))
                except (TypeError, ValueError):
                    continue
                if p_val != 2.0:
                    continue
                t = e.get("T")
                if t == 11273:
                    x["gg"] = _outcome_price(e)
                elif t == 11274:
                    x["ng"] = _outcome_price(e)
            if "gg" in x and "ng" in x:
                return {"gg": float(x["gg"]), "ng": float(x["ng"])}
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
            if _is_noise_match(home_team, away_team, league):
                return None
            
            # Parse main markets
            entries = iter_linefeed_outcomes(detail)
            
            odds_1x2 = extract_1x2(entries, 1)
            if not odds_1x2:
                return None
                
            odds_1x2_two_up = extract_1x2(entries, 11581)
            odds_dc = extract_dc(entries, 8)
            odds_ou = extract_ou(entries, 17)
            odds_gg = extract_gg(entries, 19)
            odds_gg_2plus = extract_gg_2plus(entries)
            
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
            if FETCH_HALVES and fh_id:
                subgame_tasks.append(fetch_game_zip_async(session, site, fh_id, referer))
                subgame_keys.append("fh")
            if FETCH_HALVES and sh_id:
                subgame_tasks.append(fetch_game_zip_async(session, site, sh_id, referer))
                subgame_keys.append("sh")
            if FETCH_SUBGAMES and corners_id:
                subgame_tasks.append(fetch_game_zip_async(session, site, corners_id, referer))
                subgame_keys.append("corners")
            if FETCH_SUBGAMES and bookings_id:
                subgame_tasks.append(fetch_game_zip_async(session, site, bookings_id, referer))
                subgame_keys.append("bookings")
                
            subgame_results = []
            if subgame_tasks:
                async def _fetch_sub(coro):
                    async with sem_detail:
                        return await coro
                subgame_results = await asyncio.gather(*[_fetch_sub(t) for t in subgame_tasks])
                    
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
                    "odds_gg_2plus": odds_gg_2plus,
                    "odds_dc": odds_dc,
                    "odds_fh_dc": odds_fh_dc,
                    "odds_sh_dc": odds_sh_dc,
                }
            }

        if FETCH_SUBGAMES:
            detail_label = "details, halves & deep subgames"
        elif FETCH_HALVES:
            detail_label = "details & half-time markets"
        else:
            detail_label = "main details only (ultra fast)"
        print(f"  ⚡ Fetching {detail_label} for {len(stubs)} matches...")
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
        if _is_noise_match(home_team, away_team, league):
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

        def convert_yes_no_market(raw_market):
            out = {}
            if raw_market.get("gg") and raw_market.get("ng"):
                try:
                    out = {
                        "yes": float(raw_market["gg"]),
                        "no":  float(raw_market["ng"]),
                    }
                except (TypeError, ValueError):
                    pass
            return out

        # --- GG/NG ---
        gn = odds_raw.get("gg_ng") or {}
        odds_gg = convert_yes_no_market(gn)

        # --- GG/NG 2+ / Each team to score 2 or more ---
        gg2 = odds_raw.get("odds_gg_2plus") or {}
        odds_gg_2plus = convert_yes_no_market(gg2)

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
            "event_id":    m.get("event_id"),
            "home_team":  (m.get("home_team") or "").strip(),
            "away_team":  (m.get("away_team") or "").strip(),
            "kickoff":    kickoff_str,
            "tournament": (m.get("league") or "").strip(),
            "is_live":    False,
            "source":     "betwinner",
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
            "odds_gg_2plus": odds_gg_2plus,
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
    lines.append(fmt_row("GG/NG 2+", gg_2plus))
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
    lines.append(fmt_box_bottom())
    lines.append("") # Blank line after match block
    
    return "\n".join(lines)


# Use the shared formatter so every football scraper has the same text output.
try:
    from .fb_output_formatter import format_match_text_block
except ImportError:
    from fb_output_formatter import format_match_text_block

def run() -> List[dict]:
    output_dir = _DATA_DIR
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
    print("   BETWINNER SCRAPER (ASYNC LineFeed)")
    print(f"   {now_local.strftime('%A, %d %B %Y %H:%M:%S')}")
    print("🔵 " * 20 + "\n")

    raw_matches = asyncio.run(collect_today_games_async(
        site=DEFAULT_SITE,
        tf_ms=DEFAULT_TF_MS,
        referer=f"{DEFAULT_SITE}/en/line/football",
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
        print(f"\n📋 BETWINNER")
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
    output_dir = _DATA_DIR
    os.makedirs(output_dir, exist_ok=True)
    json_path = os.path.join(output_dir, "betwinner_odds.json")
    txt_path  = os.path.join(output_dir, "betwinner_matches.txt")

    with open(json_path, "w", encoding="utf-8") as jf:
        json.dump(matches, jf, ensure_ascii=False, indent=2)

    with open(txt_path, "w", encoding="utf-8") as tf:
        tf.write("BETWINNER - ALL MATCHES\n")
        tf.write(f"Generated: {now_local.strftime('%A, %d %B %Y %H:%M:%S')}\n")
        tf.write(f"Total: {n} matches\n")
        tf.write("=" * 60 + "\n\n")
        
        if not matches:
            tf.write(f"No prematch football for today ({TIMEZONE}).\n")
        else:
            for m in matches:
                tf.write(format_match_text_block(m))

    elapsed = time.perf_counter() - started
    failed_gamezip = _REQUEST_FAILURE_COUNTS.get("GetGameZip", 0)
    if failed_gamezip:
        print(f"  WARNING: {failed_gamezip} GetGameZip detail request(s) failed after retries; skipped unavailable detail-only markets.")
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

