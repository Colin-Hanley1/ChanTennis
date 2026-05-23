"""Fetch the latest Tennis Abstract Elo ratings and write elo_<tour>.csv.

This script does ONLY the Elo write — it's a deliberate split from scrape.py,
which also pulls per-player serve/return splits (slow, 15+ minutes). Elo is a
30-second job and shouldn't be gated on the bigger pipeline; running it on
its own schedule means ratings update daily even when the weekly full
refresh fails or stalls.

Usage:
    python3 scrape_elo.py                # both tours
    python3 scrape_elo.py --tour atp
"""

import argparse
import csv
import os
import sys
import time

# Reuse the proven scraper from scrape.py — single source of truth for the
# parsing logic; we just skip the player-splits portion.
from scrape import fetch_elo_ratings

ROOT = os.path.dirname(os.path.abspath(__file__))


def write_elo(tour):
    elos = fetch_elo_ratings(tour, top_n=None)
    if not elos:
        sys.stderr.write(f"{tour}: parser returned 0 rows — refusing to overwrite\n")
        return False
    path = os.path.join(ROOT, f"elo_{tour}.csv")
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["player", "elo", "hElo", "cElo", "gElo"])
        w.writeheader()
        for e in elos:
            w.writerow({
                "player": e["display"],
                "elo":  f"{e['elo']:.1f}"  if e["elo"]  is not None else "",
                "hElo": f"{e['hElo']:.1f}" if e["hElo"] is not None else "",
                "cElo": f"{e['cElo']:.1f}" if e["cElo"] is not None else "",
                "gElo": f"{e['gElo']:.1f}" if e["gElo"] is not None else "",
            })
    sys.stderr.write(f"{tour}: wrote {len(elos)} rows to {path}\n")
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tour", choices=["atp", "wta", "both"], default="both")
    args = ap.parse_args()

    tours = ["atp", "wta"] if args.tour == "both" else [args.tour]
    t0 = time.time()
    failed = []
    for tour in tours:
        try:
            if not write_elo(tour):
                failed.append(tour)
        except Exception as e:
            sys.stderr.write(f"{tour}: FAIL — {type(e).__name__}: {e}\n")
            failed.append(tour)
    sys.stderr.write(f"\nfinished in {time.time() - t0:.1f}s\n")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
