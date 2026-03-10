import json
import os
from datetime import datetime
from itertools import combinations


# ============================================================
# ARBITRAGE DETECTION ENGINE
# ============================================================
# QUANT CONCEPT - STATISTICAL ARBITRAGE:
# =======================================
# Arbitrage in finance means exploiting price
# differences for the same asset across markets
#
# In sports betting:
# Asset = match outcome (Man Utd win)
# Markets = different bookmakers
# Price = odds
#
# When bookmakers disagree on odds enough
# we can bet all outcomes and guarantee profit
#
# This is RISK FREE profit - same concept used
# by hedge funds across stock exchanges!
# ============================================================


# Minimum profit threshold to consider an arb
# 0.5% minimum - below this fees eat profit
MIN_ARB_PROFIT = 0.5

# Maximum profit threshold
# Above 10% is suspicious - might be error
MAX_ARB_PROFIT = 10.0


def calculate_arb_percentage(odds_list):
    """
    Calculates arbitrage percentage for a set of odds

    QUANT CONCEPT - ARB FORMULA:
    ==============================
    For N outcomes with odds o1, o2, ... oN:

    Arb Sum = 1/o1 + 1/o2 + ... + 1/oN

    If Arb Sum < 1:
    → Arbitrage EXISTS!
    → Profit % = (1 - Arb Sum) / Arb Sum × 100

    If Arb Sum >= 1:
    → No arbitrage
    → Bookmakers have edge

    Example:
    odds = [2.50, 4.50, 3.20]
    arb_sum = 1/2.50 + 1/4.50 + 1/3.20
            = 0.400 + 0.222 + 0.313
            = 0.935

    0.935 < 1 → ARB EXISTS!
    profit = (1 - 0.935) / 0.935 × 100
           = 6.95%
    """
    arb_sum = sum(1 / odds for odds in odds_list)
    
    if arb_sum < 1:
        profit_percentage = ((1 - arb_sum) / arb_sum) * 100
        return arb_sum, profit_percentage
    
    return arb_sum, 0


def calculate_stakes(odds_list, total_stake):
    """
    Calculates exact stake for each outcome

    QUANT CONCEPT - OPTIMAL STAKE SIZING:
    =======================================
    For guaranteed equal profit on all outcomes:

    Stake_i = (1/odds_i) / arb_sum × total_stake

    This ensures EQUAL profit regardless
    of which outcome occurs!

    Example:
    Total stake: GHS 1000
    odds = [2.50, 4.50, 3.20]
    arb_sum = 0.935

    Stake 1 = (1/2.50) / 0.935 × 1000
            = 0.400 / 0.935 × 1000
            = GHS 427.81

    Stake 2 = (1/4.50) / 0.935 × 1000
            = 0.222 / 0.935 × 1000
            = GHS 237.54

    Stake 3 = (1/3.20) / 0.935 × 1000
            = 0.313 / 0.935 × 1000
            = GHS 334.65

    Verify:
    GHS 427.81 × 2.50 = GHS 1069.53 ✅
    GHS 237.54 × 4.50 = GHS 1068.93 ✅
    GHS 334.65 × 3.20 = GHS 1070.88 ✅

    All outcomes = ~GHS 1070 profit! 🎉
    """
    arb_sum = sum(1 / odds for odds in odds_list)
    stakes = []

    for odds in odds_list:
        stake = (1 / odds) / arb_sum * total_stake
        stakes.append(round(stake, 2))

    return stakes


def calculate_profits(odds_list, stakes):
    """
    Calculates profit for each possible outcome

    Shows exactly how much we win
    regardless of match result
    """
    profits = []

    for i, (odds, stake) in enumerate(zip(odds_list, stakes)):
        # Payout if this outcome wins
        payout = odds * stake
        # Net profit (payout minus total staked)
        total_staked = sum(stakes)
        net_profit = payout - total_staked
        profits.append(round(net_profit, 2))

    return profits


