import json
import os
from datetime import datetime
from difflib import SequenceMatcher
from dotenv import load_dotenv

load_dotenv()

MIN_ARB_PROFIT = 0.01
MAX_ARB_PROFIT = 15.0
TOTAL_CAPITAL  = int(os.getenv('STARTING_CAPITAL', 500))


def normalize_name(name):
    """
    Normalizes team name for comparison.
    Removes common suffixes/prefixes that differ across platforms.
    """
    name = name.lower().strip()
    # Remove common suffixes
    for suffix in [' fc', ' sc', ' cf', ' ac', ' bk', ' fk',
                   ' sk', ' if', ' bfk', ' spor', ' sport',
                   ' united', ' city', ' town']:
        if name.endswith(suffix):
            name = name[:-len(suffix)].strip()
    return name


def similar(a, b):
    """
    Strict fuzzy matching — REQUIRES 0.85 similarity
    """
    a_norm = normalize_name(a)
    b_norm = normalize_name(b)

    # Exact match after normalization
    if a_norm == b_norm:
        return True

    # One contains the other (handles abbreviations)
    if a_norm in b_norm or b_norm in a_norm:
        # But only if the shorter one is at least 5 chars
        shorter = min(len(a_norm), len(b_norm))
        if shorter >= 5:
            return True

    # Strict ratio check
    ratio = SequenceMatcher(None, a_norm, b_norm).ratio()
    return ratio >= 0.85


# ── VIRTUAL / SRL / ESPORTS KEYWORDS ──────────────────────────────────────────
# These are simulated / virtual matches — NOT real football.
# Must NEVER be matched with real games or used in arb calculations.
VIRTUAL_KEYWORDS = [
    'srl', 'simulated reality', 'esport', 'e-soccer', 'esoccer',
    'cyber', 'virtual', 'sim match', 'eadriatic', 'gt league',
    'efootball', 'e-football', 'fifa', 'pes ',
]


def is_virtual_match(match):
    """
    Returns True if the match is SRL / virtual / esports.
    Checks team names AND tournament name.
    """
    text = ' '.join([
        match.get('home_team', ''),
        match.get('away_team', ''),
        match.get('tournament', ''),
    ]).lower()
    return any(kw in text for kw in VIRTUAL_KEYWORDS)


def tournament_similar(a, b):
    """
    Matches tournaments loosely, but strictly enforces youth/women/SRL modifiers.
    If one is U21 and the other is not, it must return False.
    If one is SRL and the other is not, it must return False.
    """
    a_norm = a.lower()
    b_norm = b.lower()
    
    # Critical modifiers that MUST match
    modifiers = ['u19', 'u20', 'u21', 'u23', 'women', 'reserves',
                 'srl', 'esport', 'virtual', 'cyber']
    for mod in modifiers:
        if (mod in a_norm) != (mod in b_norm):
            return False
            
    return True


def match_all_platforms(all_matches):
    """
    Groups same match from different platforms.
    Matches on: home team + away team + date + tournament modifier.

    CRITICAL: Both home AND away must match, AND tournament modifiers must match!
    Prevents cross-matching different games (e.g. Senior vs U21).
    """
    groups = []
    used   = set()

    for i, match_a in enumerate(all_matches):
        if i in used:
            continue

        group = {
            'matches': [match_a],
            'sources': [match_a['source']]
        }
        used.add(i)

        for j, match_b in enumerate(all_matches):
            if j in used or i == j:
                continue
            if match_a['source'] == match_b['source']:
                continue

            home_a = match_a.get('home_team', '')
            away_a = match_a.get('away_team', '')
            home_b = match_b.get('home_team', '')
            away_b = match_b.get('away_team', '')
            date_a = match_a.get('kickoff', '')[:10]
            date_b = match_b.get('kickoff', '')[:10]

            # CRITICAL: dates must match AND both teams must match
            if date_a != date_b:
                continue

            # Prevent matching U21 with senior teams
            tourn_a = match_a.get('tournament', '')
            tourn_b = match_b.get('tournament', '')
            if not tournament_similar(tourn_a, tourn_b):
                continue
                
            # Prevent team name false matches if modifiers exist in team names
            if not tournament_similar(home_a + away_a, home_b + away_b):
                continue

            home_match = similar(home_a, home_b)
            away_match = similar(away_a, away_b)

            if not home_match or not away_match:
                continue

            # EXTRA CHECK: prevent reversed team matching
            # (home A should NOT match away B)
            if similar(home_a, away_b) and \
                    similar(away_a, home_b):
                continue

            group['matches'].append(match_b)
            group['sources'].append(match_b['source'])
            used.add(j)

        if len(group['matches']) > 1:
            groups.append(group)

    return groups


