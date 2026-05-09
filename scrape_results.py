"""Scrape live tennis match results from ESPN's tennis scoreboard.

Why this exists: Tennis Abstract draw pages can lag completed matches by 6–24
hours during a busy tournament day. ESPN updates within minutes. We pull live
results here and write them to `live_atp.json` / `live_wta.json` in the same
format the tournaments page already understands (same schema as
manual_*.json), and the page merges them into the bracket automatically.

Pipeline:
  1. Hit ESPN's tennis scoreboard JSON (ATP + WTA come back together)
  2. For each completed (status=Final) main-draw match (skip qualifying)
  3. Look up which TA event slug it belongs to via a small keyword map
  4. Output as { winner, loser, score, round, event } entries
  5. Write live_atp.json and live_wta.json

Usage:
  python3 scrape_results.py
"""

import datetime as dt
import json
import sys
import urllib.request


ESPN_URL = "https://site.api.espn.com/apis/site/v2/sports/tennis/{}/scoreboard"
UA = "Mozilla/5.0 (compatible; TennisMarkov/1.0)"

# ESPN tournament name keyword → TA event slug "city" component.
# Order matters — first match wins, so list more specific phrases first.
TOURNAMENT_KEYWORDS = [
    # ATP/WTA Masters 1000 / WTA 1000
    ("internazionali",         "Rome"),
    ("madrid open",            "Madrid"),
    ("mutua madrid",           "Madrid"),
    ("monte-carlo",            "MonteCarlo"),
    ("monte carlo",            "MonteCarlo"),
    ("indian wells",           "IndianWells"),
    ("bnp paribas",            "IndianWells"),
    ("miami open",             "Miami"),
    ("national bank open",     "Canada"),
    ("rogers cup",             "Canada"),
    ("toronto",                "Canada"),
    ("montreal",               "Canada"),
    ("western & southern",     "Cincinnati"),
    ("cincinnati",             "Cincinnati"),
    ("shanghai",               "Shanghai"),
    ("paris masters",          "Paris"),
    ("rolex paris",            "Paris"),
    ("china open",             "Beijing"),
    # Slams
    ("australian open",        "AustralianOpen"),
    ("french open",            "FrenchOpen"),
    ("roland garros",          "FrenchOpen"),
    ("wimbledon",              "Wimbledon"),
    ("us open",                "USOpen"),
    ("u.s. open",              "USOpen"),
    # ATP 500 / clay specialists
    ("barcelona",              "Barcelona"),
    ("hamburg",                "Hamburg"),
    ("estoril",                "Estoril"),
    ("munich",                 "Munich"),
    ("geneva",                 "Geneva"),
    ("bordeaux",               "Bordeaux"),
    ("marrakech",              "Marrakech"),
    ("bucharest",              "Bucharest"),
    ("bastad",                 "Bastad"),
    ("gstaad",                 "Gstaad"),
    ("kitzbuhel",              "Kitzbuhel"),
    ("generali",               "Kitzbuhel"),
    ("umag",                   "Umag"),
    ("santiago",               "Santiago"),
    ("rio open",               "Rio"),
    ("acapulco",               "Acapulco"),
    ("dubai",                  "Dubai"),
    ("doha",                   "Doha"),
    ("vienna",                 "Vienna"),
    ("tokyo",                  "Tokyo"),
    # Grass swing
    ("stuttgart",              "Stuttgart"),
    ("halle",                  "Halle"),
    ("terra wortmann",         "Halle"),
    ("queen",                  "Queens"),
    ("eastbourne",             "Eastbourne"),
    ("newport",                "Newport"),
    ("mallorca",               "Mallorca"),
    ("birmingham",             "Birmingham"),
    ("bad homburg",            "BadHomburg"),
    # WTA-specific & shared
    ("strasbourg",             "Strasbourg"),
    ("charleston",             "Charleston"),
    ("stuttgart",              "Stuttgart"),
    ("merida",                 "Merida"),
    ("austin",                 "Austin"),
    ("guadalajara",            "Guadalajara"),
    ("hong kong",              "HongKong"),
    ("wuhan",                  "Wuhan"),
    ("ningbo",                 "Ningbo"),
    ("seoul",                  "Seoul"),
    ("portoroz",               "Portoroz"),
    ("warsaw",                 "Warsaw"),
    ("prague",                 "Prague"),
    ("monterrey",              "Monterrey"),
    ("auckland",               "Auckland"),
    ("brisbane",               "Brisbane"),
    ("adelaide",               "Adelaide"),
    ("hobart",                 "Hobart"),
    ("sydney",                 "Sydney"),
    # ATP 250s
    ("los cabos",              "LosCabos"),
    ("winston-salem",          "WinstonSalem"),
    ("atlanta",                "Atlanta"),
    ("washington",             "Washington"),
    ("citi open",              "Washington"),
    ("almaty",                 "Almaty"),
    ("astana",                 "Astana"),
    ("antwerp",                "Antwerp"),
    ("metz",                   "Metz"),
    ("sofia",                  "Sofia"),
    ("stockholm",              "Stockholm"),
    ("rotterdam",              "Rotterdam"),
    ("marseille",              "Marseille"),
    ("rio",                    "Rio"),
    ("buenos aires",           "BuenosAires"),
    ("cordoba",                "Cordoba"),
    ("delray beach",           "DelrayBeach"),
    ("dallas",                 "Dallas"),
    ("rio de janeiro",         "Rio"),
]


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def map_event_slug(espn_name, year, tour, candidates):
    """Match an ESPN tournament name to a TA event slug."""
    if not espn_name or not year or not candidates:
        return None
    nl = espn_name.lower()
    for kw, city in TOURNAMENT_KEYWORDS:
        if kw in nl:
            exact = f"{year}{tour.upper()}{city}"
            if exact in candidates:
                return exact
            # Fuzzy fallback — slug containing the city
            for c in candidates:
                if city.lower() in c.lower():
                    return c
    return None


