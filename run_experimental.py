"""
EXPERIMENTAL ENGINE RUNNER — Standalone live scraper + arb detector.

Calls all active scrapers IN PARALLEL (simultaneously), then runs the
experimental engine across all 3 categories: Balanced, Unbalanced, Quasi-Arb.

Total scrape time = time of the SLOWEST scraper (not the sum of all).

Does NOT touch main.py or arbitrage_engine.py.

Usage:
    python run_experimental.py
"""

import sys
import os
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from dotenv import dotenv_values

# Fix Windows console emoji printing
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── ACTIVE platforms (6) ───────────────────────────────────────────────────────
from data.sportybet    import run as fetch_sportybet
from data.betway       import run as fetch_betway
from data.footballcom  import run as fetch_footballcom
from data.onexbet      import run as fetch_onexbet
from data.twentytwobet import run as fetch_twentytwobet
from data.msport       import run as fetch_msport

from data.bangbet  import run as fetch_bangbet

# ── INACTIVE platforms (uncomment when scrapers are stable) ───────────────────
# from data.soccabet import run as fetch_soccabet
# from data.betpawa  import run as fetch_betpawa
# from data.supabet  import run as fetch_supabet

from engine.experimental_engine import run_experimental, display_all

ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')

# ── SCRAPER REGISTRY ───────────────────────────────────────────────────────────
# Add or remove scrapers here. Each entry: (display_name, fetch_function)
ACTIVE_SCRAPERS = [
    ('Sportybet',    fetch_sportybet),
    ('Betway',       fetch_betway),
    ('Football.com', fetch_footballcom),
    ('1xBet',        fetch_onexbet),
    ('22Bet',        fetch_twentytwobet),
    ('MSport',       fetch_msport),
    ('Bangbet',      fetch_bangbet),
]


class _Tee:
    def __init__(self, *files):
        self.files = files
    def write(self, obj):
        for f in self.files:
            f.write(obj)
            f.flush()
    def flush(self):
        for f in self.files:
            f.flush()


import io
import threading

# Lock so only one scraper prints its output block at a time
_print_lock = threading.Lock()
# Thread-local storage — each thread gets its own .buf
_thread_local = threading.local()


class _ThreadLocalWriter:
    """
    A stdout wrapper that writes to the current thread's local buffer
    (if one is set), or falls through to the original real stdout.
    Shared across all threads as sys.stdout; safe because each thread
    only reads/writes its OWN _thread_local.buf.
    """
    def __init__(self, real):
        self._real = real

    def write(self, s):
        buf = getattr(_thread_local, 'buf', None)
        if buf is not None:
            buf.write(s)
        else:
            self._real.write(s)

    def flush(self):
        buf = getattr(_thread_local, 'buf', None)
        if buf is None:
            self._real.flush()


def _safe_fetch(name, fetch_fn):
    """
    Run a single scraper.  Its print() output is captured in a per-thread
    buffer, then flushed as one clean block (thread-safe) when done.
    Returns (name, result, elapsed, error).
    """
    _thread_local.buf = io.StringIO()
    start = time.time()
    error = None
    result = []
    try:
        result = fetch_fn() or []
        elapsed = time.time() - start
    except Exception as e:
        elapsed = time.time() - start
        error = e
        import traceback as _tb
        _thread_local.buf.write(_tb.format_exc())
    finally:
        captured = _thread_local.buf.getvalue()
        _thread_local.buf = None  # stop capturing for this thread

    # Flush the entire block atomically so nothing interleaves
    with _print_lock:
        if captured.strip():
            print(captured, end="")
        if error:
            print(f"  ❌ {name} failed after {elapsed:.1f}s: {error}")
        else:
            print(f"  ✅ {name}: {len(result)} matches ({elapsed:.1f}s)")
        print()  # blank separator between scraper blocks

    return name, result, elapsed, error


def fetch_all_parallel(scrapers):
    """
    Fire all scrapers simultaneously using a thread pool.
    Each scraper's full output prints as one clean block when it finishes.
    Returns a dict: {name: match_list}
    """
    results = {}
    futures = {}

    with ThreadPoolExecutor(max_workers=len(scrapers)) as executor:
        for name, fn in scrapers:
            future = executor.submit(_safe_fetch, name, fn)
            futures[future] = name

        for future in as_completed(futures):
            name, data, elapsed, error = future.result()
            results[name] = data

    return results


def main():
    os.makedirs('data', exist_ok=True)
    f = open('data/experimental_results.txt', 'w', encoding='utf-8')
    original_stdout = sys.stdout
    tee = _Tee(sys.stdout, f)
    # Wrap tee with the thread-local writer so parallel scrapers
    # capture their own output without interleaving
    sys.stdout = _ThreadLocalWriter(tee)

    try:
        _env        = dotenv_values(ENV_PATH)
        total_stake = int(_env.get('STARTING_CAPITAL', 500))

        print("\n" + "🧪 " * 20)
        print("   QUANT BET ALPHA — EXPERIMENTAL ENGINE")
        print(f"   {datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}")
        print(f"   Stake: GHS {total_stake}")
        print(f"   Platforms: {len(ACTIVE_SCRAPERS)} active — running in parallel")
        print("🧪 " * 20)

        # ── PARALLEL SCRAPE ────────────────────────────────────────────────────
        print(f"\n🔄 Fetching live odds from all {len(ACTIVE_SCRAPERS)} platforms simultaneously...\n")
        scrape_start = time.time()

        fetched = fetch_all_parallel(ACTIVE_SCRAPERS)

        scrape_time = time.time() - scrape_start
        total_fetched = sum(len(v) for v in fetched.values())
        print(f"\n⏱️  Scraping done in {scrape_time:.1f}s  |  Total matches: {total_fetched}")

        if total_fetched == 0:
            print("\n❌ No data fetched from any platform. Exiting.")
            return

        # ── EXPERIMENTAL ENGINE ────────────────────────────────────────────────
        print(f"\n🔍 Running experimental arb detection across {len(ACTIVE_SCRAPERS)} platforms...\n")

        balanced, unbalanced, quasi, num_groups = run_experimental(
            total_stake          = total_stake,
            sportybet_matches    = fetched.get('Sportybet',    []),
            betway_matches       = fetched.get('Betway',       []),
            footballcom_matches  = fetched.get('Football.com', []),
            onexbet_matches      = fetched.get('1xBet',        []),
            twentytwobet_matches = fetched.get('22Bet',        []),
            msport_matches       = fetched.get('MSport',       []),
            # Inactive platforms pass empty lists so the engine still works
            soccabet_matches     = [],
            betpawa_matches      = [],
            supabet_matches      = [],
            bangbet_matches      = fetched.get('Bangbet', []),
        )

        display_all(balanced, unbalanced, quasi, num_groups, total_stake)

    finally:
        sys.stdout = original_stdout
        f.close()


if __name__ == "__main__":
    main()
