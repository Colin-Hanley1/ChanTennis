"""Backtest the Tennis Markov model on Sackmann's match-level data.

Pipeline:
  1. Download atp_matches_YYYY.csv / wta_matches_YYYY.csv for HISTORY_YEARS
  2. Build historical surface Elo by iterating chronologically (standard K-factor update)
  3. For each TEST_YEAR match, snapshot each player's pre-match state:
        - 52-week trailing SPW/RPW per surface
        - lifetime SPW/RPW per surface
        - surface-specific Elo
  4. For each (recency_weight, elo_weight) in the grid, generate a prediction
     and accumulate log-loss, Brier score, and accuracy across the test set
  5. Report best parameters per metric

Usage:
  python3 backtest.py                          # default: ATP, test 2024, history 2017-2023
  python3 backtest.py --tour wta
  python3 backtest.py --test-year 2023 --history 2016 2017 2018 2019 2020 2021 2022
"""

import argparse
import csv
import math
import os
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta

ATP_BASE = "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master/atp_matches_{}.csv"
WTA_BASE = "https://raw.githubusercontent.com/JeffSackmann/tennis_wta/master/wta_matches_{}.csv"
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest_data")

SURFACES_BC = {"hard", "clay", "grass"}  # Carpet excluded — too rare to model

# Tour-average SPW per surface (current model defaults)
TOUR_AVG = {
    "atp": {"all": 0.638, "hard": 0.640, "clay": 0.620, "grass": 0.665},
    "wta": {"all": 0.562, "hard": 0.564, "clay": 0.548, "grass": 0.590},
}

ELO_K = 32                # standard K-factor for established players
ELO_K_NEW = 60            # higher K for first 30 career matches (provisional)
ELO_INIT = 1500
ELO_PROVISIONAL = 30


# ============================== DATA LOADING ==============================

def fetch_year(tour, year):
    base = ATP_BASE if tour == "atp" else WTA_BASE
    url = base.format(year)
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f"{tour}_matches_{year}.csv")
    if not os.path.exists(path):
        sys.stderr.write(f"  downloading {tour} {year} ... ")
        sys.stderr.flush()
        with urllib.request.urlopen(url, timeout=30) as r:
            with open(path, "wb") as f:
                f.write(r.read())
        sys.stderr.write("done\n")
    return path


def load_matches(tour, years):
    rows = []
    for y in years:
        path = fetch_year(tour, y)
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                rows.append(r)
    sys.stderr.write(f"loaded {len(rows)} {tour} matches across {min(years)}–{max(years)}\n")
    return rows


def parse_date(s):
    # Sackmann's tourney_date is YYYYMMDD as a string
    if not s or len(s) != 8:
        return None
    try:
        return datetime.strptime(s, "%Y%m%d").date()
    except ValueError:
        return None


def normalize(matches):
    """Drop matches with missing serve stats / invalid surface; sort chronologically."""
    out = []
    for m in matches:
        surf = (m.get("surface") or "").strip().lower()
        if surf not in SURFACES_BC:
            continue
        d = parse_date(m.get("tourney_date"))
        if d is None:
            continue
        try:
            wsv = int(m["w_svpt"]); lsv = int(m["l_svpt"])
            w1w = int(m["w_1stWon"]); w2w = int(m["w_2ndWon"])
            l1w = int(m["l_1stWon"]); l2w = int(m["l_2ndWon"])
        except (KeyError, TypeError, ValueError):
            continue
        if wsv <= 0 or lsv <= 0:
            continue
        out.append({
            "date": d,
            "surface": surf,
            "winner": m["winner_name"],
            "loser": m["loser_name"],
            "w_svpt": wsv, "l_svpt": lsv,
            "w_won": w1w + w2w, "l_won": l1w + l2w,
            "best_of": int(m.get("best_of") or 3),
        })
    out.sort(key=lambda x: x["date"])
    return out


# ============================== ELO ==============================

