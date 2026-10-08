"""Fail-closed, non-secret deployment authority verifier."""
import hashlib,json,os,subprocess,sys
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
EXPECTED_LEDGER="ad75e5e910662d0259ca095df9e7ae20501136dc0a8d09ccfec62d1e9273b5b8"
def clean_head():
 head=subprocess.check_output(["git","-C",str(ROOT),"rev-parse","HEAD"],text=True).strip()
 if subprocess.call(["git","-C",str(ROOT),"diff","--quiet"]) or subprocess.check_output(["git","-C",str(ROOT),"status","--porcelain"],text=True): raise SystemExit("DIRTY_WORKTREE")
 return head
def verify(expected_sha):
 actual_sha=clean_head()
 if actual_sha!=expected_sha:
  raise SystemExit(json.dumps({"actual_sha":actual_sha,"event":"SHA_MISMATCH","expected_sha":expected_sha,"pid":os.getpid(),"root":str(ROOT.resolve()),"timestamp_utc":datetime.now(timezone.utc).isoformat().replace("+00:00","Z")},sort_keys=True,separators=(",",":")))
 cfg=json.loads((ROOT/"config/watchtower_final_runtime.json").read_text())
 ledger=ROOT/cfg["product"]["audit_ledger"]
 data=json.loads(ledger.read_text())
 if hashlib.sha256(ledger.read_bytes()).hexdigest()!=EXPECTED_LEDGER or len(data["records"])!=96 or len({x["mint"] for x in data["records"]})!=96: raise SystemExit("LEDGER_INVALID")
 if any("/Users/kevinkeaveney/Dev/claude/flex" in str(v) for v in cfg.values()): raise SystemExit("DIRTY_PATH")
 return {"sha":expected_sha,"ledger":str(ledger),"worker":cfg["worker"]["module"],"product":cfg["product"]["module"]}
if __name__=='__main__': print(json.dumps(verify(sys.argv[1]),sort_keys=True))
