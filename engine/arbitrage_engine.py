import json
from datetime import datetime
from difflib import SequenceMatcher


# ============================================================
# QUANT BET ALPHA - ARBITRAGE ENGINE
# ============================================================
# Compares odds from Sportybet, Betway and Football.com Ghana
# Finds guaranteed profit opportunities
# Applies optimal stake sizing
# ============================================================

MIN_ARB_PROFIT = 0.1
MAX_ARB_PROFIT = 15.0
TOTAL_CAPITAL = 500


def similar(a, b):
    """
    Checks how similar two team names are

    QUANT CONCEPT - FUZZY MATCHING:
    =================================
    Sportybet: "Man City"
    Betway: "Manchester City"
    Football.com: "Manchester City FC"

    All same team — we need to match them!
    SequenceMatcher gives similarity 0.0-1.0
    We use 0.6 threshold = 60% similar
    """
    a = a.lower().strip()
    b = b.lower().strip()

    if a == b:
        return True
    if a in b or b in a:
        return True

    ratio = SequenceMatcher(None, a, b).ratio()
    return ratio >= 0.6


def match_all_platforms(all_matches):
    """
    Groups same match from different platforms together

    QUANT CONCEPT - DATA JOINING:
    ================================
    Like SQL JOIN across 3 tables
    Match on: home team + away team + date

    Groups all platform versions of same
    match together for comparison
    """

    groups = []
    used = set()

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

            if date_a == date_b and \
                    similar(home_a, home_b) and \
                    similar(away_a, away_b):
                group['matches'].append(match_b)
                group['sources'].append(match_b['source'])
                used.add(j)

        if len(group['matches']) > 1:
            groups.append(group)

    return groups


def calculate_arb(odds_list):
    """
    Calculates arbitrage percentage

    QUANT CONCEPT - ARBITRAGE FORMULA:
    ====================================
    Arb Sum = 1/odds1 + 1/odds2 + ... + 1/oddsN

    If Arb Sum < 1 → ARBITRAGE EXISTS!
    Profit % = (1 - Arb Sum) / Arb Sum × 100

    Example:
    odds = [2.50, 4.50, 3.20]
    sum = 0.400 + 0.222 + 0.313 = 0.935
    Profit = (1-0.935)/0.935 × 100 = 6.95%!
    """
    if not all(o > 0 for o in odds_list):
        return 0, 0

    arb_sum = sum(1 / o for o in odds_list)
    if arb_sum < 1:
        profit = ((1 - arb_sum) / arb_sum) * 100
        return arb_sum, profit

    return arb_sum, 0


def calculate_stakes(odds_list, total_stake):
    """
    Calculates optimal stake for each outcome

    QUANT CONCEPT - OPTIMAL STAKE SIZING:
    ========================================
    Stake_i = (1/odds_i) / arb_sum × total_stake

    Ensures EQUAL profit regardless of outcome!
    """
    arb_sum = sum(1 / o for o in odds_list)
    stakes = []
    for odds in odds_list:
        stake = (1 / odds) / arb_sum * total_stake
        stakes.append(round(stake, 2))
    return stakes


def calculate_profits(odds_list, stakes):
    """Calculates profit for each possible outcome"""
    total_staked = sum(stakes)
    profits = []
    for odds, stake in zip(odds_list, stakes):
        payout = odds * stake
        profit = payout - total_staked
        profits.append(round(profit, 2))
    return profits


def get_best_odds(outcome_key, *platform_odds_pairs):
    """
    Gets best odds across all platforms for one outcome

    Example:
    get_best_odds('home',
        (sb_odds, 'Sportybet'),
        (bw_odds, 'Betway'),
        (fc_odds, 'Football.com')
    )
    """
    candidates = []
    for odds_dict, platform_name in platform_odds_pairs:
        if odds_dict and odds_dict.get(outcome_key, 0) > 0:
            candidates.append(
                (odds_dict.get(outcome_key, 0), platform_name)
            )

    if not candidates:
        return 0, 'N/A'

    return max(candidates, key=lambda x: x[0])


