from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, List

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

BASE_URL = "https://www.msport.com"
API_PATH = "/api/gh/facts-center/query/frontend/sports-matches-list"
SOURCE = "msport_gh"
SPORT_ID = "sr:sport:2"


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
        "Referer": "https://www.msport.com/gh/web/sports/list/Basketball",
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


def find_market(event: dict[str, Any], names: List[str]) -> dict[str, Any] | None:
    target_names = {n.lower().strip() for n in names}
    for market in event.get("markets") or []:
        market_name = str(market.get("name") or market.get("description") or "").strip().lower()
        if market_name not in target_names:
            continue
        if market.get("status") != 0:
            continue
        return market
    return None


def parse_winner(event: dict[str, Any]) -> dict[str, float] | None:
    market = find_market(event, ["winner", "winner (incl. overtime)", "winner (incl. ot)", "winner (2-way)"])
    if not market:
        return None

    values: dict[str, float] = {}
    for outcome in market.get("outcomes") or []:
        desc = str(outcome.get("description") or "").strip().lower()
        odds = outcome.get("odds")
        if outcome.get("isActive") != 1 or odds is None or odds == "":
            continue
        try:
            odds_val = float(odds)
        except (ValueError, TypeError):
            continue
        if desc == "home" or desc == "1" or desc == "w1":
            values["home"] = odds_val
        elif desc == "away" or desc == "2" or desc == "w2":
            values["away"] = odds_val

    return values if {"home", "away"} <= values.keys() else None


def parse_overtime(event: dict[str, Any]) -> dict[str, float] | None:
    market = find_market(event, ["will there be overtime", "will there be overtime?", "will there be overtime ?"])
    if not market:
        return None

    values: dict[str, float] = {}
    for outcome in market.get("outcomes") or []:
        desc = str(outcome.get("description") or "").strip().lower()
        odds = outcome.get("odds")
        if outcome.get("isActive") != 1 or odds is None or odds == "":
            continue
        try:
            odds_val = float(odds)
        except (ValueError, TypeError):
            continue
        if desc == "yes":
            values["yes"] = odds_val
        elif desc == "no":
            values["no"] = odds_val

    return values if {"yes", "no"} <= values.keys() else None


def normalize_event(event: dict[str, Any], tz: timezone) -> dict[str, Any] | None:
    odds_2way = parse_winner(event)
    if not odds_2way:
        return None

    odds_overtime = parse_overtime(event) or {}

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
        "status": "prematch",
        "source": SOURCE,
        "odds_2way": odds_2way,
        "odds_overtime": odds_overtime,
    }


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
            "sportId": SPORT_ID,
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


def run() -> list[dict]:
    started = time.perf_counter()
    print("\n" + "* " * 20)
    print("   MSPORT GHANA BASKETBALL SCRAPER")
    print(f"   {datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}")
    print("* " * 20 + "\n")

    # Outputs folder
    output_dir = Path(__file__).resolve().parent.parent / "data"
    os.makedirs(output_dir, exist_ok=True)

    config = ScrapeConfig(output_dir=output_dir, max_pages=10, page_limit=100)
    result = scrape_today(config)

    matches = result.matches
    n = len(matches)

    json_path = output_dir / "msport_basketball_odds.json"
    txt_path = output_dir / "msport_basketball_matches.txt"

    if matches:
        print(f"\n📋 MSPORT GHANA BASKETBALL")
        print(f"🏀 Total matches fetched: {n}")
        print("=" * 50)
        print("\n📝 Sample (first 10):")
        for match in matches[:10]:
            o = match['odds_2way']
            ot = match.get('odds_overtime')
            ot_str = f" | Overtime: Yes {ot['yes']} - No {ot['no']}" if ot else " | Overtime: N/A"
            print(f"   {match['home_team']} vs {match['away_team']} | {match['kickoff']} | H {o['home']} - A {o['away']}{ot_str}")
        if n > 10:
            print(f"  ... and {n - 10} more")
        print("=" * 50)

        with open(str(json_path), "w", encoding="utf-8") as f:
            json.dump(matches, f, indent=2, ensure_ascii=False)

        with open(str(txt_path), "w", encoding="utf-8") as f:
            f.write("MSPORT GHANA BASKETBALL - ALL MATCHES\n")
            f.write(f"Generated: {datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}\n")
            f.write(f"Total: {n} matches\n")
            f.write("=" * 60 + "\n\n")
            for match in matches:
                f.write(f"{match['home_team']} vs {match['away_team']}\n")
                f.write(f"{match['tournament']}\n")
                f.write(f"{match['kickoff']}\n")
                o = match['odds_2way']
                ot = match.get('odds_overtime')
                ot_str = f" | Overtime: Yes {ot['yes']} | No {ot['no']}" if ot else " | Overtime: N/A"
                f.write(f"Winner (2-way): Home {o['home']} | Away {o['away']}{ot_str}\n\n")

        print(f"Saved to {json_path.as_posix()}")
        print(f"Full list saved to {txt_path.as_posix()}")
        print(f"⏱️  Scraping completed in {time.perf_counter() - started:.1f}s")
    else:
        print("\n⚠️ No matches found")
        with open(str(json_path), "w", encoding="utf-8") as f:
            json.dump([], f)

    return matches


if __name__ == "__main__":
    run()
