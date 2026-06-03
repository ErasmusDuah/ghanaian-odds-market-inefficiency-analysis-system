"""
table_tennis/engine/tt_quasi_logger.py
──────────────────────────────────────
Parallel ML logging system for table tennis quasi-arbitrage opportunities.
Writes to table_tennis/data/tt_quasi_ml.xlsx.
"""

import os
from datetime import datetime
import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUASI_ML_FILE = os.path.join(_ROOT, 'data', 'tt_quasi_ml.xlsx')

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
    'Result (Total Points)',
    'Result (Sets)',
    'Profit Side Hit',
]

_seen = {}
_index_loaded = False


def _load_index():
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
                str(row.get('Home Team',          '') or ''),
                str(row.get('Away Team',          '') or ''),
                str(row.get('Match Date',         '') or ''),
                str(row.get('Market',             '') or ''),
                str(row.get('Profit Side',        '') or ''),
                str(row.get('Line',               '') or ''),
                str(row.get('Bookmakers Involved', '') or ''),
            )
            try:
                profit = round(float(row.get('Profit (GHS)', 0) or 0), 2)
            except (ValueError, TypeError):
                profit = 0.0
            _seen[key] = profit
    except Exception as e:
        print(f"  [TT Quasi ML] WARNING: Could not load existing index (starting fresh): {e}")


def _make_key(home: str, away: str, match_date: str,
              market: str, profit_side: str, line: str,
              bookmakers: str = '') -> str:
    sorted_books = '+'.join(sorted(b.strip().lower() for b in bookmakers.split(',') if b.strip()))
    return '|'.join([
        home.strip().lower(),
        away.strip().lower(),
        match_date.strip(),
        market.strip().lower(),
        profit_side.strip().lower(),
        str(line).strip(),
        sorted_books,
    ])


def _shorten_profit_side(best_outcome: str) -> str:
    b_low = best_outcome.strip().lower()
    if 'home' in b_low:
        return 'Home'
    if 'away' in b_low:
        return 'Away'
    return best_outcome


def _parse_tournament(tournament_str: str):
    if '.' in tournament_str:
        parts = tournament_str.split('.', 1)
        return parts[0].strip(), parts[1].strip()
    return '', tournament_str.strip()


def _hours_before_kickoff(kickoff_str: str):
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%dT%H:%M:%S'):
        try:
            ko = datetime.strptime(kickoff_str.strip(), fmt)
            delta = (ko - datetime.now()).total_seconds()
            return round(delta / 3600, 2)
        except ValueError:
            continue
    return ''


def log_quasi_opportunities(quasi_opps: list, total_stake: int | float, quiet=False):
    """
    Log table tennis quasi-arbitrage opportunities to table_tennis/data/tt_quasi_ml.xlsx.
    """
    _load_index()

    if not quasi_opps:
        msg = "  📊 [TT Quasi ML] No new Quasi-Arb opportunities found at this time of the scan"
        if not quiet:
            print(msg)
        return msg

    now_str     = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    new_rows    = []
    skipped     = 0

    for opp in quasi_opps:
        match_str  = opp.get('match',            '')
        kickoff    = opp.get('kickoff',           '')
        tournament = opp.get('tournament',        '')
        market_str = opp.get('market',            'Winner')
        best_out   = opp.get('best_outcome',      '')
        best_ghs   = float(opp.get('best_profit_ghs', 0.0))
        bets       = opp.get('bets',              [])

        vs_parts = match_str.split(' vs ', 1)
        home = vs_parts[0].strip() if len(vs_parts) == 2 else match_str
        away = vs_parts[1].strip() if len(vs_parts) == 2 else ''

        match_date            = kickoff[:10] if kickoff else ''
        country, league       = _parse_tournament(tournament)
        profit_side           = _shorten_profit_side(best_out)
        hours_before_kickoff = _hours_before_kickoff(kickoff)

        books_list = [b.get('platform', '') for b in bets if b.get('platform')]
        books_str  = ', '.join(books_list)

        key = _make_key(home, away, match_date, market_str, profit_side, '', books_str)
        profit_rounded = round(best_ghs, 2)

        if key in _seen and _seen[key] == profit_rounded:
            skipped += 1
            continue

        _seen[key] = profit_rounded

        new_rows.append({
            'Timestamp': now_str,
            'Home Team': home,
            'Away Team': away,
            'Match Date': match_date,
            'Country': country,
            'League': league,
            'Market': market_str,
            'Line': '',
            'Profit Side': profit_side,
            'Profit Margin (%)': round(opp.get('profit_pct', 0.0), 2),
            'Profit (GHS)': profit_rounded,
            'Hours Before Kickoff': hours_before_kickoff,
            'Bookmakers Involved': books_str,
            'Result (Total Points)': '',
            'Result (Sets)': '',
            'Profit Side Hit': '',
        })

    if not new_rows:
        msg = f"  📊 [TT Quasi ML] Logged 0 rows ({skipped} skipped as duplicates)"
        if not quiet:
            print(msg)
        return msg

    try:
        os.makedirs(os.path.dirname(QUASI_ML_FILE), exist_ok=True)
        if os.path.exists(QUASI_ML_FILE):
            df_old = pd.read_excel(QUASI_ML_FILE, engine='openpyxl')
            df_new = pd.DataFrame(new_rows, columns=COLUMNS)
            df_combined = pd.concat([df_old, df_new], ignore_index=True)
        else:
            df_combined = pd.DataFrame(new_rows, columns=COLUMNS)

        df_combined.to_excel(QUASI_ML_FILE, index=False, engine='openpyxl')
        msg = f"  📊 [TT Quasi ML] Appended {len(new_rows)} new training rows ({skipped} skipped as duplicates) to table_tennis/data/tt_quasi_ml.xlsx"
    except Exception as e:
        msg = f"  ❌ [TT Quasi ML] FAILED to write to spreadsheet: {e}"

    if not quiet:
        print(msg)
    return msg
