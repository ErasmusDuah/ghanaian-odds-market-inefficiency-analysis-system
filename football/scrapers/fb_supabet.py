import os
import asyncio
import sys
import json
import time as _time
from datetime import datetime, timedelta
from playwright.async_api import async_playwright
import re

sys.stdout.reconfigure(encoding='utf-8')

SOURCE = 'supabet_gh'
VIRTUAL_KEYWORDS = ['srl', 'simulated', 'esport', 'e-soccer', 'esoccer',
                    'cyber', 'virtual', 'efootball', 'e-football']

def is_virtual(home, away):
    text = f'{home} {away}'.lower()
    return any(kw in text for kw in VIRTUAL_KEYWORDS)

async def scrape():
    start = _time.time()
    print("\n" + "🟡 " * 20)
    print("   SUPABET GHANA SCRAPER")
    print(f"   {datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}")
    print("🟡 " * 20 + "\n")

    all_matches = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        # Add headers to bypass simple bot checks
        ctx = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        page = await ctx.new_page()
        
        # Load the raw Boltbet iframe directly
        iframe_url = 'https://eu01.sportsbook.adv.bet/?orgUuid=17e5b1b7-bb38-d332-4e2f-f1fde542b6b9#/match/football'
        try:
            print(f"  [Supabet] Navigating to sportsbook...")
            await page.goto(iframe_url, wait_until='networkidle', timeout=30000)
            await page.wait_for_timeout(5000)
        except Exception as e:
            print(f"  [Supabet] Navigation error: {e}")
            await browser.close()
            return []

        print(f"  [Supabet] Scrolling to load matches...")
        for i in range(10): # Scroll up to 10 times to load more matches
            await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
            await page.wait_for_timeout(1000)

        # Query all match anchor tags
        matches = await page.query_selector_all('a[href*="#/match/football/"]')
        print(f"  [Supabet] Found {len(matches)} potential match cards")

        for m in matches:
            try:
                teams_spans = await m.query_selector_all('.event-info__teams span')
                if len(teams_spans) < 2: continue
                
                home = (await teams_spans[0].inner_text()).strip()
                away = (await teams_spans[1].inner_text()).strip()
                
                if not home or not away or is_virtual(home, away): continue

                # Get tournament/date from the UI if possible
                time_el = await m.query_selector('.event-info__time')
                time_text = (await time_el.inner_text()).strip() if time_el else ""
                
                # Boltbet formats time like '11/05 19:00' or 'Live'
                is_live = 'live' in time_text.lower() or "'" in time_text

                # Generate a dummy kickoff based on time text, or fallback to today
                dt_str = datetime.now().strftime('%Y-%m-%d %H:%M')
                if not is_live and ":" in time_text:
                    try:
                        # Extract 19:00
                        t = re.search(r'(\d{2}:\d{2})', time_text)
                        if t:
                            dt_str = datetime.now().strftime(f'%Y-%m-%d {t.group(1)}')
                    except:
                        pass

                odds_btns = await m.query_selector_all('.bid-option')
                if len(odds_btns) < 3: continue

                try:
                    h_odd = float((await odds_btns[0].inner_text()).strip() or 0)
                    d_odd = float((await odds_btns[1].inner_text()).strip() or 0)
                    a_odd = float((await odds_btns[2].inner_text()).strip() or 0)
                except ValueError:
                    continue

                if h_odd <= 1.01 or d_odd <= 1.01 or a_odd <= 1.01: continue

                match_obj = {
                    'home_team': home,
                    'away_team': away,
                    'kickoff': dt_str,
                    'tournament': 'Football', # Boltbet UI doesn't clearly show tournament on card
                    'is_live': is_live,
                    'source': SOURCE,
                    'odds_1x2': {'home': h_odd, 'draw': d_odd, 'away': a_odd},
                    'odds_ou': {},
                    'odds_gg': {}
                }
                all_matches.append(match_obj)
            except Exception as e:
                continue

        await browser.close()
    
    # Remove duplicates based on home + away names
    unique_matches = []
    seen = set()
    for m in all_matches:
        key = f"{m['home_team']}_{m['away_team']}"
        if key not in seen:
            seen.add(key)
            unique_matches.append(m)

    unique_matches.sort(key=lambda x: x['kickoff'])
    return unique_matches

def display_matches(matches):
    if not matches:
        print("⚠️ No matches found")
        return

    with_odds = [m for m in matches if m.get('odds_1x2')]
    print(f"\n📋 SUPABET GHANA")
    print(f"⚽ Total matches fetched: {len(matches)}")
    print(f"📊 With 1X2 odds: {len(with_odds)}")
    print("=" * 50)

    print("\n📝 Sample (first 10 matches):")
    for match in with_odds[:10]:
        live_tag = "🔴 LIVE" if match.get('is_live') else ""
        print(f"  {live_tag} {match['home_team']} vs {match['away_team']} | {match['kickoff']} | {match['tournament']}")

    if len(with_odds) > 10:
        print(f"\n  ... and {len(with_odds) - 10} more matches")
    print("=" * 50)

def run():
    output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
    os.makedirs(output_dir, exist_ok=True)
    start = _time.time()
    matches = asyncio.run(scrape())

    if matches:
        display_matches(matches)

        with open(os.path.join(output_dir, 'supabet_odds.json', 'w') as f:
            json.dump(matches, f, indent=2)

        with open(os.path.join(output_dir, 'supabet_matches.txt'), 'w', encoding='utf-8') as f:
            f.write('SUPABET GHANA - ALL MATCHES\n')
            f.write(f'Generated: {datetime.now().strftime("%A, %d %B %Y %H:%M:%S")}\n')
            f.write(f'Total: {len(matches)} matches\n')
            f.write('=' * 60 + '\n\n')
            for m in matches:
                f.write(f"{m['home_team']} vs {m['away_team']}\n")
                f.write(f"{m['tournament']}\n")
                f.write(f"{m['kickoff']}\n")
                if m['odds_1x2']:
                    o = m['odds_1x2']
                    f.write(f"1X2: {o['home']} | {o['draw']} | {o['away']}\n")
                f.write('\n')

        print(f"Saved to {os.path.join(output_dir, 'supabet_odds.json')}")
        print(f"Full list saved to {os.path.join(output_dir, 'supabet_matches.txt')}")
        print(f"   Open the .txt file to see all {len(matches)} matches!")
        print(f"⏱️  Scraping completed in {_time.time() - start:.1f}s")
    else:
        print('⚠️ No matches found')

    return matches

if __name__ == '__main__':
    run()