"""
INTENSIVE ENGINE RUNNER — Exhaustive pairwise arb scanner.

Scrapes all 7 platforms in PARALLEL, then runs the intensive engine which
tests every possible platform pairing combination per market:
  - 1X2:   7^3 = 343 combinations
  - O/U:   7^2 = 49  combinations per line
  - GG/NG: 7^2 = 49  combinations

Does NOT touch main.py, run_experimental.py, or their engines.

Usage:
    python run_intensive.py
"""

import sys
import os
import time
import io

import ctypes
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from dotenv import dotenv_values

from engine.fb_arb_tracker import save_arbitrage_opportunities, push_to_github

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── ALL ACTIVE PLATFORMS ───────────────────────────────────────────────────────
from scrapers.fb_sportybet    import run as fetch_sportybet
from scrapers.fb_betway       import run as fetch_betway
from scrapers.fb_footballcom  import run as fetch_footballcom
from scrapers.fb_onexbet      import run as fetch_onexbet
from scrapers.fb_twentytwobet import run as fetch_twentytwobet
from scrapers.fb_msport       import run as fetch_msport

from engine.fb_intensive_engine import run_intensive, display_all
from engine.fb_verifier       import verify_opportunities

ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')

ACTIVE_SCRAPERS = [
    ('Sportybet',    fetch_sportybet),
    ('Betway',       fetch_betway),
    ('Football.com', fetch_footballcom),
    ('1xBet',        fetch_onexbet),
    ('22Bet',        fetch_twentytwobet),
    ('MSport',       fetch_msport),
]


# ── THREAD-SAFE PARALLEL OUTPUT ────────────────────────────────────────────────

_print_lock   = threading.Lock()
_thread_local = threading.local()


class _ThreadLocalWriter:
    def __init__(self, real):
        self._real = real
    def write(self, s):
        buf = getattr(_thread_local, 'buf', None)
        if buf is not None:
            buf.write(s)
        else:
            self._real.write(s)
            self._real.flush()
    def flush(self):
        self._real.flush()

sys.stdout = _ThreadLocalWriter(sys.stdout)


def _safe_fetch(name, fetch_fn):
    _thread_local.buf = io.StringIO()
    start  = time.time()
    error  = None
    result = []
    try:
        result = fetch_fn() or []
        elapsed = time.time() - start
    except Exception as e:
        elapsed = time.time() - start
        error   = e
        import traceback as _tb
        _thread_local.buf.write(_tb.format_exc())
    finally:
        captured = _thread_local.buf.getvalue()
        _thread_local.buf = None

    with _print_lock:
        if captured.strip():
            print(captured, end='')
        if error:
            print(f"  ❌ {name} failed after {elapsed:.1f}s: {error}")
        else:
            print(f"  ✅ {name}: {len(result)} matches ({elapsed:.1f}s)")
        print()

    return name, result, elapsed, error


def fetch_all_parallel(scrapers):
    results = {}
    with ThreadPoolExecutor(max_workers=len(scrapers)) as executor:
        futures = {executor.submit(_safe_fetch, name, fn): name
                   for name, fn in scrapers}
        for future in as_completed(futures):
            name, data, elapsed, error = future.result()
            results[name] = data
    return results


# ── MAIN SCHEDULED ENGINE ──────────────────────────────────────────────────────

scan_count = 0
next_run_time = 0.0

def prevent_sleep():
    """Prevent Windows from going to sleep or turning off the display."""
    if os.name == 'nt':
        try:
            ES_CONTINUOUS = 0x80000000
            ES_SYSTEM_REQUIRED = 0x00000001
            ctypes.windll.kernel32.SetThreadExecutionState(
                ES_CONTINUOUS | ES_SYSTEM_REQUIRED
            )
            print("[System] Sleep timeout disabled. (Screen is allowed to turn off)")
        except Exception as e:
            print(f"[System] Warning: Could not disable sleep: {e}")

