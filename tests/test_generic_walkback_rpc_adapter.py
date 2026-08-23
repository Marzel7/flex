from src.discovery.immutable_jsonrpc_transport import ImmutableJsonRpcTransport
from src.discovery.generic_walkback_rpc_adapter import RetainedRpcAdapter,PAGINATION_APPLICABILITY
from src.discovery.generic_walkback_pilot_runner import PilotRunner
class R:
 def __init__(self,b):self.b=b;self.status=200
 def read(self):return self.b
def test_real_shape_adapter_routes_frozen_wrapper(tmp_path):
 pages=b'{"jsonrpc":"2.0","result":[{"signature":"S","slot":10}]}'
 tx=b'{"jsonrpc":"2.0","result":{"slot":10,"blockTime":1010,"meta":{"preBalances":[100,0],"postBalances":[0,100]},"transaction":{"message":{"accountKeys":["PARENT","CHILD"]}}}}'
 owner=b'{"jsonrpc":"2.0","result":{"value":{"owner":"11111111111111111111111111111111"}}}'
 def open(req,timeout): return R(pages if b'getSignaturesForAddress' in req.data else tx if b'getTransaction' in req.data else owner)
 a=RetainedRpcAdapter(ImmutableJsonRpcTransport(tmp_path,'r','https://fixture',open)); result,calls=a.extract('CHILD')
 assert result.parent_wallet=='PARENT' and result.signature=='S' and calls and PAGINATION_APPLICABILITY=='PAGINATION_REQUIRED'

def test_runner_counts_real_adapter_transport_artifacts(tmp_path):
 pages=b'{"result":[{"signature":"S","slot":10}]}'
 tx=b'{"result":{"slot":10,"blockTime":1010,"meta":{"preBalances":[100,0],"postBalances":[0,100]},"transaction":{"message":{"accountKeys":["PARENT","CHILD"]}}}}'
 owner=b'{"result":{"value":{"owner":"11111111111111111111111111111111"}}}'
 def open(req,timeout): return R(pages if b'getSignaturesForAddress' in req.data else tx if b'getTransaction' in req.data else owner)
 a=RetainedRpcAdapter(ImmutableJsonRpcTransport(tmp_path,'transport','https://fixture',open));r=PilotRunner(tmp_path,'run',['CHILD'],lambda w:{},max_requests=10)
 e=r.production_lookup(a,'CHILD',1);assert e['parent_wallet']=='PARENT' and len(e['transport_request_ids'])==3
