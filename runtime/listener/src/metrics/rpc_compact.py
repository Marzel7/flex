"""Bounded RPC telemetry summaries. Explicit installation; no import-time I/O.

No raw-row deletion, checkpoint, provider access or background thread. The caller
owns the connection/transaction. Install only during an authorized migration.
"""
from __future__ import annotations
import datetime as dt
import json
import math
import sqlite3
import time
from dataclasses import dataclass

VERSION = 1
DETAIL_SECONDS = 7 * 86400
DAY_LABELS = 31  # existing inclusive DATE(now, '-30 days') API
MAX_BATCH = 128
MAX_KEYS = 4096
MAX_ENCODED_BYTES = 24 * 1024 * 1024
MAX_CELL_BYTES = 4096
MAX_PINS = 1024
MAX_RESETS = 30
DIMS = ('section', 'provider', 'method', 'source_file', 'optimization_layer', 'status_code', 'cache_action')
VIEWS = ('v_rpc_daily_savings', 'v_rpc_savings_by_section', 'v_rpc_cache_performance',
         'v_rpc_cumulative_savings', 'v_rpc_efficiency_score', 'v_rpc_efficiency_all_time',
         'v_rpc_efficiency_by_section', 'v_rpc_efficiency_health',
         'v_rpc_efficiency_trend_7day', 'v_rpc_rolling_7day_average')
VIEW_HASHES = {'v_rpc_daily_savings': 'a28a0101c140ebd9c91283637f21877f763d483922cebdcdc1a8ffe2d737711e', 'v_rpc_savings_by_section': '244fae870d13beffb5d0c6d8996e5f8fd1a7f81d54323266ce0c3deeee130332', 'v_rpc_cache_performance': 'fa72b1898b0c3483c431e7ccbcd83def3c699a3c1e9d2748ffd3d067ca5db332', 'v_rpc_cumulative_savings': 'dc2496d93ea2ea910ad887a6c3df8bdc53c85beea88153466d00abdc217d9e40', 'v_rpc_efficiency_score': '6d86928153a22658146c976489dd9915a0a1053035f58f786b51c8bb97500281', 'v_rpc_efficiency_all_time': 'b43a1187aeab617cd62457e0b70b9a82febda986db2db46382a852d81228f9f3', 'v_rpc_efficiency_by_section': 'f894ef993c1fc655e5f9e15c4b2be90c4ff67316a9e5f0d188821782370ad714', 'v_rpc_efficiency_health': '0d90936e0194abaa5d6d2bbf6c61e486cc7d56822ec34acb351a14968b479671', 'v_rpc_efficiency_trend_7day': '2330a290d63499c63607aa7aa1e12f6798d1da3586898bd7d853c6f3fbd65731', 'v_rpc_rolling_7day_average': 'db746a6665535648085bcfee057010db23678aa278b58a058e7a25d39c85d2a2'}
LIFETIME_VIEWS = frozenset(('v_rpc_cache_performance','v_rpc_cumulative_savings','v_rpc_efficiency_all_time'))


SOURCE_NAMES = ['id','timestamp','recorded_at','section','provider','method','source_file','optimization_layer','cache_action','status_code','latency_ms','credits','credits_saved']
def _source_expression(name):
    if name in DIMS and name!='status_code':return f'substr({name},1,97) AS {name}'
    if name=='recorded_at':return 'substr(recorded_at,1,129) AS recorded_at'
    if name in ('credits','credits_saved','status_code'):
        return f"CASE WHEN typeof({name}) IN ('integer','null') THEN {name} ELSE '__invalid__' END AS {name}"
    if name in ('timestamp','latency_ms'):
        return f"CASE WHEN typeof({name}) IN ('integer','real','null') THEN {name} ELSE '__invalid__' END AS {name}"
    return name
SOURCE_SELECT = ','.join(_source_expression(k) for k in SOURCE_NAMES)+",DATE(recorded_at),DATE(timestamp,'unixepoch'),length(CAST(recorded_at AS BLOB))"
SOURCE_NAMES += ['recorded_day','event_day','recorded_bytes']

class Hold(RuntimeError):
    """Explicit incomplete/unsupported/capacity state; never return partial totals."""
    def __init__(self, code, **details):
        self.code, self.details = code, details
        super().__init__(code)
    def response(self):
        return dict(status='unsupported' if self.code.startswith('UNSUPPORTED') else 'hold', code=self.code, **self.details)

@dataclass(frozen=True)
class Limits:
    keys: int = MAX_KEYS
    encoded_bytes: int = MAX_ENCODED_BYTES
    batch: int = 16
    seconds: float = .05

