import asyncio
import json
from datetime import datetime
import aiohttp


TODAY_URL = (
    'https://www.sportybet.com/api/gh/factsCenter/'
    'pcUpcomingEvents?sportId=sr%3Asport%3A1'
    '&marketId=1%2C18%2C10%2C29%2C11%2C26%2C36%2C14%2C60100'
    '&pageSize=100&pageNum={page}'
    '&todayGames=true&timeline=0.9'
)

TOMORROW_URL = (
    'https://www.sportybet.com/api/gh/factsCenter/'
    'pcUpcomingEvents?sportId=sr%3Asport%3A1'
    '&marketId=1%2C18%2C10%2C29%2C11%2C26%2C36%2C14%2C60100'
    '&pageSize=100&pageNum={page}'
    '&todayGames=false&timeline=1'
)


async def fetch_page_direct(session, page_num,
                            use_tomorrow, headers):
    url = TOMORROW_URL.format(page=page_num) \
        if use_tomorrow \
        else TODAY_URL.format(page=page_num)
    try:
        async with session.get(
                url, headers=headers,
                timeout=aiohttp.ClientTimeout(total=15)
        ) as response:
            if response.status == 200:
                return await response.json()
    except Exception:
        pass
    return None


async def scrape_all_matches():
    print("\n" + "🟢 " * 20)
    print("   SPORTYBET GHANA SCRAPER")
    print(f"   {datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}")
    print("🟢 " * 20 + "\n")

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                     'AppleWebKit/537.36 (KHTML, like Gecko) '
                     'Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://www.sportybet.com/gh/sport/football',
        'Accept': 'application/json',
    }

    all_matches = []
    use_tomorrow = False

    async with aiohttp.ClientSession() as session:

        # Try today first
        print("📅 Fetching today's matches...")
        data = await fetch_page_direct(
            session, 1, False, headers)

        total = 0

        if data:
            total = data.get('data', {}).get('totalSize', 0)
            matches = parse_response(data)
            all_matches.extend(matches)
            print(f"  ✅ Page 1: {len(matches)} matches "
                  f"(Total available: {total})")

        # If no today matches try tomorrow
        if len(all_matches) == 0:
            print("\n📅 No matches today — fetching tomorrow...")
            use_tomorrow = True
            data = await fetch_page_direct(
                session, 1, True, headers)

            if data:
                total = data.get('data', {}).get('totalSize', 0)
                matches = parse_response(data)
                all_matches.extend(matches)
                print(f"  ✅ Page 1: {len(matches)} matches "
                      f"(Total available: {total})")

        # Keep fetching pages until empty
        print(f"\n📄 Fetching more pages...")
        page_num = 2
        while True:
            data = await fetch_page_direct(
                session, page_num, use_tomorrow, headers)
            if not data:
                break
            matches = parse_response(data)
            if not matches:
                break
            all_matches.extend(matches)
            print(f"  ✅ Page {page_num}: "
                  f"{len(matches)} matches "
                  f"(Total: {len(all_matches)})")
            page_num += 1
            await asyncio.sleep(0.3)

    print(f"\n✅ Total matches fetched: {len(all_matches)}")
    return all_matches


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


def parse_event(event, tournament_name='', now=None):
    if now is None:
        now = datetime.now()

    home_team = event.get('homeTeamName', '')
    away_team = event.get('awayTeamName', '')
    kickoff = event.get('estimateStartTime', '')
    status = event.get('matchStatus', '')

    if not home_team or not away_team:
        return None

    # Skip finished matches
    if status in ['ended', 'finished', 'Ended',
                  'Finished', 'cancelled', 'Cancelled']:
        return None

    if isinstance(kickoff, int):
        kickoff_dt = datetime.fromtimestamp(kickoff / 1000)
        kickoff = kickoff_dt.strftime('%Y-%m-%d %H:%M')
    else:
        return None

    is_live = status not in ['Not start', 'not_start',
                             'NotStart', '']

    match = {
        'home_team': home_team,
        'away_team': away_team,
        'kickoff': str(kickoff),
        'tournament': tournament_name,
        'is_live': is_live,
        'status': status,
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
        print("⚠️ No matches found")
        return

    with_odds = [m for m in matches if m['odds_1x2']]
    print(f"\n📋 SPORTYBET GHANA")
    print(f"⚽ Total matches: {len(matches)}")
    print(f"📊 With 1X2 odds: {len(with_odds)}")
    print("=" * 50)

    for match in with_odds:
        live_tag = "🔴 LIVE" if match.get('is_live') else ""
        print(f"\n⚽ {match['home_team']} vs "
              f"{match['away_team']} {live_tag}")
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
        print("\n⚠️ No matches found")
    return matches


if __name__ == "__main__":
    run()