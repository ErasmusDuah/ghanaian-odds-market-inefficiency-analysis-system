"""
INTENSIVE ENGINE RUNNER - Exhaustive pairwise arb scanner.

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
from concurrent.futures import ThreadPoolExecutor, as_completed, wait
from datetime import datetime
from dotenv import dotenv_values

from engine.fb_arb_tracker import ensure_tracker, save_arbitrage_opportunities

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# -- ALL ACTIVE PLATFORMS -------------------------------------------------------
from scrapers.fb_sportybet    import run as fetch_sportybet
from scrapers.fb_betway       import run as fetch_betway
from scrapers.fb_footballcom  import run as fetch_footballcom
from scrapers.fb_onexbet      import run as fetch_onexbet
from scrapers.fb_twentytwobet import run as fetch_twentytwobet
from scrapers.fb_msport       import run as fetch_msport
# Bangbet temporarily disabled; uncomment this import and ACTIVE_SCRAPERS entry to restore.
# from scrapers.fb_bangbet      import run as fetch_bangbet
from scrapers.fb_soccabet     import run as fetch_soccabet
from scrapers.fb_supabet      import run as fetch_supabet
from scrapers.fb_betwinner    import run as fetch_betwinner
from scrapers.fb_betpawa      import run as fetch_betpawa
from scrapers.fb_betano       import run as fetch_betano
from scrapers.fb_betfox       import run as fetch_betfox
from scrapers.fb_betbooker    import run as fetch_betbooker
from scrapers.fb_betika       import run as fetch_betika
from scrapers.fb_1win        import run as fetch_1win
from scrapers.fb_mybetafrica import run as fetch_mybetafrica
from scrapers.fb_odibets     import run as fetch_odibets

from engine.fb_intensive_engine import run_intensive, display_all
from engine.fb_verifier       import verify_opportunities
from engine.fb_market_guard   import sanitize_all_platform_matches, report_has_changes, compact_report_line

ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
SCRAPER_OUTPUT_PATTERNS = (
    '*_odds.json',
    '*_matches.txt',
    '*_event_cache.json',
    '*_last_good.json',
    '*_snapshot.json',
)
SCRAPER_OUTPUT_DIRS = (
    'soccabet_cache',
)
INTENSIVE_OUTPUT_FILES = (
    'intensive_balanced.txt',
    'intensive_unbalanced.txt',
    'intensive_quasi.txt',
)

def _read_total_stake(env_values):
    starting_capital = env_values.get('STARTING_CAPITAL')
    stake_amount = env_values.get('STAKE_AMOUNT')

    starting_capital = str(starting_capital).strip() if starting_capital not in (None, '') else None
    stake_amount = str(stake_amount).strip() if stake_amount not in (None, '') else None

    if starting_capital and stake_amount and starting_capital != stake_amount:
        print(
            f"  WARNING: STARTING_CAPITAL={starting_capital} overrides "
            f"legacy STAKE_AMOUNT={stake_amount}."
        )

    raw_value = starting_capital or stake_amount or '800'
    try:
        stake = int(float(str(raw_value).strip()))
    except (TypeError, ValueError):
        raise ValueError(
            "Invalid STARTING_CAPITAL in football/.env. Use a whole number like STARTING_CAPITAL=800."
        )
    if stake <= 0:
        raise ValueError(
            "Invalid STARTING_CAPITAL in football/.env. The value must be greater than zero."
        )
    return stake

def _read_int_env(env_values, key, default):
    raw_value = env_values.get(key) or os.getenv(key) or str(default)
    try:
        return int(str(raw_value).strip())
    except (TypeError, ValueError):
        print(f"  WARNING: Invalid {key} value {raw_value!r}; using {default}.")
        return default


def _apply_runtime_env(env_values):
    for key in ('MAX_PARALLEL_SCRAPERS', 'SCRAPER_GLOBAL_TIMEOUT'):
        value = env_values.get(key)
        if value not in (None, ''):
            os.environ[key] = str(value)


def _ensure_analysis_trackers():
    """Create local research/analysis trackers on fresh installs."""
    try:
        ensure_tracker()
        from engine.fb_quasi_arb_logger import ensure_quasi_ml_tracker
        ensure_quasi_ml_tracker()
    except Exception as exc:
        print(f"  WARNING: Could not initialize analysis tracker files: {exc}")



def _delete_old_scraper_outputs():
    import glob
    import shutil

    removed = 0
    failed = []

    # Fresh-scan rule: remove generated scraper outputs and discovery snapshots
    # before collecting odds. Trackers and analysis files are intentionally kept.
    for pattern in SCRAPER_OUTPUT_PATTERNS:
        for path in glob.glob(os.path.join(DATA_DIR, pattern)):
            try:
                os.remove(path)
                removed += 1
            except FileNotFoundError:
                pass
            except OSError as exc:
                failed.append((path, exc))

    for dirname in SCRAPER_OUTPUT_DIRS:
        path = os.path.join(DATA_DIR, dirname)
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
                removed += 1
        except FileNotFoundError:
            pass
        except OSError as exc:
            failed.append((path, exc))

    if removed:
        print(f"  Cleared {removed} old scraper output/cache file(s) before fresh scrape.")
    if failed:
        for path, exc in failed[:8]:
            print(f"  ERROR: Could not remove stale scraper output {path}: {exc}")
        if len(failed) > 8:
            print(f"  ERROR: ... {len(failed) - 8} more stale output file(s) could not be removed.")
        return False
    return True


def _clear_intensive_outputs(reason):
    headers = {
        'intensive_balanced.txt': 'BALANCED ARBITRAGE - 0 opportunities',
        'intensive_unbalanced.txt': 'UNBALANCED ARBITRAGE - 0 opportunities',
        'intensive_quasi.txt': 'QUASI-ARB (No-Loss) - 0 opportunities',
    }
    os.makedirs(DATA_DIR, exist_ok=True)
    for filename in INTENSIVE_OUTPUT_FILES:
        path = os.path.join(DATA_DIR, filename)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(headers.get(filename, filename) + '\n')
            f.write(reason + '\n')


ACTIVE_SCRAPERS = [
    ('Sportybet',    fetch_sportybet),
    ('Betway',       fetch_betway),
    ('Football.com', fetch_footballcom),
    ('1xBet',        fetch_onexbet),
    ('22Bet',        fetch_twentytwobet),
    ('MSport',       fetch_msport),
    # ('Bangbet',      fetch_bangbet),  # Temporarily disabled
    ('Soccabet',     fetch_soccabet),
    ('Supabet',      fetch_supabet),
    ('Betwinner',    fetch_betwinner),
    ('BetPawa',      fetch_betpawa),
    ('Betano',       fetch_betano),
    ('Betfox',       fetch_betfox),
    ('Betbooker',    fetch_betbooker),
    ('Betika',       fetch_betika),
    ('1win',         fetch_1win),
    ('MyBet.Africa', fetch_mybetafrica),
    ('Odibets',      fetch_odibets),
]

PLATFORM_ORDER = [name for name, _ in ACTIVE_SCRAPERS]
PLATFORM_TXT_FILES = {
    'Sportybet': 'sportybet_matches.txt',
    'Betway': 'betway_matches.txt',
    'Football.com': 'footballcom_matches.txt',
    '1xBet': 'onexbet_matches.txt',
    '22Bet': 'twentytwobet_matches.txt',
    'MSport': 'msport_matches.txt',
    # 'Bangbet': 'bangbet_matches.txt',  # Temporarily disabled
    'Soccabet': 'soccabet_matches.txt',
    'Supabet': 'supabet_matches.txt',
    'Betwinner': 'betwinner_matches.txt',
    'BetPawa': 'betpawa_matches.txt',
    'Betano': 'betano_matches.txt',
    'Betfox': 'betfox_matches.txt',
    'Betbooker': 'betbooker_matches.txt',
    'Betika': 'betika_matches.txt',
    '1win': 'onewin_matches.txt',
    'MyBet.Africa': 'mybetafrica_matches.txt',
    'Odibets': 'odibets_matches.txt',
}


def _platform_txt_path(name):
    filename = PLATFORM_TXT_FILES.get(name)
    return os.path.join(DATA_DIR, filename) if filename else ''


PLATFORM_JSON_FILES = {
    'Sportybet': 'sportybet_odds.json',
    'Betway': 'betway_odds.json',
    'Football.com': 'footballcom_odds.json',
    '1xBet': 'onexbet_odds.json',
    '22Bet': 'twentytwobet_odds.json',
    'MSport': 'msport_odds.json',
    # 'Bangbet': 'bangbet_odds.json',  # Temporarily disabled
    'Soccabet': 'soccabet_odds.json',
    'Supabet': 'supabet_odds.json',
    'Betwinner': 'betwinner_odds.json',
    'BetPawa': 'betpawa_odds.json',
    'Betano': 'betano_odds.json',
    'Betfox': 'betfox_odds.json',
    'Betbooker': 'betbooker_odds.json',
    'Betika': 'betika_odds.json',
    '1win': 'onewin_odds.json',
    'MyBet.Africa': 'mybetafrica_odds.json',
    'Odibets': 'odibets_odds.json',
}


def _clear_platform_outputs(name):
    """Remove one platform's previous JSON/TXT before its fresh scraper starts."""
    for filename in (PLATFORM_JSON_FILES.get(name), PLATFORM_TXT_FILES.get(name)):
        if not filename:
            continue
        path = os.path.join(DATA_DIR, filename)
        try:
            os.remove(path)
        except FileNotFoundError:
            pass

