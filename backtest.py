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
import json
import math
import os
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone

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
    """Tracks overall + per-surface Elo. Standard K-factor update.
    Surface ratings are independent — a player's hard rating is built ONLY
    from their hard matches and ignores everything they did on clay/grass."""

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


class JointSurfaceEloTracker:
    """Joint-surface model: each player has one overall skill θ_p plus
    per-surface offsets δ_{p,s}. The effective surface rating is θ + δ.

    Why this is a foundational change vs. EloTracker:
      - Overall θ updates on EVERY match (any surface), so a player who has
        played 50 hard + 5 grass has 55 updates informing their grass rating
        (via θ), not 5.
      - δ updates only from same-surface matches and uses a smaller K, so
        the surface offset accumulates slowly as a deviation from skill.
      - For thin-surface samples (Mboko clay, returning juniors), the
        prediction is anchored by overall skill instead of starting near
        the 1500 default. This solves the problem at the rating level
        rather than patching it post-hoc with probability shrinkage.

    Defaults tuned to roughly preserve total per-match movement vs. EloTracker
    (K_overall + K_surface ≈ ELO_K=32). Hyperparameters to revisit if we
    fold this into production."""

    def __init__(self, *, k_overall=24, k_surface=8, init=ELO_INIT):
        self.theta = defaultdict(lambda: init)
        self.delta = {s: defaultdict(float) for s in SURFACES_BC}
        self.matches_played = defaultdict(int)
        self.k_overall = k_overall
        self.k_surface = k_surface

    def get(self, name, surface):
        """Return (overall, surface_effective) tuple. The 'overall' is θ;
        the 'surface' is θ + δ_surface."""
        th = self.theta[name]
        return th, th + self.delta[surface][name]

    def update(self, winner, loser, surface):
        rw = self.theta[winner] + self.delta[surface][winner]
        rl = self.theta[loser]  + self.delta[surface][loser]
        exp_w = 1 / (1 + 10 ** ((rl - rw) / 400))
        err = 1 - exp_w  # winner's residual (positive)

        # Provisional bump applies to overall update only — surface offsets
        # should always move slowly to avoid wild swings on thin data.
        kw_o = ELO_K_NEW if self.matches_played[winner] < ELO_PROVISIONAL else self.k_overall
        kl_o = ELO_K_NEW if self.matches_played[loser]  < ELO_PROVISIONAL else self.k_overall

        self.theta[winner] += kw_o * err
        self.theta[loser]  -= kl_o * err
        self.delta[surface][winner] += self.k_surface * err
        self.delta[surface][loser]  -= self.k_surface * err

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


# Mirror of JS surfaceMatchCount: full weight on last-52w matches, partial
# weight on older career matches via (1 - recency). Lets backtests reflect
# how the live model judges sample-size confidence.
def effective_sample(recent, career, recency):
    n52 = recent[2] if recent else 0
    n_c = career[2] if career else 0
    older = max(n_c - n52, 0)
    return n52 + (1 - recency) * older


# Mirror of JS shrinkProbBySample: pull thin matchups toward `prior` (typically
# the same matchup evaluated cross-surface). With n0=15, both players ~60
# matches → weight ≈ 0.80; one player at 6 → weight ≈ 0.29.
def shrink_prob(p, n_a, n_b, n0=15, prior=0.5):
    if p is None:
        return None
    if n0 <= 0:
        return p
    n_min = min(n_a or 0, n_b or 0)
    w = n_min / (n_min + n0)
    target = prior if prior is not None and math.isfinite(prior) else 0.5
    return w * p + (1 - w) * target


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


