import asyncio
from playwright.async_api import async_playwright
import aiohttp
import json
from datetime import datetime, timedelta


TWENTYTWOBET_URL = 'https://22bet.com.gh/prematch/football'


async def scrape_twentytwobet():
    print("\n" + "🟣 " * 20)
    print("   22BET GHANA SCRAPER")
    print(f"   {datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}")
    print("🟣 " * 20 + "\n")

    event_data = None
    cookies_dict = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                      'AppleWebKit/537.36 (KHTML, like Gecko) '
                      'Chrome/120.0.0.0 Safari/537.36'
        )
        page = await context.new_page()

        async def handle_response(response):
            nonlocal event_data
            try:
                if 'event/list' in response.url:
                    body = await response.body()
                    if len(body) > 100000:
                        data = json.loads(body)
                        inner = data.get('data', {})
                        if 'items' in inner:
                            event_data = inner
                            total = inner.get('totalCount', 0)
                            last_page = inner.get('lastPage', 1)
                            print(f"  ✅ Captured! "
                                  f"Total: {total} "
                                  f"Pages: {last_page}")
            except Exception as e:
                print(f"  ❌ {e}")

        page.on('response', handle_response)

        print("🌐 Loading 22Bet Ghana...")
        try:
            await page.goto(
                TWENTYTWOBET_URL,
                timeout=60000,
                wait_until='domcontentloaded'
            )
        except Exception as e:
            print(f"⚠️ {str(e)[:60]}")

        await page.wait_for_timeout(15000)

        cookies = await context.cookies()
        cookies_dict = {c['name']: c['value'] for c in cookies}
        await browser.close()

    if not event_data:
        print("❌ No data captured!")
        return []

    now = datetime.now()
    today_str = now.strftime('%Y-%m-%d')
    tomorrow_str = (now + timedelta(days=1)).strftime('%Y-%m-%d')

    # Try today first
    matches = parse_page(event_data, filter_date=today_str)

    if not matches:
        print("📅 No today matches — fetching tomorrow...")
        matches = parse_page(event_data, filter_date=tomorrow_str)

        # If still no matches from browser data, fetch tomorrow via API
        if not matches:
            today = datetime(now.year, now.month, now.day)
            tomorrow = today + timedelta(days=1)
            day_after = tomorrow + timedelta(days=1)
            start_ts = int(tomorrow.timestamp())
            end_ts = int(day_after.timestamp())

            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                             'AppleWebKit/537.36',
                'Accept': 'application/json',
                'Referer': TWENTYTWOBET_URL,
            }

            async with aiohttp.ClientSession(
                    cookies=cookies_dict) as session:
                url = (
                    f'https://22bet.com.gh/api/event/list'
                    f'?lang=en&relations=odds'
                    f'&relations=competitors&relations=league'
                    f'&sportId=1&status=0'
                    f'&timeFrom={start_ts}&timeTo={end_ts}'
                    f'&page=1&limit=50'
                )
                try:
                    async with session.get(
                            url, headers=headers,
                            timeout=aiohttp.ClientTimeout(
                                total=15)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            inner = data.get('data', {})
                            matches = parse_page(inner)
                except Exception as e:
                    print(f"  ❌ API fallback error: {e}")
    else:
        # Fetch remaining today pages via API if needed
        last_page = event_data.get('lastPage', 1)
        if last_page > 1:
            today = datetime(now.year, now.month, now.day)
            tomorrow = today + timedelta(days=1)
            start_ts = int(today.timestamp())
            end_ts = int(tomorrow.timestamp())

            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                             'AppleWebKit/537.36',
                'Accept': 'application/json',
                'Referer': TWENTYTWOBET_URL,
            }

            async with aiohttp.ClientSession(
                    cookies=cookies_dict) as session:
                for page_num in range(2, last_page + 1):
                    url = (
                        f'https://22bet.com.gh/api/event/list'
                        f'?lang=en&relations=odds'
                        f'&relations=competitors&relations=league'
                        f'&sportId=1&status=0'
                        f'&timeFrom={start_ts}&timeTo={end_ts}'
                        f'&page={page_num}&limit=50'
                    )
                    try:
                        async with session.get(
                                url, headers=headers,
                                timeout=aiohttp.ClientTimeout(
                                    total=15)) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                inner = data.get('data', {})
                                page_matches = parse_page(
                                    inner,
                                    filter_date=today_str)
                                matches.extend(page_matches)
                                print(f"  ✅ Page {page_num}: "
                                      f"{len(page_matches)} matches")
                    except Exception as e:
                        print(f"  ❌ Page {page_num}: {e}")
                    await asyncio.sleep(0.3)

    matches.sort(key=lambda x: x['kickoff'])
    print(f"\n✅ Total matches: {len(matches)}")
    return matches


def parse_page(inner, filter_date=None):
    items = inner.get('items', [])

    if filter_date:
        items = [i for i in items
                 if i.get('time', '').startswith(filter_date)]

    relations = inner.get('relations', {})
    odds_map = relations.get('odds', {})

    raw_competitors = relations.get('competitors', [])
    raw_leagues = relations.get('league', [])

    if isinstance(raw_competitors, list):
        comp_map = {c.get('id'): c for c in raw_competitors}
    else:
        comp_map = raw_competitors

    if isinstance(raw_leagues, list):
        league_map = {l.get('id'): l for l in raw_leagues}
    else:
        league_map = raw_leagues

    matches = []
    for item in items:
        try:
            match = parse_event(
                item, comp_map, league_map, odds_map)
            if match:
                matches.append(match)
        except Exception:
            continue
    return matches