def find_arb_in_match(match, total_stake=1000):
    """
    Scans a single match for arbitrage opportunities

    QUANT CONCEPT - BEST ODDS SELECTION:
    ======================================
    For each outcome we want the HIGHEST odds
    available across ALL bookmakers

    This maximizes our potential profit
    Same concept as best execution in
    stock trading - always get best price!

    Process:
    1. For each outcome (home/draw/away)
       find bookmaker offering highest odds
    2. Combine best odds from different bookmakers
    3. Check if combination creates arbitrage
    4. Calculate stakes and profits
    """

    home_team = match['home_team']
    away_team = match['away_team']
    opportunities = []

    # --------------------------------------------------------
    # SCAN 1X2 MARKET (Home/Draw/Away)
    # --------------------------------------------------------
    best_odds = {}  # outcome → {odds, bookmaker}

    for bookmaker in match.get('bookmakers', []):
        bookie_name = bookmaker['title']

        for market in bookmaker.get('markets', []):
            if market['key'] == 'h2h':

                for outcome in market['outcomes']:
                    name = outcome['name']
                    odds = outcome['price']

                    # Keep highest odds for each outcome
                    if name not in best_odds or \
                            odds > best_odds[name]['odds']:
                        best_odds[name] = {
                            'odds': odds,
                            'bookmaker': bookie_name
                        }

    # Check if we have all 3 outcomes
    if len(best_odds) == 3:
        outcomes = list(best_odds.keys())
        odds_list = [best_odds[o]['odds'] for o in outcomes]
        bookmakers = [best_odds[o]['bookmaker'] for o in outcomes]

        arb_sum, profit_pct = calculate_arb_percentage(odds_list)

        if MIN_ARB_PROFIT <= profit_pct <= MAX_ARB_PROFIT:
            stakes = calculate_stakes(odds_list, total_stake)
            profits = calculate_profits(odds_list, stakes)

            opportunities.append({
                'match': f"{home_team} vs {away_team}",
                'market': '1X2',
                'arb_sum': round(arb_sum, 4),
                'profit_percentage': round(profit_pct, 2),
                'total_stake': total_stake,
                'outcomes': [
                    {
                        'name': outcomes[i],
                        'odds': odds_list[i],
                        'bookmaker': bookmakers[i],
                        'stake': stakes[i],
                        'profit_if_wins': profits[i]
                    }
                    for i in range(len(outcomes))
                ]
            })

    # --------------------------------------------------------
    # SCAN OVER/UNDER MARKET
    # --------------------------------------------------------
    ou_markets = {}  # point → {over: {}, under: {}}

    for bookmaker in match.get('bookmakers', []):
        bookie_name = bookmaker['title']

        for market in bookmaker.get('markets', []):
            if market['key'] == 'totals':

                for outcome in market['outcomes']:
                    name = outcome['name']
                    odds = outcome['price']
                    point = str(outcome.get('point', '2.5'))

                    if point not in ou_markets:
                        ou_markets[point] = {}

                    if name not in ou_markets[point] or \
                            odds > ou_markets[point][name]['odds']:
                        ou_markets[point][name] = {
                            'odds': odds,
                            'bookmaker': bookie_name
                        }

    # Check each Over/Under line
    for point, sides in ou_markets.items():
        if 'Over' in sides and 'Under' in sides:
            odds_list = [sides['Over']['odds'], sides['Under']['odds']]
            bookmakers = [sides['Over']['bookmaker'],
                         sides['Under']['bookmaker']]

            arb_sum, profit_pct = calculate_arb_percentage(odds_list)

            if MIN_ARB_PROFIT <= profit_pct <= MAX_ARB_PROFIT:
                stakes = calculate_stakes(odds_list, total_stake)
                profits = calculate_profits(odds_list, stakes)

                opportunities.append({
                    'match': f"{home_team} vs {away_team}",
                    'market': f'Over/Under {point}',
                    'arb_sum': round(arb_sum, 4),
                    'profit_percentage': round(profit_pct, 2),
                    'total_stake': total_stake,
                    'outcomes': [
                        {
                            'name': f"Over {point}",
                            'odds': odds_list[0],
                            'bookmaker': bookmakers[0],
                            'stake': stakes[0],
                            'profit_if_wins': profits[0]
                        },
                        {
                            'name': f"Under {point}",
                            'odds': odds_list[1],
                            'bookmaker': bookmakers[1],
                            'stake': stakes[1],
                            'profit_if_wins': profits[1]
                        }
                    ]
                })

    # --------------------------------------------------------
    # SCAN BTTS MARKET (Both Teams To Score)
    # --------------------------------------------------------
    btts_best = {}

    for bookmaker in match.get('bookmakers', []):
        bookie_name = bookmaker['title']

        for market in bookmaker.get('markets', []):
            if market['key'] == 'btts':

                for outcome in market['outcomes']:
                    name = outcome['name']
                    odds = outcome['price']

                    if name not in btts_best or \
                            odds > btts_best[name]['odds']:
                        btts_best[name] = {
                            'odds': odds,
                            'bookmaker': bookie_name
                        }

    if 'Yes' in btts_best and 'No' in btts_best:
        odds_list = [btts_best['Yes']['odds'],
                    btts_best['No']['odds']]
        bookmakers = [btts_best['Yes']['bookmaker'],
                     btts_best['No']['bookmaker']]

        arb_sum, profit_pct = calculate_arb_percentage(odds_list)

        if MIN_ARB_PROFIT <= profit_pct <= MAX_ARB_PROFIT:
            stakes = calculate_stakes(odds_list, total_stake)
            profits = calculate_profits(odds_list, stakes)

            opportunities.append({
                'match': f"{home_team} vs {away_team}",
                'market': 'BTTS',
                'arb_sum': round(arb_sum, 4),
                'profit_percentage': round(profit_pct, 2),
                'total_stake': total_stake,
                'outcomes': [
                    {
                        'name': 'BTTS Yes',
                        'odds': odds_list[0],
                        'bookmaker': bookmakers[0],
                        'stake': stakes[0],
                        'profit_if_wins': profits[0]
                    },
                    {
                        'name': 'BTTS No',
                        'odds': odds_list[1],
                        'bookmaker': bookmakers[1],
                        'stake': stakes[1],
                        'profit_if_wins': profits[1]
                    }
                ]
            })

    return opportunities


