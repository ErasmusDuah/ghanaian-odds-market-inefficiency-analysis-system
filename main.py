import sys
import time
import os
import json
from datetime import datetime
from dotenv import load_dotenv, dotenv_values
import ctypes

# Absolute path to .env — works regardless of which directory the script is launched from
ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')

load_dotenv(ENV_PATH)

# Fix for Windows console emoji printing
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

from data.sportybet    import run as fetch_sportybet
from data.betway       import run as fetch_betway
from data.footballcom  import run as fetch_footballcom
from data.onexbet      import run as fetch_onexbet
from data.twentytwobet import run as fetch_twentytwobet

from engine.arbitrage_engine import scan_all
from engine.arb_tracker import save_arbitrage_opportunities

from monitoring.telegram_alerts import (
    send_startup_message,
    send_arb_alert,
    send_scan_summary,
    send_scan_started_message,
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


def _safe_fetch(fetch_fn, platform_name):
    """Runs a scraper safely — a crash in one platform won't abort the whole scan."""
    try:
        return fetch_fn() or []
    except Exception as e:
        import traceback
        print(f"\nERROR [{platform_name}] scraper failed: {e}")
        traceback.print_exc()
        return []


def run_scan():
    global scan_count, next_run_time
    scan_count += 1

    # Lock in the next run time NOW — before any scraping starts
    next_run_time = time.time() + 5 * 60

    # Read stake directly from the .env file on disk — bypasses OS env cache entirely
    # so changes to STARTING_CAPITAL take effect on the very next scan, no restart needed
    _env = dotenv_values(ENV_PATH)
    TOTAL_STAKE = int(_env.get('STARTING_CAPITAL', 500))
    print(f"[DEBUG] Reading .env from: {ENV_PATH}")
    print(f"[DEBUG] STARTING_CAPITAL read as: {_env.get('STARTING_CAPITAL', 'NOT FOUND')}")

    print(f"\n{'='*60}")
    print(f"SCAN #{scan_count}")
    print(f"TIME: {datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}")
    print(f"STAKE: GHS {TOTAL_STAKE}")
    print(f"{'='*60}\n")

    send_scan_started_message(scan_count)

    opportunities   = []
    events_scanned  = 0
    cycle_start_time = time.time()

    try:
        # Clear stale data first
        clear_old_data()
        print("CLEARED old odds data\n")

        # Fetch fresh odds — each platform is isolated so one crash can't kill the scan
        sportybet_matches    = _safe_fetch(fetch_sportybet,    'Sportybet')
        betway_matches       = _safe_fetch(fetch_betway,       'Betway')
        footballcom_matches  = _safe_fetch(fetch_footballcom,  'Football.com')
        onexbet_matches      = _safe_fetch(fetch_onexbet,      '1xBet')
        twentytwobet_matches = _safe_fetch(fetch_twentytwobet, '22Bet')

        if not any([sportybet_matches, betway_matches,
                    footballcom_matches, onexbet_matches,
                    twentytwobet_matches]):
            print("\nWARNING: No matches fetched from any platform!")
            send_scan_summary([], 0, time.time() - cycle_start_time)
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

    except Exception as e:
        print(f"ERROR: Scan error: {e}")
        import traceback
        traceback.print_exc()

    # ── Telegram alerts ALWAYS fire, even if a scraper above crashed ──────────
    cycle_time_seconds = time.time() - cycle_start_time
    send_scan_summary(opportunities, events_scanned, cycle_time_seconds)

    if opportunities:
        for opp in opportunities:
            try:
                send_arb_alert(opp)
            except Exception as e:
                import traceback
                print(f"\nERROR sending Telegram alert: {e}")
                traceback.print_exc()

    # Save opportunities to file
    if opportunities:
        try:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename  = f"engine/arb_{timestamp}.json"
            os.makedirs('engine', exist_ok=True)
            with open(filename, 'w') as f:
                json.dump(opportunities, f, indent=2)
            print(f"\nSAVED to {filename}")

            # Save to Arbitrage Tracker and sync to GitHub
            save_arbitrage_opportunities(opportunities, TOTAL_STAKE)
        except Exception as e:
            print(f"ERROR saving opportunities: {e}")


def prevent_sleep():
    """Prevent Windows from going to sleep or turning off the display."""
    if os.name == 'nt':
        try:
            ES_CONTINUOUS = 0x80000000
            ES_SYSTEM_REQUIRED = 0x00000001
            # We ONLY use ES_SYSTEM_REQUIRED so the PC stays awake, 
            # but we allow the screen to turn off naturally to save power.
            ctypes.windll.kernel32.SetThreadExecutionState(
                ES_CONTINUOUS | ES_SYSTEM_REQUIRED
            )
            print("[System] Sleep timeout disabled. (Screen is allowed to turn off)")
        except Exception as e:
            print(f"[System] Warning: Could not disable sleep: {e}")

def main():
    prevent_sleep()
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