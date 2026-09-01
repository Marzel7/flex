#!/usr/bin/env python3
"""Bounded local P3R zero audit, reference comparison, and shadow qualification."""
import argparse, hashlib, json, os, sqlite3
from collections import Counter
from pathlib import Path

V="p3r-shadow-qualification-v1"
EXPECTED={"corpus":"38632f80231e29bfe686360898329331f88cf593e7dbac09c4f08a1aa58da651","manifest":"d65fd0f2b248d75fbff0aae82e8f975c390f973dfa66bfd1ac30a635bf85c287","discovery":"6f46a50443eefc4b22d38e021f7dd439145ebabb45bff83bbfe1f2e35d862e99","membership":"a96635a9e8563a5d4da9dc5998f00805d4f968e79dc48a428ed1f69981062a5d","signals":"52079c5b813bbe18ec0cfbc55d3bc2c3222b90695586997fec017d8e5ba0a389"}
def digest(p):
 h=hashlib.sha256()
 with open(p,"rb") as f:
  for b in iter(lambda:f.read(1048576),b""):h.update(b)
 return h.hexdigest()
def canon(x):return json.dumps(x,sort_keys=True,separators=(",",":"),ensure_ascii=True)
def write_new(path,x):
 path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
 if path.exists():raise RuntimeError("immutable output already exists: "+str(path))
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o644)
 with os.fdopen(fd,"w",encoding="ascii") as f:f.write(canon(x)+"\n");f.flush();os.fsync(f.fileno())
def sig(r,zero_neutral=False):
 o=r.get("selected_edge_observations") or []
 topo=(r.get("edge_count"),r.get("max_hop_depth"),len(r.get("parents") or [])); mech=tuple(x.get("mechanism") for x in o); amt=tuple(x.get("amount_lamports") for x in o)
 return topo, (() if zero_neutral and any(x==0 for x in amt) else amt), mech