def display_opportunities(opportunities, currency='GHS'):
    """
    Displays arbitrage opportunities in clean format
    """

    if not opportunities:
        print("  ❌ No arbitrage opportunities found")
        return

    print(f"\n  🎯 Found {len(opportunities)} opportunity/ies!")

    for i, opp in enumerate(opportunities, 1):
        print(f"\n  {'='*55}")
        print(f"  🏆 OPPORTUNITY #{i}")
        print(f"  {'='*55}")
        print(f"  ⚽ Match: {opp['match']}")
        print(f"  📊 Market: {opp['market']}")
        print(f"  💰 Profit: {opp['profit_percentage']:.2f}%")
        print(f"  💵 Total Stake: {currency} {opp['total_stake']:,.2f}")

        profit_amount = opp['total_stake'] * \
            opp['profit_percentage'] / 100
        print(f"  ✅ Guaranteed Profit: "
              f"{currency} {profit_amount:,.2f}")

        print(f"\n  📋 BETS TO PLACE:")
        for outcome in opp['outcomes']:
            print(f"\n     📌 {outcome['bookmaker']}")
            print(f"        Bet: {outcome['name']}")
            print(f"        Odds: {outcome['odds']}")
            print(f"        Stake: {currency} {outcome['stake']:,.2f}")
            print(f"        Profit if wins: "
                  f"{currency} {outcome['profit_if_wins']:,.2f}")


