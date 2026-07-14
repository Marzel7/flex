"""
fresh_decomposition_audit.py — READ-ONLY.

Conditions on creator_was_fresh=1, then asks: among FRESH creators, what
migration-time features separate those who LATER become watchtower_related=1
(via DIRECT_INFRA / LINEAGE_RULE_2 / treasury_lineage_3hop) from those who don't?

No leakage: inputs are migration-time snapshots only (tps scores, reason_codes,
pdc *_at_prediction, funding_context, timing). watchtower_related is OUTCOME only.

Two reason_codes (funder_ancestry_propagation, network_risk_token) MAY encode
multi-hop funder lineage that overlaps with how WATCHTOWER is later attributed —
flagged as POSSIBLE-LEAKAGE and reported separately.

Modifies nothing.  Run: python3 fresh_decomposition_audit.py
"""
from __future__ import annotations
import sqlite3, datetime, math, json
import numpy as np
from scipy import stats

DB="database/flex_complete_database.db"
POS_METHODS=("DIRECT_INFRA","LINEAGE_RULE_2","treasury_lineage_3hop")
rng=np.random.default_rng(7)

# reason_code flags to extract (migration-time relationship signals)
SAFE_CODES=["shared_funder_multi","shared_payout_wallet","self_funding_loop",
            "return_to_funder","repeated_g7","majority_g7","some_g7",
            "serial_migrator_10","serial_migrator_20","serial_migrator_50",
            "fresh_unlinked_creator","shared_funder"]
LEAK_CODES=["funder_ancestry_propagation","network_risk_token"]

def to_unix(v):
    if v is None: return None
    if isinstance(v,(int,float)): return int(v)
    s=str(v).strip()
    if s.isdigit(): return int(s)
    try: return int(datetime.datetime.fromisoformat(s.replace("Z","+00:00")).timestamp())
    except Exception: return None

def load(conn):
    rows=conn.execute("""
        SELECT ta.mint, COALESCE(ta.earliest_tx_creator,ta.pf_ws_creator) creator,
               ta.created_at, ta.migrated_at,
               crs.watchtower_related wt, json_extract(crs.evidence_basis,'$.method') wt_method,
               tps.creator_was_fresh fresh, tps.reason_codes,
               tps.creator_score,tps.network_score,tps.funding_score,
               tps.outcome_history_score,tps.liquidation_score,tps.prediction_score,
               pdc.creator_quality_at_prediction cq, pdc.creator_history_count_at_prediction chist,
               pdc.creator_historical_migration_count cmig, pdc.ecosystem_quality_at_prediction eco,
               pdc.network_size_at_prediction netsize, pdc.coordinator_exposure_at_prediction coord,
               pdc.funding_context_at_prediction fctx
        FROM token_analysis ta
        LEFT JOIN creator_risk_scores crs ON crs.creator_address=COALESCE(ta.earliest_tx_creator,ta.pf_ws_creator)
        LEFT JOIN token_prediction_scores tps ON tps.mint=ta.mint
        LEFT JOIN prediction_decision_context pdc ON pdc.mint=ta.mint
        WHERE ta.lifecycle_stage='migrated' AND ta.migrated_at IS NOT NULL AND ta.created_at IS NOT NULL
          AND tps.creator_was_fresh=1
    """).fetchall()
    recs=[]
    for r in rows:
        t0,t1=to_unix(r["created_at"]),to_unix(r["migrated_at"])
        if t0 is None or t1 is None: continue
        d=t1-t0
        if d<=0 or d>30*86400: continue
        if r["wt"]==1 and r["wt_method"] not in POS_METHODS:  # drop soft-attributed (ambiguous)
            continue
        y=1 if (r["wt"]==1 and r["wt_method"] in POS_METHODS) else 0
        codes=(r["reason_codes"] or "")
        fc={}
        try: fc=json.loads(r["fctx"]) if r["fctx"] else {}
        except Exception: pass
        rec={"mint":r["mint"],"creator":r["creator"],"y":y,"speed":d,"log_speed":math.log(d),
             "creator_score":r["creator_score"] or 0,"network_score":r["network_score"] or 0,
             "funding_score":r["funding_score"] or (fc.get("funding_score") or 0),
             "outcome_score":r["outcome_history_score"] or 0,"liq_score":r["liquidation_score"] or 0,
             "cq":(r["cq"] if r["cq"] is not None else -1),
             "chist":(r["chist"] or 0),"cmig":(r["cmig"] or 0),
             "eco":(r["eco"] if r["eco"] is not None else -1),
             "netsize":(r["netsize"] or 0),"coord":1 if r["coord"] else 0,
             "self_funding":1 if fc.get("self_funding") else 0,
             "second_hop_known":1 if fc.get("second_hop") else 0,
             "has_triggering_funder":1 if fc.get("triggering_funder") else 0,
             "sub1s":1 if d<1 else 0,"sub5s":1 if d<5 else 0,"sub15s":1 if d<15 else 0,
             "sub60s":1 if d<60 else 0}
        for c in SAFE_CODES: rec["rc_"+c]=1 if c in codes else 0
        for c in LEAK_CODES: rec["rc_"+c]=1 if c in codes else 0
        recs.append(rec)
    return recs

