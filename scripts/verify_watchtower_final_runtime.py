"""Fail-closed, non-secret deployment authority verifier."""
import hashlib,json,subprocess,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
EXPECTED_LEDGER="ad75e5e910662d0259ca095df9e7ae20501136dc0a8d09ccfec62d1e9273b5b8"
def clean_head():
 head=subprocess.check_output(["git","-C",str(ROOT),"rev-parse","HEAD"],text=True).strip()
 if subprocess.call(["git","-C",str(ROOT),"diff","--quiet"]) or subprocess.check_output(["git","-C",str(ROOT),"status","--porcelain"],text=True): raise SystemExit("DIRTY_WORKTREE")
 return head
def verify(expected_sha):
 if clean_head()!=expected_sha: raise SystemExit("SHA_MISMATCH")
 cfg=json.loads((ROOT/"config/watchtower_final_runtime.json").read_text())
 ledger=ROOT/cfg["product"]["audit_ledger"]
 data=json.loads(ledger.read_text())
 if hashlib.sha256(ledger.read_bytes()).hexdigest()!=EXPECTED_LEDGER or len(data["records"])!=96 or len({x["mint"] for x in data["records"]})!=96: raise SystemExit("LEDGER_INVALID")
 if any("/Users/kevinkeaveney/Dev/claude/flex" in str(v) for v in cfg.values()): raise SystemExit("DIRTY_PATH")
 return {"sha":expected_sha,"ledger":str(ledger),"worker":cfg["worker"]["module"],"product":cfg["product"]["module"]}
if __name__=='__main__': print(json.dumps(verify(sys.argv[1]),sort_keys=True))
