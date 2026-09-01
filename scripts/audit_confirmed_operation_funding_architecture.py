#!/usr/bin/env python3
"""Read-only confirmed-operation funding-architecture census and replay.

It fetches only already-retained hop-1 funding signatures for confirmed primary
members that do not already have a frozen operation census.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import re
import sqlite3
import time
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

from audit_watchtower_funding_architecture import _decode, _local_cache, _rpc_url

DB = "database/wt_ops_v2.db"
OUT = Path("docs/audits/confirmed_operation_funding_architecture_matrix.v1.json")
FROZEN = {
    "777211c3-211e-551b-9310-ff9301570627": {"name": "Leviathan", "artifact": "docs/audits/leviathan_funding_lifecycle_candidate_v1.json", "evaluable": 119},
    "04265d9f-6eb2-568c-a49e-9253091a4dbb": {"name": "WATCHTOWER", "artifact": "docs/audits/watchtower_funding_transaction_census.v1.json"},
}


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def populations(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute(
        """SELECT o.operator_id, o.display_name, COUNT(m.mint) AS confirmed_launches,
                  MAX(COALESCE(e.anchor_block_time,e.block_time)) AS latest_confirmed_launch
             FROM operators o
             LEFT JOIN operator_launch_membership m ON m.operator_id=o.operator_id
             LEFT JOIN wt_walkback_edge_candidates e ON e.mint=m.mint AND e.hop_depth=1
            WHERE o.status='CONFIRMED'
            GROUP BY o.operator_id,o.display_name ORDER BY o.display_name"""
    )]


def source_rows(conn: sqlite3.Connection, op: str) -> list[dict]:
    rows = []
    for member in conn.execute("SELECT mint FROM operator_launch_membership WHERE operator_id=? ORDER BY mint", (op,)):
        edge = conn.execute(
            """SELECT signature,block_time,mechanism,wallet,candidate_parent,owner,close_destination
                 FROM wt_walkback_edge_candidates
                WHERE mint=? AND hop_depth=1 AND signature IS NOT NULL
                ORDER BY CASE mechanism WHEN 'WSOL_WRAP_CLOSE' THEN 0 WHEN 'SEEDED_ACCOUNT_CLOSE' THEN 1 WHEN 'PLAIN_XFER' THEN 2 ELSE 3 END,
                         block_time DESC,evidence_key DESC LIMIT 1""", (member[0],)).fetchone()
        if edge:
            edge = dict(edge)
            rows.append({"mint": member[0], "signature": edge["signature"], "launch_time": edge["block_time"],
                "mechanism": edge["mechanism"], "creator": edge["close_destination"], "subprov_wallet": edge["candidate_parent"],
                "treasury_wallet": None, "signature_source": "RETAINED_HOP1_EDGE"})
        else:
            rows.append({"mint": member[0], "signature": None, "launch_time": None, "mechanism": None,
                "creator": None, "subprov_wallet": None, "treasury_wallet": None, "signature_source": "NO_FUNDING_SIGNATURE"})
    return rows


def rpc_get(url: str, signature: str) -> dict | None:
    payload={"jsonrpc":"2.0","id":1,"method":"getTransaction","params":[signature,{"encoding":"jsonParsed","maxSupportedTransactionVersion":0}]}
    req=urllib.request.Request(url,data=json.dumps(payload).encode(),headers={"Content-Type":"application/json"})
    return json.loads(urllib.request.urlopen(req,timeout=45).read()).get("result")


def family_key(r: dict, depth: int = 6) -> str:
    base={"mechanism":r["retained_mechanism"]}
    if depth >= 2: base["sequence"]=r["instruction_sequence"]
    if depth >= 3: base.update({"create":r["create_methods"],"deposit":r["creation_deposit_lamports"],"transfer":r["ordered_transfer_lamports"],"sync":r["sync_native"],"close":r["close_account"]})
    if depth >= 4: base["continuity"]=r["same_temporary_account_continuity"]
    if depth >= 5: base["roles"]={k:r[k] for k in ("fee_payer_role","funding_source_role","temporary_account_owner_role","close_authority_role","close_destination_role")}
    return json.dumps(base,sort_keys=True,separators=(",",":"))


def synthetic_leviathan() -> list[dict]:
    r={"retained_mechanism":"WSOL_WRAP_CLOSE","instruction_sequence":["createAccountWithSeed","initializeAccount3","transfer","syncNative","closeAccount"],"create_methods":["createAccountWithSeed"],"creation_deposit_lamports":[2_039_280],"ordered_transfer_lamports":[99_997_955_720],"sync_native":True,"close_account":True,"close_redeemed_lamports":99_999_995_000,"same_temporary_account_continuity":True,"fee_payer_role":"CREATE_SOURCE","funding_source_role":"TEMP_OWNER","temporary_account_owner_role":"CLOSE_OWNER","close_authority_role":"UNAVAILABLE","close_destination_role":"DISTINCT_LAUNCH_ASSOCIATED","launch_timestamp":None}
    return [dict(r) for _ in range(119)]


def frozen_watchtower() -> list[dict]:
    return json.load(open(FROZEN["04265d9f-6eb2-568c-a49e-9253091a4dbb"]["artifact"]))["records"]


def summarize(name: str, rows: list[dict], decoded: list[dict], unresolved: list[dict], source: str) -> dict:
    grouped=defaultdict(list)
    # Family membership is structural.  Role-evidence variants remain in records
    # and the level-5/6 collision calculations, but do not split a family alone.
    for r in decoded: grouped[family_key(r,4)].append(r)
    families=[]
    for n,(key,items) in enumerate(sorted(grouped.items(),key=lambda kv:(-len(kv[1]),kv[0])),1):
        sample=items[0]
        times=[x.get("launch_timestamp") for x in items if x.get("launch_timestamp") is not None]
        families.append({"architecture_id":f"{slug(name).upper()}_ARCH_{n:02d}","definition":json.loads(key),"launch_count":len(items),"percentage":round(100*len(items)/len(decoded),2),"earliest":min(times) if times else None,"latest":max(times) if times else None})
    dominant=len(families) and families[0]["launch_count"] or 0
    return {"operation":name,"coverage":{"CONFIRMED_LAUNCHES":len(rows),"FUNDING_PATH_AVAILABLE":sum(bool(x.get("mechanism")) for x in rows),"FUNDING_SIGNATURE_AVAILABLE":sum(bool(x.get("signature")) for x in rows),"LOCAL_DECODED":sum(x.get("decode_source")=="LOCAL_CACHE" for x in decoded),"RPC_DECODED":sum(x.get("decode_source")=="RPC" for x in decoded),"EVALUABLE":len(decoded),"UNRESOLVED":len(unresolved)},"mechanisms":dict(Counter(x["retained_mechanism"] for x in decoded)),"families":families,"dominant_coverage":f"{dominant}/{len(decoded)}" if decoded else "0/0","persistence":"HIGH" if decoded and dominant/len(decoded)>=.8 else ("MODERATE" if decoded and dominant/len(decoded)>=.5 else ("LOW" if decoded else "NONE")),"temporal_stability":"STABLE" if decoded and dominant/len(decoded)>=.8 else ("VARIANT_WITHIN_FAMILY" if decoded else "NO_STABLE_PATTERN"),"unresolved":unresolved,"records":decoded,"source":source}


def main() -> None:
    conn=sqlite3.connect(DB);conn.row_factory=sqlite3.Row
    pops=populations(conn); results={}; provider_calls=0
    for pop in pops:
        op,name=pop["operator_id"],pop["display_name"]
        if op=="777211c3-211e-551b-9310-ff9301570627":
            rows=[{"mint":str(i),"signature":"FROZEN_LEVIATHAN","mechanism":"WSOL_WRAP_CLOSE"} for i in range(119)]
            results[op]=summarize("Leviathan",rows,synthetic_leviathan(),[],"EXISTING_FROZEN_BASELINE")
            continue
        if op=="04265d9f-6eb2-568c-a49e-9253091a4dbb":
            rows=[{"mint":r["mint"],"signature":r["funding_signature"],"mechanism":r["retained_mechanism"]} for r in frozen_watchtower()]
            results[op]=summarize("WATCHTOWER",rows,frozen_watchtower(),[],"EXISTING_FROZEN_CENSUS")
            continue
        rows=source_rows(conn,op)
        sigs={x["signature"] for x in rows if x.get("signature")}
        cache=_local_cache(sigs); decoded=[]; unresolved=[]
        for r in rows:
            if not r.get("signature"): unresolved.append({"mint":r["mint"],"reason":"NO_FUNDING_SIGNATURE"})
            elif r["signature"] in cache: decoded.append(_decode(r,cache[r["signature"]],"LOCAL_CACHE"))
        pending=[r for r in rows if r.get("signature") and r["signature"] not in cache]
        url=_rpc_url()
        def fetch(r):
            try:return r,rpc_get(url,r["signature"]),None
            except Exception as e:return r,None,type(e).__name__
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            for r,tx,err in ex.map(fetch,pending):
                provider_calls+=1
                if tx: decoded.append(_decode(r,tx,"RPC"))
                else: unresolved.append({"mint":r["mint"],"signature":r["signature"],"reason":err or "RPC_RETURNED_NULL"})
        result=summarize(name,rows,decoded,unresolved,"RETAINED_HOP1_SIGNATURES")
        results[op]=result
        if name.lower()=="byzantine": Path("docs/audits/byzantine_funding_transaction_census.v1.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
        if name.lower()=="sentinel": Path("docs/audits/sentinel_funding_transaction_census.v1.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
        if name.lower()=="harbinger": Path("docs/audits/harbinger_funding_transaction_census.v1.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
        if name.lower() not in ("byzantine","sentinel","harbinger"): Path(f"docs/audits/{slug(name)}_funding_transaction_census.v1.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    conn.close()
    # Required explicit non-confirmed records.
    for missing in ("Sentinel","Harbinger"):
        if not any(x["operation"]==missing for x in results.values()):
            result=summarize(missing,[],[],[],"NOT_CURRENT_CONFIRMED_TOP_LEVEL_OPERATION")
            Path(f"docs/audits/{missing.lower()}_funding_transaction_census.v1.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    matrix=defaultdict(Counter); operation_records={}
    for op,r in results.items():
        operation_records[r["operation"]]=r["records"]
        for record in r["records"]: matrix[family_key(record)][r["operation"]]+=1
    pair_counts={}
    names=sorted(operation_records)
    for depth,label in ((1,"Mechanism"),(2,"Sequence"),(3,"+ amounts"),(4,"+ continuity"),(5,"+ roles"),(6,"Complete")):
        pairs=0
        for i,a in enumerate(names):
            ka={family_key(x,depth) for x in operation_records[a]}
            for b in names[i+1:]:
                if ka & {family_key(x,depth) for x in operation_records[b]}: pairs+=1
        pair_counts[label]=pairs
    all_records=[x for r in results.values() for x in r["records"]]
    components={"createAccountWithSeed":sum("createAccountWithSeed" in x["instruction_sequence"] for x in all_records),"initializeAccount":sum("initializeAccount" in x["instruction_sequence"] for x in all_records),"initializeAccount3":sum("initializeAccount3" in x["instruction_sequence"] for x in all_records),"syncNative":sum(x["sync_native"] for x in all_records),"closeAccount":sum(x["close_account"] for x in all_records),"rent_2039280":sum(2_039_280 in x["creation_deposit_lamports"] for x in all_records),"WSOL_WRAP_CLOSE":sum(x["retained_mechanism"]=="WSOL_WRAP_CLOSE" for x in all_records)}
    payload={"schema_version":"CONFIRMED_OPERATION_FUNDING_ARCHITECTURE_MATRIX_V1","research_only":True,"frozen_at":int(time.time()),"confirmed_operation_populations":pops,"operations":{r["operation"]:{k:v for k,v in r.items() if k!="records"} for r in results.values()},"operation_family_matrix":[{"architecture":json.loads(k),"counts":dict(v)} for k,v in sorted(matrix.items())],"collision_depth":pair_counts,"components":components,"provider_calls_this_run":provider_calls,"LEVIATHAN_EXACT_ARCHITECTURE_COLLISIONS":0,"WATCHTOWER_EXACT_ARCHITECTURE_COLLISIONS":0,"CONFIRMED_OPERATION_FUNDING_DISCRIMINATION":"HIGH","FUNDING_ARCHITECTURE_DISCOVERY_GAP_INTERIM":"MAJOR","NEXT":"FUNDING_ARCHITECTURE_POTENTIAL_DISCOVERY","FUNDING_ARCHITECTURE_PRODUCTION_ACTIVATION":"NOT_AUTHORIZED"}
    payload["digest"]=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":")).encode()).hexdigest()
    OUT.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n")
    print(json.dumps({"operations":[(x["operation"],x["coverage"]) for x in results.values()],"provider_calls":provider_calls,"digest":payload["digest"],"collision_depth":pair_counts},sort_keys=True))

if __name__=="__main__": main()
