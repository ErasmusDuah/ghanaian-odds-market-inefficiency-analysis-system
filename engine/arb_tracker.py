import csv
import os
import subprocess
import sys
from datetime import datetime

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

TRACKER_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'arbitrage_tracker.csv')

HEADERS = [
    'Date', 'Time',
    'Match', 'Tournament', 'Kickoff',
    'Market',
    'Platform 1', 'Bet 1', 'Odds 1', 'Stake 1 (GHS)', 'Win 1 (GHS)',
    'Platform 2', 'Bet 2', 'Odds 2', 'Stake 2 (GHS)', 'Win 2 (GHS)',
    'Platform 3', 'Bet 3', 'Odds 3', 'Stake 3 (GHS)', 'Win 3 (GHS)',
    'Total Stake (GHS)', 'Profit %', 'Profit (GHS)'
]

def ensure_tracker():
    if not os.path.exists(TRACKER_FILE):
        os.makedirs(os.path.dirname(TRACKER_FILE), exist_ok=True)
        with open(TRACKER_FILE, 'w', newline='', encoding='utf-8') as f:
            csv.writer(f).writerow(HEADERS)

def push_to_github():
    """Automatically commit and push the updated tracker to GitHub."""
    cwd = os.path.dirname(os.path.dirname(__file__))
    try:
        # Add the specific file to git
        subprocess.run(["git", "add", "data/arbitrage_tracker.csv"], cwd=cwd, check=True, capture_output=True)
        
        # Check if there are changes to commit
        status = subprocess.run(["git", "status", "--porcelain"], cwd=cwd, capture_output=True, text=True)
        if "data/arbitrage_tracker.csv" in status.stdout:
            subprocess.run(["git", "commit", "-m", "Auto-update arbitrage tracker"], cwd=cwd, check=True, capture_output=True)
            subprocess.run(["git", "push"], cwd=cwd, check=True, capture_output=True)
            print("  ✅ [Arb Tracker] Tracker saved locally and synced to GitHub.")
        else:
            print("  ✅ [Arb Tracker] Tracker saved locally (no new changes for GitHub).")
    except subprocess.CalledProcessError as e:
        print(f"  ⚠️ [Arb Tracker] Error syncing to GitHub: {e.stderr if e.stderr else e}")
    except Exception as e:
        print(f"  ⚠️ [Arb Tracker] Error syncing to GitHub: {e}")

def save_arbitrage_opportunities(opportunities, total_stake):
    """
    Saves a list of arbitrage opportunities to the CSV tracker and syncs with GitHub.
    Always logs a row — if no arbs found, writes a row of 0s for ML continuity.
    """
    ensure_tracker()
    
    rows = []
    now = datetime.now()
    date_str = now.strftime('%Y-%m-%d')
    time_str = now.strftime('%H:%M:%S')

    if not opportunities:
        rows.append({
            'Date': date_str, 'Time': time_str,
            'Match': 0, 'Tournament': 0, 'Kickoff': 0, 'Market': 0,
            'Platform 1': 0, 'Bet 1': 0, 'Odds 1': 0, 'Stake 1 (GHS)': 0, 'Win 1 (GHS)': 0,
            'Platform 2': 0, 'Bet 2': 0, 'Odds 2': 0, 'Stake 2 (GHS)': 0, 'Win 2 (GHS)': 0,
            'Platform 3': 0, 'Bet 3': 0, 'Odds 3': 0, 'Stake 3 (GHS)': 0, 'Win 3 (GHS)': 0,
            'Total Stake (GHS)': total_stake, 'Profit %': 0, 'Profit (GHS)': 0
        })

    for opp in opportunities:
        bets = opp.get('bets', [])
        
        p1 = bets[0] if len(bets) > 0 else {}
        p2 = bets[1] if len(bets) > 1 else {}
        p3 = bets[2] if len(bets) > 2 else {}
        
        row = {
            'Date': date_str,
            'Time': time_str,
            'Match': opp.get('match', ''),
            'Tournament': opp.get('tournament', ''),
            'Kickoff': opp.get('kickoff', ''),
            'Market': opp.get('market', ''),
            'Platform 1': p1.get('platform', ''),
            'Bet 1': p1.get('outcome', ''),
            'Odds 1': p1.get('odds', ''),
            'Stake 1 (GHS)': round(p1.get('stake', 0), 2) if p1.get('stake') else '',
            'Win 1 (GHS)': round(p1.get('profit_if_wins', 0), 2) if p1.get('profit_if_wins') else '',
            'Platform 2': p2.get('platform', ''),
            'Bet 2': p2.get('outcome', ''),
            'Odds 2': p2.get('odds', ''),
            'Stake 2 (GHS)': round(p2.get('stake', 0), 2) if p2.get('stake') else '',
            'Win 2 (GHS)': round(p2.get('profit_if_wins', 0), 2) if p2.get('profit_if_wins') else '',
            'Platform 3': p3.get('platform', ''),
            'Bet 3': p3.get('outcome', ''),
            'Odds 3': p3.get('odds', '') if p3 else '',
            'Stake 3 (GHS)': round(p3.get('stake', 0), 2) if p3.get('stake') else '',
            'Win 3 (GHS)': round(p3.get('profit_if_wins', 0), 2) if p3.get('profit_if_wins') else '',
            'Total Stake (GHS)': total_stake,
            'Profit %': round(opp.get('profit_pct', 0), 2),
            'Profit (GHS)': round(opp.get('profit_ghs', 0), 2)
        }
        rows.append(row)

    with open(TRACKER_FILE, 'a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        writer.writerows(rows)
    
    if not opportunities:
        print(f"\n  💾 [Arb Tracker] 0 opportunities found — logged 0s row to data/arbitrage_tracker.csv")
    else:
        print(f"\n  💾 [Arb Tracker] Appended {len(rows)} opportunities to data/arbitrage_tracker.csv")
    
    # Push to Github
    push_to_github()
