"""
22Bet Ghana football prematch odds (ASYNC AIOHTTP VERSION)
"""
from __future__ import annotations

import asyncio
import aiohttp
import json
import os
import time as _time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

API_BASE = "https://platform.22bet.com.gh"
REFERRER = "https://22bet.com.gh/prematch/football"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

EVENT_LIST_PATH = "/api/event/list"
EVENT_LIST_QUERY = (
    "lang=en"
    "&relations=odds&relations=league&relations=competitors&relations=sportCategories"
    "&oddsExists_eq=1&main=1&period=0&sportId_eq=1&limit=50&status_in=0"
    "&oddsBooster=0&isFavorite=0&isLive=false"
)


async def fetch_event_list_page(session: aiohttp.ClientSession, page: int) -> dict:
    url = f"{API_BASE}{EVENT_LIST_PATH}?{EVENT_LIST_QUERY}&page={page}&_t={int(_time.time() * 1000)}"
    async with session.get(url, headers={
        "User-Agent": USER_AGENT,
        "Referer": REFERRER,
        "Accept": "application/json",
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
    }, timeout=aiohttp.ClientTimeout(total=20)) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status} on page {page}")
        data = await resp.json()
        if data.get("status") != "ok":
            raise RuntimeError(f"API error: {data!r}")
        return data["data"]


def parse_kickoff_utc(s: str) -> datetime:
    """Parse API datetime as UTC (naive string from API)."""
    dt = datetime.strptime(s.strip(), "%Y-%m-%d %H:%M:%S")
    return dt.replace(tzinfo=timezone.utc)

def merge_relations(acc: dict, rel: dict) -> None:
    for key in ("league", "competitors", "sportCategories"):
        val = rel.get(key)
        if not isinstance(val, list):
            continue
        acc.setdefault(key, [])
        seen = {x.get("id") for x in acc[key] if isinstance(x, dict) and "id" in x}
        for item in val:
            if not isinstance(item, dict) or "id" not in item:
                continue
            if item["id"] not in seen:
                seen.add(item["id"])
                acc[key].append(item)
    odds = rel.get("odds")
    if isinstance(odds, dict):
        acc.setdefault("odds", {}).update(odds)


async def fetch_all_prematch() -> Tuple[List[dict], dict]:
    # Use a single TCP connection pool to reuse TLS handshakes (massive speedup)
    conn = aiohttp.TCPConnector(limit=15)
    async with aiohttp.ClientSession(connector=conn) as session:
        try:
            first = await fetch_event_list_page(session, 1)
        except Exception as e:
            print(f"  ⚠️ Error fetching page 1: {e}")
            return [], {}
            
        items = list(first.get("items") or [])
        relations: dict = {}
        merge_relations(relations, first.get("relations") or {})
        last_page = int(first.get("lastPage") or 1)
        if last_page <= 1:
            return items, relations

        pages = list(range(2, last_page + 1))
        
        print(f"  ⚡ Fetching {len(pages)} pages asynchronously...")
        
        # Concurrency limiter to prevent throttling
        sem = asyncio.Semaphore(15)
        
        async def fetch_with_sem(page):
            async with sem:
                try:
                    return await fetch_event_list_page(session, page)
                except Exception as e:
                    print(f"  ⚠️ Error fetching page {page}: {e}")
                    return None
                    
        tasks = [fetch_with_sem(p) for p in pages]
        results = await asyncio.gather(*tasks)

        for block in results:
            if block:
                items.extend(block.get("items") or [])
                merge_relations(relations, block.get("relations") or {})
                
        return items, relations


def index_by_id(rows: List[dict]) -> Dict[int, dict]:
    out: Dict[int, dict] = {}
    for r in rows:
        if isinstance(r, dict) and "id" in r:
            out[int(r["id"])] = r
    return out


def pick_1x2(markets: List[dict]) -> Optional[Dict[str, Any]]:
    for m in markets:
        if m.get("vendorMarketId") != 1:
            continue
        if m.get("specifiers"):
            continue
        outs = m.get("outcomes") or []
        if len(outs) != 3:
            continue
        by_vo: Dict[str, float] = {}
        for o in outs:
            if o.get("active") != 1:
                return None
            vid = str(o.get("vendorOutcomeId") or "")
            by_vo[vid] = o.get("odds")
        if not {"1", "2", "3"} <= set(by_vo):
            continue
        return {
            "home": float(by_vo.get("1")),
            "draw": float(by_vo.get("2")),
            "away": float(by_vo.get("3")),
        }
    return None


def pick_btts(markets: List[dict]) -> Optional[Dict[str, Any]]:
    for m in markets:
        if m.get("vendorMarketId") != 29:
            continue
        if m.get("specifiers"):
            continue
        outs = m.get("outcomes") or []
        if len(outs) != 2:
            continue
        if any(o.get("active") != 1 for o in outs):
            return None
        by_vo = {str(o.get("vendorOutcomeId") or ""): o.get("odds") for o in outs}
        if "74" in by_vo and "76" in by_vo:
            return {"yes": float(by_vo["74"]), "no": float(by_vo["76"])}
    return None


def pick_ou_lines(markets: List[dict]) -> Optional[Dict[str, Any]]:
    ou_lines = {}
    for m in markets:
        if m.get("vendorMarketId") != 18:
            continue
        spec = m.get("specifiers") or ""
        match = re.search(r'total=(\d+\.5)', spec)
        if not match:
            continue
        line_str = match.group(1)
        outs = m.get("outcomes") or []
        if len(outs) != 2:
            continue
        if any(o.get("active") != 1 for o in outs):
            continue
        by_vo = {str(o.get("vendorOutcomeId")): o.get("odds") for o in outs}
        over = by_vo.get("12")
        under = by_vo.get("13")
        if over is not None and under is not None:
            ou_lines[line_str] = {"over": float(over), "under": float(under)}
            
    return ou_lines if ou_lines else None


