"""
Scrape today's not-started Bangbet Ghana football odds.

Outputs:
  data/bangbet_odds.json
  data/bangbet_matches.txt
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BASE_URL = "https://bet-api.bangbet.com/api/bet/match/list"
SOURCE = "bangbet_gh"
SPORT_ID_SOCCER = "sr:sport:1"
MARKET_GROUPS = {
    "1x2": 0,
    "ou": 3,
    "dc": 4,
    "gg": 5,
}


@dataclass(frozen=True)
class ScrapeConfig:
    output_dir: Path
    country_tz: timezone = timezone.utc
    max_pages: int = 8
    page_size: int = 100
    request_timeout: int = 12
    retries: int = 3


@dataclass(frozen=True)
class ScrapeResult:
    matches: list[dict[str, Any]]
    total_fetched: int
    page_logs: list[tuple[str, int, int, int]]


def banner(now: datetime) -> str:
    line = "* " * 20
    return (
        f"{line}\n"
        "   BANGBET GHANA SCRAPER\n"
        f"   {now.strftime('%A, %d %B %Y %H:%M:%S')}\n"
        f"{line}\n"
    )


def request_body(group_index: int, page: int, page_size: int) -> dict[str, Any]:
    return {
        "sportId": SPORT_ID_SOCCER,
        "groupIndex": group_index,
        "tournamentId": "",
        "producer": 3,
        "position": 17,
        "beginTime": "",
        "highLight": False,
        "endTime": "",
        "showMarket": True,
        "timeZone": "0",
        "page": page,
        "sortType": 1,
        "pageSize": page_size,
        "country": "gh",
        "marketChildrenIndex": 0,
        "dataGroup": True,
    }


def request_group(group_index: int, page: int, config: ScrapeConfig) -> dict[str, Any]:
    payload = json.dumps(request_body(group_index, page, config.page_size)).encode("utf-8")
    request = urllib.request.Request(
        BASE_URL,
        data=payload,
        method="POST",
        headers={
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": "https://www.bangbet.com",
            "Referer": "https://www.bangbet.com/gh-m/",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            ),
        },
    )

    for attempt in range(1, config.retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=config.request_timeout) as response:
                raw = response.read().decode("utf-8")
            data = json.loads(raw)
            if data.get("result") != 1:
                raise RuntimeError(f"Bangbet API error: {data.get('info') or data}")
            return data.get("data") or {}
        except (TimeoutError, urllib.error.URLError, OSError):
            if attempt == config.retries:
                raise
            time.sleep(0.5 * attempt)

    return {}


def page_events(data: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for group in data.get("data") or []:
        events.extend(group.get("matchVoList") or [])
    return events


def event_dt(event: dict[str, Any], tz: timezone) -> datetime:
    return datetime.fromtimestamp(int(event["scheduledTime"]) / 1000, tz=tz)


def safe_float(value: Any) -> float | None:
    """Convert a value to float, returning None if not possible."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def active_market(market: dict[str, Any]) -> bool:
    return bool(market.get("active")) and str(market.get("marketStatus", "")).lower() == "active"


def flatten_markets(event: dict[str, Any]) -> list[dict[str, Any]]:
    markets: list[dict[str, Any]] = []
    for market_group in event.get("marketList") or []:
        markets.extend(market_group.get("markets") or [])
    return markets


def parse_1x2(event: dict[str, Any]) -> dict[str, float] | None:
    for market in flatten_markets(event):
        if str(market.get("name") or "").lower() != "1x2" or not active_market(market):
            continue
        values: dict[str, float] = {}
        for outcome in market.get("outcomes") or []:
            if not outcome.get("active"):
                continue
            odd = safe_float(outcome.get("odds"))
            if odd is None:
                continue
            outcome_id = str(outcome.get("id") or "")
            if outcome_id == "1":
                values["home"] = odd
            elif outcome_id == "2":
                values["draw"] = odd
            elif outcome_id == "3":
                values["away"] = odd
        return values if {"home", "draw", "away"} <= values.keys() else None
    return None


