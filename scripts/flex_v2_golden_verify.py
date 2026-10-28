"""Read-only verification of one Golden source commit and its frozen manifest.

No provider, database, gate, scheduler or service APIs are imported. External
mutable V2 state is identified by the manifest, never copied or reset here.
"""
from __future__ import annotations
import argparse,hashlib,json,pathlib,subprocess
MAX_MANIFEST=8_000_000
MAX_FILE=499_000_000
class Hold(RuntimeError):pass

def verify(root,manifest):
 root=pathlib.Path(root).resolve()
 if manifest.get('format')!='FLEX_V2_GOLDEN_CANDIDATE_V1':raise Hold('MANIFEST_VERSION')
 commit=manifest.get('source_commit','')
 actual=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
 if actual!=commit:raise Hold('SOURCE_COMMIT_MISMATCH')
 if subprocess.run(['git','-C',str(root),'diff','--quiet','HEAD']).returncode:raise Hold('SOURCE_DIRTY')
 sources=manifest.get('source_files')
 if not isinstance(sources,dict) or not sources:raise Hold('SOURCE_INVENTORY_REQUIRED')
 for relative,expected in sources.items():
  path=root/relative
  if pathlib.Path(relative).is_absolute() or '..' in pathlib.Path(relative).parts or not path.resolve().is_relative_to(root):raise Hold('SOURCE_PATH_ESCAPE')
  if not path.is_file() or path.stat().st_size>=MAX_FILE:raise Hold('SOURCE_MISSING_OR_OVERSIZED')
  if path.stat().st_size!=expected['bytes'] or hashlib.sha256(path.read_bytes()).hexdigest()!=expected['sha256']:raise Hold('SOURCE_HASH_MISMATCH:'+relative)
 config=manifest.get('runtime_configuration',{})
 if '.codex/worktrees/' in json.dumps(config):raise Hold('HISTORICAL_WORKTREE_DEPENDENCY')
 if config.get('generation')!=manifest.get('generation'):raise Hold('GENERATION_MISMATCH')
 if config.get('helius',{}).get('investigation_lifetime_credits')!=100000:raise Hold('SPENDING_POLICY_MISMATCH')
 if config.get('recovery',{}).get('walkback_interrupted_claim_state')!='pending':raise Hold('RECOVERY_CLAIM_STATE')
 if config.get('retention',{}).get('minimum_age_seconds')!=604800:raise Hold('RETENTION_POLICY')
 if config.get('retention',{}).get('schedule_count')!=1:raise Hold('DUPLICATE_LIFECYCLE_SCHEDULE')
 return {'status':'SOURCE_CONFIGURATION_VERIFIED','source_commit':commit,'verified_files':len(sources),'external_state_not_mutated':True}

def main():
 p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--manifest',required=True);p.add_argument('--sha256',required=True);a=p.parse_args()
 with open(a.manifest,'rb') as f:raw=f.read(MAX_MANIFEST+1)
 if len(raw)>MAX_MANIFEST or hashlib.sha256(raw).hexdigest()!=a.sha256:raise Hold('MANIFEST_DIGEST')
 print(json.dumps(verify(a.root,json.loads(raw)),sort_keys=True))
if __name__=='__main__':main()
