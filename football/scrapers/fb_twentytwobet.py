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


def pick_all_ou_lines(markets: List[dict]) -> Dict[str, Any]:
    """Fetch ALL total lines (whole, .5, .25, .75) returning exact string keys."""
    ou_lines = {}
    for m in markets:
        if m.get("vendorMarketId") != 18:
            continue
        spec = m.get("specifiers") or ""
        match = re.search(r'total=(\d+(?:\.\d+)?)', spec)
        if not match:
            continue
        raw_line = match.group(1)
        # Normalize key to str(float) to ensure "2" and "2.0" are the same
        try:
            line_str = str(float(raw_line))
        except ValueError:
            continue
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

    return ou_lines


def pick_dc(markets: List[dict]) -> Optional[Dict[str, Any]]:
    for m in markets:
        if m.get("vendorMarketId") != 10:
            continue
        if m.get("specifiers"):
            continue
        outs = m.get("outcomes") or []
        if len(outs) != 3:
            continue
        by_vo = {str(o.get("vendorOutcomeId") or ""): o.get("odds") for o in outs}
        if "9" in by_vo and "10" in by_vo and "11" in by_vo:
            return {
                "1x": float(by_vo["9"]),
                "12": float(by_vo["10"]),
                "x2": float(by_vo["11"]),
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
        odds_dc = pick_dc(markets) or {}

        # Fetch all O/U lines (standard .5 and asian wholes/.25/.75)
        all_ou = pick_all_ou_lines(markets)
        odds_ou = {}
        odds_asian_ou = {}
        for line_str, val in all_ou.items():
            try:
                line_val = float(line_str)
            except ValueError:
                continue
            if line_val % 1.0 == 0.5:
                odds_ou[line_str] = val
            else:
                odds_asian_ou[line_str] = val

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
            "odds_ou": odds_ou,
            "odds_asian_ou": odds_asian_ou,
            "odds_gg": odds_gg,
            "odds_gg_2plus": {},
            "odds_dc": odds_dc,
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


def run():
    output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
    os.makedirs(output_dir, exist_ok=True)
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

        output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
        os.makedirs(output_dir, exist_ok=True)
        import json as _json
        with open(os.path.join(output_dir, 'twentytwobet_odds.json'), 'w') as f:
            _json.dump(matches, f, indent=2)
            
        with open(os.path.join(output_dir, 'twentytwobet_matches.txt'), 'w', encoding='utf-8') as f:
            f.write("22BET GHANA - ALL MATCHES\n")
            f.write(f"Generated: {datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}\n")
            f.write(f"Total: {len(matches)} matches\n")
            f.write("=" * 60 + "\n\n")
            for match in matches:
                f.write(format_match_text_block(match))

        print(f"💾 Saved to {os.path.join(output_dir, 'twentytwobet_odds.json')}")
        print(f"📄 Full list: {os.path.join(output_dir, 'twentytwobet_matches.txt')}")
        print(f"   Open the .txt file to see all {len(matches)} matches!")
        print(f"⏱️  Scraping completed in {_time.time() - start:.1f}s")
    else:
        print("\n⚠️ No matches found")

    return matches


if __name__ == "__main__":
    run()