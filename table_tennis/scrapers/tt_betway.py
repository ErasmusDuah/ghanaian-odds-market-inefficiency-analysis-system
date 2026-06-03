import asyncio
from playwright.async_api import async_playwright
import sys
import json
import time as _time
from datetime import datetime, timedelta
import aiohttp
import os

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

BETWAY_UPCOMING_URL = (
    'https://www.betway.com.gh/sportsapi/br/v1/BetBook/Upcoming/'
    '?countryCode=GH&sportId=table-tennis'
    '&Skip={skip}&Take=100&cultureCode=en-US'
    '&isEsport=false&boostedOnly=false'
    '&marketTypes=%5BMatch%20Winner%5D'
)

BETWAY_HOME_URL = 'https://www.betway.com.gh/sport/tennis'


async def get_cookies():
    """Gets session cookies from Betway via browser"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                      'AppleWebKit/537.36 (KHTML, like Gecko) '
                      'Chrome/120.0.0.0 Safari/537.36'
        )
        page = await context.new_page()
        try:
            await page.goto(BETWAY_HOME_URL, timeout=30000,
                           wait_until='domcontentloaded')
            await page.wait_for_timeout(3000)
        except Exception:
            pass
        cookies = await context.cookies()
        await browser.close()
        return {c['name']: c['value'] for c in cookies}


async def fetch_page(session, skip, headers):
    """Fetches a page of upcoming matches"""
    base_url = BETWAY_UPCOMING_URL.format(skip=skip)
    url = f"{base_url}&_t={int(_time.time() * 1000)}"
    try:
        async with session.get(
                url, headers=headers,
                timeout=aiohttp.ClientTimeout(total=15)
        ) as response:
            if response.status == 200:
                return await response.json()
    except Exception as e:
        print(f"  ❌ Error: {e}")
    return None


def match_outcome_to_team(outcome_name, home_team, away_team):
    """Safely matches the outcome name to either the home team or away team."""
    def clean(s):
        return "".join(c for c in s.lower() if c.isalnum())
    
    oc = clean(outcome_name)
    hc = clean(home_team)
    ac = clean(away_team)
    
    if not oc:
        return None
    
    # Try exact matches first
    if oc == hc:
        return 'home'
    if oc == ac:
        return 'away'
    
    # Try substring matches
    if hc in oc or oc in hc:
        return 'home'
    if ac in oc or oc in ac:
        return 'away'
        
    # Split by spaces and try to match long name parts (like last names)
    h_parts = [p for p in hc.split() if len(p) > 2]
    a_parts = [p for p in ac.split() if len(p) > 2]
    
    for hp in h_parts:
        if hp in oc:
            return 'home'
    for ap in a_parts:
        if ap in oc:
            return 'away'
            
    return None


async def scrape_betway():
    start = _time.time()
    print("\n" + "* " * 20)
    print("   BETWAY GHANA TABLE TENNIS SCRAPER")
    print(f"   {datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}")
    print("* " * 20 + "\n")

    print("[INFO] Getting Betway session...")
    cookies = await get_cookies()

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                     'AppleWebKit/537.36 (KHTML, like Gecko) '
                     'Chrome/120.0.0.0 Safari/537.36',
        'Referer': BETWAY_HOME_URL,
        'Accept': 'application/json',
        'Cache-Control': 'no-cache, no-store, must-revalidate',
        'Pragma': 'no-cache',
    }

    all_events = []
    all_markets = []
    all_outcomes = []
    all_prices = []

    now = datetime.now()
    today = now.date()

    async with aiohttp.ClientSession(cookies=cookies) as session:
        print("[INFO] Fetching matches...")
        skip = 0

        while True:
            data = await fetch_page(session, skip, headers)
            if not data:
                break

            events = data.get('events', [])
            markets = data.get('markets', [])
            outcomes = data.get('outcomes', [])
            prices = data.get('prices', [])

            if not events:
                break

            # Filter to today only
            valid_events = []
            stop = False
            for event in events:
                epoch = event.get('expectedStartEpoch', 0)
                kickoff_dt = datetime.fromtimestamp(epoch)
                
                # Check date boundary (must be today)
                if kickoff_dt.date() == today:
                    valid_events.append(event)
                elif kickoff_dt.date() > today:
                    stop = True
                    break

            all_events.extend(valid_events)
            all_markets.extend(markets)
            all_outcomes.extend(outcomes)
            all_prices.extend(prices)

            print(f"  [OK] Skip {skip}: {len(valid_events)} matches (Total: {len(all_events)})")

            if stop or len(valid_events) < len(events):
                break

            skip += 100

    print(f"\n[INFO] Total events fetched: {len(all_events)}")

    raw_data = {
        'events': all_events,
        'markets': all_markets,
        'outcomes': all_outcomes,
        'prices': all_prices
    }

    return parse_betway_data(raw_data, today)


def parse_betway_data(raw_data, today):
    events = raw_data.get('events', [])
    markets = raw_data.get('markets', [])
    outcomes = raw_data.get('outcomes', [])
    prices = raw_data.get('prices', [])

    price_map = {
        p.get('outcomeId'): p.get('priceDecimal', 0)
        for p in prices
        if p.get('priceDecimal', 0) > 1.0
        and not p.get('isSuspended', False)
    }

    market_map = {}
    for market in markets:
        event_id = market.get('eventId')
        if event_id:
            if event_id not in market_map:
                market_map[event_id] = []
            market_map[event_id].append(market)

    outcomes_by_market = {}
    for outcome in outcomes:
        market_id = outcome.get('marketId')
        if market_id:
            if market_id not in outcomes_by_market:
                outcomes_by_market[market_id] = []
            outcomes_by_market[market_id].append(outcome)

    today_matches = []
    now = datetime.now()

    for event in events:
        event_id = event.get('eventId')
        home_team = event.get('homeTeam', '')
        away_team = event.get('awayTeam', '')
        league = event.get('league', '')
        kickoff_epoch = event.get('expectedStartEpoch', 0)
        is_live = event.get('isLive', False)

        if not home_team or not away_team:
            continue

        # Skip live and suspended events
        if is_live:
            continue
        if event.get('isActive') is False:
            continue
        if event.get('isSuspended', False):
            continue

        # Skip esports/virtual matches
        esports_keywords = [
            'eadriatic', 'gt league', 'esport',
            'virtual', 'cyber', 'esoccer', 'e-soccer', 'simulated'
        ]
        if any(k in league.lower() for k in esports_keywords):
            continue

        kickoff_dt = datetime.fromtimestamp(kickoff_epoch)
        
        # Skip past/started games
        if kickoff_dt <= now:
            continue
            
        # Only double check that it is indeed today
        if kickoff_dt.date() != today:
            continue

        kickoff = kickoff_dt.strftime('%Y-%m-%d %H:%M')

        match = {
            'home_team': home_team,
            'away_team': away_team,
            'kickoff': kickoff,
            'tournament': league,
            'is_live': is_live,
            'status': 'prematch',
            'event_id': str(event_id),
            'source': 'betway_gh',
            'odds_2way': {}
        }

        event_markets = market_map.get(event_id, [])

        for market in event_markets:
            market_name = market.get('name', '').lower()
            market_id = market.get('marketId', '')

            # Skip suspended/inactive markets
            if market.get('isSuspended', False):
                continue

            market_outcomes = outcomes_by_market.get(market_id, [])
            if not market_outcomes:
                continue
            
            # Filter active outcomes
            active_market_outcomes = [
                o for o in market_outcomes
                if not o.get('isSuspended', False)
                and o.get('isActive', True)
            ]
            
            if not active_market_outcomes:
                continue

            # Match Winner (2-Way)
            if '[match winner]' in market_name or market_name == 'match winner':
                home_odds = 0.0
                away_odds = 0.0
                for o in active_market_outcomes:
                    o_name = o.get('name', '')
                    o_id = o.get('outcomeId')
                    price = price_map.get(o_id, 0.0)
                    if price <= 1.01:
                        continue
                    
                    side = match_outcome_to_team(o_name, home_team, away_team)
                    if side == 'home':
                        home_odds = price
                    elif side == 'away':
                        away_odds = price
                
                if home_odds > 1.01 and away_odds > 1.01:
                    match['odds_2way'] = {'home': home_odds, 'away': away_odds}

        # Keep the match only if Winner odds are resolved
        if match['odds_2way']:
            today_matches.append(match)

    today_matches.sort(key=lambda x: x['kickoff'])
    return today_matches


def display_matches(matches):
    if not matches:
        print("WARNING: No matches found!")
        return

    print("\nBETWAY GHANA TABLE TENNIS")
    print(f"Total matches: {len(matches)}")
    print("=" * 50)

    print("\nSample (first 10 matches):")
    for match in matches[:10]:
        o = match['odds_2way']
        print(f"  {match['home_team']} vs {match['away_team']} | {match['kickoff']} | H {o['home']} - A {o['away']}")

    if len(matches) > 10:
        print(f"\n  ... and {len(matches) - 10} more matches")
    print("=" * 50)


def run():
    start = _time.time()
    matches = asyncio.run(scrape_betway())

    # Establish independent directories
    output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
    os.makedirs(output_dir, exist_ok=True)
    
    json_path = os.path.join(output_dir, 'betway_tt_odds.json')
    txt_path  = os.path.join(output_dir, 'betway_tt_matches.txt')

    if matches:
        display_matches(matches)

        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(matches, f, indent=2, ensure_ascii=False)

        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write(f"BETWAY GHANA TABLE TENNIS - ALL MATCHES\n")
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
        print("⚠️ No matches found")
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump([], f)

    return matches


if __name__ == "__main__":
    run()