def scan_all_matches(all_odds, total_stake=1000, currency='GHS'):
    """
    Scans ALL matches across ALL leagues
    for arbitrage opportunities

    This is our main scanning engine!
    """

    print("\n" + "🔍 " * 20)
    print("   ARBITRAGE SCANNER RUNNING...")
    print("🔍 " * 20)
    print(f"\n🕐 Scan time: {datetime.now().strftime('%H:%M:%S')}")
    print(f"💵 Total stake per trade: {currency} {total_stake:,}")
    print(f"📊 Minimum profit threshold: {MIN_ARB_PROFIT}%")

    all_opportunities = []
    total_matches_scanned = 0

    for sport, matches in all_odds.items():
        print(f"\n⚽ Scanning {sport}...")
        print(f"   {len(matches)} matches to check")

        for match in matches:
            total_matches_scanned += 1
            opps = find_arb_in_match(match, total_stake)

            if opps:
                all_opportunities.extend(opps)
                display_opportunities(opps, currency)

    # Final summary
    print("\n" + "=" * 60)
    print("📊 ARBITRAGE SCAN COMPLETE!")
    print(f"⚽ Matches scanned: {total_matches_scanned}")
    print(f"🎯 Opportunities found: {len(all_opportunities)}")

    if all_opportunities:
        total_profit = sum(
            o['total_stake'] * o['profit_percentage'] / 100
            for o in all_opportunities
        )
        print(f"💰 Total potential profit: "
              f"{currency} {total_profit:,.2f}")
        print(f"📈 Best opportunity: "
              f"{max(all_opportunities, key=lambda x: x['profit_percentage'])['profit_percentage']:.2f}%")

    print("=" * 60)

    return all_opportunities


# ============================================================
# TEST WITH MOCK DATA
# ============================================================
# Since no matches are live right now
# we test with realistic mock odds
# This proves our engine works correctly!
# ============================================================

