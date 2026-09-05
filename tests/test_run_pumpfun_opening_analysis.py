import json
from scripts.run_pumpfun_opening_analysis import resolve_rich_birth, run_mints

def birth(root, filename, mint):
    (root/filename).write_text(json.dumps({"mint":mint,"signature":"s","raw_payload":{"txType":"create"}}))

def test_resolves_by_artifact_mint_and_runs_sequentially(tmp_path):
    birth(tmp_path,"not-a-mint.json","a"); birth(tmp_path,"other.json","b"); seen=[]
    def execute(**kw): seen.append(kw["mint"]); return {"mint":kw["mint"],"status":"QUALIFIED","result_artifact":"d"}
    out=run_mints(["a","b"],operation_id="op",birth_root=tmp_path,helius=object(),alchemy=object(),artifact_store=object(),execute=execute)
    assert seen==["a","b"] and [x["result_artifact"] for x in out]==["d","d"]

def test_failure_does_not_stop_next_mint(tmp_path):
    birth(tmp_path,"x.json","a"); birth(tmp_path,"y.json","b"); seen=[]
    def execute(**kw):
        seen.append(kw["mint"])
        if kw["mint"]=="a": raise RuntimeError("x")
        return {"mint":"b","status":"QUALIFIED"}
    assert run_mints(["a","b"],operation_id="op",birth_root=tmp_path,helius=object(),alchemy=object(),artifact_store=object(),execute=execute)[1]["status"]=="QUALIFIED" and seen==["a","b"]
