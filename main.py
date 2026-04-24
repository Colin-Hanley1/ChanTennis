"""Tennis Markov match simulator.

Pipeline:
  ATP leaderboard stats (SPW, RPW) -> Barnett-Clarke point-on-serve probability
  -> analytical recursion for P(game), P(tiebreak), P(set), P(match)
  -> Monte Carlo simulator for score distributions.
"""

import argparse
import csv
import random
from functools import lru_cache


TOUR_AVG_SPW = 0.638
SURFACES = ("all", "hard", "clay", "grass")
PERIODS = ("52week", "career")


def load_players(path):
    # Keyed by player -> {(surface, period): {"spw": ..., "rpw": ...}}.
    players = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            surface = row["surface"].strip().lower()
            period = row["period"].strip().lower()
            if surface not in SURFACES:
                raise ValueError(f"bad surface {surface!r} for {row['player']}")
            if period not in PERIODS:
                raise ValueError(f"bad period {period!r} for {row['player']}")
            players.setdefault(row["player"], {})[(surface, period)] = {
                "spw": float(row["spw"]),
                "rpw": float(row["rpw"]),
            }
    return players


def effective_stats(player_rows, surface, recency_weight):
    # Blend 52-week with career on the requested surface, falling back to 'all'
    # when surface-specific data is missing.
    def pick(period, surf):
        return player_rows.get((surf, period)) or player_rows.get(("all", period))

    s_52 = pick("52week", surface)
    s_career = pick("career", surface)
    if s_52 is None and s_career is None:
        raise ValueError(f"no stats available (surface={surface})")
    if s_52 is None:
        return dict(s_career)
    if s_career is None:
        return dict(s_52)
    w = recency_weight
    return {
        "spw": w * s_52["spw"] + (1 - w) * s_career["spw"],
        "rpw": w * s_52["rpw"] + (1 - w) * s_career["rpw"],
    }


def tour_avg_for(surface):
    # Rough ATP baselines per surface. Fast surfaces favor the server.
    return {
        "all": 0.638,
        "hard": 0.640,
        "clay": 0.620,
        "grass": 0.665,
    }[surface]


def point_on_serve_prob(server, returner, tour_avg_spw=TOUR_AVG_SPW):
    # Barnett-Clarke: f_i + g_av - g_j, where g_av = 1 - f_av.
    return server["spw"] + (1 - tour_avg_spw) - returner["rpw"]


def game_win_prob(p):
    if p <= 0:
        return 0.0
    if p >= 1:
        return 1.0
    q = 1 - p
    direct = p ** 4 * (1 + 4 * q + 10 * q ** 2)
    reach_deuce = 20 * p ** 3 * q ** 3
    from_deuce = p ** 2 / (p ** 2 + q ** 2)
    return direct + reach_deuce * from_deuce


def _other(s):
    return "B" if s == "A" else "A"


