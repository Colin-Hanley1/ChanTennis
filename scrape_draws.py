"""Scrape currently-running tour event draws from Tennis Abstract.

Pipeline:
  1. Fetch the TA homepage and extract every link to current/<YEAR><TOUR><EVENT>.html
  2. Filter to ATP / WTA tour-level events (skip Challengers, ITFs, Davis Cup)
  3. For each event, fetch its page and parse the inline JS variables
       upcomingSingles / completedSingles / prob128 / prob64 / ... / prob2
  4. Heuristically infer surface and best-of from the event name
  5. Write draws_atp.json / draws_wta.json keyed by event slug

Output schema:
  {
    "fetched_at": "2026-04-24T13:00:00",
    "events": [
      {
        "slug": "2026ATPRome",
        "name": "Rome",
        "year": 2026,
        "tour": "atp",
        "surface": "clay",
        "best_of": 3,
        "url": "https://www.tennisabstract.com/current/2026ATPRome.html",
        "r1_matches": [{"a": "Sebastian Ofner", "b": "Alex Michelsen", "a_seed": null, "b_seed": null, "h2h": "1-1"}, ...],
        "completed": [{"winner": "...", "loser": "...", "round": "R128", "score": "..."}, ...]
      },
      ...
    ]
  }

Usage:  python3 scrape_draws.py
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.request

UA = "Mozilla/5.0 (compatible; TennisMarkov/1.0)"
HOME = "https://www.tennisabstract.com/"

# Skip these — not interesting for our model (challenger / ITF / team comps)
EXCLUDE_KEYWORDS = ("Challenger", "ITF", "DavisCup", "BJKCup", "Olympic", "BillieJeanKingCup", "Doubles")

# Surface lookup by event keyword (case-insensitive substring).
# Default: hard.
SURFACE_BY_KEYWORD = [
    ("FrenchOpen",  "clay"),
    ("RolandGarros","clay"),
    ("Wimbledon",   "grass"),
    ("Halle",       "grass"),
    ("Stuttgart",   "grass"),
    ("Eastbourne",  "grass"),
    ("Queens",      "grass"),
    ("Newport",     "grass"),
    ("Mallorca",    "grass"),
    ("Birmingham",  "grass"),
    ("BadHomburg",  "grass"),
    ("Madrid",      "clay"),
    ("Rome",        "clay"),
    ("MonteCarlo",  "clay"),
    ("Hamburg",     "clay"),
    ("Houston",     "clay"),
    ("Estoril",     "clay"),
    ("Munich",      "clay"),
    ("Barcelona",   "clay"),
    ("Geneva",      "clay"),
    ("Bordeaux",    "clay"),
    ("Marrakech",   "clay"),
    ("Bucharest",   "clay"),
    ("Bastad",      "clay"),
    ("Gstaad",      "clay"),
    ("Kitzbuhel",   "clay"),
    ("Umag",        "clay"),
    ("Strasbourg",  "clay"),
    ("Stuttgartw",  "clay"),  # WTA Stuttgart is clay (separate from ATP grass at same name)
    ("Charleston",  "clay"),
    ("Madrid",      "clay"),
]

# Bo5 is ATP Grand Slams only.
GRAND_SLAMS = ("AustralianOpen", "FrenchOpen", "RolandGarros", "Wimbledon", "USOpen")

EVENT_LINK_RE = re.compile(
    r'href="(https://www\.tennisabstract\.com/current/(\d{4})([A-Za-z][A-Za-z0-9_-]*)\.html)"',
    re.IGNORECASE,
)
# Player anchor only — must be of the form `player.cgi?p=<id>/<name>` with no
# extra query params. The `[^"&]+` excludes H2H links like
# `?p=<id>/<name>&f=ACareerqq&q=...` which display "d." as their text.
PLAYER_LINK_RE = re.compile(
    r'<a[^>]*href="[^"]*player\.cgi\?p=\d+/[^"&]+"[^>]*>([^<]+)</a>'
)
SEED_RE = re.compile(r'(?:^|<br/?>)\(([^)]{1,12})\)\s*<a[^>]*player\.cgi')

# Tokens that are valid seeds (numeric or known entry codes). Country codes are
# 3-letter uppercase ISO codes and should NOT be accepted as a seed.
SEED_TOKENS = {"Q", "LL", "WC", "PR", "SE", "ALT"}

def is_seed(token):
    if not token:
        return False
    t = token.strip()
    if t.isdigit():
        return True
    if t.upper() in SEED_TOKENS:
        return True
    return False


def http_get(url, retries=2):
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:
            if attempt == retries:
                raise
            time.sleep(1)


def discover_events():
    """Return [(year:int, slug:str, url:str)] for every active event link on the home page."""
    html = http_get(HOME)
    seen = set()
    events = []
    for m in EVENT_LINK_RE.finditer(html):
        url, year, rest = m.group(1), int(m.group(2)), m.group(3)
        slug = f"{year}{rest}"
        if slug in seen:
            continue
        seen.add(slug)
        if any(k.lower() in rest.lower() for k in EXCLUDE_KEYWORDS):
            continue
        # Tour assignment: explicit ATP/WTA token, or fall back to ATP heuristic.
        events.append({"year": year, "slug": slug, "url": url, "rest": rest})
    return events


def detect_tour(rest):
    low = rest.lower()
    if "wta" in low or low.endswith("women") or "women" in low:
        return "wta"
    if "atp" in low or low.endswith("men") or low.endswith("mens"):
        return "atp"
    return None  # ambiguous (slams handled separately)


def detect_surface(slug):
    for kw, surf in SURFACE_BY_KEYWORD:
        if kw.lower() in slug.lower():
            return surf
    return "hard"


def detect_best_of(slug, tour):
    if tour == "wta":
        return 3
    return 5 if any(s.lower() in slug.lower() for s in GRAND_SLAMS) else 3


def detect_event_name(slug, year):
    s = slug.replace(str(year), "", 1)
    s = re.sub(r"^(ATP|WTA)", "", s, flags=re.I)
    s = re.sub(r"(Men|Women|Mens)$", "", s, flags=re.I)
    # Insert a space before each capital letter that follows a lowercase one.
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s)
    return s.strip()


def parse_var_string(html, var_name):
    """Find `var <name> = '...'` and return the string content (handles escaped quotes)."""
    m = re.search(rf"var\s+{var_name}\s*=\s*'((?:[^'\\]|\\.)*)'", html, re.DOTALL)
    if not m:
        return None
    return m.group(1).replace("\\'", "'").replace('\\"', '"')


def split_lines(s):
    if s is None:
        return []
    return [chunk for chunk in re.split(r"<br\s*/?>", s) if chunk.strip()]


def parse_match_line(line):
    """Pull out (seedA, playerA, seedB, playerB, h2h) from a 'PA vs PB' line.
    H2H is the second player's record from PA's perspective in the [n-m] tag.
    """
    # Player names from the two anchor tags
    names = PLAYER_LINK_RE.findall(line)
    if len(names) < 2:
        return None
    a, b = names[0].strip(), names[1].strip()
    # Seeds: only accept seed-like tokens (numeric / Q / LL / WC / PR / SE / ALT).
    # Reject country codes like "USA", "ITA", etc.
    seeds = [None, None]
    for j, m in enumerate(re.finditer(r"\(([^)]{1,12})\)\s*<a[^>]*player\.cgi", line)):
        if j < 2 and is_seed(m.group(1)):
            seeds[j] = m.group(1)
    # H2H: third anchor, e.g. [2-1]
    h2h = None
    h2h_m = re.search(r'\[(\d+-\d+)\]', line)
    if h2h_m:
        h2h = h2h_m.group(1)
    return {"a": a, "b": b, "a_seed": seeds[0], "b_seed": seeds[1], "h2h": h2h}


def extract_bracket_slots(html):
    """Parse the main draw table on the page into an ordered list of slots.

    Tennis Abstract lays the draw out as a single table with the field listed
    top-to-bottom in bracket order. Each section (quadrant of the draw) is
    separated by an empty row; each section repeats a 'Player' header row.
    Adjacent slots (slot[0]/slot[1], slot[2]/slot[3], ...) are R1 opponents.
    "Bye" appears as a literal slot entry; pairing a real player against a
    bye means that player auto-advances to R2.

    Returns a list of dicts: {name, seed, country, bye}.
    """
    # The draw table is the first <table> on the page; it has many player anchors.
    table_match = None
    for m in re.finditer(r'<table[^>]*>.*?</table>', html, re.DOTALL):
        if len(re.findall(r'player\.cgi', m.group(0))) >= 16:
            table_match = m
            break
    if not table_match:
        return []
    table = table_match.group(0)
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', table, re.DOTALL)
    slots = []
    for row in rows:
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
        if not cells:
            continue
        # Cell [0] holds the slot's player display (or "Bye", or "Player" header).
        raw = re.sub(r'<[^>]+>', ' ', cells[0])
        text = re.sub(r'\s+', ' ', raw).replace('\xa0', ' ').strip()
        if not text or text == 'Player':
            continue
        if text.lower() == 'bye':
            slots.append({"bye": True})
            continue
        # Parse "(seed) Name (CTRY)" — seed/entry tag is optional.
        seed = None
        m2 = re.match(r'^\(([^)]+)\)\s*(.+)$', text)
        if m2 and is_seed(m2.group(1)):
            seed = m2.group(1)
            text = m2.group(2)
        elif m2:
            # Parenthesised but not a seed token — leave the parens as part of name.
            pass
        country = None
        m3 = re.match(r'^(.+?)\s*\(([A-Z]{2,4})\)\s*$', text)
        if m3:
            text = m3.group(1).strip()
            country = m3.group(2)
        slots.append({"name": text, "seed": seed, "country": country, "bye": False})
    return slots


def parse_completed_line(line):
    """Pull out (round, winner, loser, score)."""
    rm = re.match(r'\s*(R\d+|RR|QF|SF|F|W)\s*:\s*', line)
    if not rm:
        return None
    rest = line[rm.end():]
    names = PLAYER_LINK_RE.findall(rest)
    if len(names) < 2:
        return None
    score_m = re.search(r"\)\s*([\d\-(),\s]+)$", rest.replace("&nbsp;", " "))
    score = score_m.group(1).strip() if score_m else ""
    return {
        "round": rm.group(1),
        "winner": names[0].strip(),
        "loser": names[1].strip(),
        "score": score,
    }


def scrape_event(ev, delay=0.6):
    sys.stderr.write(f"  · {ev['slug']:<32} ")
    sys.stderr.flush()
    try:
        html = http_get(ev["url"])
    except Exception as e:
        sys.stderr.write(f"FAIL ({e})\n")
        return None

    upcoming = parse_var_string(html, "upcomingSingles") or ""
    completed = parse_var_string(html, "completedSingles") or ""
    # Parse upcoming → matches (kept for compatibility but the bracket is the source of truth)
    r1_matches = []
    for line in split_lines(upcoming):
        rec = parse_match_line(line)
        if rec:
            r1_matches.append(rec)
    completed_rows = []
    for line in split_lines(completed):
        rec = parse_completed_line(line)
        if rec:
            completed_rows.append(rec)

    bracket_slots = extract_bracket_slots(html)

    if not r1_matches and not completed_rows and not bracket_slots:
        sys.stderr.write("empty (skipped)\n")
        return None

    sys.stderr.write(f"{len(bracket_slots):>3} slots, {len(r1_matches)} upcoming, {len(completed_rows)} completed\n")
    time.sleep(delay)
    return {
        "bracket_slots": bracket_slots,
        "r1_matches": r1_matches,
        "completed": completed_rows,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--delay", type=float, default=0.6, help="Seconds between event fetches.")
    args = ap.parse_args()

    sys.stderr.write("discovering events on TA homepage...\n")
    raw_events = discover_events()
    sys.stderr.write(f"  found {len(raw_events)} candidate links\n\n")

    # Try to attach a tour to each. If neither token is present in the slug, skip
    # — too ambiguous to model (most undated stuff is country-team / exhibition).
    by_tour = {"atp": [], "wta": []}
    skipped = []
    for ev in raw_events:
        tour = detect_tour(ev["rest"])
        if tour is None:
            skipped.append(ev["slug"])
            continue
        ev["tour"] = tour
        ev["surface"] = detect_surface(ev["slug"])
        ev["best_of"] = detect_best_of(ev["slug"], tour)
        ev["name"] = detect_event_name(ev["slug"], ev["year"])
        by_tour[tour].append(ev)

    if skipped:
        sys.stderr.write(f"skipped {len(skipped)} ambiguous-tour events\n")

    out = {}
    for tour, events in by_tour.items():
        sys.stderr.write(f"\n=== {tour.upper()}: {len(events)} events ===\n")
        results = []
        for ev in events:
            scraped = scrape_event(ev, delay=args.delay)
            if not scraped:
                continue
            results.append({
                "slug": ev["slug"],
                "name": ev["name"],
                "year": ev["year"],
                "tour": ev["tour"],
                "surface": ev["surface"],
                "best_of": ev["best_of"],
                "url": ev["url"],
                **scraped,
            })
        out[tour] = results

    for tour, events in out.items():
        path = f"draws_{tour}.json"
        payload = {
            "fetched_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + "Z",
            "events": events,
        }
        with open(path, "w") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        sys.stderr.write(f"\nwrote {len(events)} events to {path}\n")


if __name__ == "__main__":
    main()
