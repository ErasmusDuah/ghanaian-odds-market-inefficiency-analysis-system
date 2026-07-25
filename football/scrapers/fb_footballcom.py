import os
import asyncio
from playwright.async_api import async_playwright
import sys
sys.stdout.reconfigure(encoding='utf-8')
import json
import time as _time
from datetime import datetime, timedelta
import aiohttp
from urllib.parse import quote


FOOTBALLCOM_URL = 'https://www.football.com/gh/sport/football'
API_URL = ('https://www.football.com/api/gh/factsCenter/'
           'wapConfigurableEventsByOrder')
DETAIL_URL = 'https://www.football.com/api/gh/factsCenter/event'
DETAIL_CONCURRENCY = int(os.getenv('FOOTBALLCOM_DETAIL_CONCURRENCY', '40'))



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
        print(f"  ERROR Error: {e}")
    return None


async def scrape_footballcom():
    start = _time.time()
    print("\n" + "* " * 20)
    print("   FOOTBALL.COM GHANA SCRAPER")
    print(f"   {datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}")
    print("* " * 20 + "\n")

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

        # -- TODAY --------------------------------------------
        print("Fetching today's matches...")
        start_ms, end_ms = get_today_timestamps()

        data = await fetch_page(session, 1, start_ms, end_ms, headers)

        if data:
            matches = parse_response(data)
            all_matches.extend(matches)
            total = data.get('data', {}).get('totalSize', 0)
            print(f"  OK Page 1: {len(matches)} matches "
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
                print(f"  OK Page {page_num}: "
                      f"{len(matches)} matches "
                      f"(Total: {len(all_matches)})")
                page_num += 1
                await asyncio.sleep(0.3)

        # -- TOMORROW (fallback if today is empty) ------------
        if not all_matches:
            print("\nDate No today matches - fetching tomorrow...")
            start_ms, end_ms = get_tomorrow_timestamps()

            data = await fetch_page(session, 1, start_ms, end_ms, headers)

            if data:
                matches = parse_response(data)
                all_matches.extend(matches)
                total = data.get('data', {}).get('totalSize', 0)
                print(f"  OK Page 1: {len(matches)} matches "
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
                    print(f"  OK Page {page_num}: "
                          f"{len(matches)} matches "
                          f"(Total: {len(all_matches)})")
                    page_num += 1
                    await asyncio.sleep(0.3)

        if all_matches:
            print("\n[INFO] Confirming markets against event detail pages...")
            all_matches = await refresh_matches_with_event_details(
                session, all_matches, headers)
            with_any_odds = sum(
                1 for match in all_matches
                if any(match.get(key) for key in ODDS_KEYS)
            )
            print(f"  [OK] Detail-confirmed matches with odds: {with_any_odds}")

    # Sort by kickoff time
    all_matches.sort(key=lambda x: x['kickoff'])

    print(f"\nTotal matches fetched: {len(all_matches)}")
    return all_matches


UNAVAILABLE_MARKET_STATUSES = {
    'suspended', 'deactivated', 'closed', 'locked', 'inactive', 'disabled',
    'halted', 'stopped', 'unavailable', 'hidden', 'blocked', 'void',
    'removed', 'settled', 'cashout',
}

_FALSEY_FLAGS = {0, '0', False, 'false', 'False', 'no', 'No'}

ODDS_KEYS = (
    'odds_1x2', 'odds_1x2_one_up', 'odds_1x2_two_up',
    'odds_fh_1x2', 'odds_sh_1x2', 'odds_fh_ou', 'odds_sh_ou',
    'odds_fh_dc', 'odds_sh_dc', 'odds_corners_1x2',
    'odds_bookings_1x2', 'odds_bookings_ou', 'odds_ou',
    'odds_asian_ou', 'odds_gg', 'odds_gg_2plus', 'odds_dc',
)



def _flag_is_false(value):
    return value in _FALSEY_FLAGS


def _flag_is_true(value):
    return value is True or str(value).lower() == 'true' or value == 1


def _status_is_available(value):
    if value is None or value == '':
        return True
    if isinstance(value, (int, float)):
        return value == 0

    text = str(value).strip().lower()
    if text in {'0', 'open', 'active', 'available'}:
        return True
    try:
        return float(text) == 0
    except ValueError:
        return text not in UNAVAILABLE_MARKET_STATUSES


def clear_odds(match):
    cleaned = dict(match)
    for key in ODDS_KEYS:
        cleaned[key] = {}
    return cleaned


def detail_tournament_name(event, default=''):
    sport = event.get('sport') if isinstance(event, dict) else {}
    category = sport.get('category', {}) if isinstance(sport, dict) else {}
    tournament = category.get('tournament', {}) if isinstance(category, dict) else {}
    category_name = category.get('name', '') if isinstance(category, dict) else ''
    tournament_name = tournament.get('name', '') if isinstance(tournament, dict) else ''
    if category_name and tournament_name:
        if tournament_name.lower().startswith(category_name.lower()):
            return tournament_name
        return f"{category_name}. {tournament_name}"
    return default or tournament_name or category_name


async def fetch_event_detail(session, event_id, headers):
    if not event_id:
        return None
    url = (
        f"{DETAIL_URL}?productId=3&eventId={quote(str(event_id), safe='')}"
        f"&_t={int(_time.time() * 1000)}"
    )
    try:
        async with session.get(
                url, headers=headers,
                timeout=aiohttp.ClientTimeout(total=12)
        ) as response:
            if response.status != 200:
                return None
            data = await response.json()
    except Exception:
        return None

    if not isinstance(data, dict):
        return None
    detail = data.get('data')
    if not isinstance(detail, dict):
        return None
    return detail


async def refresh_matches_with_event_details(session, matches, headers):
    if not matches:
        return matches

    semaphore = asyncio.Semaphore(max(1, DETAIL_CONCURRENCY))

    async def refresh(match):
        async with semaphore:
            detail = await fetch_event_detail(session, match.get('event_id'), headers)
        if not detail:
            return clear_odds(match)

        tournament = detail_tournament_name(detail, match.get('tournament', ''))
        parsed = parse_event(detail, tournament, datetime.now())
        if not parsed:
            return clear_odds(match)
        return parsed

    refreshed = await asyncio.gather(*(refresh(match) for match in matches))
    return [match for match in refreshed if match]


def is_market_active(market):
    """Return True only for Football.com markets that should be bettable."""
    if not isinstance(market, dict):
        return False

    if _flag_is_true(market.get('banned')):
        return False
    if _flag_is_true(market.get('isLocked')) or _flag_is_true(market.get('locked')):
        return False
    if _flag_is_true(market.get('isSuspended')) or _flag_is_true(market.get('isSettled')):
        return False

    for field in ('isActive', 'active', 'isVisible', 'visible', 'display'):
        if field in market and _flag_is_false(market.get(field)):
            return False

    if not _status_is_available(market.get('status')):
        return False

    return True


def is_outcome_active(outcome):
    if not isinstance(outcome, dict):
        return False

    if _flag_is_true(outcome.get('banned')):
        return False
    if _flag_is_true(outcome.get('isLocked')) or _flag_is_true(outcome.get('locked')):
        return False
    if _flag_is_true(outcome.get('isSuspended')) or _flag_is_true(outcome.get('isSettled')):
        return False

    for field in ('isActive', 'active', 'isVisible', 'visible', 'display'):
        if field in outcome and _flag_is_false(outcome.get(field)):
            return False

    if not _status_is_available(outcome.get('status')):
        return False

    try:
        odds = float(outcome.get('odds', 0) or 0)
    except (TypeError, ValueError):
        return False
    return odds > 1.01


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



def _odd_float(outcome):
    try:
        return float(outcome.get('odds', 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def _put_3way(dest, outcomes):
    mapped = {}
    for outcome in outcomes:
        desc = str(outcome.get('desc') or outcome.get('display') or '').strip().lower()
        odds = _odd_float(outcome)
        if odds <= 1.01:
            continue
        if desc in {'home', '1'}:
            mapped['home'] = odds
        elif desc in {'draw', 'x'}:
            mapped['draw'] = odds
        elif desc in {'away', '2'}:
            mapped['away'] = odds
    if len(mapped) < 3 and len(outcomes) >= 3:
        vals = [_odd_float(outcome) for outcome in outcomes[:3]]
        if all(value > 1.01 for value in vals):
            mapped = {'home': vals[0], 'draw': vals[1], 'away': vals[2]}
    if all(mapped.get(key, 0) > 1.01 for key in ('home', 'draw', 'away')):
        dest.update({'home': mapped['home'], 'draw': mapped['draw'], 'away': mapped['away']})


def _put_dc(dest, outcomes):
    mapped = {}
    fallback = ['1x', '12', 'x2']
    for idx, outcome in enumerate(outcomes):
        desc = str(outcome.get('desc') or outcome.get('display') or '').lower()
        compact = desc.replace(' ', '').replace('/', '').replace('-', '')
        key = None
        if 'homeordraw' in compact or 'homedraw' in compact or '1x' in compact:
            key = '1x'
        elif 'homeoraway' in compact or 'homeaway' in compact or '12' in compact:
            key = '12'
        elif 'draworaway' in compact or 'drawaway' in compact or 'x2' in compact:
            key = 'x2'
        elif idx < len(fallback):
            key = fallback[idx]
        odds = _odd_float(outcome)
        if key and odds > 1.01:
            mapped[key] = odds
    if all(mapped.get(key, 0) > 1.01 for key in ('1x', '12', 'x2')):
        dest.update({'1x': mapped['1x'], '12': mapped['12'], 'x2': mapped['x2']})


def _put_gg(dest, outcomes):
    mapped = {}
    for outcome in outcomes:
        desc = str(outcome.get('desc') or outcome.get('display') or '').strip().lower()
        odds = _odd_float(outcome)
        if odds <= 1.01:
            continue
        if desc.startswith('yes'):
            mapped['yes'] = odds
        elif desc.startswith('no'):
            mapped['no'] = odds
    if mapped.get('yes', 0) > 1.01 and mapped.get('no', 0) > 1.01:
        dest.update({'yes': mapped['yes'], 'no': mapped['no']})


def _put_ou(match, target_key, outcomes, partition_asian=False):
    import re
    grouped = {}
    for outcome in outcomes:
        desc = str(outcome.get('desc') or outcome.get('display') or '')
        odds = _odd_float(outcome)
        if odds <= 1.01:
            continue
        m = re.search(r'(\d+(?:\.\d+)?)', desc)
        if not m:
            continue
        line_val = float(m.group(1))
        line = str(line_val)
        side = 'over' if 'over' in desc.lower() else 'under' if 'under' in desc.lower() else None
        if side:
            grouped.setdefault(line, {})[side] = odds
    for line, row in grouped.items():
        if row.get('over', 0) <= 1.01 or row.get('under', 0) <= 1.01:
            continue
        key = target_key
        if partition_asian:
            key = 'odds_ou' if float(line) % 1.0 == 0.5 else 'odds_asian_ou'
        match[key][line] = {'over': row['over'], 'under': row['under']}
def parse_event(event, tournament_name='', now=None):
    if now is None:
        now = datetime.now()

    home_team = event.get('homeTeamName', '')
    away_team = event.get('awayTeamName', '')
    kickoff = event.get('estimateStartTime', '')
    status = event.get('matchStatus', '')
    event_id = str(event.get('eventId', '') or event.get('id', '') or '')

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
        'status': status,
        'event_id': event_id,
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
        if not is_market_active(market):
            continue

        market_id = str(market.get('id', ''))
        outcomes = market.get('outcomes', [])
        active_outcomes = [o for o in outcomes if is_outcome_active(o)]

        three_way_market_map = {
            '1': 'odds_1x2',
            '60200': 'odds_1x2_one_up',
            '60100': 'odds_1x2_two_up',
            '60': 'odds_fh_1x2',
            '83': 'odds_sh_1x2',
            '162': 'odds_corners_1x2',
            '136': 'odds_bookings_1x2',
        }
        dc_market_map = {'10': 'odds_dc', '63': 'odds_fh_dc', '85': 'odds_sh_dc'}
        ou_market_map = {'68': 'odds_fh_ou', '90': 'odds_sh_ou', '139': 'odds_bookings_ou'}

        if market_id in three_way_market_map and len(active_outcomes) >= 3:
            _put_3way(match[three_way_market_map[market_id]], active_outcomes)

        if market_id in dc_market_map and len(active_outcomes) >= 3:
            _put_dc(match[dc_market_map[market_id]], active_outcomes)

        if market_id in ou_market_map and len(active_outcomes) >= 2:
            _put_ou(match, ou_market_map[market_id], active_outcomes)

        if market_id == '60000' and len(active_outcomes) >= 2:
            _put_gg(match['odds_gg_2plus'], active_outcomes)

        if market_id == '18' and len(active_outcomes) >= 2:
            _put_ou(match, 'odds_ou', active_outcomes, partition_asian=True)

        if market_id == '29' and len(active_outcomes) >= 2:
            _put_gg(match['odds_gg'], active_outcomes)

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
    print(f"\nLIST FOOTBALL.COM GHANA")
    print(f"Total matches fetched: {len(matches)}")
    print(f"Stats With 1X2 odds: {len(with_odds)}")
    print("=" * 50)

    print("\nSample (first 10 matches):")
    for match in with_odds[:10]:
        live_tag = "LIVE" if match.get('is_live') else ""
        print(f"  {live_tag} {match['home_team']} vs "
              f"{match['away_team']} | "
              f"{match['kickoff']} | "
              f"{match['tournament']}")

    if len(with_odds) > 10:
        print(f"\n  ... and {len(with_odds) - 10} more matches")
    print("=" * 50)


def fmt_row(label, val):
    prefix = f"| {label:<16} "
    val_width = 80 - len(prefix) - 2
    return f"{prefix}{val:<{val_width}} |"

def fmt_box_top(title):
    prefix = f"+-- {title} "
    dash_count = 80 - len(prefix) - 1
    return prefix + "-" * dash_count + "+"

def fmt_box_bottom():
    return "+" + "-" * 78 + "+"

def fmt_box_subheading(sub_title):
    content = f"[{sub_title}]"
    return f"| {content:<76} |"

def fmt_box_divider():
    line = "-" * 76
    return f"| {line} |"

def fmt_3way(o):
    if not o or o.get("home") is None or o.get("draw") is None or o.get("away") is None:
        return "N/A"
    return f"Home: {o['home']:<7} | Draw: {o['draw']:<7} | Away: {o['away']}"

def fmt_dc(o):
    if not o or o.get("1x") is None or o.get("12") is None or o.get("x2") is None:
        return "N/A"
    return f"1X: {o['1x']:<8} | 12: {o['12']:<8} | X2: {o['x2']}"

def fmt_gg(o):
    if not o or o.get("yes") is None or o.get("no") is None:
        return "N/A"
    return f"GG (Yes): {o['yes']:<6} | NG (No): {o['no']}"

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
            line_val = f"Over: {over:<8} | Under: {under:<8}"
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
            line_val = f"Over: {over:<8} | Under: {under:<8}"
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
            line_val = f"Over: {over:<8} | Under: {under:<8}"
            rows.append(fmt_row(line_label, line_val))
    if not rows:
        return fmt_row("", empty_msg)
    return "\n".join(rows)

def format_match_text_block(m):
    # Header
    title = f"Football {m['home_team']} vs {m['away_team']}"
    if m.get("is_live"):
        title += " (LIVE)"
    meta = f"League {m['tournament']} | Time {m['kickoff']}"
    
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
    lines.append("=" * w)
    lines.append(f"{title}")
    lines.append(f"{meta}")
    lines.append("=" * w)
    
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


# Use the shared formatter so every football scraper has the same text output.
try:
    from .fb_output_formatter import format_match_text_block
except ImportError:
    from fb_output_formatter import format_match_text_block

def run():
    output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
    os.makedirs(output_dir, exist_ok=True)
    import time as _time
    start = _time.time()
    try:
        matches = asyncio.run(scrape_footballcom())
    except Exception as exc:
        print(f"\nWARNING: Football.com scrape failed: {exc}")
        matches = []

    if not matches:
        print("WARNING: Football.com returned no fresh matches; stale snapshot fallback is disabled.")
    if matches:
        display_matches(matches)

        output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
        os.makedirs(output_dir, exist_ok=True)
        
        with open(os.path.join(output_dir, 'footballcom_odds.json'), 'w', encoding='utf-8') as f:
            json.dump(matches, f, indent=2, ensure_ascii=False)

        with open(os.path.join(output_dir, 'footballcom_matches.txt'), 'w',
                  encoding='utf-8') as f:
            f.write(f"FOOTBALL.COM GHANA - ALL MATCHES\n")
            f.write(f"Generated: "
                    f"{datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}\n")
            f.write(f"Total: {len(matches)} matches\n")
            f.write("=" * 60 + "\n\n")

            for match in matches:
                f.write(format_match_text_block(match))

        print(f"Saved to {os.path.join(output_dir, 'footballcom_odds.json')}")
        print(f"Full list saved to {os.path.join(output_dir, 'footballcom_matches.txt')}")
        print(f"   Open the .txt file to see all {len(matches)} matches!")
        print(f"Scraping completed in {_time.time() - start:.1f}s")
    else:
        print("\nWARNING No matches found")
    return matches


if __name__ == "__main__":
    run()
