import os
import asyncio
from playwright.async_api import async_playwright
import sys
sys.stdout.reconfigure(encoding='utf-8')
import json
import time as _time
from datetime import datetime, timedelta
import aiohttp


BETWAY_UPCOMING_URL = (
    'https://www.betway.com.gh/sportsapi/br/v1/BetBook/Upcoming/'
    '?countryCode=GH&sportId=soccer'
    '&Skip={skip}&Take=100&cultureCode=en-US'
    '&isEsport=false&boostedOnly=false'
    '&marketTypes=%5BWin%2FDraw%2FWin%5D'
    '&marketTypes=%5BBoth%20Teams%20To%20Score%5D'
    '&marketTypes=%5BTotal%20Goals%5D'
)

BETWAY_HOME_URL = 'https://www.betway.com.gh/sport/soccer/'


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


async def scrape_betway():
    start = _time.time()
    print("\n" + "* " * 20)
    print("   BETWAY GHANA SCRAPER")
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
    tomorrow = (now + timedelta(days=1)).date()

    async with aiohttp.ClientSession(cookies=cookies) as session:

        print("📅 Fetching today's matches...")
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

            # Filter to today/tomorrow only
            valid_events = []
            stop = False
            for event in events:
                epoch = event.get('expectedStartEpoch', 0)
                kickoff_dt = datetime.fromtimestamp(epoch)
                if kickoff_dt.date() == today:
                    valid_events.append(event)
                elif kickoff_dt.date() > today:
                    stop = True
                    break

            all_events.extend(valid_events)
            all_markets.extend(markets)
            all_outcomes.extend(outcomes)
            all_prices.extend(prices)

            print(f"  ✅ Skip {skip}: {len(valid_events)} matches "
                  f"(Total: {len(all_events)})")

            if stop or len(valid_events) < len(events):
                break

            skip += 100

    print(f"\n✅ Total events fetched: {len(all_events)}")

    raw_data = {
        'events': all_events,
        'markets': all_markets,
        'outcomes': all_outcomes,
        'prices': all_prices
    }

    return parse_betway_data(raw_data, today, tomorrow)


def parse_betway_data(raw_data, today, tomorrow):

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
    tomorrow_matches = []

    for event in events:
        event_id = event.get('eventId')
        home_team = event.get('homeTeam', '')
        away_team = event.get('awayTeam', '')
        league = event.get('league', '')
        kickoff_epoch = event.get('expectedStartEpoch', 0)
        is_live = event.get('isLive', False)

        if not home_team or not away_team:
            continue

        # CRITICAL: Skip events that are locked/padlocked (isActive=False)
        # The padlock on the website means the entire event is deactivated
        if event.get('isActive') is False:
            continue
        if event.get('isSuspended', False):
            continue

        # Skip esports/virtual matches
        esports_keywords = [
            'eadriatic', 'gt league', 'esport',
            'virtual', 'cyber', 'esoccer', 'e-soccer'
        ]
        if any(k in league.lower() for k in esports_keywords):
            continue

        kickoff_dt = datetime.fromtimestamp(kickoff_epoch)
        kickoff = kickoff_dt.strftime('%Y-%m-%d %H:%M')

        match = {
            'home_team': home_team,
            'away_team': away_team,
            'kickoff': kickoff,
            'tournament': league,
            'is_live': is_live,
            'source': 'betway_gh',
            'odds_1x2': {},
            'odds_ou': {},
            'odds_gg': {}
        }

        event_markets = market_map.get(event_id, [])

        for market in event_markets:
            market_name = market.get('name', '').lower()
            market_id = market.get('marketId', '')

            # Skip suspended/inactive markets
            if market.get('isSuspended', False):
                continue

            market_outcomes = outcomes_by_market.get(
                market_id, [])

            if not market_outcomes:
                continue
            
            # Filter out suspended/inactive outcomes
            market_outcomes = [
                o for o in market_outcomes
                if not o.get('isSuspended', False)
                and o.get('isActive', True)
            ]
            
            if not market_outcomes:
                continue

            # 1X2
            if '[win/draw/win]' in market_name or \
                    market_name == '1x2':

                sorted_out = sorted(
                    market_outcomes,
                    key=lambda x: x.get('index', 999)
                )

                if len(sorted_out) >= 3:
                    home_price = price_map.get(
                        sorted_out[0].get('outcomeId'), 0)
                    draw_price = price_map.get(
                        sorted_out[1].get('outcomeId'), 0)
                    away_price = price_map.get(
                        sorted_out[2].get('outcomeId'), 0)

                    if home_price > 1 and \
                            draw_price > 1 and \
                            away_price > 1:
                        match['odds_1x2'] = {
                            'home': home_price,
                            'draw': draw_price,
                            'away': away_price
                        }

            # Over/Under dynamically
            if '[total goals]' in market_name or \
                    'total=' in market_id.lower() or \
                    'total' in market_name:
                import re
                for o in market_outcomes:
                    o_id = str(o.get('outcomeId', ''))
                    m = re.search(r'(\d+\.5)', o_id)
                    if m:
                        line_str = m.group(1)
                        if line_str not in match['odds_ou']:
                            match['odds_ou'][line_str] = {}
                        
                        price = price_map.get(o_id, 0)
                        if price > 1.01:
                            if o_id.endswith('12'):
                                match['odds_ou'][line_str]['over'] = price
                            else:
                                match['odds_ou'][line_str]['under'] = price
                
                # Cleanup incomplete lines
                complete_ou = {}
                for l_str, vals in match['odds_ou'].items():
                    if vals.get('over', 0) > 1 and vals.get('under', 0) > 1:
                        complete_ou[l_str] = vals
                match['odds_ou'] = complete_ou

            # BTTS
            if '[both teams to score]' in market_name:
                yes_out = next(
                    (o for o in market_outcomes
                     if 'yes' in o.get('name', '').lower()), None)
                no_out = next(
                    (o for o in market_outcomes
                     if 'no' in o.get('name', '').lower()), None)

                if yes_out and no_out:
                    match['odds_gg'] = {
                        'yes': price_map.get(
                            yes_out.get('outcomeId'), 0),
                        'no': price_map.get(
                            no_out.get('outcomeId'), 0)
                    }

        o = match['odds_1x2']
        if o.get('home', 0) > 1 and \
                o.get('draw', 0) > 1 and \
                o.get('away', 0) > 1:
            if kickoff_dt.date() == today:
                today_matches.append(match)

    if today_matches:
        matches = today_matches
        print(f"  [INFO] Today's matches: {len(matches)}")
    else:
        matches = []
        print("[INFO] No matches available for today.")

    matches.sort(key=lambda x: x['kickoff'])
    return matches


