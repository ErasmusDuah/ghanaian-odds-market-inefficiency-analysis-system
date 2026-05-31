"""
engine/quasi_arb_logger.py
──────────────────────────
Parallel ML logging system for quasi-arbitrage opportunities.
Writes to data/quasi_arb_ml.xlsx — a clean dataset for model training.

Deduplication rules (scanner runs every 2-5 mins, same opp can repeat):
  - First time a match + market + profit side + line appears  → LOG
  - Profit margin shifts by ≥ 0.10% from last logged value   → LOG (new row)
  - Same key, same side, margin within 0.10% threshold       → SKIP

Old rows are NEVER overwritten. New rows are always appended so the
time-evolution of a signal before kickoff is preserved for the ML model.

The unique dedup key is:
    home_team | away_team | match_date | market_type | profit_side | line

Does NOT touch engine/arb_tracker.py or data/arbitrage_tracker.csv.
"""

import os
from datetime import datetime

import pandas as pd

# ── CONFIG ─────────────────────────────────────────────────────────────────────

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUASI_ML_FILE = os.path.join(_ROOT, 'data', 'quasi_arb_ml.xlsx')

MARGIN_THRESHOLD = 0.10   # % — profit margin shifts smaller than this are noise

COLUMNS = [
    'Timestamp',
    'Home Team',
    'Away Team',
    'Match Date',
    'Country',
    'League',
    'Market',
    'Line',
    'Profit Side',
    'Profit Margin (%)',
    'Profit (GHS)',
    'Hours Before Kickoff',
    'Bookmakers Involved',
    'Result (Total Goals)',
    '1st Half Goals',
    '2nd Half Goals',
    'Profit Side Hit',
]

# ── IN-MEMORY DEDUP INDEX ──────────────────────────────────────────────────────
# key → (profit_side, profit_margin_pct) — most recently logged values per key
_seen: dict = {}
_index_loaded = False


def _load_index():
    """
    Build dedup index from the existing xlsx on first call.
    Iterates all rows so the LAST logged value per key is stored —
    that is what we compare against on the next scan.
    """
    global _seen, _index_loaded
    if _index_loaded:
        return
    _index_loaded = True

    if not os.path.exists(QUASI_ML_FILE):
        return

    try:
        df = pd.read_excel(QUASI_ML_FILE, engine='openpyxl', dtype=str)
        for _, row in df.iterrows():
            key = _make_key(
                str(row.get('Home Team',    '') or ''),
                str(row.get('Away Team',    '') or ''),
                str(row.get('Match Date',   '') or ''),
                str(row.get('Market',       '') or ''),
                str(row.get('Profit Side',  '') or ''),
                str(row.get('Line',         '') or ''),
            )
            try:
                margin = float(row.get('Profit Margin (%)', 0) or 0)
            except (ValueError, TypeError):
                margin = 0.0
            _seen[key] = (str(row.get('Profit Side', '') or ''), margin)
    except Exception as e:
        # If the file is unreadable, start fresh — don't crash the engine
        print(f"  [Quasi ML] WARNING: Could not load existing index (starting fresh): {e}")


# ── KEY BUILDER ────────────────────────────────────────────────────────────────

def _make_key(home: str, away: str, match_date: str,
              market: str, profit_side: str, line: str) -> str:
    """Lowercase, pipe-separated dedup key."""
    return '|'.join([
        home.strip().lower(),
        away.strip().lower(),
        match_date.strip(),
        market.strip().lower(),
        profit_side.strip().lower(),
        str(line).strip(),
    ])


# ── FIELD PARSERS ──────────────────────────────────────────────────────────────

def _parse_market(market_str: str):
    """
    Split market string into (market_type, line).

    'Over/Under 4.5'  →  ('Over/Under', '4.5')
    '1X2'             →  ('1X2', '')
    'GG/NG'           →  ('GG/NG', '')
    """
    parts = market_str.strip().rsplit(' ', 1)
    if len(parts) == 2:
        try:
            float(parts[1])          # last token is a number → it's the line
            return parts[0], parts[1]
        except ValueError:
            pass
    return market_str.strip(), ''


def _shorten_profit_side(best_outcome: str, line: str) -> str:
    """
    Convert the engine's outcome label into a compact profit-side code.

    Over/Under legs:
        'Over 4.5'  →  'O4.5'
        'Under 4.5' →  'U4.5'

    1X2 legs:
        'Home Win'  →  'Home'
        'Away Win'  →  'Away'
        'Draw'      →  'Draw'

    GG/NG legs:
        'GG Yes'    →  'GG'
        'GG No'     →  'NG'

    Unknown / future markets → raw label passed through unchanged (no crash).
    """
    b = best_outcome.strip()
    b_low = b.lower()

    if b_low.startswith('over '):
        return f"O{line}"
    if b_low.startswith('under '):
        return f"U{line}"
    if b_low == 'home win':
        return 'Home'
    if b_low == 'away win':
        return 'Away'
    if b_low == 'draw':
        return 'Draw'
    if b_low == 'gg yes':
        return 'GG'
    if b_low == 'gg no':
        return 'NG'

    # Fallback — unknown market type; log the raw label so we never crash
    return b


