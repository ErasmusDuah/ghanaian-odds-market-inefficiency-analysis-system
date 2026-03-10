import requests
import pandas as pd
from dotenv import load_dotenv
import os
import json
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# Load our API key from .env file safely
load_dotenv()
API_KEY = os.getenv('ODDS_API_KEY')

# ============================================================
# LEAGUE CONFIGURATION
# Priority order - system scans top to bottom
# Covers 365 days/year across all timezones
# ============================================================
SPORTS = [
    # ⭐ EUROPE (August - May)
    'soccer_epl',                               # English Premier League
    'soccer_efl_champ',                         # Championship
    'soccer_uefa_champs_league',                # Champions League
    'soccer_uefa_europa_league',                # Europa League
    'soccer_uefa_europa_conference_league',     # Conference League
    'soccer_spain_la_liga',                     # La Liga
    'soccer_spain_segunda_division',            # La Liga 2
    'soccer_italy_serie_a',                     # Serie A
    'soccer_italy_serie_b',                     # Serie B
    'soccer_germany_bundesliga',                # Bundesliga
    'soccer_germany_bundesliga2',               # Bundesliga 2
    'soccer_germany_liga3',                     # 3. Liga
    'soccer_france_ligue_one',                  # Ligue 1
    'soccer_france_ligue_two',                  # Ligue 2
    'soccer_netherlands_eredivisie',            # Eredivisie
    'soccer_portugal_primeira_liga',            # Primeira Liga
    'soccer_belgium_first_div',                 # Belgium First Div
    'soccer_turkey_super_league',               # Turkey Super League
    'soccer_greece_super_league',               # Greek Super League
    'soccer_spl',                               # Scottish Premiership
    'soccer_austria_bundesliga',                # Austrian Bundesliga
    'soccer_switzerland_superleague',           # Swiss Superleague
    'soccer_denmark_superliga',                 # Denmark Superliga
    'soccer_norway_eliteserien',                # Norway Eliteserien
    'soccer_sweden_allsvenskan',                # Sweden Allsvenskan
    'soccer_poland_ekstraklasa',                # Poland Ekstraklasa
    'soccer_russia_premier_league',             # Russia Premier League
    'soccer_fa_cup',                            # FA Cup
    'soccer_england_efl_cup',                   # EFL Cup
    'soccer_germany_dfb_pokal',                 # DFB Pokal
    'soccer_spain_copa_del_rey',                # Copa del Rey
    'soccer_france_coupe_de_france',            # Coupe de France

    # ⭐ AMERICAS (fills European gaps)
    'soccer_brazil_campeonato',                 # Brazilian Serie A
    'soccer_brazil_serie_b',                    # Brazilian Serie B
    'soccer_argentina_primera_division',        # Argentine Primera
    'soccer_usa_mls',                           # MLS
    'soccer_mexico_ligamx',                     # Liga MX
    'soccer_chile_campeonato',                  # Chilean Primera
    'soccer_conmebol_copa_libertadores',        # Copa Libertadores

    # ⭐ ASIA & OCEANIA
    'soccer_japan_j_league',                    # J-League
    'soccer_korea_kleague1',                    # K-League 1
    'soccer_australia_aleague',                 # A-League
    'soccer_china_superleague',                 # Chinese Super League
    'soccer_saudi_arabia_pro_league',           # Saudi Pro League

    # ⭐ WORLD COMPETITIONS
    'soccer_fifa_world_cup',                    # FIFA World Cup
    'soccer_fifa_world_cup_qualifiers_europe',  # WC Qualifiers
]

# Regions to fetch bookmakers from
REGIONS = 'uk,eu,af'

# Markets we want to trade
MARKETS = 'h2h,totals,btts'

# Decimal odds format
ODDS_FORMAT = 'decimal'


def check_single_league(sport):
    """
    Checks a single league for available matches
    Designed to run in parallel with other leagues
    """
    url = f"https://api.the-odds-api.com/v4/sports/{sport}/odds"

    params = {
        'apiKey': API_KEY,
        'regions': REGIONS,
        'markets': 'h2h',
        'oddsFormat': ODDS_FORMAT
    }

    try:
        response = requests.get(url, params=params, timeout=10)

        if response.status_code == 200:
            data = response.json()
            if len(data) > 0:
                return {
                    'sport': sport,
                    'match_count': len(data),
                    'success': True
                }
        return None

    except Exception:
        return None