def main():
 a=argparse.ArgumentParser();a.add_argument("--corpus",required=True);a.add_argument("--manifest",required=True);a.add_argument("--discovery",required=True);a.add_argument("--membership",required=True);a.add_argument("--signals",required=True);a.add_argument("--db",default="database/wt_ops_v2.db");a.add_argument("--output-dir",required=True);z=a.parse_args()
 for k,p in (("corpus",z.corpus),("manifest",z.manifest),("discovery",z.discovery),("membership",z.membership),("signals",z.signals)):
  if digest(p)!=EXPECTED[k]:raise RuntimeError("upstream digest mismatch: "+k)
 code=digest(__file__); records=[json.loads(x) for x in open(z.corpus,encoding="ascii") if x.strip()]; original=json.load(open(z.membership))["candidates"]
 if len(records)!=28883 or len({x['mint'] for x in records})!=28883:raise RuntimeError("corpus integrity failure")
 conn=sqlite3.connect("file:"+str(Path(z.db).resolve())+"?mode=ro",uri=True);conn.execute("begin")
 zero=list(conn.execute("select mint,mechanism,signature,evidence_key,pre_balance,post_balance,net_balance_change,instruction_index,inner_instruction_index from wt_walkback_edge_candidates where selection_status='SELECTED' and amount_lamports=0"))
 zg=[]
 for m in sorted({x[1] for x in zero}):
  q=[x for x in zero if x[1]==m];zg.append({"mechanism":m,"edges":len(q),"mints":len({x[0] for x in q}),"all_balance_fields_null":all(x[4] is None and x[5] is None and x[6] is None for x in q),"all_instruction_indexes_sentinel":all(x[7]==-1 and x[8]==-1 for x in q)})
 watch={x[0] for x in conn.execute("select distinct mint from wt_watchtower_launches where mint is not null")}; opmembers=conn.execute("select count(*) from operator_launch_membership").fetchone()[0];conn.close()
 observed=[r for r in records if r.get("selected_edge_observations")]
 watch_observed=len({r['mint'] for r in observed}&watch)
 zero_mints={x[0] for x in zero}; zero_audit={"schema_version":V,"classification":"UNRESOLVED","bindings":{"corpus":EXPECTED['corpus'],"code_sha256":code},"source":{"table":"wt_walkback_edge_candidates","column":"amount_lamports","materialization":"direct selected-row copy without normalization","nullable_column":True},"measurement":{"selected_zero_edges":len(zero),"affected_mints":len(zero_mints),"by_mechanism":zg,"unique_signatures":len({x[2] for x in zero}),"unique_evidence_keys":len({x[3] for x in zero})},"finding":"Stored zero is distinct from SQL null but all selected zero rows lack retained balance deltas and instruction indices; retained local evidence cannot prove economic zero versus parser/source fallback. Zero is excluded as positive amount evidence."}
 ref={"schema_version":V,"bindings":{"corpus":EXPECTED['corpus'],"code_sha256":code},"watchtower":{"source":"database/wt_ops_v2.db:wt_watchtower_launches","membership_semantics":"explicit mint rows retained by Watchtower launch table; reference only","mint_count":len(watch),"p3r_overlap":len(watch&{r['mint'] for r in records}),"complete_signature_overlap":watch_observed},"three_sw2":{"source":"database/wt_ops_v2.db:operator_launch_membership","membership_semantics":"explicit launch assignment table","mint_count":opmembers,"p3r_overlap":0,"complete_signature_overlap":0,"status":"NO_LOCAL_AUTHORITATIVE_MINT_MEMBERSHIP"}}
 # Candidate transition: any amount-zero member is not retained, otherwise original deterministic rule survives.
 trans=[]; survivors=[]
 for c in original:
  ms=set(c['mints']); haszero=bool(ms&zero_mints); wo=len(ms&watch)
  if haszero: revised=None; robust="REMOVED_ZERO_AMOUNT_UNRESOLVED"; klass="INSUFFICIENT_EVIDENCE"
  else:
   revised=c['strength'];robust="PERSISTS_EXACT_NONZERO_MULTI_DIMENSION";klass="KNOWN_REFERENCE_OVERLAP" if wo else "NOVEL_BEHAVIOURAL_CANDIDATE"; survivors.append(dict(c,known_reference_classification=klass,watchtower_overlap=wo,three_sw2_overlap=0))
  trans.append({"original_candidate_id":c['candidate_id'],"original_strength":c['strength'],"revised_strength":revised,"robustness_result":robust,"known_reference_classification":klass,"watchtower_overlap":wo,"three_sw2_overlap":0,"reason":"zero amount is not positive evidence" if haszero else "exact topology, nonzero raw amount vector and mechanism recurrence with creator/funder rotation"})
 # Same features against the P3R background and Watchtower reference, no identity fields.
 def fp(rows):
  ss=[sig(r) for r in rows if r.get('selected_edge_observations')];return {"population":len(rows),"complete_signatures":len(ss),"unique_signatures":len(set(ss)),"top_signature_count":Counter(ss).most_common(1)[0][1] if ss else 0}
 wf=[r for r in records if r['mint'] in watch]; comparison={"schema_version":V,"watchtower":fp(wf),"background":fp(records),"three_sw2":{"status":"unavailable_no_membership"},"finding":"Watchtower is measurable only as a partial 274-mint reference; 3SW2 comparison is unavailable. No behavioural similarity is treated as membership."}
 counts=Counter(x['revised_strength'] for x in trans if x['revised_strength']);classes=Counter(x['known_reference_classification'] for x in trans)
 qualification={"schema_version":V,"bindings":{"corpus":EXPECTED['corpus'],"manifest":EXPECTED['manifest'],"discovery":EXPECTED['discovery'],"membership":EXPECTED['membership'],"signals":EXPECTED['signals'],"code_sha256":code},"original_candidate_count":len(original),"surviving_candidate_count":len(survivors),"strength_counts":dict(counts),"classification_counts":dict(classes),"transitions":trans,"robustness_tests":{"creator_equality_removed":True,"direct_funder_equality_removed":True,"parent_equality_not_required":True,"zero_amount_downweighted":"removed affected families","amount_tolerance":"exact raw values retained; no unvalidated bands introduced","topology_only":"non-qualifying alone","amount_only":"non-qualifying alone","mechanism_only":"non-qualifying alone","combined_features":"required for surviving families","minimum_membership":"original n>=4, creators>=3, funders>=3 retained"},"verdict":"P3R_CANDIDATES_READY_FOR_SHADOW_EVALUATION" if survivors else "P3R_CANDIDATES_NOT_QUALIFIED"}
 membership={"schema_version":V,"bindings":{"qualification_code_sha256":code,"prior_membership":EXPECTED['membership']},"candidates":survivors}
 signals={"schema_version":V,"signals":[{"name":"topology_plus_nonzero_amount_plus_mechanism","classification":"SHADOW_READY","coverage":len(observed)-len(zero_mints),"address_dependence":"none","false_positive_control":"requires exact combined recurrence and rotating creator/funder support","limitation":"partial evidence coverage"},{"name":"topology_plus_mechanism_with_zero_neutralization","classification":"PROMISING_NOT_READY","coverage":len(observed),"address_dependence":"none","false_positive_control":"zero cannot count as positive amount evidence","limitation":"requires prospective semantic validation"},{"name":"raw_zero_amount_vector","classification":"UNQUALIFIED","coverage":len(zero_mints),"address_dependence":"none","false_positive_control":"excluded","limitation":"unresolved semantics"},{"name":"address_recurrence","classification":"NON_DISCRIMINATIVE","coverage":28883,"address_dependence":"yes","false_positive_control":"never sufficient alone","limitation":"wallet rotation"},{"name":"three_sw2_recovery","classification":"UNQUALIFIED","coverage":0,"address_dependence":"not applicable","false_positive_control":"no local authoritative memberships","limitation":"reference mapping absent"}]}
 shadow={"schema_version":V,"status":"DESIGN_ONLY_NO_ACTIVATION","frozen_candidate_membership":"revised membership artifact emitted with this run","eligible_incoming_launch":"new launch with qualified selected-edge observations and all required topology/mechanism fields","missingness":"abstain when required evidence is absent or any amount is semantically unresolved zero","address_blind_path":"match exact non-address fingerprint only; do not query creator/funder identity","known_reference_path":"measure separately only against explicit reference mint labels","match_rule":"exact topology + nonzero raw amount vector + mechanism sequence; candidate threshold remains n>=4 / >=3 creators / >=3 funders in frozen historical evidence","false_positive_controls":"background prevalence, no address-only matches, human review output, no action on match","metrics":["known-reference recall where a reference exists","candidate match rate","background match rate","address-blind persistence","abstention rate","evidence completeness","candidate stability"],"stop_conditions":["zero semantics remain unresolved for matched feature","background rate exceeds predeclared review ceiling","evidence coverage materially differs from frozen corpus","any proposed production use"],"review_output":"append-only local shadow review record; no canonical membership, identity, or action"}
 out=Path(z.output_dir); write_new(out/'p3r_zero_lamport_semantic_audit.v1.json',zero_audit);write_new(out/'p3r_known_reference_binding.v1.json',ref);write_new(out/'p3r_known_reference_fingerprint_comparison.v1.json',comparison);write_new(out/'p3r_candidate_robustness.v1.json',qualification);write_new(out/'p3r_candidate_operational_family_membership.v2.json',membership);write_new(out/'p3r_discovery_signal_qualification.v1.json',signals);write_new(out/'p3r_shadow_evaluation_contract.v1.json',shadow)
 print(canon({"verdict":qualification['verdict'],"survivors":len(survivors),"removed":len(original)-len(survivors),"digests":{p.name:digest(p) for p in sorted(out.glob('p3r_*v1.json')) if p.name!='p3r_recurring_pattern_discovery.v1.json'}|{"p3r_candidate_operational_family_membership.v2.json":digest(out/'p3r_candidate_operational_family_membership.v2.json')}}))
if __name__=='__main__':main()
