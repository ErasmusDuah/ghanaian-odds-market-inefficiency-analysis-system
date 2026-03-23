import asyncio
from playwright.async_api import async_playwright
import json
from datetime import datetime


BETWAY_URL = 'https://www.betway.com.gh/sport/soccer/'


async def scrape_betway():
    print("\n" + "🔵 " * 20)
    print("   BETWAY GHANA SCRAPER")
    print(f"   {datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}")
    print("🔵 " * 20 + "\n")

    raw_data = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                      'AppleWebKit/537.36 (KHTML, like Gecko) '
                      'Chrome/120.0.0.0 Safari/537.36'
        )
        page = await context.new_page()

        async def handle_response(response):
            if 'BetBook/Highlights' in response.url:
                try:
                    data = await response.json()
                    raw_data['events'] = data.get('events', [])
                    raw_data['markets'] = data.get('markets', [])
                    raw_data['outcomes'] = data.get('outcomes', [])
                    raw_data['prices'] = data.get('prices', [])
                    print(f"  ✅ Captured Betway data!")
                    print(f"     Events: {len(raw_data['events'])}")
                    print(f"     Markets: {len(raw_data['markets'])}")
                    print(f"     Prices: {len(raw_data['prices'])}")
                except Exception as e:
                    print(f"  ❌ Error: {e}")

        page.on('response', handle_response)

        print("🌐 Loading Betway Ghana...")
        try:
            await page.goto(
                BETWAY_URL,
                timeout=60000,
                wait_until='domcontentloaded'
            )
        except Exception as e:
            print(f"⚠️ {str(e)[:60]}")

        await page.wait_for_timeout(15000)
        await browser.close()

    if not raw_data:
        print("❌ No data captured!")
        return []

    return parse_betway_data(raw_data)


def parse_betway_data(raw_data):

    events = raw_data.get('events', [])
    markets = raw_data.get('markets', [])
    outcomes = raw_data.get('outcomes', [])
    prices = raw_data.get('prices', [])

    price_map = {
        p.get('outcomeId'): p.get('priceDecimal', 0)
        for p in prices
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

    matches = []
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

        kickoff_dt = datetime.fromtimestamp(kickoff_epoch)
        hours_away = (kickoff_dt - now).total_seconds() / 3600

        if hours_away < -2 or hours_away > 48:
            continue

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
            is_suspended = market.get('isSuspended', True)

            # Allow suspended markets close to kickoff
            # as odds still valid
            pass

            market_outcomes = outcomes_by_market.get(market_id, [])

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

            # Over/Under 2.5
            if '[total goals]' in market_name:
                over_out = next(
                    (o for o in market_outcomes
                     if 'over' in o.get('name', '').lower() and
                     '2.5' in o.get('name', '')), None)
                under_out = next(
                    (o for o in market_outcomes
                     if 'under' in o.get('name', '').lower() and
                     '2.5' in o.get('name', '')), None)

                if over_out and under_out:
                    match['odds_ou'] = {
                        'line': 2.5,
                        'over': price_map.get(
                            over_out.get('outcomeId'), 0),
                        'under': price_map.get(
                            under_out.get('outcomeId'), 0)
                    }

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
            matches.append(match)

    return matches


def display_matches(matches):
    if not matches:
        print("⚠️ No matches found!")
        return

    print(f"\n📋 BETWAY GHANA - NEXT 48 HOURS")
    print(f"⚽ Total matches: {len(matches)}")
    print("=" * 50)

    for match in matches:
        live_tag = "🔴 LIVE" if match.get('is_live') else ""
        print(f"\n⚽ {match['home_team']} vs "
              f"{match['away_team']} {live_tag}")
        print(f"🏆 {match['tournament']}")
        print(f"🕐 {match['kickoff']}")

        if match['odds_1x2']:
            o = match['odds_1x2']
            print(f"1X2: {o['home']} | {o['draw']} | {o['away']}")
        else:
            print("1X2: No odds")

        if match['odds_ou']:
            ou = match['odds_ou']
            print(f"O/U 2.5: Over {ou['over']} | "
                  f"Under {ou['under']}")
        if match['odds_gg']:
            gg = match['odds_gg']
            print(f"GG/NG: Yes {gg['yes']} | No {gg['no']}")


def run():
    matches = asyncio.run(scrape_betway())

    if matches:
        display_matches(matches)
        with open('data/betway_odds.json', 'w') as f:
            json.dump(matches, f, indent=2)
        print(f"\n💾 Saved to data/betway_odds.json")
    else:
        print("⚠️ No matches found")

    return matches


if __name__ == "__main__":
    run()