#!/usr/bin/env python3
"""CLI wrapper for known operator baseline report.

All analysis logic lives in src/core/pattern_discovery.py.
This script formats and prints the report; the same data is served
by the dashboard via GET /api/ops-v2/intel/operator-pattern-report.

Reports confirmed operator fingerprints (seed band, capital, mechanism,
migration rate). Not discovery — for WT-LIKE clustering use immediate_funder
grouping once that population has richer session data.

Usage:
    python3 scripts/report_operator_patterns.py [--db path] [--min-members N]
"""
import argparse
import os
import sqlite3
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

DEFAULT_DB = os.environ.get("WT_OPS_DB_PATH",
                             os.path.join(BASE, "database", "wt_ops_v2.db"))

from src.core.pattern_discovery import build_report


def hr(char="─", width=72):
    print(char * width)


def print_cluster(cluster, idx):
    conf = cluster["confidence"]
    conf_color = {"HIGH": "\033[92m", "MEDIUM": "\033[93m", "LOW": "\033[90m"}.get(conf, "")
    reset = "\033[0m"

    hr()
    print(f"  Pattern {idx}  ·  {cluster['label']}")
    hr("·")
    print(f"  Confidence          : {conf_color}{conf}{reset}  ({cluster['score']}/{cluster['max_score']})")
    print(f"  Members (subprovs)  : {len(cluster['members'])}")
    print(f"  Launches            : {cluster['n_launches']}")
    print(f"  Migration rate      : {cluster['migration_rate']}%  "
          f"({cluster['migration_count']}/{cluster['n_launches']})")

    if cluster["seed_min"] is not None:
        seed_range = (f"{cluster['seed_min']:.4f}◎"
                      if cluster["seed_min"] == cluster["seed_max"]
                      else f"{cluster['seed_min']:.4f}–{cluster['seed_max']:.4f}◎")
        print(f"  Creator seed        : {seed_range}")

    if cluster["capital_min"] is not None:
        cap_range = (f"{cluster['capital_min']:.0f}◎"
                     if cluster["capital_min"] == cluster["capital_max"]
                     else f"{cluster['capital_min']:.0f}–{cluster['capital_max']:.0f}◎")
        print(f"  Session capital     : {cap_range}  ({cluster['capital_band']}◎)")

    if cluster["mechs"]:
        print(f"  Funding mechanism   : {', '.join(cluster['mechs'])}")

    if cluster["fanouts"]:
        lo, hi = min(cluster["fanouts"]), max(cluster["fanouts"])
        print(f"  Fan-out width       : {lo}–{hi} creators/session")

    treasuries = cluster.get("treasuries", [])
    if len(treasuries) == 1:
        print(f"  Upstream funder     : {treasuries[0]}")
    elif treasuries:
        print(f"  Upstream funders    : {len(treasuries)} distinct")
        for t in treasuries:
            print(f"                        {t}")

    # Scoring breakdown
    print(f"  Signals             :")
    for k, v in cluster["breakdown"].items():
        pts, note = v["points"], v["note"]
        tick = "✓" if pts > 0 else "·"
        print(f"    {tick}  {k:<28}  +{pts}  {note}")

    # Member subprovs
    print(f"  Subprovs            :")
    for m in cluster["member_detail"]:
        seed_str = f"{m['seed_sol']:.4f}◎" if m["seed_sol"] else "—"
        print(f"    {m['subprov']}  "
              f"launches={m['launches']}  mig={m['migrated']}  "
              f"seed={seed_str}  mech={m['mechanism'] or '—'}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--min-members", type=int, default=2)
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print(f"ERROR: database not found: {args.db}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row

    report = build_report(conn, min_members=args.min_members)
    conn.close()

    if report["launches_total"] == 0:
        print("No launches in wt_watchtower_launches — nothing to cluster.")
        return

    total = report["launches_total"]
    mig = report["migrated_total"]
    funders = report["upstream_funders"]
    summ = report["summary"]

    print()
    hr("═")
    print(f"  OPERATOR PATTERN REPORT  ·  {total} launches  ·  "
          f"{mig} migrated ({report['migration_rate']}%)  ·  {funders} upstream funders")
    hr("═")

    primaries = report["primary_clusters"]
    if primaries:
        print(f"\n{'━'*72}")
        print(f"  PRIMARY CLUSTERS  —  grouped by shared upstream treasury")
        print(f"{'━'*72}")
        for i, c in enumerate(primaries, 1):
            print_cluster(c, i)
    else:
        print("  No primary clusters meet the minimum member threshold.")

    secondaries = report["secondary_clusters"]
    if secondaries:
        print(f"\n{'━'*72}")
        print(f"  SECONDARY CLUSTERS  —  ungrouped subprovs by (seed band, mechanism)")
        print(f"{'━'*72}")
        for i, c in enumerate(secondaries, len(primaries) + 1):
            print_cluster(c, i)

    singletons = report["singletons"]
    if singletons:
        print(f"\n{'━'*72}")
        print(f"  UNGROUPED SINGLETONS  —  {len(singletons)} subprovs with no matching cluster")
        print(f"{'━'*72}")
        for s in singletons:
            seed_str = f"{s['seed_sol']:.4f}◎" if s["seed_sol"] else "—"
            cap_str = f"{s['capital_sol']:.0f}◎" if s["capital_sol"] else "—"
            print(f"  {s['subprov']}  "
                  f"launches={s['launches']}  mig={s['migrated']}  "
                  f"seed={seed_str}  capital={cap_str}  mech={s['mechanism'] or '—'}")

    print()
    hr("═")
    print(f"  {summ['primary_count']} primary  ·  {summ['secondary_count']} secondary  ·  "
          f"{summ['singleton_count']} singletons  ·  "
          f"HIGH={summ['high_confidence']}  MEDIUM={summ['medium_confidence']}")
    hr("═")
    print()


if __name__ == "__main__":
    main()
