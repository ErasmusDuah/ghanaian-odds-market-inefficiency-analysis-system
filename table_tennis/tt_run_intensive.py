"""
INTENSIVE ENGINE RUNNER (TABLE TENNIS) — Exhaustive pairwise arb scanner.
Scrapes all 7 platforms in PARALLEL, then runs the table tennis calculation engine.
Usage:
    python table_tennis/run_intensive_tt.py
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

if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from table_tennis.scrapers.tt_sportybet import run as fetch_sportybet
from table_tennis.scrapers.tt_betway    import run as fetch_betway
from table_tennis.scrapers.tt_footballcom import run as fetch_footballcom
from table_tennis.scrapers.tt_onexbet      import run as fetch_onexbet
from table_tennis.scrapers.tt_twentytwobet import run as fetch_twentytwobet
from table_tennis.scrapers.tt_msport       import run as fetch_msport

from table_tennis.engine.tt_engine import scan_all
from table_tennis.engine.tt_arb_tracker import save_arbitrage_opportunities, push_to_github
from table_tennis.engine.tt_quasi_logger import log_quasi_opportunities

ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')

ACTIVE_SCRAPERS = [
    ('Sportybet',    fetch_sportybet),
    ('Betway',       fetch_betway),
    ('Football.com', fetch_footballcom),
    ('1xBet',        fetch_onexbet),
    ('22Bet',        fetch_twentytwobet),
    ('MSport',       fetch_msport),
]

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
    def flush(self):
        if getattr(_thread_local, 'buf', None) is None:
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


scan_count = 0


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


next_run_time = 0.0

def run_scan():
    global scan_count, next_run_time
    scan_count += 1
    
    try:
        _env        = dotenv_values(ENV_PATH)
        total_stake = int(_env.get('STARTING_CAPITAL', 500))

        print("\n" + "🏓 " * 20)
        print(f"   QUANT BET ALPHA (TABLE TENNIS) — SCAN #{scan_count}")
        print(f"   {datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}")
        print(f"   Stake: GHS {total_stake}")
        print(f"   Platforms: {len(ACTIVE_SCRAPERS)} active — running in parallel")
        print("🏓 " * 20)

        print(f"\n🔄 Fetching live Table Tennis odds from all {len(ACTIVE_SCRAPERS)} platforms simultaneously...\n")
        scrape_start = time.time()

        fetched = fetch_all_parallel(ACTIVE_SCRAPERS)

        scrape_time  = time.time() - scrape_start
        total_fetched = sum(len(v) for v in fetched.values())
        print(f"\n⏱️  Scraping done in {scrape_time:.1f}s | Total matches: {total_fetched}")

        print("============================================================")
        print("MATCHES FETCHED PER PLATFORM:")
        for name in ['Sportybet', 'Betway', 'Football.com', '1xBet', '22Bet', 'MSport']:
            count = len(fetched.get(name, []))
            print(f"  {name:<12}: {count}")
        print("============================================================\n")

        if total_fetched == 0:
            print("\n❌ No data fetched from any platform.")
            return

        # Run Table Tennis Engine
        print(f"\n🔍 Running 2-way Winner intensive scan...\n")
        scan_start = time.time()

        # Compute timing info for engine display
        calc_end_time = datetime.now()
        next_run_time = time.time() + 2 * 60
        next_run_dt = datetime.fromtimestamp(next_run_time)
        calc_end_str = calc_end_time.strftime('%I:%M:%S %p').lstrip('0').lower()
        next_run_str = next_run_dt.strftime('%I:%M:%S %p').lstrip('0').lower()

        opportunities, num_groups = scan_all(
            sportybet_matches    = fetched.get('Sportybet',    []),
            betway_matches       = fetched.get('Betway',       []),
            footballcom_matches  = fetched.get('Football.com', []),
            onexbet_matches      = fetched.get('1xBet',        []),
            twentytwobet_matches = fetched.get('22Bet',        []),
            msport_matches       = fetched.get('MSport',       []),
            total_stake          = total_stake,
            scrape_time          = scrape_time,
            scan_time            = time.time() - scan_start,
            total_time           = scrape_time + (time.time() - scan_start),
            calc_end_str         = calc_end_str,
            next_run_str         = next_run_str
        )

        # Split opportunities into standard, unbalanced, and quasi arbs
        balanced_opps   = [o for o in opportunities if o.get('category') == 'balanced']
        unbalanced_opps = [o for o in opportunities if o.get('category') == 'unbalanced']
        quasi_opps      = [o for o in opportunities if o.get('category') == 'quasi']

        # Log balanced & unbalanced arbs to CSV
        arb_msg = ""
        try:
            arb_msg = save_arbitrage_opportunities(balanced_opps + unbalanced_opps, total_stake, quiet=True)
        except Exception as e:
            print(f"  ❌ ERROR saving TT opportunities to CSV: {e}")

        # Log quasi arbs to Excel
        ml_msg = ""
        try:
            ml_msg = log_quasi_opportunities(quasi_opps, total_stake, quiet=True)
        except Exception as e:
            print(f"  ❌ ERROR logging TT quasi arbs to Excel: {e}")

        # Commit and push updated files to GitHub
        git_msg = ""
        try:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            existing_files = []
            for fp in ["table_tennis/data/tt_arbitrage_tracker.csv", "table_tennis/data/tt_quasi_ml.xlsx"]:
                if os.path.exists(os.path.join(project_root, fp)):
                    existing_files.append(fp)
            if existing_files:
                git_msg = push_to_github(
                    filepaths=existing_files,
                    message=f"Auto-update Table Tennis arbitrage results (Scan #{scan_count})",
                    quiet=True
                )
        except Exception as e:
            git_msg = f"  ⚠️ [Git Sync] Error syncing to GitHub: {e}"

        # Print consolidated summary
        print()
        if arb_msg:
            print(arb_msg.strip())
        if ml_msg:
            print(ml_msg.strip())
        if git_msg:
            print(git_msg.strip())
        print()

    except Exception as e:
        print(f"  ❌ ERROR inside run_scan: {e}")
        import traceback
        traceback.print_exc()


def has_internet():
    import socket
    for host in ["google.com", "cloudflare.com", "microsoft.com"]:
        try:
            socket.gethostbyname(host)
            return True
        except Exception:
            continue
    return False


def main():
    global next_run_time, scan_count
    prevent_sleep()
    
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
