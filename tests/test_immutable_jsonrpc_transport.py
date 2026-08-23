import json,pytest
from src.discovery.immutable_jsonrpc_transport import ImmutableJsonRpcTransport
class R:
 def __init__(self,b,status=200):self.b=b;self.status=status
 def read(self):return self.b
def test_success_empty_error_and_redaction(tmp_path):
 t=ImmutableJsonRpcTransport(tmp_path,'r','https://x/?api-key=secret',lambda q,timeout:R(b'{"jsonrpc":"2.0","result":[]}'))
 m=t.request(wallet='w',method='getSignaturesForAddress',params=['w',{}]);assert m['state']=='SUCCESS' and 'secret' not in json.dumps(m) and t.replay(m['request_id'])['envelope']['result']==[]
def test_jsonrpc_error_and_malformed(tmp_path):
 a=ImmutableJsonRpcTransport(tmp_path,'a','https://x',lambda q,timeout:R(b'{"error":{"code":1}}')).request(wallet='w',method='m',params=[]);assert a['state']=='JSON_RPC_ERROR'
 b=ImmutableJsonRpcTransport(tmp_path,'b','https://x',lambda q,timeout:R(b'{')).request(wallet='w',method='m',params=[]);assert b['state']=='JSON_PARSE_FAILURE'
def test_http_retry_and_tamper(tmp_path):
 t=ImmutableJsonRpcTransport(tmp_path,'r','https://x',lambda q,timeout:R(b'{"result":1}'));a=t.request(wallet='w',method='m',params=[],attempt=1);b=t.request(wallet='w',method='m',params=[],attempt=2);assert a['request_id']!=b['request_id']
 p=tmp_path/'r'/(a['request_id']+'.json');x=json.loads(p.read_text());x['body']='{}';p.write_text(json.dumps(x))
 with pytest.raises(RuntimeError):t.replay(a['request_id'])
def test_http_failure(tmp_path):
 t=ImmutableJsonRpcTransport(tmp_path,'r','https://x',lambda q,timeout:(_ for _ in ()).throw(OSError()));assert t.request(wallet='w',method='m',params=[])['state']=='HTTP_TRANSPORT_FAILURE'
