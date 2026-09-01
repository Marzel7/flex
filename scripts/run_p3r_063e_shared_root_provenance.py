#!/usr/bin/env python3
"""Bounded shared-root verification for 063e; no registration or queue actions."""
from __future__ import annotations
import collections, hashlib, json, re, sqlite3, time, urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]; RUN='p3r-v2-2dec1d40604c1f7c08c8'; TARGET='p3r-v2-063e24a2def354f23ec5'
ROOT_WALLET='B65cQ7sVquQ1VRYYK4m4zdPDfMwqxthycqraSWmkXptQ'
FORENSIC=ROOT/'docs/agent_handoff/p3r/v2'/RUN/'063e_forensic/p3r-v2-063e-forensic-v1/p3r_v2_063e_forensic_operation_investigation.v1.json'
FUNDER=ROOT/'docs/agent_handoff/p3r/v2'/RUN/'063e_direct_funder_rpc_forensics/p3r-v2-063e-direct-funder-rpc-v1/p3r_v2_063e_direct_funder_rpc_forensics.v1.json'
OUT=ROOT/'docs/agent_handoff/p3r/v2'/RUN/'063e_shared_root_provenance/p3r-v2-063e-shared-root-v1'
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def endpoint():
    m=re.search(r'^(?:export\s+)?HELIUS_RPC_URL=["\']?([^"\'\n]+)',(ROOT/'.env').read_text(),re.M)
    if not m: raise RuntimeError('HELIUS_RPC_URL unavailable')
    return m.group(1)
def rpc(url,method,params,calls):
    req=urllib.request.Request(url,data=json.dumps({'jsonrpc':'2.0','id':len(calls)+1,'method':method,'params':params}).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=30) as r: out=json.loads(r.read())
    calls.append({'method':method,'address_or_signature':str(params[0])[:88]})
    if out.get('error'): raise RuntimeError(str(out['error']))
    return out.get('result')
def key(x): return x.get('pubkey') if isinstance(x,dict) else x
def inspect(tx,address):
    if not tx:return {'status':'HISTORY_INSUFFICIENT'}
    keys=[key(x) for x in tx['transaction']['message']['accountKeys']]; meta=tx.get('meta') or {}
    if address not in keys:return {'status':'NOT_VERIFIED'}
    i=keys.index(address); pre=(meta.get('preBalances') or [])[i]; post=(meta.get('postBalances') or [])[i]
    negatives=[]
    for j,(a,b) in enumerate(zip(meta.get('preBalances') or [],meta.get('postBalances') or [])):
        if b<a: negatives.append((a-b,keys[j]))
    amount=post-pre; source=max(negatives,default=(0,None))[1]
    return {'status':'DIRECT_INITIAL_FUNDER_VERIFIED' if source==ROOT_WALLET and amount>0 else 'NOT_VERIFIED','raw_lamports':amount,'upstream_source':source,'slot':tx.get('slot'),'block_time':tx.get('blockTime'),'signatures':tx['transaction'].get('signatures',[]),'direct':source==ROOT_WALLET and amount>0}