def build_league_label(league: dict, cat_by_id: Dict[int, dict]) -> str:
    name = (league.get("name") or "").strip()
    lid = league.get("sportCategoryId")
    if lid is None or not name:
        return name
    cat = cat_by_id.get(int(lid))
    cname = ((cat or {}).get("name") or "").strip()
    if not cname:
        return name
    if name.lower().startswith(cname.lower() + ".") or name.lower().startswith(cname.lower() + " "):
        return name
    return f"{cname}. {name}"


def scrape_for_calendar_day(
    items: List[dict],
    relations: dict,
    target: date,
    now_utc: datetime,
) -> List[dict]:
    league_by_id = index_by_id(relations.get("league") or [])
    comp_by_id = index_by_id(relations.get("competitors") or [])
    cat_by_id = index_by_id(relations.get("sportCategories") or [])
    odds_root = relations.get("odds") or {}

    out: List[dict] = []
    
    for ev in items:
        if ev.get("sportId") != 1:
            continue
        if ev.get("status") != 0:
            continue
        t_raw = ev.get("time")
        if not t_raw:
            continue
            
        kick_utc = parse_kickoff_utc(str(t_raw))
        if kick_utc.date() != target:
            continue
        if kick_utc <= now_utc:
            continue

        eid = str(ev.get("id") or ev.get("sbEventId") or "")
        markets = odds_root.get(eid) or []
        
        odds_1x2 = pick_1x2(markets) or {}
        odds_gg = pick_btts(markets) or {}
        odds_ou = pick_ou_lines(markets) or {}

        if not odds_1x2:
            continue

        lg = league_by_id.get(int(ev.get("leagueId") or 0), {})
        c1 = comp_by_id.get(int(ev.get("competitor1Id") or 0), {})
        c2 = comp_by_id.get(int(ev.get("competitor2Id") or 0), {})
        
        home_team = c1.get("name") or ev.get("team1") or ""
        away_team = c2.get("name") or ev.get("team2") or ""
        
        if not home_team or not away_team:
            continue

        row: Dict[str, Any] = {
            "home_team": home_team,
            "away_team": away_team,
            "kickoff": kick_utc.strftime("%Y-%m-%d %H:%M"),
            "tournament": build_league_label(lg, cat_by_id),
            "is_live": False,
            "source": "twentytwobet_gh",
            "odds_1x2": odds_1x2,
            "odds_ou": odds_ou,
            "odds_gg": odds_gg,
        }
        
        out.append(row)

    out.sort(key=lambda x: x["kickoff"])
    return out


def display_matches(matches):
    if not matches:
        print("⚠️ No matches found")
        return
    print(f"\n📋 22BET GHANA (FAST API)")
    print(f"⚽ Total matches: {len(matches)}")
    print("=" * 50)
    print("\n📝 Sample (first 10):")
    for match in matches[:10]:
        live_tag = "🔴" if match.get('is_live') else ""
        print(f"  {live_tag} {match['home_team']} vs "
              f"{match['away_team']} | {match['kickoff']} | "
              f"{match['tournament']}")
    if len(matches) > 10:
        print(f"  ... and {len(matches) - 10} more")
    print("=" * 50)


def run():
    start = _time.time()
    print("\n" + "🟣 " * 20)
    print("   22BET GHANA SCRAPER (FAST API)")
    print(f"   {datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}")
    print("🟣 " * 20 + "\n")

    now_utc = datetime.now(timezone.utc)
    today = now_utc.date()
    
    # Run the async fetching
    items, relations = asyncio.run(fetch_all_prematch())
    matches = scrape_for_calendar_day(items, relations, today, now_utc)
    
    # Try tomorrow if today is empty
    if not matches:
        calendar_date = today + timedelta(days=1)
        matches = scrape_for_calendar_day(items, relations, calendar_date, now_utc)

    if matches:
        display_matches(matches)

        os.makedirs("data", exist_ok=True)
        import json as _json
        with open('data/twentytwobet_odds.json', 'w') as f:
            _json.dump(matches, f, indent=2)
            
        with open('data/twentytwobet_matches.txt', 'w', encoding='utf-8') as f:
            f.write("22BET GHANA - ALL MATCHES\n")
            f.write(f"Generated: {datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}\n")
            f.write(f"Total: {len(matches)} matches\n")
            f.write("=" * 60 + "\n\n")
            for match in matches:
                live_tag = "🔴 LIVE" if match.get('is_live') else ""
                f.write(f"⚽ {match['home_team']} vs {match['away_team']} {live_tag}\n")
                f.write(f"🏆 {match['tournament']}\n")
                f.write(f"🕐 {match['kickoff']}\n")
                if match['odds_1x2']:
                    o = match['odds_1x2']
                    f.write(f"1X2: {o['home']} | {o['draw']} | {o['away']}\n")
                if match['odds_ou']:
                    for line_str, ou in match['odds_ou'].items():
                        f.write(f"O/U {line_str}: Over {ou['over']} | Under {ou['under']}\n")
                if match['odds_gg']:
                    gg = match['odds_gg']
                    f.write(f"GG/NG: Yes {gg.get('yes', '?')} | No {gg.get('no', '?')}\n")
                f.write("\n")

        print(f"💾 Saved to data/twentytwobet_odds.json")
        print(f"📄 Full list: data/twentytwobet_matches.txt")
        print(f"   Open the .txt file to see all {len(matches)} matches!")
        print(f"⏱️  Scraping completed in {_time.time() - start:.1f}s")
    else:
        print("\n⚠️ No matches found")

    return matches


if __name__ == "__main__":
    run()