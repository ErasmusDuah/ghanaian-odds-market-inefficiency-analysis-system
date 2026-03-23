import asyncio
from playwright.async_api import async_playwright
import json
from datetime import datetime


FOOTBALLCOM_URL = 'https://www.football.com/gh/sport/football'


async def scrape_footballcom():
    print("\n" + "🟡 " * 20)
    print("   FOOTBALL.COM GHANA SCRAPER")
    print(f"   {datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}")
    print("🟡 " * 20 + "\n")

    captured = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                      'AppleWebKit/537.36 (KHTML, like Gecko) '
                      'Chrome/120.0.0.0 Safari/537.36'
        )
        page = await context.new_page()

        async def handle_response(response):
            if 'wapConfigurableEventsByOrder' in response.url:
                try:
                    data = await response.json()
                    captured['data'] = data
                    tournaments = data.get('data', {}).get(
                        'tournaments', [])
                    total = sum(
                        len(t.get('events', []))
                        for t in tournaments
                    )
                    print(f"  ✅ Captured Football.com data!")
                    print(f"     Tournaments: {len(tournaments)}")
                    print(f"     Events: {total}")
                except Exception as e:
                    print(f"  ❌ Error: {e}")

        page.on('response', handle_response)

        print("🌐 Loading Football.com Ghana...")
        try:
            await page.goto(
                FOOTBALLCOM_URL,
                timeout=60000,
                wait_until='domcontentloaded'
            )
        except Exception as e:
            print(f"⚠️ {str(e)[:60]}")

        await page.wait_for_timeout(8000)
        await browser.close()

    if not captured:
        print("❌ No data captured!")
        return []

    return parse_response(captured['data'])


def parse_response(data):
    matches = []

    if not isinstance(data, dict):
        return matches

    tournaments = data.get('data', {}).get('tournaments', [])
    now = datetime.now()

    for tournament in tournaments:
        tournament_name = tournament.get('name', '')
        events = tournament.get('events', [])

        for event in events:
            try:
                match = parse_event(event, tournament_name, now)
                if match:
                    matches.append(match)
            except Exception:
                continue

    return matches


def parse_event(event, tournament_name, now):
    home_team = event.get('homeTeamName', '')
    away_team = event.get('awayTeamName', '')
    kickoff = event.get('estimateStartTime', '')

    if not home_team or not away_team:
        return None

    if isinstance(kickoff, int):
        kickoff_dt = datetime.fromtimestamp(kickoff / 1000)
        hours_away = (kickoff_dt - now).total_seconds() / 3600
        if hours_away < 0 or hours_away > 48:
            return None
        kickoff = kickoff_dt.strftime('%Y-%m-%d %H:%M')
    else:
        return None

    match = {
        'home_team': home_team,
        'away_team': away_team,
        'kickoff': str(kickoff),
        'tournament': tournament_name,
        'source': 'footballcom_gh',
        'odds_1x2': {},
        'odds_ou': {},
        'odds_gg': {}
    }

    markets = event.get('markets', [])

    for market in markets:
        market_id = str(market.get('id', ''))
        outcomes = market.get('outcomes', [])

        if market_id == '1' and len(outcomes) >= 3:
            match['odds_1x2'] = {
                'home': float(outcomes[0].get('odds', 0) or 0),
                'draw': float(outcomes[1].get('odds', 0) or 0),
                'away': float(outcomes[2].get('odds', 0) or 0)
            }

        if market_id == '18' and len(outcomes) >= 2:
            desc = outcomes[0].get('desc', '')
            if '2.5' in str(desc):
                match['odds_ou'] = {
                    'line': 2.5,
                    'over': float(outcomes[0].get('odds', 0) or 0),
                    'under': float(outcomes[1].get('odds', 0) or 0)
                }

        if market_id == '29' and len(outcomes) >= 2:
            match['odds_gg'] = {
                'yes': float(outcomes[0].get('odds', 0) or 0),
                'no': float(outcomes[1].get('odds', 0) or 0)
            }

    return match


def display_matches(matches):
    if not matches:
        print("⚠️ No matches in next 48 hours")
        return

    with_odds = [m for m in matches if m['odds_1x2']]

    print(f"\n📋 FOOTBALL.COM GHANA - NEXT 48 HOURS")
    print(f"⚽ Total matches: {len(matches)}")
    print(f"📊 With 1X2 odds: {len(with_odds)}")
    print("=" * 50)

    for match in with_odds:
        print(f"\n⚽ {match['home_team']} vs {match['away_team']}")
        print(f"🏆 {match['tournament']}")
        print(f"🕐 {match['kickoff']}")
        o = match['odds_1x2']
        print(f"1X2: {o['home']} | {o['draw']} | {o['away']}")
        if match['odds_ou']:
            ou = match['odds_ou']
            print(f"O/U 2.5: Over {ou['over']} | "
                  f"Under {ou['under']}")
        if match['odds_gg']:
            gg = match['odds_gg']
            print(f"GG/NG: Yes {gg['yes']} | No {gg['no']}")



def run():
    matches = asyncio.run(scrape_footballcom())

    if matches:
        display_matches(matches)
        with open('data/footballcom_odds.json', 'w') as f:
            json.dump(matches, f, indent=2)
        print(f"\n💾 Saved to data/footballcom_odds.json")
    else:
        print("\n⚠️ No matches found in next 48 hours")

    return matches


if __name__ == "__main__":
    run()