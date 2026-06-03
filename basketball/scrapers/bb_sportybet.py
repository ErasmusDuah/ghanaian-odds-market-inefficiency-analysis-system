import asyncio
import sys
import json
import time as _time
from datetime import datetime
import aiohttp
import os

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

TODAY_URL = (
    'https://www.sportybet.com/api/gh/factsCenter/'
    'pcUpcomingEvents?sportId=sr%3Asport%3A2'
    '&marketId=219%2C220'
    '&pageSize=100&pageNum={page}'
    '&todayGames=true'
)

TOMORROW_URL = (
    'https://www.sportybet.com/api/gh/factsCenter/'
    'pcUpcomingEvents?sportId=sr%3Asport%3A2'
    '&marketId=219%2C220'
    '&pageSize=100&pageNum={page}'
    '&todayGames=false'
)


async def fetch_page_direct(session, page_num, use_tomorrow, headers):
    base_url = TOMORROW_URL.format(page=page_num) if use_tomorrow else TODAY_URL.format(page=page_num)
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
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://www.sportybet.com/gh/sport/basketball',
        'Accept': 'application/json',
        'Cache-Control': 'no-cache, no-store, must-revalidate',
        'Pragma': 'no-cache',
    }

    all_matches = []
    async with aiohttp.ClientSession() as session:
        # --- TODAY'S GAMES ---
        print("[INFO] Fetching today's matches...")
        data = await fetch_page_direct(session, 1, False, headers)
        if data:
            total = data.get('data', {}).get('totalSize', 0)
            matches = parse_response(data)
            all_matches.extend(matches)
            print(f"  [OK] Page 1: {len(matches)} matches (Total available: {total})")

            page_num = 2
            while True:
                data = await fetch_page_direct(session, page_num, False, headers)
                if not data:
                    break
                matches = parse_response(data)
                if not matches:
                    break
                all_matches.extend(matches)
                print(f"  [OK] Page {page_num}: {len(matches)} matches (Total: {len(all_matches)})")
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

    status_int = event.get('status')
    if status_int is not None and status_int != 0:
        return None

    if status in ['ended', 'finished', 'Ended', 'Finished', 'cancelled', 'Cancelled']:
        return None

    if isinstance(kickoff, int):
        kickoff_dt = datetime.fromtimestamp(kickoff / 1000)
        if kickoff_dt.date() != datetime.now().date():
            return None
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
        'source': 'sportybet_gh',
        'odds_2way': {},
        'odds_overtime': {}
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

        if market_id == '219' and len(active_outcomes) >= 2:
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

        elif market_id == '220' and len(active_outcomes) >= 2:
            yes_odds = 0.0
            no_odds = 0.0
            for o in active_outcomes:
                desc = o.get('desc', '')
                try:
                    odds_val = float(o.get('odds', 0) or 0)
                except (ValueError, TypeError):
                    continue
                if desc == 'Yes':
                    yes_odds = odds_val
                elif desc == 'No':
                    no_odds = odds_val
            if yes_odds > 1.01 and no_odds > 1.01:
                match['odds_overtime'] = {'yes': yes_odds, 'no': no_odds}

    if match['odds_2way']:
        return match
    return None


def display_matches(matches):
    if not matches:
        print("WARNING: No matches found")
        return

    print("\nSPORTYBET GHANA BASKETBALL")
    print(f"Total matches fetched: {len(matches)}")
    print("=" * 50)

    print("\nSample (first 10 matches):")
    for match in matches[:10]:
        live_tag = "[LIVE]" if match.get('is_live') else ""
        o = match['odds_2way']
        ot = match.get('odds_overtime')
        ot_str = f" | Overtime: Yes {ot['yes']} - No {ot['no']}" if ot else " | Overtime: N/A"
        print(f"  {live_tag} {match['home_team']} vs {match['away_team']} | {match['kickoff']} | Odds: H {o['home']} - A {o['away']}{ot_str}")
    if len(matches) > 10:
        print(f"\n  ... and {len(matches) - 10} more matches")
    print("=" * 50)


def run():
    start = _time.time()
    matches = asyncio.run(scrape_all_matches())
    
    output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
    os.makedirs(output_dir, exist_ok=True)
    
    json_path = os.path.join(output_dir, 'sportybet_basketball_odds.json')
    txt_path  = os.path.join(output_dir, 'sportybet_basketball_matches.txt')

    if matches:
        display_matches(matches)

        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(matches, f, indent=2, ensure_ascii=False)

        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write("SPORTYBET GHANA BASKETBALL - ALL MATCHES\n")
            f.write(f"Generated: {datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}\n")
            f.write(f"Total: {len(matches)} matches\n")
            f.write("=" * 60 + "\n\n")

            for match in matches:
                live_tag = "LIVE" if match.get('is_live') else ""
                f.write(f"{match['home_team']} vs {match['away_team']} {live_tag}\n")
                f.write(f"{match['tournament']}\n")
                f.write(f"{match['kickoff']}\n")
                o = match['odds_2way']
                ot = match.get('odds_overtime')
                ot_str = f" | Overtime: Yes {ot['yes']} | No {ot['no']}" if ot else " | Overtime: N/A"
                f.write(f"Winner (2-way): Home {o['home']} | Away {o['away']}{ot_str}\n\n")

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
