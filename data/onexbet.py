"""
1xBet Ghana Scraper  —  onexbet.py
====================================
Scrapes today's upcoming football matches from 1xbet.com.gh
Returns match list in the same format as sportybet.py and betway.py
so it plugs directly into the arbitrage engine.

Odds captured:
  • 1X2      (home / draw / away)
  • O/U 2.5  (over / under)
  • GG/NG    (yes / no)

Output:
  data/footballcom_odds.json
  data/footballcom_matches.txt

Requirements:
    pip install requests playwright
    playwright install chromium
"""

import json, os, re, time, datetime, requests

BASE_URL   = "https://1xbet.com.gh"
OUTPUT_DIR = "data"

HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer":         f"{BASE_URL}/en/line/football",
    "Origin":          BASE_URL,
    "Sec-Fetch-Dest":  "empty",
    "Sec-Fetch-Mode":  "cors",
    "Sec-Fetch-Site":  "same-origin",
}

# ── HELPERS ────────────────────────────────────────────────────────────────────
def norm_ts(ts):
    if not ts: return 0
    ts = int(ts)
    if ts > 9_999_999_999: ts //= 1000
    return ts

def ts_to_dt(ts):
    ts = norm_ts(ts)
    if not ts: return None
    try:    return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
    except: return None

def get_cv(item):
    """Return exact odds string from CV field."""
    v = item.get("CV")
    return float(v) if v is not None else 0.0

def extract_events(data):
    """Recursively pull all match-level dicts (those with O1 + O2 fields)."""
    found = []
    def walk(node):
        if isinstance(node, dict):
            if {"O1", "O2"} <= node.keys():
                found.append(node)
                return
            for v in node.values(): walk(v)
        elif isinstance(node, list):
            for item in node: walk(item)
    walk(data)
    return found


# ── FETCH RAW EVENTS VIA PLAYWRIGHT ───────────────────────────────────────────
def get_raw_events():
    from playwright.sync_api import sync_playwright

    best  = {"url": None, "events": [], "cookies": {}}
    done  = [False]

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
        )
        context = browser.new_context(
            user_agent=HEADERS["User-Agent"],
            locale="en-US",
            timezone_id="Africa/Accra",
            viewport={"width": 1366, "height": 768},
        )

        def block_junk(route):
            if route.request.resource_type in ("image", "media", "font"):
                route.abort()
            else:
                route.continue_()
        context.route("**/*", block_junk)

        page = context.new_page()

        def on_response(resp):
            if done[0]: return
            url = resp.url
            if "LineFeed" not in url or "banner" in url or "TopGames" in url:
                return
            try:
                events = extract_events(resp.json())
                if len(events) > len(best["events"]):
                    best["url"]     = url
                    best["events"]  = events
                    best["cookies"] = {c["name"]: c["value"]
                                       for c in context.cookies()}
                    if len(events) >= 10:
                        done[0] = True
            except Exception:
                pass

        page.on("response", on_response)

        try:
            page.goto(f"{BASE_URL}/en/line/football",
                      timeout=60_000, wait_until="domcontentloaded")
            for _ in range(30):
                if done[0]: break
                page.wait_for_timeout(500)
        except Exception:
            pass

        browser.close()

    if not best["events"]:
        return []

    # Re-fetch with count=500 to get all matches
    url = best["url"]
    if "count=" in url:
        url = re.sub(r"count=\d+", "count=500", url)

    session = requests.Session()
    session.cookies.update(best["cookies"])
    try:
        r = session.get(url, headers=HEADERS, timeout=25)
        if r.status_code == 200:
            events = extract_events(r.json())
            if events:
                return events
    except Exception:
        pass

    return best["events"]


# ── FILTER TO TODAY'S UPCOMING MATCHES ONLY ───────────────────────────────────
def filter_today(raw):
    today  = datetime.datetime.now(datetime.timezone.utc).date()
    now_ts = int(time.time())
    kept   = []
    for ev in raw:
        if not ev.get("O1") or not ev.get("O2"): continue
        start_ts = norm_ts(ev.get("S") or 0)
        dt = ts_to_dt(start_ts)
        if not dt: continue
        if start_ts <= now_ts: continue       # already kicked off
        if dt.date() != today: continue       # not today
        kept.append(ev)
    kept.sort(key=lambda e: norm_ts(e.get("S") or 0))
    return kept