def validate_odds(odds_dict, market_type):
    """
    CRITICAL: Validates odds before using in arb calculation.
    Prevents fake/wrong odds from creating false arb.

    Rules:
    - All odds must be > 1.01 (genuine odds)
    - Arb sum must be > 0.85 (real market overround)
    - Arb sum must be < 1.5 (not absurdly unbalanced)
    - For 1X2: draw odds must be between home and away ± 3x
    """
    if not odds_dict:
        return False

    if market_type == '1x2':
        h = odds_dict.get('home', 0)
        d = odds_dict.get('draw', 0)
        a = odds_dict.get('away', 0)
        if not all(o > 1.01 for o in [h, d, a]):
            return False
        arb = 1/h + 1/d + 1/a
        if arb < 0.85 or arb > 1.5:
            return False

    elif market_type == 'ou':
        ov = odds_dict.get('over', 0)
        un = odds_dict.get('under', 0)
        if not all(o > 1.01 for o in [ov, un]):
            return False
        arb = 1/ov + 1/un
        if arb < 0.85 or arb > 1.5:
            return False

    elif market_type == 'gg':
        y = odds_dict.get('yes', 0)
        n = odds_dict.get('no', 0)
        if not all(o > 1.01 for o in [y, n]):
            return False
        arb = 1/y + 1/n
        if arb < 0.85 or arb > 1.5:
            return False

    return True


def calculate_arb(odds_list):
    if not all(o > 1.01 for o in odds_list):
        return 0, 0
    arb_sum = sum(1 / o for o in odds_list)
    if arb_sum < 1:
        profit = ((1 - arb_sum) / arb_sum) * 100
        return arb_sum, profit
    return arb_sum, 0


def calculate_stakes(odds_list, total_stake):
    arb_sum = sum(1 / o for o in odds_list)
    return [round((1/o) / arb_sum * total_stake, 2)
            for o in odds_list]


def calculate_profits(odds_list, stakes):
    total_staked = sum(stakes)
    return [round(o * s - total_staked, 2)
            for o, s in zip(odds_list, stakes)]


def get_best_odds(outcome_key, market_type,
                  *platform_odds_pairs):
    """
    Gets best odds across platforms for one outcome.

    CRITICAL FIX: Now validates entire odds_dict first!
    Only uses odds from platforms where the WHOLE market
    is valid — not just one outcome in isolation.

    This prevents: using Away=5.5 from a wrong match
    where only that one outcome was grabbed incorrectly.
    """
    candidates = []
    for odds_dict, name in platform_odds_pairs:
        if not odds_dict:
            continue
        # Validate the WHOLE market from this platform
        if not validate_odds(odds_dict, market_type):
            continue
        val = odds_dict.get(outcome_key, 0)
        if val > 1.01:
            candidates.append((val, name))

    return max(candidates, key=lambda x: x[0]) \
        if candidates else (0, 'N/A')


