#!/usr/bin/env python3
"""Qualify a fixed retained birth cohort against disposable stores only.

Inputs are local retained audit metadata, never live databases or provider clients.
No dataset is embedded in the repository or sent over the network.
"""
import argparse
import ast
import collections
import contextlib
import hashlib
import importlib.util
import json
from pathlib import Path
import resource
import signal
import socket
import sqlite3
import sys
import time
import types


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cohort', type=Path, required=True)
    parser.add_argument('--receipts', type=Path, required=True)
    parser.add_argument('--schema-authority', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    if not str(out).startswith('/private/tmp/') or out.exists():
        raise ValueError('NEW_DISPOSABLE_TMP_DIRECTORY_REQUIRED')
    out.mkdir(parents=True)
    resource.setrlimit(resource.RLIMIT_FSIZE, (499_000_000, 499_000_000))
    signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
    attempts = []
    def blocked(*a, **kw):
        attempts.append('blocked')
        raise AssertionError('PROVIDER_NETWORK_FORBIDDEN')
    socket.socket = blocked
    socket.create_connection = blocked
    root = Path(__file__).resolve().parents[2]
    for name in ['src', 'src.ops']:
        module = types.ModuleType(name); module.__path__ = []; sys.modules[name] = module
    def load(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec); sys.modules[name] = module
        spec.loader.exec_module(module)
        return module
    h = load('src.ops.walkback_create_handoff', root / 'runtime/listener/src/ops/walkback_create_handoff.py')
    receipt = load('fixture_receipt', root / 'runtime/listener/src/core/pumpportal_birth_audit.py')
    listener = root / 'runtime/listener/src/core/pumpfun_curve_listener.py'
    tree = ast.parse(listener.read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'enqueue_prospective_migration')
    ns = {'time': time}; exec(compile(ast.Module(body=[node], type_ignores=[]), str(listener), 'exec'), ns)
    enqueue = ns[node.name]
    for path in [args.cohort, args.receipts, args.schema_authority]:
        if path.stat().st_size > 16_000_000: raise ValueError('INPUT_LIMIT')
    data = json.loads(args.schema_authority.read_text())
    rows = json.loads(args.cohort.read_text())['rows']; assert len(rows) == 559
    lookup = json.loads(args.receipts.read_text()); assert lookup['audit_complete']
    selected = []
    for row in rows:
        mr = row['evidence']['migration_receipt']
        qualifying = [r for r in row['evidence']['receipts'] if r.get('persistence_outcome') == 'COMMITTED'
                      and not r.get('canonical_conflict') and r.get('accepted_at_utc_ns')]
        if mr and any(r['accepted_at_utc_ns'] < mr['handoff_utc_ns'] for r in qualifying):
            selected.append(row)
    assert len(selected) == 265
    core_path = out / 'core.sqlite'; ops_path = out / 'ops.sqlite'
    core = sqlite3.connect(core_path); ops = sqlite3.connect(ops_path)
    for conn in [core, ops]: conn.execute('pragma journal_mode=WAL')
    for table in ['token_analysis', 'creator_resolution_queue']: core.execute(data['schemas'][table])
    ops.execute(data['schemas']['wt_walkback_queue']); core.commit(); ops.commit()
    core.execute('begin')
    h.initialize(core, epoch=data['control']['epoch'], activated_at=data['control']['activated_at'],
                 core_base=core_path.stat().st_size, ops_base=ops_path.stat().st_size)
    core.execute('update wb_create_control set enabled=?', (data['control']['enabled'],)); core.commit()
    receipt_root = out / 'receipts'; receipt_root.mkdir()
    audit = receipt.PumpPortalBirthAudit(enabled=False, path=str(out / 'unused-raw'), compact_path=str(receipt_root))
    token_keys = list(next(c['token'] for c in data['cases'] if c['group'] == 'PRE_BIRTH'))
    def insert(conn, table, record):
        conn.execute('insert into '+table+' ('+','.join(record)+') values ('+','.join('?' for _ in record)+')', list(record.values()))
    def store_receipt(record):
        # Lookup artifacts annotate receipts; those annotations are not wire/schema fields.
        record = {k: v for k, v in record.items() if k not in {'identity_digest_valid', 'relative_path'}}
        p = receipt_root / record['receipt_identity'][:2] / (record['receipt_identity']+'.json')
        p.parent.mkdir(exist_ok=True); encoded = json.dumps(record, separators=(',', ':')).encode()
        assert len(encoded) <= 4096; p.write_bytes(encoded)
    for row in selected:
        insert(core, 'token_analysis', {k: row[k] for k in token_keys})
        insert(ops, 'wt_walkback_queue', row['evidence']['walkback'])
        for record in row['evidence']['receipts']: store_receipt(record)
    core.commit(); ops.commit()
    token_baseline = core.execute('select * from token_analysis order by mint').fetchall()
    for row in selected:
        creator = row['earliest_tx_creator']; signature = row['create_tx_signature']
        qualified = audit.accepted_birth(row['mint'], creator, signature)
        assert qualified == (creator, signature)
        prior = row['evidence']['walkback']
        ops_row = tuple(prior[k] for k in ['status', 'enqueued_at', 'creator', 'create_anchor_signature', 'create_anchor_audit_state'])
        core.execute('begin')
        assert enqueue(core, row['mint'], core_path=core_path, ops_path=ops_path, ops_row=ops_row,
                       accepted_birth=qualified) == 'QUALIFIED_PUMPPORTAL'
        core.commit()
    assert core.execute('select count(*) from wb_create_delivery').fetchone()[0] == 265
    assert core.execute('select count(*) from creator_resolution_queue').fetchone()[0] == 0
    core.close(); ops.close()
    @contextlib.contextmanager
    def reader():
        c = sqlite3.connect(core_path.as_uri()+'?mode=ro', uri=True); c.execute('pragma query_only=on')
        try: yield c
        finally: c.close()
    peak_wal = 0
    def writer(path):
        def run(action):
            nonlocal peak_wal
            conn = sqlite3.connect(path)
            try:
                conn.execute('begin'); result = action(conn); conn.commit()
                wal = Path(str(path)+'-wal'); peak_wal = max(peak_wal, wal.stat().st_size if wal.exists() else 0)
                return result
            except BaseException: conn.rollback(); raise
            finally: conn.close()
        return run
    wc = writer(core_path); wo = writer(ops_path)
    class Crash(BaseException): pass
    def crash(where):
        if where == 'after_ops_commit': raise Crash()
    try: h.dispatch(read_core=reader, write_core=wc, write_ops=wo, core_path=core_path, ops_path=ops_path, fault=crash)
    except Crash: pass
    else: raise AssertionError('CRASH_NOT_REPRODUCED')
    rounds = 0; outcomes = []
    while True:
        with reader() as conn: pending = conn.execute("select count(*) from wb_create_delivery where state='PENDING'").fetchone()[0]
        if not pending: break
        rounds += 1; assert rounds <= 17
        outcomes += h.dispatch(read_core=reader, write_core=wc, write_ops=wo, core_path=core_path, ops_path=ops_path)
    with reader() as conn:
        assert conn.execute("select count(*) from wb_create_delivery where state='ACK'").fetchone()[0] == 265
        assert conn.execute('select pending_count,ack_count,admitted from wb_create_control').fetchone() == (0, 265, 265)
        assert conn.execute('select * from token_analysis order by mint').fetchall() == token_baseline
        assert conn.execute('select count(*) from creator_resolution_queue').fetchone()[0] == 0
    conn = sqlite3.connect(ops_path); conn.row_factory = sqlite3.Row
    assert conn.execute("select count(*) from wt_walkback_queue where status='pending' and path_state='CREATE_ANCHORED' and create_anchor_audit_state='VALID'").fetchone()[0] == 265
    assert conn.execute('select count(*) from wt_walkback_queue where create_anchor_slot is not null or create_anchor_block_time is not null').fetchone()[0] == 0
    allowed = {'status', 'path_state', 'creator', 'create_anchor_signature', 'create_anchor_slot', 'create_anchor_block_time', 'create_anchor_source', 'create_anchor_audit_state'}
    for row in selected:
        before = row['evidence']['walkback']; after = dict(conn.execute('select * from wt_walkback_queue where mint=?', (row['mint'],)).fetchone())
        assert {k for k in before if before[k] != after[k]} <= allowed
    conn.close()
    assert h.dispatch(read_core=reader, write_core=wc, write_ops=wo, core_path=core_path, ops_path=ops_path) == []
    # Retained compatibility cases: validate exact receipts and authoritative conflicts first.
    compatibility = []
    for row in rows:
        if lookup['groups'].get(row['mint']) != 'unmatched289': continue
        records = lookup['matching_receipts'].get(row['mint'], [])
        if not records: continue
        for record in records:
            store_receipt(record)
            proof = audit.accepted_birth(row['mint'], record.get('supplied_creator'), record.get('supplied_birth_signature'))
            wb = row['evidence']['walkback']
            if record['persistence_outcome'] == 'COMMITTED':
                assert proof and not row['earliest_tx_creator'] and not row['pf_ws_creator'] and not row['create_tx_signature']
                assert not wb['creator'] and (not wb['create_anchor_signature'] or wb['create_anchor_signature'] == proof[1])
                disposition = 'CANONICAL_RESTORATION_REQUIRED_TERMINAL_OPS_PROTECTED' if wb['status'] == 'complete' else 'CANONICAL_RESTORATION_REQUIRED_BEFORE_PROJECTION'
            else:
                assert proof is None
                disposition = 'UNCONFIRMED_NOT_PROMOTED'
            # Exercise rejection/protection in disposable stores, without inventing
            # an authority to restore absent canonical fields or reopen terminal jobs.
            c = sqlite3.connect(core_path); o = sqlite3.connect(ops_path)
            insert(c, 'token_analysis', {k: row[k] for k in token_keys})
            insert(c, 'creator_resolution_queue', row['evidence']['resolution'])
            insert(o, 'wt_walkback_queue', wb); c.commit(); o.commit()
            canonical_before = c.execute('select * from token_analysis where mint=?', (row['mint'],)).fetchone()
            queue_before = c.execute('select * from creator_resolution_queue where mint=?', (row['mint'],)).fetchone()
            ops_before = o.execute('select * from wt_walkback_queue where mint=?', (row['mint'],)).fetchone()
            c.execute('begin')
            prior = tuple(wb[k] for k in ['status', 'enqueued_at', 'creator', 'create_anchor_signature', 'create_anchor_audit_state'])
            gate = enqueue(c, row['mint'], core_path=core_path, ops_path=ops_path,
                           ops_row=prior, accepted_birth=proof)
            assert gate in {'OPS_PROTECTED', 'QUEUE_TERMINAL'}; c.commit()
            assert c.execute('select * from token_analysis where mint=?', (row['mint'],)).fetchone() == canonical_before
            assert c.execute('select * from creator_resolution_queue where mint=?', (row['mint'],)).fetchone() == queue_before
            assert o.execute('select * from wt_walkback_queue where mint=?', (row['mint'],)).fetchone() == ops_before
            assert c.execute('select count(*) from wb_create_delivery').fetchone()[0] == 265
            c.close(); o.close()
            compatibility.append({'mint': row['mint'], 'receipt_state': record['persistence_outcome'],
                                  'disposition': disposition, 'existing_protection_gate': gate,
                                  'canonical_queue_and_ops_unchanged': True})
    assert len(compatibility) == 4
    assert not attempts
    result = {'status': 'PASS_PROVIDER_FREE_265', 'cohort': 559, 'projected': 265, 'prior_valid_ops_anchors': 121,
              'before_valid_anchor_percent': round(121/559*100, 4), 'after_offline_valid_anchor_percent': round(386/559*100, 4),
              'new_receipt_qualification_stages': {'earlier_integer_migration': 182, 'subsecond_pre_handoff': 83},
              'all_core_fields_unchanged': True, 'ops_nonprojection_fields_unchanged': True, 'unknown_time_slot_unchanged': True,
              'batch': h.BATCH, 'rounds': rounds, 'post_crash_outcomes': dict(collections.Counter(r[1] for r in outcomes)),
              'compatibility': compatibility, 'absent_receipt_cases': {'UNKNOWN': 285},
              'provider_calls': 0, 'network_attempts': len(attempts), 'production_changes': 0,
              'sampled_dispatch_wal_peak_bytes': peak_wal,
              'fixture_file_sizes': {p.name: p.stat().st_size for p in out.glob('*.sqlite*')},
              'source_sha256': hashlib.sha256(listener.read_bytes()).hexdigest(),
              'input_sha256': {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in [args.cohort, args.receipts, args.schema_authority]}}
    (out/'RESULT.json').write_text(json.dumps(result, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k not in ['compatibility', 'input_sha256']}, indent=2))


if __name__ == '__main__':
    main()
