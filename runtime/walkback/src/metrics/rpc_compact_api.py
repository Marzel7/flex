"""Opt-in compatibility boundaries. Standard library only; no app startup I/O."""
import datetime as dt
import json
import os
import re
import time
from src.metrics.rpc_compact import CompactTelemetry, Hold, VIEWS, empty, merge


def enabled():return os.environ.get('RPC_COMPACT_ENABLED','0')=='1'

def unsupported_hours(hours):
    return Hold('UNSUPPORTED_HISTORICAL_ROLLING_WINDOW',maximum_hours=168,requested_hours=hours,alternative='UTC day-aligned /metrics/rpc/database/daily').response()

class CompactReadConnection:
    """The savings/efficiency SELECT vocabulary, with existing dictionary fields.

    Holds a read snapshot until close; returns no silently truncated history.
    Explicit metadata is surfaced by route response headers.
    """
    def __init__(self,conn,now=None):
        self.conn=conn;self.now=time.time() if now is None else now;self.rows=[]
        self.dimensions_overflow=False
        try:
            self.conn.execute('SAVEPOINT rpc_compact_api_snapshot')
            CompactTelemetry(conn).assert_ready(self.now)
        except BaseException:
            conn.close();raise
    def cursor(self):return self
    def execute(self,sql,parameters=()):
        try:
            match=re.search(r'FROM\s+(v_rpc_[a-z_0-9]+)',sql,re.I)
            if not match:raise Hold('UNSUPPORTED_COMPACT_QUERY')
            name=match.group(1)
            if parameters:raise Hold('UNSUPPORTED_COMPACT_PARAMETERS')
            days_match=re.search(r"DATE\('now',\s*'-(\d+) days'\)",sql,re.I)
            days=int(days_match.group(1)) if days_match else 30
            if name in VIEWS:
                result=CompactTelemetry(self.conn).report(name,self.now,days=days)
                self.rows=result['data'];self.dimensions_overflow=result['dimensions_overflow']
            elif name in ('v_rpc_dashboard_24h','v_rpc_efficiency_24h'):
                # These predicates fit entirely within retained detail; no summary union.
                self.conn.row_factory=__import__('sqlite3').Row
                self.rows=[dict(r) for r in self.conn.execute(sql)]
            else:raise Hold('UNSUPPORTED_COMPACT_QUERY',view=name)
            if re.search(r'ORDER BY day ASC',sql,re.I):self.rows.sort(key=lambda r:r['day'])
            return self
        except BaseException:
            self.close();raise
    def fetchall(self):rows=self.rows;self.rows=[];return rows
    def fetchone(self):return self.rows.pop(0) if self.rows else None
    def close(self):self.conn.close()


def database_daily(conn,now,start_day=None,end_day=None,*,lifetime=False):
    """New day-aligned historical endpoint, using timestamp-day summaries.

    Preserves database_metrics field names, source/section weighting and null sums.
    Adds explicit clock/window/overflow metadata rather than claiming rolling hours.
    """
    store=CompactTelemetry(conn)
    window=dict(mode='lifetime',clock='timestamp') if lifetime else store.window(now,start_day=start_day,end_day=end_day)
    window['clock']='timestamp'
    conn.execute('SAVEPOINT rpc_compact_database_day')
    try:
        store.assert_ready(now)
        rows=store._view_input('life' if lifetime else 'day','timestamp',start_day=start_day,end_day=end_day)
        total=empty();methods={};sections={};sources={};overflow=False
        for _,key,payload,o in rows:
            s=json.loads(payload);section,provider,method,source,layer,status,action=json.loads(key)
            total=merge(total,s);overflow=overflow or bool(o)
            methods[method]=merge(methods.get(method,empty()),s)
            sections[section]=merge(sections.get(section,empty()),s)
            source_sections=sources.setdefault(source,{})
            source_sections[section]=merge(source_sections.get(section,empty()),s)
        def counts(s):return dict(requests=s['n'],credits=s['spent'] if s['spent_n'] else None)
        by_source={}
        for source,groups in sources.items():
            combined=empty()
            for s in groups.values():combined=merge(combined,s)
            averages=[s['latency_sum']/s['latency_n'] if s['latency_n'] else 0 for s in groups.values()]
            by_source[source]=dict(**counts(combined),errors=combined['errors'],rate_limits_429=combined['limited'],sections={k:s['n'] for k,s in groups.items()},avg_latency_ms=round(sum(averages)/len(averages),2))
        summary=dict(total_requests=total['n'],total_credits=total['spent'] if total['spent_n'] else None,total_errors=total['errors'],total_429s=total['limited'],first_timestamp=total['first_timestamp'],last_timestamp=total['last_timestamp'])
        return dict(status='ok',query_window=window,dimensions_overflow=overflow,database_metrics=dict(summary=summary,by_method={k:counts(v) for k,v in methods.items()},by_source_file=by_source,by_section={k:counts(v) for k,v in sections.items()}))
    finally:conn.execute('RELEASE rpc_compact_database_day')


def register_flask_contract(app,db_path,connect):
    """Register explicit HOLD errors/overflow metadata without changing success bodies."""
    from flask import jsonify,request,g
    @app.errorhandler(Hold)
    def compact_hold(error):return jsonify(error.response()),409
    @app.before_request
    def compact_request():
        if not enabled() or not request.path.startswith(('/api/rpc-savings/','/api/rpc-efficiency/')):return None
        days=request.args.get('days',30,type=int)
        if not 0<=days<=30:return jsonify(Hold('UNSUPPORTED_DAILY_HORIZON',maximum_days=30).response()),400
        conn=connect(db_path,timeout=5,read_only=True)
        try:
            s=CompactTelemetry(conn);conn.execute('SAVEPOINT compact_request');s.assert_ready(time.time())
            g.rpc_compact_overflow=bool(conn.execute('SELECT 1 FROM rpc_compact_cells WHERE overflow=1 LIMIT 1').fetchone())
        finally:conn.close()
    @app.after_request
    def compact_metadata(response):
        if enabled() and request.path.startswith(('/api/rpc-savings/','/api/rpc-efficiency/')):
            response.headers['X-RPC-Telemetry-Contract']='compact-v1'
            response.headers['X-RPC-Dimensions-Overflow']=('1' if g.rpc_compact_overflow else '0') if hasattr(g,'rpc_compact_overflow') else 'unknown'
        return response
