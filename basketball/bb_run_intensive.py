"""
INTENSIVE ENGINE RUNNER (BASKETBALL) — Exhaustive pairwise arb scanner.

Scrapes all 6 platforms in PARALLEL, then runs the basketball engine which
tests every possible platform pairing combination per market:
  - Winner (2-way):  6^2 = 36 directional pairs
  - Overtime (Y/N):  6^2 = 36 directional pairs

Usage:
    python basketball/bb_run_intensive.py
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

from basketball.scrapers.bb_sportybet    import run as fetch_sportybet
from basketball.scrapers.bb_betway       import run as fetch_betway
from basketball.scrapers.bb_footballcom  import run as fetch_footballcom
from basketball.scrapers.bb_onexbet      import run as fetch_onexbet
from basketball.scrapers.bb_twentytwobet import run as fetch_twentytwobet
from basketball.scrapers.bb_msport       import run as fetch_msport

from basketball.engine.bb_engine import scan_all
from basketball.engine.bb_arb_tracker import save_arbitrage_opportunities, push_to_github
from basketball.engine.bb_quasi_logger import log_quasi_opportunities

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


def run_scan():
    global scan_count
    scan_count += 1
    
    try:
        _env        = dotenv_values(ENV_PATH)
        total_stake = int(_env.get('STARTING_CAPITAL', 500))

        print("\n" + "🏀 " * 20)
        print(f"   QUANT BET ALPHA (BASKETBALL) — SCAN #{scan_count}")
        print(f"   {datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}")
        print(f"   Stake: GHS {total_stake}")
        print(f"   Platforms: {len(ACTIVE_SCRAPERS)} active — running in parallel")
        print("🏀 " * 20)

        print(f"\n🔄 Fetching live Basketball odds from all {len(ACTIVE_SCRAPERS)} platforms simultaneously...\n")
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

        # Run Basketball Engine
        print(f"\n🔍 Running exhaustive 2-way Winner + Overtime intensive scan...\n")
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
            calc_end_str         = datetime.now().strftime('%I:%M:%S %p').lstrip('0').lower(),
            next_run_str         = next_run_str
        )

        scan_time_actual = time.time() - scan_start
        total_time = scrape_time + scan_time_actual

        # Split opportunities into balanced, unbalanced, and quasi arbs
        balanced_opps   = [o for o in opportunities if o.get('category') == 'balanced']
        unbalanced_opps = [o for o in opportunities if o.get('category') == 'unbalanced']
        quasi_opps      = [o for o in opportunities if o.get('category') == 'quasi']

        # Log balanced & unbalanced arbs to CSV
        arb_msg = ""
        try:
            arb_msg = save_arbitrage_opportunities(balanced_opps + unbalanced_opps, total_stake, quiet=True)
        except Exception as e:
            print(f"  ❌ ERROR saving BB opportunities to CSV: {e}")

        # Log quasi arbs to Excel
        ml_msg = ""
        try:
            ml_msg = log_quasi_opportunities(quasi_opps, total_stake, quiet=True)
        except Exception as e:
            print(f"  ❌ ERROR logging BB quasi arbs to Excel: {e}")

        # Commit and push updated files to GitHub
        git_msg = ""
        try:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            existing_files = []
            for fp in ["basketball/data/bb_arbitrage_tracker.csv", "basketball/data/bb_quasi_ml.xlsx"]:
                if os.path.exists(os.path.join(project_root, fp)):
                    existing_files.append(fp)
            if existing_files:
                git_msg = push_to_github(
                    filepaths=existing_files,
                    message=f"Auto-update Basketball arbitrage results (Scan #{scan_count})",
                    quiet=True
                )
        except Exception as e:
            git_msg = f"  ⚠️ [Git Sync] Error syncing to GitHub: {e}"

        # Print tracker/logger/git summary
        print()
        if arb_msg:
            print(arb_msg.strip())
        if ml_msg:
            print(ml_msg.strip())
        if git_msg:
            print(git_msg.strip())
        print()

        # Wait for next cycle
        print(f"[Scheduled] Next scan in 2 minutes ({next_run_str})")
        print("Press Ctrl+C to stop\n")

        while time.time() < next_run_time:
            time.sleep(1)

        # Run next scan
        run_scan()

    except KeyboardInterrupt:
        print("\n\n🛑 Stopped by user.")
        return
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
    prevent_sleep()
    
    if not has_internet():
        print("\n❌ [System] No active internet connection detected! Waiting for connection...")
        while not has_internet():
            print(f"\r[System] ⚠️ Offline. Waiting 10s...", end="", flush=True)
            time.sleep(10)
        print(f"\n[System] ✅ Internet connection established!")
        
    run_scan()


if __name__ == "__main__":
    main()