def build_snapshots(tour, test_year, history_years, *, extra_snapshot_years=()):
    """Walk all history + test matches chronologically. For every match in the
    test year *and* in any extra_snapshot_years (e.g. a calibration-fit year),
    capture the pre-match state we need to score predictions.
    Each snapshot is tagged with `year` so callers can split."""
    snapshot_years = {test_year, *extra_snapshot_years}
    sys.stderr.write(f"\n=== {tour.upper()} backtest, test={test_year}, "
                     f"history={history_years}, snapshot_years={sorted(snapshot_years)} ===\n")
    all_years = sorted(set(history_years) | snapshot_years)
    raw = load_matches(tour, all_years)
    matches = normalize(raw)
    sys.stderr.write(f"after cleanup: {len(matches)} matches\n")

    elo = EloTracker()
    stats = StatTracker()

    snapshots = []
    sys.stderr.write("walking chronologically...\n")

    for m in matches:
        if m["date"].year in snapshot_years:
            wname, lname = m["winner"], m["loser"]
            surf = m["surface"]
            best_of = m["best_of"]

            # Surface-specific stats (preferred primary signal)
            s_recent_w = stats.aggregate(wname, surf, m["date"], days=365)
            s_career_w = stats.aggregate(wname, surf, m["date"], days=None)
            s_recent_l = stats.aggregate(lname, surf, m["date"], days=365)
            s_career_l = stats.aggregate(lname, surf, m["date"], days=None)

            # All-surface stats (used as the shrinkage prior — the same player
            # judged by their broader game when surface data is thin)
            a_recent_w = stats.aggregate_all_surfaces(wname, m["date"], days=365)
            a_career_w = stats.aggregate_all_surfaces(wname, m["date"], days=None)
            a_recent_l = stats.aggregate_all_surfaces(lname, m["date"], days=365)
            a_career_l = stats.aggregate_all_surfaces(lname, m["date"], days=None)

            # Need *some* data on each player.
            if (s_recent_w is None and s_career_w is None and a_recent_w is None and a_career_w is None) or \
               (s_recent_l is None and s_career_l is None and a_recent_l is None and a_career_l is None):
                elo.update(wname, lname, surf)
                stats.record(m)
                continue

            ew_over, ew_surf = elo.get(wname, surf)
            el_over, el_surf = elo.get(lname, surf)

            snapshots.append({
                "year": m["date"].year,
                "surface": surf,
                "tour_avg": TOUR_AVG[tour][surf],
                "tour_avg_all": TOUR_AVG[tour]["all"],
                "best_of": best_of,
                # surface-specific
                "rw": s_recent_w, "cw": s_career_w,
                "rl": s_recent_l, "cl": s_career_l,
                "ew": ew_surf, "el": el_surf,
                # all-surface (prior)
                "raw": a_recent_w, "caw": a_career_w,
                "ral": a_recent_l, "cal": a_career_l,
                "ew_all": ew_over, "el_all": el_over,
            })

        elo.update(m["winner"], m["loser"], m["surface"])
        stats.record(m)

    sys.stderr.write(f"snapshots ready: {len(snapshots)} matches across "
                     f"{sorted({s['year'] for s in snapshots})}\n")
    return snapshots


def predict_snapshot(s, recency, elo_weight, sample_prior):
    """Reproduce the JS model's prediction path for a single snapshot:
    surface BC+Elo blend, then shrink toward the all-surface BC+Elo blend
    using effective sample size."""
    stats_a = blend_stats(s["rw"], s["cw"], recency)
    stats_b = blend_stats(s["rl"], s["cl"], recency)
    p = predict(stats_a, stats_b, s["tour_avg"], s["ew"], s["el"],
                s["best_of"], elo_weight)
    if p is None:
        return None
    if sample_prior <= 0:
        return p
    # Cross-surface prior using all-surface stats + overall Elo.
    stats_a_all = blend_stats(s["raw"], s["caw"], recency)
    stats_b_all = blend_stats(s["ral"], s["cal"], recency)
    prior = predict(stats_a_all, stats_b_all, s["tour_avg_all"],
                    s["ew_all"], s["el_all"], s["best_of"], elo_weight)
    if prior is None:
        prior = 0.5
    n_a = effective_sample(s["rw"], s["cw"], recency)
    n_b = effective_sample(s["rl"], s["cl"], recency)
    return shrink_prob(p, n_a, n_b, sample_prior, prior)


