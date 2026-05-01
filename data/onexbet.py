"""
1xBet Ghana Scraper — onexbet.py
=================================
Fast scraper: browser captures API URL, then fetches ALL data
via in-browser JavaScript (stays in auth context).

Strategy:
  1. Open browser, navigate to prematch page
  2. Capture the event/list API URL from intercepted response
  3. Use page.evaluate() to re-fetch with count=500 (all-in-one)
  4. Parse & filter to today's upcoming matches
  5. Close browser — total time ~10-15s

Same platform as 22Bet — uses identical API structure.
"""

import asyncio
import json
import os
import re
import time as _time
from datetime import datetime, timedelta

from playwright.async_api import async_playwright

SITE_URL     = 'https://1xbet.com.gh'
PREMATCH_URL = f'{SITE_URL}/prematch/football'
LEGACY_URL   = f'{SITE_URL}/en/line/football'
OUTPUT_DIR   = 'data'


async def scrape_onexbet():
    """Main scraper — browser-based but fast."""
    start = _time.time()

    captured_url  = None
    captured_data = None
    api_type      = None   # 'event_list' or 'linefeed'

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                       'AppleWebKit/537.36 (KHTML, like Gecko) '
                       'Chrome/122.0.0.0 Safari/537.36',
            viewport={'width': 1366, 'height': 768},
        )

        # Block heavy assets for speed
        await context.route(
            re.compile(r'\.(png|jpg|jpeg|gif|svg|ico|woff|woff2|ttf|mp4|mp3)(\?|$)', re.I),
            lambda route: route.abort()
        )

        page = await context.new_page()

        async def on_response(response):
            nonlocal captured_url, captured_data, api_type
            if captured_url:
                return
            url = response.url
            try:
                # Modern event/list API (same as 22Bet)
                if 'event/list' in url:
                    body = await response.body()
                    if len(body) < 200:
                        return
                    data = json.loads(body)
                    inner = data.get('data', {})
                    if 'items' in inner and inner['items']:
                        captured_url  = url
                        captured_data = inner
                        api_type = 'event_list'
                        total   = inner.get('totalCount', 0)
                        last_pg = inner.get('lastPage', 1)
                        print(f"  📥 Captured event/list: "
                              f"{len(inner['items'])} events "
                              f"(total={total}, pages={last_pg})")
                        return

                # Legacy LineFeed API
                if 'LineFeed' in url:
                    if any(x in url for x in (
                        'banner', 'TopGames', 'WebGetTop',
                        'GetTopChamp', 'Sports?')):
                        return
                    body = await response.body()
                    if len(body) < 100:
                        return
                    data = json.loads(body)
                    events = extract_linefeed_events(data)
                    if len(events) >= 3:
                        captured_url  = url
                        captured_data = events
                        api_type = 'linefeed'
                        print(f"  📥 Captured LineFeed: {len(events)} events")
                        return
            except Exception:
                pass

        page.on('response', on_response)

        # Try modern prematch URL first
        print("🌐 Loading 1xBet (prematch)...")
        try:
            await page.goto(PREMATCH_URL, timeout=30000,
                            wait_until='domcontentloaded')
        except Exception as e:
            print(f"  ⚠️ {str(e)[:60]}")

        # Wait up to 12s for API
        for _ in range(24):
            if captured_url:
                break
            await page.wait_for_timeout(500)

        # If prematch didn't work, try legacy URL
        if not captured_url:
            print("  ⚠️ No data from prematch, trying legacy URL...")
            try:
                await page.goto(LEGACY_URL, timeout=30000,
                                wait_until='domcontentloaded')
            except Exception as e:
                print(f"  ⚠️ {str(e)[:60]}")

            for _ in range(30):
                if captured_url:
                    break
                await page.wait_for_timeout(500)

        if not captured_url:
            print("❌ Could not capture 1xBet API")
            await browser.close()
            return []

        # ── Process based on API type ─────────────────────────────────────
        all_matches = []

        if api_type == 'event_list':
            all_matches = await process_event_list(
                page, captured_url, captured_data)

        elif api_type == 'linefeed':
            all_matches = await process_linefeed(
                page, captured_url, captured_data)

        await browser.close()

    elapsed = _time.time() - start
    print(f"⏱️  Completed in {elapsed:.1f}s")
    return all_matches


# ── EVENT/LIST PROCESSING (modern API, same as 22Bet) ─────────────────────────

