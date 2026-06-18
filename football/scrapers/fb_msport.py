"""
Scrape today's not-started MSport Ghana football odds.

Outputs:
  data/msport_odds.json
  data/msport_matches.txt
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BASE_URL = "https://www.msport.com"
API_PATH = "/api/gh/facts-center/query/frontend/sports-matches-list"
SOURCE = "msport_gh"
SPORT_ID_SOCCER = "sr:sport:1"
OU_LINES = ("0.5", "1.5", "2.5", "3.5", "4.5", "5.5")


@dataclass(frozen=True)
class ScrapeConfig:
    output_dir: Path
    country_tz: timezone = timezone.utc
    max_pages: int = 10
    page_limit: int = 100
    request_timeout: int = 12
    retries: int = 3


@dataclass(frozen=True)
class ScrapeResult:
    matches: list[dict[str, Any]]
    total_fetched: int
    page_logs: list[tuple[int, int, int]]


def msport_headers() -> dict[str, str]:
    return {
        "Accept": "application/json, text/plain, */*",
        "ApiLevel": "2",
        "clientid": "WEB",
        "operid": "3",
        "platform": "WEB",
        "Referer": "https://www.msport.com/gh/web/sports/list/Soccer",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        ),
    }


def request_matches(query: dict[str, Any], timeout: int, retries: int) -> dict[str, Any]:
    url = BASE_URL + API_PATH + "?" + urllib.parse.urlencode(query)
    request = urllib.request.Request(url, headers=msport_headers(), method="POST")

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read().decode("utf-8")
            break
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            last_error = exc
            if attempt == retries:
                raise
            time.sleep(0.6 * attempt)
    else:
        raise RuntimeError(f"MSport request failed: {last_error}")

    data = json.loads(payload)
    if data.get("bizCode") != 10000:
        raise RuntimeError(f"MSport API error: {data.get('message') or data}")
    return data.get("data") or {}


def iter_page_events(data: dict[str, Any]) -> list[dict[str, Any]]:
    if data.get("events"):
        return list(data["events"])

    events: list[dict[str, Any]] = []
    for tournament in data.get("tournaments") or []:
        events.extend(tournament.get("events") or [])
    return events


def event_dt(event: dict[str, Any], tz: timezone) -> datetime:
    return datetime.fromtimestamp(int(event["startTime"]) / 1000, tz=tz)


def odds_value(outcome: dict[str, Any]) -> str | None:
    odds = outcome.get("odds")
    if odds is None or odds == "":
        return None
    return str(odds)


def find_market(event: dict[str, Any], name: str, specifier: str | None = None) -> dict[str, Any] | None:
    for market in event.get("markets") or []:
        market_name = str(market.get("name") or market.get("description") or "").strip().lower()
        if market_name != name.lower():
            continue
        if specifier is not None and market.get("specifiers") != specifier:
            continue
        if market.get("status") != 0:
            continue
        return market
    return None


def parse_1x2(event: dict[str, Any]) -> dict[str, float] | None:
    market = find_market(event, "1x2")
    if not market:
        return None

    values: dict[str, float] = {}
    for outcome in market.get("outcomes") or []:
        desc = str(outcome.get("description") or "").strip().lower()
        value = odds_value(outcome)
        if outcome.get("isActive") != 1 or value is None:
            continue
        if desc == "home":
            values["home"] = float(value)
        elif desc == "draw":
            values["draw"] = float(value)
        elif desc == "away":
            values["away"] = float(value)

    return values if {"home", "draw", "away"} <= values.keys() else None


def parse_dc(event: dict[str, Any]) -> dict[str, float] | None:
    market = find_market(event, "Double Chance")
    if not market:
        return None

    values: dict[str, float] = {}
    for outcome in market.get("outcomes") or []:
        desc = str(outcome.get("description") or "").strip().lower()
        value = odds_value(outcome)
        if outcome.get("isActive") != 1 or value is None:
            continue
        desc_clean = desc.replace(" ", "")
        if desc_clean in {"1x", "x1"}:
            values["1x"] = float(value)
        elif desc_clean in {"12", "21"}:
            values["12"] = float(value)
        elif desc_clean in {"x2", "2x"}:
            values["x2"] = float(value)

    return values if len(values) == 3 else None


def parse_all_ou(event: dict[str, Any]) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, float]]]:
    odds_ou = {}
    odds_asian_ou = {}

    for market in event.get("markets") or []:
        market_name = str(market.get("name") or market.get("description") or "").strip().lower()
        if market_name != "over/under":
            continue
        if market.get("status") != 0:
            continue
        
        spec = market.get("specifiers") or ""
        import re
        m = re.search(r'total=(\d+(?:\.\d+)?)', spec)
        if not m:
            continue
        
        raw_line = m.group(1)
        try:
            line_val = float(raw_line)
            line_str = str(line_val)
        except ValueError:
            continue
            
        row = {}
        for outcome in market.get("outcomes") or []:
            desc = str(outcome.get("description") or "").strip().lower()
            value = odds_value(outcome)
            if outcome.get("isActive") != 1 or value is None:
                continue
            if desc.startswith("over "):
                row["over"] = float(value)
            elif desc.startswith("under "):
                row["under"] = float(value)
                
        if len(row) == 2:
            if line_val % 1.0 == 0.5:
                odds_ou[line_str] = row
            else:
                odds_asian_ou[line_str] = row
                
    return odds_ou, odds_asian_ou


def parse_gg(event: dict[str, Any]) -> dict[str, float] | None:
    market = find_market(event, "GG/NG")
    if not market:
        return None

    values: dict[str, float] = {}
    for outcome in market.get("outcomes") or []:
        desc = str(outcome.get("description") or "").strip().lower()
        value = odds_value(outcome)
        if outcome.get("isActive") != 1 or value is None:
            continue
        if desc in {"yes", "gg"}:
            values["yes"] = float(value)
        elif desc in {"no", "ng"}:
            values["no"] = float(value)

    return values if {"yes", "no"} <= values.keys() else None


def normalize_event(event: dict[str, Any], tz: timezone) -> dict[str, Any] | None:
    odds_1x2 = parse_1x2(event)
    odds_ou, odds_asian_ou = parse_all_ou(event)
    odds_gg = parse_gg(event)
    odds_dc = parse_dc(event)

    if not odds_1x2:
        return None

    kickoff = event_dt(event, tz).strftime("%Y-%m-%d %H:%M")
    tournament = event.get("tournament") or ""
    category = event.get("category") or ""
    tournament_label = f"{category}. {tournament}" if category and tournament else tournament or category

    return {
        "home_team": event.get("homeTeam") or "",
        "away_team": event.get("awayTeam") or "",
        "kickoff": kickoff,
        "tournament": tournament_label,
        "is_live": False,
        "status": "Not start",
        "source": SOURCE,
        'odds_1x2': odds_1x2,
        'odds_1x2_one_up': {},
        'odds_1x2_two_up': {},
        'odds_fh_1x2': {},
        'odds_sh_1x2': {},
        'odds_fh_ou': {},
        'odds_sh_ou': {},
        'odds_fh_dc': {},
        'odds_sh_dc': {},
        'odds_corners_1x2': {},
        'odds_bookings_1x2': {},
        'odds_bookings_ou': {},
        'odds_ou': odds_ou,
        'odds_asian_ou': odds_asian_ou,
        'odds_gg': odds_gg,
        'odds_gg_2plus': {},
        'odds_dc': odds_dc,
    }


def banner(now: datetime) -> str:
    line = "* " * 20
    return (
        f"{line}\n"
        "   MSPORT GHANA SCRAPER\n"
        f"   {now.strftime('%A, %d %B %Y %H:%M:%S')}\n"
        f"{line}\n"
    )


def scrape_today(config: ScrapeConfig) -> ScrapeResult:
    now = datetime.now(config.country_tz)
    today = now.date()
    matches: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    last_event_id = ""
    total_fetched = 0
    page_logs: list[tuple[int, int, int]] = []

    for page in range(1, config.max_pages + 1):
        query: dict[str, Any] = {
            "sportId": SPORT_ID_SOCCER,
            "sortBy": "TIME_ASC",
            "limit": config.page_limit,
        }
        if last_event_id:
            query["lastEventId"] = last_event_id

        data = request_matches(query, timeout=config.request_timeout, retries=config.retries)
        events = iter_page_events(data)
        if not events:
            break
        total_fetched += len(events)
        page_logs.append((page, len(events), total_fetched))

        stop_after_page = False
        for event in events:
            event_id = str(event.get("eventId") or "")
            if event_id in seen_ids:
                continue
            seen_ids.add(event_id)

            kickoff = event_dt(event, config.country_tz)
            if kickoff.date() > today:
                stop_after_page = True
                continue
            if kickoff.date() < today:
                continue
            if kickoff <= now:
                continue
            if event.get("status") != 0:
                continue

            normalized = normalize_event(event, config.country_tz)
            if normalized:
                matches.append(normalized)

        last_event_id = str(events[-1].get("eventId") or "")
        if stop_after_page or not last_event_id:
            break

    matches.sort(key=lambda item: (item["kickoff"], item["tournament"], item["home_team"], item["away_team"]))
    return ScrapeResult(matches=matches, total_fetched=total_fetched, page_logs=page_logs)


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
    return "\n".join(rows)

def fmt_ou_section_all(ou_dict, empty_msg="(No Over/Under lines available)"):
    """Like fmt_ou_section but shows ALL lines (no .5 filter). Used for half-time markets."""
    if not ou_dict:
        return fmt_row("", empty_msg)
    try:
        sorted_keys = sorted(ou_dict.keys(), key=lambda x: float(x))
    except Exception:
        return fmt_row("", empty_msg)
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
        return fmt_row("", empty_msg)
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
    
    # 1st Half / 2nd Half
    fh_1x2 = fmt_3way(m.get("odds_fh_1x2"))
    fh_dc = fmt_dc(m.get("odds_fh_dc"))
    
    sh_1x2 = fmt_3way(m.get("odds_sh_1x2"))
    sh_dc = fmt_dc(m.get("odds_sh_dc"))
    
    # Specials
    c_1x2 = fmt_3way(m.get("odds_corners_1x2"))
    b_1x2 = fmt_3way(m.get("odds_bookings_1x2"))
    gg_2plus = fmt_gg(m.get("odds_gg_2plus"))

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
    
    # Half Time Markets
    lines.append(fmt_box_top("HALF TIME MARKETS"))
    lines.append(fmt_box_subheading("1ST HALF"))
    lines.append(fmt_row("1X2 (Result)", fh_1x2))
    lines.append(fmt_row("Double Chance", fh_dc))
    lines.append(fmt_box_subheading("1ST HALF OVER/UNDER"))
    lines.append(fmt_ou_section_all(m.get("odds_fh_ou")))
    lines.append(fmt_box_divider())
    lines.append(fmt_box_subheading("2ND HALF"))
    lines.append(fmt_row("1X2 (Result)", sh_1x2))
    lines.append(fmt_row("Double Chance", sh_dc))
    lines.append(fmt_box_subheading("2ND HALF OVER/UNDER"))
    lines.append(fmt_ou_section_all(m.get("odds_sh_ou")))
    lines.append(fmt_box_bottom())
    
    # Specials & Stats
    lines.append(fmt_box_top("CORNERS, BOOKINGS & SPECIALS"))
    lines.append(fmt_row("Corners 1X2", c_1x2))
    lines.append(fmt_row("Bookings 1X2", b_1x2))
    lines.append(fmt_box_subheading("BOOKINGS OVER/UNDER"))
    lines.append(fmt_ou_section_all(m.get("odds_bookings_ou"), empty_msg="(No Bookings O/U lines available)"))
    lines.append(fmt_row("GG/NG 2+", gg_2plus))
    lines.append(fmt_box_bottom())
    lines.append("") # Blank line after match block
    
    return "\n".join(lines)


def format_txt(matches: list[dict[str, Any]]) -> str:
    return "".join(format_match_text_block(m) for m in matches)


def write_outputs(matches: list[dict[str, Any]], output_dir: Path) -> tuple[Path, Path]:
    os.makedirs(output_dir, exist_ok=True)
    json_path = output_dir / "msport_odds.json"
    txt_path = output_dir / "msport_matches.txt"

    with open(str(json_path), "w", encoding="utf-8", newline="\n") as file:
        file.write(json.dumps(matches, indent=2, ensure_ascii=False))
        file.write("\n")

    with open(str(txt_path), "w", encoding="utf-8", newline="\n") as file:
        file.write(f"MSPORT GHANA - ALL MATCHES\n")
        file.write(f"Generated: {datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}\n")
        file.write(f"Total: {len(matches)} matches\n")
        file.write("=" * 60 + "\n\n")
        file.write(format_txt(matches))

    return json_path, txt_path


def save_outputs(matches: list[dict[str, Any]], preferred_output_dir: Path) -> tuple[Path, Path]:
    try:
        return write_outputs(matches, preferred_output_dir)
    except OSError as first_error:
        fallback_dir = Path(tempfile.gettempdir()) / "msport-output"
        try:
            paths = write_outputs(matches, fallback_dir)
        except OSError as second_error:
            raise OSError(
                f"Could not write to {preferred_output_dir} ({first_error}); "
                f"fallback {fallback_dir} also failed ({second_error})"
            ) from second_error
        print(f"[WARN] Could not write to {preferred_output_dir}")
        print(f"[WARN] Saved to fallback folder instead: {fallback_dir}")
        return paths


def print_summary(result: ScrapeResult, json_path: Path, txt_path: Path, elapsed: float) -> None:
    matches = result.matches
    print()
    print("[INFO] Fetching today's matches...")
    for page, count, total in result.page_logs[:1]:
        print(f"  [OK] Page {page}: {count} matches (Total: {total})")

    if len(result.page_logs) > 1:
        print()
        print("[INFO] Fetching more pages...")
        for page, count, total in result.page_logs[1:]:
            print(f"  [OK] Page {page}: {count} matches (Total: {total})")

    print()
    print(f"[INFO] Total matches fetched: {result.total_fetched}")
    print()
    print("MSPORT GHANA")
    print(f"Total matches fetched: {len(matches)}")
    print(f"With 1X2 odds: {sum(1 for match in matches if match.get('odds_1x2'))}")
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape today's upcoming MSport Ghana soccer odds.")
    parser.add_argument("--output-dir", default="data", help="Directory for msport_odds.json and msport_matches.txt")
    parser.add_argument("--max-pages", type=int, default=10, help="Safety limit for paginated API requests")
    parser.add_argument("--page-limit", type=int, default=100, help="Matches requested per API page")
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
    config = ScrapeConfig(output_dir=output_dir, max_pages=args.max_pages, page_limit=args.page_limit)

    try:
        result = scrape_today(config)
        json_path, txt_path = save_outputs(result.matches, config.output_dir)
    except urllib.error.URLError as exc:
        raise SystemExit(f"Network error while scraping MSport. Check your internet, then run again.\nDetails: {exc}") from exc
    except OSError as exc:
        raise SystemExit(
            "Windows could not write the output files, even in the fallback folder.\n"
            f"Details: {exc}"
        ) from exc

    elapsed = time.perf_counter() - started
    print_summary(result, json_path, txt_path, elapsed)
    if args.print_all:
        print()
        print(format_txt(result.matches), end="")
    return 0


def run() -> list:
    output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
    os.makedirs(output_dir, exist_ok=True)
    """
    Programmatic entry point for run_experimental.py.
    Scrapes today's MSport Ghana matches, saves to data/, and returns match list.
    """
    started = time.perf_counter()
    now = datetime.now()
    print(banner(now))

    project_root = Path(__file__).resolve().parents[1]
    output_dir = project_root / "data"
    config = ScrapeConfig(output_dir=output_dir)

    result = scrape_today(config)
    if result.matches:
        json_path, txt_path = save_outputs(result.matches, output_dir)
    else:
        json_path = output_dir / "msport_odds.json"
        txt_path  = output_dir / "msport_matches.txt"

    elapsed = time.perf_counter() - started
    print_summary(result, json_path, txt_path, elapsed)
    return result.matches


if __name__ == "__main__":
    raise SystemExit(main())