def run_backtest(tour, test_year, history_years, recency_grid, elo_grid, sample_prior=15):
    snapshots = build_snapshots(tour, test_year, history_years)
    if not snapshots:
        sys.stderr.write("No usable test matches found.\n")
        return None

    sys.stderr.write(f"running grid search: {len(recency_grid)}×{len(elo_grid)} = "
                     f"{len(recency_grid) * len(elo_grid)} combos × {len(snapshots)} matches "
                     f"(sample_prior={sample_prior})\n")

    results = []
    for rw in recency_grid:
        for ew in elo_grid:
            ll_sum = 0.0
            br_sum = 0.0
            correct = 0
            n = 0
            for s in snapshots:
                p = predict_snapshot(s, rw, ew, sample_prior)
                if p is None:
                    continue
                p_clip = max(min(p, 1 - 1e-6), 1e-6)
                ll_sum += -math.log(p_clip)
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


# ============================== SINGLE-CONFIG JSON EMITTER ==============================

def metrics_from_preds(preds):
    """preds: list of predicted P(winner wins). Compute summary metrics."""
    n = len(preds)
    if n == 0:
        return {"n": 0, "log_loss": None, "brier": None, "accuracy": None}
    ll = 0.0
    br = 0.0
    correct = 0
    for p in preds:
        pc = max(min(p, 1 - 1e-6), 1e-6)
        ll += -math.log(pc)
        br += (1 - p) ** 2
        if p > 0.5:
            correct += 1
    return {
        "n": n,
        "log_loss": ll / n,
        "brier": br / n,
        "accuracy": correct / n,
    }


def calibration_bins(preds, n_bins=10):
    """Bucket predictions into n_bins equal-width intervals over [0, 1] and
    report mean predicted prob vs. observed win rate per bucket. Winner-side
    framing means outcome is always 1, so observed rate within a bin is just
    (count of predictions in that bin) / (count) — wait, that's always 1.
    The actual observed rate per bin = fraction of predictions where outcome=1.
    Since outcome=1 always here, calibration is judged differently: at bin
    [0.8, 0.9], the predicted is ~0.85 and observed (i.e. winner-side
    realization) should also be ~0.85 — meaning the model said "this player
    wins 85%" and they won 85% of the time when looking at all such cases.

    To recover that, we ALSO include each match from the loser perspective:
    (1 - p, outcome=0). Then bucket all (prob, outcome) pairs.
    """
    pairs = []
    for p in preds:
        pairs.append((p, 1))
        pairs.append((1 - p, 0))
    bins = []
    for i in range(n_bins):
        lo = i / n_bins
        hi = (i + 1) / n_bins
        bucket = [(pp, oo) for (pp, oo) in pairs if (lo <= pp < hi) or (i == n_bins - 1 and pp == 1.0)]
        if not bucket:
            bins.append({"bin": i, "lo": lo, "hi": hi, "n": 0, "mean_pred": None, "observed_rate": None})
            continue
        mean_pred = sum(pp for pp, _ in bucket) / len(bucket)
        observed = sum(oo for _, oo in bucket) / len(bucket)
        bins.append({
            "bin": i, "lo": lo, "hi": hi,
            "n": len(bucket),
            "mean_pred": mean_pred,
            "observed_rate": observed,
        })
    return bins


def prediction_histogram(preds, n_bins=20):
    """Distribution of winner-side predicted probabilities, n_bins over [0, 1]."""
    counts = [0] * n_bins
    for p in preds:
        idx = min(int(p * n_bins), n_bins - 1)
        counts[idx] += 1
    return [{"bin": i, "lo": i / n_bins, "hi": (i + 1) / n_bins, "count": counts[i]}
            for i in range(n_bins)]


# ============================== PLATT CALIBRATION ==============================

# Platt scaling: p_cal = sigmoid(A * logit(p_raw) + B). Two parameters fit by
# minimising binary cross-entropy. Strictly monotonic, won't distort orderings.
# Justification: the raw model's calibration curve shows a near-sigmoidal
# deviation (overconfident at the extremes) that a single sigmoid corrects
# cleanly. Isotonic would also work but adds parameters with little gain.

_EPS = 1e-7


