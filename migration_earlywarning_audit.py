"""
migration_earlywarning_audit.py — READ-ONLY.

EARLY-WARNING question: using ONLY information available at migration time,
which features predict that a creator will LATER be attributed
watchtower_related=1 (via DIRECT_INFRA / LINEAGE_RULE_2 / treasury_lineage_3hop)?

Strict no-leakage rule: features come only from migration-time snapshots —
  - prediction_decision_context.*_at_prediction  (point-in-time snapshot)
  - token_prediction_scores.{creator_*,network_*,funding_*,creator_was_fresh}
  - token_analysis timing (created_at, migrated_at)
NO future lineage / hub / operation / attribution fields are used as inputs.
The label (watchtower_related) is the OUTCOME only.

Modifies nothing.  Run: python3 migration_earlywarning_audit.py
"""
from __future__ import annotations
import sqlite3, datetime, math, json
import numpy as np
from scipy import stats

DB = "database/flex_complete_database.db"
POS_METHODS = ("DIRECT_INFRA","LINEAGE_RULE_2","treasury_lineage_3hop")
rng = np.random.default_rng(42)

def to_unix(v):
    if v is None: return None
    if isinstance(v,(int,float)): return int(v)
    s=str(v).strip()
    if s.isdigit(): return int(s)
    try: return int(datetime.datetime.fromisoformat(s.replace("Z","+00:00")).timestamp())
    except Exception: return None

def load(conn):
    rows = conn.execute(f"""
        SELECT ta.mint,
               COALESCE(ta.earliest_tx_creator, ta.pf_ws_creator) AS creator,
               ta.created_at, ta.migrated_at,
               crs.watchtower_related AS wt,
               json_extract(crs.evidence_basis,'$.method') AS wt_method,
               -- migration-time snapshots (SAFE):
               tps.creator_score, tps.network_score, tps.funding_score,
               tps.outcome_history_score, tps.liquidation_score,
               tps.creator_was_fresh, tps.prediction_score,
               pdc.creator_quality_at_prediction      AS cq,
               pdc.creator_history_count_at_prediction AS chist,
               pdc.creator_historical_migration_count   AS cmig,
               pdc.ecosystem_quality_at_prediction     AS eco,
               pdc.network_size_at_prediction          AS netsize,
               pdc.coordinator_exposure_at_prediction  AS coord,
               pdc.funding_context_at_prediction        AS fctx
        FROM token_analysis ta
        LEFT JOIN creator_risk_scores crs
               ON crs.creator_address = COALESCE(ta.earliest_tx_creator, ta.pf_ws_creator)
        LEFT JOIN token_prediction_scores tps ON tps.mint = ta.mint
        LEFT JOIN prediction_decision_context pdc ON pdc.mint = ta.mint
        WHERE ta.lifecycle_stage='migrated'
          AND ta.migrated_at IS NOT NULL AND ta.created_at IS NOT NULL
    """).fetchall()
    recs=[]
    for r in rows:
        t0,t1=to_unix(r["created_at"]),to_unix(r["migrated_at"])
        if t0 is None or t1 is None: continue
        d=t1-t0
        if d<=0 or d>30*86400: continue
        # label: positive only if attributed via the strong lineage/infra methods
        is_pos = (r["wt"]==1 and r["wt_method"] in POS_METHODS)
        # exclude soft/fingerprint-attributed from BOTH classes (ambiguous label)
        if r["wt"]==1 and r["wt_method"] not in POS_METHODS:
            continue
        fc={}
        try: fc=json.loads(r["fctx"]) if r["fctx"] else {}
        except Exception: fc={}
        recs.append({
            "mint":r["mint"],"speed":d,"y":1 if is_pos else 0,
            "creator_score":r["creator_score"] or 0,
            "network_score":r["network_score"] or 0,
            "funding_score":r["funding_score"] or (fc.get("funding_score") or 0),
            "outcome_score":r["outcome_history_score"] or 0,
            "liq_score":r["liquidation_score"] or 0,
            "fresh":1 if r["creator_was_fresh"] else 0,
            "cq":(r["cq"] if r["cq"] is not None else -1),
            "chist":(r["chist"] if r["chist"] is not None else 0),
            "cmig":(r["cmig"] if r["cmig"] is not None else 0),
            "eco":(r["eco"] if r["eco"] is not None else -1),
            "netsize":(r["netsize"] if r["netsize"] is not None else 0),
            "coord":1 if r["coord"] else 0,
            "self_funding":1 if fc.get("self_funding") else 0,
            "has_triggering_funder":1 if fc.get("triggering_funder") else 0,
            "log_speed":math.log(d),
            "sub5s":1 if d<5 else 0,"sub60s":1 if d<60 else 0,"sub5m":1 if d<300 else 0,
        })
    return recs

