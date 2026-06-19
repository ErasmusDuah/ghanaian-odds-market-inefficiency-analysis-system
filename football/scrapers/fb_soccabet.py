"""
Scrape today's not-started Soccabet Ghana football odds.

ULTRAFAST — Direct WebSocket connection to Soccabet's real-time feed.
No browser, no Playwright, no DOM parsing. Pure data.

Outputs:
  data/soccabet_odds.json
  data/soccabet_matches.txt
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp

try:
    from scrapers.lzstring import LZString
except ImportError:
    try:
        from lzstring import LZString
    except ImportError:
        import sys
        import os
        sys.path.append(os.path.dirname(os.path.abspath(__file__)))
        from lzstring import LZString


if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

SOURCE = "soccabet_gh"
WS_URL = "wss://www.soccabet.com/ws/"
SPORT_ID_SOCCER = "77"
OU_LINES = ("0.5", "1.5", "2.5", "3.5", "4.5", "5.5")

# Soccabet marketTypeId → our internal market name
# Discovered via WebSocket frame inspection:
#   5521 = 1X2 (3-way)
#   5054 = Over/Under
#   5030 = GG/NG (Both Teams to Score)
#   4978 = unknown (possibly live-specific, skip)
MARKET_TYPE_1X2_LIVE = 5521
MARKET_TYPE_1X2_PRE  = 4102
MARKET_TYPE_OU  = 5054
MARKET_TYPE_GG  = 5030


def banner(now: datetime) -> str:
    line = "⚽ " * 20
    return (
        f"{line}\n"
        "   SOCCABET GHANA SCRAPER\n"
        f"   {now.strftime('%A, %d %B %Y %H:%M:%S')}\n"
        f"{line}\n"
    )


async def ws_fetch_all(today_str: str, timeout_secs: float = 12.0) -> tuple[dict, dict]:
    """
    Connect to Soccabet WebSocket, subscribe for today's football,
    collect all match and market messages until the stream goes idle.
    
    Returns:
        (matches_by_id, markets_by_match_id)
    """
    matches: dict[int, dict] = {}
    markets: dict[int, list[dict]] = {}  # matchId → list of market dicts

    headers = {
        "Origin": "https://www.soccabet.com",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        ),
    }

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(
            WS_URL,
            headers=headers,
            timeout=aiohttp.ClientWSTimeout(**{"ws_close": 5.0}),
            heartbeat=20.0,
        ) as ws:
            # Subscribe for today's football with all markets
            subscribe_msg = json.dumps({
                "subscribe": {
                    "object": "sport",
                    "ids": SPORT_ID_SOCCER,
                    "marketfilter": "all",
                    "timerange": today_str,
                }
            })
            await ws.send_str(subscribe_msg)

            # Collect messages until we receive the "init" signal
            # that marks end of the initial data dump, then wait
            # a brief moment for any trailing messages.
            got_init = False
            idle_deadline = None

            while True:
                try:
                    msg = await asyncio.wait_for(ws.receive(), timeout=timeout_secs)
                except (asyncio.TimeoutError, TimeoutError):
                    break

                if msg.type == aiohttp.WSMsgType.TEXT:
                    data_str = msg.data
                    if not data_str.startswith("{"):
                        try:
                            data_str = LZString.decompressFromUTF16(data_str)
                        except Exception:
                            continue
                    try:
                        payload = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    # Process messages array
                    for item in payload.get("messages", []):
                        obj_type = item.get("object")
                        if obj_type == "match":
                            match_id = item.get("id")
                            if match_id is not None:
                                if match_id in matches:
                                    matches[match_id].update(item)
                                else:
                                    matches[match_id] = item
                        elif obj_type == "market":
                            match_id = item.get("matchId")
                            if match_id is not None:
                                if match_id not in markets:
                                    markets[match_id] = []
                                markets[match_id].append(item)
                        elif obj_type == "init":
                            got_init = True
                            idle_deadline = time.time() + 0.3

                    # Check subscription confirmations
                    sub = payload.get("subscription", {})
                    req = sub.get("request", {})
                    if req.get("object") == "sport":
                        pass  # subscription confirmed

                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break

                # After init signal, give a short window for trailing data
                if got_init and idle_deadline and time.time() > idle_deadline:
                    break

    return matches, markets


def parse_matches(
    raw_matches: dict[int, dict],
    raw_markets: dict[int, list[dict]],
    today_str: str,
) -> list[dict[str, Any]]:
    """
    Parse raw WebSocket data into normalized match dicts.
    """
    now = datetime.now()
    results: list[dict[str, Any]] = []

    for match_id, match_data in raw_matches.items():
        # Skip live matches
        if match_data.get("isLive"):
            continue
        # Skip suspended matches
        if match_data.get("isSuspended"):
            continue

        # Parse kickoff
        start_ts = match_data.get("startTs")
        if not start_ts:
            continue
        kickoff_dt = datetime.fromtimestamp(start_ts / 1000)
        
        # Only today's matches
        if kickoff_dt.strftime("%Y-%m-%d") != today_str:
            continue
        # Only future matches
        if kickoff_dt <= now:
            continue

        # Team names
        competitors = match_data.get("competitors", [])
        if len(competitors) < 2:
            continue
        home_team = competitors[0]
        away_team = competitors[1]

        # Skip virtual / simulated matches
        combined = f"{home_team} {away_team}".lower()
        virtual_kws = ['srl', 'simulated', 'esport', 'e-soccer', 'esoccer',
                        'cyber', 'virtual', 'efootball', 'e-football']
        if any(kw in combined for kw in virtual_kws):
            continue

        # Tournament
        tournament_id = match_data.get("tournamentId", "")
        tournament_name = match_data.get("tournamentName", "")
        category_name = match_data.get("categoryName", "")
        if category_name and tournament_name:
            tournament = f"{category_name}. {tournament_name}"
        elif tournament_name:
            tournament = tournament_name
        else:
            tournament = str(tournament_id)

        # Parse markets for this match
        match_markets = raw_markets.get(match_id, [])
        
        odds_1x2 = {}
        odds_ou: dict[str, dict[str, Any]] = {}
        odds_gg = None

        for mkt in match_markets:
            if mkt.get("isSuspended"):
                continue
            mtype = mkt.get("marketTypeId")
            selections = mkt.get("selections", [])
            special = mkt.get("special", "")

            if mtype in (MARKET_TYPE_1X2_LIVE, MARKET_TYPE_1X2_PRE):
                # 1X2: selections have outcome "1" (home), "X" (draw), "2" (away)
                parsed = {}
                for sel in selections:
                    outcome = str(sel.get("outcome", "")).strip()
                    odds = sel.get("odds", 0)
                    if odds and odds > 1.0:
                        if outcome == "1":
                            parsed["home"] = odds
                        elif outcome in ("X", "x"):
                            parsed["draw"] = odds
                        elif outcome == "2":
                            parsed["away"] = odds
                if {"home", "draw", "away"} <= parsed.keys():
                    odds_1x2 = parsed

            elif mtype == MARKET_TYPE_OU:
                # Over/Under: special field contains the line e.g. "2.5"
                line = special.strip()
                if not line:
                    # Try extracting from selections description
                    for sel in selections:
                        desc = str(sel.get("description", ""))
                        if "over" in desc.lower() or "under" in desc.lower():
                            import re
                            m = re.search(r'(\d+\.?\d*)', desc)
                            if m:
                                line = m.group(1)
                                break
                if not line:
                    continue
                    
                try:
                    line_f = float(line)
                    line_str = str(line_f)
                except ValueError:
                    continue

                # Only standard .5 lines for arb scanning
                if line_str not in OU_LINES:
                    continue

                row: dict[str, Any] = {}
                for sel in selections:
                    outcome = str(sel.get("outcome", "")).strip()
                    odds = sel.get("odds", 0)
                    if odds and odds > 1.0:
                        if outcome == "1":  # Over
                            row["over"] = odds
                        elif outcome == "2":  # Under
                            row["under"] = odds
                if {"over", "under"} <= row.keys():
                    odds_ou[line_str] = row

            elif mtype == MARKET_TYPE_GG:
                # GG/NG: outcome "1" = Yes, "2" = No
                parsed_gg: dict[str, Any] = {}
                for sel in selections:
                    outcome = str(sel.get("outcome", "")).strip()
                    odds = sel.get("odds", 0)
                    if odds and odds > 1.0:
                        if outcome == "1":
                            parsed_gg["yes"] = odds
                        elif outcome == "2":
                            parsed_gg["no"] = odds
                if {"yes", "no"} <= parsed_gg.keys():
                    odds_gg = parsed_gg

        # Must have at least 1X2 odds
        if not odds_1x2:
            continue

        results.append({
            "home_team": home_team,
            "away_team": away_team,
            "kickoff": kickoff_dt.strftime("%Y-%m-%d %H:%M"),
            "tournament": tournament,
            "is_live": False,
            "status": "Not start",
            "source": SOURCE,
            "odds_1x2": odds_1x2,
            "odds_ou": odds_ou,
            "odds_gg": odds_gg,
        })

    results.sort(key=lambda x: (x["kickoff"], x["tournament"], x["home_team"]))
    return results


def format_txt(matches: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for match in matches:
        lines = [
            f"{match['home_team']} vs {match['away_team']}",
            match["tournament"],
            match["kickoff"],
        ]
        one_x_two = match["odds_1x2"]
        lines.append(f"1X2: {one_x_two['home']} | {one_x_two['draw']} | {one_x_two['away']}")
        for line in OU_LINES:
            ou = match.get("odds_ou", {}).get(line)
            if ou:
                lines.append(f"O/U {line}: Over {ou['over']} | Under {ou['under']}")
        gg = match.get("odds_gg")
        if gg:
            lines.append(f"GG/NG: Yes {gg['yes']} | No {gg['no']}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def write_outputs(matches: list[dict[str, Any]], output_dir: Path) -> tuple[Path, Path]:
    os.makedirs(output_dir, exist_ok=True)
    json_path = output_dir / "soccabet_odds.json"
    txt_path = output_dir / "soccabet_matches.txt"
    with open(str(json_path), "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(matches, indent=2, ensure_ascii=False))
        f.write("\n")
    with open(str(txt_path), "w", encoding="utf-8", newline="\n") as f:
        f.write(format_txt(matches))
    return json_path, txt_path


def save_outputs(matches: list[dict[str, Any]], preferred_dir: Path) -> tuple[Path, Path]:
    try:
        return write_outputs(matches, preferred_dir)
    except OSError:
        fallback = Path(tempfile.gettempdir()) / "soccabet-output"
        print(f"[WARN] Could not write to {preferred_dir}")
        print(f"[WARN] Saved to fallback folder instead: {fallback}")
        return write_outputs(matches, fallback)


def print_summary(matches: list[dict], json_path: Path, txt_path: Path,
                   elapsed: float, n_raw_matches: int, n_raw_markets: int) -> None:
    print()
    print(f"[INFO] WebSocket received: {n_raw_matches} match objects, {n_raw_markets} market objects")
    print()
    print("SOCCABET GHANA")
    print(f"Total matches fetched: {len(matches)}")
    print(f"With 1X2 odds: {sum(1 for m in matches if m.get('odds_1x2'))}")
    print("=" * 50)
    print()
    print("Sample (first 10 matches):")
    for match in matches[:10]:
        print(f"   {match['home_team']} vs {match['away_team']} | {match['kickoff']} | {match['tournament']}")
    if len(matches) > 10:
        print(f"\n  ... and {len(matches) - 10} more matches")
    print("=" * 50)
    print(f"Saved to {json_path.as_posix()}")
    print(f"Full list saved to {txt_path.as_posix()}")
    print(f"   Open the .txt file to see all {len(matches)} matches!")
    print(f"Scraping completed in {elapsed:.1f}s")


async def _async_scrape() -> list[dict]:
    """Core async scraper — connects to WS, fetches, parses, saves."""
    started = time.perf_counter()
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")

    print(banner(now))
    print(f"  [Soccabet] Connecting to WebSocket feed...")

    raw_matches, raw_markets = await ws_fetch_all(today_str, timeout_secs=12.0)
    n_raw_markets = sum(len(v) for v in raw_markets.values())

    print(f"  [Soccabet] Received {len(raw_matches)} match objects, {n_raw_markets} market updates")
    print(f"  [Soccabet] Parsing and filtering today's prematch football...")

    matches = parse_matches(raw_matches, raw_markets, today_str)

    output_dir = Path(__file__).resolve().parent.parent / "data"
    json_path, txt_path = save_outputs(matches, output_dir)

    elapsed = time.perf_counter() - started
    print_summary(matches, json_path, txt_path, elapsed, len(raw_matches), n_raw_markets)

    return matches


def run() -> list[dict]:
    """Entry point for the intensive/experimental engine — returns match list."""
    return asyncio.run(_async_scrape())


if __name__ == "__main__":
    run()