import asyncio
import aiohttp
import json
import os
import sys
import time
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

DEFAULT_SITE = "https://1xbet.com.gh"
REFERRER = "https://1xbet.com.gh/en/line/basketball"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
TIMEZONE = "Africa/Accra"
DEFAULT_TF_MS = 172800000
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10, connect=4, sock_read=8)
CHAMP_CONCURRENCY = 40
DETAIL_CONCURRENCY = 24
FAST_BULK_LIMIT = 50
FAST_BULK_PARAM_SETS = [
    {
        "sports": 3,
        "count": FAST_BULK_LIMIT,
        "lng": "en",
        "mode": 4,
        "getEmpty": "true",
    },
    {
        "sports": 3,
        "count": FAST_BULK_LIMIT,
        "lng": "en",
        "mode": 4,
        "country": 87,
        "partner": 159,
        "getEmpty": "true",
        "noFilterBlockEvent": "true",
    },
    {
        "sports": 3,
        "count": FAST_BULK_LIMIT,
        "lng": "en",
        "mode": 4,
        "country": 87,
        "partner": 159,
    },
    {
        "sports": 3,
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
                        return {}
                    return data
        except Exception:
            if attempt < max_attempts - 1:
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


def build_odds_block(entries) -> Dict[str, Any]:
    block: Dict[str, Any] = {}
    x2 = {}
    ot = {}
    for e in entries:
        gid = e.get("G")
        tid = e.get("T")
        if gid == 101 and tid in (401, 402):
            price = _outcome_price(e)
            if price is not None:
                x2[int(tid)] = price
        elif gid == 90 and tid in (759, 761):
            price = _outcome_price(e)
            if price is not None:
                ot[int(tid)] = price

    if len(x2) == 2:
        try:
            block["odds_2way"] = {
                "home": float(x2[401]),
                "away": float(x2[402])
            }
        except (TypeError, ValueError):
            pass
            
    if len(ot) == 2:
        try:
            block["odds_overtime"] = {
                "yes": float(ot[759]),
                "no": float(ot[761])
            }
        except (TypeError, ValueError):
            pass
            
    return block


async def fetch_champs_async(session: aiohttp.ClientSession, site: str, tf_ms: int, referer: str) -> List[dict]:
    data = await async_linefeed_get(
        session, site, "GetChampsZip",
        {"sport": 3, "lng": "en", "tf": tf_ms, "tz": 0},
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
            "sport": 3,
            "GroupEvents": "true",
            "countevents": 250,
        },
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
        'special bets', 'points', 'sets', 'virtual', 'cyber'
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

        odds = build_odds_block(iter_linefeed_outcomes(game))
        if not odds.get("odds_2way"):
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
        fast_games = await fetch_fast_bulk_async(session, site, referer)
        fast_matches = collect_from_fast_bulk(fast_games, target, tz, now_utc)
        if fast_matches:
            print(f"  ⚡ Fast bulk endpoint returned {len(fast_games)} events; using {len(fast_matches)} matches.")
            return fast_matches

        print("  ⚠️  Fast bulk endpoint returned no matches; falling back to league scan...")

        # 1. Fetch champs
        champs = await fetch_champs_async(session, site, tf_ms, referer)
        if not champs:
            return []

        # 2. Fetch games within champs
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

        # 3. Fetch detailed odds
        sem_game = asyncio.Semaphore(DETAIL_CONCURRENCY)
        
        async def load_odds(item: Tuple[int, dict, str]) -> dict:
            gid, stub, league_fallback = item
            league = (stub.get("LE") or stub.get("L") or league_fallback or "").strip()
            kick = kickoff_utc_from_game(stub)

            odds = build_odds_block(iter_linefeed_outcomes(stub))

            # GetChampZip can include grouped markets already. Full game calls are
            # only needed for games where the cheap league payload lacks 2-way odds.
            if not odds.get("odds_2way"):
                async with sem_game:
                    detail = await fetch_game_zip_async(session, site, gid, referer)
                if detail:
                    odds = build_odds_block(iter_linefeed_outcomes(detail))

            return {
                "event_id": gid,
                "kickoff_utc": kick.isoformat().replace("+00:00", "Z") if kick else "",
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
        
        if not home_team or not away_team:
            continue
            
        league = (m.get("league") or "").strip()
        lower_league = league.lower()
        
        if any(x in lower_league for x in [
            'alternative', 'matches of the day', 'player props', 
            'special bets', 'points', 'sets', 'virtual', 'cyber'
        ]):
            continue

        odds_raw = m.get("odds") or {}
        odds_2way = odds_raw.get("odds_2way") or {}

        if not odds_2way:
            continue

        ku = m.get("kickoff_utc") or ""
        kickoff_str = ""
        if ku:
            try:
                dt = datetime.fromisoformat(ku.replace("Z", "+00:00"))
                kickoff_str = dt.astimezone(tz).strftime("%Y-%m-%d %H:%M")
            except ValueError:
                kickoff_str = ku[:16].replace("T", " ")

        standard.append({
            "home_team": home_team,
            "away_team": away_team,
            "kickoff": kickoff_str,
            "tournament": league,
            "is_live": False,
            "source": "1xbet_gh",
            "odds_2way": odds_2way,
            "odds_overtime": odds_raw.get("odds_overtime") or {}
        })

    return standard


def run() -> List[dict]:
    started = time.perf_counter()

    tz = ZoneInfo(TIMEZONE)
    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(tz)
    today = now_local.date()

    print("\n" + "🔵 " * 20)
    print("   1XBET GHANA BASKETBALL SCRAPER")
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

    matches = _convert_to_standard_format(raw_matches, tz)
    n = len(matches)

    if n == 0:
        print(f"⚠️  No prematch matches found for today.")
    else:
        print(f"\n📋 1XBET GHANA BASKETBALL")
        print(f"🏀 Total matches: {n}")
        print("=" * 50)
        head = min(10, n)
        print(f"\n📝 Sample (first {head}):")
        for m in matches[:head]:
            o = m['odds_2way']
            ot = m.get('odds_overtime')
            ot_str = f" | Overtime: Yes {ot['yes']} - No {ot['no']}" if ot else " | Overtime: N/A"
            print(f"   {m['home_team']} vs {m['away_team']} | {m['kickoff']} | H {o['home']} - A {o['away']}{ot_str}")
        if n > head:
            print(f"  ... and {n - head} more")
        print("=" * 50)

    output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
    os.makedirs(output_dir, exist_ok=True)
    json_path = os.path.join(output_dir, "onexbet_basketball_odds.json")
    txt_path  = os.path.join(output_dir, "onexbet_basketball_matches.txt")

    with open(json_path, "w", encoding="utf-8") as jf:
        json.dump(matches, jf, ensure_ascii=False, indent=2)

    with open(txt_path, "w", encoding="utf-8") as tf:
        tf.write("1XBET GHANA BASKETBALL - ALL MATCHES\n")
        tf.write(f"Generated: {now_local.strftime('%A, %d %B %Y %H:%M:%S')}\n")
        tf.write(f"Total: {n} matches\n")
        tf.write("=" * 60 + "\n\n")
        
        if not matches:
            tf.write(f"No prematch basketball for today ({TIMEZONE}).\n")
        else:
            for m in matches:
                tf.write(f"{m['home_team']} vs {m['away_team']}\n")
                tf.write(f"{m['tournament']}\n")
                tf.write(f"{m['kickoff']}\n")
                o = m["odds_2way"]
                ot = m.get('odds_overtime')
                ot_str = f" | Overtime: Yes {ot['yes']} | No {ot['no']}" if ot else " | Overtime: N/A"
                tf.write(f"Winner (2-way): Home {o['home']} | Away {o['away']}{ot_str}\n\n")

    elapsed = time.perf_counter() - started
    print(f"Saved to {json_path}")
    print(f"Full list saved to {txt_path}")
    print(f"⏱️  Scraping completed in {elapsed:.1f}s")

    return matches


if __name__ == "__main__":
    run()