def scan_1x2_arb(pair, total_stake):
    """
    Scans 1X2 market for arbitrage across all 3 platforms

    QUANT CONCEPT - BEST EXECUTION:
    ==================================
    For each outcome take the HIGHEST odds
    available across ALL platforms

    Home win → best of Sportybet/Betway/Football.com
    Draw → best of Sportybet/Betway/Football.com
    Away win → best of Sportybet/Betway/Football.com

    This maximizes potential profit!
    Same as best execution in stock trading
    """

    sb_odds = pair['sportybet'].get('odds_1x2', {})
    bw_odds = pair['betway'].get('odds_1x2', {})
    fc_odds = pair['footballcom'].get('odds_1x2', {})

    best_home = get_best_odds('home',
        (sb_odds, 'Sportybet'),
        (bw_odds, 'Betway'),
        (fc_odds, 'Football.com')
    )
    best_draw = get_best_odds('draw',
        (sb_odds, 'Sportybet'),
        (bw_odds, 'Betway'),
        (fc_odds, 'Football.com')
    )
    best_away = get_best_odds('away',
        (sb_odds, 'Sportybet'),
        (bw_odds, 'Betway'),
        (fc_odds, 'Football.com')
    )

    if not all([best_home[0], best_draw[0], best_away[0]]):
        return None

    odds_list = [best_home[0], best_draw[0], best_away[0]]
    arb_sum, profit_pct = calculate_arb(odds_list)

    if MIN_ARB_PROFIT <= profit_pct <= MAX_ARB_PROFIT:
        stakes = calculate_stakes(odds_list, total_stake)
        profits = calculate_profits(odds_list, stakes)

        return {
            'market': '1X2',
            'arb_sum': round(arb_sum, 4),
            'profit_pct': round(profit_pct, 2),
            'profit_ghs': round(
                total_stake * profit_pct / 100, 2),
            'bets': [
                {
                    'outcome': 'Home Win',
                    'platform': best_home[1],
                    'odds': best_home[0],
                    'stake': stakes[0],
                    'profit_if_wins': profits[0]
                },
                {
                    'outcome': 'Draw',
                    'platform': best_draw[1],
                    'odds': best_draw[0],
                    'stake': stakes[1],
                    'profit_if_wins': profits[1]
                },
                {
                    'outcome': 'Away Win',
                    'platform': best_away[1],
                    'odds': best_away[0],
                    'stake': stakes[2],
                    'profit_if_wins': profits[2]
                }
            ]
        }

    return None


def scan_ou_arb(pair, total_stake):
    """Scans Over/Under 2.5 market for arbitrage"""

    sb_ou = pair['sportybet'].get('odds_ou', {})
    bw_ou = pair['betway'].get('odds_ou', {})
    fc_ou = pair['footballcom'].get('odds_ou', {})

    best_over = get_best_odds('over',
        (sb_ou, 'Sportybet'),
        (bw_ou, 'Betway'),
        (fc_ou, 'Football.com')
    )
    best_under = get_best_odds('under',
        (sb_ou, 'Sportybet'),
        (bw_ou, 'Betway'),
        (fc_ou, 'Football.com')
    )

    if not all([best_over[0], best_under[0]]):
        return None

    odds_list = [best_over[0], best_under[0]]
    arb_sum, profit_pct = calculate_arb(odds_list)

    if MIN_ARB_PROFIT <= profit_pct <= MAX_ARB_PROFIT:
        stakes = calculate_stakes(odds_list, total_stake)
        profits = calculate_profits(odds_list, stakes)

        return {
            'market': 'Over/Under 2.5',
            'arb_sum': round(arb_sum, 4),
            'profit_pct': round(profit_pct, 2),
            'profit_ghs': round(
                total_stake * profit_pct / 100, 2),
            'bets': [
                {
                    'outcome': 'Over 2.5',
                    'platform': best_over[1],
                    'odds': best_over[0],
                    'stake': stakes[0],
                    'profit_if_wins': profits[0]
                },
                {
                    'outcome': 'Under 2.5',
                    'platform': best_under[1],
                    'odds': best_under[0],
                    'stake': stakes[1],
                    'profit_if_wins': profits[1]
                }
            ]
        }

    return None


def scan_gg_arb(pair, total_stake):
    """Scans GG/NG market for arbitrage"""

    sb_gg = pair['sportybet'].get('odds_gg', {})
    bw_gg = pair['betway'].get('odds_gg', {})
    fc_gg = pair['footballcom'].get('odds_gg', {})

    best_yes = get_best_odds('yes',
        (sb_gg, 'Sportybet'),
        (bw_gg, 'Betway'),
        (fc_gg, 'Football.com')
    )
    best_no = get_best_odds('no',
        (sb_gg, 'Sportybet'),
        (bw_gg, 'Betway'),
        (fc_gg, 'Football.com')
    )

    if not all([best_yes[0], best_no[0]]):
        return None

    odds_list = [best_yes[0], best_no[0]]
    arb_sum, profit_pct = calculate_arb(odds_list)

    if MIN_ARB_PROFIT <= profit_pct <= MAX_ARB_PROFIT:
        stakes = calculate_stakes(odds_list, total_stake)
        profits = calculate_profits(odds_list, stakes)

        return {
            'market': 'GG/NG',
            'arb_sum': round(arb_sum, 4),
            'profit_pct': round(profit_pct, 2),
            'profit_ghs': round(
                total_stake * profit_pct / 100, 2),
            'bets': [
                {
                    'outcome': 'GG Yes',
                    'platform': best_yes[1],
                    'odds': best_yes[0],
                    'stake': stakes[0],
                    'profit_if_wins': profits[0]
                },
                {
                    'outcome': 'GG No',
                    'platform': best_no[1],
                    'odds': best_no[0],
                    'stake': stakes[1],
                    'profit_if_wins': profits[1]
                }
            ]
        }

    return None


