"""
migration_speed_audit.py — READ-ONLY statistical audit.

Determines whether birth->migration time (migration_speed_secs) carries
predictive signal for WATCH / WATCHTOWER. Modifies NOTHING: no scoring, no
classifications, no thresholds, no production code. Pure measurement.

Run:  python3 migration_speed_audit.py
"""
from __future__ import annotations
import sqlite3, math, datetime
import numpy as np
from scipy import stats

DB = "database/flex_complete_database.db"

# ── timestamp parsing ───────────────────────────────────────────────────────
def to_unix(v):
    """created_at is TEXT ISO ('2026-06-07T07:54:21Z') or numeric; migrated_at is int."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip()
    if s.isdigit():
        return int(s)
    try:
        s2 = s.replace("Z", "+00:00")
        return int(datetime.datetime.fromisoformat(s2).timestamp())
    except Exception:
        return None

# ── load the universe: migrated tokens with a valid birth->migration delta ───
def load(conn):
    rows = conn.execute("""
        SELECT ta.mint,
               COALESCE(ta.earliest_tx_creator, ta.pf_ws_creator) AS creator,
               ta.created_at, ta.migrated_at,
               tps.risk_level,
               crs.watchtower_related,
               crs.category AS wt_category,
               json_extract(crs.evidence_basis,'$.method') AS wt_method,
               crs.total_tokens, crs.migrated_tokens
        FROM token_analysis ta
        LEFT JOIN token_prediction_scores tps ON tps.mint = ta.mint
        LEFT JOIN creator_risk_scores crs
               ON crs.creator_address = COALESCE(ta.earliest_tx_creator, ta.pf_ws_creator)
        WHERE ta.lifecycle_stage='migrated'
          AND ta.migrated_at IS NOT NULL AND ta.created_at IS NOT NULL
    """).fetchall()

    # reservoir wallets that CONVERTED (later launched)
    converted = set(r[0] for r in conn.execute(
        "SELECT wallet_address FROM wt_creator_reservoir WHERE launch_token IS NOT NULL OR status!='DORMANT'"
    ).fetchall())
    # migrations-per-creator (within this migrated universe)
    mig_per_creator = {}

    recs = []
    for r in rows:
        c, ts0, ts1 = r["creator"], to_unix(r["created_at"]), to_unix(r["migrated_at"])
        if ts0 is None or ts1 is None:
            continue
        d = ts1 - ts0
        if d <= 0 or d > 30 * 86400:      # drop nonsensical/negative & >30d outliers
            continue
        recs.append({
            "mint": r["mint"], "creator": c, "speed": d,
            "risk": r["risk_level"],
            "wt": r["watchtower_related"] == 1,
            "wt_method": r["wt_method"], "wt_cat": r["wt_category"],
            "converted_reservoir": c in converted,
        })
        if c:
            mig_per_creator[c] = mig_per_creator.get(c, 0) + 1
    for rec in recs:
        rec["creator_migrations"] = mig_per_creator.get(rec["creator"], 0)
    return recs

# ── descriptive stats ────────────────────────────────────────────────────────
def desc(speeds):
    a = np.array(speeds, dtype=float)
    if len(a) == 0:
        return None
    return {
        "count": len(a), "min": a.min(), "max": a.max(),
        "mean": a.mean(), "median": np.median(a),
        "p25": np.percentile(a, 25), "p75": np.percentile(a, 75),
        "p90": np.percentile(a, 90), "p95": np.percentile(a, 95),
        "std": a.std(ddof=1) if len(a) > 1 else 0.0,
    }

BUCKETS = [("<60s",60),("<2m",120),("<5m",300),("<15m",900),("<30m",1800),("<1h",3600)]
def buckets(speeds):
    a = np.array(speeds, dtype=float); n = len(a)
    if n == 0:
        return {}
    out = {lbl: 100.0*np.sum(a < thr)/n for lbl,thr in BUCKETS}
    out[">1h"] = 100.0*np.sum(a >= 3600)/n
    return out

def fmt_secs(s):
    if s is None: return "—"
    if s < 60: return f"{s:.0f}s"
    if s < 3600: return f"{s/60:.1f}m"
    if s < 86400: return f"{s/3600:.1f}h"
    return f"{s/86400:.1f}d"

def print_desc(name, speeds):
    d = desc(speeds)
    if not d:
        print(f"\n## {name}: NO DATA (count=0)"); return
    print(f"\n## {name}  (n={d['count']})")
    print(f"   min {fmt_secs(d['min'])} | p25 {fmt_secs(d['p25'])} | median {fmt_secs(d['median'])} "
          f"| p75 {fmt_secs(d['p75'])} | p90 {fmt_secs(d['p90'])} | p95 {fmt_secs(d['p95'])} | max {fmt_secs(d['max'])}")
    print(f"   mean {fmt_secs(d['mean'])} | std {fmt_secs(d['std'])}")
    b = buckets(speeds)
    print("   " + " | ".join(f"{k} {b[k]:.1f}%" for k,_ in BUCKETS) + f" | >1h {b['>1h']:.1f}%")

# ── effect size (rank-biserial from Mann-Whitney) + Cliff's delta ────────────
def cliffs_delta(x, y):
    x = np.asarray(x); y = np.asarray(y)
    # sample if huge to keep it tractable
    if len(x) > 4000: x = np.random.default_rng(0).choice(x, 4000, replace=False)
    if len(y) > 4000: y = np.random.default_rng(1).choice(y, 4000, replace=False)
    gt = sum(np.sum(xi > y) for xi in x); lt = sum(np.sum(xi < y) for xi in x)
    return (gt - lt) / (len(x) * len(y))

def compare(name_a, a, name_b, b):
    print(f"\n{'='*72}\nCOMPARISON: {name_a} (n={len(a)}) vs {name_b} (n={len(b)})\n{'='*72}")
    if len(a) < 5 or len(b) < 5:
        print("  Insufficient sample (need >=5 each) — skipping tests."); return
    da, db = desc(a), desc(b)
    print(f"  median {name_a}: {fmt_secs(da['median'])}   median {name_b}: {fmt_secs(db['median'])}   "
          f"diff: {fmt_secs(abs(da['median']-db['median']))}")
    u, pu = stats.mannwhitneyu(a, b, alternative="two-sided")
    ks, pks = stats.ks_2samp(a, b)
    delta = cliffs_delta(a, b)
    mag = ("negligible" if abs(delta)<0.147 else "small" if abs(delta)<0.33
           else "medium" if abs(delta)<0.474 else "large")
    print(f"  Mann-Whitney U: p={pu:.2e}")
    print(f"  Kolmogorov-Smirnov: D={ks:.3f}, p={pks:.2e}")
    print(f"  Cliff's delta (effect size): {delta:+.3f} ({mag})   "
          f"[<0 ⇒ {name_a} faster]")
    return {"p_mw": pu, "ks_D": ks, "p_ks": pks, "delta": delta}

# ── operator-fingerprint precision/recall ────────────────────────────────────
def precision_recall(recs, threshold_s, label):
    # positive class = WATCHTOWER; predictor = (speed < threshold)
    tp = sum(1 for r in recs if r["wt"] and r["speed"] < threshold_s)
    fp = sum(1 for r in recs if (not r["wt"]) and r["speed"] < threshold_s)
    fn = sum(1 for r in recs if r["wt"] and r["speed"] >= threshold_s)
    prec = tp/(tp+fp) if (tp+fp) else 0.0
    rec = tp/(tp+fn) if (tp+fn) else 0.0
    print(f"   {label:6s}: precision {prec*100:5.1f}%  recall {rec*100:5.1f}%  "
          f"(tp={tp} fp={fp} fn={fn})")
    return prec, rec

# ── main ──────────────────────────────────────────────────────────────────────
def main():
    conn = sqlite3.connect(DB); conn.row_factory = sqlite3.Row
    recs = load(conn); conn.close()
    print(f"UNIVERSE: {len(recs)} migrated tokens with valid birth→migration delta "
          f"(dropped <=0 and >30d).")

    sp = lambda f: [r["speed"] for r in recs if f(r)]

    # ── Risk groups ──
    print("\n" + "#"*72 + "\n# RISK GROUPS\n" + "#"*72)
    for lvl in ["LOW","MEDIUM","HIGH","WATCH","CRITICAL"]:
        print_desc(f"RISK={lvl}", sp(lambda r,l=lvl: r["risk"]==l))

    # ── WATCHTOWER groups (mapped to real backing data) ──
    print("\n" + "#"*72 + "\n# WATCHTOWER GROUPS  (mapped to evidence_basis / reservoir)\n" + "#"*72)
    print("# mapping: LAUNCH_DIRECT=DIRECT_INFRA ; LAUNCH_PROVISIONING=LINEAGE_RULE_2+treasury_lineage_3hop ;")
    print("#          PROFIT_EXTRACTION=PROFIT_RELAY ; RELAY_FUNDED_DORMANT(converted)=reservoir launch_token ;")
    print("#          COLLECTOR_FLOW=fee_payer_observation")
    print_desc("WT: ALL watchtower_related", sp(lambda r: r["wt"]))
    print_desc("WT: LAUNCH_DIRECT (DIRECT_INFRA)", sp(lambda r: r["wt"] and r["wt_method"]=="DIRECT_INFRA"))
    print_desc("WT: LAUNCH_PROVISIONING (lineage)", sp(lambda r: r["wt"] and r["wt_method"] in ("LINEAGE_RULE_2","treasury_lineage_3hop")))
    print_desc("WT: PROFIT_EXTRACTION (PROFIT_RELAY)", sp(lambda r: r["wt"] and r["wt_method"]=="PROFIT_RELAY"))
    print_desc("WT: COLLECTOR_FLOW (fee_payer_obs)", sp(lambda r: r["wt"] and r["wt_method"]=="fee_payer_observation"))
    print_desc("WT: RELAY_FUNDED_DORMANT (converted)", sp(lambda r: r["converted_reservoir"]))

    # ── Control groups ──
    print("\n" + "#"*72 + "\n# CONTROL GROUPS\n" + "#"*72)
    print_desc("CTRL: Non-WATCHTOWER launchers", sp(lambda r: not r["wt"]))
    print_desc("CTRL: Serial creators (>=5 migrations)", sp(lambda r: r["creator_migrations"]>=5))
    print_desc("CTRL: Creators >50 migrations", sp(lambda r: r["creator_migrations"]>50))
    print_desc("CTRL: Creators >100 migrations", sp(lambda r: r["creator_migrations"]>100))

    # ── Key Comparison #1 ──
    compare("WATCHTOWER", sp(lambda r: r["wt"]), "Non-WATCHTOWER", sp(lambda r: not r["wt"]))
    # ── Key Comparison #2 ──
    compare("LAUNCH_PROVISIONING", sp(lambda r: r["wt"] and r["wt_method"] in ("LINEAGE_RULE_2","treasury_lineage_3hop")),
            "LAUNCH_DIRECT", sp(lambda r: r["wt"] and r["wt_method"]=="DIRECT_INFRA"))
    # ── Key Comparison #3 ──
    compare("WATCH", sp(lambda r: r["risk"]=="WATCH"), "HIGH", sp(lambda r: r["risk"]=="HIGH"))

    # ── Operator Fingerprint Test ──
    print("\n" + "#"*72 + "\n# OPERATOR FINGERPRINT TEST\n" + "#"*72)
    wt = sp(lambda r: r["wt"]); other = sp(lambda r: not r["wt"])
    print(f"\nWATCHTOWER launches migrating within (n={len(wt)}):")
    bw = buckets(wt)
    for lbl,_ in BUCKETS: print(f"   {lbl:5s}: {bw[lbl]:.1f}%")
    print(f"\nAll OTHER launches within (n={len(other)}):")
    bo = buckets(other)
    for lbl,_ in BUCKETS: print(f"   {lbl:5s}: {bo[lbl]:.1f}%")
    print("\nCan migration speed ALONE distinguish WATCHTOWER? (positive=WATCHTOWER)")
    for thr,lbl in [(60,"<60s"),(120,"<2m"),(300,"<5m"),(900,"<15m")]:
        precision_recall(recs, thr, lbl)
    base = 100.0*len(wt)/len(recs)
    print(f"\n   WATCHTOWER base rate in universe: {base:.2f}%  (precision must beat this to add value)")

if __name__ == "__main__":
    main()