# feature groups
CREATOR=["creator_score","network_score","outcome_score","liq_score","cq","chist","cmig","eco","netsize"]
FUNDING=["funding_score","self_funding","second_hop_known","has_triggering_funder","coord"]
TIMING=["log_speed","sub1s","sub5s","sub15s","sub60s"]
REL_SAFE=["rc_"+c for c in SAFE_CODES]
REL_LEAK=["rc_"+c for c in LEAK_CODES]
ALL_SAFE=CREATOR+FUNDING+TIMING+REL_SAFE

def auc(y,s):
    y=np.asarray(y); s=np.asarray(s,float); pos=(y==1).sum(); neg=(y==0).sum()
    if pos==0 or neg==0: return float("nan")
    order=np.argsort(s); ranks=np.empty(len(s)); ranks[order]=np.arange(1,len(s)+1)
    return (ranks[y==1].sum()-pos*(pos+1)/2)/(pos*neg)
def cliffs(x,y):
    x=np.asarray(x,float); y=np.asarray(y,float)
    if len(x)>2500:x=rng.choice(x,2500,replace=False)
    if len(y)>2500:y=rng.choice(y,2500,replace=False)
    gt=sum(np.sum(xi>y) for xi in x); lt=sum(np.sum(xi<y) for xi in x)
    return (gt-lt)/(len(x)*len(y))
def mag(d):
    d=abs(d);return "negligible" if d<.147 else "small" if d<.33 else "medium" if d<.474 else "large"
def logistic_fit(X,y,it=100):
    X=np.asarray(X,float);y=np.asarray(y,float);n,k=X.shape;b=np.zeros(k)
    for _ in range(it):
        p=np.clip(1/(1+np.exp(-(X@b))),1e-9,1-1e-9);W=p*(1-p)
        H=(X*W[:,None]).T@X+1e-3*np.eye(k);g=X.T@(y-p)
        try:s=np.linalg.solve(H,g)
        except np.linalg.LinAlgError:break
        b=b+s
        if np.max(np.abs(s))<1e-8:break
    return b
def pred(X,b):return np.clip(1/(1+np.exp(-(np.asarray(X,float)@b))),1e-9,1-1e-9)
def cv_metrics(recs,feats,folds=5):
    y=np.array([r["y"] for r in recs]);Xr=np.array([[r[f] for f in feats] for r in recs],float)
    idx=np.arange(len(recs));rng.shuffle(idx);fid=idx%folds
    aucs=[];lls=[];ys=[];ps=[]
    for k in range(folds):
        tr=fid!=k;te=fid==k
        if y[te].sum()==0 or y[tr].sum()==0:continue
        mu=Xr[tr].mean(0);sd=Xr[tr].std(0);sd[sd==0]=1
        Xtr=np.column_stack([np.ones(tr.sum()),(Xr[tr]-mu)/sd])
        b=logistic_fit(Xtr,y[tr])
        Xte=np.column_stack([np.ones(te.sum()),(Xr[te]-mu)/sd]);pte=pred(Xte,b)
        aucs.append(auc(y[te],pte))
        lls.append(np.sum(y[te]*np.log(pte)+(1-y[te])*np.log(1-pte)))
        ys+=list(y[te]);ps+=list(pte)
    ys=np.array(ys);ps=np.array(ps)
    # F1/prec/recall at threshold = top-k where k=#positives (rank-based operating point)
    kpos=int(ys.sum());order=np.argsort(-ps);flag=np.zeros(len(ps));flag[order[:kpos]]=1
    tp=int(((flag==1)&(ys==1)).sum());fp=int(((flag==1)&(ys==0)).sum());fn=int(((flag==0)&(ys==1)).sum())
    prec=tp/(tp+fp) if tp+fp else 0;rec=tp/(tp+fn) if tp+fn else 0
    f1=2*prec*rec/(prec+rec) if prec+rec else 0
    return np.nanmean(aucs),prec,rec,f1,np.nansum(lls)