def create_mock_data():
    """
    Creates realistic mock odds data for testing

    These odds are based on real bookmaker
    patterns we'd see on a match day
    Some contain deliberate arb opportunities
    so we can verify our engine works!
    """

    return {
        'soccer_epl': [
            {
                'home_team': 'Manchester United',
                'away_team': 'Arsenal',
                'commence_time': '2026-03-14T15:00:00Z',
                'bookmakers': [
                    {
                        'title': 'Betway',
                        'markets': [
                            {
                                'key': 'h2h',
                                'outcomes': [
                                    {'name': 'Manchester United',
                                     'price': 2.50},
                                    {'name': 'Arsenal', 'price': 2.80},
                                    {'name': 'Draw', 'price': 3.20}
                                ]
                            },
                            {
                                'key': 'totals',
                                'outcomes': [
                                    {'name': 'Over', 'point': 2.5,
                                     'price': 2.10},
                                    {'name': 'Under', 'point': 2.5,
                                     'price': 1.85}
                                ]
                            },
                            {
                                'key': 'btts',
                                'outcomes': [
                                    {'name': 'Yes', 'price': 1.90},
                                    {'name': 'No', 'price': 2.05}
                                ]
                            }
                        ]
                    },
                    {
                        'title': 'Sportybet',
                        'markets': [
                            {
                                'key': 'h2h',
                                'outcomes': [
                                    {'name': 'Manchester United',
                                     'price': 2.40},
                                    {'name': 'Arsenal', 'price': 3.10},
                                    {'name': 'Draw', 'price': 3.40}
                                ]
                            },
                            {
                                'key': 'totals',
                                'outcomes': [
                                    {'name': 'Over', 'point': 2.5,
                                     'price': 1.95},
                                    {'name': 'Under', 'point': 2.5,
                                     'price': 2.10}
                                ]
                            },
                            {
                                'key': 'btts',
                                'outcomes': [
                                    {'name': 'Yes', 'price': 2.05},
                                    {'name': 'No', 'price': 1.90}
                                ]
                            }
                        ]
                    },
                    {
                        'title': '1xBet',
                        'markets': [
                            {
                                'key': 'h2h',
                                'outcomes': [
                                    {'name': 'Manchester United',
                                     'price': 2.60},
                                    {'name': 'Arsenal', 'price': 2.90},
                                    {'name': 'Draw', 'price': 3.50}
                                ]
                            },
                            {
                                'key': 'totals',
                                'outcomes': [
                                    {'name': 'Over', 'point': 2.5,
                                     'price': 2.20},
                                    {'name': 'Under', 'point': 2.5,
                                     'price': 1.80}
                                ]
                            },
                            {
                                'key': 'btts',
                                'outcomes': [
                                    {'name': 'Yes', 'price': 2.10},
                                    {'name': 'No', 'price': 1.95}
                                ]
                            }
                        ]
                    }
                ]
            },
            {
                'home_team': 'Chelsea',
                'away_team': 'Liverpool',
                'commence_time': '2026-03-14T17:30:00Z',
                'bookmakers': [
                    {
                        'title': 'Betway',
                        'markets': [
                            {
                                'key': 'h2h',
                                'outcomes': [
                                    {'name': 'Chelsea', 'price': 3.10},
                                    {'name': 'Liverpool', 'price': 2.30},
                                    {'name': 'Draw', 'price': 3.40}
                                ]
                            },
                            {
                                'key': 'totals',
                                'outcomes': [
                                    {'name': 'Over', 'point': 2.5,
                                     'price': 1.85},
                                    {'name': 'Under', 'point': 2.5,
                                     'price': 2.10}
                                ]
                            },
                            {
                                'key': 'btts',
                                'outcomes': [
                                    {'name': 'Yes', 'price': 1.80},
                                    {'name': 'No', 'price': 2.20}
                                ]
                            }
                        ]
                    },
                    {
                        'title': 'Sportybet',
                        'markets': [
                            {
                                'key': 'h2h',
                                'outcomes': [
                                    {'name': 'Chelsea', 'price': 2.90},
                                    {'name': 'Liverpool', 'price': 2.50},
                                    {'name': 'Draw', 'price': 3.60}
                                ]
                            },
                            {
                                'key': 'totals',
                                'outcomes': [
                                    {'name': 'Over', 'point': 2.5,
                                     'price': 2.05},
                                    {'name': 'Under', 'point': 2.5,
                                     'price': 1.90}
                                ]
                            },
                            {
                                'key': 'btts',
                                'outcomes': [
                                    {'name': 'Yes', 'price': 2.15},
                                    {'name': 'No', 'price': 1.85}
                                ]
                            }
                        ]
                    },
                    {
                        'title': '1xBet',
                        'markets': [
                            {
                                'key': 'h2h',
                                'outcomes': [
                                    {'name': 'Chelsea', 'price': 3.20},
                                    {'name': 'Liverpool', 'price': 2.40},
                                    {'name': 'Draw', 'price': 3.30}
                                ]
                            },
                            {
                                'key': 'totals',
                                'outcomes': [
                                    {'name': 'Over', 'point': 2.5,
                                     'price': 1.95},
                                    {'name': 'Under', 'point': 2.5,
                                     'price': 2.00}
                                ]
                            },
                            {
                                'key': 'btts',
                                'outcomes': [
                                    {'name': 'Yes', 'price': 1.95},
                                    {'name': 'No', 'price': 2.10}
                                ]
                            }
                        ]
                    }
                ]
            }
        ]
    }


if __name__ == "__main__":

    print("\n" + "🚀 " * 20)
    print("   QUANT BET ALPHA - ARBITRAGE ENGINE")
    print("   Testing with mock data...")
    print("🚀 " * 20)

    # Create mock data
    mock_data = create_mock_data()

    # Run arbitrage scanner
    # Total stake = GHS 500 (your starting capital)
    opportunities = scan_all_matches(
        mock_data,
        total_stake=500,
        currency='GHS'
    )

    # Save opportunities to file
    if opportunities:
        with open('engine/opportunities.json', 'w') as f:
            json.dump(opportunities, f, indent=2)
        print("\n💾 Opportunities saved to engine/opportunities.json")

    print("\n✅ Engine test complete!")
    print("💡 Wednesday: plug in real odds and find real arb!")
