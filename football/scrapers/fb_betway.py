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
    '&marketTypes=%5BDouble%20Chance%5D'
    '&marketTypes=%5BBoth%20Teams%20To%20Score%5D'
    '&marketTypes=%5BTotal%20Goals%5D'
)

BETWAY_HOME_URL = 'https://www.betway.com.gh/sport/soccer/'

BETWAY_EVENT_MARKETS_URL = (
    'https://www.betway.com.gh/sportsapi/br/v1/MarketGroupings/'
    'MarketGroupNamesAndMarketsForEvent?eventId={event_id}'
    '&marketGroupId=%20&countryCode=GH&cultureCode=en-US'
    '&skip=0&take=200&isBuildABetOnly=false&searchQuery='
)


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
        print(f"  ERROR Error: {e}")
    return None


async def fetch_event_markets(session, event_id, headers):
    """Fetches Betway More Bets markets for one event."""
    if not event_id:
        return None
    url = BETWAY_EVENT_MARKETS_URL.format(event_id=event_id)
    url = f"{url}&_t={int(_time.time() * 1000)}"
    try:
        async with session.get(
                url, headers=headers,
                timeout=aiohttp.ClientTimeout(total=15)
        ) as response:
            if response.status == 200:
                return await response.json()
    except Exception as e:
        print(f"  Error fetching event {event_id} markets: {e}")
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

        print("Fetching today's matches...")
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

            print(f"  OK Skip {skip}: {len(valid_events)} matches "
                  f"(Total: {len(all_events)})")

            if stop or len(valid_events) < len(events):
                break

            skip += 100

        if all_events:
            print("[INFO] Fetching Betway More Bets market details...")
            detail_tasks = [
                fetch_event_markets(session, event.get('eventId'), headers)
                for event in all_events
            ]
            details = await asyncio.gather(*detail_tasks)
            detail_count = 0
            for event, detail in zip(all_events, details):
                if not isinstance(detail, dict):
                    continue
                event_id = event.get('eventId')
                markets = detail.get('marketsInGroup') or []
                outcomes = detail.get('outcomes') or []
                prices = detail.get('prices') or []
                for market in markets:
                    market.setdefault('eventId', event_id)
                if markets:
                    detail_count += 1
                all_markets.extend(markets)
                all_outcomes.extend(outcomes)
                all_prices.extend(prices)
            print(
                f"  [INFO] More Bets details fetched: "
                f"{detail_count}/{len(all_events)} events"
            )

    print(f"\nTotal events fetched: {len(all_events)}")

    raw_data = {
        'events': all_events,
        'markets': all_markets,
        'outcomes': all_outcomes,
        'prices': all_prices
    }

    return parse_betway_data(raw_data, today, tomorrow)


def _flag_false(value):
    return value is False or str(value).strip().lower() in {'false', '0', 'no'}


def _flag_true(value):
    return value is True or str(value).strip().lower() in {'true', '1', 'yes'}


def _event_is_bettable(event):
    if _flag_false(event.get('isActive')) or _flag_false(event.get('shouldDisplay')):
        return False
    if _flag_true(event.get('isSuspended')) or _flag_true(event.get('isFinished')):
        return False
    if _flag_true(event.get('isOutright')) or _flag_true(event.get('isLive')):
        return False
    if str(event.get('sportId', 'soccer')).lower() != 'soccer':
        return False
    state = event.get('gameStateTimeScore') or {}
    comments = str(state.get('comments') or '').strip().lower()
    if comments and comments not in {'notstarted', 'not started'}:
        return False
    return True


def _market_is_bettable(market):
    if _flag_false(market.get('isActive')) or _flag_false(market.get('shouldDisplay')):
        return False
    if _flag_true(market.get('isSuspended')):
        return False
    return True


def _outcome_is_bettable(outcome):
    if _flag_false(outcome.get('isActive')) or _flag_false(outcome.get('shouldDisplay')):
        return False
    if _flag_false(outcome.get('isTradingActive')):
        return False
    if _flag_true(outcome.get('isSuspended')):
        return False
    return True


def _outcome_text(outcome):
    return ' '.join(str(outcome.get(key) or '') for key in ('name', 'displayName', 'description')).lower()


def _dc_key(outcome, home_team='', away_team=''):
    outcome_id = str(outcome.get('outcomeId') or '')
    if outcome_id.endswith('9'):
        return '1x'
    if outcome_id.endswith('10'):
        return '12'
    if outcome_id.endswith('11'):
        return 'x2'

    text = _outcome_text(outcome)
    compact = text.replace(' ', '').replace('/', '').replace('-', '')
    home_norm = _team_norm(home_team)
    away_norm = _team_norm(away_team)
    text_norm = _team_norm(text)

    if home_norm and away_norm:
        has_home = home_norm in text_norm
        has_away = away_norm in text_norm
        has_draw = 'draw' in text_norm
        if has_home and has_draw and not has_away:
            return '1x'
        if has_home and has_away and not has_draw:
            return '12'
        if has_away and has_draw and not has_home:
            return 'x2'

    if '1x' in compact or 'homedraw' in compact or 'homeordraw' in compact or 'drawhome' in compact:
        return '1x'
    if '12' in compact or 'homeaway' in compact or 'homeoraway' in compact or 'awayhome' in compact:
        return '12'
    if 'x2' in compact or 'drawaway' in compact or 'draworaway' in compact or 'awaydraw' in compact:
        return 'x2'
    return None


