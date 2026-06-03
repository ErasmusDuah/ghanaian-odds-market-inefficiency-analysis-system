"""
engine/verifier.py — Frontend market availability verifier.

Uses Playwright headless Chromium to confirm that odds shown in
the intensive engine's results are actually bettable on the
bookmaker's website (not locked/suspended on the frontend).

Currently supports: Sportybet Ghana
Other platforms can be added by implementing their _verify_* function.

Safe-default policy:
  - If the page fails to load, times out, or selector is not found
    → opportunity is KEPT (we never silently drop without confirmation)
  - Only drop when we EXPLICITLY confirm the market is locked

Usage (called from run_intensive.py):
    from engine.verifier import verify_opportunities
    opportunities, n_dropped = verify_opportunities(opportunities, fetched)
"""

import re
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ── CONFIG ─────────────────────────────────────────────────────────────────────
HEADLESS         = True
PAGE_TIMEOUT_MS  = 15_000   # 15s max per page load
WAIT_AFTER_MS    = 2_500    # let dynamic content render

# Sportybet Ghana match page URL patterns (tried in order)
SPORTYBET_URLS = [
    "https://www.sportybet.com/gh/sport/football/highlights/{eid}",
    "https://www.sportybet.com/gh/sport/football/match/{eid}",
    "https://www.sportybet.com/gh/#/sport/football/event/{eid}",
]

# CSS selectors / text patterns that indicate a LOCKED element on Sportybet
LOCKED_CLASSES = re.compile(
    r'\b(locked|disabled|suspended|unavailable|inactive)\b', re.I
)


# ── SPORTYBET VERIFIER ─────────────────────────────────────────────────────────

