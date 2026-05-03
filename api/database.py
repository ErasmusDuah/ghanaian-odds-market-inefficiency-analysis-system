"""
Quant Bet Alpha — SQLite Ledger Database
Handles all bet tracking, profit history and stats.
"""

import sqlite3
import os
from datetime import datetime
from typing import Optional

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'ledger.db')


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # Return rows as dicts
    return conn


def init_db():
    """Create all tables if they don't exist yet."""
    conn = get_connection()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS bets (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at          TEXT    NOT NULL,
            match               TEXT    NOT NULL,
            tournament          TEXT,
            kickoff             TEXT,
            market              TEXT,

            platform_1          TEXT,
            bet_1               TEXT,
            odds_1              REAL,
            stake_1             REAL,

            platform_2          TEXT,
            bet_2               TEXT,
            odds_2              REAL,
            stake_2             REAL,

            platform_3          TEXT,
            bet_3               TEXT,
            odds_3              REAL,
            stake_3             REAL,

            total_stake         REAL,
            expected_profit_pct REAL,
            expected_profit_ghs REAL,

            status              TEXT    DEFAULT 'pending',
            actual_profit_ghs   REAL    DEFAULT 0,
            notes               TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    # Seed default capital if not set
    c.execute("""
        INSERT OR IGNORE INTO settings (key, value)
        VALUES ('total_capital', '500')
    """)

    conn.commit()
    conn.close()


# ─── BET OPERATIONS ────────────────────────────────────────────────────────────

def log_bet(opportunity: dict) -> int:
    """
    Creates a new ledger entry from an arb opportunity.
    Returns the new bet ID.
    """
    bets = opportunity.get('bets', [])
    def safe(idx, key):
        return bets[idx][key] if len(bets) > idx else None

    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        INSERT INTO bets (
            created_at, match, tournament, kickoff, market,
            platform_1, bet_1, odds_1, stake_1,
            platform_2, bet_2, odds_2, stake_2,
            platform_3, bet_3, odds_3, stake_3,
            total_stake, expected_profit_pct, expected_profit_ghs,
            status, actual_profit_ghs
        ) VALUES (?,?,?,?,?, ?,?,?,?, ?,?,?,?, ?,?,?,?, ?,?,?, ?,?)
    """, (
        datetime.now().isoformat(),
        opportunity.get('match'),
        opportunity.get('tournament'),
        opportunity.get('kickoff'),
        opportunity.get('market'),

        safe(0, 'platform'), safe(0, 'outcome'), safe(0, 'odds'), safe(0, 'stake'),
        safe(1, 'platform'), safe(1, 'outcome'), safe(1, 'odds'), safe(1, 'stake'),
        safe(2, 'platform'), safe(2, 'outcome'), safe(2, 'odds'), safe(2, 'stake'),

        opportunity.get('total_stake', sum(b['stake'] for b in bets)),
        opportunity.get('profit_pct'),
        opportunity.get('profit_ghs'),

        'pending',
        0,
    ))
    bet_id = c.lastrowid
    conn.commit()
    conn.close()
    return bet_id


def get_all_bets(limit: int = 100) -> list:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM bets ORDER BY created_at DESC LIMIT ?", (limit,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_bet(bet_id: int) -> Optional[dict]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM bets WHERE id = ?", (bet_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def update_bet_status(bet_id: int, status: str) -> dict:
    """
    Mark a bet as 'won' or 'lost'.
    For 'won': actual_profit = expected_profit_ghs
    For 'lost': actual_profit = -total_stake
    """
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT expected_profit_ghs, total_stake FROM bets WHERE id = ?", (bet_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return {"error": "Bet not found"}

    actual_profit = row['expected_profit_ghs'] if status == 'won' else -row['total_stake']

    c.execute("""
        UPDATE bets SET status = ?, actual_profit_ghs = ?
        WHERE id = ?
    """, (status, actual_profit, bet_id))
    conn.commit()
    conn.close()
    return {"id": bet_id, "status": status, "actual_profit_ghs": actual_profit}


def delete_bet(bet_id: int):
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM bets WHERE id = ?", (bet_id,))
    conn.commit()
    conn.close()


# ─── STATS ─────────────────────────────────────────────────────────────────────

def get_stats() -> dict:
    conn = get_connection()
    c = conn.cursor()

    c.execute("SELECT value FROM settings WHERE key = 'total_capital'")
    row = c.fetchone()
    capital = float(row['value']) if row else 500.0

    c.execute("SELECT COUNT(*) as total FROM bets")
    total_bets = c.fetchone()['total']

    c.execute("SELECT COUNT(*) as won FROM bets WHERE status = 'won'")
    total_won = c.fetchone()['won']

    c.execute("SELECT COUNT(*) as lost FROM bets WHERE status = 'lost'")
    total_lost = c.fetchone()['lost']

    c.execute("SELECT COUNT(*) as pending FROM bets WHERE status = 'pending'")
    total_pending = c.fetchone()['pending']

    c.execute("SELECT SUM(actual_profit_ghs) as profit FROM bets WHERE status IN ('won','lost')")
    total_profit = c.fetchone()['profit'] or 0.0

    c.execute("SELECT SUM(expected_profit_ghs) as exp FROM bets WHERE status = 'pending'")
    pending_profit = c.fetchone()['exp'] or 0.0

    c.execute("""
        SELECT created_at, actual_profit_ghs
        FROM bets
        WHERE status IN ('won','lost')
        ORDER BY created_at ASC
    """)
    profit_history = [{"date": r['created_at'], "profit": r['actual_profit_ghs']} for r in c.fetchall()]

    conn.close()

    win_rate = round((total_won / (total_won + total_lost)) * 100, 1) if (total_won + total_lost) > 0 else 0

    return {
        "capital":         capital,
        "current_balance": round(capital + total_profit, 2),
        "total_profit":    round(total_profit, 2),
        "pending_profit":  round(pending_profit, 2),
        "total_bets":      total_bets,
        "total_won":       total_won,
        "total_lost":      total_lost,
        "total_pending":   total_pending,
        "win_rate":        win_rate,
        "profit_history":  profit_history,
    }


def update_capital(amount: float):
    conn = get_connection()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('total_capital', ?)", (str(amount),))
    conn.commit()
    conn.close()


# Initialize on import
init_db()