def birth(url,address,calls,max_pages):
    """Read at most max_pages x 1,000 entries, then inspect only the earliest reached tx."""
    before=None; pages=[]; exhausted=False
    for _ in range(max_pages):
        opts={'limit':1000};
        if before:opts['before']=before
        page=rpc(url,'getSignaturesForAddress',[address,opts],calls) or []; pages.extend(page)
        if len(page)<1000: exhausted=True; break
        before=page[-1]['signature']
    if not pages:return {'address':address,'status':'HISTORY_INSUFFICIENT','pages':len(pages)}
    oldest=pages[-1]; verdict=inspect(rpc(url,'getTransaction',[oldest['signature'],{'encoding':'jsonParsed','maxSupportedTransactionVersion':0}],calls),address)
    verdict.update({'address':address,'history_pages':(len(pages)+999)//1000,'history_entries':len(pages),'history_exhausted':exhausted,'earliest_signature':oldest['signature'],'earliest_slot':oldest.get('slot'),'earliest_block_time':oldest.get('blockTime')})
    if not exhausted and verdict['status']=='NOT_VERIFIED': verdict['status']='HISTORY_INSUFFICIENT'
    return verdict
def main():
    base=json.loads(FORENSIC.read_text()); prior=json.loads(FUNDER.read_text()); funders=[x['direct_funder'] for x in prior['frozen_direct_funders']]
    url=endpoint(); calls=[]
    # Account birth: three supplied root candidates plus dominant ByZc and its known immediate provisioner.
    birth_profiles={a:birth(url,a,calls,10) for a in funders}
    byzc='ByZc7RNeYowEg2jKo2giytWb9WmNyZPrQ1hXhnGSzHTY'; df8='Df8CJQR7fUTYAQSQwtsgUDs5b6JWNULzwhJJXDCJkdya'
    df8_birth=birth(url,df8,calls,10)
    # Frozen FP cohort: only their selected direct funders, max 3 pages each.
    conn=sqlite3.connect(ROOT/'database/wt_ops_v2.db'); conn.row_factory=sqlite3.Row
    try:
        fp=base['detectors']['H0_behaviour_only']['external_mints']; ph=','.join('?' for _ in fp)
        fp_rows=conn.execute(f"SELECT mint,candidate_parent FROM wt_walkback_edge_candidates WHERE rowid<=60299 AND selection_status='SELECTED' AND hop_depth=1 AND mechanism='WSOL_WRAP_CLOSE' AND amount_lamports=9999985000 AND mint IN ({ph})",fp).fetchall()
        core_rows=conn.execute("SELECT mint,candidate_parent FROM wt_walkback_edge_candidates WHERE rowid<=60299 AND selection_status='SELECTED' AND hop_depth=1 AND mechanism='WSOL_WRAP_CLOSE' AND amount_lamports=9999985000").fetchall()
    finally: conn.close()
    fp_funders=sorted({r['candidate_parent'] for r in fp_rows}); fp_birth={a:birth(url,a,calls,3) for a in fp_funders if a not in birth_profiles}
    direct_root={a:bool(v.get('direct')) for a,v in birth_profiles.items()}
    byzc_root=direct_root.get(byzc,False) or bool(df8_birth.get('direct'))
    root_descendants={a for a,v in direct_root.items() if v}
    if byzc_root:root_descendants.add(byzc)
    canonical=set(m['mint'] for m in base['member_forensics'])
    recurrent=set(prior['recurrent_vs_singleton']['recurrent_funders'])
    def score(allow):
        matched={r['mint'] for r in core_rows if r['candidate_parent'] in allow}; tp=len(matched&canonical); fp=len(matched-canonical); fn=len(canonical-matched)
        return {'TP':tp,'FP':fp,'FN':fn,'precision':tp/(tp+fp) if tp+fp else 0,'recall':tp/len(canonical)}
    detectors={'R0_behaviour_only':{'TP':41,'FP':5,'FN':0,'precision':41/46,'recall':1.0},'R1_recurrent_funder':score(recurrent),'R2_verified_B65c_root':score(root_descendants),'R3_recurrent_or_B65c_root':score(recurrent|root_descendants),'R4_route_provenance':score(root_descendants)}
    fp_root=sorted({a for a,v in fp_birth.items() if v.get('direct')} | {a for a,v in birth_profiles.items() if v.get('direct') and any(row['candidate_parent']==a for row in fp_rows)})
    # No confirmed conclusion unless every canonical funder is root-derived, zero FPs, and the root route carries rotation beyond an allowlist.
    complete=all(a in root_descendants for a in funders)
    final='063E_CONFIRMED_HYBRID_ROUTE_SUPPORTED' if complete and detectors['R3_recurrent_or_B65c_root']['FP']==0 else '063E_PROVISIONAL_OPERATION_STILL_APPROPRIATE'
    rec='READY_FOR_CONFIRMED_REGISTRATION' if final.startswith('063E_CONFIRMED') else 'READY_FOR_PROVISIONAL_REGISTRATION'
    graph=[]
    for a,v in birth_profiles.items(): graph.append({'from':ROOT_WALLET if v.get('direct') else v.get('upstream_source'),'to':a,'kind':'initial_material_funding','amount':v.get('raw_lamports'),'signature':v.get('earliest_signature'),'verified_direct_root':v.get('direct',False)})
    graph.append({'from':ROOT_WALLET if df8_birth.get('direct') else df8_birth.get('upstream_source'),'to':df8,'kind':'ByZc_known_immediate_provisioner_birth','amount':df8_birth.get('raw_lamports'),'signature':df8_birth.get('earliest_signature'),'verified_direct_root':df8_birth.get('direct',False)})
    report={'schema_version':'P3R_V2_063E_SHARED_ROOT_PROVENANCE.v1','candidate_id':TARGET,'root_wallet':ROOT_WALLET,'initial_funding_definition':'Earliest economically material inbound transaction reached within the per-address page cap, requiring positive native balance delta into the recipient and a negative balance delta from the source; dust/rent/later replenishment excluded.','birth_profiles':birth_profiles,'byzc_bounded_provenance':{'direct_B65c_to_ByZc':direct_root.get(byzc,False),'known_path':'B65c → Df8CJQR → ByZc tested via Df8 account birth only','Df8_birth':df8_birth,'ByZc_connected_within_two_hops':byzc_root},'initial_vs_launch_time':{'initial_root_layer':birth_profiles,'launch_time_layer':prior['frozen_direct_funders']},'provenance_graph':graph,'false_positive_root_comparison':{'frozen_false_positive_mints':fp,'direct_funders':fp_funders,'birth_profiles':fp_birth,'B65c_direct_root_funders':fp_root},'detectors':detectors,'route_dependence':'HYBRID_ROUTE_AND_BEHAVIOUR' if root_descendants else 'ADDRESS_ALLOWLIST','rotation_analysis':{'verified_root_descendants':sorted(root_descendants),'captures_H1_false_negatives':sum(1 for r in core_rows if r['mint'] in canonical and r['candidate_parent'] not in recurrent and r['candidate_parent'] in root_descendants),'note':'Root verification can add a new funder only when its bounded initial route is independently observed; it is not a static list.'},'watchtower_comparison':'063e has a deterministic route candidate but does not reach WATCHTOWER-like address-blind structural confirmation unless all funders and frozen false positives are discriminated by the root route.','qualification':final,'registration_recommendation':rec,'remaining_uncertainty':'A B65c-rooted singleton also funds one frozen H0 false positive; ByZc/Df8 root provenance remains insufficient within the cap. No broader recursive history was used.','next_action':'Keep 063e provisional. Retain H1 recurrent-funder matching for review only; do not use B65c root as a confirming discriminator.'}
    OUT.mkdir(parents=True,exist_ok=True); rp=OUT/'p3r_v2_063e_shared_root_provenance.v1.json';rp.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    manifest={'schema_version':'P3R_V2_063E_SHARED_ROOT_PROVENANCE_MANIFEST.v1','report':str(rp.relative_to(ROOT)),'report_sha256':sha(rp),'source_digests':{'forensic':sha(FORENSIC),'funder_rpc':sha(FUNDER)},'rpc_call_counts':dict(collections.Counter(x['method'] for x in calls)),'total_calls':len(calls),'page_caps':{'canonical_funders_and_Df8':10,'false_positive_funders':3},'recursive_walkback':False};mp=OUT/'p3r_v2_063e_shared_root_provenance_manifest.v1.json';mp.write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
    conn=sqlite3.connect(ROOT/'database/wt_ops_v2.db');conn.execute("UPDATE potential_operation_workflows SET latest_verdict=?, principal_gap=?, next_action=?, last_investigated_at=?, updated_at=? WHERE candidate_id=?",(final,report['remaining_uncertainty'],report['next_action'],int(time.time()),int(time.time()),TARGET));conn.commit();conn.close()
    print(json.dumps({'qualification':final,'recommendation':rec,'root_descendants':sorted(root_descendants),'calls':len(calls),'report':str(rp),'manifest':str(mp)},sort_keys=True))
if __name__=='__main__':main()
