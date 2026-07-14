"""
migration_speed_conditional_audit.py — READ-ONLY.

Follow-up: does migration speed add INCREMENTAL evidence *conditional on lineage
already flagging WATCHTOWER*?  (Not: can speed classify on its own — that was
already answered No.)

Tests, all restricted to the lineage-flagged population:
  1. Does speed stratify attribution hardness?  (CONFIRMED vs STRONG grade;
     operation-member vs lineage-only)
  2. Incremental predictive value: logistic  outcome ~ lineage_method  vs
     outcome ~ lineage_method + log(speed).  Likelihood-ratio test + AUC.
  3. Corroboration: within lineage hits, confirmed-operation rate for fast vs slow.

HONESTY: there is NO labelled lineage-false-positive set in the DB (every
confirmed operation is confirmed; no "lineage fired but wrong" label exists).
So we CANNOT test "speed separates lineage TPs from lineage FPs". We use the
available proxies for attribution strength and say so.

Modifies nothing.  Run: python3 migration_speed_conditional_audit.py
"""
from __future__ import annotations
import sqlite3, datetime, math
import numpy as np
from scipy import stats

DB = "database/flex_complete_database.db"

def to_unix(v):
    if v is None: return None
    if isinstance(v,(int,float)): return int(v)
    s=str(v).strip()
    if s.isdigit(): return int(s)
    try: return int(datetime.datetime.fromisoformat(s.replace("Z","+00:00")).timestamp())
    except Exception: return None

def load(conn):
    rows = conn.execute("""
        SELECT ta.mint,
               COALESCE(ta.earliest_tx_creator, ta.pf_ws_creator) AS creator,
               ta.created_at, ta.migrated_at,
               crs.watchtower_related,
               crs.evidence_grade,
               json_extract(crs.evidence_basis,'$.method') AS wt_method
        FROM token_analysis ta
        JOIN creator_risk_scores crs
          ON crs.creator_address = COALESCE(ta.earliest_tx_creator, ta.pf_ws_creator)
        WHERE ta.lifecycle_stage='migrated'
          AND ta.migrated_at IS NOT NULL AND ta.created_at IS NOT NULL
          AND crs.watchtower_related = 1
    """).fetchall()
    op_mints = set(r[0] for r in conn.execute("SELECT DISTINCT token_mint FROM wt_operation_members").fetchall())
    # tokens whose creator belongs to a CONFIRMED operation
    confirmed_creators = set(r[0] for r in conn.execute("""
        SELECT DISTINCT m.creator_wallet FROM wt_operation_members m
        JOIN wt_operations o ON o.operation_id=m.operation_id
        WHERE o.state='CONFIRMED'
    """).fetchall())
    recs=[]
    for r in rows:
        t0,t1 = to_unix(r["created_at"]), to_unix(r["migrated_at"])
        if t0 is None or t1 is None: continue
        d = t1-t0
        if d<=0 or d>30*86400: continue
        recs.append({
            "mint":r["mint"],"creator":r["creator"],"speed":d,
            "grade":r["evidence_grade"],"method":r["wt_method"],
            "in_operation": r["mint"] in op_mints,
            "confirmed_op": r["creator"] in confirmed_creators,
        })
    return recs

def med(a): return np.median(a) if len(a) else float("nan")
def fmt(s):
    if s!=s: return "—"
    if s<60: return f"{s:.0f}s"
    if s<3600: return f"{s/60:.1f}m"
    return f"{s/3600:.1f}h"

def cliffs(x,y):
    x=np.asarray(x,float); y=np.asarray(y,float)
    gt=sum(np.sum(xi>y) for xi in x); lt=sum(np.sum(xi<y) for xi in x)
    return (gt-lt)/(len(x)*len(y))
def mag(d):
    d=abs(d); return "negligible" if d<.147 else "small" if d<.33 else "medium" if d<.474 else "large"

def two_group(name,a,b,la,lb):
    print(f"\n{'-'*68}\n{name}\n  {la} n={len(a)} median={fmt(med(a))} | {lb} n={len(b)} median={fmt(med(b))}")
    if len(a)<5 or len(b)<5:
        print("  (insufficient sample — need >=5 each)"); return
    u,p = stats.mannwhitneyu(a,b,alternative="two-sided")
    d = cliffs(a,b)
    print(f"  Mann-Whitney p={p:.3e} | Cliff's delta={d:+.3f} ({mag(d)})  [<0 ⇒ {la} faster]")

# ── minimal logistic regression (no sklearn dependency) ──────────────────────
def logistic_fit(X, y, iters=200, lr=None):
    """Newton-IRLS logistic regression. X includes intercept col. Returns beta, loglik."""
    X=np.asarray(X,float); y=np.asarray(y,float)
    n,k = X.shape
    beta=np.zeros(k)
    for _ in range(iters):
        z=X@beta; p=1/(1+np.exp(-z)); p=np.clip(p,1e-9,1-1e-9)
        W=p*(1-p)
        grad=X.T@(y-p)
        H=(X*W[:,None]).T@X + 1e-6*np.eye(k)   # ridge for stability
        try: step=np.linalg.solve(H,grad)
        except np.linalg.LinAlgError: break
        beta=beta+step
        if np.max(np.abs(step))<1e-8: break
    z=X@beta; p=np.clip(1/(1+np.exp(-z)),1e-9,1-1e-9)
    ll=np.sum(y*np.log(p)+(1-y)*np.log(1-p))
    return beta, ll, p

