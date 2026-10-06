import importlib.util
from types import SimpleNamespace
import pytest

P="scripts/run_watchtower_historical_executor.py"
spec=importlib.util.spec_from_file_location("cook_launcher",P); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
def payload(r):
 p=r["request_parameters"]; return {"data":{"items":[{"unixTime":t,"o":100,"h":300 if t==p["time_from"] else 200,"l":10 if t==p["time_to"] else 100,"c":10 if t==p["time_to"] else 200} for t in range(p["time_from"],p["time_to"]+1,900)]}}
def test_launcher_admits_exact_cook_and_invokes_existing_executor(tmp_path):
 calls=[]
 def bind(r): calls.append(r); return SimpleNamespace(status_code=200,payload=payload(r))
 out=mod.launch_cook(db=str(tmp_path/"cook.db"),binding=bind)
 assert out["state"]=="COMPLETED" and len(calls)==6
def test_launcher_rejects_boundary_mismatch_before_executor(tmp_path):
 bad=dict(mod.COOK_BOUNDARY); bad["migration_slot"]+=1
 with pytest.raises(ValueError,match="BOUNDARY_MISMATCH"): mod.launch_cook(db=str(tmp_path/"x.db"),boundary=bad)