def _odd_price(price_map, outcome):
    try:
        return float(price_map.get(outcome.get('outcomeId'), 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def _market_text(market):
    return ' '.join(str(market.get(key) or '') for key in ('name', 'displayName', 'marketTypeCName')).lower()


def _line_from_market(market):
    import re
    text = ' '.join(str(market.get(key) or '') for key in ('marketId', 'displayName', 'name'))
    match = re.search(r'total=(\d+(?:\.\d+)?)', text, re.I)
    if not match:
        match = re.search(r'\((\d+(?:\.\d+)?)\)', text)
    if not match:
        return None
    try:
        return str(float(match.group(1)))
    except ValueError:
        return None


def _put_price_3way(dest, outcomes, price_map, home_team='', away_team=''):
    by_key = {}
    home_norm = _team_norm(home_team)
    away_norm = _team_norm(away_team)
    for outcome in outcomes:
        labels = [str(outcome.get(key) or '').strip().lower() for key in ('name', 'displayName', 'description')]
        labels = [label for label in labels if label]
        compact_labels = [_team_norm(label) for label in labels]
        price = _odd_price(price_map, outcome)
        if price <= 1.01:
            continue
        if any(label in {'x', 'draw'} for label in labels):
            by_key['draw'] = price
        elif home_norm and any(label == home_norm for label in compact_labels):
            by_key['home'] = price
        elif away_norm and any(label == away_norm for label in compact_labels):
            by_key['away'] = price

    if all(by_key.get(key, 0) > 1.01 for key in ('home', 'draw', 'away')):
        dest.update({'home': by_key['home'], 'draw': by_key['draw'], 'away': by_key['away']})


def _team_norm(value):
    import re
    return re.sub(r'[^a-z0-9]+', '', str(value or '').lower())


def _market_label(market, key):
    return str(market.get(key) or '').strip().lower()


def _is_exact_full_time_1x2(market):
    name = _market_label(market, 'name')
    display = _market_label(market, 'displayName')
    cname = _market_label(market, 'marketTypeCName')
    text = _market_text(market)
    if '&' in text or 'half' in text or 'total' in text or 'both teams' in text:
        return False
    return name in {'[win/draw/win]', '1x2'} or display == '1x2' or cname == 'win-draw-win'


def _is_exact_period_1x2(market, period):
    expected = '1st half - 1x2' if period == 1 else '2nd half - 1x2'
    name = _market_label(market, 'name')
    display = _market_label(market, 'displayName')
    cname = _market_label(market, 'marketTypeCName')
    text = _market_text(market)
    if '&' in text or 'total' in text or 'both teams' in text:
        return False
    return display == expected or cname == expected or name == ('[1st half] - [win/draw/win]' if period == 1 else '[2nd half] - [win/draw/win]')


def _is_exact_full_time_dc(market):
    name = _market_label(market, 'name')
    display = _market_label(market, 'displayName')
    cname = _market_label(market, 'marketTypeCName')
    text = _market_text(market)
    if '&' in text or 'half' in text:
        return False
    return name == '[double chance]' or display == 'double chance' or cname == 'double-chance'


def _is_exact_period_dc(market, period):
    expected = '1st half - double chance' if period == 1 else '2nd half - double chance'
    text = _market_text(market)
    if '&' in text:
        return False
    return _market_label(market, 'displayName') == expected or _market_label(market, 'marketTypeCName') == expected


def _put_price_dc(dest, outcomes, price_map, home_team='', away_team=''):
    by_key = {}
    for outcome in outcomes:
        key = _dc_key(outcome, home_team, away_team)
        if key is None:
            continue
        price = _odd_price(price_map, outcome)
        if price > 1.01:
            by_key[key] = price
    if all(by_key.get(key, 0) > 1.01 for key in ('1x', '12', 'x2')):
        dest.update({'1x': by_key['1x'], '12': by_key['12'], 'x2': by_key['x2']})


def _put_price_ou(dest, market, outcomes, price_map):
    line = _line_from_market(market)
    if not line:
        return
    row = {}
    for outcome in outcomes:
        name = _outcome_text(outcome)
        price = _odd_price(price_map, outcome)
        if price <= 1.01:
            continue
        if 'over' in name:
            row['over'] = price
        elif 'under' in name:
            row['under'] = price
    if row.get('over', 0) > 1.01 and row.get('under', 0) > 1.01:
        dest[line] = {'over': row['over'], 'under': row['under']}


def _is_full_time_total_market(market):
    name = str(market.get('name') or '').strip().lower()
    display = str(market.get('displayName') or '').strip().lower()
    market_id = str(market.get('marketId') or '').lower()
    return (
        '[total goals]' in name
        or (name == 'total' and display.startswith('total ('))
        or ('18total=' in market_id and display.startswith('total ('))
    )


def _is_exact_btts(market):
    name = _market_label(market, 'name')
    display = _market_label(market, 'displayName')
    cname = _market_label(market, 'marketTypeCName')
    text = _market_text(market)
    if '&' in text or 'half' in text:
        return False
    return name == '[both teams to score]' or display == 'both teams to score' or cname == 'both-teams-to-score'


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
    seen_outcomes_by_market = {}
    for outcome in outcomes:
        market_id = outcome.get('marketId')
        if market_id:
            outcome_key = outcome.get('outcomeId') or (outcome.get('name'), outcome.get('displayName'), outcome.get('index'))
            seen = seen_outcomes_by_market.setdefault(market_id, set())
            if outcome_key in seen:
                continue
            seen.add(outcome_key)
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

        # Keep only prematch events Betway marks as displayable and bettable.
        if not _event_is_bettable(event):
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
            'source_event_id': str(event_id),
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
        visible_child_market_ids = {
            str(m.get('marketId'))
            for m in event_markets
            if _market_is_bettable(m) and not _flag_true(m.get('isSquashedParent'))
        }

        for market in event_markets:
            market_name = market.get('name', '').lower()
            market_id = market.get('marketId', '')
            market_text = _market_text(market)

            # Skip markets that Betway marks hidden, inactive, or suspended.
            if not _market_is_bettable(market):
                continue

            market_outcomes = outcomes_by_market.get(
                market_id, [])

            if not market_outcomes:
                continue
            
            # Filter out suspended/inactive outcomes
            market_outcomes = [
                o for o in market_outcomes
                if _outcome_is_bettable(o)
            ]
            
            if not market_outcomes:
                continue


            if '1x2 (1up)' in market_text:
                _put_price_3way(match['odds_1x2_one_up'], market_outcomes, price_map, home_team, away_team)

            if '1x2 (2up)' in market_text:
                _put_price_3way(match['odds_1x2_two_up'], market_outcomes, price_map, home_team, away_team)

            if _is_exact_period_1x2(market, 1):
                _put_price_3way(match['odds_fh_1x2'], market_outcomes, price_map, home_team, away_team)

            if _is_exact_period_1x2(market, 2):
                _put_price_3way(match['odds_sh_1x2'], market_outcomes, price_map, home_team, away_team)

            if _is_exact_period_dc(market, 1):
                _put_price_dc(match['odds_fh_dc'], market_outcomes, price_map, home_team, away_team)

            if _is_exact_period_dc(market, 2):
                _put_price_dc(match['odds_sh_dc'], market_outcomes, price_map, home_team, away_team)

            if '1st half - total' in market_text and ' total (' in market_text:
                _put_price_ou(match['odds_fh_ou'], market, market_outcomes, price_map)

            if '2nd half - total' in market_text and ' total (' in market_text:
                _put_price_ou(match['odds_sh_ou'], market, market_outcomes, price_map)

            # 1X2
            if _is_exact_full_time_1x2(market):
                _put_price_3way(match['odds_1x2'], market_outcomes, price_map, home_team, away_team)


            # Double Chance
            if _is_exact_full_time_dc(market):
                _put_price_dc(match['odds_dc'], market_outcomes, price_map, home_team, away_team)

            # Over/Under dynamically. Betway sends hidden squashed lines on
            # the parent market; only keep outcomes whose original child market
            # is visible on the event page.
            if _is_full_time_total_market(market):
                import re
                allowed_original_ids = set()
                if _flag_true(market.get('isSquashedParent')):
                    allowed_original_ids = {
                        str(mid) for mid in market.get('squashedMarketIds', [])
                        if str(mid) in visible_child_market_ids
                    }
                    default_line = market.get('defaultLine')
                    if default_line and str(default_line) in visible_child_market_ids:
                        allowed_original_ids.add(str(default_line))
                    if not allowed_original_ids:
                        continue

                for o in market_outcomes:
                    original_market_id = str(o.get('originalMarketId') or o.get('marketId') or '')
                    if allowed_original_ids and original_market_id not in allowed_original_ids:
                        continue
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
                
                # Detail endpoint full-time Total lines are already unsquashed.
                if not _flag_true(market.get('isSquashedParent')):
                    _put_price_ou(
                        match['odds_ou'] if (_line_from_market(market) and float(_line_from_market(market)) % 1.0 == 0.5) else match['odds_asian_ou'],
                        market,
                        market_outcomes,
                        price_map,
                    )

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
            if _is_exact_btts(market):
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
        print(f"Scraping completed in {_time.time() - start:.1f}s")
    else:
        print("WARNING No matches found")

    return matches


if __name__ == "__main__":
    run()
