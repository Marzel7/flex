#!/usr/bin/env python3
"""Two-hop continuation from frozen P3R 13a04 RPC hop-1 sources."""
import hashlib, json, re, time, urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('/tmp/p3r-clean-20260824T092959Z')
PRIOR = ROOT / 'rpc_13a04_upstream_v3/p3r_13a04_upstream_rpc_edges.v1.jsonl'
OUT = ROOT / 'rpc_13a04_upstream_continue_2hop_v1'
CEIL = {'max_additional_depth': 2, 'max_transactions_per_wallet': 8, 'max_requests_per_wallet': 9, 'max_total_requests': 90, 'max_pagination_depth': 1, 'max_retries': 0, 'max_elapsed_seconds': 180}

def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def put(path, value):
    Path(path).write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')
    return {'path': str(path), 'sha256': sha(path)}
def endpoint():
    text = Path('.env').read_text()
    match = re.search(r'^\s*(?:export\s+)?HELIUS_TEMP_API_KEY\s*=\s*["\']?([^"\'\s]+)', text, re.M)
    return 'https://mainnet.helius-rpc.com/?api-key=' + match.group(1) if match else None
def inbound(tx, wallet):
    found = []
    def inspect(rows):
        for row in rows or []:
            parsed = row.get('parsed') if isinstance(row, dict) else None
            info = (parsed or {}).get('info', {}) if isinstance(parsed, dict) else {}
            if info.get('destination') == wallet and info.get('source'):
                try: amount = int(info.get('lamports') or info.get('amount'))
                except (ValueError, TypeError): continue
                found.append({'source_wallet': info['source'], 'destination_wallet': wallet, 'lamports': amount, 'mechanism': row.get('program', 'UNKNOWN'), 'instruction_type': parsed.get('type')})
    inspect(tx.get('transaction', {}).get('message', {}).get('instructions'))
    for row in tx.get('meta', {}).get('innerInstructions') or []: inspect(row.get('instructions'))
    return found

def main():
    if OUT.exists(): raise SystemExit('refuse to reuse immutable namespace')
    OUT.mkdir()
    starts = [json.loads(line) for line in PRIOR.read_text().splitlines()]
    bindings = {'candidate_id': 'p3r-candidate-13a04d7da7a1fc55', 'prior_edges_path': str(PRIOR), 'prior_edges_sha256': sha(PRIOR), 'analysis_code_sha256': sha(__file__), 'provider': 'HELIUS_TEMP_API_KEY (not persisted)', 'ceilings': CEIL, 'acquired_at_utc': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')}
    start = put(OUT / 'p3r_13a04_continue_start_manifest.v1.json', {'bindings': bindings, 'start_edges': starts, 'semantics': 'Start exactly at prior verified hop-1 source wallets and walk two older inbound funding hops.'})
    url = endpoint(); requests, edges, branches = [], [], []; total = 0; begun = time.monotonic()
    def rpc(method, params, wallet):
        nonlocal total
        if total >= CEIL['max_total_requests'] or time.monotonic() - begun > CEIL['max_elapsed_seconds']: raise RuntimeError('CEILING')
        total += 1
        try:
            data = json.dumps({'jsonrpc': '2.0', 'id': total, 'method': method, 'params': params}).encode()
            response = urllib.request.urlopen(urllib.request.Request(url, data, {'Content-Type': 'application/json'}), timeout=20)
            result = json.loads(response.read()).get('result')
            requests.append({'ordinal': total, 'wallet': wallet, 'method': method, 'status': 'OK'})
            return result
        except Exception as error:
            requests.append({'ordinal': total, 'wallet': wallet, 'method': method, 'status': 'ERROR', 'error_type': type(error).__name__})
            return None
    for source in starts:
        wallet, before = source['source_wallet'], source['signature']
        branch = {'mint': source['mint'], 'start_wallet': wallet, 'status': 'COMPLETE', 'edges': 0}
        for depth in (2, 3):
            signatures = rpc('getSignaturesForAddress', [wallet, {'limit': CEIL['max_transactions_per_wallet'], 'before': before}], wallet)
            chosen = None
            for item in signatures or []:
                tx = rpc('getTransaction', [item['signature'], {'encoding': 'jsonParsed', 'maxSupportedTransactionVersion': 0}], wallet)
                candidates = inbound(tx or {}, wallet)
                if candidates:
                    chosen = max(candidates, key=lambda x: x['lamports'])
                    chosen.update({'mint': source['mint'], 'relative_hop': depth, 'signature': item['signature'], 'block_time': (tx or {}).get('blockTime'), 'transaction_success': (tx or {}).get('meta', {}).get('err') is None, 'provider_provenance': 'dotenv:HELIUS_TEMP_API_KEY'})
                    edges.append(chosen); branch['edges'] += 1; wallet, before = chosen['source_wallet'], item['signature']; break
            if not chosen: branch['status'] = 'BOUNDED_INCOMPLETE'; break
        branches.append(branch)
    by_source = defaultdict(list)
    for edge in edges: by_source[edge['source_wallet']].append(edge)
    shared = [{'wallet': wallet, 'branches': len({x['mint'] for x in rows}), 'mints': sorted({x['mint'] for x in rows}), 'amounts': sorted({x['lamports'] for x in rows}), 'hops': sorted({x['relative_hop'] for x in rows})} for wallet, rows in by_source.items() if len({x['mint'] for x in rows}) > 1]
    edge_path = OUT / 'p3r_13a04_continue_rpc_edges.v1.jsonl'
    edge_path.write_text(''.join(json.dumps(edge, sort_keys=True) + '\n' for edge in edges))
    artifacts = {'start_manifest': start, 'edges': {'path': str(edge_path), 'sha256': sha(edge_path)}, 'branch_graph': put(OUT / 'p3r_13a04_continue_branch_graph.v1.json', {'bindings': bindings, 'branches': branches, 'edges': edges}), 'convergence': put(OUT / 'p3r_13a04_continue_convergence.v1.json', {'bindings': bindings, 'shared_upstream_wallets': shared, 'classification': 'STRONG_SHARED_UPSTREAM_ADDRESS' if shared else 'NO_SHARED_UPSTREAM_ADDRESS' if all(x['status'] == 'COMPLETE' for x in branches) else 'INSUFFICIENT_RPC_EVIDENCE'}), 'acquisition': put(OUT / 'p3r_13a04_continue_acquisition_manifest.v1.json', {'bindings': bindings, 'provider_requests': requests, 'request_count': total, 'branches': branches, 'elapsed_seconds': time.monotonic() - begun, 'ceilings_enforced': True})}
    artifacts['manifest'] = put(OUT / 'p3r_13a04_continue_artifact_manifest.v1.json', {'bindings': bindings, 'artifacts': artifacts})
    print(json.dumps({'requests': total, 'branches': branches, 'edges': edges, 'shared': shared, 'artifacts': artifacts}, indent=2))
if __name__ == '__main__': main()
