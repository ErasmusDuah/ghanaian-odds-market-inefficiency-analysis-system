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
REFERRER = "https://22bet.com.gh/prematch/table-tennis"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

EVENT_LIST_PATH = "/api/event/list"
EVENT_LIST_QUERY = (
    "lang=en"
    "&relations=odds&relations=league&relations=competitors&relations=sportCategories"
    "&oddsExists_eq=1&main=1&period=0&sportId_eq=15&limit=50&status_in=0"
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


def pick_2way_winner(markets: List[dict]) -> Optional[Dict[str, Any]]:
    for m in markets:
        if m.get("vendorMarketId") != 186:
            continue
        if m.get("specifiers"):
            continue
        outs = m.get("outcomes") or []
        if len(outs) != 2:
            continue
        by_vo = {str(o.get("vendorOutcomeId") or ""): o.get("odds") for o in outs}
        if "4" in by_vo and "5" in by_vo:
            return {
                "home": float(by_vo.get("4")),
                "away": float(by_vo.get("5")),
            }
    return None


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
        if ev.get("sportId") != 15:
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
        
        odds_2way = pick_2way_winner(markets) or {}
        if not odds_2way:
            continue

        lg = league_by_id.get(int(ev.get("leagueId") or 0), {})
        c1 = comp_by_id.get(int(ev.get("competitor1Id") or 0), {})
        c2 = comp_by_id.get(int(ev.get("competitor2Id") or 0), {})
        
        home_team = c1.get("name") or ev.get("team1") or ""
        away_team = c2.get("name") or ev.get("team2") or ""
        
        if not home_team or not away_team:
            continue

        # Skip virtual / esports leagues
        league_label = build_league_label(lg, cat_by_id)
        if any(x in league_label.lower() for x in ['virtual', 'cyber', 'esoccer', 'esport']):
            continue

        row: Dict[str, Any] = {
            "home_team": home_team,
            "away_team": away_team,
            "kickoff": kick_utc.strftime("%Y-%m-%d %H:%M"),
            "tournament": league_label,
            "is_live": False,
            "source": "twentytwobet_gh",
            "odds_2way": odds_2way,
        }
        
        out.append(row)

    out.sort(key=lambda x: x["kickoff"])
    return out


def display_matches(matches):
    if not matches:
        print("⚠️ No matches found")
        return
    print(f"\n📋 22BET GHANA TABLE TENNIS")
    print(f"🏓 Total matches: {len(matches)}")
    print("=" * 50)
    print("\n📝 Sample (first 10):")
    for match in matches[:10]:
        o = match['odds_2way']
        print(f"  {match['home_team']} vs {match['away_team']} | {match['kickoff']} | H {o['home']} - A {o['away']}")
    if len(matches) > 10:
        print(f"  ... and {len(matches) - 10} more")
    print("=" * 50)


def run():
    start = _time.time()
    print("\n" + "🟣 " * 20)
    print("   22BET GHANA TABLE TENNIS SCRAPER (FAST API)")
    print(f"   {datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}")
    print("🟣 " * 20 + "\n")

    now_utc = datetime.now(timezone.utc)
    today = now_utc.date()
    
    items, relations = asyncio.run(fetch_all_prematch())
    matches = scrape_for_calendar_day(items, relations, today, now_utc)
    
    # Establish independent directories
    output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
    os.makedirs(output_dir, exist_ok=True)
    
    json_path = os.path.join(output_dir, 'twentytwobet_tt_odds.json')
    txt_path  = os.path.join(output_dir, 'twentytwobet_tt_matches.txt')

    if matches:
        display_matches(matches)

        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(matches, f, indent=2, ensure_ascii=False)
            
        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write("22BET GHANA TABLE TENNIS - ALL MATCHES\n")
            f.write(f"Generated: {datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}\n")
            f.write(f"Total: {len(matches)} matches\n")
            f.write("=" * 60 + "\n\n")
            for match in matches:
                f.write(f"{match['home_team']} vs {match['away_team']}\n")
                f.write(f"{match['tournament']}\n")
                f.write(f"{match['kickoff']}\n")
                o = match['odds_2way']
                f.write(f"Winner (2-way): Home {o['home']} | Away {o['away']}\n\n")

        print(f"Saved to {json_path}")
        print(f"Full list saved to {txt_path}")
        print(f"⏱️  Scraping completed in {_time.time() - start:.1f}s")
    else:
        print("\n⚠️ No matches found")
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump([], f)

    return matches


if __name__ == "__main__":
    run()
