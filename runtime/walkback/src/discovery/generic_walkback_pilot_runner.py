"""Durable, provider-injected runner for the frozen generic walkback pilot.

The runner never imports a live client.  Production execution must inject one
explicitly; tests use a deterministic fake provider.
"""
from __future__ import annotations
import hashlib, json, time
from pathlib import Path
from src.discovery.generic_wallet_walkback import find_funding_parent, HistoricalContext

TERMINAL = {"PASS", "HOLD_REQUEST_BOUND_EXCEEDED", "HOLD_WALLTIME_EXCEEDED", "HOLD_PROVIDER_ERROR", "HOLD_REPLAY_MISMATCH", "HOLD_INTERRUPTED", "HOLD_ERROR"}

def canonical(value): return json.dumps(value, sort_keys=True, separators=(",", ":"))
def digest(value): return hashlib.sha256(canonical(value).encode()).hexdigest()

class PilotRunner:
    def __init__(self, root, run_id, manifest, provider, *, max_requests=5000, wall_seconds=1800, clock=time.monotonic):
        self.root, self.run_id, self.provider, self.max_requests, self.clock = Path(root), run_id, provider, max_requests, clock
        self.deadline = clock() + wall_seconds
        self.path = self.root / run_id
        self.manifest = manifest
    def _write(self, name, value):
        p=self.path/name; p.parent.mkdir(parents=True,exist_ok=True)
        if p.exists(): raise RuntimeError("immutable artifact collision")
        p.write_text(canonical(value)+"\n"); return p
    def _event(self, state, **extra):
        with (self.path/'lifecycle.jsonl').open('a') as f: f.write(canonical({'state':state,'at':self.clock(),**extra})+'\n')
    def start(self):
        if self.path.exists(): raise RuntimeError('namespace collision')
        if len(self.manifest) != len(set(self.manifest)): raise ValueError('duplicate manifest seed')
        self.path.mkdir(parents=True); self._write('manifest.json', {'seeds':self.manifest,'sha256':digest(self.manifest)}); self._event('STARTED')
    def request(self, wallet, depth):
        if self.clock() >= self.deadline: self._event('HOLD_WALLTIME_EXCEEDED'); raise TimeoutError
        records=list((self.path/'requests').glob('*.json')) if (self.path/'requests').exists() else []
        if len(records) >= self.max_requests: self._event('HOLD_REQUEST_BOUND_EXCEEDED'); raise RuntimeError('request bound')
        rid=f'{depth}-{wallet}-{len(records):06d}'; self._event('REQUEST_STARTED',request_id=rid)
        try: raw=self.provider(wallet)
        except Exception as e: self._write(Path('requests')/(rid+'.json'), {'request_id':rid,'wallet':wallet,'depth':depth,'status':'RPC_ERROR','error':type(e).__name__}); raise
        raw_bytes=(canonical(raw)+'\n').encode(); self._write(Path('responses')/(rid+'.json'), raw)
        record={'request_id':rid,'wallet':wallet,'depth':depth,'status':'SUCCESS','response_sha256':hashlib.sha256(raw_bytes).hexdigest()}
        self._write(Path('requests')/(rid+'.json'), record); return record,raw
    def production_lookup(self, adapter, wallet, depth):
        """Use retained-envelope adapter only; each emitted RPC attempt counts."""
        result, transport_ids = adapter.extract(wallet)
        if not transport_ids: raise RuntimeError('production adapter emitted no immutable transport artifacts')
        existing=list((adapter.transport.root).glob('*.json'))
        if len(existing) > self.max_requests: raise RuntimeError('request bound')
        edge={'child_wallet':wallet,'parent_wallet':result.parent_wallet,'signature':result.signature,'slot':result.slot,'block_time':result.block_time,'amount_sol':result.amount_sol,'mechanism':result.mechanism,'depth':depth,'state':result.state,'transport_request_ids':transport_ids}
        return edge
    def request_with_retry(self, wallet, depth, *, retryable=(TimeoutError,), attempts=3):
        last=None
        for attempt in range(1, attempts + 1):
            try: return self.request(wallet, depth)
            except retryable as exc: last=exc; self._event('RETRYABLE_FAILURE', wallet=wallet, attempt=attempt)
            except Exception: self._event('HOLD_PROVIDER_ERROR', wallet=wallet); raise
        self._event('HOLD_PROVIDER_ERROR', wallet=wallet, exhausted=True); raise last
    def replay(self):
        if not (self.path/'edges.json').exists(): raise RuntimeError('missing retained edges')
        edges=json.loads((self.path/'edges.json').read_text())
        for r in (self.path/'requests').glob('*.json'):
            x=json.loads(r.read_text())
            if x.get('status') == 'SUCCESS' and not (self.path/'responses'/(r.stem+'.json')).exists():
                raise RuntimeError('missing retained response')
            if x.get('status') == 'SUCCESS':
                raw=(self.path/'responses'/(r.stem+'.json')).read_bytes()
                if hashlib.sha256(raw).hexdigest() != x.get('response_sha256'):
                    raise RuntimeError('retained response digest mismatch')
        responses={p.stem:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((self.path/'responses').glob('*.json'))}
        paths={}
        for edge in edges:
            paths.setdefault(edge['child_wallet'], []).append(digest(edge))
        if json.loads((self.path/'paths.json').read_text()) != paths: raise RuntimeError('HOLD_REPLAY_MISMATCH')
        result={'requests':self.replay_digest(),'responses':digest(responses),'edges':digest(edges),'paths':digest(paths)}
        result['run']=digest({'manifest':digest(self.manifest),**result})
        persisted=json.loads((self.path/'digests.json').read_text())
        if result != persisted: raise RuntimeError('HOLD_REPLAY_MISMATCH')
        return result
    def cache_result(self, entry):
        if not entry: return 'PARTIAL'
        if entry.get('complete') and entry.get('response_sha256') and 'response' in entry: return 'COMPLETE_REPLAYABLE'
        return 'UNVERIFIABLE'
    def lookup_with_cache(self, wallet, depth, cache, extract):
        """Return factual evidence; only complete, digest-bound cache avoids RPC."""
        entry=cache.get(wallet)
        state=self.cache_result(entry)
        if state == 'COMPLETE_REPLAYABLE':
            raw=entry['response']
            if hashlib.sha256((canonical(raw)+'\n').encode()).hexdigest() != entry['response_sha256']:
                raise RuntimeError('UNVERIFIABLE_CACHE')
            edge=extract(wallet, raw, depth)
            return edge, {'cache_state':state,'provider_called':False,'branch_wallet':wallet}
        if state == 'PARTIAL':
            self._event('PARTIAL_CACHE_REQUIRES_ACQUISITION', wallet=wallet)
        elif state == 'UNVERIFIABLE' and entry:
            self._event('UNVERIFIABLE_CACHE_REJECTED', wallet=wallet)
        record, raw=self.request(wallet, depth)
        return extract(wallet, raw, depth), {'cache_state':state,'provider_called':True,'request_id':record['request_id'],'branch_wallet':wallet}
    def resume_guard(self):
        started=[]; completed=[]
        for line in (self.path/'lifecycle.jsonl').read_text().splitlines():
            x=json.loads(line)
            if x['state']=='REQUEST_STARTED': started.append(x['request_id'])
        for p in (self.path/'requests').glob('*.json'):
            completed.append(json.loads(p.read_text()).get('request_id'))
        if set(started)-set(completed):
            self._event('HOLD_INTERRUPTED', reason='HOLD_AMBIGUOUS_IN_FLIGHT'); raise RuntimeError('ambiguous in-flight')
    def resume(self, extract):
        """Resume solely from durable artifacts; never calls ``provider``."""
        self.resume_guard()
        requests=sorted((self.path/'requests').glob('*.json'))
        edges_path=self.path/'edges.json'
        edges=json.loads(edges_path.read_text()) if edges_path.exists() else []
        known={e['request_id'] for e in edges}
        for request_path in requests:
            record=json.loads(request_path.read_text())
            if record.get('status') != 'SUCCESS' or record['request_id'] in known: continue
            response_path=self.path/'responses'/(request_path.stem+'.json')
            if not response_path.exists(): raise RuntimeError('HOLD_AMBIGUOUS_IN_FLIGHT')
            raw=json.loads(response_path.read_text())
            edge=extract(record['wallet'], raw, record['depth'])
            edges.append({'request_id':record['request_id'], **edge})
        if edges_path.exists(): edges_path.unlink()
        self._write('edges.json', edges)
        self._event('RESUMED', reused_requests=len(requests), extracted_edges=len(edges))
        return edges
    def replay_digest(self):
        records=[json.loads(p.read_text()) for p in sorted((self.path/'requests').glob('*.json'))]
        return digest(records)

    @staticmethod
    def extract_with_frozen_wrapper(wallet, rpc_response, depth):
        """Test adapter: inject retained RPC replies through the frozen wrapper only."""
        from src.core import walkback_worker
        old = walkback_worker._rpc
        try:
            walkback_worker._rpc = lambda method, params: rpc_response.get((method, tuple(params)))
            result = find_funding_parent(wallet, HistoricalContext(depth=depth, prefer_oldest=depth > 1))
            return {'child_wallet': wallet, 'parent_wallet': result.parent_wallet, 'signature': result.signature, 'slot': result.slot, 'block_time': result.block_time, 'amount_sol': result.amount_sol, 'mechanism': result.mechanism, 'depth': depth, 'state': result.state}
        finally:
            walkback_worker._rpc = old

    def run(self, extract):
        """Run depth 1 then deduplicated depth 2; no depth 3 exists."""
        self.start(); edges=[]; cache={}
        for depth, wallets in ((1, self.manifest),):
            self._event('DEPTH_1_STARTED')
            for wallet in wallets:
                if wallet in cache: continue
                record, raw = self.request(wallet, depth); edge = extract(wallet, raw, depth)
                cache[wallet] = edge; edges.append({'request_id':record['request_id'], **edge})
            self._event('DEPTH_1_COMPLETE')
        parents=sorted({e.get('parent_wallet') for e in edges if e.get('parent_wallet')})
        self._event('DEPTH_2_STARTED', seed_count=len(parents))
        for wallet in parents:
            if wallet in cache: continue
            record, raw = self.request(wallet, 2); edge = extract(wallet, raw, 2)
            cache[wallet] = edge; edges.append({'request_id':record['request_id'], **edge})
        self._event('DEPTH_2_COMPLETE')
        self._write('edges.json', edges); self._event('REPLAY_STARTED')
        responses={p.stem:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((self.path/'responses').glob('*.json'))}
        paths={}
        for edge in edges: paths.setdefault(edge['child_wallet'], []).append(digest(edge))
        self._write('paths.json', paths)
        digests={'requests':self.replay_digest(),'responses':digest(responses),'edges':digest(edges),'paths':digest(paths)}
        digests['run']=digest({'manifest':digest(self.manifest),**digests})
        self._write('digests.json', digests); self._write('replay.json', {'provider_disabled':True, **digests}); self._event('PASS', run_sha256=digests['run'])
        return edges, digests

    def run_acquisition(self, adapter, *, contract_bindings=None):
        """Canonical production acquisition: depth 1 then sorted unique depth 2."""
        self.start()
        edges=[]; traces=[]; seed_edges={}
        for depth, wallets in ((1, self.manifest),):
            self._event('DEPTH_1_STARTED')
            for wallet in wallets:
                edge=self.production_lookup(adapter, wallet, depth)
                edge['rpc_transcript']=list(adapter.last_transcript); edge['rpc_transcript_sha256']=digest(edge['rpc_transcript'])
                records=[adapter.transport.replay(rid) for rid in edge['transport_request_ids']]
                edge['supporting_request_ids']=edge.pop('transport_request_ids')
                edge['supporting_response_digests']=[r['response_sha256'] for r in records]
                edge['edge_sha256']=digest({k:edge.get(k) for k in sorted(edge)})
                trace={'run_id':self.run_id,'initial_seed':wallet,'wallet':wallet,'depth':depth,'request_ids':edge['supporting_request_ids'],'response_digests':edge['supporting_response_digests'],'disposition':edge['state'],'parent_wallet':edge['parent_wallet'],'edge_sha256':edge['edge_sha256'],'rpc_transcript_sha256':edge['rpc_transcript_sha256'],'rpc_transcript':edge['rpc_transcript']}
                traces.append(trace); edges.append(edge); seed_edges[wallet]=[edge]
            self._event('DEPTH_1_COMPLETE')
        parents=sorted({e['parent_wallet'] for e in edges if e.get('parent_wallet')})
        self._event('DEPTH_2_STARTED',seed_count=len(parents))
        for wallet in parents:
            edge=self.production_lookup(adapter,wallet,2); edge['rpc_transcript']=list(adapter.last_transcript); edge['rpc_transcript_sha256']=digest(edge['rpc_transcript']); records=[adapter.transport.replay(rid) for rid in edge['transport_request_ids']]
            edge['supporting_request_ids']=edge.pop('transport_request_ids');edge['supporting_response_digests']=[r['response_sha256'] for r in records];edge['edge_sha256']=digest({k:edge.get(k) for k in sorted(edge)})
            traces.append({'run_id':self.run_id,'initial_seed':None,'wallet':wallet,'depth':2,'request_ids':edge['supporting_request_ids'],'response_digests':edge['supporting_response_digests'],'disposition':edge['state'],'parent_wallet':edge['parent_wallet'],'edge_sha256':edge['edge_sha256'],'rpc_transcript_sha256':edge['rpc_transcript_sha256'],'rpc_transcript':edge['rpc_transcript']});edges.append(edge)
        self._event('DEPTH_2_COMPLETE')
        paths={seed:{'seed':seed,'edge_digests':[e['edge_sha256'] for e in es],'terminal_disposition':es[-1]['state']} for seed,es in seed_edges.items()}
        req=[r['request_sha256'] for r in adapter.transport.records()];resp=[r['response_sha256'] for r in adapter.transport.records()];ed=[e['edge_sha256'] for e in edges];pd={k:digest(v) for k,v in paths.items()}
        digests={'requests':digest(req),'responses':digest(resp),'edges':digest(ed),'paths':digest(pd)};digests['run']=digest({'manifest':digest(self.manifest),'contract':contract_bindings or {},**digests})
        self._write('traces.json',traces);self._write('edges.json',edges);self._write('paths.json',paths);self._write('digests.json',digests);self._event('PASS',run_sha256=digests['run']);return {'state':'PASS','depth_1_seeds':list(self.manifest),'depth_1_seed_count':len(self.manifest),'edges':edges,'traces':traces,'paths':paths,'digests':digests}