def main():
    conn=sqlite3.connect(DB);conn.row_factory=sqlite3.Row
    recs=load(conn);conn.close()
    pos=[r for r in recs if r["y"]==1];neg=[r for r in recs if r["y"]==0]
    base=len(pos)/len(recs)
    print("DELIVERABLE 2 — base rate WITHIN fresh creators")
    print(f"  FRESH migrated tokens: {len(recs)}   future-WATCHTOWER: {len(pos)}   controls: {len(neg)}")
    print(f"  P(WATCHTOWER | fresh) = {base*100:.2f}%   (vs ~1.26% unconditioned ⇒ {base/0.0126:.1f}x enriched)")

    print("\n"+"#"*72+"\n# DELIVERABLE 1+4 — FEATURE RANKING among FRESH creators\n"
          "#  (AUC dir-agnostic; Lift=precision@flagged/base; * = possible-leakage)\n"+"#"*72)
    y=np.array([r["y"] for r in recs])
    scored=[]
    for f in ALL_SAFE+REL_LEAK:
        vals=np.array([r[f] for r in recs],float)
        a=auc(y,vals);a=max(a,1-a)
        d=cliffs([r[f] for r in pos],[r[f] for r in neg])
        # binary lift: among flagged==1 what's the positive rate
        flagged=[r for r in recs if r[f]>=1] if set(np.unique(vals))<= {0,1} else None
        if flagged is not None and flagged:
            lift=(sum(1 for r in flagged if r["y"])/len(flagged))/base
            rec=sum(1 for r in flagged if r["y"])/len(pos)
        else: lift=float("nan");rec=float("nan")
        scored.append((f,a,d,lift,rec))
    for f,a,d,lift,rec in sorted(scored,key=lambda x:-x[1]):
        tag=" *" if f in REL_LEAK else ""
        lifts=f"{lift:4.1f}x" if lift==lift else "  —  "
        recs_=f"{rec*100:4.0f}%" if rec==rec else "  — "
        print(f"  {f:28s}{tag:2s} AUC={a:.3f}  δ={d:+.3f} ({mag(d)})  lift={lifts}  recall={recs_}")

    print("\n"+"#"*72+"\n# DELIVERABLE 3 — INCREMENTAL MODELS (5-fold CV, FRESH only)\n"+"#"*72)
    models=[("A  fresh-only baseline (intercept)",["liq_score"]),  # near-constant ⇒ ~base rate
            ("B  + creator+funding",CREATOR+FUNDING),
            ("C  + timing",CREATOR+FUNDING+TIMING),
            ("D  + relationship (SAFE codes)",CREATOR+FUNDING+TIMING+REL_SAFE),
            ("D' + relationship (incl. possible-leak)",CREATOR+FUNDING+TIMING+REL_SAFE+REL_LEAK)]
    prev=None
    for name,feats in models:
        a,p,r,f1,ll=cv_metrics(recs,feats)
        delta=f"  (ΔAUC {a-prev:+.3f})" if prev is not None else ""
        print(f"  {name:42s} AUC={a:.3f} prec={p*100:4.1f}% rec={r*100:4.1f}% F1={f1:.3f} ll={ll:7.1f}{delta}")
        prev=a

    print("\n"+"#"*72+"\n# DELIVERABLE 5 — RANKED EARLY-WARNING FRAMEWORK (fresh + which extra signal)\n"+"#"*72)
    top=[s for s in sorted(scored,key=lambda x:-x[1]) if s[0] not in REL_LEAK][:6]
    for i,(f,a,d,lift,rec) in enumerate(top,1):
        print(f"  {i}. {f:26s} AUC={a:.3f}  effect={mag(d)}")

if __name__=="__main__":
    main()
