"""Refresh all site data in one command.

Modes (mutually exclusive — pick one or accept the default):
    --quick    Live ESPN results only.            ~5  sec
    (default)  Live results + tournament draws.   ~15 sec
    --full     Everything, including player
               splits + Elo ratings.              ~16 min

Then reload `tournaments.html` (or any other page) in the browser to see
the changes. All pages re-fetch their JSON / CSV on every load.

Recommended cron schedule (add via `crontab -e`):

    # Every 5 min during peak hours: live results — near-real-time bracket
    */5 8-23 * * *  cd /Users/colinhanley/Desktop/TennisMarkov && \\
                    /usr/bin/python3 refresh.py --quick >> /tmp/tm.log 2>&1

    # Every hour: draws — picks up new tournaments + any TA updates
    0 * * * *       cd /Users/colinhanley/Desktop/TennisMarkov && \\
                    /usr/bin/python3 refresh.py >> /tmp/tm.log 2>&1

    # Sunday 3am: full refresh — re-pulls all 270+ player splits + Elo
    0 3 * * 0       cd /Users/colinhanley/Desktop/TennisMarkov && \\
                    /usr/bin/python3 refresh.py --full >> /tmp/tm.log 2>&1
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))


def run(cmd, label):
    """Run a sub-script. Print a one-line status. Return True on success."""
    sys.stdout.write(f"  → {label:<42} ")
    sys.stdout.flush()
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    dt = time.time() - t0
    if proc.returncode != 0:
        sys.stdout.write(f"FAIL  ({dt:>5.1f}s)\n")
        # Last few lines of stderr for diagnosis
        tail = (proc.stderr or "").strip().splitlines()[-4:]
        for line in tail:
            sys.stdout.write(f"      {line}\n")
        return False
    # Show the script's own final summary line if there is one
    summary = ""
    if proc.stderr:
        for line in reversed(proc.stderr.strip().splitlines()):
            if line.strip().startswith(("wrote", "loaded", "✓")):
                summary = line.strip()
                break
    sys.stdout.write(f"OK    ({dt:>5.1f}s)\n")
    if summary:
        sys.stdout.write(f"      {summary}\n")
    return True


def file_status():
    """Return a list of (name, mtime_str, age_str) for each data file we serve."""
    targets = [
        "draws_atp.json", "draws_wta.json",
        "live_atp.json", "live_wta.json",
        "manual_atp.json", "manual_wta.json",
        "players.csv", "players_wta.csv",
        "elo_atp.csv", "elo_wta.csv",
    ]
    rows = []
    now = datetime.now(timezone.utc)
    for name in targets:
        path = os.path.join(ROOT, name)
        if not os.path.exists(path):
            rows.append((name, "—", "missing"))
            continue
        mtime = datetime.fromtimestamp(os.path.getmtime(path), timezone.utc)
        delta = now - mtime
        secs = int(delta.total_seconds())
        if secs < 60:           age = f"{secs}s ago"
        elif secs < 3600:       age = f"{secs // 60} min ago"
        elif secs < 86400:      age = f"{secs // 3600} h ago"
        else:                   age = f"{secs // 86400} d ago"
        rows.append((name, mtime.astimezone().strftime("%H:%M"), age))
    return rows


def datafile_summary():
    """Brief content count per file (events / matches / players / etc.)."""
    out = {}
    for tour in ("atp", "wta"):
        try:
            d = json.load(open(os.path.join(ROOT, f"draws_{tour}.json")))
            out[f"draws_{tour}"] = f"{len(d.get('events', []))} events"
        except Exception:
            pass
        try:
            d = json.load(open(os.path.join(ROOT, f"live_{tour}.json")))
            out[f"live_{tour}"] = f"{len(d.get('matches', []))} matches"
        except Exception:
            pass
        try:
            d = json.load(open(os.path.join(ROOT, f"manual_{tour}.json")))
            ms = d.get("matches", []) if isinstance(d, dict) else d
            out[f"manual_{tour}"] = f"{len(ms)} matches"
        except Exception:
            pass
    return out


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--quick", action="store_true",
                      help="Live ESPN results only (~5 sec).")
    mode.add_argument("--full", action="store_true",
                      help="Everything including player splits + Elo (~16 min).")
    ap.add_argument("--silent-on-success", action="store_true",
                    help="Suppress all output unless something fails. Use in cron.")
    args = ap.parse_args()

    when = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    t_start = time.time()
    failed = []

    # Buffer output if requested so cron can stay quiet on the happy path.
    if args.silent_on_success:
        import io
        sys.stdout = io.StringIO()

    print(f"════════ Tennis Markov refresh — {when} ════════")
    if args.full:
        print("  mode: FULL (all data, expect ~16 min)\n")
    elif args.quick:
        print("  mode: QUICK (live results only)\n")
    else:
        print("  mode: NORMAL (live results + draws, ~15s)\n")

    # Live ESPN results — always run, fastest path
    if not run(["python3", "scrape_results.py"], "live results (ESPN)"):
        failed.append("scrape_results.py")

    # Tournament draws — skip in --quick
    if not args.quick:
        if not run(["python3", "scrape_draws.py"], "tournament draws (Tennis Abstract)"):
            failed.append("scrape_draws.py")

    # Player splits + Elo — only in --full
    if args.full:
        if not run(["python3", "scrape.py", "--tour", "both", "--top", "0"],
                   "player splits + Elo (Tennis Abstract)"):
            failed.append("scrape.py")

    elapsed = time.time() - t_start
    print(f"\n  finished in {elapsed:.1f}s — {'OK' if not failed else 'WITH ERRORS'}")

    # File status table
    print("\n  ── data file status ──")
    for name, when_, age in file_status():
        print(f"    {name:<22} {when_:<8} {age}")

    # Content summary
    summary = datafile_summary()
    if summary:
        print("\n  ── content ──")
        for k, v in summary.items():
            print(f"    {k:<22} {v}")

    if failed:
        print(f"\n  ✗ FAILED: {', '.join(failed)}")
        if args.silent_on_success:
            sys.__stdout__.write(sys.stdout.getvalue())
        sys.exit(1)

    print("\n  ✓ reload your browser tab to see the updates")
    if args.silent_on_success:
        # Throw away the buffered output on success.
        pass


if __name__ == "__main__":
    main()
