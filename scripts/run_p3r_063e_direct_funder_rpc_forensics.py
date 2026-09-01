#!/usr/bin/env python3
"""Bounded, launch-linked four-funder RPC study for frozen P3R v2 063e.

The only history read is one `getSignaturesForAddress` page per canonical direct
funder.  For a launch, its candidate upstream is deterministically the ledger
entry immediately older than that launch's selected funding transaction.  A
candidate is accepted only when its parsed native balance delta into the funder
is positive.  No address history is followed recursively.
"""
from __future__ import annotations
import collections, hashlib, json, os, re, sqlite3, time, urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
RUN='p3r-v2-2dec1d40604c1f7c08c8'; TARGET='p3r-v2-063e24a2def354f23ec5'
FORENSIC=ROOT/'docs/agent_handoff/p3r/v2'/RUN/'063e_forensic/p3r-v2-063e-forensic-v1/p3r_v2_063e_forensic_operation_investigation.v1.json'
OUT=ROOT/'docs/agent_handoff/p3r/v2'/RUN/'063e_direct_funder_rpc_forensics/p3r-v2-063e-direct-funder-rpc-v1'
EDGE_HW=60299

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def iso(ts): return datetime.fromtimestamp(ts,timezone.utc).isoformat() if ts else None
def rpc_url():
    text=(ROOT/'.env').read_text()
    match=re.search(r'^(?:export\s+)?(?:HELIUS_RPC_URL|SOLANA_RPC_URL)=["\']?([^"\'\n]+)',text,re.M)
    if not match: raise RuntimeError('No Helius/Solana RPC URL configured in .env')
    return match.group(1)
def call(url,method,params,calls):
    request=urllib.request.Request(url,data=json.dumps({'jsonrpc':'2.0','id':len(calls)+1,'method':method,'params':params}).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=30) as response: payload=json.loads(response.read())
    calls.append({'method':method,'params_summary':str(params[0])[:64] if params else None})
    if payload.get('error'): raise RuntimeError(f"{method}: {payload['error']}")
    return payload.get('result')
def account_key(k): return k.get('pubkey') if isinstance(k,dict) else k
def tx_summary(tx,funder):
    if not tx: return {'observable':False}
    keys=[account_key(k) for k in tx['transaction']['message']['accountKeys']]
    try: idx=keys.index(funder)
    except ValueError: return {'observable':False,'reason':'FUNDER_NOT_IN_ACCOUNT_KEYS'}
    meta=tx.get('meta') or {}; pre=(meta.get('preBalances') or [])[idx]; post=(meta.get('postBalances') or [])[idx]
    delta=post-pre
    negatives=[]
    for i,(a,b) in enumerate(zip(meta.get('preBalances') or [],meta.get('postBalances') or [])):
        if b-a<0: negatives.append((a-b,keys[i]))
    source=max(negatives,default=(0,None))[1]
    msg=tx['transaction']['message']; instructions=msg.get('instructions') or []
    programs=sorted({i.get('programId') or i.get('program') or 'UNKNOWN' for i in instructions if isinstance(i,dict)})
    return {'observable':True,'native_delta_lamports':delta,'upstream_source':source,'program_ids':programs,'instruction_count':len(instructions),'signers':[account_key(k) for k in tx['transaction']['message']['accountKeys'] if isinstance(k,dict) and k.get('signer')]}
