"""Streamlit front-end for the tennis Markov simulator.

Run with:  streamlit run app.py
"""

import pandas as pd
import streamlit as st

from main import (
    SURFACES,
    effective_stats,
    load_players,
    match_win_prob,
    point_on_serve_prob,
    simulate_many,
    tour_avg_for,
)


def implied_to_american(p):
    if p <= 0 or p >= 1:
        return 0
    if p >= 0.5:
        return int(round(-100 * p / (1 - p)))
    return int(round(100 * (1 - p) / p))


st.set_page_config(page_title="Tennis Markov Simulator", layout="wide")
st.title("Tennis Markov Match Simulator")

players = load_players("players.csv")
names = sorted(players.keys())

with st.sidebar:
    st.header("Settings")
    surface = st.selectbox("Surface", list(SURFACES), index=1)
    best_of = st.radio("Format", [3, 5], horizontal=True)
    recency = st.slider("Recency weight (52-week vs career)", 0.0, 1.0, 0.7, 0.05)
    sims = st.slider("Monte Carlo trials", 0, 50000, 10000, step=1000)
    default_avg = tour_avg_for(surface)
    tour_avg = st.number_input(
        "Tour-average SPW (baseline)", value=default_avg, step=0.001, format="%.3f"
    )
    seed = st.number_input("Random seed", value=42, step=1)

col1, col2 = st.columns(2)
with col1:
    a = st.selectbox("Player A", names, index=0)
with col2:
    default_b = 1 if len(names) > 1 else 0
    b = st.selectbox("Player B", names, index=default_b)

if a == b:
    st.warning("Pick two different players.")
    st.stop()

pa = effective_stats(players[a], surface, recency)
pb = effective_stats(players[b], surface, recency)
p_a_serve = point_on_serve_prob(pa, pb, tour_avg)
p_b_serve = point_on_serve_prob(pb, pa, tour_avg)

st.subheader("Blended inputs")
stats_df = pd.DataFrame(
    {
        "SPW (blended)": [pa["spw"], pb["spw"]],
        "RPW (blended)": [pa["rpw"], pb["rpw"]],
        "Point-win on serve": [p_a_serve, p_b_serve],
    },
    index=[a, b],
)
st.dataframe(stats_df.style.format("{:.3f}"), use_container_width=True)

st.subheader(f"Bo{best_of} match probability ({surface})")
p_a = match_win_prob(p_a_serve, p_b_serve, best_of)
c1, c2, c3, c4 = st.columns(4)
c1.metric(f"P({a}) analytical", f"{p_a:.1%}")
c2.metric(f"P({b}) analytical", f"{1 - p_a:.1%}")
c3.metric(f"Fair odds, {a}", f"{implied_to_american(p_a):+d}")
c4.metric(f"Fair odds, {b}", f"{implied_to_american(1 - p_a):+d}")

if sims > 0:
    with st.spinner(f"Simulating {sims:,} matches..."):
        sim = simulate_many(p_a_serve, p_b_serve, best_of, sims, seed=int(seed))
    st.metric(
        f"Monte Carlo P({a})",
        f"{sim['a_win_rate']:.1%}",
        delta=f"{(sim['a_win_rate'] - p_a) * 100:+.2f} pp vs analytical",
    )

    dist = sim["set_distribution"]
    rows = []
    for (a_sets, b_sets), freq in sorted(dist.items()):
        rows.append({"score": f"{a_sets}-{b_sets}", "winner": a if a_sets > b_sets else b, "freq": freq})
    chart_df = pd.DataFrame(rows)
    st.subheader("Set-score distribution")
    st.bar_chart(chart_df, x="score", y="freq", color="winner")
    st.dataframe(
        chart_df.assign(freq=lambda d: d["freq"].map("{:.1%}".format)),
        hide_index=True,
        use_container_width=True,
    )

with st.expander("Raw stat rows from CSV"):
    rows = []
    for surf, per in [(s, p) for s in SURFACES for p in ("52week", "career")]:
        for name in (a, b):
            stats = players[name].get((surf, per))
            if stats:
                rows.append({"player": name, "surface": surf, "period": per, **stats})
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
