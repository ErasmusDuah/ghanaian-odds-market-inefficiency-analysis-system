"""
22Bet Ghana Scraper — twentytwobet.py
======================================
Fast scraper: browser captures API URL, then fetches ALL data
via in-browser JavaScript (stays in auth context, no extra pages).

Strategy:
  1. Open browser, navigate to prematch page
  2. Capture the event/list API URL from intercepted response
  3. Use page.evaluate() to re-fetch with count=500 (all-in-one)
  4. Parse & filter to today's upcoming matches
  5. Close browser — total time ~10-15s
"""

import asyncio
import json
import os
import re
import time as _time
from datetime import datetime, timedelta

from playwright.async_api import async_playwright

SITE_URL     = 'https://22bet.com.gh'
PREMATCH_URL = f'{SITE_URL}/prematch/football'
OUTPUT_DIR   = 'data'


async def scrape_twentytwobet():
    """Main scraper — browser-based but fast."""
    start = _time.time()

    captured_url  = None
    captured_data = None

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
            nonlocal captured_url, captured_data
            if captured_url:
                return
            url = response.url
            if 'event/list' not in url:
                return
            try:
                body = await response.body()
                if len(body) < 200:
                    return
                data = json.loads(body)
                inner = data.get('data', {})
                if 'items' in inner and inner['items']:
                    captured_url  = url
                    captured_data = inner
                    total   = inner.get('totalCount', 0)
                    last_pg = inner.get('lastPage', 1)
                    print(f"  📥 Captured API: {len(inner['items'])} events "
                          f"(total={total}, pages={last_pg})")
            except Exception:
                pass

        page.on('response', on_response)

        print("🌐 Loading 22Bet...")
        try:
            await page.goto(PREMATCH_URL, timeout=45000,
                            wait_until='domcontentloaded')
        except Exception as e:
            print(f"  ⚠️ Page load: {str(e)[:60]}")

        # Wait for API capture (up to 20s)
        for _ in range(40):
            if captured_url:
                break
            await page.wait_for_timeout(500)

        if not captured_url:
            print("❌ Could not capture API URL")
            await browser.close()
            return []

        # ── Fetch all pages via in-browser JS (batched) ──────────────────
        # API ignores count param, stuck at 50/page.
        # Fetch in batches of 8 to avoid rate limiting.
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
                            const results = await Promise.all(
                                urls.map(url =>
                                    fetch(url, {{ credentials: 'include' }})
                                        .then(r => r.json())
                                        .catch(() => null)
                                )
                            );
                            return results;
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

                # Small delay between batches to avoid rate limiting
                if batch_start + BATCH_SIZE < len(all_page_urls):
                    await page.wait_for_timeout(300)

            print(f"  ✅ All {len(all_pages)} pages fetched")

        await browser.close()

    # ── Merge all pages ───────────────────────────────────────────────────
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
            # CRITICAL: Must EXTEND, not overwrite — same event's
            # markets may be split across pages
            for eid, markets in raw_odds.items():
                if eid in odds_map:
                    if isinstance(markets, list):
                        odds_map[eid].extend(markets)
                    else:
                        odds_map[eid] = markets
                else:
                    odds_map[eid] = markets if isinstance(markets, list) else [markets]

    print(f"📦 Total raw events (with dupes): {len(all_items)}")

    # Deduplicate events by ID — same event can appear on multiple pages
    seen_ids = set()
    unique_items = []
    for item in all_items:
        eid = item.get('id')
        if eid not in seen_ids:
            seen_ids.add(eid)
            unique_items.append(item)
    all_items = unique_items
    print(f"📦 Unique events: {len(all_items)}")

    # ── Filter to today (fallback tomorrow) ───────────────────────────────
    matches = filter_matches(all_items, comp_map, league_map, odds_map)

    elapsed = _time.time() - start
    print(f"⏱️  Completed in {elapsed:.1f}s")
    return matches


def parse_event(item, comp_map, league_map, odds_map):
    """Parse a single event into standard match format."""
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
        'home_team':  home_team,
        'away_team':  away_team,
        'kickoff':    kickoff,
        'tournament': league_name,
        'is_live':    (status == 1),
        'source':     'twentytwobet_gh',
        'odds_1x2':   {},
        'odds_ou':    {},
        'odds_gg':    {},
    }

    for market in (odds_map.get(event_id, []) or []):
        market_id  = market.get('id')
        specifiers = market.get('specifiers', '') or ''
        outcomes   = market.get('outcomes', [])

        if market_id == 621:
            home = next((o.get('odds') for o in outcomes
                         if o.get('id') == 1 and o.get('active') == 1), None)
            draw = next((o.get('odds') for o in outcomes
                         if o.get('id') == 2 and o.get('active') == 1), None)
            away = next((o.get('odds') for o in outcomes
                         if o.get('id') == 3 and o.get('active') == 1), None)
            if home and draw and away:
                match['odds_1x2'] = {
                    'home': float(home), 'draw': float(draw),
                    'away': float(away),
                }

        if market_id == 289 and 'total=2.5' in specifiers:
            over  = next((o.get('odds') for o in outcomes
                          if o.get('id') == 12 and o.get('active') == 1), None)
            under = next((o.get('odds') for o in outcomes
                          if o.get('id') == 13 and o.get('active') == 1), None)
            if over and under:
                match['odds_ou'] = {
                    'line': 2.5, 'over': float(over),
                    'under': float(under),
                }

    if not match['odds_1x2']:
        return None
    return match


def filter_matches(all_items, comp_map, league_map, odds_map):
    """Filter to today's upcoming matches."""
    now   = datetime.now()
    today = now.date()

    today_matches = []

    for item in all_items:
        try:
            match = parse_event(item, comp_map, league_map, odds_map)
            if not match:
                continue
            kickoff_dt = datetime.strptime(match['kickoff'], '%Y-%m-%d %H:%M')
            if kickoff_dt <= now:
                continue
            
            if kickoff_dt.date() == today:
                today_matches.append(match)
        except Exception:
            continue

    if today_matches:
        today_matches.sort(key=lambda x: x['kickoff'])
        print(f"✅ Upcoming matches: {len(today_matches)}")
        return today_matches
    return []


def display_matches(matches):
    if not matches:
        print("⚠️ No matches found")
        return
    print(f"\n📋 22BET GHANA")
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
    print("\n" + "🟣 " * 20)
    print("   22BET GHANA SCRAPER")
    print(f"   {datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}")
    print("🟣 " * 20 + "\n")

    matches = asyncio.run(scrape_twentytwobet())

    if matches:
        display_matches(matches)

        os.makedirs(OUTPUT_DIR, exist_ok=True)
        with open('data/twentytwobet_odds.json', 'w') as f:
            json.dump(matches, f, indent=2)
        with open('data/twentytwobet_matches.txt', 'w',
                  encoding='utf-8') as f:
            f.write("22BET GHANA - ALL MATCHES\n")
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
                    f.write(f"O/U 2.5: Over {ou['over']} | "
                            f"Under {ou['under']}\n")
                f.write("\n")

        print(f"💾 Saved to data/twentytwobet_odds.json")
        print(f"📄 Full list: data/twentytwobet_matches.txt")
    else:
        print("\n⚠️ No matches found")

    return matches


if __name__ == "__main__":
    run()