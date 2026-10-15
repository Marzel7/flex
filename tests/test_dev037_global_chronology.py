"""Canonical chronology contracts; read-only SELECT fixtures, no DB writes."""
import ast
from pathlib import Path
import sqlite3
import subprocess

from src.ops.operator_routes import _recent_operation_tokens

ROOT=Path(__file__).resolve().parents[1]

def test_global_order_ties_no_operation_quota_select_only():
    # Canonical membership is unique by mint. Construct rows in memory, not
    # through schema/INSERT writes; operation interleaving is deliberate.
    membership=[(f'm{i:02}', 'watchtower' if i%7==0 else 'byzantine',i//2) for i in range(55)]
    connection=sqlite3.connect(':memory:')
    try:
        connection.execute('PRAGMA query_only=ON')
        values=','.join('(?,?,?)' for _ in membership)
        chosen=connection.execute(
            f'WITH membership(mint,operation_id,assigned_at) AS (VALUES {values}) '
            'SELECT mint,operation_id,assigned_at FROM membership ORDER BY assigned_at DESC,mint DESC LIMIT 20',
            tuple(value for row in membership for value in row)).fetchall()
    finally:
        connection.close()
    assert chosen==sorted(membership,key=lambda row:(row[2],row[0]),reverse=True)[:20]
    rows=[dict(mint=m,operation_id=o,assignment_timestamp=t) for m,o,t in chosen]
    history=_recent_operation_tokens(rows+rows[:2],now=1792100000)
    assert len(history)==20 and len({r['mint'] for r in history})==20
    assert [(r['mint'],r['operation_id'],r['assignment_timestamp']) for r in history]==chosen
    assert sum(r['operation_id']=='watchtower' for r in history)==3

def test_no_legacy_substitution_and_unchanged_direct_dependencies():
    source=(ROOT/'src/ops/operator_routes.py').read_text()
    assert 'byzantine_facts =' not in source
    assert 'ORDER BY assigned_at DESC,mint DESC LIMIT' in source
    assert 'rows = projected' in source
    previous=subprocess.check_output(['git','show','7d5243d0:src/ops/operator_routes.py'],cwd=ROOT,text=True)
    def function(text,name):
        return ast.dump(next(n for n in ast.parse(text).body if isinstance(n,ast.FunctionDef) and n.name==name))
    for name in ['operator_coverage_24h','_authoritative_live','_selected_monitor_facts','_recent_operation_tokens']:
        assert function(previous,name)==function(source,name)
    for path in ['src/dev_runtime.py','templates/operators_index.html','templates/system_health_dashboard.html']:
        assert (ROOT/path).read_bytes()==subprocess.check_output(['git','show','7d5243d0:'+path],cwd=ROOT)