def _sigmoid(z):
    if z >= 0:
        e = math.exp(-z)
        return 1.0 / (1.0 + e)
    e = math.exp(z)
    return e / (1.0 + e)


def _logit(p):
    p = max(_EPS, min(1 - _EPS, p))
    return math.log(p / (1 - p))


def fit_platt(probs, outcomes, n_iter=4000, lr=0.05):
    """Gradient descent on log-loss. Pure Python — no scipy/sklearn dependency.
    With ~5k samples and 2 parameters this converges quickly."""
    n = len(probs)
    if n == 0:
        return 1.0, 0.0
    zs = [_logit(p) for p in probs]
    A, B = 1.0, 0.0
    last_loss = float("inf")
    for it in range(n_iter):
        gA = 0.0
        gB = 0.0
        loss = 0.0
        for i in range(n):
            zi = A * zs[i] + B
            p_cal = _sigmoid(zi)
            diff = p_cal - outcomes[i]
            gA += diff * zs[i]
            gB += diff
            # loss accumulates only every 200 iters for early-stop check
            if it % 200 == 0:
                pc = max(_EPS, min(1 - _EPS, p_cal))
                loss -= outcomes[i] * math.log(pc) + (1 - outcomes[i]) * math.log(1 - pc)
        gA /= n; gB /= n
        A -= lr * gA
        B -= lr * gB
        if it % 200 == 0:
            loss /= n
            if abs(last_loss - loss) < 1e-7:
                break
            last_loss = loss
    return A, B


def apply_platt(p, A, B):
    if p is None:
        return None
    return _sigmoid(A * _logit(p) + B)