SCHEMA = '''
CREATE TABLE IF NOT EXISTS rpc_compact_meta (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1), version INTEGER NOT NULL,
 cursor INTEGER NOT NULL, window_day TEXT NOT NULL, encoded_bytes INTEGER NOT NULL,
 keys_limit INTEGER NOT NULL, bytes_limit INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS rpc_compact_counts (
 clock TEXT NOT NULL, scope TEXT NOT NULL, day TEXT NOT NULL,
 n INTEGER NOT NULL CHECK(n>=0 AND n<=4096), PRIMARY KEY(clock,scope,day)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS rpc_compact_cells (
 clock TEXT NOT NULL CHECK(clock IN ('timestamp','recorded_at')),
 scope TEXT NOT NULL CHECK(scope IN ('day','life','seed')),
 day TEXT NOT NULL, dimension TEXT NOT NULL CHECK(length(dimension)<=768),
 stats TEXT NOT NULL CHECK(length(CAST(stats AS BLOB))<=4096),
 overflow INTEGER NOT NULL CHECK(overflow IN (0,1)),
 PRIMARY KEY(clock,scope,day,dimension)
) WITHOUT ROWID;
CREATE TRIGGER IF NOT EXISTS rpc_compact_cell_added AFTER INSERT ON rpc_compact_cells
 BEGIN INSERT INTO rpc_compact_counts VALUES(NEW.clock,NEW.scope,NEW.day,1)
 ON CONFLICT(clock,scope,day) DO UPDATE SET n=n+1; END;
CREATE TRIGGER IF NOT EXISTS rpc_compact_cell_removed AFTER DELETE ON rpc_compact_cells
 BEGIN UPDATE rpc_compact_counts SET n=n-1 WHERE clock=OLD.clock AND scope=OLD.scope AND day=OLD.day;
 DELETE FROM rpc_compact_counts WHERE clock=OLD.clock AND scope=OLD.scope AND day=OLD.day AND n=0; END;
CREATE INDEX IF NOT EXISTS idx_rpc_compact_expiry ON rpc_compact_cells(scope,day,clock,dimension);
CREATE INDEX IF NOT EXISTS idx_rpc_compact_overflow ON rpc_compact_cells(overflow);
CREATE TABLE IF NOT EXISTS rpc_compact_days (
 clock TEXT NOT NULL, chunk INTEGER NOT NULL CHECK(chunk>=0 AND chunk<=893),
 bits BLOB NOT NULL CHECK(length(bits)=512), PRIMARY KEY(clock,chunk)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS rpc_compact_resets (
 name TEXT PRIMARY KEY CHECK(length(name)<=64), payload TEXT NOT NULL CHECK(length(payload)<=8192)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS rpc_compact_pins (
 row_id INTEGER PRIMARY KEY, reason TEXT NOT NULL CHECK(length(reason)<=512)
);
CREATE TRIGGER IF NOT EXISTS rpc_compact_immutable_update BEFORE UPDATE ON rpc_metrics
 WHEN OLD.id <= (SELECT cursor FROM rpc_compact_meta WHERE singleton=1) OR NEW.id <= (SELECT cursor FROM rpc_compact_meta WHERE singleton=1)
 BEGIN SELECT RAISE(ABORT,'rpc_compact: materialized history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS rpc_compact_immutable_delete BEFORE DELETE ON rpc_metrics
 WHEN OLD.id <= (SELECT cursor FROM rpc_compact_meta WHERE singleton=1)
 BEGIN SELECT RAISE(ABORT,'rpc_compact: retirement requires separate authorization'); END;
CREATE TRIGGER IF NOT EXISTS rpc_compact_pin_delete BEFORE DELETE ON rpc_metrics
 WHEN EXISTS(SELECT 1 FROM rpc_compact_pins WHERE row_id=OLD.id)
 BEGIN SELECT RAISE(ABORT,'rpc_compact: pinned forensic evidence'); END;
CREATE TRIGGER IF NOT EXISTS rpc_compact_pin_update BEFORE UPDATE ON rpc_metrics
 WHEN EXISTS(SELECT 1 FROM rpc_compact_pins WHERE row_id=OLD.id)
 BEGIN SELECT RAISE(ABORT,'rpc_compact: pinned forensic evidence'); END;
CREATE TRIGGER IF NOT EXISTS rpc_compact_monotonic_insert AFTER INSERT ON rpc_metrics
 WHEN NEW.id <= (SELECT cursor FROM rpc_compact_meta WHERE singleton=1)
 BEGIN SELECT RAISE(ABORT,'rpc_compact: historical ID reuse'); END;
'''

def dumps(value):
    return json.dumps(value, separators=(',', ':'), ensure_ascii=True, allow_nan=False)

def day_at(now):
    return dt.datetime.fromtimestamp(now, dt.timezone.utc).date().isoformat()

def empty():
    return dict(n=0, spent=0, spent_n=0, saved=0, saved_n=0, paid_n=0,
                paid_sum=0, saved_positive=0, saved_positive_n=0, errors=0, limited=0,
                latency_sum=0., latency_n=0, latency_min=None, latency_max=None,
                saved_min=None, saved_max=None, first_timestamp=None,last_timestamp=None,
                first_recorded=None,last_recorded=None,day_min=None,day_max=None,
                skip_n=0,refresh_n=0,full_scan_n=0,none_n=0,hit_n=0,cache_saved=0,
                skip_saved=0,refresh_saved=0,skip_saved_n=0,refresh_saved_n=0)

def merge(a, b):
    result={}
    mins={'latency_min','saved_min','first_timestamp','first_recorded','day_min'}
    maxs={'latency_max','saved_max','last_timestamp','last_recorded','day_max'}
    for k in a:
        if k in mins|maxs:
            vals=[v for v in (a[k],b[k]) if v is not None]
            result[k]=(min(vals) if k in mins else max(vals)) if vals else None
        else:
            v=a[k]+b[k]
            if not math.isfinite(v) or (isinstance(v,int) and abs(v)>2**63-1):
                raise Hold('COUNTER_OVERFLOW',field=k)
            result[k]=v
    return result

