"""
Quant Bet Alpha — Bet Tracker
Simple ledger to document every arb stake and track your profit.

Usage:
    python tracker.py          → view your profit summary
    python tracker.py log      → add a new bet entry
    python tracker.py result   → mark a bet as Won or Lost
"""

import csv
import os
import sys
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

LEDGER_FILE = os.path.join(os.path.dirname(__file__), 'data', 'ledger.csv')
STARTING_CAPITAL = float(os.getenv('STARTING_CAPITAL', 500))

HEADERS = [
    'ID', 'Date', 'Time',
    'Match', 'League',
    'Market',
    'Platform 1', 'Bet 1', 'Odds 1', 'Stake 1 (GHS)',
    'Platform 2', 'Bet 2', 'Odds 2', 'Stake 2 (GHS)',
    'Total Staked (GHS)', 'Expected Profit %', 'Expected Profit (GHS)',
    'Status',
    'Actual Return (GHS)', 'Actual Profit/Loss (GHS)',
    'Running Balance (GHS)',
    'Notes'
]

SEP  = "=" * 65
LINE = "-" * 65


def ensure_ledger():
    if not os.path.exists(LEDGER_FILE):
        with open(LEDGER_FILE, 'w', newline='', encoding='utf-8') as f:
            csv.writer(f).writerow(HEADERS)


def read_all():
    ensure_ledger()
    with open(LEDGER_FILE, 'r', encoding='utf-8') as f:
        return list(csv.DictReader(f))


def write_all(rows):
    with open(LEDGER_FILE, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=HEADERS)
        w.writeheader()
        w.writerows(rows)


def recalc_balances(rows):
    """Recalculate running balance after every change."""
    balance = STARTING_CAPITAL
    for r in rows:
        pl = r.get('Actual Profit/Loss (GHS)', '')
        if pl and r['Status'] in ('Won', 'Lost'):
            balance = round(balance + float(pl), 2)
            r['Running Balance (GHS)'] = balance
        else:
            r['Running Balance (GHS)'] = ''
    return rows


def next_id(rows):
    return max((int(r['ID']) for r in rows), default=0) + 1


def ask(label, default=None):
    suffix = f" [{default}]" if default else ""
    val = input(f"  {label}{suffix}: ").strip()
    return val if val else (default or "")


# ─── LOG ───────────────────────────────────────────────────────────────────────

def log_bet():
    print(f"\n{SEP}")
    print("  LOG NEW STAKED BET")
    print(SEP)

    rows = read_all()
    now  = datetime.now()

    match  = ask("Match (e.g. Aston Villa vs Tottenham)")
    league = ask("League (e.g. Premier League)")
    market = ask("Market (e.g. Over/Under 2.5)")

    print()
    p1 = ask("Platform 1 (e.g. 1xBet)")
    b1 = ask("  Bet (e.g. Over 2.5)")
    o1 = float(ask("  Odds"))
    s1 = float(ask("  Stake (GHS)"))

    print()
    p2 = ask("Platform 2 (e.g. Betway)")
    b2 = ask("  Bet (e.g. Under 2.5)")
    o2 = float(ask("  Odds"))
    s2 = float(ask("  Stake (GHS)"))

    total   = round(s1 + s2, 2)
    arb_sum = 1/o1 + 1/o2
    exp_pct = round((1 - arb_sum) / arb_sum * 100, 2) if arb_sum < 1 else 0
    exp_ghs = round(total * exp_pct / 100, 2)

    notes = ask("Notes (optional)", default="")

    row = {
        'ID':                        next_id(rows),
        'Date':                      now.strftime('%Y-%m-%d'),
        'Time':                      now.strftime('%H:%M'),
        'Match':                     match,
        'League':                    league,
        'Market':                    market,
        'Platform 1':                p1,
        'Bet 1':                     b1,
        'Odds 1':                    o1,
        'Stake 1 (GHS)':             s1,
        'Platform 2':                p2,
        'Bet 2':                     b2,
        'Odds 2':                    o2,
        'Stake 2 (GHS)':             s2,
        'Total Staked (GHS)':        total,
        'Expected Profit %':         exp_pct,
        'Expected Profit (GHS)':     exp_ghs,
        'Status':                    'Pending',
        'Actual Return (GHS)':       '',
        'Actual Profit/Loss (GHS)':  '',
        'Running Balance (GHS)':     '',
        'Notes':                     notes,
    }

    rows.append(row)
    rows = recalc_balances(rows)
    write_all(rows)

    print(f"\n  ✅ Bet #{row['ID']} logged!")
    print(f"     {match} | {market}")
    print(f"     Total Staked : GHS {total}")
    print(f"     Expected Win : GHS {exp_ghs} ({exp_pct}%)")
    print(f"\n  💡 Open data/ledger.csv in Excel to see your ledger.\n")