FEATURES = ["creator_score","network_score","funding_score","outcome_score","liq_score",
            "fresh","cq","chist","cmig","eco","netsize","coord","self_funding",
            "has_triggering_funder","log_speed","sub5s","sub60s","sub5m"]
NONTIMING = ["creator_score","network_score","funding_score","outcome_score","liq_score",
             "fresh","cq","chist","cmig","eco","netsize","coord","self_funding","has_triggering_funder"]

def auc(y,s):
    y=np.asarray(y); s=np.asarray(s,float)
    pos=(y==1).sum(); neg=(y==0).sum()
    if pos==0 or neg==0: return float("nan")
    order=np.argsort(s); ranks=np.empty(len(s)); ranks[order]=np.arange(1,len(s)+1)
    # average ties
    return (ranks[y==1].sum()-pos*(pos+1)/2)/(pos*neg)

def cliffs(x,y):
    x=np.asarray(x,float); y=np.asarray(y,float)
    if len(x)>3000: x=rng.choice(x,3000,replace=False)
    if len(y)>3000: y=rng.choice(y,3000,replace=False)
    gt=sum(np.sum(xi>y) for xi in x); lt=sum(np.sum(xi<y) for xi in x)
    return (gt-lt)/(len(x)*len(y))
def mag(d):
    d=abs(d); return "negligible" if d<.147 else "small" if d<.33 else "medium" if d<.474 else "large"

def logistic_fit(X,y,iters=100):
    X=np.asarray(X,float); y=np.asarray(y,float); n,k=X.shape; beta=np.zeros(k)
    for _ in range(iters):
        p=np.clip(1/(1+np.exp(-(X@beta))),1e-9,1-1e-9); W=p*(1-p)
        H=(X*W[:,None]).T@X+1e-4*np.eye(k); g=X.T@(y-p)
        try: step=np.linalg.solve(H,g)
        except np.linalg.LinAlgError: break
        beta=beta+step
        if np.max(np.abs(step))<1e-8: break
    return beta
def predict(X,beta): return np.clip(1/(1+np.exp(-(np.asarray(X,float)@beta))),1e-9,1-1e-9)

def standardize(M):
    M=np.asarray(M,float); mu=M.mean(0); sd=M.std(0); sd[sd==0]=1; return (M-mu)/sd, mu, sd

def cv_auc(recs, feats, folds=5):
    y=np.array([r["y"] for r in recs])
    Xraw=np.array([[r[f] for f in feats] for r in recs])
    idx=np.arange(len(recs)); rng.shuffle(idx)
    fold_id=idx % folds
    aucs=[]
    for k in range(folds):
        tr=fold_id!=k; te=fold_id==k
        if y[te].sum()==0 or y[tr].sum()==0: continue
        Xs,mu,sd=standardize(Xraw[tr])
        Xtr=np.column_stack([np.ones(tr.sum()),Xs])
        beta=logistic_fit(Xtr,y[tr])
        Xte=(Xraw[te]-mu)/sd
        Xte=np.column_stack([np.ones(te.sum()),Xte])
        aucs.append(auc(y[te],predict(Xte,beta)))
    return np.nanmean(aucs), np.nanstd(aucs)

