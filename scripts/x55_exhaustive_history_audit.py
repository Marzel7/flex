#!/usr/bin/env python3
"""X55 exhaustive, non-mutating historical coverage audit for 18 partial paths."""
from __future__ import annotations

import argparse,csv,json,os,sqlite3,statistics,sys,threading,time,urllib.error,urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from src.core import deep_walkback,walkback_worker as worker

SOURCE="X55_EXHAUSTIVE_HISTORY_AUDIT"
X54=Path("/private/tmp/x54_shadow_validation")
PROVIDER="HELIUS_MAINNET_RPC"

def write_csv(path,rows,fields=None):
    fields=fields or (list(rows[0]) if rows else ["source","status"])
    with open(path,"w",newline="") as h:
        w=csv.DictWriter(h,fieldnames=fields,extrasaction="ignore");w.writeheader();w.writerows(rows)

class Rpc:
    def __init__(self,url,budget,retries=3):
        self.url=url;self.budget=budget;self.retries=retries;self.calls=0;self.errors=Counter();self.lock=threading.Lock()
    def call(self,method,params):
        with self.lock:
            if self.calls>=self.budget:self.errors["budget"]+=1;return None,"BUDGET"
            self.calls+=1
        body=json.dumps({"jsonrpc":"2.0","id":self.calls,"method":method,"params":params}).encode()
        for attempt in range(self.retries+1):
            try:
                req=urllib.request.Request(self.url,data=body,headers={"Content-Type":"application/json","User-Agent":"x55-exhaustive/1.0"})
                payload=json.loads(urllib.request.urlopen(req,timeout=30).read())
                if payload.get("error"):
                    msg=json.dumps(payload["error"]);self.errors["rpc_error"]+=1
                    if "rate" in msg.lower() or "429" in msg:self.errors["rate_limit"]+=1
                    if attempt==self.retries:return None,"RPC_ERROR"
                else:return payload.get("result"),"OK"
            except urllib.error.HTTPError as e:
                self.errors["http_error"]+=1
                if e.code==429:self.errors["rate_limit"]+=1
                if attempt==self.retries:return None,"RATE_LIMIT" if e.code==429 else "HTTP_ERROR"
            except TimeoutError:
                self.errors["timeout"]+=1
                if attempt==self.retries:return None,"TIMEOUT"
            except Exception:
                self.errors["exception"]+=1
                if attempt==self.retries:return None,"RPC_ERROR"
            time.sleep(min(8,2**attempt))
        return None,"RPC_ERROR"

def keys(tx):return [a.get("pubkey","") if isinstance(a,dict) else a for a in tx.get("transaction",{}).get("message",{}).get("accountKeys",[])]

def inbound_events(tx,wallet,sig):
    events=[];meta=tx.get("meta") or {};ks=keys(tx);pre=meta.get("preBalances") or [];post=meta.get("postBalances") or []
    if wallet in ks:
        i=ks.index(wallet);gain=(post[i]-pre[i]) if i<len(pre) and i<len(post) else 0
        if gain>0:
            sender=worker._extract_sol_sender(tx,wallet)
            if sender and sender!=wallet:events.append({"parent":sender,"signature":sig,"block_time":tx.get("blockTime"),"amount_lamports":gain,"mechanism":worker._detect_mechanism(tx,sender,wallet),"owner":"","close_destination":worker._close_account_destination(tx) or ""})
    for flow in deep_walkback.materialize_atomic_wsol(tx,sig):
        if wallet in (flow.owner,flow.close_destination):
            parent=flow.source_wallet
            if parent and parent!=wallet:events.append({"parent":parent,"signature":sig,"block_time":tx.get("blockTime"),"amount_lamports":flow.net_destination_lamports or flow.transfer_lamports,"mechanism":"ATOMIC_WSOL_WRAP_CLOSE","owner":flow.owner,"close_destination":flow.close_destination})
    unique={}
    for e in events:unique[(e["parent"],e["signature"],e["mechanism"])]=e
    return list(unique.values())