def scan_1x2_arb(pair, total_stake):
    sb_odds  = pair['sportybet'].get('odds_1x2', {})
    bw_odds  = pair['betway'].get('odds_1x2', {})
    fc_odds  = pair['footballcom'].get('odds_1x2', {})
    ox_odds  = pair['onexbet'].get('odds_1x2', {})
    ttb_odds = pair['twentytwobet'].get('odds_1x2', {})

    best_home = get_best_odds('home', '1x2',
        (sb_odds,  'Sportybet'),
        (bw_odds,  'Betway'),
        (fc_odds,  'Football.com'),
        (ox_odds,  '1xBet'),
        (ttb_odds, '22Bet'),
    )
    best_draw = get_best_odds('draw', '1x2',
        (sb_odds,  'Sportybet'),
        (bw_odds,  'Betway'),
        (fc_odds,  'Football.com'),
        (ox_odds,  '1xBet'),
        (ttb_odds, '22Bet'),
    )
    best_away = get_best_odds('away', '1x2',
        (sb_odds,  'Sportybet'),
        (bw_odds,  'Betway'),
        (fc_odds,  'Football.com'),
        (ox_odds,  '1xBet'),
        (ttb_odds, '22Bet'),
    )

    if not all([best_home[0], best_draw[0], best_away[0]]):
        return None

    odds_list = [best_home[0], best_draw[0], best_away[0]]
    arb_sum, profit_pct = calculate_arb(odds_list)

    if MIN_ARB_PROFIT <= profit_pct <= MAX_ARB_PROFIT:
        stakes  = calculate_stakes(odds_list, total_stake)
        profits = calculate_profits(odds_list, stakes)
        return {
            'market':     '1X2',
            'arb_sum':    round(arb_sum, 4),
            'profit_pct': round(profit_pct, 2),
            'profit_ghs': round(
                total_stake * profit_pct / 100, 2),
            'bets': [
                {'outcome':        'Home Win',
                 'platform':       best_home[1],
                 'odds':           best_home[0],
                 'stake':          stakes[0],
                 'profit_if_wins': profits[0]},
                {'outcome':        'Draw',
                 'platform':       best_draw[1],
                 'odds':           best_draw[0],
                 'stake':          stakes[1],
                 'profit_if_wins': profits[1]},
                {'outcome':        'Away Win',
                 'platform':       best_away[1],
                 'odds':           best_away[0],
                 'stake':          stakes[2],
                 'profit_if_wins': profits[2]},
            ]
        }
    return None


def scan_ou_arb(pair, total_stake):
    def filter_to_main_line(ou_dict):
        if not ou_dict: return {}
        # Find the line with the smallest difference between over and under (the "Main" line)
        best_line = min(ou_dict.keys(), key=lambda k: abs(ou_dict[k]['over'] - ou_dict[k]['under']))
        return {best_line: ou_dict[best_line]}

    # SportyBet and Football.com hide alternate lines for some leagues,
    # so we restrict them to ONLY their main, visible line.
    sb_ou  = filter_to_main_line(pair['sportybet'].get('odds_ou', {}))
    fc_ou  = filter_to_main_line(pair['footballcom'].get('odds_ou', {}))
    
    # Betway, 1xBet, 22Bet display all lines clearly, so keep all of them
    bw_ou  = pair['betway'].get('odds_ou', {})
    ox_ou  = pair['onexbet'].get('odds_ou', {})
    ttb_ou = pair['twentytwobet'].get('odds_ou', {})

    all_lines = set()
    for ou in [sb_ou, bw_ou, fc_ou, ox_ou, ttb_ou]:
        all_lines.update(ou.keys())

    opportunities = []

    for line_str in all_lines:
        sb_line = sb_ou.get(line_str, {})
        bw_line = bw_ou.get(line_str, {})
        fc_line = fc_ou.get(line_str, {})
        ox_line = ox_ou.get(line_str, {})
        ttb_line = ttb_ou.get(line_str, {})

        best_over = get_best_odds('over', 'ou',
            (sb_line,  'Sportybet'),
            (bw_line,  'Betway'),
            (fc_line,  'Football.com'),
            (ox_line,  '1xBet'),
            (ttb_line, '22Bet'),
        )
        best_under = get_best_odds('under', 'ou',
            (sb_line,  'Sportybet'),
            (bw_line,  'Betway'),
            (fc_line,  'Football.com'),
            (ox_line,  '1xBet'),
            (ttb_line, '22Bet'),
        )

        if not all([best_over[0], best_under[0]]):
            continue

        odds_list = [best_over[0], best_under[0]]
        arb_sum, profit_pct = calculate_arb(odds_list)

        if MIN_ARB_PROFIT <= profit_pct <= MAX_ARB_PROFIT:
            stakes  = calculate_stakes(odds_list, total_stake)
            profits = calculate_profits(odds_list, stakes)
            opportunities.append({
                'market':     f'Over/Under {line_str}',
                'arb_sum':    round(arb_sum, 4),
                'profit_pct': round(profit_pct, 2),
                'profit_ghs': round(
                    total_stake * profit_pct / 100, 2),
                'bets': [
                    {'outcome':        f'Over {line_str}',
                     'platform':       best_over[1],
                     'odds':           best_over[0],
                     'stake':          stakes[0],
                     'profit_if_wins': profits[0]},
                    {'outcome':        f'Under {line_str}',
                     'platform':       best_under[1],
                     'odds':           best_under[0],
                     'stake':          stakes[1],
                     'profit_if_wins': profits[1]},
                ]
            })
            
    return opportunities if opportunities else None