# ── PARSE ODDS FROM ONE RAW EVENT ─────────────────────────────────────────────
def parse_odds(ev):
    """
    Confirmed field map from live API diagnostic:
      E array:
        G=1, T=1  → W1 (home win)
        G=1, T=2  → X  (draw)
        G=1, T=3  → W2 (away win)
        G=17, T=9,  P=2.5 → Over 2.5
        G=17, T=10, P=2.5 → Under 2.5
        G=19, T=180 → GG (both teams score)
        G=19, T=181 → NG (no goal)
    """
    w1 = x = w2 = over = under = gg = ng = 0.0

    for item in (ev.get("E") or []):
        g   = item.get("G")
        t   = item.get("T")
        p   = item.get("P")
        val = get_cv(item)

        if g == 1:
            if t == 1 and not w1:  w1  = val
            if t == 2 and not x:   x   = val
            if t == 3 and not w2:  w2  = val

        elif g == 17 and p is not None:
            try:    pf = float(p)
            except: pf = 0.0
            if abs(pf - 2.5) < 0.01:
                if t == 9  and not over:  over  = val
                if t == 10 and not under: under = val

        elif g == 19:
            if t == 180 and not gg: gg = val
            if t == 181 and not ng: ng = val

    return w1, x, w2, over, under, gg, ng


# ── BUILD CLEAN MATCH LIST (same format as sportybet/betway) ──────────────────
def build_matches(events):
    matches = []
    for ev in events:
        start_ts = norm_ts(ev.get("S") or 0)
        dt       = ts_to_dt(start_ts)
        kickoff  = dt.strftime("%Y-%m-%d %H:%M") if dt else ""

        w1, x, w2, over, under, gg, ng = parse_odds(ev)

        match = {
            "home_team":  ev.get("O1", "?"),
            "away_team":  ev.get("O2", "?"),
            "kickoff":    kickoff,
            "tournament": ev.get("LE") or "Unknown",
            "is_live":    False,   # we filter out live matches
            "source":     "1xbet_gh",
            "odds_1x2":   {"home": w1,   "draw": x,     "away": w2}   if w1 and x and w2 else {},
            "odds_ou":    {"line": 2.5,  "over": over,  "under": under} if over and under else {},
            "odds_gg":    {"yes": gg,    "no":   ng}                    if gg and ng else {},
        }
        matches.append(match)
    return matches


# ── SAVE OUTPUT FILES ──────────────────────────────────────────────────────────
def save(matches, t):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    jfile = os.path.join(OUTPUT_DIR, "onexbet_odds.json")
    tfile = os.path.join(OUTPUT_DIR, "onexbet_matches.txt")

    with open(jfile, "w", encoding="utf-8") as f:
        json.dump(matches, f, indent=2, ensure_ascii=False)

    sep  = "=" * 72
    dash = "-" * 72
    lines = [
        sep,
        "  1xBET GHANA — TODAY'S FOOTBALL MATCHES",
        sep,
        f"  Date         : {t.strftime('%A, %d %B %Y')}",
        f"  Scraped at   : {t.strftime('%H:%M')} UTC",
        f"  Total matches: {len(matches)}",
        sep, "",
    ]
    for i, m in enumerate(matches, 1):
        o    = m["odds_1x2"]
        ou   = m["odds_ou"]
        gg   = m["odds_gg"]
        lines += [
            f"{i:>4}.  {m['kickoff']} UTC  |  {m['tournament']}",
            f"        {m['home_team']}  vs  {m['away_team']}",
            f"        1X2      :  W1 {o.get('home','-')}   X {o.get('draw','-')}   W2 {o.get('away','-')}"
            if o else "        1X2      :  -",
            f"        O/U 2.5  :  Over {ou.get('over','-')}   Under {ou.get('under','-')}"
            if ou else "        O/U 2.5  :  -",
            f"        GG/NG    :  GG {gg.get('yes','-')}   NG {gg.get('no','-')}"
            if gg else "        GG/NG    :  -",
            dash,
        ]
    lines += ["", sep, f"  End of list — {len(matches)} matches", sep]

    with open(tfile, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return jfile, tfile


# ── PUBLIC run() — called by main orchestrator ────────────────────────────────
def run():
    t = datetime.datetime.now(datetime.timezone.utc)
    print(f"1xBet Ghana Scraper  |  {t.strftime('%d %b %Y  %H:%M UTC')}")
    print("Fetching matches ...")

    raw = get_raw_events()
    if not raw:
        print("⚠️  1xBet: Could not retrieve match data")
        return []

    today_raw = filter_today(raw)
    if not today_raw:
        print(f"⚠️  1xBet: No matches found for today")
        return []

    matches = build_matches(today_raw)
    jfile, tfile = save(matches, t)

    print(f"✅ 1xBet: {len(matches)} matches fetched")
    print(f"💾 Saved to {jfile}")
    print(f"📄 Full list saved to {tfile}")

    return matches


# ── STANDALONE ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    run()