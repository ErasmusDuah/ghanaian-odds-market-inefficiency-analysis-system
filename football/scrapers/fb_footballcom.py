import os
import asyncio
from playwright.async_api import async_playwright
import sys
sys.stdout.reconfigure(encoding='utf-8')
import json
import time as _time
from datetime import datetime, timedelta
import aiohttp


FOOTBALLCOM_URL = 'https://www.football.com/gh/sport/football'
API_URL = ('https://www.football.com/api/gh/factsCenter/'
           'wapConfigurableEventsByOrder')


def get_today_timestamps():
    """Gets start and end timestamps for today"""
    now = datetime.now()
    start = datetime(now.year, now.month, now.day, 0, 0, 0)
    end = start + timedelta(days=1)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    return start_ms, end_ms


def get_tomorrow_timestamps():
    """Gets start and end timestamps for tomorrow"""
    now = datetime.now()
    start = datetime(now.year, now.month, now.day, 0, 0, 0)
    start = start + timedelta(days=1)
    end = start + timedelta(days=1)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    return start_ms, end_ms


async def fetch_page(session, page_num,
                     start_ms, end_ms, headers):
    """Fetches a single page via POST request"""
    payload = {
        'order': 0,
        'startTime': start_ms,
        'endTime': end_ms,
        'productId': 3,
        'sportId': 'sr:sport:1',
        'pageNum': page_num,
        'pageSize': 100
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


async def scrape_footballcom():
    start = _time.time()
    print("\n" + "🟡 " * 20)
    print("   FOOTBALL.COM GHANA SCRAPER")
    print(f"   {datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}")
    print("🟡 " * 20 + "\n")

    # Get session cookies via browser first
    cookies_list = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                      'AppleWebKit/537.36 (KHTML, like Gecko) '
                      'Chrome/120.0.0.0 Safari/537.36'
        )
        page = await context.new_page()
        await page.goto(FOOTBALLCOM_URL, timeout=30000,
                       wait_until='domcontentloaded')
        await page.wait_for_timeout(3000)
        cookies = await context.cookies()
        cookies_list = {c['name']: c['value'] for c in cookies}
        await browser.close()

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

    async with aiohttp.ClientSession(
            cookies=cookies_list) as session:

        # ── TODAY ────────────────────────────────────────────
        print("📅 Fetching today's matches...")
        start_ms, end_ms = get_today_timestamps()

        data = await fetch_page(session, 1, start_ms, end_ms, headers)

        if data:
            matches = parse_response(data)
            all_matches.extend(matches)
            total = data.get('data', {}).get('totalSize', 0)
            print(f"  ✅ Page 1: {len(matches)} matches "
                  f"(Total: {total})")

            # Keep fetching pages until API returns empty
            page_num = 2
            while True:
                data = await fetch_page(
                    session, page_num, start_ms, end_ms, headers)
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

        # ── TOMORROW (fallback if today is empty) ────────────
        if not all_matches:
            print("\n📅 No today matches — fetching tomorrow...")
            start_ms, end_ms = get_tomorrow_timestamps()

            data = await fetch_page(session, 1, start_ms, end_ms, headers)

            if data:
                matches = parse_response(data)
                all_matches.extend(matches)
                total = data.get('data', {}).get('totalSize', 0)
                print(f"  ✅ Page 1: {len(matches)} matches "
                      f"(Total: {total})")

                # Keep fetching pages until API returns empty
                page_num = 2
                while True:
                    data = await fetch_page(
                        session, page_num, start_ms, end_ms, headers)
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

    # Sort by kickoff time
    all_matches.sort(key=lambda x: x['kickoff'])

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

    if status in ['ended', 'finished', 'Ended',
                  'Finished', 'cancelled']:
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
        'source': 'footballcom_gh',
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

        if market_id == '10' and len(active_outcomes) >= 3:
            by_desc = {}
            for outcome in active_outcomes:
                desc = str(outcome.get('desc', '')).lower()
                odds = float(outcome.get('odds', 0) or 0)
                if odds > 1.01:
                    if 'home or draw' in desc or '1x' in desc or 'home/draw' in desc:
                        by_desc['1x'] = odds
                    elif 'home or away' in desc or '12' in desc or 'home/away' in desc:
                        by_desc['12'] = odds
                    elif 'draw or away' in desc or 'x2' in desc or 'draw/away' in desc:
                        by_desc['x2'] = odds
            if len(by_desc) == 3:
                match['odds_dc'] = by_desc

        if market_id == '18' and len(active_outcomes) >= 2:
            desc = active_outcomes[0].get('desc', '')
            import re
            m = re.search(r'(\d+(?:\.\d+)?)', str(desc))
            if m:
                raw_line = m.group(1)
                try:
                    line_val = float(raw_line)
                    line_str = str(line_val)
                except ValueError:
                    continue

                ov = 0.0
                un = 0.0
                for outcome in active_outcomes:
                    o_desc = str(outcome.get('desc', '')).lower()
                    o_odds = float(outcome.get('odds', 0) or 0)
                    if 'over' in o_desc:
                        ov = o_odds
                    elif 'under' in o_desc:
                        un = o_odds

                if ov > 1.01 and un > 1.01:
                    if line_val % 1.0 == 0.5:
                        match['odds_ou'][line_str] = {'over': ov, 'under': un}
                    else:
                        match['odds_asian_ou'][line_str] = {'over': ov, 'under': un}

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
    print(f"\n📋 FOOTBALL.COM GHANA")
    print(f"⚽ Total matches fetched: {len(matches)}")
    print(f"📊 With 1X2 odds: {len(with_odds)}")
    print("=" * 50)

    print("\n📝 Sample (first 10 matches):")
    for match in with_odds[:10]:
        live_tag = "🔴" if match.get('is_live') else ""
        print(f"  {live_tag} {match['home_team']} vs "
              f"{match['away_team']} | "
              f"{match['kickoff']} | "
              f"{match['tournament']}")

    if len(with_odds) > 10:
        print(f"\n  ... and {len(with_odds) - 10} more matches")
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
    matches = asyncio.run(scrape_footballcom())
    if matches:
        display_matches(matches)

        output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
        os.makedirs(output_dir, exist_ok=True)
        
        with open(os.path.join(output_dir, 'footballcom_odds.json'), 'w') as f:
            json.dump(matches, f, indent=2)

        with open(os.path.join(output_dir, 'footballcom_matches.txt'), 'w',
                  encoding='utf-8') as f:
            f.write(f"FOOTBALL.COM GHANA - ALL MATCHES\n")
            f.write(f"Generated: "
                    f"{datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}\n")
            f.write(f"Total: {len(matches)} matches\n")
            f.write("=" * 60 + "\n\n")

            for match in matches:
                f.write(format_match_text_block(match))

        print(f"💾 Saved to {os.path.join(output_dir, 'footballcom_odds.json')}")
        print(f"📄 Full list saved to {os.path.join(output_dir, 'footballcom_matches.txt')}")
        print(f"   Open the .txt file to see all {len(matches)} matches!")
        print(f"⏱️  Scraping completed in {_time.time() - start:.1f}s")
    else:
        print("\n⚠️ No matches found")
    return matches


if __name__ == "__main__":
    run()