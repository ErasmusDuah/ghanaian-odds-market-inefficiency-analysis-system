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

FOOTBALLCOM_URL = 'https://www.football.com/gh/sport/tabletennis'
API_URL = 'https://www.football.com/api/gh/factsCenter/wapConfigurableEventsByOrder'


def get_today_timestamps():
    """Gets start and end timestamps for today"""
    now = datetime.now()
    start = datetime(now.year, now.month, now.day, 0, 0, 0)
    end = start + timedelta(days=1)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    return start_ms, end_ms


async def get_cookies():
    """Gets session cookies from Football.com via browser"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                      'AppleWebKit/537.36 (KHTML, like Gecko) '
                      'Chrome/120.0.0.0 Safari/537.36'
        )
        page = await context.new_page()
        try:
            await page.goto(FOOTBALLCOM_URL, timeout=30000,
                           wait_until='domcontentloaded')
            await page.wait_for_timeout(3000)
        except Exception:
            pass
        cookies = await context.cookies()
        await browser.close()
        return {c['name']: c['value'] for c in cookies}


async def fetch_page(session, page_num, start_ms, end_ms, headers):
    """Fetches a single page via POST request"""
    payload = {
        'order': 0,
        'startTime': start_ms,
        'endTime': end_ms,
        'productId': 3,
        'sportId': 'sr:sport:20',
        'pageNum': page_num,
        'pageSize': 100,
        'marketId': '186'
    }
    try:
        async with session.post(
                API_URL,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15)
        ) as response:
            if response.status == 200:
                return await response.json()
    except Exception as e:
        print(f"  ❌ Error: {e}")
    return None


async def scrape_all_matches():
    start = _time.time()
    print("\n" + "🟡 " * 20)
    print("   FOOTBALL.COM GHANA TABLE TENNIS SCRAPER")
    print(f"   {datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}")
    print("🟡 " * 20 + "\n")

    print("[INFO] Getting Football.com session...")
    cookies = await get_cookies()

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                     'AppleWebKit/537.36 (KHTML, like Gecko) '
                     'Chrome/120.0.0.0 Safari/537.36',
        'Content-Type': 'application/json',
        'Referer': FOOTBALLCOM_URL,
        'Origin': 'https://www.football.com',
        'Cache-Control': 'no-cache, no-store, must-revalidate',
        'Pragma': 'no-cache',
    }

    all_matches = []
    start_ms, end_ms = get_today_timestamps()

    async with aiohttp.ClientSession(cookies=cookies) as session:
        print("[INFO] Fetching today's matches...")
        data = await fetch_page(session, 1, start_ms, end_ms, headers)
        
        if data:
            total = data.get('data', {}).get('totalSize', 0)
            matches = parse_response(data)
            all_matches.extend(matches)
            print(f"  [OK] Page 1: {len(matches)} matches (Total available: {total})")

            page_num = 2
            while True:
                data = await fetch_page(session, page_num, start_ms, end_ms, headers)
                if not data:
                    break
                matches = parse_response(data)
                if not matches:
                    break
                all_matches.extend(matches)
                print(f"  [OK] Page {page_num}: {len(matches)} matches (Total: {len(all_matches)})")
                page_num += 1
                await asyncio.sleep(0.3)

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
    kickoff   = event.get('estimateStartTime', '')
    status    = event.get('matchStatus', '')
    event_id  = str(event.get('eventId', '') or event.get('id', '') or '')

    if not home_team or not away_team:
        return None

    # Check status code (0 = Not start/pre-match, 1 = Live, 2 = Suspended, etc.)
    status_int = event.get('status')
    if status_int is not None and status_int != 0:
        return None

    if status in ['ended', 'finished', 'Ended', 'Finished', 'cancelled', 'Cancelled']:
        return None

    if isinstance(kickoff, int):
        kickoff_dt = datetime.fromtimestamp(kickoff / 1000)
        # Only keep games played on the current local day
        if kickoff_dt.date() != datetime.now().date():
            return None
        # Skip already started or past matches
        if kickoff_dt <= datetime.now():
            return None
        kickoff = kickoff_dt.strftime('%Y-%m-%d %H:%M')
    else:
        return None

    is_live = status in ['Living', 'living', 'live', 'Live', 'LIVE', 'inprogress', 'InProgress', 'in_progress']
    if is_live:
        return None

    match = {
        'home_team': home_team,
        'away_team': away_team,
        'kickoff': str(kickoff),
        'tournament': tournament_name,
        'is_live': is_live,
        'status': status,
        'event_id': event_id,
        'source': 'footballcom_gh',
        'odds_2way': {}
    }

    markets = event.get('markets', [])
    for market in markets:
        market_id = str(market.get('id', ''))
        market_status = str(market.get('status', '') or '').lower()
        is_market_locked = market.get('isLocked', False) or market.get('locked', False)

        if is_market_locked or market_status in {
            'suspended', 'deactivated', 'closed',
            'locked', 'inactive', 'disabled',
            'halted', 'stopped', 'unavailable',
        }:
            continue

        outcomes = market.get('outcomes', [])
        active_outcomes = [
            o for o in outcomes
            if o.get('isActive', 1) != 0
            and not o.get('isLocked', False)
            and not o.get('locked', False)
            and str(o.get('odds', 0) or 0) not in ('0', '', 'None')
        ]

        if market_id == '186' and len(active_outcomes) >= 2:
            home_odds = 0.0
            away_odds = 0.0
            for o in active_outcomes:
                desc = o.get('desc', '')
                try:
                    odds_val = float(o.get('odds', 0) or 0)
                except (ValueError, TypeError):
                    continue
                if desc == 'Home':
                    home_odds = odds_val
                elif desc == 'Away':
                    away_odds = odds_val
            if home_odds > 1.01 and away_odds > 1.01:
                match['odds_2way'] = {'home': home_odds, 'away': away_odds}

    if match['odds_2way']:
        return match
    return None


def display_matches(matches):
    if not matches:
        print("WARNING: No matches found")
        return

    print("\nFOOTBALL.COM GHANA TABLE TENNIS")
    print(f"Total matches fetched: {len(matches)}")
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
    matches = asyncio.run(scrape_all_matches())
    
    # Establish independent directories
    output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
    os.makedirs(output_dir, exist_ok=True)
    
    json_path = os.path.join(output_dir, 'footballcom_tt_odds.json')
    txt_path  = os.path.join(output_dir, 'footballcom_tt_matches.txt')

    if matches:
        display_matches(matches)

        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(matches, f, indent=2, ensure_ascii=False)

        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write("FOOTBALL.COM GHANA TABLE TENNIS - ALL MATCHES\n")
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
        print("\n⚠️ No matches found")
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump([], f)
            
    return matches


if __name__ == "__main__":
    run()
