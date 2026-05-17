"""
Soccabet Ghana scraper.
API: POST https://www.soccabet.com/api/endpoint
     Command-based protocol — fetch all today's football matches.
"""
import asyncio
import sys
import json
import re
import time as _time
from datetime import datetime
import aiohttp

sys.stdout.reconfigure(encoding='utf-8')

SOURCE   = 'soccabet_gh'
API_URL  = 'https://www.soccabet.com/api/endpoint'
HOME_URL = 'https://www.soccabet.com'

VIRTUAL_KEYWORDS = [
    'srl', 'simulated', 'esport', 'e-soccer', 'esoccer',
    'cyber', 'virtual', 'efootball', 'e-football',
]


def is_virtual(home, away, tournament):
    text = f'{home} {away} {tournament}'.lower()
    return any(kw in text for kw in VIRTUAL_KEYWORDS)


async def post_command(session, command, params, headers):
    payload = {'command': command, 'params': params}
    try:
        async with session.post(
            API_URL,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            if resp.status == 200:
                return await resp.json(content_type=None)
    except Exception as e:
        print(f'  [Soccabet] command={command} error: {e}')
    return None


async def get_sport_id(session, headers):
    """Get the football sport ID from Soccabet."""
    data = await post_command(session, 'get_sports', {}, headers)
    if not data:
        return 1  # default
    sports = (data.get('data') or {}).get('sport') or \
             data.get('sport') or []
    for sport in sports:
        name = sport.get('name', '').lower()
        if 'soccer' in name or 'football' in name:
            return sport.get('id', 1)
    return 1


async def get_competitions(session, sport_id, headers):
    """Get today's competition IDs for football."""
    data = await post_command(session, 'get_competitions', {
        'sport_id': sport_id,
        'show_bets': True,
    }, headers)
    if not data:
        return []
    comps = (data.get('data') or {}).get('competition') or \
            data.get('competition') or []
    return comps


async def get_matches_for_competition(session, comp_id, headers):
    """Get all matches for a competition."""
    data = await post_command(session, 'get_events', {
        'competition_id': comp_id,
        'show_bets': True,
        'type': 'prematch',
    }, headers)
    if not data:
        return []
    return (data.get('data') or {}).get('event') or \
           data.get('event') or []


def parse_match(event, tournament_name, now):
    home   = event.get('team1_name') or event.get('t1') or event.get('home_team', '')
    away   = event.get('team2_name') or event.get('t2') or event.get('away_team', '')
    start  = event.get('start_ts') or event.get('st') or event.get('start_time', 0)

    if not home or not away:
        return None
    if is_virtual(home, away, tournament_name):
        return None

    try:
        if isinstance(start, (int, float)):
            dt = datetime.fromtimestamp(int(start))
        else:
            dt = datetime.fromisoformat(str(start).replace('Z', ''))
    except Exception:
        return None

    if dt.date() != now.date():
        return None

    match = {
        'home_team':  home,
        'away_team':  away,
        'kickoff':    dt.strftime('%Y-%m-%d %H:%M'),
        'tournament': tournament_name,
        'is_live':    bool(event.get('is_live', False)),
        'source':     SOURCE,
        'odds_1x2':   {},
        'odds_ou':    {},
        'odds_gg':    {},
    }

    # Parse markets/bets — Soccabet nests bets under various keys
    bets  = event.get('market') or event.get('markets') or \
            event.get('bet') or []

    for mkt in bets:
        mkt_name = (mkt.get('name') or mkt.get('market_name') or '').lower()
        outcomes  = mkt.get('event') or mkt.get('outcomes') or \
                    mkt.get('selection') or []

        # 1X2
        if mkt_name in ('1x2', 'match result', 'win/draw/win',
                         'result', 'full time result') or \
                mkt.get('id') in (1, '1'):
            if len(outcomes) >= 3:
                h = float(outcomes[0].get('price') or outcomes[0].get('odds') or 0)
                d = float(outcomes[1].get('price') or outcomes[1].get('odds') or 0)
                a = float(outcomes[2].get('price') or outcomes[2].get('odds') or 0)
                if h > 1.01 and d > 1.01 and a > 1.01:
                    match['odds_1x2'] = {'home': h, 'draw': d, 'away': a}

        # Over/Under
        if 'total' in mkt_name or 'over' in mkt_name or \
                'under' in mkt_name or mkt.get('id') in (2, '2'):
            for o in outcomes:
                name = (o.get('name') or o.get('desc') or '').lower()
                odds = float(o.get('price') or o.get('odds') or 0)
                m = re.search(r'(\d+\.5)', name)
                if m and odds > 1.01:
                    line = m.group(1)
                    if line not in match['odds_ou']:
                        match['odds_ou'][line] = {}
                    if 'over' in name:
                        match['odds_ou'][line]['over'] = odds
                    elif 'under' in name:
                        match['odds_ou'][line]['under'] = odds

        # GG/NG
        if 'both teams' in mkt_name or 'gg' in mkt_name or \
                'btts' in mkt_name or mkt.get('id') in (29, '29'):
            for o in outcomes:
                name = (o.get('name') or '').lower()
                odds = float(o.get('price') or o.get('odds') or 0)
                if 'yes' in name or name == 'gg':
                    match['odds_gg']['yes'] = odds
                elif 'no' in name or name == 'ng':
                    match['odds_gg']['no'] = odds

    # Clean incomplete O/U lines
    match['odds_ou'] = {
        k: v for k, v in match['odds_ou'].items()
        if v.get('over', 0) > 1.01 and v.get('under', 0) > 1.01
    }
    if not (match['odds_gg'].get('yes', 0) > 1.01 and
            match['odds_gg'].get('no', 0) > 1.01):
        match['odds_gg'] = {}

    return match


async def scrape():
    print('\n' + '* ' * 20)
    print('   SOCCABET GHANA SCRAPER')
    print(f'   {datetime.now().strftime("%A, %d %B %Y %H:%M:%S")}')
    print('* ' * 20 + '\n')

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                      'AppleWebKit/537.36 (KHTML, like Gecko) '
                      'Chrome/120.0.0.0 Safari/537.36',
        'Content-Type': 'application/json',
        'Accept':       'application/json',
        'Referer':      HOME_URL,
        'Cache-Control': 'no-cache',
        'Pragma':       'no-cache',
    }

    now         = datetime.now()
    all_matches = []

    async with aiohttp.ClientSession() as session:
        # 1. Get sport ID
        sport_id = await get_sport_id(session, headers)
        print(f'  [Soccabet] Football sport_id: {sport_id}')

        # 2. Try direct matches endpoint first (faster)
        data = await post_command(session, 'get_events', {
            'sport_id':   sport_id,
            'type':       'prematch',
            'show_bets':  True,
            'time_filter': 'today',
        }, headers)

        events = []
        if data:
            events = (data.get('data') or {}).get('event') or \
                     data.get('event') or []

        if events:
            print(f'  [Soccabet] Direct events: {len(events)}')
            for event in events:
                try:
                    tournament = event.get('competition_name') or \
                                 event.get('league') or ''
                    m = parse_match(event, tournament, now)
                    if m:
                        all_matches.append(m)
                except Exception:
                    continue
        else:
            # 3. Fallback: iterate competitions
            print('  [Soccabet] Fetching via competitions...')
            comps = await get_competitions(session, sport_id, headers)
            print(f'  [Soccabet] Competitions: {len(comps)}')
            for comp in comps[:50]:   # limit to avoid overloading
                comp_id   = comp.get('id')
                comp_name = comp.get('name', '')
                if not comp_id:
                    continue
                events = await get_matches_for_competition(
                    session, comp_id, headers)
                for event in events:
                    try:
                        m = parse_match(event, comp_name, now)
                        if m:
                            all_matches.append(m)
                    except Exception:
                        continue
                await asyncio.sleep(0.2)

    all_matches.sort(key=lambda x: x['kickoff'])
    print(f'\n[Soccabet] Total today: {len(all_matches)}')
    return all_matches


