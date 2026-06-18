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
            'odds_1x2_one_up': {},
            'odds_1x2_two_up': {},
            'odds_fh_1x2': {},
            'odds_sh_1x2': {},
            'odds_fh_ou': {},
            'odds_sh_ou': {},
            'odds_fh_dc': {},
            'odds_sh_dc': {},
            'odds_corners_1x2': {},
            'odds_bookings_1x2': {},
            'odds_bookings_ou': {},
            'odds_ou': {},
            'odds_asian_ou': {},
            'odds_gg': {},
            'odds_gg_2plus': {},
            'odds_dc': {},
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
                    m = re.search(r'total=(\d+(?:\.\d+)?)', o_id)
                    if not m:
                        m = re.search(r'(\d+(?:\.\d+)?)', o_id)
                    if m:
                        raw_line = m.group(1)
                        try:
                            line_val = float(raw_line)
                            line_str = str(line_val)
                        except ValueError:
                            continue

                        target_dict = match['odds_ou'] if line_val % 1.0 == 0.5 else match['odds_asian_ou']

                        if line_str not in target_dict:
                            target_dict[line_str] = {}
                        
                        price = price_map.get(o_id, 0)
                        if price > 1.01:
                            if o_id.endswith('12'):
                                target_dict[line_str]['over'] = price
                            else:
                                target_dict[line_str]['under'] = price
                
                # Cleanup incomplete lines
                complete_ou = {}
                for l_str, vals in match['odds_ou'].items():
                    if vals.get('over', 0) > 1 and vals.get('under', 0) > 1:
                        complete_ou[l_str] = vals
                match['odds_ou'] = complete_ou

                complete_asian = {}
                for l_str, vals in match['odds_asian_ou'].items():
                    if vals.get('over', 0) > 1 and vals.get('under', 0) > 1:
                        complete_asian[l_str] = vals
                match['odds_asian_ou'] = complete_asian

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


def fmt_row(label, val):
    prefix = f"│ {label:<16} "
    val_width = 80 - len(prefix) - 2
    return f"{prefix}{val:<{val_width}} │"

def fmt_box_top(title):
    prefix = f"┌── {title} "
    dash_count = 80 - len(prefix) - 1
    return prefix + "─" * dash_count + "┐"

def fmt_box_bottom():
    return "└" + "─" * 78 + "┘"

def fmt_box_subheading(sub_title):
    content = f"[{sub_title}]"
    return f"│ {content:<76} │"

def fmt_box_divider():
    line = "─" * 76
    return f"│ {line} │"

def fmt_3way(o):
    if not o or o.get("home") is None or o.get("draw") is None or o.get("away") is None:
        return "N/A"
    return f"Home: {o['home']:<7} │ Draw: {o['draw']:<7} │ Away: {o['away']}"

def fmt_dc(o):
    if not o or o.get("1x") is None or o.get("12") is None or o.get("x2") is None:
        return "N/A"
    return f"1X: {o['1x']:<8} │ 12: {o['12']:<8} │ X2: {o['x2']}"

def fmt_gg(o):
    if not o or o.get("yes") is None or o.get("no") is None:
        return "N/A"
    return f"GG (Yes): {o['yes']:<6} │ NG (No): {o['no']}"

def fmt_ou_section(ou_dict):
    if not ou_dict:
        return fmt_row("", "(No Over/Under lines available)")
    try:
        sorted_keys = sorted(ou_dict.keys(), key=lambda x: float(x))
    except Exception:
        return fmt_row("", "(No Over/Under lines available)")
    rows = []
    for line in sorted_keys:
        try:
            if float(line) % 1.0 != 0.5:
                continue
        except ValueError:
            continue
        ou = ou_dict[line]
        over = ou.get("over")
        under = ou.get("under")
        if over is not None and under is not None:
            line_label = f"Line {line}"
            line_val = f"Over: {over:<8} │ Under: {under:<8}"
            rows.append(fmt_row(line_label, line_val))
    return "\n".join(rows)

def fmt_asian_ou_section(ou_dict):
    if not ou_dict:
        return fmt_row("", "(No Asian Over/Under lines available)")
    try:
        sorted_keys = sorted(ou_dict.keys(), key=lambda x: float(x))
    except Exception:
        return fmt_row("", "(No Asian Over/Under lines available)")
    rows = []
    for line in sorted_keys:
        ou = ou_dict[line]
        over = ou.get("over")
        under = ou.get("under")
        if over is not None and under is not None:
            line_label = f"Line {line}"
            line_val = f"Over: {over:<8} │ Under: {under:<8}"
            rows.append(fmt_row(line_label, line_val))
    return "\n".join(rows)

def fmt_ou_section_all(ou_dict, empty_msg="(No Over/Under lines available)"):
    """Like fmt_ou_section but shows ALL lines (no .5 filter). Used for half-time markets."""
    if not ou_dict:
        return fmt_row("", empty_msg)
    try:
        sorted_keys = sorted(ou_dict.keys(), key=lambda x: float(x))
    except Exception:
        return fmt_row("", empty_msg)
    rows = []
    for line in sorted_keys:
        ou = ou_dict[line]
        over = ou.get("over")
        under = ou.get("under")
        if over is not None and under is not None:
            line_label = f"Line {line}"
            line_val = f"Over: {over:<8} │ Under: {under:<8}"
            rows.append(fmt_row(line_label, line_val))
    if not rows:
        return fmt_row("", empty_msg)
    return "\n".join(rows)