def parse_ou(event: dict[str, Any]) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, float]]]:
    """Parse all Over/Under lines, partitioning .5 lines into odds_ou and others into odds_asian_ou."""
    odds_ou: dict[str, dict[str, float]] = {}
    odds_asian_ou: dict[str, dict[str, float]] = {}

    for market in flatten_markets(event):
        if str(market.get("id") or "") != "18" or not active_market(market):
            continue
        spec = str(market.get("specifiers") or "")
        raw_line = spec.replace("total=", "").replace("&", "")
        try:
            line_val = float(raw_line)
            line_str = str(line_val)
        except (ValueError, TypeError):
            continue

        row: dict[str, float] = {}
        for outcome in market.get("outcomes") or []:
            if not outcome.get("active"):
                continue
            desc = str(outcome.get("desc") or "").lower()
            odd = safe_float(outcome.get("odds"))
            if odd is None:
                continue
            if desc.startswith("over"):
                row["over"] = odd
            elif desc.startswith("under"):
                row["under"] = odd

        if {"over", "under"} <= row.keys():
            if line_val % 1.0 == 0.5:
                odds_ou[line_str] = row
            else:
                odds_asian_ou[line_str] = row

    return odds_ou, odds_asian_ou


def parse_dc(event: dict[str, Any]) -> dict[str, float]:
    """Parse Double Chance market (market id=10). Returns {'1x': ..., '12': ..., 'x2': ...}."""
    for market in flatten_markets(event):
        if str(market.get("id") or "") != "10" or not active_market(market):
            continue
        values: dict[str, float] = {}
        for outcome in market.get("outcomes") or []:
            if not outcome.get("active"):
                continue
            odd = safe_float(outcome.get("odds"))
            if odd is None:
                continue
            outcome_id = str(outcome.get("id") or "")
            # Bangbet DC outcome IDs: 9 = home or draw (1X), 10 = home or away (12), 11 = draw or away (X2)
            if outcome_id == "9":
                values["1x"] = odd
            elif outcome_id == "10":
                values["12"] = odd
            elif outcome_id == "11":
                values["x2"] = odd
        if {"1x", "12", "x2"} <= values.keys():
            return values
    return {}


def parse_gg(event: dict[str, Any]) -> dict[str, float] | None:
    for market in flatten_markets(event):
        if str(market.get("id") or "") != "29" or not active_market(market):
            continue
        values: dict[str, float] = {}
        for outcome in market.get("outcomes") or []:
            if not outcome.get("active"):
                continue
            desc = str(outcome.get("desc") or "").lower()
            odd = safe_float(outcome.get("odds"))
            if odd is None:
                continue
            if desc == "yes":
                values["yes"] = odd
            elif desc == "no":
                values["no"] = odd
        return values if {"yes", "no"} <= values.keys() else None
    return None


def normalize_tournament(name: str) -> str:
    if " - " in name:
        country, tournament = name.split(" - ", 1)
        return f"{country}. {tournament}"
    return name


def base_match(event: dict[str, Any], tz: timezone) -> dict[str, Any]:
    return {
        "home_team": event.get("homeTeamName") or "",
        "away_team": event.get("awayTeamName") or "",
        "kickoff": event_dt(event, tz).strftime("%Y-%m-%d %H:%M"),
        "tournament": normalize_tournament(event.get("tournamentName") or ""),
        "is_live": False,
        "status": "Not start",
        "source": SOURCE,
        "odds_1x2": {},
        "odds_ou": {},
        "odds_asian_ou": {},
        "odds_dc": {},
        "odds_gg": None,
        "odds_1x2_one_up": {},
        "odds_1x2_two_up": {},
        "odds_fh_1x2": {},
        "odds_sh_1x2": {},
        "odds_fh_ou": {},
        "odds_sh_ou": {},
        "odds_fh_dc": {},
        "odds_sh_dc": {},
        "odds_corners_1x2": {},
        "odds_bookings_1x2": {},
        "odds_bookings_ou": {},
        "odds_gg_2plus": {},
    }