def _parse_tournament(tournament_str: str):
    """
    Split 'Country. League Name' into (country, league).

    'Ecuador. LigaPro Primera A'           →  ('Ecuador', 'LigaPro Primera A')
    'Brazil. Campeonato Brasileiro Série D' →  ('Brazil', 'Campeonato Brasileiro Série D')
    'Premier League'                        →  ('', 'Premier League')
    """
    if '.' in tournament_str:
        parts = tournament_str.split('.', 1)
        return parts[0].strip(), parts[1].strip()
    return '', tournament_str.strip()


def _hours_before_kickoff(kickoff_str: str):
    """
    Calculate hours between now and kickoff.
    '2026-05-31 18:00' → 0.55  (float, rounded to 2dp)
    Returns '' on parse failure so the cell stays blank rather than crashing.
    """
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%dT%H:%M:%S'):
        try:
            ko = datetime.strptime(kickoff_str.strip(), fmt)
            delta = (ko - datetime.now()).total_seconds()
            return round(delta / 3600, 2)
        except ValueError:
            continue
    return ''


# ── MAIN PUBLIC FUNCTION ───────────────────────────────────────────────────────

def log_quasi_opportunities(quasi_opps: list, total_stake: int | float):
    """
    Log quasi-arbitrage opportunities to data/quasi_arb_ml.xlsx.

    Called once per scan cycle from engine/intensive_engine.py → display_all().
    Wrapped in try/except there so any error here is non-fatal to the engine.

    Parameters
    ----------
    quasi_opps  : list of opportunity dicts with category == 'quasi'
    total_stake : total capital being staked (int/float, from .env)
    """
    _load_index()

    if not quasi_opps:
        print("  ✅ [Quasi ML] 0 quasi opportunities — nothing to log.")
        return

    now_str     = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    new_rows    = []
    skipped     = 0

    for opp in quasi_opps:
        match_str  = opp.get('match',            '')
        kickoff    = opp.get('kickoff',           '')
        tournament = opp.get('tournament',        '')
        market_str = opp.get('market',            '')
        best_out   = opp.get('best_outcome',      '')
        best_ghs   = float(opp.get('best_profit_ghs', 0.0))
        bets       = opp.get('bets',              [])

        # ── Parse all fields ──────────────────────────────────────────────────

        # Team names from 'Home vs Away'
        vs_parts = match_str.split(' vs ', 1)
        home = vs_parts[0].strip() if len(vs_parts) == 2 else match_str
        away = vs_parts[1].strip() if len(vs_parts) == 2 else ''

        match_date            = kickoff[:10] if kickoff else ''
        country, league       = _parse_tournament(tournament)
        market_type, line     = _parse_market(market_str)
        profit_side           = _shorten_profit_side(best_out, line)
        profit_margin_pct     = round(best_ghs / total_stake * 100, 4) \
                                if total_stake else 0.0
        hours_before          = _hours_before_kickoff(kickoff)
        bookmakers            = ', '.join(
                                    b.get('platform', '')
                                    for b in bets
                                    if b.get('platform')
                                )

        # ── Dedup check ───────────────────────────────────────────────────────
        key = _make_key(home, away, match_date, market_type, profit_side, line)

        if key in _seen:
            _last_side, last_margin = _seen[key]
            margin_delta = abs(profit_margin_pct - last_margin)
            if margin_delta < MARGIN_THRESHOLD:
                skipped += 1
                continue   # same opp, margin unchanged — skip

        # ── Build row ─────────────────────────────────────────────────────────
        row = {
            'Timestamp':            now_str,
            'Home Team':            home,
            'Away Team':            away,
            'Match Date':           match_date,
            'Country':              country,
            'League':               league,
            'Market':               market_type,
            'Line':                 line,
            'Profit Side':          profit_side,
            'Profit Margin (%)':    profit_margin_pct,
            'Profit (GHS)':         round(best_ghs, 2),
            'Hours Before Kickoff': hours_before,
            'Bookmakers Involved':  bookmakers,
            'Result (Total Goals)': '',
            '1st Half Goals':       '',
            '2nd Half Goals':       '',
            'Profit Side Hit':      '',
        }
        new_rows.append(row)
        _seen[key] = (profit_side, profit_margin_pct)   # update live index

    # ── Write to Excel ────────────────────────────────────────────────────────
    if not new_rows:
        print(f"  [Quasi ML] All {skipped} opportunity/ies unchanged -- dedup skipped. "
              f"(quasi_arb_ml.xlsx untouched)")
        return

    new_df = pd.DataFrame(new_rows, columns=COLUMNS)

    if os.path.exists(QUASI_ML_FILE):
        try:
            existing_df = pd.read_excel(QUASI_ML_FILE, engine='openpyxl')
            combined_df = pd.concat([existing_df, new_df], ignore_index=True)
        except Exception:
            combined_df = new_df   # corrupted file → start fresh, don't crash
    else:
        combined_df = new_df

    combined_df.to_excel(QUASI_ML_FILE, index=False, engine='openpyxl')

    skipped_msg = f"  ({skipped} skipped by dedup)" if skipped else ""
    print(f"  [Quasi ML] Logged {len(new_rows)} new row(s) -> {QUASI_ML_FILE}{skipped_msg}")