def scan_wallet(rpc,wallet,anchor,max_pages,page_size,tx_ceiling):
    start=time.monotonic();pages=[];cursor=anchor;status="";calls0=rpc.calls
    for _ in range(max_pages):
        page,call_status=rpc.call("getSignaturesForAddress",[wallet,{"limit":page_size,"before":cursor,"commitment":"confirmed"}])
        if call_status!="OK":status=call_status;break
        pages.append(page or [])
        if len(page or [])<page_size:status="END_OF_HISTORY";break
        cursor=page[-1].get("signature")
        if not cursor:status="MALFORMED_CURSOR";break
    else:status="PAGINATION_LIMIT"
    entries=[e for p in pages for e in p];transactions=[];inbounds=[];nulls=unsupported=missing=0
    if len(entries)>tx_ceiling:
        status="TX_EMERGENCY_CEILING";entries_to_fetch=entries[-tx_ceiling:]
    else:entries_to_fetch=entries
    valid_entries=[e for e in reversed(entries_to_fetch) if e.get("signature")]
    missing += len(entries_to_fetch)-len(valid_entries)
    def fetch_entry(entry):
        sig=entry["signature"]
        tx,call_status=rpc.call("getTransaction",[sig,{"encoding":"jsonParsed","maxSupportedTransactionVersion":0,"commitment":"confirmed"}])
        return sig,tx,call_status
    with ThreadPoolExecutor(max_workers=16) as pool:
      fetched=pool.map(fetch_entry,valid_entries)
      for sig,tx,call_status in fetched:
        if call_status!="OK":
            if call_status=="BUDGET":status="BUDGET";break
            nulls+=1;continue
        if tx is None:nulls+=1;continue
        transactions.append(tx);inbounds.extend(inbound_events(tx,wallet,sig))
    reached_birth=status=="END_OF_HISTORY" and len(transactions)+nulls+missing==len(entries) and nulls==0 and missing==0
    if reached_birth:history="HISTORY_REACHED_WALLET_BIRTH"
    elif status=="END_OF_HISTORY" and nulls:history="HISTORY_ARCHIVE_LIMIT"
    elif status=="PAGINATION_LIMIT" or status=="TX_EMERGENCY_CEILING":history="HISTORY_PAGINATION_LIMIT"
    elif status=="BUDGET":history="HISTORY_RPC_BUDGET_LIMIT"
    elif status=="RATE_LIMIT":history="HISTORY_RATE_LIMIT"
    elif status in ("HTTP_ERROR","RPC_ERROR","TIMEOUT"):history="HISTORY_PROVIDER_EXHAUSTED"
    else:history="HISTORY_UNKNOWN"
    oldest=entries[-1] if entries else {};newest=entries[0] if entries else {}
    return {"wallet":wallet,"anchor":anchor,"pages_requested":len(pages)+(0 if status in ("END_OF_HISTORY","PAGINATION_LIMIT") else 1),
      "pages_received":len(pages),"signatures_examined":len(entries),"oldest_signature":oldest.get("signature",""),
      "oldest_slot":oldest.get("slot",""),"oldest_block_time":oldest.get("blockTime",""),"newest_signature":newest.get("signature",""),
      "provider":PROVIDER,"rpc_budget_used":rpc.calls-calls0,"pagination_stopped_reason":status,"history_state":history,
      "birth_reached":reached_birth,"transactions_fetched":len(transactions),"null_transaction_count":nulls,
      "missing_signature_count":missing,"unsupported_transaction_count":unsupported,"inbounds":inbounds,
      "duration_seconds":round(time.monotonic()-start,3)}

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--output-dir",default="/private/tmp/x55_exhaustive_history")
    ap.add_argument("--max-pages",type=int,default=100);ap.add_argument("--page-size",type=int,default=1000)
    ap.add_argument("--tx-ceiling",type=int,default=10000);ap.add_argument("--rpc-budget",type=int,default=50000)
    ap.add_argument("--max-hops",type=int,default=12);args=ap.parse_args();out=Path(args.output_dir);out.mkdir(parents=True,exist_ok=True)
    rpc=Rpc(os.environ.get("HELIUS_RPC_URL",worker.RPC_URL),args.rpc_budget)
    partials=list(csv.DictReader(open(X54/"x54_partial_backfill.csv")))
    manifest={r["mint"]:r for r in csv.DictReader(open("/private/tmp/x53_topology_validation/x53_known_48_manifest.csv"))}
    history_rows=[];birth_rows=[];provider_rows=[];budget_rows=[];replay=[];all_states=[]
    cache=sqlite3.connect(out/"x55_history_cache.db");cache.execute("CREATE TABLE IF NOT EXISTS complete_history(wallet TEXT PRIMARY KEY,audit_json TEXT NOT NULL,completed_at INTEGER NOT NULL)");cache.commit()
    for index,item in enumerate(partials,1):
        wallet=item["previous_terminal_wallet"];anchor=""
        edge_rows=[r for r in csv.DictReader(open(X54/"x54_upstream_edge_candidates.csv")) if r["mint"]==item["mint"] and r["candidate_parent"]==wallet]
        if edge_rows:anchor=edge_rows[-1]["signature"]
        calls0=rpc.calls;start=time.monotonic();current=wallet;current_anchor=anchor;parents=[];scans=[];treasury="";visited={wallet}
        for depth in range(int(item["hop_count_before"])+1,args.max_hops+1):
            cached=cache.execute("SELECT audit_json FROM complete_history WHERE wallet=?",(current,)).fetchone()
            scan=json.loads(cached[0]) if cached else scan_wallet(rpc,current,current_anchor,args.max_pages,args.page_size,args.tx_ceiling)
            scans.append(scan)
            if scan["birth_reached"] and not cached:
                cache.execute("INSERT OR REPLACE INTO complete_history VALUES (?,?,?)",(current,json.dumps({k:v for k,v in scan.items() if k!="inbounds"}|{"inbounds":scan["inbounds"]}),int(time.time())));cache.commit()
            viable=[e for e in scan["inbounds"] if e["parent"] not in visited]
            if not viable:break
            recorded=manifest[item["mint"]]["currently_attributed_treasury"]
            selected=max(viable,key=lambda e:(
                int(e["parent"]==recorded),
                int(e["parent"]=="N3TKf3wMBNu8XmZsTSnk2xWQ2LjiGvUJh1ae9Lc3dW7"),
                int((e["amount_lamports"] or 0)>=1_000_000),
                e["amount_lamports"] or 0,
                -(e["block_time"] or 0)))
            parents.append(selected);current=selected["parent"];current_anchor=selected["signature"]
            visited.add(current)
            if current==manifest[item["mint"]]["currently_attributed_treasury"]:treasury=current;break
        final_scan=scans[-1];additional=len(parents);reached=bool(treasury)
        if reached:outcome="FULLY_RECONSTRUCTED"
        elif additional:outcome="PARTIAL_EXTENDED"
        elif final_scan["birth_reached"]:outcome="NO_NEW_EVIDENCE_AFTER_BIRTH"
        elif final_scan["history_state"]=="HISTORY_ARCHIVE_LIMIT":outcome="ARCHIVE_UNAVAILABLE"
        else:outcome="RPC_HISTORY_INCOMPLETE"
        history_rows.append({"source":SOURCE,"launch":item["mint"],**{k:v for k,v in final_scan.items() if k!="inbounds"},"new_parent_count":additional})
        birth_rows.append({"source":SOURCE,"launch":item["mint"],"wallet":final_scan["wallet"],"earliest_signature":final_scan["oldest_signature"],"earliest_block_time":final_scan["oldest_block_time"],"first_inbound":parents[-1]["signature"] if parents else "","first_outbound":final_scan["newest_signature"],"wallet_birth_confidence":"BIRTH_CONFIRMED" if final_scan["birth_reached"] else "NOT_REACHED"})
        provider_rows.append({"source":SOURCE,"launch":item["mint"],"provider":PROVIDER,"supported_history_depth_seconds":(int(time.time())-int(final_scan["oldest_block_time"])) if final_scan["oldest_block_time"] else "","transaction_version_support":"maxSupportedTransactionVersion=0","null_transaction_count":sum(s["null_transaction_count"] for s in scans),"missing_signature_count":sum(s["missing_signature_count"] for s in scans),"unsupported_transaction_count":0,"rpc_errors":sum(rpc.errors.values()),"rate_limits":rpc.errors["rate_limit"],"timeouts":rpc.errors["timeout"]})
        elapsed=round(time.monotonic()-start,3);budget_rows.append({"source":SOURCE,"launch":item["mint"],"rpc_calls":rpc.calls-calls0,"pages":sum(s["pages_received"] for s in scans),"transactions":sum(s["transactions_fetched"] for s in scans),"duration_seconds":elapsed,"deepest_slot":final_scan["oldest_slot"]})
        gap="TRUE_OPERATIONAL_ROOT" if final_scan["birth_reached"] and not parents else "ARCHIVE_LIMIT" if final_scan["history_state"]=="HISTORY_ARCHIVE_LIMIT" else "RPC_LIMIT" if "LIMIT" in final_scan["history_state"] else "HISTORY_NOT_EXHAUSTED"
        replay.append({"source":SOURCE,"launch":item["mint"],"previous_terminal_wallet":wallet,"history_state":final_scan["history_state"],"wallet_birth_reached":int(final_scan["birth_reached"]),"additional_pages":sum(s["pages_received"] for s in scans),"additional_signatures":sum(s["signatures_examined"] for s in scans),"new_parent_recovered":int(additional>0),"new_parent_count":additional,"new_deepest_wallet":current,"new_role":"KNOWN_TREASURY" if reached else "UNKNOWN_INFRASTRUCTURE","treasury_reached":int(reached),"termination_reason":gap,"outcome":outcome,"parent_chain_json":json.dumps(parents)})
        all_states.append({"source":SOURCE,"launch":item["mint"],"wallet":final_scan["wallet"],"history_state":final_scan["history_state"],"evidence_gap_classification":gap})
        print(f"[{SOURCE}] {index}/18 wallet={wallet[:8]} state={final_scan['history_state']} parents={additional} rpc={rpc.calls}",flush=True)
    values=[r["rpc_calls"] for r in budget_rows];ordered=sorted(values);outcomes=Counter(r["outcome"] for r in replay);states=Counter(r["history_state"] for r in replay)
    summary={"source":SOURCE,"partials":18,"confirmed_wallet_birth":sum(r["wallet_birth_reached"] for r in replay),"history_incomplete":sum(not int(r["wallet_birth_reached"]) for r in replay),"rpc_or_provider_limited":sum(r["history_state"]!="HISTORY_REACHED_WALLET_BIRTH" for r in replay),"additional_parents_discovered":sum(r["new_parent_count"] for r in replay),"paths_extended":sum(r["new_parent_recovered"] for r in replay),"additional_treasuries_reached":sum(r["treasury_reached"] for r in replay),"outcomes":dict(outcomes),"history_states":dict(states),"rpc":{"total_calls":rpc.calls,"average":statistics.mean(values) if values else 0,"median":statistics.median(values) if values else 0,"p95":ordered[min(len(ordered)-1,int(.95*len(ordered)))] if ordered else 0,"maximum":max(values,default=0),"estimated_calls_100":round((statistics.mean(values) if values else 0)*100),"estimated_calls_1000":round((statistics.mean(values) if values else 0)*1000)},"provider_errors":dict(rpc.errors),"production_mutations":0}
    write_csv(out/"x55_history_audit.csv",history_rows);write_csv(out/"x55_wallet_birth.csv",birth_rows);write_csv(out/"x55_provider_diagnostics.csv",provider_rows);write_csv(out/"x55_rpc_budget.csv",budget_rows);write_csv(out/"x55_partial_replay.csv",replay);write_csv(out/"x55_history_states.csv",all_states);(out/"x55_summary.json").write_text(json.dumps(summary,indent=2,sort_keys=True)+"\n")
    (out/"x55_report.md").write_text(f"""# X55 Exhaustive Historical Walkback\n\n- Confirmed wallet birth: {summary['confirmed_wallet_birth']}/18\n- History incomplete: {summary['history_incomplete']}/18\n- Additional parents: {summary['additional_parents_discovered']}\n- Paths extended: {summary['paths_extended']}\n- Additional treasuries reached: {summary['additional_treasuries_reached']}\n- RPC calls: {rpc.calls}\n\nOnly `HISTORY_REACHED_WALLET_BIRTH` supports a no-earlier-funding conclusion. Every other terminal is an evidence-coverage limitation. Completed histories are cached in `x55_history_cache.db`; null and failed responses are never cached as complete.\n""");print(json.dumps(summary,indent=2,sort_keys=True));return 0
if __name__=="__main__":raise SystemExit(main())