def _tb_server(points_played, first_server):
    # Point 1 served by first_server; after that, serve alternates every 2 points.
    flipped = ((points_played + 1) // 2) % 2 == 1
    return _other(first_server) if flipped else first_server


def tiebreak_win_prob(p_a, p_b, first_server="A"):
    # Tail closed form at any tied state (k,k), k>=6. Solving the Markov chain
    # on (parity of k, Δ ∈ {-1,0,+1}) shows P(A wins) is independent of k and
    # collapses to p_a*qb / (1 - α) where α = p_a*p_b + qa*qb. For first_server=B,
    # swap roles: P(A wins) = 1 - p_b*qa / (1 - α).
    qa, qb = 1 - p_a, 1 - p_b
    alpha = p_a * p_b + qa * qb
    if alpha >= 1:
        tied_tail = 0.5
    elif first_server == "A":
        tied_tail = p_a * qb / (1 - alpha)
    else:
        tied_tail = 1 - p_b * qa / (1 - alpha)

    @lru_cache(maxsize=None)
    def prob(a, b):
        if a >= 7 and a - b >= 2:
            return 1.0
        if b >= 7 and b - a >= 2:
            return 0.0
        if a == b and a >= 6:
            return tied_tail
        p_win = p_a if _tb_server(a + b, first_server) == "A" else (1 - p_b)
        return p_win * prob(a + 1, b) + (1 - p_win) * prob(a, b + 1)

    return prob(0, 0)


def set_win_prob(p_a, p_b, first_server="A"):
    pg_a = game_win_prob(p_a)
    pg_a_returning = 1 - game_win_prob(p_b)

    @lru_cache(maxsize=None)
    def prob(a, b):
        if a == 6 and b <= 4:
            return 1.0
        if b == 6 and a <= 4:
            return 0.0
        if a == 7 and b == 5:
            return 1.0
        if b == 7 and a == 5:
            return 0.0
        if a == 6 and b == 6:
            return tiebreak_win_prob(p_a, p_b, first_server)
        server = first_server if (a + b) % 2 == 0 else _other(first_server)
        win_game = pg_a if server == "A" else pg_a_returning
        return win_game * prob(a + 1, b) + (1 - win_game) * prob(a, b + 1)

    return prob(0, 0)


def match_win_prob(p_a, p_b, best_of=3):
    sets_to_win = (best_of + 1) // 2
    sp_first_a = set_win_prob(p_a, p_b, "A")
    sp_first_b = set_win_prob(p_a, p_b, "B")

    @lru_cache(maxsize=None)
    def prob(a_sets, b_sets, set_idx):
        if a_sets == sets_to_win:
            return 1.0
        if b_sets == sets_to_win:
            return 0.0
        sp = sp_first_a if set_idx % 2 == 0 else sp_first_b
        return sp * prob(a_sets + 1, b_sets, set_idx + 1) + (1 - sp) * prob(a_sets, b_sets + 1, set_idx + 1)

    return prob(0, 0, 0)


def _simulate_game(p_server_wins, rng):
    a, b = 0, 0
    while True:
        if rng.random() < p_server_wins:
            a += 1
        else:
            b += 1
        if a >= 4 and a - b >= 2:
            return True
        if b >= 4 and b - a >= 2:
            return False


def _simulate_tiebreak(p_a, p_b, first_server, rng):
    a, b, played = 0, 0, 0
    while True:
        server = _tb_server(played, first_server)
        p_win_a = p_a if server == "A" else (1 - p_b)
        if rng.random() < p_win_a:
            a += 1
        else:
            b += 1
        played += 1
        if a >= 7 and a - b >= 2:
            return True, (a, b)
        if b >= 7 and b - a >= 2:
            return False, (a, b)


def _simulate_set(p_a, p_b, first_server, rng):
    a, b = 0, 0
    while True:
        if a == 6 and b == 6:
            won, tb = _simulate_tiebreak(p_a, p_b, first_server, rng)
            return (won, (7, 6) if won else (6, 7), tb)
        server = first_server if (a + b) % 2 == 0 else _other(first_server)
        if server == "A":
            a_won_game = _simulate_game(p_a, rng)
        else:
            a_won_game = not _simulate_game(p_b, rng)
        if a_won_game:
            a += 1
        else:
            b += 1
        if a == 6 and b <= 4:
            return True, (6, b), None
        if b == 6 and a <= 4:
            return False, (a, 6), None
        if a == 7 and b == 5:
            return True, (7, 5), None
        if b == 7 and a == 5:
            return False, (5, 7), None


def simulate_match(p_a, p_b, best_of, rng):
    sets_to_win = (best_of + 1) // 2
    a_sets, b_sets = 0, 0
    scores = []
    idx = 0
    while a_sets < sets_to_win and b_sets < sets_to_win:
        first_server = "A" if idx % 2 == 0 else "B"
        won, score, tb = _simulate_set(p_a, p_b, first_server, rng)
        scores.append((score, tb))
        if won:
            a_sets += 1
        else:
            b_sets += 1
        idx += 1
    return {
        "winner": "A" if a_sets > b_sets else "B",
        "sets": (a_sets, b_sets),
        "set_scores": scores,
    }


def simulate_many(p_a, p_b, best_of, n, seed=None):
    rng = random.Random(seed)
    a_wins = 0
    set_dist = {}
    for _ in range(n):
        r = simulate_match(p_a, p_b, best_of, rng)
        if r["winner"] == "A":
            a_wins += 1
        set_dist[r["sets"]] = set_dist.get(r["sets"], 0) + 1
    return {
        "a_win_rate": a_wins / n,
        "set_distribution": {k: v / n for k, v in sorted(set_dist.items())},
    }


def main():
    parser = argparse.ArgumentParser(description="Tennis Markov match simulator")
    parser.add_argument("--players", default="players.csv",
                        help="CSV: player,surface,period,spw,rpw")
    parser.add_argument("--a", required=True, help="Player A name (must match CSV)")
    parser.add_argument("--b", required=True, help="Player B name (must match CSV)")
    parser.add_argument("--best-of", type=int, choices=[3, 5], default=3)
    parser.add_argument("--surface", choices=list(SURFACES), default="hard")
    parser.add_argument("--recency-weight", type=float, default=0.7,
                        help="Weight on 52-week (vs career). 0.7 = standard.")
    parser.add_argument("--tour-avg-spw", type=float, default=None,
                        help="Overrides the surface-specific default.")
    parser.add_argument("--sims", type=int, default=10000, help="Monte Carlo trials; 0 to skip")
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()

    players = load_players(args.players)
    for name in (args.a, args.b):
        if name not in players:
            raise SystemExit(f"player {name!r} not in {args.players}")

    pa = effective_stats(players[args.a], args.surface, args.recency_weight)
    pb = effective_stats(players[args.b], args.surface, args.recency_weight)

    tour_avg = args.tour_avg_spw if args.tour_avg_spw is not None else tour_avg_for(args.surface)

    p_a = point_on_serve_prob(pa, pb, tour_avg)
    p_b = point_on_serve_prob(pb, pa, tour_avg)

    print(f"surface={args.surface}  recency_w={args.recency_weight}  tour_avg_SPW={tour_avg:.3f}")
    print(f"{args.a}: SPW={pa['spw']:.3f}  RPW={pa['rpw']:.3f}  (blended)")
    print(f"{args.b}: SPW={pb['spw']:.3f}  RPW={pb['rpw']:.3f}  (blended)")
    print(f"P({args.a} wins pt on serve) = {p_a:.4f}")
    print(f"P({args.b} wins pt on serve) = {p_b:.4f}")
    print()

    analytic = match_win_prob(p_a, p_b, args.best_of)
    print(f"Analytical  P({args.a} wins Bo{args.best_of}) = {analytic:.4f}")

    if args.sims > 0:
        sim = simulate_many(p_a, p_b, args.best_of, args.sims, args.seed)
        print(f"Monte Carlo P({args.a} wins Bo{args.best_of}) = {sim['a_win_rate']:.4f}  ({args.sims} trials)")
        print("Set-score distribution (A sets - B sets):")
        for (a_s, b_s), freq in sim["set_distribution"].items():
            print(f"  {a_s}-{b_s}: {freq:.4f}")


if __name__ == "__main__":
    main()