def _write_empty_platform_outputs(name, reason):
    os.makedirs(DATA_DIR, exist_ok=True)
    json_file = PLATFORM_JSON_FILES.get(name)
    txt_file = PLATFORM_TXT_FILES.get(name)
    if json_file:
        with open(os.path.join(DATA_DIR, json_file), 'w', encoding='utf-8') as f:
            f.write('[]\n')
    if txt_file:
        with open(os.path.join(DATA_DIR, txt_file), 'w', encoding='utf-8') as f:
            f.write(f"{name.upper()} - NO FRESH DATA\n")
            f.write(f"Reason: {reason}\n")
            f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

def _write_guarded_json_outputs(fetched):
    import json
    os.makedirs(DATA_DIR, exist_ok=True)
    for name, matches in fetched.items():
        json_file = PLATFORM_JSON_FILES.get(name)
        if not json_file:
            continue
        path = os.path.join(DATA_DIR, json_file)
        tmp_path = path + '.tmp'
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(matches or [], f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)


def _platform_health_warnings(fetched, failures):
    warnings = []
    manifest_path = os.path.join(DATA_DIR, 'last_scan_manifest.json')
    previous_counts = {}
    try:
        import json
        if os.path.exists(manifest_path):
            with open(manifest_path, encoding='utf-8') as f:
                previous_counts = json.load(f).get('platform_counts', {})
    except Exception:
        previous_counts = {}

    for name in PLATFORM_ORDER:
        count = len(fetched.get(name, []))
        if name in failures:
            warnings.append(f"{name}: failed/timed out; excluded from arb scan")
            continue
        previous = int(previous_counts.get(name, 0) or 0)
        if count == 0:
            warnings.append(f"{name}: returned 0 fresh matches")
        elif previous >= 80 and count < max(20, int(previous * 0.35)):
            warnings.append(f"{name}: sharp count drop {previous}->{count}")
    return warnings

