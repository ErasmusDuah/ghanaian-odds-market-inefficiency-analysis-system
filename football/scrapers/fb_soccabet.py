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

SOURCE = 'soccabet_gh'
VIRTUAL_KEYWORDS = ['srl', 'simulated', 'esport', 'e-soccer', 'esoccer',
                    'cyber', 'virtual', 'efootball', 'e-football']

def is_virtual(home, away):
    text = f'{home} {away}'.lower()
    return any(kw in text for kw in VIRTUAL_KEYWORDS)

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

def parse_soccabet_date(date_str: str, time_str: str) -> datetime:
    now = datetime.now()
    date_str = date_str.strip().lower()
    time_str = time_str.strip()
    
    if not time_str or ":" not in time_str:
        return now
        
    try:
        hour, minute = map(int, time_str.split(":"))
    except ValueError:
        hour, minute = 0, 0
        
    if date_str == "today":
        dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    elif date_str == "tomorrow":
        dt = (now + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    else:
        # e.g., "20 Jun" or "20 Jun 2026"
        m = re.match(r'(\d+)\s+([a-zA-Z]+)', date_str)
        if m:
            day = int(m.group(1))
            month_name = m.group(2)
            months = {
                "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
                "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12
            }
            month = months.get(month_name[:3], now.month)
            dt = datetime(now.year, month, day, hour, minute)
        else:
            dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            
    return dt

async def scrape():
    start = _time.time()
    print("\n" + "⚽ " * 20)
    print("   SOCCABET GHANA SCRAPER")
    print(f"   {datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}")
    print("⚽ " * 20 + "\n")

    all_matches = []
    now = datetime.now()
    today = now.date()
    today_str = today.strftime('%Y-%m-%d')

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        page = await ctx.new_page()
        
        url = f'https://www.soccabet.com/sports?tr={today_str}&s=77'
        try:
            print(f"  [Soccabet] Navigating to sportsbook...")
            await page.goto(url, wait_until='domcontentloaded', timeout=30000)
            try:
                await page.wait_for_selector('app-event-item', timeout=15000)
            except Exception:
                pass
            await page.wait_for_timeout(3000)
        except Exception as e:
            print(f"  [Soccabet] Navigation error: {e}")
            await browser.close()
            return []

        print(f"  [Soccabet] Scrolling to load matches...")
        for i in range(10): # Scroll to load more matches
            await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
            await page.wait_for_timeout(1000)

        loc = page.locator("app-event-item")
        count = await loc.count()
        print(f"  [Soccabet] Found {count} event cards in DOM")

        skipped_dates = {}
        for i in range(count):
            try:
                item = loc.nth(i)
                home_el = item.locator(".match-home-team")
                away_el = item.locator(".match-away-team")
                date_el = item.locator(".match-date")
                time_el = item.locator(".match-time")
                tour_el = item.locator(".match-tournament")
                
                if await home_el.count() == 0 or await away_el.count() == 0:
                    continue
                    
                home = (await home_el.inner_text()).strip()
                away = (await away_el.inner_text()).strip()
                date_text = (await date_el.inner_text()).strip() if await date_el.count() > 0 else "Today"
                time_text = (await time_el.inner_text()).strip() if await time_el.count() > 0 else "00:00"
                tournament = (await tour_el.inner_text()).strip() if await tour_el.count() > 0 else "Soccer"
                
                if not home or not away or is_virtual(home, away):
                    continue
                    
                dt = parse_soccabet_date(date_text, time_text)
                
                # Check date filter (today matches only)
                if dt.date() != today:
                    date_key = dt.strftime('%Y-%m-%d')
                    skipped_dates[date_key] = skipped_dates.get(date_key, 0) + 1
                    continue
                    
                is_live = False # pre-match page
                
                # Extract odds
                odds_els = await item.locator(".match-odd").all()
                if len(odds_els) < 3:
                    continue
                    
                try:
                    odds_dict = {}
                    for el in odds_els:
                        text = await el.inner_text()
                        parts = [p.strip() for p in text.split("\n") if p.strip()]
                        if len(parts) == 2:
                            outcome, val_str = parts[0], parts[1]
                            odds_dict[outcome.lower()] = float(val_str)
                            
                    h_odd = odds_dict.get("1")
                    d_odd = odds_dict.get("x")
                    a_odd = odds_dict.get("2")
                except Exception:
                    continue
                    
                if not h_odd or not d_odd or not a_odd:
                    continue
                    
                match_obj = base_match(home, away, dt.strftime('%Y-%m-%d %H:%M'), tournament, is_live)
                match_obj['odds_1x2'] = {'home': h_odd, 'draw': d_odd, 'away': a_odd}
                all_matches.append(match_obj)
            except Exception:
                continue

        await browser.close()
        
        if skipped_dates:
            print(f"  [Soccabet] Skipped matches on future dates: {skipped_dates}")
    
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
        with open(os.path.join(output_dir, 'soccabet_odds.json'), 'w') as f:
            json.dump(matches, f, indent=2)

        with open(os.path.join(output_dir, 'soccabet_matches.txt'), 'w', encoding='utf-8') as f:
            f.write('SOCCABET GHANA - ALL MATCHES\n')
            f.write(f'Generated: {datetime.now().strftime("%A, %d %B %Y %H:%M:%S")}\n')
            f.write(f'Total: {len(matches)} matches\n')
            f.write('=' * 60 + '\n\n')
            for m in matches:
                f.write(format_match_text_block(m))

        print(f"Saved to {os.path.join(output_dir, 'soccabet_odds.json')}")
        print(f"Full list saved to {os.path.join(output_dir, 'soccabet_matches.txt')}")
        print(f"   Open the .txt file to see all {len(matches)} matches!")
        print(f"⏱️  Scraping completed in {_time.time() - start:.1f}s")
    else:
        print('⚠️ No matches found')

    return matches

if __name__ == '__main__':
    run()