def display_matches(matches):
    if not matches:
        print("WARNING: No matches found!")
        return

    print("\nBETWAY GHANA")
    print(f"Total matches: {len(matches)}")
    print("=" * 50)

    print("\nSample (first 10 matches):")
    for match in matches[:10]:
        live_tag = "[LIVE]" if match.get('is_live') else ""
        print(f"  {live_tag} {match['home_team']} vs {match['away_team']} | {match['kickoff']} | {match['tournament']}")

    if len(matches) > 10:
        print(f"\n  ... and {len(matches) - 10} more matches")
    print("=" * 50)


def run():
    output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
    os.makedirs(output_dir, exist_ok=True)
    import time as _time
    start = _time.time()
    matches = asyncio.run(scrape_betway())

    if matches:
        display_matches(matches)

        with open(os.path.join(output_dir, 'betway_odds.json', 'w') as f:
            json.dump(matches, f, indent=2)

        with open(os.path.join(output_dir, 'betway_matches.txt', 'w',
                  encoding='utf-8') as f:
            f.write(f"BETWAY GHANA - ALL MATCHES\n")
            f.write(f"Generated: "
                    f"{datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}\n")
            f.write(f"Total: {len(matches)} matches\n")
            f.write("=" * 60 + "\n\n")

            for match in matches:
                live_tag = "🔴 LIVE" if match.get(
                    'is_live') else ""
                f.write(f"{match['home_team']} vs {match['away_team']} {live_tag}\n")
                f.write(f"{match['tournament']}\n")
                f.write(f"{match['kickoff']}\n")

                if match['odds_1x2']:
                    o = match['odds_1x2']
                    f.write(f"1X2: {o['home']} | "
                            f"{o['draw']} | {o['away']}\n")
                if match['odds_ou']:
                    for line_str, ou in match['odds_ou'].items():
                        f.write(f"O/U {line_str}: Over {ou['over']} | "
                                f"Under {ou['under']}\n")
                if match['odds_gg']:
                    gg = match['odds_gg']
                    f.write(f"GG/NG: Yes {gg['yes']} | "
                            f"No {gg['no']}\n")
                f.write("\n")

        print(f"Saved to {os.path.join(output_dir, 'betway_odds.json')}")
        print(f"Full list saved to {os.path.join(output_dir, 'betway_matches.txt')}")
        print(f"   Open the .txt file to see all {len(matches)} matches!")
        print(f"⏱️  Scraping completed in {_time.time() - start:.1f}s")
    else:
        print("⚠️ No matches found")

    return matches


if __name__ == "__main__":
    run()