def scrape_today(config: ScrapeConfig) -> ScrapeResult:
    now = datetime.now(config.country_tz)
    today = now.date()
    records: dict[str, dict[str, Any]] = {}
    total_fetched = 0
    page_logs: list[tuple[str, int, int, int]] = []

    for market_name, group_index in MARKET_GROUPS.items():
        for page in range(1, config.max_pages + 1):
            data = request_group(group_index, page, config)
            events = page_events(data)
            if not events:
                break

            total_fetched += len(events)
            page_logs.append((market_name, page, len(events), total_fetched))
            stop_after_page = False

            for event in events:
                kickoff = event_dt(event, config.country_tz)
                if kickoff.date() > today:
                    stop_after_page = True
                    continue
                if kickoff.date() < today:
                    continue
                if event.get("matchStatus") != "not_started" or not event.get("active"):
                    continue

                event_id = str(event.get("id") or "")
                if event_id not in records:
                    records[event_id] = base_match(event, config.country_tz)

                if market_name == "1x2":
                    odds = parse_1x2(event)
                    if odds:
                        records[event_id]["odds_1x2"] = odds
                elif market_name == "ou":
                    ou, asian_ou = parse_ou(event)
                    records[event_id]["odds_ou"].update(ou)
                    records[event_id]["odds_asian_ou"].update(asian_ou)
                elif market_name == "dc":
                    dc = parse_dc(event)
                    if dc:
                        records[event_id]["odds_dc"] = dc
                elif market_name == "gg":
                    records[event_id]["odds_gg"] = parse_gg(event)

            if stop_after_page or len(events) < config.page_size:
                break

    matches = [
        match
        for match in records.values()
        if match.get("odds_1x2")
    ]
    matches.sort(key=lambda item: (item["kickoff"], item["tournament"], item["home_team"], item["away_team"]))
    return ScrapeResult(matches=matches, total_fetched=total_fetched, page_logs=page_logs)


# ── BOX-DRAWING FORMAT HELPERS ────────────────────────────────────────────────

def fmt_row(label, val):
    prefix = f"│ {label:<16} "
    val_width = 80 - len(prefix) - 2
    return f"{prefix}{val:<{val_width}} │"

def fmt_box_top(title):
    prefix = f"┌── {title} "
    dash_count = 80 - len(prefix) - 1
    return prefix + "─" * dash_count + "┐"

def fmt_box_bottom():
    return "└" + "─" * 78 + "┘"

def fmt_box_subheading(sub_title):
    content = f"[{sub_title}]"
    return f"│ {content:<76} │"

def fmt_box_divider():
    line = "─" * 76
    return f"│ {line} │"

def fmt_3way(o):
    if not o or o.get("home") is None or o.get("draw") is None or o.get("away") is None:
        return "N/A"
    return f"Home: {o['home']:<7} │ Draw: {o['draw']:<7} │ Away: {o['away']}"

def fmt_dc(o):
    if not o or o.get("1x") is None or o.get("12") is None or o.get("x2") is None:
        return "N/A"
    return f"1X: {o['1x']:<8} │ 12: {o['12']:<8} │ X2: {o['x2']}"

def fmt_gg(o):
    if not o or o.get("yes") is None or o.get("no") is None:
        return "N/A"
    return f"GG (Yes): {o['yes']:<6} │ NG (No): {o['no']}"