def parse_event(item, comp_map, league_map, odds_map):
    event_id = str(item.get('id'))
    c1_id = item.get('competitor1Id')
    c2_id = item.get('competitor2Id')
    league_id = item.get('leagueId')
    time_str = item.get('time', '')
    status = item.get('status', 0)

    c1 = comp_map.get(c1_id, {})
    c2 = comp_map.get(c2_id, {})
    league = league_map.get(league_id, {})

    home_team = c1.get('name', '') if c1 else ''
    away_team = c2.get('name', '') if c2 else ''
    league_name = league.get('name', '') if league else ''

    if not home_team or not away_team:
        return None

    if status in [2, 3, 4]:
        return None

    is_live = status == 1

    try:
        kickoff_dt = datetime.strptime(
            time_str, '%Y-%m-%d %H:%M:%S')
        kickoff = kickoff_dt.strftime('%Y-%m-%d %H:%M')
    except Exception:
        return None

    match = {
        'home_team': home_team,
        'away_team': away_team,
        'kickoff': kickoff,
        'tournament': league_name,
        'is_live': is_live,
        'source': 'twentytwobet_gh',
        'odds_1x2': {},
        'odds_ou': {},
        'odds_gg': {}
    }

    event_odds = odds_map.get(event_id, [])

    for market in event_odds:
        market_id = market.get('id')
        specifiers = market.get('specifiers', '') or ''
        outcomes = market.get('outcomes', [])

        if market_id == 621:
            home = next((o.get('odds') for o in outcomes
                        if o.get('id') == 1 and
                        o.get('active') == 1), None)
            draw = next((o.get('odds') for o in outcomes
                        if o.get('id') == 2 and
                        o.get('active') == 1), None)
            away = next((o.get('odds') for o in outcomes
                        if o.get('id') == 3 and
                        o.get('active') == 1), None)
            if home and draw and away:
                match['odds_1x2'] = {
                    'home': float(home),
                    'draw': float(draw),
                    'away': float(away)
                }

        if market_id == 289 and 'total=2.5' in specifiers:
            over = next((o.get('odds') for o in outcomes
                        if o.get('id') == 12 and
                        o.get('active') == 1), None)
            under = next((o.get('odds') for o in outcomes
                         if o.get('id') == 13 and
                         o.get('active') == 1), None)
            if over and under:
                match['odds_ou'] = {
                    'line': 2.5,
                    'over': float(over),
                    'under': float(under)
                }

        if market_id == 868:
            yes = next((o.get('odds') for o in outcomes
                       if o.get('id') == 4 and
                       o.get('active') == 1), None)
            no = next((o.get('odds') for o in outcomes
                      if o.get('id') == 5 and
                      o.get('active') == 1), None)
            if yes and no:
                match['odds_gg'] = {
                    'yes': float(yes),
                    'no': float(no)
                }

    if not match['odds_1x2']:
        return None

    return match


def display_matches(matches):
    if not matches:
        print("⚠️ No matches found")
        return

    print(f"\n📋 22BET GHANA")
    print(f"⚽ Total matches: {len(matches)}")
    print("=" * 50)
    print("\n📝 Sample (first 10):")
    for match in matches[:10]:
        live_tag = "🔴" if match.get('is_live') else ""
        print(f"  {live_tag} {match['home_team']} vs "
              f"{match['away_team']} | "
              f"{match['kickoff']} | "
              f"{match['tournament']}")
    if len(matches) > 10:
        print(f"  ... and {len(matches) - 10} more")
    print("=" * 50)


def run():
    matches = asyncio.run(scrape_twentytwobet())

    if matches:
        display_matches(matches)

        with open('data/twentytwobet_odds.json', 'w') as f:
            json.dump(matches, f, indent=2)

        with open('data/twentytwobet_matches.txt', 'w',
                  encoding='utf-8') as f:
            f.write(f"22BET GHANA - ALL MATCHES\n")
            f.write(
                f"Generated: "
                f"{datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}\n")
            f.write(f"Total: {len(matches)} matches\n")
            f.write("=" * 60 + "\n\n")
            for match in matches:
                live_tag = "🔴 LIVE" if match.get(
                    'is_live') else ""
                f.write(f"⚽ {match['home_team']} vs "
                        f"{match['away_team']} {live_tag}\n")
                f.write(f"🏆 {match['tournament']}\n")
                f.write(f"🕐 {match['kickoff']}\n")
                if match['odds_1x2']:
                    o = match['odds_1x2']
                    f.write(f"1X2: {o['home']} | "
                            f"{o['draw']} | {o['away']}\n")
                if match['odds_ou']:
                    ou = match['odds_ou']
                    f.write(f"O/U 2.5: Over {ou['over']} | "
                            f"Under {ou['under']}\n")
                if match['odds_gg']:
                    gg = match['odds_gg']
                    f.write(f"GG/NG: Yes {gg['yes']} | "
                            f"No {gg['no']}\n")
                f.write("\n")

        print(f"💾 Saved to data/twentytwobet_odds.json")
        print(f"📄 Full list: data/twentytwobet_matches.txt")
    else:
        print("\n⚠️ No matches found")

    return matches


if __name__ == "__main__":
    run()