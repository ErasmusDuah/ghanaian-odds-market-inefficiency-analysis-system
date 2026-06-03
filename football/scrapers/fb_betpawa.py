import os
import asyncio
import sys
import json
import time as _time
from datetime import datetime
from playwright.async_api import async_playwright
import re

sys.stdout.reconfigure(encoding='utf-8')

SOURCE = 'betpawa_gh'
VIRTUAL_KEYWORDS = ['srl', 'simulated', 'esport', 'e-soccer', 'esoccer',
                    'cyber', 'virtual', 'efootball', 'e-football']

def is_virtual(home, away, tournament):
    text = f'{home} {away} {tournament}'.lower()
    return any(kw in text for kw in VIRTUAL_KEYWORDS)

async def scrape():
    start = _time.time()
    print("\n" + "🟡 " * 20)
    print("   BETPAWA GHANA SCRAPER")
    print(f"   {datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}")
    print("🟡 " * 20 + "\n")

    all_matches = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        page = await ctx.new_page()
        
        try:
            print(f"  [BetPawa] Navigating to sportsbook...")
            await page.goto('https://www.betpawa.com.gh/events?categoryId=2', wait_until='networkidle', timeout=30000)
            await page.wait_for_timeout(5000)
        except Exception as e:
            print(f"  [BetPawa] Navigation error: {e}")
            await browser.close()
            return []

        print(f"  [BetPawa] Scrolling to load matches...")
        for i in range(10): # Scroll to load more matches
            await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
            await page.wait_for_timeout(1000)

        matches = await page.query_selector_all('a[href*="/event/"]')
        print(f"  [BetPawa] Found {len(matches)} potential match cards")

        for m in matches:
            try:
                text = await m.inner_text()
                lines = [x.strip() for x in text.split('\n') if x.strip()]
                
                # We expect at least 10 lines: time, home, away, tournament, 1, h_odd, X, d_odd, 2, a_odd
                if len(lines) < 10:
                    continue

                time_str = lines[0]
                home = lines[1]
                away = lines[2]
                tournament = lines[3]
                
                if is_virtual(home, away, tournament):
                    continue

                # The odds are usually at the end of the block
                # Let's find the '1', 'X', '2' markers
                try:
                    idx_1 = lines.index('1')
                    h_odd = float(lines[idx_1 + 1])
                    
                    idx_x = lines.index('X')
                    d_odd = float(lines[idx_x + 1])
                    
                    idx_2 = lines.index('2')
                    a_odd = float(lines[idx_2 + 1])
                except (ValueError, IndexError):
                    continue

                if h_odd <= 1.01 or d_odd <= 1.01 or a_odd <= 1.01:
                    continue

                is_live = 'live' in time_str.lower() or "'" in time_str
                
                # Parse the date '8:00 pm Mon 11/05' or simply 'Live'
                kickoff = datetime.now().strftime('%Y-%m-%d %H:%M')
                if not is_live:
                    try:
                        # Extract the '11/05' and '8:00 pm' if available
                        t_match = re.search(r'(\d{1,2}:\d{2}\s*(?:am|pm))', time_str.lower())
                        d_match = re.search(r'(\d{1,2}/\d{2})', time_str)
                        if t_match and d_match:
                            year = datetime.now().year
                            raw_dt = f"{year}/{d_match.group(1)} {t_match.group(1)}"
                            parsed_dt = datetime.strptime(raw_dt, '%Y/%d/%m %I:%M %p')
                            kickoff = parsed_dt.strftime('%Y-%m-%d %H:%M')
                    except Exception:
                        pass

                match_obj = {
                    'home_team': home,
                    'away_team': away,
                    'kickoff': kickoff,
                    'tournament': tournament,
                    'is_live': is_live,
                    'source': SOURCE,
                    'odds_1x2': {'home': h_odd, 'draw': d_odd, 'away': a_odd},
                    'odds_ou': {},
                    'odds_gg': {}
                }
                all_matches.append(match_obj)
            except Exception:
                continue

        await browser.close()
    
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
    print(f"\n📋 BETPAWA GHANA")
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

        with open(os.path.join(output_dir, 'betpawa_odds.json', 'w') as f:
            json.dump(matches, f, indent=2)

        with open(os.path.join(output_dir, 'betpawa_matches.txt'), 'w', encoding='utf-8') as f:
            f.write('BETPAWA GHANA - ALL MATCHES\n')
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

        print(f"Saved to {os.path.join(output_dir, 'betpawa_odds.json')}")
        print(f"Full list saved to {os.path.join(output_dir, 'betpawa_matches.txt')}")
        print(f"   Open the .txt file to see all {len(matches)} matches!")
        print(f"⏱️  Scraping completed in {_time.time() - start:.1f}s")
    else:
        print('⚠️ No matches found')

    return matches

if __name__ == '__main__':
    run()