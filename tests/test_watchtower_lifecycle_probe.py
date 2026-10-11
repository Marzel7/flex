import hashlib, json
from pathlib import Path
import pytest
from src.ops.token_data_provider_bindings import ProviderTransportOutcome
from src.ops.watchtower_lifecycle_probe import OneRequestLifecycleProbe, request_identity
from src.ops.watchtower_lifecycle_probe import compact_outcome

AUTH={"rank":49,"mint":"AqYdSQ6oFq8HjLoFkvHW6kYcRM1p94Q1FcvGFxDfpump","creation_timestamp":100,"time_from":100,"time_to":1000,"interval":"15m"}
AUTH["content_hash"]=request_identity(AUTH)
class Gate:
 def __init__(self): self.calls=0
 def check(self): self.calls+=1
class Admission:
 def __init__(self): self.calls=[]
 def admit(self,**kw): self.calls.append(kw)
def probe(tmp_path, response):
 gate,admission=Gate(),Admission(); calls=[]
 def transport(request): calls.append(request); return response
 return OneRequestLifecycleProbe(dict(AUTH),tmp_path/'journal.json',runtime_gate=gate,admission=admission,transport=transport,disk_free=lambda:5*1024**3),gate,admission,calls
def test_exactly_one_transport_and_compact_extrema(tmp_path):
 p,g,a,c=probe(tmp_path,ProviderTransportOutcome(200,{"data":{"items":[{"unixTime":100,"h":12,"l":8},{"unixTime":1000,"h":15,"l":9}]}},{}))
 result=p.execute(); assert result["outcome"]=="COMPLETED"; assert len(c)==len(a.calls)==g.calls==1
 assert "items" not in (tmp_path/'journal.json').read_text()
 with pytest.raises(RuntimeError,match="ALREADY_ATTEMPTED"): p.execute()
def test_empty_is_terminal_and_crash_is_nonretryable(tmp_path):
 p,_,_,_=probe(tmp_path,ProviderTransportOutcome(200,{"data":{"items":[]}},{})); assert p.execute()["outcome"]=="NO_VALID_CANDLES"
 p,_,_,_=probe(tmp_path/'crash',ProviderTransportOutcome(200,{"data":{"items":[]}},{}))
 with pytest.raises(RuntimeError,match="CRASH"): p.execute(crash_after_admission=True)
 with pytest.raises(RuntimeError,match="ALREADY_ATTEMPTED"): p.execute()
def test_malformed_fails_closed(tmp_path):
 p,_,_,_=probe(tmp_path,ProviderTransportOutcome(200,{"data":{"items":[{"unixTime":1,"h":"nan","l":1}]}},{})); assert p.execute()["outcome"]=="INVALID_CANDLE_VALUES"

def test_http200_provider_error_and_invalid_values_are_distinguished():
 assert compact_outcome({'success':False,'code':'RANGE_TOO_LARGE','message':'discarded'}, {}, 200)['outcome']=='PROVIDER_API_ERROR'
 assert compact_outcome({'data':{'items':[{'unixTime':1,'h':'nan','l':1}]}}, {}, 200)['outcome']=='INVALID_CANDLE_VALUES'
 assert compact_outcome({'data':{'items':[{'unixTime':1,'h':2,'l':1},{'unixTime':1,'h':3,'l':2}]}}, {}, 200)['outcome']=='INVALID_CANDLE_VALUES'