def run_single(tour, test_year, history_years, recency, elo_weight, sample_prior,
               calibration_year=None):
    """Run one config and return a dict ready to JSON-dump.
    If `calibration_year` is given, also fit Platt scaling on predictions from
    that year and report pre- vs. post-calibration metrics on the test year."""
    extras = (calibration_year,) if (calibration_year and calibration_year != test_year) else ()
    snapshots = build_snapshots(tour, test_year, history_years,
                                extra_snapshot_years=extras)
    if not snapshots:
        return None

    # Score every snapshot; tag with year + surface.
    rows = []
    for s in snapshots:
        p = predict_snapshot(s, recency, elo_weight, sample_prior)
        if p is None:
            continue
        rows.append({"year": s["year"], "surface": s["surface"], "p": p})

    if not rows:
        return None

    test_rows = [r for r in rows if r["year"] == test_year]
    preds_all = [r["p"] for r in test_rows]
    summary = metrics_from_preds(preds_all)

    by_surface = {}
    for surf in SURFACES_BC:
        preds_s = [r["p"] for r in test_rows if r["surface"] == surf]
        by_surface[surf] = metrics_from_preds(preds_s)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tour": tour,
        "test_year": test_year,
        "history_years": list(history_years),
        "config": {
            "recency_weight": recency,
            "elo_weight": elo_weight,
            "sample_prior": sample_prior,
        },
        "summary": summary,
        "by_surface": by_surface,
        "calibration_bins": calibration_bins(preds_all, n_bins=10),
        "histogram": prediction_histogram(preds_all, n_bins=20),
    }

    if calibration_year and calibration_year != test_year:
        # Build the calibration-fit set from a *separate* year so the Platt
        # parameters are out-of-sample relative to the reported metrics.
        # Fit on both perspectives (winner: y=1, loser: y=0) so Platt sees a
        # balanced binary problem rather than all-1s.
        fit_rows = [r for r in rows if r["year"] == calibration_year]
        if fit_rows:
            fit_probs, fit_outcomes = [], []
            for r in fit_rows:
                fit_probs.append(r["p"]);     fit_outcomes.append(1)
                fit_probs.append(1 - r["p"]); fit_outcomes.append(0)
            A, B = fit_platt(fit_probs, fit_outcomes)
            sys.stderr.write(f"  platt fit on {calibration_year}: A={A:.4f} B={B:.4f} "
                             f"(n={len(fit_rows)} matches)\n")

            # Apply Platt to the test-year predictions and compute new metrics.
            preds_cal = [apply_platt(p, A, B) for p in preds_all]
            summary_cal = metrics_from_preds(preds_cal)
            by_surface_cal = {}
            for surf in SURFACES_BC:
                preds_s = [apply_platt(r["p"], A, B)
                           for r in test_rows if r["surface"] == surf]
                by_surface_cal[surf] = metrics_from_preds(preds_s)
            payload["platt"] = {
                "A": A, "B": B,
                "fit_year": calibration_year,
                "fit_n_matches": len(fit_rows),
            }
            payload["summary_calibrated"] = summary_cal
            payload["by_surface_calibrated"] = by_surface_cal
            payload["calibration_bins_calibrated"] = calibration_bins(preds_cal, n_bins=10)
            payload["histogram_calibrated"] = prediction_histogram(preds_cal, n_bins=20)

    return payload


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
    ap.add_argument("--test-year", type=int, default=2025)
    ap.add_argument("--history", type=int, nargs="+",
                    default=list(range(2018, 2025)),
                    help="Years to use as history before the test year (Elo + stats warmup).")
    ap.add_argument("--grid", type=float, default=0.1,
                    help="Step size for grid search over recency_w and elo_w. Default 0.1.")
    ap.add_argument("--sample-prior", type=int, default=15,
                    help="Sample-size shrinkage strength (n0). 0 disables. Default 15.")
    ap.add_argument("--json", action="store_true",
                    help="Skip grid search; run a single config matching the UI "
                         "defaults and emit backtest_<tour>.json for the dashboard.")
    ap.add_argument("--recency", type=float, default=0.7,
                    help="recency_weight for --json mode (default 0.7).")
    ap.add_argument("--elo-weight", type=float, default=0.4,
                    help="elo_weight for --json mode (default 0.4).")
    ap.add_argument("--calibration-year", type=int, default=2024,
                    help="Year used to fit Platt calibration (out-of-sample "
                         "vs --test-year). Set to 0 to disable. Default 2024.")
    args = ap.parse_args()

    tours = ["atp", "wta"] if args.tour == "both" else [args.tour]

    if args.json:
        cal_year = args.calibration_year or None
        if cal_year == 0:
            cal_year = None
        for tour in tours:
            payload = run_single(tour, args.test_year, args.history,
                                 args.recency, args.elo_weight, args.sample_prior,
                                 calibration_year=cal_year)
            if payload is None:
                sys.stderr.write(f"no results for {tour}\n")
                continue
            root = os.path.dirname(os.path.abspath(__file__))
            with open(os.path.join(root, f"backtest_{tour}.json"), "w") as f:
                json.dump(payload, f, indent=2)
            # Separate calibration file consumed by the JS model on each page.
            if "platt" in payload:
                cal_payload = {
                    "generated_at": payload["generated_at"],
                    "tour": tour,
                    "method": "platt",
                    "A": payload["platt"]["A"],
                    "B": payload["platt"]["B"],
                    "fit_year": payload["platt"]["fit_year"],
                    "fit_n_matches": payload["platt"]["fit_n_matches"],
                    "metrics_uncalibrated": payload["summary"],
                    "metrics_calibrated": payload["summary_calibrated"],
                }
                with open(os.path.join(root, f"calibration_{tour}.json"), "w") as f:
                    json.dump(cal_payload, f, indent=2)
            s = payload["summary"]
            sc = payload.get("summary_calibrated")
            line = (f"wrote backtest_{tour}.json — "
                    f"raw: brier={s['brier']:.4f} acc={s['accuracy']*100:.2f}%")
            if sc:
                line += (f" → calibrated: brier={sc['brier']:.4f} "
                         f"acc={sc['accuracy']*100:.2f}%")
            sys.stderr.write(line + "\n")
        return

    step = args.grid
    grid = [round(i * step, 4) for i in range(int(round(1.0 / step)) + 1)]
    for tour in tours:
        results = run_backtest(tour, args.test_year, args.history, grid, grid,
                               sample_prior=args.sample_prior)
        report(results, tour, args.test_year)


if __name__ == "__main__":
    main()