def _write_scan_manifest(fetched, failures, guard_reports):
    import json
    manifest = {
        'scan': scan_count,
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'platform_counts': {name: len(fetched.get(name, [])) for name in PLATFORM_ORDER},
        'failures': {name: str(err) for name, err in failures.items()},
        'guard_drops': {
            name: {
                'input_matches': r.input_matches,
                'output_matches': r.output_matches,
                'dropped_matches': r.dropped_matches,
                'dropped_markets': r.dropped_markets,
                'dropped_lines': r.dropped_lines,
                'reasons': r.reasons,
            }
            for name, r in guard_reports.items()
        },
    }
    with open(os.path.join(DATA_DIR, 'last_scan_manifest.json'), 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2)


# -- THREAD-SAFE PARALLEL OUTPUT ------------------------------------------------

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
        _clear_platform_outputs(name)
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
            print(f"  ERROR {name} failed after {elapsed:.1f}s: {error}")
        else:
            print(f"  OK {name}: {len(result)} matches ({elapsed:.1f}s)")
        print()

    return name, result, elapsed, error


def fetch_all_parallel(scrapers):
    results = {}
    failures = {}
    max_workers = int(os.getenv('MAX_PARALLEL_SCRAPERS', '8'))
    max_workers = max(1, min(len(scrapers), max_workers))
    deadline = int(os.getenv('SCRAPER_GLOBAL_TIMEOUT', '90'))
    executor = ThreadPoolExecutor(max_workers=max_workers)
    futures = {executor.submit(_safe_fetch, name, fn): name for name, fn in scrapers}
    done, pending = wait(futures, timeout=deadline)

    for future in done:
        name, data, elapsed, error = future.result()
        results[name] = data
        if error:
            failures[name] = error
            try:
                _write_empty_platform_outputs(name, f"Scraper failed: {error}")
            except Exception as write_err:
                print(f"  WARNING: Could not write empty output files for {name}: {write_err}")
        elif not data:
            try:
                _write_empty_platform_outputs(name, "Scraper returned 0 fresh matches")
            except Exception as write_err:
                print(f"  WARNING: Could not write empty output files for {name}: {write_err}")
    for future in pending:
        name = futures[future]
        failures[name] = TimeoutError(f"Exceeded SCRAPER_GLOBAL_TIMEOUT={deadline}s")
        results[name] = []
        future.cancel()
        try:
            _write_empty_platform_outputs(name, f"Scraper timed out after {deadline}s")
        except Exception as write_err:
            print(f"  WARNING: Could not write empty output files for {name}: {write_err}")
        print(f"  WARNING: {name} exceeded {deadline}s and was excluded from this scan.")

    executor.shutdown(wait=False, cancel_futures=True)
    return results, failures


