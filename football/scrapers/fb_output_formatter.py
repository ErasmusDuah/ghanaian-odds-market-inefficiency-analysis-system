"""
Shared football scraper text formatter.

All football scrapers should call format_match_text_block(match) from here so
market display stays consistent across current and future bookmakers.
"""

BOX_WIDTH = 80


def fmt_row(label, val):
    prefix = f"│ {label:<16} "
    val_width = BOX_WIDTH - len(prefix) - 2
    return f"{prefix}{val:<{val_width}} │"


def fmt_box_top(title):
    prefix = f"┌── {title} "
    dash_count = BOX_WIDTH - len(prefix) - 1
    return prefix + "─" * dash_count + "┐"


def fmt_box_bottom():
    return "└" + "─" * (BOX_WIDTH - 2) + "┘"


def fmt_box_subheading(sub_title):
    content = f"[{sub_title}]"
    return f"│ {content:<76} │"


def fmt_box_divider():
    return f"│ {'─' * 76} │"


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


def _sorted_line_keys(ou_dict):
    try:
        return sorted((ou_dict or {}).keys(), key=lambda x: float(x))
    except Exception:
        return []


def fmt_ou_section(ou_dict):
    rows = []
    for line in _sorted_line_keys(ou_dict):
        try:
            if float(line) % 1.0 != 0.5:
                continue
        except (TypeError, ValueError):
            continue
        ou = ou_dict[line]
        over = ou.get("over")
        under = ou.get("under")
        if over is not None and under is not None:
            rows.append(fmt_row(f"Line {line}", f"Over: {over:<8} │ Under: {under:<8}"))
    return "\n".join(rows) if rows else fmt_row("", "(No Over/Under lines available)")


def fmt_asian_ou_section(ou_dict):
    rows = []
    for line in _sorted_line_keys(ou_dict):
        ou = ou_dict[line]
        over = ou.get("over")
        under = ou.get("under")
        if over is not None and under is not None:
            rows.append(fmt_row(f"Line {line}", f"Over: {over:<8} │ Under: {under:<8}"))
    return "\n".join(rows) if rows else fmt_row("", "(No Asian Over/Under lines available)")


def fmt_ou_section_all(ou_dict, empty_msg="(No Over/Under lines available)"):
    rows = []
    for line in _sorted_line_keys(ou_dict):
        ou = ou_dict[line]
        over = ou.get("over")
        under = ou.get("under")
        if over is not None and under is not None:
            rows.append(fmt_row(f"Line {line}", f"Over: {over:<8} │ Under: {under:<8}"))
    return "\n".join(rows) if rows else fmt_row("", empty_msg)


def format_match_text_block(m):
    title = f"⚽ {m.get('home_team', '')} vs {m.get('away_team', '')}"
    if m.get("is_live"):
        title += " (🔴 LIVE)"
    meta = f"🏆 {m.get('tournament', '')} │ 🕐 {m.get('kickoff', '')}"

    lines = [
        "═" * BOX_WIDTH,
        title,
        meta,
        "═" * BOX_WIDTH,
        fmt_box_top("MAIN MARKETS"),
        fmt_row("1X2 (Result)", fmt_3way(m.get("odds_1x2"))),
        fmt_row("Double Chance", fmt_dc(m.get("odds_dc"))),
        fmt_row("GG/NG", fmt_gg(m.get("odds_gg"))),
        fmt_row("GG/NG 2+", fmt_gg(m.get("odds_gg_2plus"))),
        fmt_row("1X2 Two Up", fmt_3way(m.get("odds_1x2_two_up"))),
        fmt_row("1X2 One Up", fmt_3way(m.get("odds_1x2_one_up"))),
        fmt_box_bottom(),
        fmt_box_top("OVER/UNDER LINES"),
        fmt_ou_section(m.get("odds_ou")),
        fmt_box_bottom(),
        fmt_box_top("ASIAN OVER/UNDER LINES"),
        fmt_asian_ou_section(m.get("odds_asian_ou")),
        fmt_box_bottom(),
        fmt_box_top("HALF TIME MARKETS"),
        fmt_box_subheading("1ST HALF"),
        fmt_row("1X2 (Result)", fmt_3way(m.get("odds_fh_1x2"))),
        fmt_row("Double Chance", fmt_dc(m.get("odds_fh_dc"))),
        fmt_box_subheading("1ST HALF OVER/UNDER"),
        fmt_ou_section_all(m.get("odds_fh_ou")),
        fmt_box_divider(),
        fmt_box_subheading("2ND HALF"),
        fmt_row("1X2 (Result)", fmt_3way(m.get("odds_sh_1x2"))),
        fmt_row("Double Chance", fmt_dc(m.get("odds_sh_dc"))),
        fmt_box_subheading("2ND HALF OVER/UNDER"),
        fmt_ou_section_all(m.get("odds_sh_ou")),
        fmt_box_bottom(),
        fmt_box_top("CORNERS, BOOKINGS & SPECIALS"),
        fmt_row("Corners 1X2", fmt_3way(m.get("odds_corners_1x2"))),
        fmt_row("Bookings 1X2", fmt_3way(m.get("odds_bookings_1x2"))),
        fmt_box_subheading("BOOKINGS OVER/UNDER"),
        fmt_ou_section_all(m.get("odds_bookings_ou"), empty_msg="(No Bookings O/U lines available)"),
        fmt_box_bottom(),
        "",
    ]
    return "\n".join(lines)
