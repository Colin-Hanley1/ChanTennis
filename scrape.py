"""Fetch Tennis Abstract splits and write players.csv.

Usage:
    python3 scrape.py                                # default roster
    python3 scrape.py --players "Jannik Sinner, Carlos Alcaraz"
    python3 scrape.py --players-file roster.txt     # one name per line
    python3 scrape.py --out players.csv
    python3 scrape.py --short-name                  # store surnames in CSV

Produces rows of (player, surface, period, spw, rpw) for every combination of
surface in {hard, clay, grass, all} and period in {52week, career}. The 'all'
rows are match-weighted averages of the three surface rows.

Data source: Tennis Abstract (Jeff Sackmann). Each player's splits live at
https://www.tennisabstract.com/jsfrags/<Name>.js as a template-literal HTML
fragment rendered into the player page.
"""

import argparse
import csv
import re
import sys
import time
import unicodedata
import urllib.request

SECTIONS = [("last52-splits-h", "52week"), ("career-splits-h", "career")]
SURFACES = ["Hard", "Clay", "Grass"]
BASE = "https://www.tennisabstract.com/jsfrags/{}.js"
ELO_URLS = {
    "atp": "https://www.tennisabstract.com/reports/atp_elo_ratings.html",
    "wta": "https://www.tennisabstract.com/reports/wta_elo_ratings.html",
}
DEFAULT_OUT = {"atp": "players.csv", "wta": "players_wta.csv"}
UA = "Mozilla/5.0 (compatible; TennisMarkov/1.0)"

ELO_LINK_RE = re.compile(
    r'href="[^"]*player\.cgi\?p=([A-Za-z]+)"[^>]*>([^<]+)</a>'
)

TH_RE = re.compile(r"<th[^>]*>(.*?)</th>", re.DOTALL)
TR_RE = re.compile(r"<tr>(.*?)</tr>", re.DOTALL)
TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")
PCT_RE = re.compile(r"([\d.]+)%")


def url_name(full_name):
    """'Félix Auger-Aliassime' -> 'FelixAugerAliassime', 'Alex de Minaur' -> 'AlexDeMinaur'."""
    nfkd = unicodedata.normalize("NFKD", full_name)
    ascii_name = "".join(c for c in nfkd if not unicodedata.combining(c))
    parts = re.split(r"[\s\-']+", ascii_name)
    return "".join(p[:1].upper() + p[1:] for p in parts if p)


def fetch_js(slug):
    url = BASE.format(slug)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", errors="replace")


