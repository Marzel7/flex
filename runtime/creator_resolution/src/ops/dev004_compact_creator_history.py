"""DEV004 immutable historical baseline; opt-in, read-only, bounded.

No counters are incremented: results are baseline plus novel mint identities in
committed detailed storage. Historical identity/time conflicts fail closed.
"""
import json
from pathlib import Path

MAX_BYTES = 8_000_000
MAX_CREATORS = 100
MAX_IDENTITIES = 25_000
MAX_LOCAL_ROWS = 10_000


def compact_history(core, creator, time_columns, normalize, path):
    with Path(path).open('rb') as f:
        raw = f.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('COMPACT_HISTORY_FILE_BUDGET')
    manifest = json.loads(raw)
    if manifest.get('contract') != 'dev004-compact-creator-history.v1':
        raise ValueError('COMPACT_HISTORY_CONTRACT')
    if manifest.get('timestamp_columns') != time_columns:
        raise ValueError('COMPACT_HISTORY_TIMESTAMP_SCHEMA')
    creators = manifest['creators']
    if not isinstance(creators, dict) or len(creators) > MAX_CREATORS:
        raise ValueError('COMPACT_HISTORY_CREATOR_BUDGET')
    owners = {}
    for wallet, item in creators.items():
        history = item['historical_mints']
        if not isinstance(history, dict):
            raise ValueError('COMPACT_HISTORY_IDENTITIES')
        for mint, timestamp in history.items():
            if mint in owners or (timestamp is not None and type(timestamp) is not int):
                raise ValueError('COMPACT_HISTORY_IDENTITY_CONFLICT')
            owners[mint] = wallet
        if len(owners) > MAX_IDENTITIES:
            raise ValueError('COMPACT_HISTORY_IDENTITY_BUDGET')
        timestamps = [v for v in history.values() if v is not None]
        expected = (len(history), len(timestamps), min(timestamps) if len(timestamps) >= 2 else None,
                    max(timestamps) if len(timestamps) >= 2 else None)
        if expected != (item['launch_count'], item['valid_count'], item['first_seen'], item['last_seen']):
            raise ValueError('COMPACT_HISTORY_STATISTICS_CONFLICT')
        if type(item['cutoff_rowid']) is not int or item['cutoff_rowid'] < 0:
            raise ValueError('COMPACT_HISTORY_CUTOFF')
    base = creators.get(creator, {'historical_mints': {}})
    history = base['historical_mints']
    def timestamp_of(d):
        for col in time_columns:
            if d[col] is not None:
                return normalize(d[col])
        return None

    # Also inspect retained identities by PK: a reassigned historical row must
    # fail for its old owner even though it no longer matches pf_ws_creator.
    keys = list(history)
    for offset in range(0, len(keys), 128):
        batch = keys[offset:offset + 128]
        sql = ('SELECT mint,pf_ws_creator,' + ','.join(time_columns) +
               ' FROM token_analysis WHERE mint IN (' + ','.join('?' for _ in batch) + ')')
        plan = [r[3] for r in core.execute('EXPLAIN QUERY PLAN ' + sql, batch)]
        if any('SCAN ' in p.upper() for p in plan):
            raise ValueError('COMPACT_HISTORY_UNINDEXED_IDENTITY')
        for row in core.execute(sql, batch):
            d = dict(row)
            if d['pf_ws_creator'] != creator or history[d['mint']] != timestamp_of(d):
                raise ValueError('COMPACT_HISTORY_RETAINED_EVIDENCE_CONFLICT')
    sql = 'SELECT mint,' + ','.join(time_columns) + ' FROM token_analysis WHERE pf_ws_creator=? LIMIT 10001'
    plan = [r[3] for r in core.execute('EXPLAIN QUERY PLAN ' + sql, (creator,))]
    if any('SCAN ' in p.upper() for p in plan):
        raise ValueError('COMPACT_HISTORY_UNINDEXED_DETAIL')
    rows = core.execute(sql, (creator,)).fetchall()
    if len(rows) > MAX_LOCAL_ROWS:
        raise ValueError('COMPACT_HISTORY_LOCAL_ROW_BUDGET')
    count = len(history)
    timestamps = [t for t in history.values() if t is not None]
    seen = set()
    for row in rows:
        d = dict(row)
        mint = d['mint']
        if mint in seen:
            raise ValueError('COMPACT_HISTORY_DUPLICATE_DETAIL')
        seen.add(mint)
        timestamp = timestamp_of(d)
        if mint in owners:
            if owners[mint] != creator or history[mint] != timestamp:
                raise ValueError('COMPACT_HISTORY_RETAINED_EVIDENCE_CONFLICT')
            continue
        count += 1
        if timestamp is not None:
            timestamps.append(timestamp)
    valid = len(timestamps)
    return count, valid, min(timestamps) if valid >= 2 else None, max(timestamps) if valid >= 2 else None