# -- MAIN SCHEDULED ENGINE ------------------------------------------------------

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
        _apply_runtime_env(_env)
        total_stake = _read_total_stake(_env)

        print("\n" + "* " * 20)
        print(f"   GHANAIAN ODDS MARKET INEFFICIENCY ANALYSIS SYSTEM - INTENSIVE ENGINE (SCAN #{scan_count})")
        print(f"   {datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}")
        print(f"   Stake: GHS {total_stake}")
        max_parallel = _read_int_env(_env, 'MAX_PARALLEL_SCRAPERS', 8)
        print(f"   Platforms: {len(ACTIVE_SCRAPERS)} active - running up to {min(len(ACTIVE_SCRAPERS), max_parallel)} in parallel")
        print(f"   Scan mode: EXHAUSTIVE (all platform pairings per market)")
        print("* " * 20)

        _clear_intensive_outputs(
            'Scan in progress; previous opportunities cleared to prevent stale odds.'
        )

        if not _delete_old_scraper_outputs():
            _clear_intensive_outputs('Stale scraper output could not be removed safely; scan aborted to prevent stale odds.')
            return

        # -- PARALLEL SCRAPE ----------------------------------------------------
        print(f"\nFetching live odds from all {len(ACTIVE_SCRAPERS)} platforms simultaneously...\n")
        scrape_start = time.time()
        fetched, failures = fetch_all_parallel(ACTIVE_SCRAPERS)
        fetched, guard_reports = sanitize_all_platform_matches(fetched)
        health_warnings = _platform_health_warnings(fetched, failures)
        _write_guarded_json_outputs(fetched)
        _write_scan_manifest(fetched, failures, guard_reports)
        changed_reports = [r for r in guard_reports.values() if report_has_changes(r)]
        if changed_reports:
            print("\nMarket guard removed hidden/incomplete/suspicious scraper output:")
            for report in changed_reports[:12]:
                print(f"   - {compact_report_line(report)}")
            if len(changed_reports) > 12:
                print(f"   - ... {len(changed_reports) - 12} more platform report(s)")
        scrape_time  = time.time() - scrape_start
        total_fetched = sum(len(v) for v in fetched.values())
        print(f"\nScraping done in {scrape_time:.1f}s ({scrape_time/60:.2f} min)  |  Total matches: {total_fetched}")

        print("============================================================")
        print("MATCHES FETCHED PER PLATFORM:")
        for name in PLATFORM_ORDER:
            count = len(fetched.get(name, []))
            print(f"  {name:<12}: {count:<4} | txt: {_platform_txt_path(name)}")
        print("============================================================\n")

        if total_fetched == 0:
            print("\nERROR No data fetched from any platform.")
            _clear_intensive_outputs('No fresh platform data was fetched on this scan; old opportunities were cleared to prevent stale odds.')
            return

        # -- INTENSIVE ENGINE ---------------------------------------------------
        print(f"\nRunning intensive exhaustive scan across {len(ACTIVE_SCRAPERS)} platforms...\n")
        scan_start = time.time()

        opportunities, num_groups = run_intensive(
            total_stake          = total_stake,
            sportybet_matches    = fetched.get('Sportybet',    []),
            betway_matches       = fetched.get('Betway',       []),
            footballcom_matches  = fetched.get('Football.com', []),
            onexbet_matches      = fetched.get('1xBet',        []),
            twentytwobet_matches = fetched.get('22Bet',        []),
            msport_matches       = fetched.get('MSport',       []),
            bangbet_matches      = [],  # Bangbet temporarily disabled
            soccabet_matches     = fetched.get('Soccabet',     []),
            supabet_matches      = fetched.get('Supabet',      []),
            betwinner_matches    = fetched.get('Betwinner',    []),

            betpawa_matches      = fetched.get('BetPawa',      []),
            betano_matches       = fetched.get('Betano',       []),
            betfox_matches       = fetched.get('Betfox',       []),
            betbooker_matches    = fetched.get('Betbooker',    []),
            betika_matches       = fetched.get('Betika',       []),
            onewin_matches       = fetched.get('1win',         []),
            mybetafrica_matches  = fetched.get('MyBet.Africa', []),
            odibets_matches      = fetched.get('Odibets',      []),
        )

        # Lock in next run time exactly 2 minutes after calculations complete
        calc_end_time = datetime.now()
        next_run_time = time.time() + 2 * 60
        next_run_dt = datetime.fromtimestamp(next_run_time)

        calc_end_str = calc_end_time.strftime('%I:%M:%S %p').lstrip('0').lower()
        next_run_str = next_run_dt.strftime('%I:%M:%S %p').lstrip('0').lower()

        verify_enabled = str(_env.get('SPORTYBET_FRONTEND_VERIFY', '0')).lower() in {'1', 'true', 'yes', 'on'}
        if verify_enabled and opportunities:
            opportunities, dropped = verify_opportunities(opportunities, fetched)
            if dropped:
                print(f"  WARNING: Sportybet frontend verifier dropped {dropped} hidden/locked opportunity(s).")
            scan_time = time.time() - scan_start
        else:
            scan_time = time.time() - scan_start

        total_time = scrape_time + scan_time

        quasi_summary = display_all(opportunities, num_groups, total_stake,
                                    scrape_time, scan_time, total_time,
                                    calc_end_str=calc_end_str, next_run_str=next_run_str)

        # Log opportunities to CSV (always logs a row for ML continuity)
        arb_msg = ""
        try:
            arb_msg = save_arbitrage_opportunities(opportunities, total_stake, quiet=True)
        except Exception as e:
            print(f"  ERROR ERROR saving intensive opportunities to CSV: {e}")


        # Print the beautiful consolidated summary at the very bottom
        print()
        print(arb_msg.strip())
        print(quasi_summary.strip())
        print()

    except Exception as e:
        print(f"  ERROR ERROR inside intensive run_scan: {e}")
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




