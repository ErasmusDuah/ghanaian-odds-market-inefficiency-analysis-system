"""
Quant Bet Alpha — FastAPI Backend Server
Runs the scrapers, serves arb opportunities, and manages the ledger.
"""

import sys
import os
import json
import asyncio
import time
from datetime import datetime
from typing import Optional

# Fix import paths
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

from api.database import (
    get_all_bets, get_bet, log_bet,
    update_bet_status, delete_bet,
    get_stats, update_capital
)

app = FastAPI(title="Quant Bet Alpha API", version="1.0.0")

# Allow the Next.js frontend to talk to this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── SCAN STATE ────────────────────────────────────────────────────────────────
# Shared state for the background scan process
scan_state = {
    "status":        "idle",      # idle | scanning | done | error
    "last_scanned":  None,
    "opportunities": [],
    "events_scanned": 0,
    "cycle_time":    None,
    "error":         None,
}


def run_scan_background():
    """Runs the full scrape + arb scan in a background thread."""
    scan_state["status"] = "scanning"
    scan_state["error"]  = None
    start = time.time()

    try:
        from football.scrapers.fb_sportybet    import run as fetch_sportybet
        from football.scrapers.fb_betway       import run as fetch_betway
        from football.scrapers.fb_footballcom  import run as fetch_footballcom
        from football.scrapers.fb_onexbet      import run as fetch_onexbet
        from football.scrapers.fb_twentytwobet import run as fetch_twentytwobet
        from football.engine.fb_arbitrage_engine import scan_all

        total_stake = int(os.getenv('STARTING_CAPITAL', 500))

        sportybet_matches    = fetch_sportybet()    or []
        betway_matches       = fetch_betway()       or []
        footballcom_matches  = fetch_footballcom()  or []
        onexbet_matches      = fetch_onexbet()      or []
        twentytwobet_matches = fetch_twentytwobet() or []

        opportunities, events_scanned = scan_all(
            sportybet_matches,
            betway_matches,
            footballcom_matches,
            onexbet_matches,
            twentytwobet_matches,
            total_stake=total_stake,
        )

        scan_state["opportunities"]  = opportunities
        scan_state["events_scanned"] = events_scanned
        scan_state["cycle_time"]     = round(time.time() - start, 1)
        scan_state["last_scanned"]   = datetime.now().isoformat()
        scan_state["status"]         = "done"

    except Exception as e:
        scan_state["status"] = "error"
        scan_state["error"]  = str(e)
        import traceback
        traceback.print_exc()


# ─── SCAN ENDPOINTS ────────────────────────────────────────────────────────────

@app.get("/api/status")
def get_status():
    """Returns current scan state and latest opportunities."""
    return {
        "status":        scan_state["status"],
        "last_scanned":  scan_state["last_scanned"],
        "events_scanned": scan_state["events_scanned"],
        "cycle_time":    scan_state["cycle_time"],
        "opportunities": scan_state["opportunities"],
        "error":         scan_state["error"],
    }


@app.post("/api/scan")
def trigger_scan(background_tasks: BackgroundTasks):
    """Triggers a fresh scan in the background."""
    if scan_state["status"] == "scanning":
        return {"message": "Scan already in progress", "status": "scanning"}
    background_tasks.add_task(run_scan_background)
    scan_state["status"] = "scanning"
    return {"message": "Scan started", "status": "scanning"}


# ─── LEDGER ENDPOINTS ──────────────────────────────────────────────────────────

class LogBetRequest(BaseModel):
    opportunity_index: int  # Index into scan_state["opportunities"]


class UpdateBetRequest(BaseModel):
    status: str  # "won" or "lost"


class UpdateCapitalRequest(BaseModel):
    amount: float


@app.get("/api/ledger")
def get_ledger(limit: int = 100):
    """Returns all logged bets."""
    return get_all_bets(limit)


@app.get("/api/ledger/{bet_id}")
def get_ledger_entry(bet_id: int):
    bet = get_bet(bet_id)
    if not bet:
        raise HTTPException(status_code=404, detail="Bet not found")
    return bet


@app.post("/api/ledger/log")
def log_staked_bet(req: LogBetRequest):
    """
    Logs a bet from the latest scan results.
    Call this when the user clicks 'Staked' on an opportunity.
    """
    opps = scan_state.get("opportunities", [])
    if req.opportunity_index >= len(opps):
        raise HTTPException(status_code=400, detail="Invalid opportunity index")

    opp = opps[req.opportunity_index]
    bet_id = log_bet(opp)
    return {"message": "Bet logged", "bet_id": bet_id}


@app.put("/api/ledger/{bet_id}")
def update_bet(bet_id: int, req: UpdateBetRequest):
    """Mark a bet as won or lost."""
    if req.status not in ("won", "lost", "pending"):
        raise HTTPException(status_code=400, detail="Status must be: won, lost, or pending")
    return update_bet_status(bet_id, req.status)


@app.delete("/api/ledger/{bet_id}")
def remove_bet(bet_id: int):
    """Remove a bet entry from the ledger."""
    delete_bet(bet_id)
    return {"message": "Deleted"}


# ─── STATS ENDPOINT ────────────────────────────────────────────────────────────

@app.get("/api/stats")
def get_profit_stats():
    """Returns overall profit, win rate, and history."""
    return get_stats()


@app.put("/api/stats/capital")
def set_capital(req: UpdateCapitalRequest):
    """Update starting capital."""
    update_capital(req.amount)
    return {"message": "Capital updated", "amount": req.amount}


# ─── HEALTH CHECK ──────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


# ─── ENTRY POINT ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.server:app", host="0.0.0.0", port=8000, reload=True)