def statistics(row):
    if row.get('recorded_bytes') is not None and row['recorded_bytes']>128:raise Hold('UNSUPPORTED_RECORDED_CLOCK_LENGTH')
    if not isinstance(row['timestamp'],(int,float)) or not math.isfinite(row['timestamp']):raise Hold('INVALID_EVENT_CLOCK')
    if row['status_code'] is not None and not isinstance(row['status_code'],int):raise Hold('UNSUPPORTED_STATUS_TYPE')
    s=empty(); s['n']=1
    for col,target in [('credits','spent'),('credits_saved','saved')]:
        v=row[col]
        if v is not None:
            if not isinstance(v,int): raise Hold('UNSUPPORTED_COST_TYPE',field=col)
            s[target]=v;s[target+'_n']=1
    s['paid_n']=int((row['credits'] or 0)>0)
    s['paid_sum']=row['credits'] if s['paid_n'] else 0
    s['saved_positive_n']=int((row['credits_saved'] or 0)>0)
    s['saved_positive']=row['credits_saved'] if s['saved_positive_n'] else 0
    s['saved_min']=s['saved_max']=row['credits_saved'] or 0
    s['errors']=int(row['status_code'] is not None and row['status_code']>=400)
    s['limited']=int(row['status_code']==429)
    lat=row['latency_ms']
    if lat is not None:
        if not isinstance(lat,(int,float)) or not math.isfinite(lat): raise Hold('INVALID_LATENCY')
        s.update(latency_sum=lat,latency_n=1,latency_min=lat,latency_max=lat)
    s['first_timestamp']=s['last_timestamp']=row['timestamp']
    s['first_recorded']=s['last_recorded']=row['recorded_at']
    s['day_min']=s['day_max']=row['recorded_day']
    action=row['cache_action']
    if action is not None and action!='none':s['cache_saved']=row['credits_saved'] or 0
    if action in ('skip','refresh','full_scan','none'):s[action+'_n']=1
    if action in ('skip','refresh'):
        s['hit_n']=1;s[action+'_saved']=row['credits_saved'] or 0
        s[action+'_saved_n']=int(row['credits_saved'] is not None)
    return s

