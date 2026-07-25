"""
Quasi-arb ML logger.

Writes quasi arbitrage opportunities to data/quasi_arb_ml.xlsx as a clean
change-log for later ML training.

Deduplication rule:
  - Same opportunity identity + same change signature: skip.
  - Same teams/market/profit side but profit or platforms changed: append row.

Opportunity identity:
  home_team | away_team | match_date | market_type | line | profit_side

Change signature:
  profit_ghs | profit_margin_pct | sorted_bookmakers | sorted_bet_legs

Old rows are never overwritten. New rows are appended so the time evolution of a
quasi signal before kickoff is preserved.
"""

from __future__ import annotations

import math
import os
from datetime import datetime
from typing import Any

import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUASI_ML_FILE = os.path.join(_ROOT, 'data', 'quasi_arb_ml.xlsx')

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
    'Profit Side Hit',
    'Profit Margin (%)',
    'Profit (GHS)',
    'Hours Before Kickoff',
    'Bookmakers Involved',
    'Result (Total Goals)',
    '1st Half Goals',
    '2nd Half Goals',
    'Home Goals',
    'Away Goals',
    'Total Bookings',
    'Home Bookings',
    'Away Bookings',
    'Winning Outcome',
    'Change Signature',
]

# opportunity identity key -> set(change signatures already written)
_seen: dict[str, set[str]] = {}
# Old workbook rows created before Change Signature existed can only be compared
# by profit + margin + books. Keep them separate so new rows can use fuller
# bet-leg signatures without being over-deduped.
_legacy_seen: dict[str, set[str]] = {}
_index_loaded = False


def ensure_quasi_ml_tracker() -> str:
    """Create the quasi ML workbook with the expected columns if it is missing."""
    os.makedirs(os.path.dirname(QUASI_ML_FILE), exist_ok=True)
    if not os.path.exists(QUASI_ML_FILE):
        pd.DataFrame(columns=COLUMNS).to_excel(QUASI_ML_FILE, index=False, engine='openpyxl')
        return f'  [Quasi ML] Created {os.path.basename(QUASI_ML_FILE)}'
    return f'  [Quasi ML] Using existing {os.path.basename(QUASI_ML_FILE)}'

def _clean_cell(value: Any) -> str:
    """Convert Excel blanks/NaN/None into stable empty strings."""
    if value is None:
        return ''
    try:
        if pd.isna(value):
            return ''
    except (TypeError, ValueError):
        pass
    if isinstance(value, float) and math.isnan(value):
        return ''
    text = str(value).strip()
    return '' if text.lower() in {'nan', 'none', 'nat'} else text


def _normalize_text(value: Any) -> str:
    return ' '.join(_clean_cell(value).lower().split())


def _normalize_line(value: Any) -> str:
    text = _clean_cell(value)
    if not text:
        return ''
    try:
        number = float(text)
    except ValueError:
        return _normalize_text(text)
    if number.is_integer():
        return f'{int(number)}.0'
    return str(number).rstrip('0').rstrip('.')