def fmt_ou_section(ou_dict):
    if not ou_dict:
        return fmt_row("", "(No Over/Under lines available)")
    try:
        sorted_keys = sorted(ou_dict.keys(), key=lambda x: float(x))
    except Exception:
        return fmt_row("", "(No Over/Under lines available)")
    rows = []
    for line in sorted_keys:
        try:
            if float(line) % 1.0 != 0.5:
                continue
        except ValueError:
            continue
        ou = ou_dict[line]
        over = ou.get("over")
        under = ou.get("under")
        if over is not None and under is not None:
            line_label = f"Line {line}"
            line_val = f"Over: {over:<8} │ Under: {under:<8}"
            rows.append(fmt_row(line_label, line_val))
    if not rows:
        return fmt_row("", "(No Over/Under lines available)")
    return "\n".join(rows)

def fmt_asian_ou_section(ou_dict):
    if not ou_dict:
        return fmt_row("", "(No Asian Over/Under lines available)")
    try:
        sorted_keys = sorted(ou_dict.keys(), key=lambda x: float(x))
    except Exception:
        return fmt_row("", "(No Asian Over/Under lines available)")
    rows = []
    for line in sorted_keys:
        ou = ou_dict[line]
        over = ou.get("over")
        under = ou.get("under")
        if over is not None and under is not None:
            line_label = f"Line {line}"
            line_val = f"Over: {over:<8} │ Under: {under:<8}"
            rows.append(fmt_row(line_label, line_val))
    if not rows:
        return fmt_row("", "(No Asian Over/Under lines available)")
    return "\n".join(rows)


def format_match_text_block(m):
    # Header
    title = f"⚽ {m['home_team']} vs {m['away_team']}"
    if m.get("is_live"):
        title += " (🔴 LIVE)"
    meta = f"🏆 {m['tournament']} │ 🕐 {m['kickoff']}"

    # Border width
    w = 80

    # Formatting markets
    m_1x2 = fmt_3way(m.get("odds_1x2"))
    m_dc = fmt_dc(m.get("odds_dc"))
    m_gg = fmt_gg(m.get("odds_gg"))
    m_2up = fmt_3way(m.get("odds_1x2_two_up"))
    m_1up = fmt_3way(m.get("odds_1x2_one_up"))

    # Construct the block
    lines = []
    lines.append("═" * w)
    lines.append(f"{title}")
    lines.append(f"{meta}")
    lines.append("═" * w)

    # Main Markets
    lines.append(fmt_box_top("MAIN MARKETS"))
    lines.append(fmt_row("1X2 (Result)", m_1x2))
    lines.append(fmt_row("Double Chance", m_dc))
    lines.append(fmt_row("GG/NG", m_gg))
    lines.append(fmt_row("1X2 Two Up", m_2up))
    lines.append(fmt_row("1X2 One Up", m_1up))
    lines.append(fmt_box_bottom())

    # Over/Under Lines
    lines.append(fmt_box_top("OVER/UNDER LINES"))
    lines.append(fmt_ou_section(m.get("odds_ou")))
    lines.append(fmt_box_bottom())

    # Asian Over/Under Lines
    lines.append(fmt_box_top("ASIAN OVER/UNDER LINES"))
    lines.append(fmt_asian_ou_section(m.get("odds_asian_ou")))
    lines.append(fmt_box_bottom())

    lines.append("")  # Blank line after match block

    return "\n".join(lines)


# ── OUTPUT WRITERS ─────────────────────────────────────────────────────────────

def write_outputs(matches: list[dict[str, Any]], output_dir: Path) -> tuple[Path, Path]:
    os.makedirs(output_dir, exist_ok=True)
    json_path = output_dir / "bangbet_odds.json"
    txt_path = output_dir / "bangbet_matches.txt"

    now = datetime.now()

    with open(str(json_path), "w", encoding="utf-8", newline="\n") as file:
        file.write(json.dumps(matches, indent=2, ensure_ascii=False))
        file.write("\n")

    with open(str(txt_path), "w", encoding="utf-8", newline="\n") as file:
        file.write("BANGBET GHANA - ALL MATCHES\n")
        file.write(f"Generated: {now.strftime('%A, %d %B %Y %H:%M:%S')}\n")
        file.write(f"Total: {len(matches)} matches\n")
        file.write("=" * 60 + "\n\n")
        if not matches:
            file.write("No prematch football for today.\n")
        else:
            for m in matches:
                file.write(format_match_text_block(m))

    return json_path, txt_path