def main():
    global next_run_time, scan_count  # <- fix: declare globals so Python doesn't
                                      #         treat them as unassigned locals

    prevent_sleep()
    
    _ensure_analysis_trackers()
    
    if not has_internet():
        print("\nERROR [System] No active internet connection detected! Waiting for connection...")
        while not has_internet():
            print(f"\r[System] WARNING Offline at {datetime.now().strftime('%H:%M:%S')}. Waiting 10s for internet...", end="", flush=True)
            time.sleep(10)
        print(f"\n[System] OK Internet connection established! Starting engine...")
        
    try:
        run_scan()
        
        print("\n[Scheduled] Scanning every 2 minutes (interval starts after calculations complete)")
        print("Press Ctrl+C to stop\n")
        
        while True:
            if time.time() >= next_run_time:
                if not has_internet():
                    print(f"\nERROR [System] Internet connection lost at {datetime.now().strftime('%H:%M:%S')}! Pausing engine...")
                    while not has_internet():
                        print(f"\r[System] WARNING Offline. Waiting 10s for internet...", end="", flush=True)
                        time.sleep(10)
                    print(f"\n[System] OK Internet connection restored at {datetime.now().strftime('%H:%M:%S')}! Resuming scan...")
                    next_run_time = time.time()
                run_scan()
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n\nStopped Stopped by user.")

if __name__ == "__main__":
    main()
