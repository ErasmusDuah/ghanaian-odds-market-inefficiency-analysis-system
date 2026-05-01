import time
import schedule
import os
import json
from datetime import datetime

from data.sportybet    import run as fetch_sportybet
from data.betway       import run as fetch_betway
from data.footballcom  import run as fetch_footballcom
from data.onexbet      import run as fetch_onexbet
from data.twentytwobet import run as fetch_twentytwobet

from engine.arbitrage_engine import scan_all

from monitoring.telegram_alerts import (
    send_startup_message,
    send_arb_alert,
    send_scan_summary,
)

scan_count = 0


def clear_old_data():
    """Delete old odds files before each scan."""
    files = [
        'data/sportybet_odds.json',
        'data/betway_odds.json',
        'data/footballcom_odds.json',
        'data/onexbet_odds.json',
        'data/twentytwobet_odds.json',
    ]
    for f in files:
        try:
            if os.path.exists(f):
                os.remove(f)
        except Exception:
            pass


def run_scan():
    global scan_count
    scan_count += 1

    print(f"\n{'='*60}")
    print(f"🔍 SCAN #{scan_count}")
    print(f"🕐 {datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}")
    print(f"{'='*60}\n")

    try:
        # Clear stale data first
        clear_old_data()
        print("🗑️  Cleared old odds data\n")

        # Fetch fresh odds from all 5 platforms
        sportybet_matches    = fetch_sportybet()    or []
        betway_matches       = fetch_betway()       or []
        footballcom_matches  = fetch_footballcom()  or []
        onexbet_matches      = fetch_onexbet()      or []
        twentytwobet_matches = fetch_twentytwobet() or []

        print(f"\n📊 Matches fetched:")
        print(f"   Sportybet   : {len(sportybet_matches)}")
        print(f"   Betway      : {len(betway_matches)}")
        print(f"   Football.com: {len(footballcom_matches)}")
        print(f"   1xBet       : {len(onexbet_matches)}")
        print(f"   22Bet       : {len(twentytwobet_matches)}")

        if not any([sportybet_matches, betway_matches,
                    footballcom_matches, onexbet_matches,
                    twentytwobet_matches]):
            print("\n⚠️  No matches fetched from any platform!")
            return

        # Scan for arbitrage across all 5 platforms
        opportunities = scan_all(
            sportybet_matches,
            betway_matches,
            footballcom_matches,
            onexbet_matches,
            twentytwobet_matches,
            total_stake=500,
        )

        # Send Telegram alerts
        if opportunities:
            for opp in opportunities:
                send_arb_alert(opp)

        send_scan_summary(opportunities, scan_count)

        # Save opportunities to file
        if opportunities:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename  = f"engine/arb_{timestamp}.json"
            os.makedirs('engine', exist_ok=True)
            with open(filename, 'w') as f:
                json.dump(opportunities, f, indent=2)
            print(f"\n💾 Saved to {filename}")

    except Exception as e:
        print(f"❌ Scan error: {e}")
        import traceback
        traceback.print_exc()


def main():
    print("\n" + "🚀 " * 20)
    print("   QUANT BET ALPHA - LIVE SYSTEM")
    print(f"   {datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}")
    print("🚀 " * 20)

    send_startup_message()
    run_scan()

    schedule.every(5).minutes.do(run_scan)

    print("\n⏰ Scheduled to scan every 5 minutes")
    print("📱 Alerts will be sent to Telegram")
    print("🛑 Press Ctrl+C to stop\n")

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()