def run():
    start   = _time.time()
    matches = asyncio.run(scrape())

    if matches:
        with open('data/soccabet_odds.json', 'w') as f:
            json.dump(matches, f, indent=2)

        with open('data/soccabet_matches.txt', 'w', encoding='utf-8') as f:
            f.write('SOCCABET GHANA - ALL MATCHES\n')
            f.write(f'Generated: {datetime.now().strftime("%A, %d %B %Y %H:%M:%S")}\n')
            f.write(f'Total: {len(matches)} matches\n')
            f.write('=' * 60 + '\n\n')
            for m in matches:
                f.write(f"{m['home_team']} vs {m['away_team']}\n")
                f.write(f"{m['tournament']}\n")
                f.write(f"{m['kickoff']}\n")
                if m['odds_1x2']:
                    o = m['odds_1x2']
                    f.write(f"1X2: {o['home']} | {o['draw']} | {o['away']}\n")
                if m['odds_ou']:
                    for line, ou in m['odds_ou'].items():
                        f.write(f"O/U {line}: Over {ou['over']} | Under {ou['under']}\n")
                if m['odds_gg']:
                    gg = m['odds_gg']
                    f.write(f"GG/NG: Yes {gg['yes']} | No {gg['no']}\n")
                f.write('\n')

        print(f'Saved to data/soccabet_odds.json')
        print(f'⏱️  Done in {_time.time() - start:.1f}s')
    else:
        print('⚠️ No matches found')

    return matches


if __name__ == '__main__':
    run()