def run_scan():
    global scan_count, next_run_time
    scan_count += 1
    
    try:
        _env        = dotenv_values(ENV_PATH)
        total_stake = int(_env.get('STARTING_CAPITAL', 500))

        print("\n" + "🔬 " * 20)
        print(f"   QUANT BET ALPHA — INTENSIVE ENGINE (SCAN #{scan_count})")
        print(f"   {datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}")
        print(f"   Stake: GHS {total_stake}")
        print(f"   Platforms: {len(ACTIVE_SCRAPERS)} active — running in parallel")
        print(f"   Scan mode: EXHAUSTIVE (all platform pairings per market)")
        print("🔬 " * 20)

        # ── PARALLEL SCRAPE ────────────────────────────────────────────────────
        print(f"\n🔄 Fetching live odds from all {len(ACTIVE_SCRAPERS)} platforms simultaneously...\n")
        scrape_start = time.time()
        fetched = fetch_all_parallel(ACTIVE_SCRAPERS)

        scrape_time  = time.time() - scrape_start
        total_fetched = sum(len(v) for v in fetched.values())
        print(f"\n⏱️  Scraping done in {scrape_time:.1f}s ({scrape_time/60:.2f} min)  |  Total matches: {total_fetched}")

        print("============================================================")
        print("MATCHES FETCHED PER PLATFORM:")
        for name in ['Sportybet', 'Betway', 'Football.com', '1xBet', '22Bet', 'MSport']:
            count = len(fetched.get(name, []))
            print(f"  {name:<12}: {count}")
        print("============================================================\n")

        if total_fetched == 0:
            print("\n❌ No data fetched from any platform.")
            return

        # ── INTENSIVE ENGINE ───────────────────────────────────────────────────
        print(f"\n🔍 Running intensive exhaustive scan across {len(ACTIVE_SCRAPERS)} platforms...\n")
        scan_start = time.time()

        opportunities, num_groups = run_intensive(
            total_stake          = total_stake,
            sportybet_matches    = fetched.get('Sportybet',    []),
            betway_matches       = fetched.get('Betway',       []),
            footballcom_matches  = fetched.get('Football.com', []),
            onexbet_matches      = fetched.get('1xBet',        []),
            twentytwobet_matches = fetched.get('22Bet',        []),
            msport_matches       = fetched.get('MSport',       []),
        )

        # Lock in next run time exactly 2 minutes after calculations complete
        calc_end_time = datetime.now()
        next_run_time = time.time() + 2 * 60
        next_run_dt = datetime.fromtimestamp(next_run_time)

        calc_end_str = calc_end_time.strftime('%I:%M:%S %p').lstrip('0').lower()
        next_run_str = next_run_dt.strftime('%I:%M:%S %p').lstrip('0').lower()

        scan_time  = time.time() - scan_start

        total_time = scrape_time + scan_time

        from engine.fb_stake_tracker_helper import _file_lock, check_and_log_ticked_bets
        with _file_lock:
            try:
                check_and_log_ticked_bets()
            except Exception as log_err:
                print(f"  ⚠️ Error checking ticked bets before overwrite: {log_err}")

            quasi_summary = display_all(opportunities, num_groups, total_stake,
                                        scrape_time, scan_time, total_time,
                                        calc_end_str=calc_end_str, next_run_str=next_run_str)

        # Log opportunities to CSV (always logs a row for ML continuity)
        arb_msg = ""
        try:
            arb_msg = save_arbitrage_opportunities(opportunities, total_stake, quiet=True)
        except Exception as e:
            print(f"  ❌ ERROR saving intensive opportunities to CSV: {e}")

        # Commit and push updated files to GitHub
        git_msg = ""
        try:
            git_msg = push_to_github(
                filepaths=["football/data/arbitrage_tracker.csv", "football/data/quasi_arb_ml.xlsx", "football/data/stake_tracker.xlsx"],
                message=f"Auto-update intensive arbitrage results (Scan #{scan_count})",
                quiet=True
            )
        except Exception as e:
            git_msg = f"  ⚠️ [Git Sync] Error syncing to GitHub: {e}"

        # Print the beautiful consolidated summary at the very bottom
        print()
        print(arb_msg.strip())
        print(quasi_summary.strip())
        print(git_msg.strip())
        print()

    except Exception as e:
        print(f"  ❌ ERROR inside intensive run_scan: {e}")
        import traceback
        traceback.print_exc()

