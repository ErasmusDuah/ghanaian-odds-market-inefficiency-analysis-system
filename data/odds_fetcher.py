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
# ============================================================
SPORTS = [
    # ⭐ UEFA COMPETITIONS
    'soccer_uefa_champs_league',
    'soccer_uefa_europa_league',
    'soccer_uefa_europa_conference_league',
    'soccer_fifa_world_cup',
    'soccer_fifa_world_cup_qualifiers_europe',

    # ⭐ ENGLAND
    'soccer_epl',
    'soccer_efl_champ',
    'soccer_england_league1',
    'soccer_england_league2',
    'soccer_fa_cup',
    'soccer_england_efl_cup',

    # ⭐ SPAIN
    'soccer_spain_la_liga',
    'soccer_spain_segunda_division',
    'soccer_spain_copa_del_rey',

    # ⭐ GERMANY
    'soccer_germany_bundesliga',
    'soccer_germany_bundesliga2',
    'soccer_germany_liga3',
    'soccer_germany_bundesliga_women',
    'soccer_germany_dfb_pokal',

    # ⭐ ITALY
    'soccer_italy_serie_a',
    'soccer_italy_serie_b',

    # ⭐ FRANCE
    'soccer_france_ligue_one',
    'soccer_france_ligue_two',
    'soccer_france_coupe_de_france',

    # ⭐ OTHER EUROPE
    'soccer_netherlands_eredivisie',
    'soccer_portugal_primeira_liga',
    'soccer_belgium_first_div',
    'soccer_turkey_super_league',
    'soccer_greece_super_league',
    'soccer_spl',
    'soccer_austria_bundesliga',
    'soccer_switzerland_superleague',
    'soccer_denmark_superliga',
    'soccer_norway_eliteserien',
    'soccer_sweden_allsvenskan',
    'soccer_poland_ekstraklasa',
    'soccer_russia_premier_league',
    'soccer_league_of_ireland',

    # ⭐ SOUTH AMERICA
    'soccer_brazil_campeonato',
    'soccer_brazil_serie_b',
    'soccer_argentina_primera_division',
    'soccer_chile_campeonato',
    'soccer_conmebol_copa_libertadores',

    # ⭐ NORTH AMERICA
    'soccer_usa_mls',
    'soccer_mexico_ligamx',

    # ⭐ ASIA & OCEANIA
    'soccer_japan_j_league',
    'soccer_korea_kleague1',
    'soccer_australia_aleague',
    'soccer_china_superleague',
    'soccer_saudi_arabia_pro_league',
]

# API Settings
REGIONS = 'eu'
MARKETS = 'h2h'
ODDS_FORMAT = 'decimal'

# Ghana available bookmakers
GHANA_BOOKMAKERS = ['Betway', '1xBet']


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
    Speed = catching opportunities before they disappear!
    """

    print("🔍 Scanning all leagues simultaneously...")
    print(f"📅 Date: {datetime.now().strftime('%A, %d %B %Y')}")
    print(f"🕐 Time: {datetime.now().strftime('%H:%M:%S')}")
    print("-" * 60)

    start_time = datetime.now()
    available = []
    total_matches = 0

    with ThreadPoolExecutor(max_workers=20) as executor:
        future_to_sport = {
            executor.submit(check_single_league, sport): sport
            for sport in SPORTS
        }

        for future in as_completed(future_to_sport):
            result = future.result()

            if result:
                available.append(result)
                total_matches += result['match_count']
                print(f"  ✅ {result['sport']}: "
                      f"{result['match_count']} matches")

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
    Data pipelines must be:
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
    Formula: probability = 1 / odds
    """
    return (1 / odds) * 100


def calculate_market_overround(outcomes):
    """
    Calculates bookmaker overround (profit margin)
    Lower overround = better odds for us
    """
    total_implied = sum(1 / o['price'] for o in outcomes)
    overround = (total_implied - 1) * 100
    return round(overround, 2)


def display_odds(data, sport):
    """
    Displays odds in clean readable format
    Only shows Ghana available bookmakers
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

        # Check if match has Ghana bookmakers
        has_ghana_bookie = False
        for bookmaker in match.get('bookmakers', []):
            if bookmaker['title'] in GHANA_BOOKMAKERS:
                has_ghana_bookie = True
                break

        if not has_ghana_bookie:
            continue

        print(f"\n⚽ {home_team} vs {away_team}")
        print(f"🕐 Kickoff: {commence_time}")
        print("-" * 50)

        for bookmaker in match.get('bookmakers', []):
            bookie_name = bookmaker['title']

            # Only show Ghana bookmakers
            if bookie_name not in GHANA_BOOKMAKERS:
                continue

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


def save_odds(data, sport):
    """
    Saves raw odds data to JSON file
    Builds our historical database for backtesting
    """

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"data/odds_{sport}_{timestamp}.json"

    with open(filename, 'w') as f:
        json.dump(data, f, indent=2)

    print(f"  💾 Saved to {filename}")


def run():
    """
    Main function - runs the complete odds fetcher
    """

    print("\n" + "🚀 " * 20)
    print("   QUANT BET ALPHA - ODDS FETCHER v2.0")
    print("   365 Days Coverage | 30+ Leagues")
    print("   Ghana Bookmakers: Betway + 1xBet")
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