def display_opportunity(opp):
    """Displays a single arbitrage opportunity"""

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
        print(f"        Bet: {bet['outcome']}")
        print(f"        Odds: {bet['odds']}")
        print(f"        Stake: GHS {bet['stake']:.2f}")
        print(f"        Win: GHS {bet['profit_if_wins']:.2f}")


def scan_all(sportybet_matches, betway_matches,
             footballcom_matches=None,
             total_stake=TOTAL_CAPITAL):
    """
    Main arbitrage scanner across all 3 platforms
    """

    if footballcom_matches is None:
        footballcom_matches = []

    print("\n" + "🔍 " * 20)
    print("   ARBITRAGE SCANNER")
    print(f"   {datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}")
    print("🔍 " * 20 + "\n")

    print(f"📊 Sportybet: {len(sportybet_matches)} matches")
    print(f"📊 Betway: {len(betway_matches)} matches")
    print(f"📊 Football.com: {len(footballcom_matches)} matches")

    # Combine all matches
    all_matches = (sportybet_matches +
                   betway_matches +
                   footballcom_matches)

    # Find same matches across platforms
    groups = match_all_platforms(all_matches)
    print(f"\n✅ Matched across platforms: {len(groups)} events")

    if not groups:
        print("\n⚠️ No matching events found!")
        print("💡 Try running all 3 scrapers first")
        return []

    # Scan each group for arb
    opportunities = []
    print(f"\n🔍 Scanning {len(groups)} matched events...\n")

    for group in groups:
        matches = group['matches']
        first = matches[0]

        match_name = (f"{first['home_team']} vs "
                      f"{first['away_team']}")
        kickoff = first['kickoff']
        tournament = first['tournament']

        # Build pair with all platform data
        empty = {
            'odds_1x2': {},
            'odds_ou': {},
            'odds_gg': {}
        }

        pair = {
            'sportybet': next(
                (m for m in matches
                 if m['source'] == 'sportybet_gh'), empty),
            'betway': next(
                (m for m in matches
                 if m['source'] == 'betway_gh'), empty),
            'footballcom': next(
                (m for m in matches
                 if m['source'] == 'footballcom_gh'), empty),
            'match': match_name,
            'kickoff': kickoff,
            'tournament': tournament
        }

        # Scan all markets
        for arb_fn in [scan_1x2_arb, scan_ou_arb, scan_gg_arb]:
            arb = arb_fn(pair, total_stake)
            if arb:
                opp = {
                    'match': match_name,
                    'kickoff': kickoff,
                    'tournament': tournament,
                    **arb
                }
                opportunities.append(opp)
                display_opportunity(opp)

    # Summary
    print(f"\n{'='*60}")
    print(f"✅ SCAN COMPLETE!")
    print(f"⚽ Events scanned: {len(groups)}")
    print(f"🎯 Arb opportunities: {len(opportunities)}")

    if opportunities:
        total_profit = sum(o['profit_ghs'] for o in opportunities)
        best = max(opportunities,
                   key=lambda x: x['profit_pct'])
        print(f"💰 Total potential profit: GHS {total_profit:.2f}")
        print(f"📈 Best: {best['profit_pct']:.2f}% "
              f"on {best['match']}")
    else:
        print("💡 No arb opportunities right now")
        print("   Try again closer to kickoff times")
        print("   Best opportunities 2-3 hours before kickoff")

    print(f"{'='*60}")

    return opportunities


def run():
    """Loads saved odds and runs arbitrage scan"""

    print("\n" + "🚀 " * 20)
    print("   QUANT BET ALPHA - ARBITRAGE ENGINE")
    print("🚀 " * 20 + "\n")

    sportybet_matches = []
    betway_matches = []
    footballcom_matches = []

    try:
        with open('data/sportybet_odds.json') as f:
            sportybet_matches = json.load(f)
        print(f"✅ Sportybet: {len(sportybet_matches)} matches")
    except FileNotFoundError:
        print("❌ sportybet_odds.json not found!")
        print("   Run: python data/sportybet.py first")

    try:
        with open('data/betway_odds.json') as f:
            betway_matches = json.load(f)
        print(f"✅ Betway: {len(betway_matches)} matches")
    except FileNotFoundError:
        print("❌ betway_odds.json not found!")
        print("   Run: python data/betway.py first")

    try:
        with open('data/footballcom_odds.json') as f:
            footballcom_matches = json.load(f)
        print(f"✅ Football.com: {len(footballcom_matches)} matches")
    except FileNotFoundError:
        print("❌ footballcom_odds.json not found!")
        print("   Run: python data/footballcom.py first")

    if not any([sportybet_matches,
                betway_matches,
                footballcom_matches]):
        print("\n❌ No odds data found!")
        return []

    opportunities = scan_all(
        sportybet_matches,
        betway_matches,
        footballcom_matches,
        total_stake=TOTAL_CAPITAL
    )

    if opportunities:
        with open('engine/opportunities.json', 'w') as f:
            json.dump(opportunities, f, indent=2)
        print(f"\n💾 Saved to engine/opportunities.json")

    return opportunities


if __name__ == "__main__":
    run()