def auc(y, scores):
    y=np.asarray(y); s=np.asarray(scores)
    pos=s[y==1]; neg=s[y==0]
    if len(pos)==0 or len(neg)==0: return float("nan")
    # Mann-Whitney U based AUC
    order=np.argsort(s); ranks=np.empty(len(s)); ranks[order]=np.arange(1,len(s)+1)
    return (ranks[y==1].sum()-len(pos)*(len(pos)+1)/2)/(len(pos)*len(neg))

def incremental_test(recs, outcome_key, outcome_name):
    print(f"\n{'='*68}\nINCREMENTAL VALUE: does log(speed) improve prediction of [{outcome_name}]\n"
          f"on top of lineage_method, WITHIN lineage-flagged tokens?\n{'='*68}")
    methods = sorted(set(r["method"] or "NULL" for r in recs))
    y = np.array([1 if r[outcome_key] else 0 for r in recs],float)
    if y.sum()<5 or (len(y)-y.sum())<5:
        print(f"  outcome too imbalanced (pos={int(y.sum())}, neg={int(len(y)-y.sum())}) — skipping."); return
    # design matrix: intercept + method dummies (drop first)
    base_methods=methods[1:]
    def dummies(r):
        return [1.0]+[1.0 if (r["method"] or "NULL")==m else 0.0 for m in base_methods]
    X0=np.array([dummies(r) for r in recs])
    logsp=np.array([math.log(r["speed"]) for r in recs]); logsp=(logsp-logsp.mean())/logsp.std()
    X1=np.column_stack([X0, logsp])
    b0,ll0,p0=logistic_fit(X0,y); b1,ll1,p1=logistic_fit(X1,y)
    lr=2*(ll1-ll0); dof=1; pval=stats.chi2.sf(lr,dof)
    print(f"  n={len(recs)}  pos[{outcome_name}]={int(y.sum())}  neg={int(len(y)-y.sum())}")
    print(f"  model A: lineage_method only          loglik={ll0:.2f}  AUC={auc(y,p0):.3f}")
    print(f"  model B: lineage_method + log(speed)  loglik={ll1:.2f}  AUC={auc(y,p1):.3f}")
    print(f"  Likelihood-ratio test (speed term): chi2={lr:.3f}, df=1, p={pval:.3e}")
    print(f"  speed coefficient (standardized): {b1[-1]:+.3f}  [<0 ⇒ faster ⇒ higher {outcome_name}]")
    print(f"  AUC gain from adding speed: {auc(y,p1)-auc(y,p0):+.3f}")

def corroboration(recs, outcome_key, outcome_name):
    print(f"\n{'='*68}\nCORROBORATION: within lineage hits, [{outcome_name}] rate by speed band\n{'='*68}")
    bands=[("<5s",0,5),("5–60s",5,60),("1–5m",60,300),(">5m",300,10**9)]
    for lbl,lo,hi in bands:
        sub=[r for r in recs if lo<=r["speed"]<hi]
        if not sub: print(f"  {lbl:7s}: n=0"); continue
        rate=100*sum(1 for r in sub if r[outcome_key])/len(sub)
        print(f"  {lbl:7s}: n={len(sub):4d}  {outcome_name} rate={rate:5.1f}%")

def main():
    conn=sqlite3.connect(DB); conn.row_factory=sqlite3.Row
    recs=load(conn); conn.close()
    print(f"LINEAGE-FLAGGED MIGRATED UNIVERSE: {len(recs)} tokens (watchtower_related=1, valid speed)")
    print("  method split:", {m:sum(1 for r in recs if (r['method'] or 'NULL')==m) for m in sorted(set(r['method'] or 'NULL' for r in recs))})
    print("  grade split :", {g:sum(1 for r in recs if r['grade']==g) for g in sorted(set(r['grade'] for r in recs),key=str)})
    print("  in_operation:", sum(1 for r in recs if r['in_operation']), "/", len(recs))
    print("  confirmed_op:", sum(1 for r in recs if r['confirmed_op']), "/", len(recs))

    print("\n" + "#"*68 + "\n# TEST 1 — does speed stratify attribution hardness (given lineage)?\n" + "#"*68)
    two_group("CONFIRMED-grade vs STRONG-grade",
              [r["speed"] for r in recs if r["grade"]=="CONFIRMED"],
              [r["speed"] for r in recs if r["grade"]=="STRONG"],
              "CONFIRMED","STRONG")
    two_group("In confirmed/known operation vs lineage-only",
              [r["speed"] for r in recs if r["in_operation"]],
              [r["speed"] for r in recs if not r["in_operation"]],
              "in_operation","lineage_only")
    two_group("Creator in CONFIRMED operation vs not",
              [r["speed"] for r in recs if r["confirmed_op"]],
              [r["speed"] for r in recs if not r["confirmed_op"]],
              "confirmed_op","other")

    print("\n" + "#"*68 + "\n# TEST 2 — incremental predictive value (logistic LR test)\n" + "#"*68)
    incremental_test(recs, "in_operation", "in_operation")
    incremental_test(recs, "confirmed_op", "confirmed_op")

    print("\n" + "#"*68 + "\n# TEST 3 — corroboration (your fast-vs-slow example, conditional on lineage)\n" + "#"*68)
    corroboration(recs, "in_operation", "in_operation")
    corroboration(recs, "confirmed_op", "confirmed_op")

if __name__=="__main__":
    main()