def format_match_text_block(m):
    # Header
    title = f"⚽ {m['home_team']} vs {m['away_team']}"
    if m.get("is_live"):
        title += " (🔴 LIVE)"
    meta = f"🏆 {m['tournament']} │ 🕐 {m['kickoff']}"
    
    # Border width
    w = 80
    
    # Formatting markets
    m_1x2 = fmt_3way(m.get("odds_1x2"))
    m_dc = fmt_dc(m.get("odds_dc"))
    m_gg = fmt_gg(m.get("odds_gg"))
    m_2up = fmt_3way(m.get("odds_1x2_two_up"))
    m_1up = fmt_3way(m.get("odds_1x2_one_up"))
    
    # 1st Half / 2nd Half
    fh_1x2 = fmt_3way(m.get("odds_fh_1x2"))
    fh_dc = fmt_dc(m.get("odds_fh_dc"))
    
    sh_1x2 = fmt_3way(m.get("odds_sh_1x2"))
    sh_dc = fmt_dc(m.get("odds_sh_dc"))
    
    # Specials
    c_1x2 = fmt_3way(m.get("odds_corners_1x2"))
    b_1x2 = fmt_3way(m.get("odds_bookings_1x2"))
    gg_2plus = fmt_gg(m.get("odds_gg_2plus"))

    # Construct the block
    lines = []
    lines.append("═" * w)
    lines.append(f"{title}")
    lines.append(f"{meta}")
    lines.append("═" * w)
    
    # Main Markets
    lines.append(fmt_box_top("MAIN MARKETS"))
    lines.append(fmt_row("1X2 (Result)", m_1x2))
    lines.append(fmt_row("Double Chance", m_dc))
    lines.append(fmt_row("GG/NG", m_gg))
    lines.append(fmt_row("1X2 Two Up", m_2up))
    lines.append(fmt_row("1X2 One Up", m_1up))
    lines.append(fmt_box_bottom())
    
    # Over/Under Lines
    lines.append(fmt_box_top("OVER/UNDER LINES"))
    lines.append(fmt_ou_section(m.get("odds_ou")))
    lines.append(fmt_box_bottom())
    
    # Asian Over/Under Lines
    lines.append(fmt_box_top("ASIAN OVER/UNDER LINES"))
    lines.append(fmt_asian_ou_section(m.get("odds_asian_ou")))
    lines.append(fmt_box_bottom())
    
    # Half Time Markets
    lines.append(fmt_box_top("HALF TIME MARKETS"))
    lines.append(fmt_box_subheading("1ST HALF"))
    lines.append(fmt_row("1X2 (Result)", fh_1x2))
    lines.append(fmt_row("Double Chance", fh_dc))
    lines.append(fmt_box_subheading("1ST HALF OVER/UNDER"))
    lines.append(fmt_ou_section_all(m.get("odds_fh_ou")))
    lines.append(fmt_box_divider())
    lines.append(fmt_box_subheading("2ND HALF"))
    lines.append(fmt_row("1X2 (Result)", sh_1x2))
    lines.append(fmt_row("Double Chance", sh_dc))
    lines.append(fmt_box_subheading("2ND HALF OVER/UNDER"))
    lines.append(fmt_ou_section_all(m.get("odds_sh_ou")))
    lines.append(fmt_box_bottom())
    
    # Specials & Stats
    lines.append(fmt_box_top("CORNERS, BOOKINGS & SPECIALS"))
    lines.append(fmt_row("Corners 1X2", c_1x2))
    lines.append(fmt_row("Bookings 1X2", b_1x2))
    lines.append(fmt_box_subheading("BOOKINGS OVER/UNDER"))
    lines.append(fmt_ou_section_all(m.get("odds_bookings_ou"), empty_msg="(No Bookings O/U lines available)"))
    lines.append(fmt_row("GG/NG 2+", gg_2plus))
    lines.append(fmt_box_bottom())
    lines.append("") # Blank line after match block
    
    return "\n".join(lines)


def run():
    output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
    os.makedirs(output_dir, exist_ok=True)
    import time as _time
    start = _time.time()
    matches = asyncio.run(scrape_betway())

    if matches:
        display_matches(matches)

        with open(os.path.join(output_dir, 'betway_odds.json'), 'w') as f:
            json.dump(matches, f, indent=2)

        with open(os.path.join(output_dir, 'betway_matches.txt'), 'w',
                  encoding='utf-8') as f:
            f.write(f"BETWAY GHANA - ALL MATCHES\n")
            f.write(f"Generated: "
                    f"{datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}\n")
            f.write(f"Total: {len(matches)} matches\n")
            f.write("=" * 60 + "\n\n")

            for match in matches:
                f.write(format_match_text_block(match))

        print(f"Saved to {os.path.join(output_dir, 'betway_odds.json')}")
        print(f"Full list saved to {os.path.join(output_dir, 'betway_matches.txt')}")
        print(f"   Open the .txt file to see all {len(matches)} matches!")
        print(f"⏱️  Scraping completed in {_time.time() - start:.1f}s")
    else:
        print("⚠️ No matches found")

    return matches


if __name__ == "__main__":
    run()