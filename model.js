// Tennis Markov — shared model.
// Used by both index.html (simulator) and rankings.html (power rankings).
//
// Wrapped in an IIFE so internal `const` names don't collide with the consumer
// pages' top-level scope (classic <script> blocks share one global lexical env).
// Only `globalThis.TennisModel` leaks outward.

(function () {

//////////////////// EMBEDDED FALLBACK DATA ////////////////////

const DEFAULT_CSV_ATP = `player,surface,period,spw,rpw
Sinner,all,52week,0.683,0.428
Sinner,all,career,0.676,0.417
Sinner,hard,52week,0.693,0.434
Sinner,hard,career,0.684,0.425
Sinner,clay,52week,0.660,0.410
Sinner,clay,career,0.655,0.400
Sinner,grass,52week,0.680,0.432
Sinner,grass,career,0.672,0.420
Alcaraz,all,52week,0.676,0.421
Alcaraz,all,career,0.668,0.415
Alcaraz,hard,52week,0.678,0.420
Alcaraz,hard,career,0.670,0.415
Alcaraz,clay,52week,0.672,0.425
Alcaraz,clay,career,0.665,0.420
Alcaraz,grass,52week,0.688,0.410
Alcaraz,grass,career,0.680,0.405
Djokovic,all,52week,0.668,0.420
Djokovic,all,career,0.656,0.410
Djokovic,hard,52week,0.672,0.423
Djokovic,hard,career,0.662,0.415
Djokovic,clay,52week,0.652,0.418
Djokovic,clay,career,0.645,0.410
Djokovic,grass,52week,0.682,0.410
Djokovic,grass,career,0.675,0.400
Zverev,all,52week,0.684,0.378
Zverev,all,career,0.675,0.372
Zverev,hard,52week,0.693,0.380
Zverev,hard,career,0.684,0.375
Zverev,clay,52week,0.660,0.380
Zverev,clay,career,0.655,0.372
Zverev,grass,52week,0.695,0.360
Zverev,grass,career,0.687,0.355
`;

const DEFAULT_CSV_WTA = `player,surface,period,spw,rpw
Aryna Sabalenka,hard,52week,0.6530,0.4350
Aryna Sabalenka,clay,52week,0.6050,0.4750
Aryna Sabalenka,grass,52week,0.6090,0.4230
Aryna Sabalenka,all,52week,0.6350,0.4437
Aryna Sabalenka,hard,career,0.6150,0.4460
Aryna Sabalenka,clay,career,0.6010,0.4530
Aryna Sabalenka,grass,career,0.6130,0.4240
Aryna Sabalenka,all,career,0.6118,0.4453
Elena Rybakina,hard,52week,0.6540,0.4260
Elena Rybakina,clay,52week,0.6610,0.4260
Elena Rybakina,grass,52week,0.6320,0.4330
Elena Rybakina,all,52week,0.6533,0.4267
Elena Rybakina,hard,career,0.6300,0.4250
Elena Rybakina,clay,career,0.6140,0.4470
Elena Rybakina,grass,career,0.6430,0.4230
Elena Rybakina,all,career,0.6280,0.4295
Coco Gauff,hard,52week,0.5710,0.4750
Coco Gauff,clay,52week,0.5880,0.5190
Coco Gauff,grass,52week,0.5260,0.3390
Coco Gauff,all,52week,0.5751,0.4851
Coco Gauff,hard,career,0.5910,0.4540
Coco Gauff,clay,career,0.5780,0.4920
Coco Gauff,grass,career,0.6220,0.4130
Coco Gauff,all,career,0.5905,0.4598
Iga Swiatek,hard,52week,0.6180,0.4750
Iga Swiatek,clay,52week,0.5920,0.4590
Iga Swiatek,grass,52week,0.6800,0.4680
Iga Swiatek,all,52week,0.6217,0.4703
Iga Swiatek,hard,career,0.6180,0.4780
Iga Swiatek,clay,career,0.6290,0.4940
Iga Swiatek,grass,career,0.6370,0.4480
Iga Swiatek,all,career,0.6225,0.4800
Jessica Pegula,hard,52week,0.6200,0.4570
Jessica Pegula,clay,52week,0.5980,0.4670
Jessica Pegula,grass,52week,0.6360,0.3780
Jessica Pegula,all,52week,0.6153,0.4536
Jessica Pegula,hard,career,0.6010,0.4530
Jessica Pegula,clay,career,0.5740,0.4610
Jessica Pegula,grass,career,0.6140,0.4210
Jessica Pegula,all,career,0.5972,0.4517
Madison Keys,hard,52week,0.5840,0.4290
Madison Keys,clay,52week,0.6040,0.4600
Madison Keys,grass,52week,0.6140,0.4070
Madison Keys,all,52week,0.5957,0.4369
Madison Keys,hard,career,0.6080,0.4320
Madison Keys,clay,career,0.5990,0.4430
Madison Keys,grass,career,0.6380,0.4190
Madison Keys,all,career,0.6095,0.4331
Mirra Andreeva,hard,52week,0.5940,0.4740
Mirra Andreeva,clay,52week,0.5980,0.4520
Mirra Andreeva,grass,52week,0.6050,0.4390
Mirra Andreeva,all,52week,0.5970,0.4611
Mirra Andreeva,hard,career,0.5980,0.4640
Mirra Andreeva,clay,career,0.5860,0.4670
Mirra Andreeva,grass,career,0.6070,0.4270
Mirra Andreeva,all,career,0.5947,0.4617
Naomi Osaka,hard,52week,0.6250,0.4450
Naomi Osaka,clay,52week,0.6170,0.4270
Naomi Osaka,grass,52week,0.6270,0.4030
Naomi Osaka,all,52week,0.6240,0.4362
Naomi Osaka,hard,career,0.6240,0.4290
Naomi Osaka,clay,career,0.5870,0.4270
Naomi Osaka,grass,career,0.6180,0.3960
Naomi Osaka,all,career,0.6174,0.4258
`;

//////////////////// MODEL ////////////////////

const TOUR_AVG_BY_SURFACE = {
  atp: { all: 0.638, hard: 0.640, clay: 0.620, grass: 0.665 },
  wta: { all: 0.562, hard: 0.564, clay: 0.548, grass: 0.590 },
};
const TOUR_SOURCES = {
  atp: { csv: 'players.csv', fallback: DEFAULT_CSV_ATP },
  wta: { csv: 'players_wta.csv', fallback: DEFAULT_CSV_WTA },
};
const SURFACES = ['all', 'hard', 'clay', 'grass'];
const PERIODS  = ['52week', 'career'];

const other = s => (s === 'A' ? 'B' : 'A');

function parseCSV(text) {
  const lines = text.trim().split(/\r?\n/).filter(l => l.trim());
  if (lines.length < 2) return {};
  const header = lines[0].split(',').map(s => s.trim().toLowerCase());
  const idx = Object.fromEntries(header.map((h, i) => [h, i]));
  if (!('player' in idx && 'surface' in idx && 'period' in idx && 'spw' in idx && 'rpw' in idx)) return {};
  const players = {};
  for (let i = 1; i < lines.length; i++) {
    const parts = lines[i].split(',').map(s => s.trim());
    const name = parts[idx.player];
    const surface = (parts[idx.surface] || '').toLowerCase();
    const period  = (parts[idx.period] || '').toLowerCase();
    if (!name || !SURFACES.includes(surface) || !PERIODS.includes(period)) continue;
    const spw = parseFloat(parts[idx.spw]);
    const rpw = parseFloat(parts[idx.rpw]);
    if (!isFinite(spw) || !isFinite(rpw)) continue;
    const matches = 'matches' in idx ? parseInt(parts[idx.matches], 10) : null;
    if (!players[name]) players[name] = {};
    players[name][`${surface}|${period}`] = {
      spw, rpw,
      matches: isFinite(matches) ? matches : null,
    };
  }
  return players;
}

function effectiveStats(rows, surface, recencyWeight) {
  const pick = (period, surf) => rows[`${surf}|${period}`] || rows[`all|${period}`] || null;
  const s52 = pick('52week', surface);
  const sc  = pick('career', surface);
  if (!s52 && !sc) return null;
  if (!s52) return { ...sc };
  if (!sc)  return { ...s52 };
  const w = recencyWeight;
  return {
    spw: w * s52.spw + (1 - w) * sc.spw,
    rpw: w * s52.rpw + (1 - w) * sc.rpw,
  };
}

// Return the match count we have for a player on the given surface,
// preferring 52-week but falling back to career or the aggregated "all" row.
function sampleSize(rows, surface) {
  const try_ = (k) => rows[k] && rows[k].matches != null ? rows[k].matches : null;
  return (
    try_(`${surface}|52week`) ??
    try_(`${surface}|career`) ??
    try_(`all|52week`) ??
    try_(`all|career`) ??
    0
  );
}

function pointOnServeProb(server, returner, tourAvg) {
  return server.spw + (1 - tourAvg) - returner.rpw;
}

function gameWinProb(p) {
  if (p <= 0) return 0;
  if (p >= 1) return 1;
  const q = 1 - p;
  const direct = p ** 4 * (1 + 4 * q + 10 * q ** 2);
  const reachDeuce = 20 * p ** 3 * q ** 3;
  const fromDeuce = p ** 2 / (p ** 2 + q ** 2);
  return direct + reachDeuce * fromDeuce;
}

function tbServer(pointsPlayed, firstServer) {
  const flipped = Math.floor((pointsPlayed + 1) / 2) % 2 === 1;
  return flipped ? other(firstServer) : firstServer;
}

function tiebreakWinProb(pa, pb, firstServer) {
  const qa = 1 - pa, qb = 1 - pb;
  const alpha = pa * pb + qa * qb;
  let tail;
  if (alpha >= 1) tail = 0.5;
  else if (firstServer === 'A') tail = (pa * qb) / (1 - alpha);
  else tail = 1 - (pb * qa) / (1 - alpha);

  const memo = new Map();
  function prob(a, b) {
    const k = a * 100 + b;
    if (memo.has(k)) return memo.get(k);
    let v;
    if (a >= 7 && a - b >= 2) v = 1;
    else if (b >= 7 && b - a >= 2) v = 0;
    else if (a === b && a >= 6) v = tail;
    else {
      const s = tbServer(a + b, firstServer);
      const pw = s === 'A' ? pa : 1 - pb;
      v = pw * prob(a + 1, b) + (1 - pw) * prob(a, b + 1);
    }
    memo.set(k, v);
    return v;
  }
  return prob(0, 0);
}

function setWinProb(pa, pb, firstServer) {
  const pgA = gameWinProb(pa);
  const pgAReturning = 1 - gameWinProb(pb);
  const memo = new Map();
  function prob(a, b) {
    const k = a * 100 + b;
    if (memo.has(k)) return memo.get(k);
    let v;
    if (a === 6 && b <= 4) v = 1;
    else if (b === 6 && a <= 4) v = 0;
    else if (a === 7 && b === 5) v = 1;
    else if (b === 7 && a === 5) v = 0;
    else if (a === 6 && b === 6) v = tiebreakWinProb(pa, pb, firstServer);
    else {
      const server = (a + b) % 2 === 0 ? firstServer : other(firstServer);
      const wg = server === 'A' ? pgA : pgAReturning;
      v = wg * prob(a + 1, b) + (1 - wg) * prob(a, b + 1);
    }
    memo.set(k, v);
    return v;
  }
  return prob(0, 0);
}

function matchWinProb(pa, pb, bestOf) {
  const target = (bestOf + 1) >> 1;
  const spA = setWinProb(pa, pb, 'A');
  const spB = setWinProb(pa, pb, 'B');
  const memo = new Map();
  function prob(a, b, idx) {
    const k = a * 10000 + b * 100 + idx;
    if (memo.has(k)) return memo.get(k);
    let v;
    if (a === target) v = 1;
    else if (b === target) v = 0;
    else {
      const sp = idx % 2 === 0 ? spA : spB;
      v = sp * prob(a + 1, b, idx + 1) + (1 - sp) * prob(a, b + 1, idx + 1);
    }
    memo.set(k, v);
    return v;
  }
  return prob(0, 0, 0);
}

//////////////////// MONTE CARLO ////////////////////

function mulberry32(seed) {
  let s = seed >>> 0;
  return function () {
    s = (s + 0x6D2B79F5) >>> 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function simGame(p, rng) {
  let a = 0, b = 0;
  while (true) {
    if (rng() < p) a++; else b++;
    if (a >= 4 && a - b >= 2) return true;
    if (b >= 4 && b - a >= 2) return false;
  }
}
function simTiebreak(pa, pb, firstServer, rng) {
  let a = 0, b = 0, played = 0;
  while (true) {
    const s = tbServer(played, firstServer);
    const pw = s === 'A' ? pa : 1 - pb;
    if (rng() < pw) a++; else b++;
    played++;
    if (a >= 7 && a - b >= 2) return true;
    if (b >= 7 && b - a >= 2) return false;
  }
}
function simSet(pa, pb, firstServer, rng) {
  let a = 0, b = 0;
  while (true) {
    if (a === 6 && b === 6) {
      const aWon = simTiebreak(pa, pb, firstServer, rng);
      return { aWon, games: aWon ? [7, 6] : [6, 7], tb: true };
    }
    const server = (a + b) % 2 === 0 ? firstServer : other(firstServer);
    const won = server === 'A' ? simGame(pa, rng) : !simGame(pb, rng);
    if (won) a++; else b++;
    if (a === 6 && b <= 4) return { aWon: true, games: [6, b], tb: false };
    if (b === 6 && a <= 4) return { aWon: false, games: [a, 6], tb: false };
    if (a === 7 && b === 5) return { aWon: true, games: [7, 5], tb: false };
    if (b === 7 && a === 5) return { aWon: false, games: [5, 7], tb: false };
  }
}
function simMatch(pa, pb, bestOf, rng) {
  const target = (bestOf + 1) >> 1;
  let aSets = 0, bSets = 0, idx = 0;
  let aGames = 0, bGames = 0, tiebreaks = 0, firstSetA = null;
  while (aSets < target && bSets < target) {
    const first = idx % 2 === 0 ? 'A' : 'B';
    const r = simSet(pa, pb, first, rng);
    if (r.aWon) aSets++; else bSets++;
    aGames += r.games[0];
    bGames += r.games[1];
    if (r.tb) tiebreaks++;
    if (firstSetA === null) firstSetA = r.aWon;
    idx++;
  }
  return {
    aWin: aSets > bSets,
    sets: [aSets, bSets],
    aGames, bGames,
    tiebreaks, firstSetA,
  };
}

function simulateMany(pa, pb, bestOf, n, seed) {
  if (n <= 0) return { aWinRate: null, distribution: {}, markets: null };
  const rng = mulberry32(seed);
  let aWins = 0, firstSetA = 0, anyTb = 0, allTb = 0;
  const setDist = {};
  const diffCount = new Map();
  const totalCount = new Map();
  for (let i = 0; i < n; i++) {
    const r = simMatch(pa, pb, bestOf, rng);
    if (r.aWin) aWins++;
    if (r.firstSetA) firstSetA++;
    if (r.tiebreaks > 0) anyTb++;
    if (r.tiebreaks === (r.sets[0] + r.sets[1])) allTb++;
    const key = `${r.sets[0]}-${r.sets[1]}`;
    setDist[key] = (setDist[key] || 0) + 1;
    const diff = r.aGames - r.bGames;
    diffCount.set(diff, (diffCount.get(diff) || 0) + 1);
    const total = r.aGames + r.bGames;
    totalCount.set(total, (totalCount.get(total) || 0) + 1);
  }
  const dist = {};
  for (const k in setDist) dist[k] = setDist[k] / n;
  return {
    aWinRate: aWins / n,
    distribution: dist,
    markets: {
      n,
      setDistCounts: setDist,
      diffCount,
      totalCount,
      firstSetA: firstSetA / n,
      anyTiebreak: anyTb / n,
      allTiebreak: allTb / n,
    },
  };
}

function computeMarkets(mc, bestOf, nameA, nameB) {
  const { markets, distribution } = mc;
  if (!markets) return null;
  const n = markets.n;
  const dist = distribution;
  const target = (bestOf + 1) >> 1;

  const pStraightA = dist[`${target}-0`] || 0;
  const pStraightB = dist[`0-${target}`] || 0;
  const setRows = [];
  if (bestOf === 3) {
    setRows.push({ label: `${nameA} −1.5 sets`, p: pStraightA });
    setRows.push({ label: `${nameA} +1.5 sets`, p: 1 - pStraightB });
    setRows.push({ label: `${nameB} −1.5 sets`, p: pStraightB });
    setRows.push({ label: `${nameB} +1.5 sets`, p: 1 - pStraightA });
    setRows.push({ label: 'Match to decider (2–1 or 1–2)', p: (dist['2-1'] || 0) + (dist['1-2'] || 0) });
    setRows.push({ label: 'Straight-set match', p: pStraightA + pStraightB });
  } else {
    const a30 = dist['3-0'] || 0, a31 = dist['3-1'] || 0, a32 = dist['3-2'] || 0;
    const b30 = dist['0-3'] || 0, b31 = dist['1-3'] || 0, b32 = dist['2-3'] || 0;
    setRows.push({ label: `${nameA} −2.5 sets`, p: a30 });
    setRows.push({ label: `${nameA} −1.5 sets`, p: a30 + a31 });
    setRows.push({ label: `${nameA} +1.5 sets`, p: 1 - b30 - b31 });
    setRows.push({ label: `${nameA} +2.5 sets`, p: 1 - b30 });
    setRows.push({ label: `${nameB} −2.5 sets`, p: b30 });
    setRows.push({ label: `${nameB} −1.5 sets`, p: b30 + b31 });
    setRows.push({ label: `${nameB} +1.5 sets`, p: 1 - a30 - a31 });
    setRows.push({ label: `${nameB} +2.5 sets`, p: 1 - a30 });
    setRows.push({ label: 'Match to 5 sets', p: a32 + b32 });
    setRows.push({ label: 'Straight-set match', p: a30 + b30 });
  }

  const diffEntries = [...markets.diffCount.entries()].sort((a, b) => b[0] - a[0]);
  const cumAbove = new Map();
  let running = 0;
  for (const [d, c] of diffEntries) {
    running += c;
    cumAbove.set(d, running);
  }
  const pGE = (d) => (cumAbove.get(d) || 0) / n;
  const pAWins = (line) => pGE(Math.ceil(line + 0.01));
  const pAPlus = (line) => pGE(-Math.floor(line));

  const gameLines = bestOf === 3
    ? [1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5]
    : [1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 9.5, 11.5];
  const gameRows = [];
  for (const x of gameLines) gameRows.push({ label: `${nameA} −${x} games`, p: pAWins(x) });
  for (const x of gameLines) gameRows.push({ label: `${nameA} +${x} games`, p: pAPlus(x) });

  const totals = [...markets.totalCount.entries()].map(([t, c]) => ({ t: +t, c }))
    .sort((x, y) => x.t - y.t);
  const totCumLE = new Map();
  let runLE = 0;
  for (const { t, c } of totals) {
    runLE += c;
    totCumLE.set(t, runLE);
  }
  const pUnder = (line) => (totCumLE.get(Math.floor(line)) || 0) / n;
  const pOver = (line) => 1 - pUnder(line);

  const medianTotal = pickMedian(totals, n);
  const totalLines = [];
  for (let off = -8; off <= 8; off += 2) totalLines.push(medianTotal + off + 0.5);
  const totalRows = [];
  for (const x of totalLines) totalRows.push({ label: `Over ${x} games`, p: pOver(x) });
  for (const x of totalLines) totalRows.push({ label: `Under ${x} games`, p: pUnder(x) });

  const propRows = [
    { label: `${nameA} wins 1st set`, p: markets.firstSetA },
    { label: `${nameB} wins 1st set`, p: 1 - markets.firstSetA },
    { label: 'At least one tiebreak', p: markets.anyTiebreak },
    { label: 'No tiebreak', p: 1 - markets.anyTiebreak },
  ];
  if (bestOf === 3) {
    propRows.push({ label: 'Match goes 3 sets', p: (dist['2-1'] || 0) + (dist['1-2'] || 0) });
  } else {
    propRows.push({ label: 'Match goes 4+ sets', p: 1 - (dist['3-0'] || 0) - (dist['0-3'] || 0) });
    propRows.push({ label: 'Match goes 5 sets', p: (dist['3-2'] || 0) + (dist['2-3'] || 0) });
  }

  return { setRows, gameRows, totalRows, propRows };
}

function pickMedian(sortedTotals, n) {
  let acc = 0;
  for (const { t, c } of sortedTotals) {
    acc += c;
    if (acc >= n / 2) return t;
  }
  return sortedTotals.length ? sortedTotals[sortedTotals.length - 1].t : 20;
}

function impliedToAmerican(p) {
  if (!isFinite(p) || p <= 0 || p >= 1) return '—';
  if (p >= 0.5) return `${Math.round(-100 * p / (1 - p))}`;
  return `+${Math.round(100 * (1 - p) / p)}`;
}

//////////////////// POWER-RATING ALL-PLAY ////////////////////

/**
 * For each qualifying player, compute the average analytic Bo3 win probability
 * against every other qualifying player on the given surface.
 * Returns [{name, rate, matches}] sorted desc by rate.
 */
function allPlayRanking(players, tour, surface, opts = {}) {
  const { recencyWeight = 0.7, minMatches = 15 } = opts;
  const tourAvg = TOUR_AVG_BY_SURFACE[tour][surface];
  const names = Object.keys(players);
  // Precompute effective stats + sample size per player for this surface.
  const rows = [];
  for (const n of names) {
    const s = effectiveStats(players[n], surface, recencyWeight);
    if (!s) continue;
    const m = sampleSize(players[n], surface);
    if (m < minMatches) continue;
    rows.push({ name: n, stats: s, matches: m });
  }
  // All pairs.
  const N = rows.length;
  const sums = new Float64Array(N);
  for (let i = 0; i < N; i++) {
    for (let j = 0; j < N; j++) {
      if (i === j) continue;
      const pa = pointOnServeProb(rows[i].stats, rows[j].stats, tourAvg);
      const pb = pointOnServeProb(rows[j].stats, rows[i].stats, tourAvg);
      sums[i] += matchWinProb(pa, pb, 3);
    }
  }
  const denom = Math.max(N - 1, 1);
  return rows.map((r, i) => ({
    name: r.name,
    rate: sums[i] / denom,
    matches: r.matches,
    spw: r.stats.spw,
    rpw: r.stats.rpw,
  })).sort((a, b) => b.rate - a.rate);
}

//////////////////// EXPORT ////////////////////

globalThis.TennisModel = {
  // constants
  TOUR_AVG_BY_SURFACE, TOUR_SOURCES, SURFACES, PERIODS,
  DEFAULT_CSV_ATP, DEFAULT_CSV_WTA,
  // parsing
  parseCSV, effectiveStats, sampleSize,
  // analytic
  pointOnServeProb, gameWinProb, tbServer, tiebreakWinProb, setWinProb, matchWinProb,
  // monte carlo
  mulberry32, simGame, simTiebreak, simSet, simMatch, simulateMany,
  // betting / display
  computeMarkets, pickMedian, impliedToAmerican,
  // rankings
  allPlayRanking,
  // utils
  other,
};

})();