def main():
    conn=sqlite3.connect(DB); conn.row_factory=sqlite3.Row
    recs=load(conn); conn.close()
    pos=[r for r in recs if r["y"]==1]; neg=[r for r in recs if r["y"]==0]
    base=len(pos)/len(recs)
    print(f"POPULATION (migrated, leak-free features):")
    print(f"  positives (WATCHTOWER via {POS_METHODS}): {len(pos)}")
    print(f"  controls  (watchtower_related=0):          {len(neg)}")
    print(f"  base rate: {base*100:.2f}%   (soft/fingerprint-attributed EXCLUDED from both)")

    # ── univariate feature ranking by AUC ──
    print("\n"+"#"*70+"\n# FEATURE RANKING — univariate AUC predicting future WATCHTOWER\n"+"#"*70)
    y=np.array([r["y"] for r in recs])
    scored=[]
    for f in FEATURES:
        vals=np.array([r[f] for r in recs],float)
        a=auc(y,vals); a=max(a,1-a)  # direction-agnostic strength
        d=cliffs([r[f] for r in pos],[r[f] for r in neg])
        scored.append((f,a,d))
    for f,a,d in sorted(scored,key=lambda x:-x[1]):
        print(f"  {f:22s} AUC={a:.3f}  Cliff's δ(pos vs neg)={d:+.3f} ({mag(d)})")

    # ── migration speed deep dive ──
    print("\n"+"#"*70+"\n# MIGRATION SPEED DEEP DIVE (pos vs neg, migration-time only)\n"+"#"*70)
    sp_p=np.array([r["speed"] for r in pos],float); sp_n=np.array([r["speed"] for r in neg],float)
    fmt=lambda s: f"{s:.0f}s" if s<60 else f"{s/60:.1f}m" if s<3600 else f"{s/3600:.1f}h"
    for nm,a in [("POS",sp_p),("NEG",sp_n)]:
        print(f"  {nm}: n={len(a)} median={fmt(np.median(a))} mean={fmt(a.mean())} "
              f"p90={fmt(np.percentile(a,90))} p95={fmt(np.percentile(a,95))}")
    u,pu=stats.mannwhitneyu(sp_p,sp_n,alternative="two-sided")
    ks,pks=stats.ks_2samp(sp_p,sp_n); d=cliffs(sp_p,sp_n)
    print(f"  Mann-Whitney p={pu:.2e} | KS D={ks:.3f} p={pks:.2e} | Cliff's δ={d:+.3f} ({mag(d)}) [<0 ⇒ pos faster]")

    # ── Model A vs Model B (cross-validated AUC) ──
    print("\n"+"#"*70+"\n# MODEL A (no timing) vs MODEL B (+ migration speed)  — 5-fold CV AUC\n"+"#"*70)
    aA,sA=cv_auc(recs,NONTIMING)
    aB,sB=cv_auc(recs,NONTIMING+["log_speed","sub5s"])
    print(f"  Model A  (creator+network+funding):           AUC={aA:.3f} ± {sA:.3f}")
    print(f"  Model B  (A + log_speed + sub5s):             AUC={aB:.3f} ± {sB:.3f}")
    print(f"  AUC gain from timing features: {aB-aA:+.3f}")

    # ── threshold analysis (speed alone, early-warning framing) ──
    print("\n"+"#"*70+"\n# THRESHOLD ANALYSIS — migration speed alone, lift vs base rate\n"+"#"*70)
    print(f"  base rate = {base*100:.2f}%")
    for thr,lbl in [(1,"<1s"),(5,"<5s"),(15,"<15s"),(60,"<60s"),(300,"<5m")]:
        tp=sum(1 for r in pos if r["speed"]<thr); fp=sum(1 for r in neg if r["speed"]<thr)
        prec=tp/(tp+fp) if (tp+fp) else 0; rec=tp/len(pos) if pos else 0
        lift=(prec/base) if base else 0
        print(f"  {lbl:5s}: precision {prec*100:5.2f}%  recall {rec*100:5.1f}%  lift {lift:5.2f}x  (tp={tp} fp={fp})")

if __name__=="__main__":
    main()