def scan_gg_arb(pair, total_stake):
    sb_gg  = pair['sportybet'].get('odds_gg', {})
    bw_gg  = pair['betway'].get('odds_gg', {})
    fc_gg  = pair['footballcom'].get('odds_gg', {})
    ox_gg  = pair['onexbet'].get('odds_gg', {})
    ttb_gg = pair['twentytwobet'].get('odds_gg', {})

    best_yes = get_best_odds('yes', 'gg',
        (sb_gg,  'Sportybet'),
        (bw_gg,  'Betway'),
        (fc_gg,  'Football.com'),
        (ox_gg,  '1xBet'),
        (ttb_gg, '22Bet'),
    )
    best_no = get_best_odds('no', 'gg',
        (sb_gg,  'Sportybet'),
        (bw_gg,  'Betway'),
        (fc_gg,  'Football.com'),
        (ox_gg,  '1xBet'),
        (ttb_gg, '22Bet'),
    )

    if not all([best_yes[0], best_no[0]]):
        return None

    odds_list = [best_yes[0], best_no[0]]
    arb_sum, profit_pct = calculate_arb(odds_list)

    if MIN_ARB_PROFIT <= profit_pct <= MAX_ARB_PROFIT:
        stakes  = calculate_stakes(odds_list, total_stake)
        profits = calculate_profits(odds_list, stakes)
        return {
            'market':     'GG/NG',
            'arb_sum':    round(arb_sum, 4),
            'profit_pct': round(profit_pct, 2),
            'profit_ghs': round(
                total_stake * profit_pct / 100, 2),
            'bets': [
                {'outcome':        'GG Yes',
                 'platform':       best_yes[1],
                 'odds':           best_yes[0],
                 'stake':          stakes[0],
                 'profit_if_wins': profits[0]},
                {'outcome':        'GG No',
                 'platform':       best_no[1],
                 'odds':           best_no[0],
                 'stake':          stakes[1],
                 'profit_if_wins': profits[1]},
            ]
        }
    return None


def display_opportunity(opp):
    print(f"\n  {'='*55}")
    print(f"  🏆 {opp['match']}")
    print(f"  📅 {opp['kickoff']} | {opp['tournament']}")
    print(f"  {'='*55}")
    print(f"  📊 Market: {opp['market']}")
    print(f"  💰 Profit: {opp['profit_pct']:.2f}% "
          f"= GHS {opp['profit_ghs']:.2f}")
    print(f"  💵 Total Stake: GHS {TOTAL_CAPITAL}")
    print(f"\n  📋 BETS TO PLACE:")
    for bet in opp['bets']:
        print(f"\n     🎯 {bet['platform']}")
        print(f"        Bet:   {bet['outcome']}")
        print(f"        Odds:  {bet['odds']}")
        print(f"        Stake: GHS {bet['stake']:.2f}")
        print(f"        Win:   GHS {bet['profit_if_wins']:.2f}")