# ─── RESULT ────────────────────────────────────────────────────────────────────

def update_result():
    rows = read_all()
    pending = [r for r in rows if r['Status'] == 'Pending']

    if not pending:
        print("\n  No pending bets to update.\n")
        return

    print(f"\n{SEP}")
    print("  PENDING BETS")
    print(SEP)
    for r in pending:
        print(f"  #{r['ID']}  {r['Date']}  {r['Match'][:35]}  |  {r['Market']}  |  Exp: GHS {r['Expected Profit (GHS)']}")

    print()
    bet_id = ask("Bet ID to update")
    result = ask("Result (won / lost)").lower()

    if result not in ('won', 'lost'):
        print("  Invalid. Enter 'won' or 'lost'.\n")
        return

    found = False
    for r in rows:
        if str(r['ID']) == str(bet_id):
            found = True
            r['Status'] = result.capitalize()
            total  = float(r['Total Staked (GHS)'])
            exp    = float(r['Expected Profit (GHS)'])

            if result == 'won':
                actual_return = round(total + exp, 2)
                pl = round(exp, 2)
            else:
                actual_return = 0
                pl = round(-total, 2)

            r['Actual Return (GHS)']      = actual_return
            r['Actual Profit/Loss (GHS)'] = pl
            break

    if not found:
        print(f"  Bet #{bet_id} not found.\n")
        return

    rows = recalc_balances(rows)
    write_all(rows)

    print(f"\n  ✅ Bet #{bet_id} marked as {'WON 🎉' if result == 'won' else 'LOST 😔'}")
    print(f"     Profit/Loss: GHS {pl:+.2f}")
    print()


# ─── SUMMARY ───────────────────────────────────────────────────────────────────

def show_summary():
    rows = read_all()

    won     = [r for r in rows if r['Status'] == 'Won']
    lost    = [r for r in rows if r['Status'] == 'Lost']
    pending = [r for r in rows if r['Status'] == 'Pending']

    gross_profit  = sum(float(r['Actual Profit/Loss (GHS)']) for r in won)
    gross_loss    = sum(float(r['Actual Profit/Loss (GHS)']) for r in lost)
    net_profit    = round(gross_profit + gross_loss, 2)
    total_staked  = sum(float(r['Total Staked (GHS)']) for r in rows if r.get('Total Staked (GHS)'))
    pending_exp   = sum(float(r['Expected Profit (GHS)']) for r in pending)
    win_rate      = round(len(won) / (len(won) + len(lost)) * 100, 1) if (won or lost) else 0
    current_bal   = STARTING_CAPITAL + net_profit

    print(f"\n{SEP}")
    print("  QUANT BET ALPHA — PROFIT SUMMARY")
    print(SEP)
    print(f"  Starting Capital : GHS {STARTING_CAPITAL:.2f}")
    print(f"  Current Balance  : GHS {current_bal:.2f}")
    print(f"  Net Profit       : GHS {net_profit:+.2f}")
    print(LINE)
    print(f"  Total Bets  : {len(rows)}  |  Won: {len(won)}  |  Lost: {len(lost)}  |  Pending: {len(pending)}")
    print(f"  Win Rate    : {win_rate}%")
    print(f"  Total Staked (all time) : GHS {total_staked:.2f}")
    print(f"  Gross Profit (wins)     : GHS {gross_profit:.2f}")
    print(f"  Gross Loss (losses)     : GHS {abs(gross_loss):.2f}")
    print(f"  Pending Expected Profit : GHS {pending_exp:.2f}")
    print(SEP)

    if not rows:
        print("  No bets logged yet.\n  Run:  python tracker.py log\n")
        return

    # Recent entries
    print(f"\n  {'#':<4} {'Date':<12} {'Match':<28} {'Market':<16} {'Status':<9} {'P/L':>9}")
    print(f"  {LINE}")
    for r in rows[-15:]:
        pl_str = f"GHS {float(r['Actual Profit/Loss (GHS)']):+.2f}" if r['Actual Profit/Loss (GHS)'] else f"({r['Expected Profit (GHS)']} exp)"
        print(f"  {r['ID']:<4} {r['Date']:<12} {r['Match'][:27]:<28} {r['Market'][:15]:<16} {r['Status']:<9} {pl_str:>12}")

    print(f"\n  📂 Full ledger: data/ledger.csv  (open in Excel)\n")


# ─── MAIN ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ensure_ledger()
    cmd = sys.argv[1].lower() if len(sys.argv) > 1 else "summary"

    if cmd == "log":
        log_bet()
    elif cmd in ("result", "update"):
        update_result()
    else:
        show_summary()
