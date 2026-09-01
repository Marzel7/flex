#!/usr/bin/env python3
"""Provider-free replay of the confirmed-operation funding matrix."""
from __future__ import annotations
import hashlib,json
from collections import Counter,defaultdict
from pathlib import Path
from audit_confirmed_operation_funding_architecture import family_key,synthetic_leviathan

M=Path('docs/audits/confirmed_operation_funding_architecture_matrix.v1.json')
def load(path): return json.load(open(path))
def main():
 p=load(M)
 records={
  'Leviathan':synthetic_leviathan(),
  'WATCHTOWER':load('docs/audits/watchtower_funding_transaction_census.v1.json')['records'],
  'Byzantine':load('docs/audits/byzantine_funding_transaction_census.v1.json')['records'],
  'FOUR_STEP_30_SOL_14_479K_WSOL_LADDER':load('docs/audits/four_step_30_sol_14_479k_wsol_ladder_funding_transaction_census.v1.json')['records'],
 }
 matrix=defaultdict(Counter)
 for op,rs in records.items():
  for r in rs: matrix[family_key(r)][op]+=1
 rebuilt=[{'architecture':json.loads(k),'counts':dict(v)} for k,v in sorted(matrix.items())]
 expected=p['operation_family_matrix']
 names=sorted(records); collisions={}
 for depth,label in ((1,'Mechanism'),(2,'Sequence'),(3,'+ amounts'),(4,'+ continuity'),(5,'+ roles'),(6,'Complete')):
  n=0
  for i,a in enumerate(names):
   ka={family_key(x,depth) for x in records[a]}
   for b in names[i+1:]:
    if ka & {family_key(x,depth) for x in records[b]}: n+=1
  collisions[label]=n
 raw=dict(p); digest=raw.pop('digest')
 digest_ok=hashlib.sha256(json.dumps(raw,sort_keys=True,separators=(',',':')).encode()).hexdigest()==digest
 ok=rebuilt==expected and collisions==p['collision_depth'] and digest_ok
 print(json.dumps({'CONFIRMED_FUNDING_ARCHITECTURE_REPLAY_PASS':ok,'provider_calls':0,'matrix_match':rebuilt==expected,'collision_match':collisions==p['collision_depth'],'digest_match':digest_ok},sort_keys=True))
 if not ok: raise SystemExit(1)
if __name__=='__main__': main()