def main():
    report=json.loads(FORENSIC.read_text()); members=report['member_forensics']
    by_funder=collections.defaultdict(list)
    for m in members: by_funder[m['direct_funder']].append(m)
    if len(by_funder)!=4: raise RuntimeError(f'Expected four frozen funders, got {len(by_funder)}')
    url=rpc_url(); calls=[]; histories={}
    for funder in sorted(by_funder):
        histories[funder]=call(url,'getSignaturesForAddress',[funder,{'limit':1000}],calls) or []
    lookup={f:{x['signature']:i for i,x in enumerate(rows)} for f,rows in histories.items()}
    candidates={}; launch_links=[]
    for m in sorted(members,key=lambda x:x['mint']):
        rows=histories[m['direct_funder']]; idx=lookup[m['direct_funder']].get(m['selected_signature'])
        prev=rows[idx+1] if idx is not None and idx+1<len(rows) else None
        if prev: candidates[(m['direct_funder'],prev['signature'])]=prev
        launch_links.append({'mint':m['mint'],'direct_funder':m['direct_funder'],'launch_signature':m['selected_signature'],'launch_time':m['launch_timestamp'],'history_position':idx,'candidate_upstream_signature':prev['signature'] if prev else None})
    txs={}
    for key,row in sorted(candidates.items()): txs[key]=tx_summary(call(url,'getTransaction',[row['signature'],{'encoding':'jsonParsed','maxSupportedTransactionVersion':0}],calls),key[0])
    for link in launch_links:
        key=(link['direct_funder'],link['candidate_upstream_signature']) if link['candidate_upstream_signature'] else None
        info=txs.get(key,{})
        link.update({'upstream_observable':info.get('observable',False),'raw_amount_lamports':info.get('native_delta_lamports'),'upstream_source':info.get('upstream_source'),'upstream_program_ids':info.get('program_ids',[]),'upstream_instruction_count':info.get('instruction_count'),'qualifying_material_inbound':bool(info.get('native_delta_lamports',0)>0),'slot_time_delta_seconds':None})
        if link['candidate_upstream_signature']:
            row=candidates[key]; link['slot_time_delta_seconds']=(link['launch_time'] or 0)-(row.get('blockTime') or 0)
    resolved=[x for x in launch_links if x['qualifying_material_inbound']]
    profiles=[]
    for funder, ms in sorted(by_funder.items()):
        links=[x for x in launch_links if x['direct_funder']==funder]; good=[x for x in links if x['qualifying_material_inbound']]
        amounts=collections.Counter(x['raw_amount_lamports'] for x in good); sources=collections.Counter(x['upstream_source'] for x in good)
        profiles.append({'direct_funder':funder,'members':len(ms),'share':len(ms)/len(members),'first_observed':iso(min(x['launch_timestamp'] for x in ms)),'last_observed':iso(max(x['launch_timestamp'] for x in ms)),'history_entries':len(histories[funder]),'launches_with_candidate':sum(x['candidate_upstream_signature'] is not None for x in links),'material_inbound_resolved':len(good),'inbound_amounts':[{'lamports':k,'count':v} for k,v in amounts.most_common()],'upstream_provisioners':[{'address':k,'count':v} for k,v in sources.most_common()],'median_replenishment_to_launch_seconds':sorted([x['slot_time_delta_seconds'] for x in good if x['slot_time_delta_seconds'] is not None])[len(good)//2] if good else None})
    source_sets={p['direct_funder']:{x['address'] for x in p['upstream_provisioners'] if x['address']} for p in profiles}
    shared=sorted(set.intersection(*source_sets.values())) if all(source_sets.values()) else []
    conn=sqlite3.connect(ROOT/'database/wt_ops_v2.db'); conn.row_factory=sqlite3.Row
    try:
        fp_mints=report['detectors']['H0_behaviour_only']['external_mints']; ph=','.join('?' for _ in fp_mints)
        fps=conn.execute(f"SELECT mint,candidate_parent FROM wt_walkback_edge_candidates WHERE rowid<=? AND selection_status='SELECTED' AND mint IN ({ph})",(EDGE_HW,*fp_mints)).fetchall()
        fp_overlap=sorted({r['candidate_parent'] for r in fps}&set(by_funder))
    finally: conn.close()
    local_cross=report['cross_family_infrastructure']['063e_900b_c357']
    any_shared=any(source_sets[a] & source_sets[b] for a in source_sets for b in source_sets if a < b)
    infrastructure_class='STRONGLY_SHARED_PROVISIONING_INFRASTRUCTURE' if shared else ('PARTIALLY_SHARED_INFRASTRUCTURE' if any_shared else ('INDEPENDENT_FUNDER_INFRASTRUCTURE' if any(source_sets.values()) else 'INSUFFICIENT_EVIDENCE'))
    impact='063E_PROVISIONAL_REGISTRATION_STRENGTHENED' if shared and not fp_overlap else '063E_PROVISIONAL_REGISTRATION_UNCHANGED'
    result={'schema_version':'P3R_V2_063E_DIRECT_FUNDER_RPC_FORENSICS.v1','candidate_id':TARGET,'canonical_run_id':RUN,'frozen_direct_funders':profiles,'predeclared_selection_rule':'Immediate ledger entry older than the direct funder launch transaction; accept only positive native balance delta into the funder.','rpc_request_manifest':{'calls':calls,'getSignaturesForAddress_calls':4,'getTransaction_calls':len(txs),'total_calls':len(calls),'history_page_limit':1000,'recursive_history_calls':0},'launch_linked_upstream_transactions':launch_links,'cross_funder_infrastructure':{'shared_upstream_provisioners_all_four':shared,'classification':infrastructure_class,'note':'Shared provisioners are infrastructure evidence, not controller identity.'},'recurrent_vs_singleton':{'recurrent_funders':[p['direct_funder'] for p in profiles if p['members']>=2],'low_frequency_funders':[p['direct_funder'] for p in profiles if p['members']<2]},'cross_family_local_overlap':local_cross,'false_positive_infrastructure':{'false_positive_count':len(fp_mints),'shared_direct_funders':fp_overlap,'note':'No false-positive address histories were queried.'},'address_independent_features':[{'feature':'exact hop-1 WSOL_WRAP_CLOSE 9,999,985,000 lamports plus dominant four-step lifecycle','classification':'ADDRESS_INDEPENDENT'},{'feature':'recurrent direct funder condition','classification':'ADDRESS_DEPENDENT'}],'infrastructure_verdict':'063E_FUNDER_INFRASTRUCTURE_DOES_NOT_BROADEN_H1' if resolved else '063E_FUNDER_INFRASTRUCTURE_UNRESOLVED','registration_impact':impact,'remaining_uncertainty':'Immediate-predecessor ledger entries may not expose a material inbound transaction for every launch; no recursive or broader history was used.','next_action':'If separately authorized, register provisionally using the existing H1 recurrent-funder rule only; exclude singleton funders from automatic attribution.'}
    OUT.mkdir(parents=True,exist_ok=True); rp=OUT/'p3r_v2_063e_direct_funder_rpc_forensics.v1.json'; rp.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
    manifest={'schema_version':'P3R_V2_063E_DIRECT_FUNDER_RPC_FORENSICS_MANIFEST.v1','report':str(rp.relative_to(ROOT)),'report_sha256':sha(rp),'source_forensic_sha256':sha(FORENSIC),'provider_call_count':len(calls),'provider_methods':collections.Counter(x['method'] for x in calls),'selection_rule':result['predeclared_selection_rule']}; mp=OUT/'p3r_v2_063e_direct_funder_rpc_forensics_manifest.v1.json'; mp.write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
    conn=sqlite3.connect(ROOT/'database/wt_ops_v2.db')
    try:
        conn.execute("UPDATE potential_operation_workflows SET latest_verdict=?, principal_gap=?, next_action=?, last_investigated_at=?, updated_at=? WHERE candidate_id=?",(result['infrastructure_verdict'],result['remaining_uncertainty'],result['next_action'],int(time.time()),int(time.time()),TARGET));conn.commit()
    finally: conn.close()
    print(json.dumps({'report':str(rp),'manifest':str(mp),'calls':len(calls),'resolved':len(resolved),'verdict':result['infrastructure_verdict'],'impact':impact},sort_keys=True))
if __name__=='__main__': main()
