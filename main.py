import sys
import time
import os
import json
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# Fix for Windows console emoji printing
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# Read stake from .env — change STARTING_CAPITAL in .env to use a different amount
TOTAL_STAKE = int(os.getenv('STARTING_CAPITAL', 500))

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
next_run_time = None


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
    global scan_count, next_run_time
    scan_count += 1

    # Lock in the next run time NOW — before any scraping starts
    next_run_time = time.time() + 5 * 60

    print(f"\n{'='*60}")
    print(f"SCAN #{scan_count}")
    print(f"TIME: {datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}")
    print(f"STAKE: GHS {TOTAL_STAKE}")
    print(f"{'='*60}\n")

    try:
        # Start the master stopwatch
        cycle_start_time = time.time()

        # Clear stale data first
        clear_old_data()
        print("CLEARED old odds data\n")

        # Fetch fresh odds from all 5 platforms
        sportybet_matches    = fetch_sportybet()    or []
        betway_matches       = fetch_betway()       or []
        footballcom_matches  = fetch_footballcom()  or []
        onexbet_matches      = fetch_onexbet()      or []
        twentytwobet_matches = fetch_twentytwobet() or []


        if not any([sportybet_matches, betway_matches,
                    footballcom_matches, onexbet_matches,
                    twentytwobet_matches]):
            print("\nWARNING: No matches fetched from any platform!")
            return

        # Scan for arbitrage across all 5 platforms
        opportunities, events_scanned = scan_all(
            sportybet_matches,
            betway_matches,
            footballcom_matches,
            onexbet_matches,
            twentytwobet_matches,
            total_stake=TOTAL_STAKE,
            cycle_start_time=cycle_start_time
        )

        # Send Telegram alerts (Summary FIRST, then the detailed matches)
        cycle_time_seconds = time.time() - cycle_start_time
        send_scan_summary(opportunities, events_scanned, cycle_time_seconds)

        if opportunities:
            for opp in opportunities:
                send_arb_alert(opp)

        # Save opportunities to file
        if opportunities:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename  = f"engine/arb_{timestamp}.json"
            os.makedirs('engine', exist_ok=True)
            with open(filename, 'w') as f:
                json.dump(opportunities, f, indent=2)
            print(f"\nSAVED to {filename}")

    except Exception as e:
        print(f"ERROR: Scan error: {e}")
        import traceback
        traceback.print_exc()


def main():
    print("\n" + "* " * 20)
    print("   QUANT BET ALPHA - LIVE SYSTEM")
    print(f"   {datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}")
    print("* " * 20)

    send_startup_message()
    run_scan()  # next_run_time is set inside here

    print("\n[Scheduled] Scanning every 5 minutes (interval starts when scraping starts)")
    print("Alerts will be sent to Telegram")
    print("STOP Press Ctrl+C to stop\n")

    while True:
        if time.time() >= next_run_time:
            run_scan()
        time.sleep(1)


if __name__ == "__main__":
    main()