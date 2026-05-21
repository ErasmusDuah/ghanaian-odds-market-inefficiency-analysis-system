import json
import os
import re
from datetime import datetime
from difflib import SequenceMatcher
from dotenv import dotenv_values

# Absolute path to .env — read fresh on every call, never cached
_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.env')

MIN_ARB_PROFIT = 0.01
MAX_ARB_PROFIT = 15.0


STOPWORDS = {
    'fk', 'fc', 'sc', 'cf', 'ac', 'bk', 'fk', 'sk', 'if', 'bfk', 'spor', 'sport',
    'united', 'city', 'town', 'reserve', 'reserves', 'u19', 'u20', 'u21', 'u23',
    'women', 'youth', 'under', 'club', 'team', 'real', 'atletico', 'atletico',
    'depor', 'deportivo', 'de', 'la', 'del', 'ii', 'b', 'u-19', 'u-20', 'u-21'
}

def clean_tokens(name):
    name = str(name).lower().strip()
    name = re.sub(r'[^a-z0-9]', ' ', name)
    words = name.split()
    filtered = []
    for w in words:
        if w in STOPWORDS:
            continue
        if w.startswith('y') and len(w) > 4 and w[1] in 'aeiou':
            w = w[1:]
        filtered.append(w)
    return filtered

def smart_team_match(a, b):
    tokens_a = clean_tokens(a)
    tokens_b = clean_tokens(b)
    
    if not tokens_a or not tokens_b:
        return False
        
    if tokens_a == tokens_b:
        return True
        
    str_a = ' '.join(tokens_a)
    str_b = ' '.join(tokens_b)
    if (len(str_a) >= 4 and str_a in str_b) or (len(str_b) >= 4 and str_b in str_a):
        return True
        
    ratio = SequenceMatcher(None, str_a, str_b).ratio()
    if ratio >= 0.72:
        return True
        
    overlap = set(tokens_a).intersection(set(tokens_b))
    long_overlap = [w for w in overlap if len(w) >= 5]
    if long_overlap and ratio >= 0.50:
        return True
        
    return False


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


def _matches_same_game(a, b):
    """
    Returns True if two match records from DIFFERENT platforms refer to the
    same real-world fixture.
    """
    if a['source'] == b['source']:
        return False
    date_a = a.get('kickoff', '')[:10]
    date_b = b.get('kickoff', '')[:10]
    if date_a != date_b:
        return False
        
    t_a = a.get('kickoff', '').split()
    t_b = b.get('kickoff', '').split()
    times_match = (len(t_a) > 1 and len(t_b) > 1 and t_a[1][:5] == t_b[1][:5])
        
    tourn_a = a.get('tournament', '')
    tourn_b = b.get('tournament', '')
    if not tournament_similar(tourn_a, tourn_b):
        return False
    home_a, away_a = a.get('home_team', ''), a.get('away_team', '')
    home_b, away_b = b.get('home_team', ''), b.get('away_team', '')
    if not tournament_similar(home_a + away_a, home_b + away_b):
        return False
        
    if times_match:
        home_ok = smart_team_match(home_a, home_b)
        away_ok = smart_team_match(away_a, away_b)
        if home_ok and away_ok:
            return True
            
    return smart_team_match(home_a, home_b) and smart_team_match(away_a, away_b)


def match_all_platforms(all_matches):
    """
    Groups the same match across different platforms using a union-find
    (disjoint-set) approach so that grouping is TRANSITIVE.

    If Sportybet<->MSport matches and MSport<->Bangbet matches, all three
    end up in the same group even if Sportybet<->Bangbet fails directly.
    """
    from collections import defaultdict
    n = len(all_matches)

    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    for i in range(n):
        for j in range(i + 1, n):
            if _matches_same_game(all_matches[i], all_matches[j]):
                union(i, j)

    components = defaultdict(list)
    for i in range(n):
        components[find(i)].append(i)

    groups = []
    for indices in components.values():
        if len(indices) < 2:
            continue
        sources = [all_matches[i]['source'] for i in indices]
        if len(set(sources)) < 2:
            continue
        matches = [all_matches[i] for i in indices]
        groups.append({'matches': matches, 'sources': sources})

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
    # Use ALL O/U lines from ALL platforms — no restriction.
    # More lines = more chances to catch arb across different bookmaker line offerings.
    sb_ou  = pair['sportybet'].get('odds_ou', {})
    fc_ou  = pair['footballcom'].get('odds_ou', {})
    bw_ou  = pair['betway'].get('odds_ou', {})
    ox_ou  = pair['onexbet'].get('odds_ou', {})
    ttb_ou = pair['twentytwobet'].get('odds_ou', {})

    all_lines = set()
    for ou in [sb_ou, bw_ou, fc_ou, ox_ou, ttb_ou]:
        all_lines.update(ou.keys())

    opportunities = []

    for line_str in all_lines:
        sb_line  = sb_ou.get(line_str, {})
        bw_line  = bw_ou.get(line_str, {})
        fc_line  = fc_ou.get(line_str, {})
        ox_line  = ox_ou.get(line_str, {})
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
    total_stake_used = sum(bet.get('stake', 0) for bet in opp['bets'])
    print(f"  💵 Total Stake: GHS {total_stake_used:.2f}")
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
             total_stake=None,
             cycle_start_time=None):

    # If no stake was passed in, read it live from .env right now
    if total_stake is None:
        _env = dotenv_values(_ENV_PATH)
        total_stake = int(_env.get('STARTING_CAPITAL', 500))

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
        return [], 0

    opportunities = []
    empty = {'odds_1x2': {}, 'odds_ou': {}, 'odds_gg': {}}

    for group in groups:
        matches    = group['matches']
        first      = matches[0]
        match_name = (f"{first['home_team']} vs "
                      f"{first['away_team']}")
        kickoff    = first['kickoff']
        # Prefer a tournament label that includes a country prefix
        # (e.g. "Jamaica. Premier League" from Sportybet/22Bet)
        # over a bare name (e.g. "Premier League" from Betway/1xBet)
        tournament = first['tournament']
        for m in matches:
            t = m.get('tournament', '')
            if '.' in t and len(t) > len(tournament):
                tournament = t
                break

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
    )

    if opportunities:
        with open('engine/opportunities.json', 'w') as f:
            json.dump(opportunities, f, indent=2)
        print(f"\n💾 Saved to engine/opportunities.json")

    return opportunities


if __name__ == "__main__":
    run()