class CompactTelemetry:
    def __init__(self, conn, limits=Limits()):
        self.conn,self.limits=conn,limits
        if not 8<=limits.keys<=MAX_KEYS or not 1<=limits.batch<=MAX_BATCH or not 0<limits.seconds<=1:
            raise ValueError('limits exceed qualified ceilings')
        if not 1<=limits.encoded_bytes<=MAX_ENCODED_BYTES:raise ValueError('byte bound')

    def install(self, now):
        """Explicit schema migration on a caller-authorized database, never automatic."""
        if self.conn.in_transaction:raise Hold('INSTALL_REQUIRES_IDLE_CONNECTION')
        first=self.conn.execute('SELECT id FROM rpc_metrics ORDER BY id LIMIT 1').fetchone()
        if first and first[0]<=0:raise Hold('NONPOSITIVE_SOURCE_ID')
        try:
            self.conn.executescript('BEGIN IMMEDIATE;'+SCHEMA)
            self.conn.execute('INSERT OR IGNORE INTO rpc_compact_meta VALUES(1,?,?,?,?,?,?)',
                              (VERSION,0,day_at(now),0,self.limits.keys,self.limits.encoded_bytes))
            self.conn.commit()
        except BaseException:
            self.conn.rollback();raise

    def meta(self):
        try:
            row=self.conn.execute('SELECT version,cursor,window_day,encoded_bytes,keys_limit,bytes_limit FROM rpc_compact_meta WHERE singleton=1').fetchone()
        except sqlite3.Error as error:
            raise Hold('SCHEMA_NOT_READY') from error
        if row is None or row[0]!=VERSION:raise Hold('SCHEMA_NOT_READY')
        if (row[4],row[5])!=(self.limits.keys,self.limits.encoded_bytes):raise Hold('LIMIT_CONFIGURATION_MISMATCH')
        return dict(zip(('version','cursor','window_day','encoded_bytes','keys_limit','bytes_limit'),row))

    def _put(self,clock,scope,day,key,s,overflow):
        row=self.conn.execute('SELECT stats,overflow FROM rpc_compact_cells WHERE clock=? AND scope=? AND day=? AND dimension=?',(clock,scope,day,key)).fetchone()
        if row:s=merge(json.loads(row[0]),s);overflow=overflow or row[1]
        encoded=dumps(s)
        if len(encoded)>MAX_CELL_BYTES:raise Hold('CELL_BYTE_LIMIT')
        old=len(row[0].encode())+len(key.encode())+64 if row else 0
        new=len(encoded.encode())+len(key.encode())+64
        self.conn.execute('UPDATE rpc_compact_meta SET encoded_bytes=encoded_bytes+? WHERE singleton=1',(new-old,))
        if self.meta()['encoded_bytes']>self.limits.encoded_bytes:raise Hold('AGGREGATE_BYTE_LIMIT')
        self.conn.execute('INSERT INTO rpc_compact_cells VALUES(?,?,?,?,?,?) ON CONFLICT(clock,scope,day,dimension) DO UPDATE SET stats=excluded.stats,overflow=excluded.overflow',(clock,scope,day,key,encoded,int(overflow)))

    def _key(self,row,clock,scope,day,pending=None):
        values=[row[k] for k in DIMS]
        valid=all((v is None or isinstance(v,int) and -1<=v<=599) if k=='status_code' else (v is None or isinstance(v,str) and len(v.encode())<=96 and v!='__overflow__') for k,v in zip(DIMS,values))
        key=dumps(values)
        group=(clock,scope,day)
        reserved=pending.setdefault(group,set()) if pending is not None else set()
        if key in reserved:return key,False
        if valid and len(key)<=768:
            exists=self.conn.execute('SELECT 1 FROM rpc_compact_cells WHERE clock=? AND scope=? AND day=? AND dimension=?',(clock,scope,day,key)).fetchone()
            # O(1) transactional metadata, independent of dimension cardinality.
            count=self.conn.execute('SELECT n FROM rpc_compact_counts WHERE clock=? AND scope=? AND day=?', (clock,scope,day)).fetchone()
            keys=count[0] if count else 0
            reserve=min(1024*1024,self.limits.encoded_bytes//8)
            if exists:return key,False
            if keys+len(reserved)<self.limits.keys-6 and self.meta()['encoded_bytes']<self.limits.encoded_bytes-reserve:
                reserved.add(key)
                return key,False
        # Six reserved bins preserve SQL cache-action filtering; unknown action explicit.
        action=row['cache_action']
        if action not in ('none','skip','refresh','full_scan',None):action='__overflow__'
        return dumps(['__overflow__']*6+[action]),True

    def _seen_day(self,clock,day):
        if day is None:return
        # SQLite's supported 0000..9999 date domain; year zero maps before Python epoch.
        year=int(day[:4]);ordinal=(dt.date.fromisoformat(day).toordinal() if year else int(self.conn.execute("SELECT julianday(?)-julianday('0001-01-01')+1",(day,)).fetchone()[0]))+366
        chunk,bit=divmod(ordinal,4096)
        row=self.conn.execute('SELECT bits FROM rpc_compact_days WHERE clock=? AND chunk=?',(clock,chunk)).fetchone()
        bits=bytearray(row[0] if row else bytes(512));bits[bit//8]|=1<<(bit%8)
        self.conn.execute('INSERT OR REPLACE INTO rpc_compact_days VALUES(?,?,?)',(clock,chunk,bytes(bits)))

    def observed_days(self,clock='recorded_at'):
        return sum(sum(b.bit_count() for b in row[0]) for row in self.conn.execute('SELECT bits FROM rpc_compact_days WHERE clock=?',(clock,)))

    def _trim(self,today):
        cutoff=(dt.date.fromisoformat(today)-dt.timedelta(days=30)).isoformat()
        # At most 128 expired cells per slice, not an unbounded cleanup transaction.
        old=self.conn.execute("SELECT clock,scope,day,dimension,stats FROM rpc_compact_cells WHERE scope='day' AND day<? ORDER BY day LIMIT 128",(cutoff,)).fetchall()
        for clock,scope,day,key,payload in old:
            self._put(clock,'seed',day,'[]',json.loads(payload),False)
            self.conn.execute('DELETE FROM rpc_compact_cells WHERE clock=? AND scope=? AND day=? AND dimension=?',(clock,scope,day,key))
            self.conn.execute('UPDATE rpc_compact_meta SET encoded_bytes=encoded_bytes-? WHERE singleton=1',(len(payload.encode())+len(key.encode())+64,))
        for clock in ('timestamp','recorded_at'):
            seeds=self.conn.execute("SELECT day,stats FROM rpc_compact_cells WHERE clock=? AND scope='seed' ORDER BY day DESC LIMIT 134",(clock,)).fetchall()
            for day,payload in seeds[6:]:
                self.conn.execute("DELETE FROM rpc_compact_cells WHERE clock=? AND scope='seed' AND day=?",(clock,day))
                self.conn.execute('UPDATE rpc_compact_meta SET encoded_bytes=encoded_bytes-? WHERE singleton=1',(len(payload.encode())+2+64,))
        return cutoff

    def _wal_guard(self):
        import os
        path=self.conn.execute('PRAGMA database_list').fetchone()[2]
        if path:
            try:size=os.stat(path+'-wal').st_size
            except FileNotFoundError:size=0
            if size>=64*1024*1024:raise Hold('WAL_BUDGET_HOLD',wal_bytes=size)

    def advance(self,now,limit=None,crash=None):
        """One keyset slice. SAVEPOINT keeps counters/cursor/pins atomic with caller.

        Failure rolls back this slice, including expiry. Raw records are never retired.
        Existing persisted IDs are event identities, not network request dedupe tokens.
        """
        limit=self.limits.batch if limit is None else limit
        if not 1<=limit<=self.limits.batch:raise ValueError('batch limit')
        self._wal_guard()
        start=time.monotonic();self.conn.execute('SAVEPOINT rpc_compact_slice')
        try:
            m=self.meta();today=day_at(now)
            if today<m['window_day']:raise Hold('CLOCK_REGRESSION')
            cutoff=self._trim(today)
            # Avoid silently exposing day groups left behind by bounded expiry.
            remaining=self.conn.execute("SELECT 1 FROM rpc_compact_cells WHERE scope='day' AND day<? LIMIT 1",(cutoff,)).fetchone()
            if remaining:
                self.conn.execute('RELEASE rpc_compact_slice')
                return dict(status='expiry_pending',cursor=m['cursor'],processed=0)
            rows=self.conn.execute(f'SELECT {SOURCE_SELECT} FROM rpc_metrics WHERE id>? ORDER BY id LIMIT ?',(m['cursor'],limit)).fetchall()
            names=SOURCE_NAMES
            processed=0;pending={};reservations={};seen=set();events=[]
            def collect(clock,scope,day,key,stats,overflow):
                k=(clock,scope,day,key)
                if k in pending:
                    old,flag=pending[k];pending[k]=(merge(old,stats),flag or overflow)
                else:pending[k]=(stats,overflow)
            for values in rows:
                if time.monotonic()-start>self.limits.seconds:
                    if not processed:raise Hold('SLICE_TIME_LIMIT',retry_smaller_batch=True)
                    break
                row=dict(zip(names,values));s=statistics(row)
                if row['event_day'] is None:raise Hold('INVALID_EVENT_CLOCK',row_id=row['id'])
                for clock,day in [('timestamp',row['event_day']),('recorded_at',row['recorded_day'])]:
                    if clock=='recorded_at' and row['recorded_at'] is None:continue
                    cs=s.copy()
                    if clock=='timestamp':
                        cs['first_recorded']=cs['last_recorded']=dt.datetime.fromtimestamp(row['timestamp'],dt.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                        cs['day_min']=cs['day_max']=day
                    key,overflow=self._key(row,clock,'life','',reservations)
                    collect(clock,'life','',key,cs,overflow);seen.add((clock,day))
                    if day is not None and cutoff<=day<=today:
                        key,overflow=self._key(row,clock,'day',day,reservations)
                        collect(clock,'day',day,key,cs,overflow)
                    elif day is not None and day<cutoff:collect(clock,'seed',day,'[]',cs,False)
                    elif day is not None and day>today:raise Hold('FUTURE_CLOCK',row_id=row['id'])
                events.append((row,s));processed+=1
            for (clock,scope,day,key),(stats,overflow) in pending.items():
                self._put(clock,scope,day,key,stats,overflow)
                if crash:crash(events[-1][0]['id'])
            for clock,day in seen:self._seen_day(clock,day)
            # One update per reset per slice, not one database update per event.
            for name,payload in self.conn.execute('SELECT name,payload FROM rpc_compact_resets').fetchall():
                reset=json.loads(payload)
                for row,stats in events:
                    if row['id']>reset['watermark'] and row['timestamp']>reset['timestamp']:
                        reset['stats']=merge(reset['stats'],stats)
                self.conn.execute('UPDATE rpc_compact_resets SET payload=? WHERE name=?',(dumps(reset),name))
            self._trim(today)
            if events:self.conn.execute('UPDATE rpc_compact_meta SET cursor=? WHERE singleton=1',(events[-1][0]['id'],))
            self.conn.execute('UPDATE rpc_compact_meta SET window_day=? WHERE singleton=1',(today,))
            self.conn.execute('RELEASE rpc_compact_slice')
            return dict(status='ok',cursor=self.meta()['cursor'],processed=processed)
        except BaseException:
            self.conn.execute('ROLLBACK TO rpc_compact_slice');self.conn.execute('RELEASE rpc_compact_slice');raise

    def assert_ready(self,now):
        m=self.meta()
        if self.conn.execute('SELECT 1 FROM rpc_metrics WHERE id>? LIMIT 1',(m['cursor'],)).fetchone():raise Hold('BACKFILL_INCOMPLETE',cursor=m['cursor'])
        if m['window_day']!=day_at(now):raise Hold('WINDOW_NOT_ADVANCED')
        cutoff=(dt.date.fromisoformat(day_at(now))-dt.timedelta(days=30)).isoformat()
        if self.conn.execute("SELECT 1 FROM rpc_compact_cells WHERE scope='day' AND day<? LIMIT 1",(cutoff,)).fetchone():raise Hold('EXPIRY_PENDING')

    def totals(self,clock='timestamp'):
        if clock not in ('timestamp','recorded_at'):raise ValueError('clock')
        s=empty();overflow=False
        for payload,o in self.conn.execute("SELECT stats,overflow FROM rpc_compact_cells WHERE clock=? AND scope='life'",(clock,)):
            s=merge(s,json.loads(payload));overflow=overflow or bool(o)
        return dict(stats=s,dimensions_overflow=overflow,days_tracked=self.observed_days(clock))

    def pin(self,row_id,reason):
        if not isinstance(reason,str) or not reason or len(reason)>512:raise ValueError('pin reason')
        if not self.conn.execute('SELECT 1 FROM rpc_metrics WHERE id=?',(row_id,)).fetchone():raise Hold('PIN_ROW_MISSING')
        if not self.conn.execute('SELECT 1 FROM rpc_compact_pins WHERE row_id=?',(row_id,)).fetchone() and self.conn.execute('SELECT COUNT(*) FROM rpc_compact_pins').fetchone()[0]>=MAX_PINS:raise Hold('PIN_CAPACITY')
        self.conn.execute('INSERT OR REPLACE INTO rpc_compact_pins VALUES(?,?)',(row_id,reason))

    def reset(self,name,timestamp,elapsed_origin,provider_baseline,now):
        """Explicit reset snapshot; separate boundary/origin/provider baseline."""
        if not name or len(name)>64:raise ValueError('reset name')
        self.meta()
        if timestamp<now-DETAIL_SECONDS:raise Hold('UNSUPPORTED_OLD_RESET_WITHOUT_CHECKPOINT')
        if timestamp>now:raise Hold('FUTURE_RESET')
        if self.conn.execute('SELECT 1 FROM rpc_compact_resets WHERE name=?',(name,)).fetchone():raise Hold('RESET_ID_EXISTS')
        if self.conn.execute('SELECT COUNT(*) FROM rpc_compact_resets').fetchone()[0]>=MAX_RESETS:raise Hold('RESET_CAPACITY')
        upper=self.meta()['cursor']
        highest=self.totals('timestamp')['stats']['last_timestamp']
        top=(highest,) if highest is not None else None
        p=dict(timestamp=timestamp,elapsed_origin=elapsed_origin,provider_baseline=provider_baseline,watermark=upper,stats=empty(),
               bootstrap=dict(last_timestamp=top[0] if top else timestamp,last_id=-1,done=not bool(top)))
        self.conn.execute('INSERT INTO rpc_compact_resets VALUES(?,?)',(name,dumps(p)))
        self.bootstrap_reset(name)

    def _bounded_source(self,sql,args):
        start=time.monotonic();steps=[0]
        def cancel():
            steps[0]+=1000
            return steps[0]>100000 or time.monotonic()-start>.05
        self.conn.set_progress_handler(cancel,1000)
        try:return self.conn.execute(sql,args).fetchall()
        except sqlite3.OperationalError as error:
            if str(error)=='interrupted':raise Hold('RESET_INDEX_WORK_LIMIT') from error
            raise
        finally:self.conn.set_progress_handler(None,0)

    def bootstrap_reset(self,name,limit=128):
        """Bounded existing timestamp-index traversal, splitting timestamp ties.

        Bootstrap IDs <= watermark and live IDs > watermark are disjoint. No OFFSET,
        full-table scan or long-lived snapshot; source fence prevents prefix mutation.
        """
        if not 1<=limit<=MAX_BATCH:raise ValueError('reset batch limit')
        self.conn.execute('SAVEPOINT rpc_compact_reset_slice')
        try:
            p=self.reset_totals(name);b=p.get('bootstrap')
            if not b or b['done']:
                self.conn.execute('RELEASE rpc_compact_reset_slice');return
            select=SOURCE_SELECT
            names=SOURCE_NAMES
            rows=[]
            if b['last_timestamp']>p['timestamp']:
                rows=self._bounded_source(f'SELECT {select} FROM rpc_metrics WHERE timestamp=? AND id>? ORDER BY id LIMIT ?',
                    (b['last_timestamp'],b['last_id'],limit))
                if len(rows)<limit:
                    rows+=self._bounded_source(f'SELECT {select} FROM rpc_metrics WHERE timestamp>? AND timestamp<? ORDER BY rpc_metrics.timestamp DESC,rpc_metrics.id ASC LIMIT ?',
                        (p['timestamp'],b['last_timestamp'],limit-len(rows)))
            for values in rows:
                if values[0]<=p['watermark']:p['stats']=merge(p['stats'],statistics(dict(zip(names,values))))
            if rows:b['last_timestamp'],b['last_id']=rows[-1][1],rows[-1][0]
            if len(rows)<limit:b['done']=True
            self.conn.execute('UPDATE rpc_compact_resets SET payload=? WHERE name=?',(dumps(p),name))
            self.conn.execute('RELEASE rpc_compact_reset_slice')
        except BaseException:
            self.conn.execute('ROLLBACK TO rpc_compact_reset_slice');self.conn.execute('RELEASE rpc_compact_reset_slice');raise

    def reset_totals(self,name):
        row=self.conn.execute('SELECT payload FROM rpc_compact_resets WHERE name=?',(name,)).fetchone()
        if not row:raise Hold('RESET_NOT_FOUND')
        return json.loads(row[0])

    def window(self,now,*,hours=None,start_day=None,end_day=None,p95=False):
        if p95 and start_day is not None:raise Hold('UNSUPPORTED_HISTORICAL_P95',available='recent session percentiles only')
        if hours is not None:
            if isinstance(hours,bool) or not isinstance(hours,(int,float)) or not math.isfinite(hours) or not 0<hours<=168:raise Hold('UNSUPPORTED_HISTORICAL_ROLLING_WINDOW',maximum_hours=168,alternative='UTC day-aligned query')
            return dict(mode='detail',cutoff=now-hours*3600,clock='timestamp')
        if start_day is None or end_day is None:raise Hold('UNSUPPORTED_WINDOW')
        try:start=dt.date.fromisoformat(start_day);end=dt.date.fromisoformat(end_day)
        except (TypeError,ValueError):raise Hold('UNSUPPORTED_DAY_FORMAT')
        if start_day!=start.isoformat() or end_day!=end.isoformat():raise Hold('UNSUPPORTED_DAY_FORMAT')
        today=dt.date.fromisoformat(day_at(now))
        if not today-dt.timedelta(days=30)<=start<=end<=today:raise Hold('UNSUPPORTED_DAILY_HORIZON',inclusive_labels=DAY_LABELS)
        return dict(mode='daily',start_day=start_day,end_day=end_day,clock='recorded_at')

    def _view_input(self,scope,clock,include_seeds=False,start_day=None,end_day=None):
        clauses=['clock=?','scope=?'];params=[clock,scope]
        if start_day is not None:clauses.append('day>=?');params.append(start_day)
        if end_day is not None:clauses.append('day<=?');params.append(end_day)
        rows=self.conn.execute('SELECT day,dimension,stats,overflow FROM rpc_compact_cells WHERE '+' AND '.join(clauses),params).fetchall()
        if include_seeds:
            rows+=self.conn.execute("SELECT day,dimension,stats,overflow FROM rpc_compact_cells WHERE clock=? AND scope='seed' ORDER BY day LIMIT 6",(clock,)).fetchall()
        return rows

    def report(self,name,now,*,days=30,clock='recorded_at'):
        """Framework-neutral compatibility adapter; preserves installed view columns.

        Results carry explicit overflow/incomplete states. No helper catches HOLD and
        disguises it as an empty result. No arbitrary SQL or second-level old window.
        """
        if name not in VIEWS:raise Hold('UNSUPPORTED_VIEW',view=name)
        if clock not in ('recorded_at','timestamp'):raise ValueError('clock')
        if isinstance(days,bool) or not isinstance(days,int) or not 0<=days<=30:raise Hold('UNSUPPORTED_DAILY_HORIZON',maximum_days=30)
        # A read transaction freezes readiness, cursor, counters and input together.
        self.conn.execute('SAVEPOINT rpc_compact_read')
        try:
            self.assert_ready(now)
            scope='life' if name in LIFETIME_VIEWS else 'day'
            trend=name in ('v_rpc_efficiency_trend_7day','v_rpc_rolling_7day_average')
            rows=self._view_input(scope,clock,include_seeds=trend)
            schema=self.conn.execute("SELECT sql FROM sqlite_master WHERE type='view' AND name=?",(name,)).fetchone()
            if not schema:raise Hold('VIEW_DEFINITION_MISSING',view=name)
            import hashlib
            if hashlib.sha256(schema[0].encode()).hexdigest()!=VIEW_HASHES[name]:raise Hold('VIEW_CONTRACT_CHANGED',view=name)
            sql=schema[0].split(' AS\n',1)[1]
            replacements={
                'DATE(MIN(recorded_at))':'DATE(MIN(first_recorded))',
                'DATE(MAX(recorded_at))':'DATE(MAX(last_recorded))',
                'MIN(DATE(recorded_at))':'MIN(day_min)',
                'MAX(DATE(recorded_at))':'MAX(day_max)',
                'COUNT(DISTINCT DATE(recorded_at))':str(self.observed_days(clock)),
                'SUM(COALESCE(credits_saved, 0))':'SUM(saved)',
                'SUM(COALESCE(credits, 0))':'SUM(spent)',
                'AVG(COALESCE(credits_saved, 0))':'(1.0*SUM(saved)/SUM(n))',
                'MIN(COALESCE(credits_saved, 0))':'MIN(saved_min)',
                'MAX(COALESCE(credits_saved, 0))':'MAX(saved_max)',
                "SUM(CASE WHEN cache_action IN ('skip', 'refresh') THEN 1 ELSE 0 END)":'SUM(hit_n)',
                'COUNT(*)':'COALESCE(SUM(n),0)', 'DATE(recorded_at)':'day',
                'FROM rpc_metrics':'FROM compact', 'recorded_at IS NOT NULL':'1=1',
            }
            for action in ('skip','refresh','full_scan','none'):
                replacements[f"SUM(CASE WHEN cache_action = '{action}' THEN 1 ELSE 0 END)"]=f'SUM({action}_n)'
            for action in ('skip','refresh'):
                replacements[f"SUM(CASE WHEN cache_action = '{action}' THEN credits_saved ELSE 0 END)"]=f"SUM(CASE WHEN cache_action = '{action}' THEN saved_nullable ELSE 0 END)"
            for old,new in replacements.items():sql=sql.replace(old,new)
            # Bounded in-memory query workspace; never a disk temporary database.
            mem=sqlite3.connect(':memory:');mem.row_factory=sqlite3.Row
            mem.execute('PRAGMA temp_store=MEMORY')
            mem.execute('PRAGMA cache_size=-2048')
            try:
                fields=['day',*DIMS,*empty().keys(),'spent_nullable','saved_nullable']
                mem.execute('CREATE TABLE compact ('+','.join('"'+f+'"' for f in fields)+')')
                for day,key,payload,overflow in rows:
                    s=json.loads(payload)
                    dims=json.loads(key) if key!='[]' else [None]*len(DIMS)
                    values=[day,*dims,*s.values(),s['spent'] if s['spent_n'] else None,s['saved'] if s['saved_n'] else None]
                    mem.execute('INSERT INTO compact VALUES('+','.join('?' for _ in values)+')',values)
                start=time.monotonic();steps=[0]
                def bounded():
                    steps[0]+=1000
                    return steps[0]>2_000_000 or time.monotonic()-start>.25
                mem.set_progress_handler(bounded,1000)
                try:result=[dict(r) for r in mem.execute(sql)]
                except sqlite3.OperationalError as e:
                    if str(e)=='interrupted':raise Hold('REPORT_WORK_LIMIT') from e
                    raise
                if scope=='day':
                    cutoff=(dt.date.fromisoformat(day_at(now))-dt.timedelta(days=days)).isoformat()
                    result=[r for r in result if r['day'] is not None and r['day']>=cutoff]
                return dict(status='ok',data=result,dimensions_overflow=any(r[3] for r in rows),clock=clock,inclusive_labels=days+1,partial_day=day_at(now),lifetime_scope='retained instrumented rows')
            finally:mem.close()
        finally:self.conn.execute('RELEASE rpc_compact_read')

    def seed_reset(self,name,timestamp,elapsed_origin,provider_baseline):
        """Initial migration only: capture an existing reset BEFORE cursor advances.

        Subsequent backfill certifies it in the same slices as lifetime. Until the
        source is caught up, reset accounting returns BACKFILL_INCOMPLETE via API.
        """
        if self.meta()['cursor']!=0:raise Hold('RESET_SEED_REQUIRES_INITIAL_BACKFILL')
        if not name or len(name)>64:raise ValueError('reset name')
        if self.conn.execute('SELECT 1 FROM rpc_compact_resets WHERE name=?',(name,)).fetchone():raise Hold('RESET_ID_EXISTS')
        if self.conn.execute('SELECT COUNT(*) FROM rpc_compact_resets').fetchone()[0]>=MAX_RESETS:raise Hold('RESET_CAPACITY')
        p=dict(timestamp=timestamp,elapsed_origin=elapsed_origin,provider_baseline=provider_baseline,watermark=0,stats=empty())
        self.conn.execute('INSERT INTO rpc_compact_resets VALUES(?,?)',(name,dumps(p)))

    def reset_report(self,name,now,provider_used):
        self.conn.execute('SAVEPOINT rpc_compact_reset_read')
        try:
            self.assert_ready(now);p=self.reset_totals(name)
            if name=='recorder_active':
                values=recorder_state_snapshot(self.conn)
                try:current=(float(values['comparison_reset_timestamp']),dt.datetime.fromisoformat(values['comparison_reset_time']).timestamp(),int(values['baseline_helius_credits_today']))
                except (KeyError,ValueError,TypeError):raise Hold('INVALID_RECORDER_BASELINE')
                if current!=(p['timestamp'],p['elapsed_origin'],p['provider_baseline']):raise Hold('RESET_GENERATION_NOT_CERTIFIED')
            if p.get('bootstrap') and not p['bootstrap']['done']:raise Hold('RESET_BACKFILL_INCOMPLETE')
            s=p['stats']
            elapsed=max(now-p['elapsed_origin'],1)
            billed=max(0,provider_used-p['provider_baseline'])
            return dict(status='ok',local_credits_since_reset=s['spent'],tracked_calls=s['paid_n'],
                        helius_credits_since_reset=billed,untracked_usage=max(0,billed-s['spent']),
                        comparison_diff=billed-s['spent'],comparison_coverage_pct=round(100*s['spent']/billed,2) if billed else 0,
                        credits_monthly_estimate=int(s['spent']/elapsed*86400*30),
                        credits_burn_rate_per_minute=round(s['spent']/max(elapsed/60,1),2),
                        saved_credits=s['cache_saved'],comparison_reset_timestamp=p['timestamp'],
                        comparison_reset_elapsed_origin=p['elapsed_origin'])
        finally:self.conn.execute('RELEASE rpc_compact_reset_read')


def advance_if_enabled(conn,now):
    """Recorder hook, disabled by default. Never installs a schema or starts work.

    Summary failure must not discard detailed telemetry: the slice savepoint rolls
    back independently. Lag is explicit at every compact reporting boundary.
    """
    import os
    if os.environ.get('RPC_COMPACT_ENABLED','0')!='1':return None
    try:
        store=CompactTelemetry(conn,Limits(batch=MAX_BATCH))
        sync_recorder_reset(store,now)
        start=time.monotonic();processed=0
        result=None
        for _ in range(4):
            if time.monotonic()-start>.25:break
            result=store.advance(now,limit=MAX_BATCH)
            processed+=result['processed']
            if result['status']!='ok' or not result['processed']:break
        return dict(result,processed=processed)
    except Exception as e:
        # The detailed INSERT/commit must survive any summary-specific failure.
        return dict(status='hold',code=e.code if isinstance(e,Hold) else 'COMPACT_UPDATE_UNAVAILABLE')


def sync_recorder_reset(store,now):
    """Mirror the existing recorder's active persisted baseline, never invent it."""
    exists=store.conn.execute("SELECT 1 FROM sqlite_master WHERE name='rpc_metrics_state'").fetchone()
    if not exists:return
    values=recorder_state_snapshot(store.conn)
    if len(values)!=3:return
    try:
        boundary=float(values['comparison_reset_timestamp'])
        origin=dt.datetime.fromisoformat(values['comparison_reset_time']).timestamp()
        baseline=int(values['baseline_helius_credits_today'])
    except (ValueError,TypeError):raise Hold('INVALID_RECORDER_BASELINE')
    found=store.conn.execute("SELECT payload FROM rpc_compact_resets WHERE name='recorder_active'").fetchone()
    if found:
        p=json.loads(found[0])
        if (p['timestamp'],p['elapsed_origin'],p['provider_baseline'])==(boundary,origin,baseline):
            store.bootstrap_reset('recorder_active');return
    # Replace only the active checkpoint, matching the existing single-baseline state.
    # Explicitly named forensic checkpoints are never rotated or discarded here.
    store.conn.execute('SAVEPOINT compact_sync_reset')
    try:
        store.conn.execute("DELETE FROM rpc_compact_resets WHERE name='recorder_active'")
        if store.meta()['cursor']==0:
            store.seed_reset('recorder_active',boundary,origin,baseline)
        else:
            store.reset('recorder_active',boundary,origin,baseline,now)
        store.conn.execute('RELEASE compact_sync_reset')
    except BaseException:
        store.conn.execute('ROLLBACK TO compact_sync_reset');store.conn.execute('RELEASE compact_sync_reset');raise


def recorder_compact_stats(path,cutoff=None):
    """Read-only counter bridge. Validate the exact recorder baseline generation."""
    import os
    from pathlib import Path
    if os.environ.get('RPC_COMPACT_ENABLED','0')!='1':return None
    c=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=5)
    try:
        c.execute('SAVEPOINT compact_recorder_read');s=CompactTelemetry(c);s.assert_ready(time.time())
        if cutoff is None:return s.totals('timestamp')['stats']
        p=s.reset_totals('recorder_active')
        row=c.execute("SELECT value FROM rpc_metrics_state WHERE key='comparison_reset_time'").fetchone()
        baseline=c.execute("SELECT value FROM rpc_metrics_state WHERE key='baseline_helius_credits_today'").fetchone()
        if not row or not baseline or (p['timestamp'],p['elapsed_origin'],p['provider_baseline'])!=(cutoff,dt.datetime.fromisoformat(row[0]).timestamp(),int(baseline[0])):
            raise Hold('RESET_GENERATION_NOT_CERTIFIED')
        if p.get('bootstrap') and not p['bootstrap']['done']:raise Hold('RESET_BACKFILL_INCOMPLETE')
        return p['stats']
    finally:c.close()


def require_detail_window(hours,reset_timestamp=None,cutoff=None):
    import os
    if os.environ.get('RPC_COMPACT_ENABLED','0')!='1':return
    actual=cutoff if cutoff is not None else reset_timestamp if hours==24 and reset_timestamp is not None else time.time()-hours*3600
    if actual<time.time()-DETAIL_SECONDS:
        raise Hold('UNSUPPORTED_HISTORICAL_ROLLING_WINDOW',maximum_hours=168,alternative='day-aligned report or certified reset totals')


def recorder_state_snapshot(conn):
    return dict(conn.execute("SELECT key,value FROM rpc_metrics_state WHERE key IN ('comparison_reset_timestamp','comparison_reset_time','baseline_helius_credits_today')"))


def write_recorder_state(conn,values):
    """Atomic reset generation; the caller retains write-lane/connection ownership."""
    conn.execute('SAVEPOINT rpc_compact_reset_generation')
    try:
        conn.executemany('''INSERT INTO rpc_metrics_state(key,value,updated_at)
            VALUES(?,?,CURRENT_TIMESTAMP) ON CONFLICT(key) DO UPDATE SET
            value=excluded.value,updated_at=CURRENT_TIMESTAMP''',list(values.items()))
        conn.execute('RELEASE rpc_compact_reset_generation')
    except BaseException:
        conn.execute('ROLLBACK TO rpc_compact_reset_generation')
        conn.execute('RELEASE rpc_compact_reset_generation');raise