def fmt_score(w_lines, l_lines):
    """Build '7-5 6-4' from per-set linescore arrays. Truncates extras."""
    parts = []
    for a, b in zip(w_lines, l_lines):
        try:
            parts.append(f"{int(a)}-{int(b)}")
        except (TypeError, ValueError):
            parts.append(f"{a}-{b}")
    return " ".join(parts)


def extract_matches(data, ta_slugs):
    """Returns [(tour, match_dict), ...] across both genders."""
    out = []
    for ev in data.get("events", []):
        name = ev.get("name", "")
        year = (ev.get("season") or {}).get("year")
        slugs_per_tour = {
            "atp": map_event_slug(name, year, "atp", ta_slugs.get("atp", [])),
            "wta": map_event_slug(name, year, "wta", ta_slugs.get("wta", [])),
        }
        for grouping in ev.get("groupings", []):
            g_slug = (grouping.get("grouping") or {}).get("slug")
            if g_slug == "mens-singles":
                tour = "atp"
            elif g_slug == "womens-singles":
                tour = "wta"
            else:
                continue
            for comp in grouping.get("competitions", []):
                status = ((comp.get("status") or {}).get("type") or {}).get("description")
                if status != "Final":
                    continue
                round_name = (comp.get("round") or {}).get("displayName", "")
                if "Qualifying" in round_name:
                    continue
                competitors = comp.get("competitors", [])
                if len(competitors) != 2:
                    continue
                winner = next((c for c in competitors if c.get("winner")), None)
                loser = next((c for c in competitors if c.get("winner") is False), None)
                if not winner or not loser:
                    continue
                w_name = (winner.get("athlete") or {}).get("displayName")
                l_name = (loser.get("athlete") or {}).get("displayName")
                if not w_name or not l_name:
                    continue
                w_lines = [s.get("value") for s in winner.get("linescores", []) if s.get("value") is not None]
                l_lines = [s.get("value") for s in loser.get("linescores", []) if s.get("value") is not None]
                m = {
                    "winner": w_name,
                    "loser": l_name,
                    "score": fmt_score(w_lines, l_lines),
                    "round": round_name or "live",
                }
                slug = slugs_per_tour.get(tour)
                if slug:
                    m["event"] = slug
                out.append((tour, m))
    return out


def main():
    # Load TA slugs so we can map ESPN names → our event identifiers
    ta_slugs = {"atp": [], "wta": []}
    for t in ("atp", "wta"):
        try:
            d = json.load(open(f"draws_{t}.json"))
            ta_slugs[t] = [e["slug"] for e in d.get("events", [])]
        except FileNotFoundError:
            sys.stderr.write(f"  note: draws_{t}.json missing — slugs unknown, entries will be ungrouped\n")
        except Exception as e:
            sys.stderr.write(f"  warn: couldn't load draws_{t}.json: {e}\n")

    sys.stderr.write("fetching ESPN tennis scoreboard ... ")
    sys.stderr.flush()
    try:
        data = fetch_json(ESPN_URL.format("atp"))
    except Exception as e:
        sys.stderr.write(f"FAILED ({e})\n")
        sys.exit(1)
    sys.stderr.write("ok\n")

    pairs = extract_matches(data, ta_slugs)
    by_tour = {"atp": [], "wta": []}
    for tour, m in pairs:
        by_tour[tour].append(m)

    fetched = dt.datetime.now(dt.timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + "Z"
    for tour, matches in by_tour.items():
        path = f"live_{tour}.json"
        # Sort: scoped first, then by round, then by winner — stable diff between runs
        matches.sort(key=lambda m: (not m.get("event"), m.get("event") or "", m.get("round", ""), m["winner"]))
        with open(path, "w") as f:
            json.dump({
                "fetched_at": fetched,
                "source": "espn",
                "matches": matches,
            }, f, indent=2, ensure_ascii=False)
        scoped = sum(1 for m in matches if m.get("event"))
        sys.stderr.write(f"wrote {len(matches):>4} {tour.upper()} matches "
                         f"({scoped} scoped, {len(matches) - scoped} ungrouped) → {path}\n")


if __name__ == "__main__":
    main()