def get_available_leagues():
    """
    Checks ALL leagues SIMULTANEOUSLY using parallel requests

    QUANT CONCEPT - CONCURRENT PROGRAMMING:
    =========================================
    Instead of checking leagues one by one
    we check ALL of them at the same time

    Like having 50 people each checking
    one league simultaneously vs
    one person checking all 50 one by one

    This is critical in trading systems
    Speed = catching opportunities
    before they disappear!

    In quant finance this concept is used for:
    → Scanning multiple exchanges simultaneously
    → Fetching multiple stock prices at once
    → Running parallel backtests
    → Real time market monitoring
    """

    print("🔍 Scanning all leagues simultaneously...")
    print(f"📅 Date: {datetime.now().strftime('%A, %d %B %Y')}")
    print(f"🕐 Time: {datetime.now().strftime('%H:%M:%S')}")
    print("-" * 60)

    start_time = datetime.now()
    available = []
    total_matches = 0

    # Run all league checks at the same time
    with ThreadPoolExecutor(max_workers=20) as executor:

        # Submit all leagues simultaneously
        future_to_sport = {
            executor.submit(check_single_league, sport): sport
            for sport in SPORTS
        }

        # Collect results as they come in
        for future in as_completed(future_to_sport):
            result = future.result()

            if result:
                available.append(result)
                total_matches += result['match_count']
                print(f"  ✅ {result['sport']}: "
                      f"{result['match_count']} matches")

    # Calculate how fast we scanned
    elapsed = (datetime.now() - start_time).total_seconds()

    print("-" * 60)
    print(f"⚡ Scanned {len(SPORTS)} leagues in {elapsed:.1f} seconds!")
    print(f"📊 Active leagues: {len(available)} | "
          f"Total matches: {total_matches}")

    return available


def fetch_odds(sport):
    """
    Fetches live odds for a specific sport/league

    QUANT CONCEPT - DATA PIPELINE:
    ================================
    In quant finance data pipelines are critical
    They must be:
    → Reliable (handle errors gracefully)
    → Fast (get data before odds change)
    → Accurate (validate data received)
    → Logged (track every request)
    """

    print(f"\n📡 Fetching full odds for {sport}...")

    url = f"https://api.the-odds-api.com/v4/sports/{sport}/odds"

    params = {
        'apiKey': API_KEY,
        'regions': REGIONS,
        'markets': MARKETS,
        'oddsFormat': ODDS_FORMAT
    }

    try:
        response = requests.get(url, params=params)

        if response.status_code == 200:
            data = response.json()

            credits_used = response.headers.get(
                'x-requests-used', 'N/A')
            credits_remaining = response.headers.get(
                'x-requests-remaining', 'N/A')

            print(f"  ✅ {len(data)} matches fetched")
            print(f"  💳 Credits used: {credits_used} | "
                  f"Remaining: {credits_remaining}")

            return data

        else:
            print(f"  ❌ Error {response.status_code}")
            return None

    except Exception as e:
        print(f"  ❌ Connection error: {e}")
        return None


def calculate_implied_probability(odds):
    """
    Converts decimal odds to implied probability

    QUANT CONCEPT - IMPLIED PROBABILITY:
    =====================================
    Formula: probability = 1 / odds

    Example:
    Betway → Man Utd win @ 2.10
    implied_prob = 1/2.10 = 47.6%

    Sportybet → Man Utd win @ 2.40
    implied_prob = 1/2.40 = 41.7%

    Same team same match
    Different probabilities = arb opportunity! 🎯
    """
    return (1 / odds) * 100


def calculate_market_overround(outcomes):
    """
    Calculates bookmaker overround (profit margin)

    QUANT CONCEPT - OVERROUND/VIG:
    ================================
    Fair market: all probabilities sum to 100%
    Bookmaker market: probabilities sum to 105-115%
    Extra % = bookmaker profit margin

    Lower overround = better odds for us
    We always target lowest overround bookmakers!
    """
    total_implied = sum(1 / o['price'] for o in outcomes)
    overround = (total_implied - 1) * 100
    return round(overround, 2)


