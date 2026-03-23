import asyncio
from playwright.async_api import async_playwright
import json
from datetime import datetime


async def scrape_all_matches():
    print("\n" + "🟢 " * 20)
    print("   SPORTYBET GHANA SCRAPER")
    print(f"   {datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}")
    print("🟢 " * 20 + "\n")

    all_matches = []
    captured_pages = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                      'AppleWebKit/537.36 (KHTML, like Gecko) '
                      'Chrome/120.0.0.0 Safari/537.36',
            viewport={'width': 1920, 'height': 1080}
        )
        page = await context.new_page()

        async def handle_response(response):
            if 'pcUpcomingEvents' in response.url:
                try:
                    url = response.url
                    page_num = 1
                    if 'pageNum=' in url:
                        page_num = int(
                            url.split('pageNum=')[1].split('&')[0])
                    if page_num not in captured_pages:
                        data = await response.json()
                        captured_pages[page_num] = True
                        matches = parse_response(data)
                        all_matches.extend(matches)
                        print(f"  ✅ Page {page_num}: "
                              f"{len(matches)} matches in next 48hrs "
                              f"(Total: {len(all_matches)})")
                except Exception:
                    pass

        page.on('response', handle_response)

        print("🌐 Loading Sportybet Ghana...")
        await page.goto(
            'https://www.sportybet.com/gh/sport/football',
            timeout=30000,
            wait_until='domcontentloaded'
        )
        await page.wait_for_timeout(5000)

        await browser.close()

    print(f"\n✅ Total matches in next 48hrs: {len(all_matches)}")
    return all_matches


def parse_response(data):
    matches = []
    if not isinstance(data, dict):
        return matches
    tournaments = data.get('data', {}).get('tournaments', [])
    for tournament in tournaments:
        tournament_name = tournament.get('name', '')
        events = tournament.get('events', [])
        for event in events:
            try:
                match = parse_event(event, tournament_name)
                if match:
                    matches.append(match)
            except Exception:
                continue
    return matches


def parse_event(event, tournament_name=''):
    home_team = event.get('homeTeamName', '')
    away_team = event.get('awayTeamName', '')
    kickoff = event.get('estimateStartTime', '')

    if not home_team or not away_team:
        return None

    # Only include matches in next 48 hours
    if isinstance(kickoff, int):
        kickoff_dt = datetime.fromtimestamp(kickoff / 1000)
        now = datetime.now()
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
        'source': 'sportybet_gh',
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

    print(f"\n📋 SPORTYBET GHANA - NEXT 48 HOURS")
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
    matches = asyncio.run(scrape_all_matches())
    if matches:
        display_matches(matches)
        with open('data/sportybet_odds.json', 'w') as f:
            json.dump(matches, f, indent=2)
        print(f"\n💾 Saved to data/sportybet_odds.json")
    else:
        print("\n⚠️ No matches found in next 48 hours")
        print("💡 Check back closer to match days!")
    return matches


if __name__ == "__main__":
    run()