class EloTracker:
    """Tracks overall + per-surface Elo. Standard K-factor update."""

    def __init__(self):
        self.elo_overall = defaultdict(lambda: ELO_INIT)
        self.elo_surface = {s: defaultdict(lambda: ELO_INIT) for s in SURFACES_BC}
        self.matches_played = defaultdict(int)

    def get(self, name, surface):
        """Return (overall, surface) tuple."""
        return self.elo_overall[name], self.elo_surface[surface][name]

    def update(self, winner, loser, surface):
        # Use higher K for new players.
        kw = ELO_K_NEW if self.matches_played[winner] < ELO_PROVISIONAL else ELO_K
        kl = ELO_K_NEW if self.matches_played[loser]  < ELO_PROVISIONAL else ELO_K

        # Overall
        ew, el = self.elo_overall[winner], self.elo_overall[loser]
        exp_w = 1 / (1 + 10 ** ((el - ew) / 400))
        self.elo_overall[winner] = ew + kw * (1 - exp_w)
        self.elo_overall[loser]  = el + kl * (0 - (1 - exp_w))

        # Surface
        ews, els = self.elo_surface[surface][winner], self.elo_surface[surface][loser]
        exp_ws = 1 / (1 + 10 ** ((els - ews) / 400))
        self.elo_surface[surface][winner] = ews + kw * (1 - exp_ws)
        self.elo_surface[surface][loser]  = els + kl * (0 - (1 - exp_ws))

        self.matches_played[winner] += 1
        self.matches_played[loser]  += 1


# ========================== POINT-IN-TIME STATS ==========================

class StatTracker:
    """Accumulate each player's serve-point and return-point counts per surface,
    storing per-match contributions so we can compute trailing windows."""

    def __init__(self):
        # per-player, per-surface: list of (date, svpt, won, rsvpt, rwon)
        # rsvpt / rwon are receive-side counters (opponent's serve points)
        self.records = defaultdict(lambda: defaultdict(list))

    def record(self, m):
        surf = m["surface"]
        # Winner's stats from this match
        self.records[m["winner"]][surf].append({
            "date": m["date"],
            "svpt": m["w_svpt"], "won": m["w_won"],
            "rsvpt": m["l_svpt"], "rwon": m["l_svpt"] - m["l_won"],
        })
        self.records[m["loser"]][surf].append({
            "date": m["date"],
            "svpt": m["l_svpt"], "won": m["l_won"],
            "rsvpt": m["w_svpt"], "rwon": m["w_svpt"] - m["w_won"],
        })

    def aggregate(self, name, surface, before_date, days=None):
        """Return (spw, rpw, matches) over the prior window (or lifetime if days=None)."""
        recs = self.records[name].get(surface, [])
        if not recs:
            # Try aggregating across all surfaces if surface-specific is missing
            return None
        cutoff = before_date - timedelta(days=days) if days else None
        svpt = won = rsvpt = rwon = 0
        n = 0
        for r in recs:
            if r["date"] >= before_date:
                continue
            if cutoff and r["date"] < cutoff:
                continue
            svpt += r["svpt"]; won += r["won"]
            rsvpt += r["rsvpt"]; rwon += r["rwon"]
            n += 1
        if svpt == 0 or rsvpt == 0:
            return None
        return won / svpt, rwon / rsvpt, n

    def aggregate_all_surfaces(self, name, before_date, days=None):
        cutoff = before_date - timedelta(days=days) if days else None
        svpt = won = rsvpt = rwon = 0; n = 0
        for surf in SURFACES_BC:
            for r in self.records[name].get(surf, []):
                if r["date"] >= before_date:
                    continue
                if cutoff and r["date"] < cutoff:
                    continue
                svpt += r["svpt"]; won += r["won"]
                rsvpt += r["rsvpt"]; rwon += r["rwon"]
                n += 1
        if svpt == 0 or rsvpt == 0:
            return None
        return won / svpt, rwon / rsvpt, n


# ============================== MODEL ==============================

def game_win_prob(p):
    if p <= 0: return 0.0
    if p >= 1: return 1.0
    q = 1 - p
    return p**4 * (1 + 4*q + 10*q**2) + 20*p**3*q**3 * p**2 / (p**2 + q**2)


def _other(s):
    return "B" if s == "A" else "A"


