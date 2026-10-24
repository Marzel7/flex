"""Indexed, durable page continuation for an explicitly frozen migration source.

No discovery, provider calls, source mutation or whole-population materialization.
The caller commits idempotent target writes before each page checkpoint advances.
"""
import hashlib,json,sqlite3
from pathlib import Path
from . import dev004_quarantine_transfer as q
BATCH=128
MAX_PAGE_BYTES=8*1024*1024

def identifier(value):
    if not value or not value.replace('_','').isalnum():raise q.TransferHold('INVALID_IDENTIFIER')
    return '"'+value+'"'

def _pages(connection,source,checkpoint,table,*,commit,accept=lambda row:True,where='',args=(),order=None):
    """Each yielded page is bounded; restart replays only an uncheckpointed page.

    Complete means every selected source row was visited and caller-verified;
    source identity, schema and predicate are pinned in the checkpoint.
    """
    tableq=identifier(table);info=connection.execute('pragma table_info('+tableq+')').fetchall()
    keys=[x[1] for x in sorted(info,key=lambda x:x[5]) if x[5]]
    if order is not None:keys=list(order)
    if not keys:
        # Ordinary SQLite tables have a stable integer rowid; preserve it explicitly.
        keys=['rowid']
    terms=','.join(identifier(k) for k in keys)
    ddl=connection.execute('select sql from sqlite_schema where type="table" and name=?',(table,)).fetchone()
    if not ddl:raise q.TransferHold('SOURCE_SCHEMA_REQUIRED')
    authority={'source':q.fingerprint(source),'table':table,'keys':keys,'where':where,'args':list(args),'schema':hashlib.sha256(ddl[0].encode()).hexdigest()}
    cp=Path(checkpoint)
    if cp.exists():
        state=json.loads(cp.read_text())
        if state['authority']!=authority:raise q.TransferHold('CONTINUATION_SOURCE_CHANGED')
    else:
        state={'authority':authority,'cursor':None,'rows':0,'accepted':0,'digest':'00'*32,'complete':False};q.write_checkpoint(cp,state)
    if state['complete']:return
    while True:
        if q.fingerprint(source)!=authority['source']:raise q.TransferHold('CONTINUATION_SOURCE_CHANGED')
        cursor=state['cursor'];pred=[];values=list(args)
        if where:pred.append('('+where+')')
        if cursor is not None:
            pred.append('('+terms+') > ('+','.join('?' for _ in keys)+')');values.extend(cursor)
        sql='select rowid as __migration_rowid__,* from '+tableq+(' where '+' and '.join(pred) if pred else '')+' order by '+terms+' limit ?';values.append(BATCH)
        plan=[x[3] for x in connection.execute('explain query plan '+sql,values)]
        if any('TEMP B-TREE' in x for x in plan):raise q.TransferHold('INDEXED_CONTINUATION_REQUIRED:'+table)
        batch=connection.execute(sql,values).fetchmany(BATCH)
        if sum(len(json.dumps(dict(x),sort_keys=True,default=str).encode()) for x in batch)>MAX_PAGE_BYTES:raise q.TransferHold('PAGE_BYTE_LIMIT')
        for raw in batch:
            row=dict(raw);rid=row.pop('__migration_rowid__');pos=[rid if k=='rowid' else row[k] for k in keys]
            if any(x is None for x in pos):raise q.TransferHold('NULL_CONTINUATION_IDENTITY')
            state['rows']+=1;state['digest']=q.advance(state['digest'],row);state['cursor']=pos
            if accept(row):
                state['accepted']+=1;yield row
        commit()
        if q.fingerprint(source)!=authority['source']:raise q.TransferHold('CONTINUATION_SOURCE_CHANGED')
        state['complete']=len(batch)<BATCH;q.write_checkpoint(cp,state)
        if state['complete']:return


def pages(connection,source,checkpoint,table,**kwargs):
    with q.checkpoint_owner(checkpoint):
        yield from _pages(connection,source,checkpoint,table,**kwargs)