async def process_event_list(page, captured_url, captured_data):
    """Process the modern event/list API with batched fetching."""
    # API ignores count param, stuck at 50/page. Use batched parallel fetch.
    last_page = captured_data.get('lastPage', 1)
    all_pages = [captured_data]

    if last_page > 1:
        BATCH_SIZE = 8
        all_page_urls = []
        for pg in range(2, last_page + 1):
            if 'page=' in captured_url:
                pg_url = re.sub(r'page=\d+', f'page={pg}', captured_url)
            else:
                sep = '&' if '?' in captured_url else '?'
                pg_url = f'{captured_url}{sep}page={pg}'
            all_page_urls.append(pg_url)

        print(f"  ⚡ Fetching {len(all_page_urls)} pages "
              f"(batches of {BATCH_SIZE})...")

        for batch_start in range(0, len(all_page_urls), BATCH_SIZE):
            batch = all_page_urls[batch_start:batch_start + BATCH_SIZE]
            batch_json = json.dumps(batch)
            batch_num = batch_start // BATCH_SIZE + 1
            total_batches = (len(all_page_urls) + BATCH_SIZE - 1) // BATCH_SIZE

            try:
                more = await page.evaluate(f"""
                    async () => {{
                        const urls = {batch_json};
                        return await Promise.all(
                            urls.map(url =>
                                fetch(url, {{ credentials: 'include' }})
                                    .then(r => r.json())
                                    .catch(() => null)
                            )
                        );
                    }}
                """)
                for r in (more or []):
                    if r:
                        inner2 = r.get('data', {})
                        if inner2 and 'items' in inner2:
                            all_pages.append(inner2)
                print(f"  ✅ Batch {batch_num}/{total_batches} done "
                      f"({len(all_pages)} pages total)")
            except Exception as e:
                print(f"  ⚠️ Batch {batch_num} error: {str(e)[:60]}")

            if batch_start + BATCH_SIZE < len(all_page_urls):
                await page.wait_for_timeout(300)

        print(f"  ✅ All {len(all_pages)} pages fetched")

    # Merge pages
    all_items, comp_map, league_map, odds_map = merge_event_pages(all_pages)
    print(f"📦 Total raw events: {len(all_items)}")
    return filter_event_list_matches(all_items, comp_map, league_map, odds_map)


def merge_event_pages(all_pages):
    """Merge multiple event/list pages into combined maps."""
    all_items  = []
    comp_map   = {}
    league_map = {}
    odds_map   = {}

    for inner in all_pages:
        all_items.extend(inner.get('items', []))
        relations = inner.get('relations', {})

        raw_comp = relations.get('competitors', [])
        if isinstance(raw_comp, list):
            for c in raw_comp:
                if c.get('id') is not None:
                    comp_map[c['id']] = c
        elif isinstance(raw_comp, dict):
            comp_map.update(raw_comp)

        raw_league = relations.get('league', [])
        if isinstance(raw_league, list):
            for lg in raw_league:
                if lg.get('id') is not None:
                    league_map[lg['id']] = lg
        elif isinstance(raw_league, dict):
            league_map.update(raw_league)

        raw_odds = relations.get('odds', {})
        if isinstance(raw_odds, dict):
            for eid, markets in raw_odds.items():
                if eid in odds_map:
                    if isinstance(markets, list):
                        odds_map[eid].extend(markets)
                    else:
                        odds_map[eid] = markets
                else:
                    odds_map[eid] = markets if isinstance(markets, list) else [markets]

    return all_items, comp_map, league_map, odds_map


