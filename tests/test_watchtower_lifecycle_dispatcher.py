import json
from pathlib import Path

from src.ops.watchtower_lifecycle_cohort import build_cohort_authorization
from src.ops.watchtower_lifecycle_dispatcher import FiniteLifecycleDispatcher
from src.ops.watchtower_lifecycle_probe import request_identity
from src.ops.watchtower_lifecycle_probe import OneRequestLifecycleProbe
from src.ops.token_data_provider_bindings import ProviderTransportOutcome


def _record(rank):
    value={"rank":rank,"mint":f"mint-{rank}","creation_timestamp":rank,"time_from":rank,"time_to":rank+864000,"interval":"15m"}; value["content_hash"]=request_identity(value); return value


class Guard:
    def __init__(self): self.calls=0
    def check(self, reserve=0): self.calls+=1


def test_dispatcher_enforces_sessions_cadence_resume_and_exhaustion(tmp_path):
    auth=build_cohort_authorization([_record(n) for n in range(1,52)],chronology_hash="c",authority_id="a")
    clock=[0.0]; calls=[]; guard=Guard()
    class Probe:
        def __init__(self, record, path): self.record,self.path=record,path
        def execute(self):
            self.path.parent.mkdir(parents=True,exist_ok=True); self.path.write_text(json.dumps({"state":"COMPLETED","request_identity":self.record["request_identity"],"mint":self.record["mint"]})); calls.append(self.record["rank"])
    result=FiniteLifecycleDispatcher(auth,tmp_path,health=lambda:None,probe_factory=Probe,storage_guard=guard,clock=lambda:clock[0],sleep=lambda n:clock.__setitem__(0,clock[0]+n)).run()
    assert result["status"]=="EXHAUSTED" and calls==list(range(1,52)) and [len(s) for s in result["sessions"]]==[50,1] and clock[0]>=300


def test_dispatcher_excludes_existing_terminal_and_stops_on_guard(tmp_path):
    auth=build_cohort_authorization([_record(1),_record(2)],chronology_hash="c",authority_id="a")
    prior=tmp_path/auth["records"][0]["request_identity"]/'journal.json'; prior.parent.mkdir(parents=True); prior.write_text(json.dumps({"state":"COMPLETED","request_identity":auth["records"][0]["request_identity"],"mint":"mint-1"}))
    class Stop:
        def check(self, reserve=0): raise RuntimeError("STORAGE_HOLD")
    try:
        FiniteLifecycleDispatcher(auth,tmp_path,health=lambda:None,probe_factory=lambda *_:None,storage_guard=Stop()).run()
    except RuntimeError as exc:
        assert str(exc)=="STORAGE_HOLD"
    assert prior.exists()


def test_dispatcher_composes_actual_probe_with_fake_transport(tmp_path):
    auth=build_cohort_authorization([_record(1),_record(2)],chronology_hash="c",authority_id="a")
    guard=Guard(); calls=[]
    class Gate:
        def check(self): return None
    class Admission:
        def admit(self, **kwargs): calls.append(kwargs)
    def factory(record, path):
        item={key:record[key] for key in ("rank","mint","creation_timestamp","time_from","time_to","interval","content_hash")}
        return OneRequestLifecycleProbe(item,path,runtime_gate=Gate(),admission=Admission(),disk_free=lambda:5*1024**3,storage_guard=guard,
            transport=lambda _:ProviderTransportOutcome(200,{"success":True,"data":{"items":[]}},{}))
    result=FiniteLifecycleDispatcher(auth,tmp_path,health=lambda:None,probe_factory=factory,storage_guard=guard).run()
    assert result["status"]=="EXHAUSTED" and len(calls)==2
    assert all(json.loads((tmp_path/record["request_identity"]/'journal.json').read_text())["state"]=="NO_VALID_CANDLES" for record in auth["records"])


def test_dispatcher_cancellation_precedes_any_probe(tmp_path):
    auth=build_cohort_authorization([_record(1)],chronology_hash="c",authority_id="a")
    result=FiniteLifecycleDispatcher(auth,tmp_path,health=lambda:None,probe_factory=lambda *_:(_ for _ in ()).throw(AssertionError("probe")),storage_guard=Guard(),cancelled=lambda:True).run()
    assert result["status"]=="CANCELLED" and not result["completed"]


def test_dispatcher_reports_progress_only_after_durable_completion(tmp_path):
    auth=build_cohort_authorization([_record(1),_record(2)],chronology_hash="c",authority_id="a")
    progress=[]
    class Probe:
        def __init__(self, record, path): self.record,self.path=record,path
        def execute(self):
            self.path.parent.mkdir(parents=True,exist_ok=True)
            self.path.write_text(json.dumps({"state":"COMPLETED","request_identity":self.record["request_identity"],"mint":self.record["mint"]}))
    result=FiniteLifecycleDispatcher(auth,tmp_path,health=lambda:None,probe_factory=Probe,storage_guard=Guard(),progress=progress.append).run()
    assert result["status"]=="EXHAUSTED"
    assert progress==[
        {"completed":1,"completed_sessions":0,"current_session":1},
        {"completed":2,"completed_sessions":0,"current_session":1},
    ]