def _navigate_sportybet(page, event_id, home_team, away_team):
    """
    Search-based navigation:
      1. Open sportybet Ghana homepage
      2. Click the search icon
      3. Type cleaned team name and press Enter
      4. Click first match result to load details page.
    Falls back to direct URL navigation if search fails.
    """
    # Clean standard words for title verification
    import re
    words_home = re.findall(r'[a-zA-Z]{4,}', home_team)
    words_away = re.findall(r'[a-zA-Z]{4,}', away_team)
    
    clean_home_word = words_home[0].lower() if words_home else ""
    clean_away_word = words_away[0].lower() if words_away else ""

    try:
        # 1. Open homepage
        page.goto("https://www.sportybet.com/gh/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        page.wait_for_timeout(3000)
        
        # 2. Click search icon
        search_icon = page.locator(".m-icon-search, span[data-op='home-search-icon']").first
        if search_icon.is_visible():
            search_icon.click()
            page.wait_for_timeout(1000)
            
            # 3. Use away team or home team as search term (prefer cleaner/longer alphanumeric name)
            search_term = clean_away_word if len(clean_away_word) >= len(clean_home_word) else clean_home_word
            if not search_term:
                search_term = "Lokomotiv"
            
            print(f"  🔍 Sportybet Search: searching for '{search_term}'...")
            
            search_input = page.locator("input.m-input-wap, input[placeholder*='Teams/Players']").first
            if search_input.is_visible():
                search_input.fill(search_term)
                page.wait_for_timeout(1000)
                
                # 4. Trigger search
                page.keyboard.press("Enter")
                page.wait_for_timeout(3000)
                
                # 5. Click the matching event by clicking the team name text exactly
                result_cell = page.locator(f"text={search_term}").first
                if not result_cell.is_visible():
                    result_cell = page.locator(".m-sports-names, .m-info-cell, .m-event-sport").first
                
                if result_cell.is_visible():
                    result_cell.click()
                    page.wait_for_timeout(5000)
                    
                    # Verify page body text contains either cleaned team name word to confirm detail page loaded
                    body_text = page.locator("body").inner_text().lower()
                    if (clean_home_word and clean_home_word in body_text) or (clean_away_word and clean_away_word in body_text):
                        print(f"  ✅ Genuinely loaded match page: '{home_team} vs {away_team}'")
                        return True
                    else:
                        print("  ⚠️ Navigation clicked, but team names not found in body text.")
                else:
                    print("  ⚠️ Search returned no clickable match cards.")
    except Exception as e:
        print(f"  ⚠️ Search navigation failed: {e}. Trying fallback URL...")

    # Fallback to direct URL navigation
    print("  🔗 Falling back to direct URL navigation...")
    eid_clean = event_id.replace('sr:match:', '').replace('sr:event:', '').strip()
    for eid in [event_id, eid_clean]:
        if not eid:
            continue
        for url_tmpl in SPORTYBET_URLS:
            url = url_tmpl.format(eid=eid)
            try:
                response = page.goto(
                    url,
                    wait_until='domcontentloaded',
                    timeout=PAGE_TIMEOUT_MS,
                )
                if response and response.status < 400:
                    page.wait_for_timeout(WAIT_AFTER_MS)
                    
                    # Verify page body text contains either cleaned team name word
                    body_text = page.locator("body").inner_text().lower()
                    if (clean_home_word and clean_home_word in body_text) or (clean_away_word and clean_away_word in body_text):
                        return True
            except PWTimeout:
                continue
            except Exception:
                continue
    return False


def _check_ou_locked_sportybet(page, line, over_odds, under_odds):
    """
    Inspect the loaded Sportybet match page to see if O/U for the line is locked.
    Looks for the row container matching the line (e.g. "1.5"),
    and checks if it contains padlock icons or disabled cell classes.
    """
    try:
        # Wait up to 10 seconds for the markets grid to render
        page.wait_for_selector(".m-market, .m-outcome, .af-select, .m-table-cell", timeout=10000)
        page.wait_for_timeout(1500) # extra safety margin
    except Exception:
        pass

    try:
        # Search for the line text exactly (e.g. "1.5" or "4.5")
        line_els = page.query_selector_all(f"text='{line}'")
        if not line_els:
            line_els = page.query_selector_all(f":text-is('{line}')")

        for el in line_els:
            parent = el
            # Walk up to the row/container element
            for _ in range(4):
                if not parent:
                    break
                cls = parent.get_attribute('class') or ''
                if any(x in cls.lower() for x in ['row', 'market', 'table', 'item']) and 'cell' not in cls.lower():
                    # Check for explicit lock icons or disabled/locked elements inside this row
                    lock_icons = parent.query_selector_all(
                        '.m-icon-lock, .m-table-cell--disable, .m-outcome--disabled, svg[class*="lock"]'
                    )
                    if lock_icons:
                        return False  # Confirmed LOCKED!
                    
                    # Found correct row container and it has no locked cells -> active
                    return True
                
                next_p = parent.query_selector('xpath=..')
                if next_p:
                    parent = next_p
                else:
                    break
    except Exception:
        pass

    return True  # Safe Default: Active / Bettable


# ── MAIN VERIFY FUNCTION ───────────────────────────────────────────────────────

def verify_opportunities(opportunities, fetched_matches):
    """
    Verify each opportunity that involves a platform where frontend-locking
    is a known issue (currently: Sportybet O/U markets).

    Args:
        opportunities   : list of opportunity dicts from run_intensive
        fetched_matches : dict mapping display name → list of raw match dicts
                          e.g. {'Sportybet': [...], 'Betway': [...], ...}

    Returns:
        (verified_opportunities, n_dropped)
    """
    # Build event_id lookup: (home_lower[:12], away_lower[:12]) → event_id
    sb_matches   = fetched_matches.get('Sportybet', [])
    event_id_map = {}
    for m in sb_matches:
        eid = m.get('event_id', '')
        if eid:
            h = m.get('home_team', '').lower()[:12]
            a = m.get('away_team', '').lower()[:12]
            event_id_map[(h, a)] = eid

    # Decide which opportunities need Sportybet O/U verification
    def _needs_check(opp):
        is_ou = opp.get('market', '').startswith('Over/Under')
        has_sb = any(b['platform'] == 'Sportybet' for b in opp.get('bets', []))
        return is_ou and has_sb

    to_check  = [o for o in opportunities if _needs_check(o)]
    no_check  = [o for o in opportunities if not _needs_check(o)]

    if not to_check:
        return opportunities, 0

    print(f"\n🔍 Verifying {len(to_check)} Sportybet O/U "
          f"opportunity(s) via headless browser...")

    verified  = []
    n_dropped = 0
    # Cache per (event_id, line) so we don't re-navigate for duplicate categories
    cache = {}   # (event_id, line) → bool (True = bettable)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=HEADLESS)
        ctx     = browser.new_context(
            viewport={'width': 375, 'height': 812},
            is_mobile=True,
            user_agent=(
                'Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) '
                'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1'
            )
        )
        page = ctx.new_page()
        page.set_default_timeout(PAGE_TIMEOUT_MS)

        try:
            for opp in to_check:
                market = opp.get('market', '')
                line   = market.replace('Over/Under', '').strip()   # e.g. "1.5"

                # Resolve event_id and teams from match name
                parts     = opp.get('match', ' vs ').split(' vs ', 1)
                h_team    = parts[0]
                a_team    = parts[1] if len(parts) > 1 else ''
                
                h_key     = h_team.lower()[:12]
                a_key     = a_team.lower()[:12]
                event_id  = event_id_map.get((h_key, a_key), '')

                cache_key = (event_id or opp.get('match', ''), line)

                if cache_key in cache:
                    is_ok = cache[cache_key]
                else:
                    # Default: assume bettable
                    is_ok = True

                    # Search navigation first
                    loaded = _navigate_sportybet(page, event_id, h_team, a_team)
                    if loaded:
                        # Extract the Sportybet legs' odds
                        bets       = opp.get('bets', [])
                        over_odds  = next(
                            (b['odds'] for b in bets
                             if b['platform'] == 'Sportybet'
                             and b['outcome'].startswith('Over')),
                            None
                        )
                        under_odds = next(
                            (b['odds'] for b in bets
                             if b['platform'] == 'Sportybet'
                             and b['outcome'].startswith('Under')),
                            None
                        )
                        if not over_odds:
                            over_odds = next(
                                (b['odds'] for b in bets
                                 if b['outcome'].startswith('Over')),
                                None
                            )
                        if not under_odds:
                            under_odds = next(
                                (b['odds'] for b in bets
                                 if b['outcome'].startswith('Under')),
                                None
                            )
                        if over_odds and under_odds:
                            is_ok = _check_ou_locked_sportybet(
                                page, line, over_odds, under_odds
                            )
                    else:
                        print(f"  ⚠️  Could not load match page for "
                              f"{opp['match']} — keeping opportunity")

                    cache[cache_key] = is_ok

                if is_ok:
                    verified.append(opp)
                    status = '✅ Verified'
                    if event_id:
                        print(f"  {status}: {opp['match']} | {market} | "
                              f"{opp.get('category','')}")
                else:
                    n_dropped += 1
                    print(f"  ❌ Dropped (market locked on Sportybet): "
                          f"{opp['match']} | {market} | {opp.get('category','')}")

        finally:
            browser.close()

    total_verified = no_check + verified
    print(f"\n  📊 Verification summary: "
          f"{len(to_check)} checked | "
          f"{len(verified)} passed | "
          f"{n_dropped} dropped")

    return total_verified, n_dropped