def parse_event_list_item(item, comp_map, league_map, odds_map):
    """Parse one event/list item into standard match format."""
    event_id  = str(item.get('id'))
    c1_id     = item.get('competitor1Id')
    c2_id     = item.get('competitor2Id')
    league_id = item.get('leagueId')
    time_str  = item.get('time', '')
    status    = item.get('status', 0)

    c1     = comp_map.get(c1_id, {}) or {}
    c2     = comp_map.get(c2_id, {}) or {}
    league = league_map.get(league_id, {}) or {}

    home_team   = c1.get('name', '')
    away_team   = c2.get('name', '')
    league_name = league.get('name', '')

    if not home_team or not away_team:
        return None
    if status in [2, 3, 4]:
        return None

    try:
        kickoff_dt = datetime.strptime(time_str, '%Y-%m-%d %H:%M:%S')
        kickoff    = kickoff_dt.strftime('%Y-%m-%d %H:%M')
    except Exception:
        return None

    match = {
        'home_team': home_team, 'away_team': away_team,
        'kickoff': kickoff, 'tournament': league_name,
        'is_live': (status == 1), 'source': '1xbet_gh',
        'odds_1x2': {}, 'odds_ou': {}, 'odds_gg': {},
    }

    for market in (odds_map.get(event_id, []) or []):
        mid  = market.get('id')
        spec = market.get('specifiers', '') or ''
        outs = market.get('outcomes', [])

        if mid == 621:
            h = next((o.get('odds') for o in outs
                      if o.get('id') == 1 and o.get('active') == 1), None)
            d = next((o.get('odds') for o in outs
                      if o.get('id') == 2 and o.get('active') == 1), None)
            a = next((o.get('odds') for o in outs
                      if o.get('id') == 3 and o.get('active') == 1), None)
            if h and d and a:
                match['odds_1x2'] = {
                    'home': float(h), 'draw': float(d), 'away': float(a)}

        if mid == 289 and 'total=2.5' in spec:
            ov = next((o.get('odds') for o in outs
                       if o.get('id') == 12 and o.get('active') == 1), None)
            un = next((o.get('odds') for o in outs
                       if o.get('id') == 13 and o.get('active') == 1), None)
            if ov and un:
                match['odds_ou'] = {
                    'line': 2.5, 'over': float(ov), 'under': float(un)}

    if not match['odds_1x2']:
        return None
    return match


def filter_event_list_matches(all_items, comp_map, league_map, odds_map):
    """Filter event/list items to **today only** (no tomorrow fallback)."""
    now = datetime.now()
    today = now.date()
    today_matches = []
    for item in all_items:
        try:
            m = parse_event_list_item(item, comp_map, league_map, odds_map)
            if not m:
                continue
            kdt = datetime.strptime(m['kickoff'], '%Y-%m-%d %H:%M')
            if kdt <= now:
                continue
            if kdt.date() == today:
                today_matches.append(m)
        except Exception:
            continue
    if today_matches:
        today_matches.sort(key=lambda x: x['kickoff'])
        print(f"✅ Upcoming matches: {len(today_matches)}")
        return today_matches
    print("⚠️ No matches for today")
    return []


# ── LINEFEED PROCESSING (legacy API) ─────────────────────────────────────────

def extract_linefeed_events(data):
    """Recursively pull all match dicts (those with O1 + O2 fields)."""
    found = []
    def walk(node):
        if isinstance(node, dict):
            if {'O1', 'O2'} <= node.keys():
                found.append(node)
                return
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)
    walk(data)
    return found


async def process_linefeed(page, captured_url, initial_events):
    """Process legacy LineFeed API data."""
    # Try to get more events with count=500
    big_url = re.sub(r'count=\d+', 'count=500', captured_url)
    if 'count=' not in big_url:
        sep = '&' if '?' in big_url else '?'
        big_url = f'{big_url}{sep}count=500'

    all_events = list(initial_events)

    print(f"  ⚡ Re-fetching with count=500...")
    try:
        result = await page.evaluate(f"""
            async () => {{
                try {{
                    const resp = await fetch("{big_url}", {{
                        credentials: 'include',
                        headers: {{
                            'Accept': 'application/json',
                            'X-Requested-With': 'XMLHttpRequest'
                        }}
                    }});
                    return await resp.json();
                }} catch(e) {{
                    return null;
                }}
            }}
        """)
        if result:
            events = extract_linefeed_events(result)
            if events and len(events) > len(all_events):
                all_events = events
                print(f"  ✅ Got {len(all_events)} events")
    except Exception as e:
        print(f"  ⚠️ Re-fetch failed: {str(e)[:60]}")

    return filter_linefeed_matches(all_events)


def norm_ts(ts):
    if not ts:
        return 0
    ts = int(ts)
    if ts > 9_999_999_999:
        ts //= 1000
    return ts