def save_outputs(matches: list[dict[str, Any]], preferred_output_dir: Path) -> tuple[Path, Path]:
    try:
        return write_outputs(matches, preferred_output_dir)
    except OSError:
        fallback_dir = Path(tempfile.gettempdir()) / "bangbet-output"
        print(f"[WARN] Could not write to {preferred_output_dir}")
        print(f"[WARN] Saved to fallback folder instead: {fallback_dir}")
        return write_outputs(matches, fallback_dir)


def print_summary(result: ScrapeResult, json_path: Path, txt_path: Path, elapsed: float) -> None:
    print()
    print("[INFO] Fetching today's matches...")
    for market, page, count, total in result.page_logs:
        label = {"1x2": "1X2", "ou": "O/U", "dc": "DC", "gg": "GG/NG"}.get(market, market)
        print(f"  [OK] {label} Page {page}: {count} matches (Total API rows: {total})")
    print()
    print(f"[INFO] Total matches fetched: {result.total_fetched}")
    print()
    print("BANGBET GHANA")
    print(f"Total matches fetched: {len(result.matches)}")
    print(f"With 1X2 odds: {sum(1 for match in result.matches if match.get('odds_1x2'))}")
    print(f"With DC odds:  {sum(1 for match in result.matches if match.get('odds_dc'))}")
    print("=" * 50)
    print()
    print("Sample (first 10 matches):")
    for match in result.matches[:10]:
        print(f"   {match['home_team']} vs {match['away_team']} | {match['kickoff']} | {match['tournament']}")
    if len(result.matches) > 10:
        print(f"\n  ... and {len(result.matches) - 10} more matches")
    print("=" * 50)
    print(f"Saved to {json_path.as_posix()}")
    print(f"Full list saved to {txt_path.as_posix()}")
    print(f"   Open the .txt file to see all {len(result.matches)} matches!")
    print(f"Scraping completed in {elapsed:.1f}s")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape today's upcoming Bangbet Ghana soccer odds.")
    parser.add_argument("--output-dir", default="data", help="Directory for bangbet_odds.json and bangbet_matches.txt")
    parser.add_argument("--max-pages", type=int, default=8, help="Safety limit for paginated API requests per market")
    parser.add_argument("--page-size", type=int, default=100, help="Matches requested per API page")
    parser.add_argument("--print-all", action="store_true", help="Print the full TXT output in the terminal after saving")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    started = time.perf_counter()
    print(banner(datetime.now()))

    project_root = Path(__file__).resolve().parents[1]
    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = project_root / output_dir

    config = ScrapeConfig(output_dir=output_dir, max_pages=args.max_pages, page_size=args.page_size)
    try:
        result = scrape_today(config)
        json_path, txt_path = save_outputs(result.matches, config.output_dir)
    except urllib.error.URLError as exc:
        raise SystemExit(f"Network error while scraping Bangbet. Check your internet, then run again.\nDetails: {exc}") from exc

    elapsed = time.perf_counter() - started
    print_summary(result, json_path, txt_path, elapsed)
    if args.print_all:
        print()
        for m in result.matches:
            print(format_match_text_block(m), end="")
    return 0


def run() -> list[dict]:
    output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
    os.makedirs(output_dir, exist_ok=True)
    """Entry point for the experimental engine — returns match list."""
    import time as _time
    started = _time.perf_counter()
    print(banner(datetime.now()))

    config = ScrapeConfig(
        output_dir=Path(output_dir),
        max_pages=8,
        page_size=100,
    )
    result = scrape_today(config)
    json_path, txt_path = save_outputs(result.matches, config.output_dir)

    elapsed = _time.perf_counter() - started
    print_summary(result, json_path, txt_path, elapsed)
    return result.matches


if __name__ == "__main__":
    raise SystemExit(main())