def _money(value: Any) -> float:
    try:
        return round(float(_clean_cell(value) or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _pct(value: Any) -> float:
    try:
        return round(float(_clean_cell(value) or 0), 4)
    except (TypeError, ValueError):
        return 0.0


def _normalized_bookmakers(bookmakers: Any) -> str:
    books = []
    for book in _clean_cell(bookmakers).split(','):
        normalized = _normalize_text(book)
        if normalized:
            books.append(normalized)
    return '+'.join(sorted(books))


def _make_key(home: str, away: str, match_date: str,
              market: str, profit_side: str, line: str,
              bookmakers: str = '') -> str:
    """
    Build the stable opportunity identity key.

    Bookmakers are intentionally excluded here. Platform changes belong in the
    change signature, so the same match/market/profit side can append a new row
    when the books involved change.
    """
    return '|'.join([
        _normalize_text(home),
        _normalize_text(away),
        _clean_cell(match_date),
        _normalize_text(market),
        _normalize_text(profit_side),
        _normalize_line(line),
    ])


def _normalized_bet_signature(bets: list | None) -> str:
    if not bets:
        return ''
    parts = []
    for bet in bets:
        platform = _normalize_text(bet.get('platform', ''))
        outcome = _normalize_text(bet.get('outcome', ''))
        odds = _clean_cell(bet.get('odds', ''))
        try:
            odds = str(float(odds)).rstrip('0').rstrip('.')
        except ValueError:
            odds = _normalize_text(odds)
        if platform or outcome or odds:
            parts.append(f'{platform}:{outcome}:{odds}')
    return '+'.join(sorted(parts))


def _make_signature(
    profit_ghs: Any,
    profit_margin_pct: Any,
    bookmakers: Any,
    bets: list | None = None,
) -> str:
    pieces = [
        f'{_money(profit_ghs):.2f}',
        f'{_pct(profit_margin_pct):.4f}',
        _normalized_bookmakers(bookmakers),
    ]
    bet_signature = _normalized_bet_signature(bets)
    if bet_signature:
        pieces.append(bet_signature)
    return '|'.join(pieces)


def _make_legacy_signature(profit_ghs: Any, profit_margin_pct: Any, bookmakers: Any) -> str:
    return '|'.join([
        f'{_money(profit_ghs):.2f}',
        f'{_pct(profit_margin_pct):.4f}',
        _normalized_bookmakers(bookmakers),
    ])


def _load_index() -> None:
    """
    Build the dedup index from the existing Excel file on first use.

    Every historical signature for an opportunity identity is loaded so an exact
    row is never appended twice, even after the scanner or machine restarts.
    """
    global _index_loaded
    if _index_loaded:
        return
    _index_loaded = True

    if not os.path.exists(QUASI_ML_FILE):
        ensure_quasi_ml_tracker()
        return

    try:
        df = pd.read_excel(QUASI_ML_FILE, engine='openpyxl', dtype=object)
    except Exception as exc:
        print(f'  [Quasi ML] WARNING: Could not load existing index; starting fresh: {exc}')
        return

    for _, row in df.iterrows():
        key = _make_key(
            row.get('Home Team', ''),
            row.get('Away Team', ''),
            row.get('Match Date', ''),
            row.get('Market', ''),
            row.get('Profit Side', ''),
            row.get('Line', ''),
        )
        if not key.strip('|'):
            continue
        signature = _clean_cell(row.get('Change Signature', ''))
        if not signature:
            signature = _make_legacy_signature(
                row.get('Profit (GHS)', 0),
                row.get('Profit Margin (%)', 0),
                row.get('Bookmakers Involved', ''),
            )
            _legacy_seen.setdefault(key, set()).add(signature)
        else:
            _seen.setdefault(key, set()).add(signature)


def _parse_market(market_str: str):
    """
    Split market string into (market_type, line).

    'Over/Under 4.5' -> ('Over/Under', '4.5')
    '1X2'            -> ('1X2', '')
    'GG/NG'          -> ('GG/NG', '')
    """
    text = _clean_cell(market_str)
    parts = text.rsplit(' ', 1)
    if len(parts) == 2:
        try:
            float(parts[1])
            return parts[0], _normalize_line(parts[1])
        except ValueError:
            pass
    return text, ''


def _shorten_profit_side(best_outcome: str, line: str) -> str:
    """Convert engine outcome labels into compact ML-friendly profit-side codes."""
    b = _clean_cell(best_outcome)
    b_low = b.lower()
    line = _normalize_line(line)

    if b_low.startswith('over '):
        return f'O{line}'
    if b_low.startswith('under '):
        return f'U{line}'
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
    if b_low == 'gg 2+ yes':
        return 'GG2+'
    if b_low == 'gg 2+ no':
        return 'NG2+'
    return b


def _parse_tournament(tournament_str: str):
    """Split 'Country. League Name' into (country, league)."""
    text = _clean_cell(tournament_str)
    if '.' in text:
        country, league = text.split('.', 1)
        return country.strip(), league.strip()
    return '', text


def _hours_before_kickoff(kickoff_str: str):
    """Calculate hours between now and kickoff; blank on parse failure."""
    text = _clean_cell(kickoff_str)
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%dT%H:%M:%S'):
        try:
            ko = datetime.strptime(text, fmt)
            delta = (ko - datetime.now()).total_seconds()
            return round(delta / 3600, 2)
        except ValueError:
            continue
    return ''


def _bookmakers_from_bets(bets: list) -> str:
    return ', '.join(
        _clean_cell(bet.get('platform', ''))
        for bet in bets
        if _clean_cell(bet.get('platform', ''))
    )


def _append_rows(new_df: pd.DataFrame) -> None:
    os.makedirs(os.path.dirname(QUASI_ML_FILE), exist_ok=True)

    if os.path.exists(QUASI_ML_FILE):
        try:
            existing_df = pd.read_excel(QUASI_ML_FILE, engine='openpyxl', dtype=object)
            # Keep existing user columns, but add any new system columns permanently.
            cols_to_use = list(existing_df.columns)
            for col in COLUMNS:
                if col not in cols_to_use:
                    cols_to_use.append(col)
            existing_df = existing_df.reindex(columns=cols_to_use)
            new_df_aligned = new_df.reindex(columns=cols_to_use)

            combined_df = pd.concat([existing_df, new_df_aligned], ignore_index=True)
            combined_df = combined_df[cols_to_use]
        except Exception as e:
            print(f"  [Quasi ML] ERROR: Could not read existing {os.path.basename(QUASI_ML_FILE)}: {e}")
            print(f"  [Quasi ML] Aborting write to protect existing data. New rows will be retried next scan.")
            raise
    else:
        combined_df = new_df

    tmp_path = QUASI_ML_FILE + '.tmp.xlsx'
    try:
        combined_df.to_excel(tmp_path, index=False, engine='openpyxl')
        os.replace(tmp_path, QUASI_ML_FILE)
    except PermissionError:
        print(f"  [Quasi ML] ERROR: Cannot write to {os.path.basename(QUASI_ML_FILE)} because it is locked (likely open in Excel). Close Excel so it can save.")
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        raise


def log_quasi_opportunities(quasi_opps: list, total_stake: int | float, quiet=False):
    """
    Append new quasi-arb rows to data/quasi_arb_ml.xlsx.

    Repeated scans skip exact repeats. Meaningful profit, bookmaker, or bet-leg
    changes are appended as new rows for ML history.
    """
    _load_index()

    if not quasi_opps:
        msg = '  [Quasi ML] No new Quasi-Arb opportunities found at this time of the scan'
        if not quiet:
            print(msg)
        return msg

    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    new_rows = []
    skipped = 0

    for opp in quasi_opps:
        match_str = _clean_cell(opp.get('match', ''))
        kickoff = _clean_cell(opp.get('kickoff', ''))
        tournament = _clean_cell(opp.get('tournament', ''))
        market_str = _clean_cell(opp.get('market', ''))
        best_out = _clean_cell(opp.get('best_outcome', ''))
        best_ghs = _money(opp.get('best_profit_ghs', 0.0))
        bets = opp.get('bets', []) or []

        vs_parts = match_str.split(' vs ', 1)
        home = vs_parts[0].strip() if len(vs_parts) == 2 else match_str
        away = vs_parts[1].strip() if len(vs_parts) == 2 else ''

        match_date = kickoff[:10] if kickoff else ''
        country, league = _parse_tournament(tournament)
        market_type, line = _parse_market(market_str)

        # Identify all profitable outcomes (excluding break-even outcome)
        break_even_out = _clean_cell(opp.get('break_even_outcome', ''))
        if not break_even_out and bets:
            be_bet = min(bets, key=lambda b: abs(_money(b.get('profit_if_wins', 0.0))))
            break_even_out = _clean_cell(be_bet.get('outcome', ''))

        profit_outcomes = []
        for bet in bets:
            outcome_name = _clean_cell(bet.get('outcome', ''))
            if outcome_name != break_even_out:
                p_val = _money(bet.get('profit_if_wins', 0.0))
                if p_val >= 0.10:  # Yields profit threshold
                    shortened = _shorten_profit_side(outcome_name, line)
                    if shortened and shortened not in profit_outcomes:
                        profit_outcomes.append(shortened)

        if profit_outcomes:
            profit_side = ", ".join(profit_outcomes)
        else:
            profit_side = _shorten_profit_side(best_out, line)

        profit_margin_pct = round(best_ghs / total_stake * 100, 4) if total_stake else 0.0
        hours_before = _hours_before_kickoff(kickoff)
        bookmakers = _bookmakers_from_bets(bets)

        key = _make_key(home, away, match_date, market_type, profit_side, line)
        signature = _make_signature(best_ghs, profit_margin_pct, bookmakers, bets)
        legacy_signature = _make_legacy_signature(best_ghs, profit_margin_pct, bookmakers)

        existing_signatures = _seen.get(key, set())
        legacy_signatures = _legacy_seen.get(key, set())
        if signature in existing_signatures or legacy_signature in legacy_signatures:
            skipped += 1
            continue

        row = {
            'Timestamp': now_str,
            'Home Team': home,
            'Away Team': away,
            'Match Date': match_date,
            'Country': country,
            'League': league,
            'Market': market_type,
            'Line': line,
            'Profit Side': profit_side,
            'Profit Margin (%)': profit_margin_pct,
            'Profit (GHS)': best_ghs,
            'Hours Before Kickoff': hours_before,
            'Bookmakers Involved': bookmakers,
            'Result (Total Goals)': '',
            '1st Half Goals': '',
            '2nd Half Goals': '',
            'Home Goals': '',
            'Away Goals': '',
            'Total Bookings': '',
            'Home Bookings': '',
            'Away Bookings': '',
            'Profit Side Hit': '',
            'Change Signature': signature,
        }
        new_rows.append(row)
        _seen.setdefault(key, set()).add(signature)

    if not new_rows:
        msg = f'  [Quasi ML] No new rows; skipped {skipped} unchanged quasi opportunities'
        if not quiet:
            print(msg)
        return msg

    new_df = pd.DataFrame(new_rows, columns=COLUMNS)
    _append_rows(new_df)

    msg = f'  [Quasi ML] {len(new_rows)} new rows added to quasi_arb_ml.xlsx'
    if skipped:
        msg += f' ({skipped} unchanged skipped)'
    if not quiet:
        print(msg)
    return msg
