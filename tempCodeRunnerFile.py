import time
import schedule
from data.sportybet import run as fetch_sportybet
from data.betway import run as fetch_betway
from data.footballcom import run as fetch_footballcom
from engine.arbitrage_engine import scan_all
from monitoring.telegram_alerts import (
    send_startup_message,
    send_arb_alert,
    send_scan_summary
)
import json
from datetime import datetime

scan_count = 0


def run_scan():
    global scan_count
    scan_count += 1

    print(f"\n{'='*60}")
    print(f"🔍 SCAN #{scan_count}")
    print(f"🕐 {datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}")
    print(f"{'='*60}\n")

    try:
        # Fetch fresh odds from all platforms
        sportybet_matches = fetch_sportybet() or []
        betway_matches = fetch_betway() or []
        footballcom_matches = fetch_footballcom() or []

        # Scan for arbitrage
        opportunities = scan_all(
            sportybet_matches,
            betway_matches,
            footballcom_matches,
            total_stake=500
        )

        # Send Telegram alerts
        if opportunities:
            for opp in opportunities:
                send_arb_alert(opp)

        send_scan_summary(opportunities, scan_count)

        # Save opportunities
        if opportunities:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"engine/arb_{timestamp}.json"
            with open(filename, 'w') as f:
                json.dump(opportunities, f, indent=2)
            print(f"\n💾 Saved to {filename}")

    except Exception as e:
        print(f"❌ Scan error: {e}")


def main():
    print("\n" + "🚀 " * 20)
    print("   QUANT BET ALPHA - LIVE SYSTEM")
    print(f"   {datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}")
    print("🚀 " * 20)

    # Send startup message
    send_startup_message()

    # Run immediately first
    run_scan()