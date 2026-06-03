from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

BASE_URL = "https://bet-api.bangbet.com/api/bet/match/list"
SOURCE = "bangbet_gh"
SPORT_ID = "sr:sport:20"


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
    page_logs: list[tuple[int, int, int]]


def request_body(page: int, page_size: int) -> dict[str, Any]:
    return {
        "sportId": SPORT_ID,
        "groupIndex": 0,
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


def request_group(page: int, config: ScrapeConfig) -> dict[str, Any]:
    payload = json.dumps(request_body(page, config.page_size)).encode("utf-8")
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


def active_market(market: dict[str, Any]) -> bool:
    return bool(market.get("active")) and str(market.get("marketStatus", "")).lower() == "active"


def match_outcome_to_team(outcome_name, home_team, away_team):
    """Safely matches outcome names to home or away teams."""
    def clean(s):
        return "".join(c for c in s.lower() if c.isalnum())
    
    oc = clean(outcome_name)
    hc = clean(home_team)
    ac = clean(away_team)
    
    if not oc:
        return None
        
    if oc == hc:
        return 'home'
    if oc == ac:
        return 'away'
    if hc in oc or oc in hc:
        return 'home'
    if ac in oc or oc in ac:
        return 'away'
        
    h_parts = [p for p in hc.split() if len(p) > 2]
    a_parts = [p for p in ac.split() if len(p) > 2]
    for hp in h_parts:
        if hp in oc:
            return 'home'
    for ap in a_parts:
        if ap in oc:
            return 'away'
    return None


def parse_winner(event: dict[str, Any]) -> dict[str, float] | None:
    # Flatten markets
    markets = []
    for mg in event.get("marketList") or []:
        markets.extend(mg.get("markets") or [])
        
    for market in markets:
        if str(market.get("id") or "") != "186" or not active_market(market):
            continue
            
        values: dict[str, float] = {}
        home_team = event.get("homeTeamName") or ""
        away_team = event.get("awayTeamName") or ""
        
        for outcome in market.get("outcomes") or []:
            if not outcome.get("active"):
                continue
            odd = outcome.get("odds")
            if odd is None or odd == "":
                continue
            desc = outcome.get("desc") or ""
            try:
                odd_val = float(odd)
            except (ValueError, TypeError):
                continue
                
            side = match_outcome_to_team(desc, home_team, away_team)
            if side == 'home':
                values["home"] = odd_val
            elif side == 'away':
                values["away"] = odd_val
                
        if len(values) == 2:
            return values
            
    return None


def normalize_tournament(name: str) -> str:
    if " - " in name:
        country, tournament = name.split(" - ", 1)
        return f"{country}. {tournament}"
    return name


def normalize_event(event: dict[str, Any], tz: timezone) -> dict[str, Any] | None:
    odds_2way = parse_winner(event)
    if not odds_2way:
        return None

    kickoff = event_dt(event, tz).strftime("%Y-%m-%d %H:%M")
    tournament = normalize_tournament(event.get("tournamentName") or "")

    return {
        "home_team": event.get("homeTeamName") or "",
        "away_team": event.get("awayTeamName") or "",
        "kickoff": kickoff,
        "tournament": tournament,
        "is_live": False,
        "status": "prematch",
        "source": SOURCE,
        "odds_2way": odds_2way,
    }


def scrape_today(config: ScrapeConfig) -> ScrapeResult:
    now = datetime.now(config.country_tz)
    today = now.date()
    matches: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    total_fetched = 0
    page_logs: list[tuple[int, int, int]] = []

    for page in range(1, config.max_pages + 1):
        data = request_group(page, config)
        events = page_events(data)
        if not events:
            break

        total_fetched += len(events)
        page_logs.append((page, len(events), total_fetched))
        stop_after_page = False

        for event in events:
            event_id = str(event.get("id") or "")
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
            if event.get("matchStatus") != "not_started" or not event.get("active"):
                continue

            normalized = normalize_event(event, config.country_tz)
            if normalized:
                matches.append(normalized)

        if stop_after_page or len(events) < config.page_size:
            break

    matches.sort(key=lambda item: (item["kickoff"], item["tournament"], item["home_team"], item["away_team"]))
    return ScrapeResult(matches=matches, total_fetched=total_fetched, page_logs=page_logs)


def run() -> list[dict]:
    started = time.perf_counter()
    print("\n" + "* " * 20)
    print("   BANGBET GHANA TABLE TENNIS SCRAPER")
    print(f"   {datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}")
    print("* " * 20 + "\n")

    output_dir = Path(__file__).resolve().parent.parent / "data"
    os.makedirs(output_dir, exist_ok=True)

    config = ScrapeConfig(output_dir=output_dir, max_pages=8, page_size=100)
    result = scrape_today(config)

    matches = result.matches
    n = len(matches)

    json_path = output_dir / "bangbet_tt_odds.json"
    txt_path = output_dir / "bangbet_tt_matches.txt"

    if matches:
        print(f"\n📋 BANGBET GHANA TABLE TENNIS")
        print(f"🏓 Total matches fetched: {n}")
        print("=" * 50)
        print("\n📝 Sample (first 10):")
        for match in matches[:10]:
            o = match['odds_2way']
            print(f"   {match['home_team']} vs {match['away_team']} | {match['kickoff']} | H {o['home']} - A {o['away']}")
        if n > 10:
            print(f"  ... and {n - 10} more")
        print("=" * 50)

        with open(str(json_path), "w", encoding="utf-8") as f:
            json.dump(matches, f, indent=2, ensure_ascii=False)

        with open(str(txt_path), "w", encoding="utf-8") as f:
            f.write("BANGBET GHANA TABLE TENNIS - ALL MATCHES\n")
            f.write(f"Generated: {datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}\n")
            f.write(f"Total: {n} matches\n")
            f.write("=" * 60 + "\n\n")
            for match in matches:
                f.write(f"{match['home_team']} vs {match['away_team']}\n")
                f.write(f"{match['tournament']}\n")
                f.write(f"{match['kickoff']}\n")
                o = match['odds_2way']
                f.write(f"Winner (2-way): Home {o['home']} | Away {o['away']}\n\n")

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