def display_odds(data, sport):
    """
    Displays odds in clean readable format
    Shows implied probabilities and overround
    """

    if not data:
        print("No data to display")
        return

    print("\n" + "=" * 60)
    print(f"📋 LIVE ODDS - {sport.upper()}")
    print(f"🕐 Generated: {datetime.now().strftime('%H:%M:%S')}")
    print("=" * 60)

    for match in data:
        home_team = match['home_team']
        away_team = match['away_team']
        commence_time = match['commence_time']

        print(f"\n⚽ {home_team} vs {away_team}")
        print(f"🕐 Kickoff: {commence_time}")
        print("-" * 50)

        for bookmaker in match.get('bookmakers', []):
            bookie_name = bookmaker['title']

            for market in bookmaker.get('markets', []):
                market_key = market['key']
                outcomes = market['outcomes']
                overround = calculate_market_overround(outcomes)

                if market_key == 'h2h':
                    print(f"\n  📌 {bookie_name} - 1X2 "
                          f"(Overround: {overround}%)")

                    for outcome in outcomes:
                        name = outcome['name']
                        odds = outcome['price']
                        implied_prob = calculate_implied_probability(odds)
                        print(f"     {name}: {odds} "
                              f"(Implied: {implied_prob:.1f}%)")

                elif market_key == 'totals':
                    print(f"\n  📌 {bookie_name} - Over/Under "
                          f"(Overround: {overround}%)")

                    for outcome in outcomes:
                        name = outcome['name']
                        odds = outcome['price']
                        point = outcome.get('point', '')
                        implied_prob = calculate_implied_probability(odds)
                        print(f"     {name} {point}: {odds} "
                              f"(Implied: {implied_prob:.1f}%)")

                elif market_key == 'btts':
                    print(f"\n  📌 {bookie_name} - BTTS "
                          f"(Overround: {overround}%)")

                    for outcome in outcomes:
                        name = outcome['name']
                        odds = outcome['price']
                        implied_prob = calculate_implied_probability(odds)
                        print(f"     {name}: {odds} "
                              f"(Implied: {implied_prob:.1f}%)")


def save_odds(data, sport):
    """
    Saves raw odds data to JSON file

    QUANT CONCEPT - DATA STORAGE:
    ==============================
    Every odds snapshot we save builds our
    historical database for:
    → Backtesting strategies
    → Training Poisson models
    → Monte Carlo simulations
    → Pattern detection over time

    Professional quant firms store
    YEARS of historical data
    We start building ours from day 1!
    """

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"data/odds_{sport}_{timestamp}.json"

    with open(filename, 'w') as f:
        json.dump(data, f, indent=2)

    print(f"  💾 Saved to {filename}")


def run():
    """
    Main function - runs the complete odds fetcher
    Scans all leagues and fetches available odds
    """

    print("\n" + "🚀 " * 20)
    print("   QUANT BET ALPHA - ODDS FETCHER v2.0")
    print("   365 Days Coverage | 30+ Leagues")
    print("🚀 " * 20 + "\n")

    # Step 1: Find active leagues
    available_leagues = get_available_leagues()

    if not available_leagues:
        print("\n⚠️ No matches available right now")
        print("💡 System will check again shortly")
        print("📅 Best times: Weekends | Match days | Evenings")
        return

    print(f"\n🎯 Fetching full odds for {len(available_leagues)} leagues...")

    # Step 2: Fetch full odds for each league
    all_odds = {}

    for league in available_leagues:
        sport = league['sport']
        data = fetch_odds(sport)

        if data:
            display_odds(data, sport)
            save_odds(data, sport)
            all_odds[sport] = data

    # Step 3: Summary
    total_matches = sum(len(v) for v in all_odds.values())

    print("\n" + "=" * 60)
    print("✅ ODDS FETCH COMPLETE!")
    print(f"📊 Leagues scanned: {len(all_odds)}")
    print(f"⚽ Total matches: {total_matches}")
    print(f"🕐 Completed: {datetime.now().strftime('%H:%M:%S')}")
    print("💡 Next: Scanning for arbitrage opportunities...")
    print("=" * 60)

    return all_odds


if __name__ == "__main__":
    run()
