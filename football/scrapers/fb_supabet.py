import os
import asyncio
import sys
import json
import tempfile
import time as _time
from datetime import datetime, timedelta
from playwright.async_api import async_playwright
import re
from typing import Any

sys.stdout.reconfigure(encoding='utf-8')

SOURCE = 'supabet_gh'
VIRTUAL_KEYWORDS = ['srl', 'simulated', 'esport', 'e-soccer', 'esoccer',
                    'cyber', 'virtual', 'efootball', 'e-football']

def is_virtual(home, away):
    text = f'{home} {away}'.lower()
    return any(kw in text for kw in VIRTUAL_KEYWORDS)

def parse_supabet_date(time_text: str) -> datetime | None:
    now = datetime.now()
    text = time_text.strip().lower()
    
    if not text:
        return None
        
    if "live" in text or "in " in text:
        return now
        
    if "today" in text:
        t = re.search(r'(\d{2}:\d{2})', text)
        if t:
            hour, minute = map(int, t.group(1).split(":"))
            return now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return now
        
    if "tomorrow" in text:
        t = re.search(r'(\d{2}:\d{2})', text)
        if t:
            hour, minute = map(int, t.group(1).split(":"))
            return (now + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        return now + timedelta(days=1)
        
    # check for DD/MM or DD.MM
    m = re.search(r'(\d{2})[/\.](\d{2})\s+(\d{2}):(\d{2})', text)
    if m:
        day = int(m.group(1))
        month = int(m.group(2))
        hour = int(m.group(3))
        minute = int(m.group(4))
        return datetime(now.year, month, day, hour, minute)
        
    return None

def base_match(home: str, away: str, kickoff: str, tournament: str, is_live: bool) -> dict[str, Any]:
    return {
        "home_team": home,
        "away_team": away,
        "kickoff": kickoff,
        "tournament": tournament,
        "is_live": is_live,
        "status": "Not start",
        "source": SOURCE,
        "odds_1x2": {},
        "odds_ou": {},
        "odds_asian_ou": {},
        "odds_dc": {},
        "odds_gg": {},
        "odds_1x2_one_up": {},
        "odds_1x2_two_up": {},
        "odds_fh_1x2": {},
        "odds_sh_1x2": {},
        "odds_fh_ou": {},
        "odds_sh_ou": {},
        "odds_fh_dc": {},
        "odds_sh_dc": {},
        "odds_corners_1x2": {},
        "odds_bookings_1x2": {},
        "odds_bookings_ou": {},
        "odds_gg_2plus": {},
    }

async def scrape():
    start = _time.time()
    print("\n" + "🟡 " * 20)
    print("   SUPABET GHANA SCRAPER")
    print(f"   {datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}")
    print("🟡 " * 20 + "\n")

    all_matches = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        # Add headers to bypass simple bot checks
        ctx = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        page = await ctx.new_page()
        
        # Load the raw Boltbet iframe directly
        iframe_url = 'https://eu01.sportsbook.adv.bet/?orgUuid=17e5b1b7-bb38-d332-4e2f-f1fde542b6b9#/match/football'
        try:
            print(f"  [Supabet] Navigating to sportsbook...")
            await page.goto(iframe_url, wait_until='networkidle', timeout=30000)
            await page.wait_for_timeout(5000)
        except Exception as e:
            print(f"  [Supabet] Navigation error: {e}")
            await browser.close()
            return []

        print(f"  [Supabet] Scrolling to load matches...")
        for i in range(10): # Scroll up to 10 times to load more matches
            await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
            await page.wait_for_timeout(1000)

        # Query all match anchor tags
        matches = await page.query_selector_all('a[href*="#/match/football/"]')
        print(f"  [Supabet] Found {len(matches)} potential match cards")

        today = datetime.now().date()
        skipped_dates = {}

        for m in matches:
            try:
                teams_spans = await m.query_selector_all('.event-info__teams span')
                if len(teams_spans) < 2: continue
                
                home = (await teams_spans[0].inner_text()).strip()
                away = (await teams_spans[1].inner_text()).strip()
                
                if not home or not away or is_virtual(home, away): continue

                # Get tournament/date from the UI if possible
                time_el = await m.query_selector('.event-info__time')
                time_text = (await time_el.inner_text()).strip() if time_el else ""
                
                # Boltbet formats time like '11/05 19:00' or 'Live'
                is_live = 'live' in time_text.lower() or "'" in time_text

                # Parse kickoff date
                dt = parse_supabet_date(time_text)
                if not dt:
                    dt = datetime.now()
                
                # Filter to today's matches only
                if dt.date() != today:
                    date_key = dt.strftime('%Y-%m-%d')
                    skipped_dates[date_key] = skipped_dates.get(date_key, 0) + 1
                    continue

                dt_str = dt.strftime('%Y-%m-%d %H:%M')

                odds_btns = await m.query_selector_all('.bid-option')
                if len(odds_btns) < 3: continue

                try:
                    h_odd = float((await odds_btns[0].inner_text()).strip() or 0)
                    d_odd = float((await odds_btns[1].inner_text()).strip() or 0)
                    a_odd = float((await odds_btns[2].inner_text()).strip() or 0)
                except ValueError:
                    continue

                if h_odd <= 1.01 or d_odd <= 1.01 or a_odd <= 1.01: continue

                match_obj = base_match(home, away, dt_str, 'Football', is_live)
                match_obj['odds_1x2'] = {'home': h_odd, 'draw': d_odd, 'away': a_odd}
                all_matches.append(match_obj)
            except Exception as e:
                continue

        await browser.close()
        
        if skipped_dates:
            print(f"  [Supabet] Skipped matches on future dates: {skipped_dates}")
    
    # Remove duplicates based on home + away names
    unique_matches = []
    seen = set()
    for m in all_matches:
        key = f"{m['home_team']}_{m['away_team']}"
        if key not in seen:
            seen.add(key)
            unique_matches.append(m)

    unique_matches.sort(key=lambda x: x['kickoff'])
    return unique_matches


# ── FORMATTING HELPERS ─────────────────────────────────────────────────────────

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
    if not rows:
        return fmt_row("", "(No Over/Under lines available)")
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
    if not rows:
        return fmt_row("", "(No Asian Over/Under lines available)")
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

    lines.append("")  # Blank line after match block

    return "\n".join(lines)


def run():
    output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
    os.makedirs(output_dir, exist_ok=True)
    start = _time.time()
    matches = asyncio.run(scrape())

    if matches:
        with open(os.path.join(output_dir, 'supabet_odds.json'), 'w') as f:
            json.dump(matches, f, indent=2)

        with open(os.path.join(output_dir, 'supabet_matches.txt'), 'w', encoding='utf-8') as f:
            f.write('SUPABET GHANA - ALL MATCHES\n')
            f.write(f'Generated: {datetime.now().strftime("%A, %d %B %Y %H:%M:%S")}\n')
            f.write(f'Total: {len(matches)} matches\n')
            f.write('=' * 60 + '\n\n')
            for m in matches:
                f.write(format_match_text_block(m))

        print(f"Saved to {os.path.join(output_dir, 'supabet_odds.json')}")
        print(f"Full list saved to {os.path.join(output_dir, 'supabet_matches.txt')}")
        print(f"   Open the .txt file to see all {len(matches)} matches!")
        print(f"⏱️  Scraping completed in {_time.time() - start:.1f}s")
    else:
        print('⚠️ No matches found')

    return matches

if __name__ == '__main__':
    run()