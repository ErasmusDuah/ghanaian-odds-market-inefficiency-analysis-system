from data.odds_fetcher import run as fetch_odds
from engine.arbitrage_engine import scan_all_matches
import json
from datetime import datetime

def main():
    print("\n" + "🚀 " * 20)
    print("   QUANT BET ALPHA - LIVE SYSTEM")
    print(f"   {datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}")
    print("🚀 " * 20)

    # Step 1: Fetch real live odds
    print("\n📡 STEP 1: Fetching live odds...")
    all_odds = fetch_odds()

    if not all_odds:
        print("❌ No odds available right now")
        print("💡 Try again when matches are available")
        return

    # Step 2: Scan for arbitrage
    print("\n🔍 STEP 2: Scanning for arbitrage...")
    opportunities = scan_all_matches(
        all_odds,
        total_stake=500,    # Your GHS 500 capital
        currency='GHS'
    )

    # Step 3: Save results
    if opportunities:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"engine/arb_{timestamp}.json"
        with open(filename, 'w') as f:
            json.dump(opportunities, f, indent=2)
        print(f"\n💾 Saved to {filename}")

    print("\n✅ SCAN COMPLETE!")

if __name__ == "__main__":
    main()



    