def _tb_server(played, first):
    flipped = ((played + 1) // 2) % 2 == 1
    return _other(first) if flipped else first


def tiebreak_win_prob(pa, pb, first):
    qa, qb = 1 - pa, 1 - pb
    alpha = pa*pb + qa*qb
    if alpha >= 1: tail = 0.5
    elif first == "A": tail = pa*qb / (1 - alpha)
    else: tail = 1 - pb*qa / (1 - alpha)
    memo = {}
    def prob(a, b):
        if (a, b) in memo: return memo[(a, b)]
        if a >= 7 and a - b >= 2: v = 1.0
        elif b >= 7 and b - a >= 2: v = 0.0
        elif a == b and a >= 6: v = tail
        else:
            srv = _tb_server(a + b, first)
            pw = pa if srv == "A" else 1 - pb
            v = pw * prob(a+1, b) + (1-pw) * prob(a, b+1)
        memo[(a, b)] = v
        return v
    return prob(0, 0)


def set_win_prob(pa, pb, first):
    pgA = game_win_prob(pa)
    pgAR = 1 - game_win_prob(pb)
    memo = {}
    def prob(a, b):
        if (a, b) in memo: return memo[(a, b)]
        if a == 6 and b <= 4: v = 1.0
        elif b == 6 and a <= 4: v = 0.0
        elif a == 7 and b == 5: v = 1.0
        elif b == 7 and a == 5: v = 0.0
        elif a == 6 and b == 6: v = tiebreak_win_prob(pa, pb, first)
        else:
            srv = first if (a + b) % 2 == 0 else _other(first)
            wg = pgA if srv == "A" else pgAR
            v = wg * prob(a+1, b) + (1-wg) * prob(a, b+1)
        memo[(a, b)] = v
        return v
    return prob(0, 0)


def match_win_prob(pa, pb, best_of):
    target = (best_of + 1) // 2
    spA = set_win_prob(pa, pb, "A")
    spB = set_win_prob(pa, pb, "B")
    memo = {}
    def prob(a, b, idx):
        if (a, b, idx) in memo: return memo[(a, b, idx)]
        if a == target: v = 1.0
        elif b == target: v = 0.0
        else:
            sp = spA if idx % 2 == 0 else spB
            v = sp * prob(a+1, b, idx+1) + (1-sp) * prob(a, b+1, idx+1)
        memo[(a, b, idx)] = v
        return v
    return prob(0, 0, 0)


def elo_match_prob(elo_a, elo_b):
    return 1 / (1 + 10 ** ((elo_b - elo_a) / 400))


# ============================== BACKTEST CORE ==============================

def predict(stats_a, stats_b, tour_avg, elo_a, elo_b, best_of, elo_weight, *, blend_career=None):
    """Combine BC + Elo into a single probability that A beats B.
    stats_a/b: dict with 'spw', 'rpw' already blended across recency.
    """
    if stats_a is None or stats_b is None:
        return None
    pa_serve = stats_a["spw"] + (1 - tour_avg) - stats_b["rpw"]
    pb_serve = stats_b["spw"] + (1 - tour_avg) - stats_a["rpw"]
    p_bc = match_win_prob(pa_serve, pb_serve, best_of)
    p_elo = elo_match_prob(elo_a, elo_b)
    return (1 - elo_weight) * p_bc + elo_weight * p_elo


def blend_stats(s_recent, s_career, recency_weight):
    """Return blended {spw, rpw} given recent (52w) and lifetime tuples (spw, rpw, n).
    Falls back gracefully if one side is missing."""
    if s_recent is None and s_career is None:
        return None
    if s_recent is None:
        return {"spw": s_career[0], "rpw": s_career[1]}
    if s_career is None:
        return {"spw": s_recent[0], "rpw": s_recent[1]}
    w = recency_weight
    return {
        "spw": w * s_recent[0] + (1 - w) * s_career[0],
        "rpw": w * s_recent[1] + (1 - w) * s_career[1],
    }


def run_backtest(tour, test_year, history_years, recency_grid, elo_grid):
    sys.stderr.write(f"\n=== {tour.upper()} backtest, test={test_year}, history={history_years} ===\n")
    all_years = sorted(set(history_years) | {test_year})
    raw = load_matches(tour, all_years)
    matches = normalize(raw)
    sys.stderr.write(f"after cleanup: {len(matches)} matches\n")

    # Walk chronologically: for each test-year match, snapshot then update.
    elo = EloTracker()
    stats = StatTracker()

    # Cache per-test-match snapshots so we can grid-search without recomputing
    snapshots = []
    sys.stderr.write("walking chronologically...\n")
    test_start = datetime(test_year, 1, 1).date()

    for m in matches:
        if m["date"] >= test_start:
            # Snapshot pre-match state for both players, label the truth (winner)
            wname, lname = m["winner"], m["loser"]
            surf = m["surface"]
            tavg = TOUR_AVG[tour][surf]
            best_of = m["best_of"]

            # 52-week stats per surface, plus lifetime per surface
            s_recent_w = stats.aggregate(wname, surf, m["date"], days=365)
            s_career_w = stats.aggregate(wname, surf, m["date"], days=None)
            s_recent_l = stats.aggregate(lname, surf, m["date"], days=365)
            s_career_l = stats.aggregate(lname, surf, m["date"], days=None)

            # Fall back to all-surface aggregate if surface-specific is empty
            if s_recent_w is None: s_recent_w = stats.aggregate_all_surfaces(wname, m["date"], days=365)
            if s_career_w is None: s_career_w = stats.aggregate_all_surfaces(wname, m["date"], days=None)
            if s_recent_l is None: s_recent_l = stats.aggregate_all_surfaces(lname, m["date"], days=365)
            if s_career_l is None: s_career_l = stats.aggregate_all_surfaces(lname, m["date"], days=None)

            # Skip if either side has no data at all
            if (s_recent_w is None and s_career_w is None) or \
               (s_recent_l is None and s_career_l is None):
                # update Elo + stats and move on
                elo.update(wname, lname, surf)
                stats.record(m)
                continue

            # Pre-match Elo
            _, ew_surf = elo.get(wname, surf)
            _, el_surf = elo.get(lname, surf)

            snapshots.append({
                "tour_avg": tavg,
                "best_of": best_of,
                # recent and career for both
                "rw": s_recent_w, "cw": s_career_w,
                "rl": s_recent_l, "cl": s_career_l,
                "ew": ew_surf, "el": el_surf,
                # truth: winner predicted prob is the prob from A=winner perspective
                # we'll use A=winner so the actual outcome is always 1
            })

        # Update state AFTER snapshotting (Elo and stats reflect this match for future snapshots)
        elo.update(m["winner"], m["loser"], m["surface"])
        stats.record(m)

    if not snapshots:
        sys.stderr.write("No usable test matches found.\n")
        return

    sys.stderr.write(f"snapshots ready: {len(snapshots)} test matches\n")

    # ---- Grid search ----
    sys.stderr.write(f"running grid search: {len(recency_grid)}×{len(elo_grid)} = "
                     f"{len(recency_grid) * len(elo_grid)} combos × {len(snapshots)} matches\n")

    results = []
    for rw in recency_grid:
        for ew in elo_grid:
            ll_sum = 0.0  # log loss
            br_sum = 0.0  # brier
            correct = 0
            n = 0
            for s in snapshots:
                # A = winner side
                stats_a = blend_stats(s["rw"], s["cw"], rw)
                stats_b = blend_stats(s["rl"], s["cl"], rw)
                p = predict(stats_a, stats_b, s["tour_avg"], s["ew"], s["el"],
                            s["best_of"], ew)
                if p is None:
                    continue
                # clip to avoid log(0)
                p_clip = max(min(p, 1 - 1e-6), 1e-6)
                ll_sum += -math.log(p_clip)        # winner is "1"
                br_sum += (1 - p) ** 2
                if p > 0.5:
                    correct += 1
                n += 1
            if n == 0:
                continue
            results.append({
                "recency": rw, "elo_w": ew,
                "n": n,
                "log_loss": ll_sum / n,
                "brier": br_sum / n,
                "accuracy": correct / n,
            })
    return results


# ============================== REPORTING ==============================

def print_grid(results, metric, lower_is_better=True):
    rs = sorted({r["recency"] for r in results})
    es = sorted({r["elo_w"] for r in results})
    table = {(r["recency"], r["elo_w"]): r[metric] for r in results}
    pad = 8
    direction = "↓ lower better" if lower_is_better else "↑ higher better"
    print(f"\n  {metric}  ({direction})")
    print("    " + "  Elo→".rjust(pad) + "".join(f"{e:>{pad}.2f}" for e in es))
    print("    " + "  rec↓".rjust(pad) + "─" * (pad * len(es)))
    for rw in rs:
        line = f"    {rw:>{pad-2}.2f} │"
        for ew in es:
            v = table.get((rw, ew))
            line += f"{v:>{pad}.4f}" if v is not None else " " * pad
        print(line)


def report(results, tour, test_year):
    if not results:
        return
    print(f"\n========== {tour.upper()} {test_year} BACKTEST RESULTS ==========")
    n = results[0]["n"]
    print(f"test matches scored: {n:,}\n")

    by_ll = sorted(results, key=lambda r: r["log_loss"])
    print(f"BEST by log loss:   recency={by_ll[0]['recency']:.2f}  elo_w={by_ll[0]['elo_w']:.2f}  "
          f"log_loss={by_ll[0]['log_loss']:.4f}  brier={by_ll[0]['brier']:.4f}  acc={by_ll[0]['accuracy']*100:.2f}%")
    by_br = sorted(results, key=lambda r: r["brier"])
    print(f"BEST by brier:      recency={by_br[0]['recency']:.2f}  elo_w={by_br[0]['elo_w']:.2f}  "
          f"log_loss={by_br[0]['log_loss']:.4f}  brier={by_br[0]['brier']:.4f}  acc={by_br[0]['accuracy']*100:.2f}%")
    by_ac = sorted(results, key=lambda r: -r["accuracy"])
    print(f"BEST by accuracy:   recency={by_ac[0]['recency']:.2f}  elo_w={by_ac[0]['elo_w']:.2f}  "
          f"log_loss={by_ac[0]['log_loss']:.4f}  brier={by_ac[0]['brier']:.4f}  acc={by_ac[0]['accuracy']*100:.2f}%")

    # Reference: pure-Elo and pure-BC corners
    pure_bc = [r for r in results if r["elo_w"] == 0.0 and r["recency"] == 0.7]
    pure_elo = [r for r in results if r["elo_w"] == 1.0]
    if pure_bc:
        r = pure_bc[0]
        print(f"\n  PURE BC (rec=0.70, elo=0):  log_loss={r['log_loss']:.4f}  brier={r['brier']:.4f}  acc={r['accuracy']*100:.2f}%")
    if pure_elo:
        r = min(pure_elo, key=lambda x: x["log_loss"])
        print(f"  PURE ELO (elo=1.00):        log_loss={r['log_loss']:.4f}  brier={r['brier']:.4f}  acc={r['accuracy']*100:.2f}%")

    print_grid(results, "log_loss", True)
    print_grid(results, "brier", True)
    print_grid(results, "accuracy", False)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tour", choices=["atp", "wta", "both"], default="atp")
    ap.add_argument("--test-year", type=int, default=2024)
    ap.add_argument("--history", type=int, nargs="+",
                    default=list(range(2017, 2024)),
                    help="Years to use as history before the test year (Elo + stats warmup).")
    ap.add_argument("--grid", type=float, default=0.1,
                    help="Step size for grid search over recency_w and elo_w. Default 0.1.")
    args = ap.parse_args()

    step = args.grid
    grid = [round(i * step, 4) for i in range(int(round(1.0 / step)) + 1)]

    tours = ["atp", "wta"] if args.tour == "both" else [args.tour]
    for tour in tours:
        results = run_backtest(tour, args.test_year, args.history, grid, grid)
        report(results, tour, args.test_year)


if __name__ == "__main__":
    main()
