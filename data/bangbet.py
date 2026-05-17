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
OU_LINES = ("0.5", "1.5", "2.5", "3.5", "4.5", "5.5")
MARKET_GROUPS = {
    "1x2": 0,
    "ou": 3,
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
            data = json.loads(raw, parse_float=str)
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


def odds_value(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def active_market(market: dict[str, Any]) -> bool:
    return bool(market.get("active")) and str(market.get("marketStatus", "")).lower() == "active"


def flatten_markets(event: dict[str, Any]) -> list[dict[str, Any]]:
    markets: list[dict[str, Any]] = []
    for market_group in event.get("marketList") or []:
        markets.extend(market_group.get("markets") or [])
    return markets


def parse_1x2(event: dict[str, Any]) -> dict[str, str] | None:
    for market in flatten_markets(event):
        if str(market.get("name") or "").lower() != "1x2" or not active_market(market):
            continue
        values: dict[str, str] = {}
        for outcome in market.get("outcomes") or []:
            if not outcome.get("active"):
                continue
            odd = odds_value(outcome.get("odds"))
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


def parse_ou(event: dict[str, Any]) -> dict[str, dict[str, str]]:
    parsed: dict[str, dict[str, str]] = {}
    for market in flatten_markets(event):
        if str(market.get("id") or "") != "18" or not active_market(market):
            continue
        spec = str(market.get("specifiers") or "")
        line = spec.replace("total=", "").replace("&", "")
        if line not in OU_LINES:
            continue
        row: dict[str, str] = {}
        for outcome in market.get("outcomes") or []:
            if not outcome.get("active"):
                continue
            desc = str(outcome.get("desc") or "").lower()
            odd = odds_value(outcome.get("odds"))
            if odd is None:
                continue
            if desc.startswith("over"):
                row["over"] = odd
            elif desc.startswith("under"):
                row["under"] = odd
        if {"over", "under"} <= row.keys():
            parsed[line] = row
    return parsed


def parse_gg(event: dict[str, Any]) -> dict[str, str] | None:
    for market in flatten_markets(event):
        if str(market.get("id") or "") != "29" or not active_market(market):
            continue
        values: dict[str, str] = {}
        for outcome in market.get("outcomes") or []:
            if not outcome.get("active"):
                continue
            desc = str(outcome.get("desc") or "").lower()
            odd = odds_value(outcome.get("odds"))
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
        "odds_gg": None,
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
                    records[event_id]["odds_ou"].update(parse_ou(event))
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
            ou = match["odds_ou"].get(line)
            if ou:
                lines.append(f"O/U {line}: Over {ou['over']} | Under {ou['under']}")
        gg = match.get("odds_gg")
        if gg:
            lines.append(f"GG/NG: Yes {gg['yes']} | No {gg['no']}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def write_outputs(matches: list[dict[str, Any]], output_dir: Path) -> tuple[Path, Path]:
    os.makedirs(output_dir, exist_ok=True)
    json_path = output_dir / "bangbet_odds.json"
    txt_path = output_dir / "bangbet_matches.txt"
    with open(str(json_path), "w", encoding="utf-8", newline="\n") as file:
        file.write(json.dumps(matches, indent=2, ensure_ascii=False))
        file.write("\n")
    with open(str(txt_path), "w", encoding="utf-8", newline="\n") as file:
        file.write(format_txt(matches))
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
        label = "1X2" if market == "1x2" else "O/U" if market == "ou" else "GG/NG"
        print(f"  [OK] {label} Page {page}: {count} matches (Total API rows: {total})")
    print()
    print(f"[INFO] Total matches fetched: {result.total_fetched}")
    print()
    print("BANGBET GHANA")
    print(f"Total matches fetched: {len(result.matches)}")
    print(f"With 1X2 odds: {sum(1 for match in result.matches if match.get('odds_1x2'))}")
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
        print(format_txt(result.matches), end="")
    return 0


def run() -> list[dict]:
    """Entry point for the experimental engine — returns match list."""
    import time as _time
    started = _time.perf_counter()
    print(banner(datetime.now()))

    config = ScrapeConfig(
        output_dir=Path(__file__).resolve().parent,
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