def filter_linefeed_matches(raw_events):
    """Parse and filter LineFeed events to **today only** (no tomorrow fallback)."""
    import time
    now = datetime.now()
    today = now.date()
    now_ts = int(time.time())

    today_matches = []

    for ev in raw_events:
        if not ev.get('O1') or not ev.get('O2'):
            continue
        # Only football (sport ID 1)
        sport_id = ev.get('SI') or ev.get('SportId') or ev.get('sport_id')
        if sport_id is not None and int(sport_id) != 1:
            continue
        # Skip non‑football tournaments
        league = (ev.get('LE') or '').lower()
        non_football = ['ufc', 'nhl', 'nba', 'nfl', 'mlb', 'tennis',
                        'boxing', 'mma', 'hockey', 'basketball',
                        'baseball', 'cricket', 'rugby', 'handball',
                        'volleyball', 'table tennis', 'badminton',
                        'darts', 'snooker', 'counter-strike', 'dota',
                        'league of legends', 'valorant']
        if any(nf in league for nf in non_football):
            continue
        start_ts = norm_ts(ev.get('S') or 0)
        if start_ts <= now_ts:
            continue
        try:
            dt = datetime.fromtimestamp(start_ts)
        except Exception:
            continue
        kickoff = dt.strftime('%Y-%m-%d %H:%M')
        # Parse odds from E array
        w1 = x = w2 = over = under = 0.0
        for item in (ev.get('E') or []):
            g, t, p_val = item.get('G'), item.get('T'), item.get('P')
            val = float(item.get('CV', 0) or 0)
            if g == 1:
                if t == 1 and not w1: w1 = val
                if t == 2 and not x:  x  = val
                if t == 3 and not w2: w2 = val
            elif g == 17 and p_val is not None:
                try:
                    pf = float(p_val)
                except (ValueError, TypeError):
                    pf = 0.0
                if abs(pf - 2.5) < 0.01:
                    if t == 9  and not over:  over  = val
                    if t == 10 and not under: under = val
        if not (w1 and x and w2):
            continue
        match = {
            'home_team': ev.get('O1', '?'),
            'away_team': ev.get('O2', '?'),
            'kickoff': kickoff,
            'tournament': ev.get('LE') or 'Unknown',
            'is_live': False,
            'source': '1xbet_gh',
            'odds_1x2': {'home': w1, 'draw': x, 'away': w2},
            'odds_ou': {'line': 2.5, 'over': over, 'under': under} if over and under else {},
            'odds_gg': {},
        }
        if dt.date() == today:
            today_matches.append(match)
    if today_matches:
        today_matches.sort(key=lambda x: x['kickoff'])
        print(f"✅ Upcoming matches: {len(today_matches)}")
        return today_matches
    print("⚠️ No matches for today")
    return []


# ── DISPLAY & SAVE ────────────────────────────────────────────────────────────

def display_matches(matches):
    if not matches:
        print("⚠️ No matches found")
        return
    print(f"\n📋 1XBET GHANA")
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


def save_results(matches):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    jfile = os.path.join(OUTPUT_DIR, 'onexbet_odds.json')
    tfile = os.path.join(OUTPUT_DIR, 'onexbet_matches.txt')

    with open(jfile, 'w', encoding='utf-8') as f:
        json.dump(matches, f, indent=2, ensure_ascii=False)

    with open(tfile, 'w', encoding='utf-8') as f:
        f.write("1XBET GHANA - ALL MATCHES\n")
        f.write(f"Generated: "
                f"{datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}\n")
        f.write(f"Total: {len(matches)} matches\n")
        f.write("=" * 60 + "\n\n")
        for match in matches:
            live_tag = "🔴 LIVE" if match.get('is_live') else ""
            f.write(f"⚽ {match['home_team']} vs "
                    f"{match['away_team']} {live_tag}\n")
            f.write(f"🏆 {match['tournament']}\n")
            f.write(f"🕐 {match['kickoff']}\n")
            if match['odds_1x2']:
                o = match['odds_1x2']
                f.write(f"1X2: {o['home']} | {o['draw']} | {o['away']}\n")
            if match['odds_ou']:
                ou = match['odds_ou']
                f.write(f"O/U 2.5: Over {ou['over']} | Under {ou['under']}\n")
            f.write("\n")

    return jfile, tfile


# ── PUBLIC run() ───────────────────────────────────────────────────────────────

def run():
    print("\n" + "🔴 " * 20)
    print("   1XBET GHANA SCRAPER")
    print(f"   {datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}")
    print("🔴 " * 20 + "\n")

    matches = asyncio.run(scrape_onexbet())

    if matches:
        display_matches(matches)
        jfile, tfile = save_results(matches)
        print(f"💾 Saved to {jfile}")
        print(f"📄 Full list saved to {tfile}")
    else:
        print("\n⚠️ No matches found")

    return matches


if __name__ == "__main__":
    run()