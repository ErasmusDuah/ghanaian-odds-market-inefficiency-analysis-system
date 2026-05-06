import asyncio
import sys
sys.stdout.reconfigure(encoding='utf-8')
import json
import time as _time
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
    base_url = TOMORROW_URL.format(page=page_num) \
        if use_tomorrow \
        else TODAY_URL.format(page=page_num)
    url = f"{base_url}&_t={int(_time.time() * 1000)}"
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
    start = _time.time()
    print("\n" + "* " * 20)
    print("   SPORTYBET GHANA SCRAPER")
    print(f"   {datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}")
    print("* " * 20 + "\n")

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                     'AppleWebKit/537.36 (KHTML, like Gecko) '
                     'Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://www.sportybet.com/gh/sport/football',
        'Accept': 'application/json',
        'Cache-Control': 'no-cache, no-store, must-revalidate',
        'Pragma': 'no-cache',
    }

    all_matches = []
    use_tomorrow = False

    async with aiohttp.ClientSession() as session:

        # Try today first
        print("[INFO] Fetching today's matches...")
        data = await fetch_page_direct(
            session, 1, False, headers)

        total = 0

        if data:
            total = data.get('data', {}).get('totalSize', 0)
            matches = parse_response(data)
            all_matches.extend(matches)
            print(f"  [OK] Page 1: {len(matches)} matches "
                  f"(Total available: {total})")

        # If no today matches, do not fallback to tomorrow
        if len(all_matches) == 0:
            print("[INFO] No matches available for today.")

        # Keep fetching pages until empty
        print(f"\n[INFO] Fetching more pages...")
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
            print(f"  [OK] Page {page_num}: "
                  f"{len(matches)} matches "
                  f"(Total: {len(all_matches)})")
            page_num += 1
            await asyncio.sleep(0.3)

        # Keep fetching pages until empty
        print(f"\n[INFO] Fetching more pages...")
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
            print(f"  [OK] Page {page_num}: "
                  f"{len(matches)} matches "
                  f"(Total: {len(all_matches)})")
            page_num += 1
            await asyncio.sleep(0.3)

    # Sort by kickoff time
    all_matches.sort(key=lambda x: x['kickoff'])

    print(f"\n[INFO] Total matches fetched: {len(all_matches)}")
    return all_matches


def parse_response(data):
    matches = []
    if not isinstance(data, dict):
        return matches
    tournaments = data.get('data', {}).get('tournaments', [])
    now = datetime.now()

    for tournament in tournaments:
        tournament_name = tournament.get('name', '')
        category_name   = tournament.get('categoryName', '')
        # Build "Country. Tournament" label (e.g. "Jamaica. Premier League")
        if category_name and not tournament_name.lower().startswith(category_name.lower()):
            full_tournament = f"{category_name}. {tournament_name}"
        else:
            full_tournament = tournament_name
        events = tournament.get('events', [])
        for event in events:
            try:
                match = parse_event(event, full_tournament, now)
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

    is_live = status in ['Living', 'living', 'live',
                         'Live', 'LIVE', 'inprogress',
                         'InProgress', 'in_progress']

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
        market_status = market.get('status', '')
        
        # Skip suspended/deactivated/closed markets
        if market_status in ['suspended', 'deactivated', 'closed',
                              'Suspended', 'Deactivated', 'Closed']:
            continue
        
        outcomes = market.get('outcomes', [])
        
        # Filter out suspended/inactive outcomes
        active_outcomes = [
            o for o in outcomes
            if o.get('isActive', 1) != 0
            and str(o.get('odds', 0) or 0) not in ('0', '', 'None')
        ]

        if market_id == '1' and len(active_outcomes) >= 3:
            h = float(active_outcomes[0].get('odds', 0) or 0)
            d = float(active_outcomes[1].get('odds', 0) or 0)
            a = float(active_outcomes[2].get('odds', 0) or 0)
            if h > 1.01 and d > 1.01 and a > 1.01:
                match['odds_1x2'] = {'home': h, 'draw': d, 'away': a}

        if market_id == '18' and len(active_outcomes) >= 2:
            desc = active_outcomes[0].get('desc', '')
            import re
            m = re.search(r'(\d+\.5)', str(desc))
            if m:
                line_str = m.group(1)
                ov = float(active_outcomes[0].get('odds', 0) or 0)
                un = float(active_outcomes[1].get('odds', 0) or 0)
                if ov > 1.01 and un > 1.01:
                    match['odds_ou'][line_str] = {'over': ov, 'under': un}

        if market_id == '29' and len(active_outcomes) >= 2:
            y = float(active_outcomes[0].get('odds', 0) or 0)
            n = float(active_outcomes[1].get('odds', 0) or 0)
            if y > 1.01 and n > 1.01:
                match['odds_gg'] = {'yes': y, 'no': n}

    return match


def display_matches(matches):
    if not matches:
        print("WARNING: No matches found")
        return

    with_odds = [m for m in matches if m['odds_1x2']]
    print("\nSPORTYBET GHANA")
    print(f"Total matches fetched: {len(matches)}")
    print(f"With 1X2 odds: {len(with_odds)}")
    print("=" * 50)

    # Show first 10 as sample
    print("\nSample (first 10 matches):")
    for match in with_odds[:10]:
        live_tag = "[LIVE]" if match.get('is_live') else ""
        print(f"  {live_tag} {match['home_team']} vs {match['away_team']} | {match['kickoff']} | {match['tournament']}")
    if len(with_odds) > 10:
        print(f"\n  ... and {len(with_odds) - 10} more matches")
    print("=" * 50)

def run():
    import time as _time
    start = _time.time()
    matches = asyncio.run(scrape_all_matches())
    if matches:
        display_matches(matches)

        # Save JSON
        with open('data/sportybet_odds.json', 'w') as f:
            json.dump(matches, f, indent=2)

        # Save readable text file
        with open('data/sportybet_matches.txt', 'w',
                  encoding='utf-8') as f:
            f.write(f"SPORTYBET GHANA - ALL MATCHES\n")
            f.write(f"Generated: "
                    f"{datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}\n")
            f.write(f"Total: {len(matches)} matches\n")
            f.write("=" * 60 + "\n\n")

            for match in matches:
                live_tag = "LIVE" if match.get('is_live') else ""
                f.write(f"{match['home_team']} vs {match['away_team']} {live_tag}\n")
                f.write(f"{match['tournament']}\n")
                f.write(f"{match['kickoff']}\n")

                if match['odds_1x2']:
                    o = match['odds_1x2']
                    f.write(f"1X2: {o['home']} | {o['draw']} | {o['away']}\n")
                if match['odds_ou']:
                    for line_str, ou in match['odds_ou'].items():
                        f.write(f"O/U {line_str}: Over {ou['over']} | Under {ou['under']}\n")
                if match['odds_gg']:
                    gg = match['odds_gg']
                    f.write(f"GG/NG: Yes {gg['yes']} | "
                            f"No {gg['no']}\n")
                f.write("\n")

        print(f"Saved to data/sportybet_odds.json")
        print(f"Full list saved to data/sportybet_matches.txt")
        print(f"   Open the .txt file to see all {len(matches)} matches!")
        print(f"⏱️  Scraping completed in {_time.time() - start:.1f}s")
    else:
        print("\n⚠️ No matches found")
    return matches


if __name__ == "__main__":
    run()