def fetch_elo_roster(tour, top_n=None):
    """Return [(display_name, url_slug)] from the ATP or WTA Elo ratings page."""
    req = urllib.request.Request(ELO_URLS[tour], headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        html = r.read().decode("utf-8", errors="replace")
    roster = []
    seen = set()
    for m in ELO_LINK_RE.finditer(html):
        slug, display = m.group(1), m.group(2).replace("\xa0", " ").replace("&nbsp;", " ").strip()
        if slug in seen:
            continue
        seen.add(slug)
        roster.append((display, slug))
        if top_n and len(roster) >= top_n:
            break
    return roster


def strip_tags(s):
    return TAG_RE.sub("", s).strip()


def pct(s):
    m = PCT_RE.search(s or "")
    return float(m.group(1)) / 100.0 if m else None


def parse_splits(js, section_id):
    """Parse the HTML table under the given section anchor into {split: {col: val}}."""
    start = js.find(f'id="{section_id}"')
    if start < 0:
        return {}
    end = js.find("</table>", start)
    tbl = js[start:end]
    headers = [strip_tags(h) for h in TH_RE.findall(tbl)]
    out = {}
    for row in TR_RE.findall(tbl):
        cells = [strip_tags(c) for c in TD_RE.findall(row)]
        if len(cells) == len(headers):
            rec = dict(zip(headers, cells))
            if rec.get("Split"):
                out[rec["Split"]] = rec
    return out


def extract(js):
    """Yield (surface, period, spw, rpw, matches) rows for one player."""
    for section_id, period in SECTIONS:
        table = parse_splits(js, section_id)
        by_surface = {}
        for surf in SURFACES:
            r = table.get(surf)
            if not r:
                continue
            spw = pct(r.get("SPW"))
            rpw = pct(r.get("RPW"))
            try:
                matches = int(r.get("M", "0"))
            except ValueError:
                matches = 0
            if spw is None or rpw is None:
                continue
            by_surface[surf.lower()] = (spw, rpw, matches)
            yield surf.lower(), period, spw, rpw, matches
        if by_surface:
            total = sum(m for _, _, m in by_surface.values()) or 1
            agg_spw = sum(s * m for s, _, m in by_surface.values()) / total
            agg_rpw = sum(r * m for _, r, m in by_surface.values()) / total
            yield "all", period, agg_spw, agg_rpw, total


def run_scrape(tour, roster, args):
    rows, fails = [], []
    for i, (display, slug) in enumerate(roster):
        if i:
            time.sleep(args.delay)
        resolved_slug = slug or url_name(display)
        label = display.split()[-1] if args.short_name else display
        sys.stderr.write(f"  [{i+1:>3}/{len(roster)}] {display:<30} ")
        sys.stderr.flush()
        try:
            js = fetch_js(resolved_slug)
        except Exception as e:
            sys.stderr.write(f"FETCH FAILED ({e})\n")
            fails.append(display)
            continue
        count = 0
        for surface, period, spw, rpw, matches in extract(js):
            rows.append({
                "player": label, "surface": surface, "period": period,
                "spw": f"{spw:.4f}", "rpw": f"{rpw:.4f}", "matches": matches,
            })
            count += 1
        if count:
            sys.stderr.write(f"{count} rows\n")
        else:
            sys.stderr.write("NO STATS (likely challenger-only, skipped)\n")
            fails.append(display)

    out = args.out if args.out else DEFAULT_OUT[tour]
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["player", "surface", "period", "spw", "rpw", "matches"])
        w.writeheader()
        w.writerows(rows)
    n_players = len({r["player"] for r in rows})
    sys.stderr.write(f"\nwrote {len(rows)} rows for {n_players} players to {out}\n")
    if fails:
        sys.stderr.write(f"{len(fails)} players skipped (no tour-level splits available)\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tour", choices=["atp", "wta", "both"], default="atp",
                    help="Which tour to scrape. 'both' runs ATP then WTA sequentially.")
    ap.add_argument("--players", help="Comma-separated full names (overrides --top).")
    ap.add_argument("--players-file", help="Path to file with one full name per line.")
    ap.add_argument("--top", type=int, default=100,
                    help="Scrape the top N from the Elo roster. Default: 100. Use 0 for all ranked players.")
    ap.add_argument("--out", default=None,
                    help="Output path. Default: players.csv (atp) / players_wta.csv (wta).")
    ap.add_argument("--short-name", action="store_true",
                    help="Use surname only in CSV (risks collisions at larger rosters).")
    ap.add_argument("--delay", type=float, default=0.4,
                    help="Seconds between fetches (be polite).")
    args = ap.parse_args()

    tours = ["atp", "wta"] if args.tour == "both" else [args.tour]

    # Explicit roster overrides the Elo roster and is used for all tours (rare for 'both').
    shared_roster = None
    if args.players_file:
        with open(args.players_file) as f:
            names = [l.strip() for l in f if l.strip() and not l.startswith("#")]
        shared_roster = [(n, None) for n in names]
    elif args.players:
        names = [n.strip() for n in args.players.split(",") if n.strip()]
        shared_roster = [(n, None) for n in names]

    original_out = args.out
    for tour in tours:
        sys.stderr.write(f"\n=== {tour.upper()} ===\n")
        if shared_roster:
            roster = shared_roster
        else:
            sys.stderr.write(f"fetching {tour.upper()} Elo roster ... ")
            sys.stderr.flush()
            top = args.top if args.top > 0 else None
            roster = fetch_elo_roster(tour, top)
            sys.stderr.write(f"{len(roster)} players\n")
        # With --tour both, ignore --out so each tour writes to its default file.
        args.out = None if len(tours) > 1 else original_out
        run_scrape(tour, roster, args)


if __name__ == "__main__":
    main()