def has_internet():
    """Checks for active internet connectivity using OS DNS resolution."""
    import socket
    for host in ["google.com", "cloudflare.com", "microsoft.com"]:
        try:
            socket.gethostbyname(host)
            return True
        except Exception:
            continue
    return False


def start_stake_watcher():
    """Starts a background thread to watch for manual stakes checked in the text files."""
    import threading
    from engine.fb_stake_tracker_helper import check_and_log_ticked_bets
    
    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
    files = [
        os.path.join(data_dir, 'intensive_balanced.txt'),
        os.path.join(data_dir, 'intensive_unbalanced.txt'),
        os.path.join(data_dir, 'intensive_quasi.txt')
    ]
    
    last_mtimes = {}
    for f in files:
        if os.path.exists(f):
            last_mtimes[f] = os.path.getmtime(f)
        else:
            last_mtimes[f] = 0.0

    def watch_loop():
        while True:
            try:
                changed = False
                for f in files:
                    if os.path.exists(f):
                        current_mtime = os.path.getmtime(f)
                        if current_mtime > last_mtimes.get(f, 0.0):
                            last_mtimes[f] = current_mtime
                            changed = True
                    else:
                        if last_mtimes.get(f, 0.0) > 0.0:
                            last_mtimes[f] = 0.0
                            changed = True
                
                if changed:
                    check_and_log_ticked_bets()
            except Exception as watch_err:
                print(f"  ⚠️ [Stake Watcher Thread Error] {watch_err}")
                
            time.sleep(2)  # check every 2 seconds

    t = threading.Thread(target=watch_loop, name="fb_stake_watcher", daemon=True)
    t.start()
    print("[Stake Watcher] Background watcher thread started successfully.")


def main():
    global next_run_time, scan_count  # ← fix: declare globals so Python doesn't
                                      #         treat them as unassigned locals

    prevent_sleep()
    
    # Initialize and migrate Excel stake tracker on startup
    try:
        from engine.fb_stake_tracker_helper import ensure_stake_tracker
        ensure_stake_tracker()
    except Exception as e:
        print(f"  ⚠️ [Stake Watcher Startup Error] Could not initialize Excel stake tracker: {e}")
        
    start_stake_watcher()
    
    if not has_internet():
        print("\n❌ [System] No active internet connection detected! Waiting for connection...")
        while not has_internet():
            print(f"\r[System] ⚠️ Offline at {datetime.now().strftime('%H:%M:%S')}. Waiting 10s for internet...", end="", flush=True)
            time.sleep(10)
        print(f"\n[System] ✅ Internet connection established! Starting engine...")
        
    try:
        run_scan()
        
        print("\n[Scheduled] Scanning every 2 minutes (interval starts after calculations complete)")
        print("Press Ctrl+C to stop\n")
        
        while True:
            if time.time() >= next_run_time:
                if not has_internet():
                    print(f"\n❌ [System] Internet connection lost at {datetime.now().strftime('%H:%M:%S')}! Pausing engine...")
                    while not has_internet():
                        print(f"\r[System] ⚠️ Offline. Waiting 10s for internet...", end="", flush=True)
                        time.sleep(10)
                    print(f"\n[System] ✅ Internet connection restored at {datetime.now().strftime('%H:%M:%S')}! Resuming scan...")
                    next_run_time = time.time()
                run_scan()
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n\n🛑 Stopped by user.")

if __name__ == "__main__":
    main()