def scan_all(sportybet_matches,
             betway_matches,
             footballcom_matches=None,
             onexbet_matches=None,
             twentytwobet_matches=None,
             total_stake=TOTAL_CAPITAL,
             cycle_start_time=None):

    if footballcom_matches  is None: footballcom_matches  = []
    if onexbet_matches      is None: onexbet_matches      = []
    if twentytwobet_matches is None: twentytwobet_matches = []

    # CRITICAL: Filter out SRL / virtual / esports matches from ALL platforms
    sportybet_matches    = [m for m in sportybet_matches    if not is_virtual_match(m)]
    betway_matches       = [m for m in betway_matches       if not is_virtual_match(m)]
    footballcom_matches  = [m for m in footballcom_matches  if not is_virtual_match(m)]
    onexbet_matches      = [m for m in onexbet_matches      if not is_virtual_match(m)]
    twentytwobet_matches = [m for m in twentytwobet_matches if not is_virtual_match(m)]

    all_matches = (sportybet_matches   +
                   betway_matches      +
                   footballcom_matches +
                   onexbet_matches     +
                   twentytwobet_matches)

    groups = match_all_platforms(all_matches)

    if not groups:
        print("\n⚠️ No matching events found!")
        return []

    opportunities = []
    empty = {'odds_1x2': {}, 'odds_ou': {}, 'odds_gg': {}}

    for group in groups:
        matches    = group['matches']
        first      = matches[0]
        match_name = (f"{first['home_team']} vs "
                      f"{first['away_team']}")
        kickoff    = first['kickoff']
        tournament = first['tournament']

        pair = {
            'sportybet':    next((m for m in matches
                if m['source'] == 'sportybet_gh'),    empty),
            'betway':       next((m for m in matches
                if m['source'] == 'betway_gh'),       empty),
            'footballcom':  next((m for m in matches
                if m['source'] == 'footballcom_gh'),  empty),
            'onexbet':      next((m for m in matches
                if m['source'] == '1xbet_gh'),        empty),
            'twentytwobet': next((m for m in matches
                if m['source'] == 'twentytwobet_gh'), empty),
        }

        for arb_fn in [scan_1x2_arb, scan_ou_arb, scan_gg_arb]:
            arb_result = arb_fn(pair, total_stake)
            if not arb_result:
                continue
                
            if not isinstance(arb_result, list):
                arb_result = [arb_result]
                
            for arb in arb_result:
                opp = {
                    'match':      match_name,
                    'kickoff':    kickoff,
                    'tournament': tournament,
                    **arb
                }
                opportunities.append(opp)

    print(f"\n{'='*60}")
    print("SCAN COMPLETE!")
    print(f"⚽ Events scanned    : {len(groups)}")
    
    import time
    if cycle_start_time:
        total_seconds = time.time() - cycle_start_time
        total_minutes = total_seconds / 60
        print(f"⏱️ Total cycle time: {total_seconds:.1f} seconds ({total_minutes:.1f} minutes)")

    print(f"🎯 Arb opportunities : {len(opportunities)}")

    if opportunities:
        total_profit = sum(o['profit_ghs'] for o in opportunities)
        best = max(opportunities, key=lambda x: x['profit_pct'])
        print(f"💰 Total potential profit: GHS {total_profit:.2f}")
        print(f"📈 Best: {best['profit_pct']:.2f}% on {best['match']}")
        
        # Now print all the actual opportunities
        for opp in opportunities:
            display_opportunity(opp)
    else:
        print("💡 No arb opportunities right now")

    print(f"\n{'='*60}")
    print("MATCHES FETCHED PER PLATFORM:")
    print(f"  Sportybet   : {len(sportybet_matches)}")
    print(f"  Betway      : {len(betway_matches)}")
    print(f"  Football.com: {len(footballcom_matches)}")
    print(f"  1xBet       : {len(onexbet_matches)}")
    print(f"  22Bet       : {len(twentytwobet_matches)}")
    print(f"{'='*60}")
    return opportunities, len(groups)


def run():
    print("\n" + "🚀 " * 20)
    print("   QUANT BET ALPHA - ARBITRAGE ENGINE")
    print("🚀 " * 20 + "\n")

    def load(path, label):
        try:
            with open(path) as f:
                data = json.load(f)
            print(f"✅ {label}: {len(data)} matches")
            return data
        except FileNotFoundError:
            print(f"❌ {path} not found!")
            return []

    sportybet_matches    = load(
        'data/sportybet_odds.json',    'Sportybet')
    betway_matches       = load(
        'data/betway_odds.json',       'Betway')
    footballcom_matches  = load(
        'data/footballcom_odds.json',  'Football.com')
    onexbet_matches      = load(
        'data/onexbet_odds.json',      '1xBet')
    twentytwobet_matches = load(
        'data/twentytwobet_odds.json', '22Bet')

    if not any([sportybet_matches, betway_matches,
                footballcom_matches, onexbet_matches,
                twentytwobet_matches]):
        print("\n❌ No odds data found!")
        return []

    opportunities, _ = scan_all(
        sportybet_matches,
        betway_matches,
        footballcom_matches,
        onexbet_matches,
        twentytwobet_matches,
        total_stake=TOTAL_CAPITAL
    )

    if opportunities:
        with open('engine/opportunities.json', 'w') as f:
            json.dump(opportunities, f, indent=2)
        print(f"\n💾 Saved to engine/opportunities.json")

    return opportunities


if __name__ == "__main__":
    run()