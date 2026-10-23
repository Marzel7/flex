import ast,hashlib,json,subprocess,tempfile
from pathlib import Path
r=Path('/Users/kevinkeaveney/.codex/worktrees/dev004-first-v2-prototype/flex');out=Path(tempfile.mkdtemp(prefix='dev004-listener-reconcile-',dir='/private/tmp'));target=out/'composition';target.mkdir()
installed=Path('/Users/kevinkeaveney/Production/flex-listener');dev=r/'runtime/listener';rel='src/core/pumpfun_curve_listener.py'
a=(installed/rel).read_text();b=(dev/rel).read_text();fn=lambda s:next(n for n in ast.parse(s).body if isinstance(n,ast.FunctionDef) and n.name=='_ensure_webhook_birth_queue_schema')
x,y=fn(a),fn(b);lines=a.splitlines(keepends=True);lines[x.lineno-1:x.end_lineno]=b.splitlines(keepends=True)[y.lineno-1:y.end_lineno];composed=''.join(lines);ast.parse(composed)
for rel in ['src/core/pumpfun_curve_listener.py','src/core/pumpportal_birth_audit.py','src/core/birth_retry_quarantine.py','src/core/pumpportal_receipt_maintenance.py','scripts/maintain_pumpportal_compact_receipts.py']:
 p=target/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(composed if rel.endswith('/pumpfun_curve_listener.py') else (installed/rel).read_text())
tests=out/'tests';tests.mkdir();(tests/'__init__.py').write_text('')
for name in ['test_pumpportal_numeric_parser.py','test_receipt_failure_accounting.py']:
 (tests/name).write_bytes(subprocess.check_output(['git','-C',str(r),'show','65da0e22:tests/'+name]))
for rev,rel,key in [('a766d7e9','runtime/listener/src/core/pumpfun_curve_listener.py','numeric-baseline.py.txt'),('ce98ec0','runtime/listener/src/core/pumpportal_birth_audit.py','receipt-baseline.py.txt')]:
 (out/key).write_bytes(subprocess.check_output(['git','-C',str(r),'show',rev+':'+rel]))
e={'dev_revision':subprocess.check_output(['git','-C',str(r),'rev-parse','HEAD'],text=True).strip(),'accepted_revision':'65da0e227727da5fb19a75950cae8de1c05bc9ab','composition':{}}
for rel in ['src/core/pumpfun_curve_listener.py','src/core/pumpportal_birth_audit.py','src/core/birth_retry_quarantine.py','src/core/pumpportal_receipt_maintenance.py','scripts/maintain_pumpportal_compact_receipts.py']:
 accepted=subprocess.check_output(['git','-C',str(r),'show','65da0e22:runtime/listener/'+rel]);p=target/rel;e['composition'][rel]={'accepted_sha256':hashlib.sha256(accepted).hexdigest(),'installed_sha256':hashlib.sha256((installed/rel).read_bytes()).hexdigest(),'prototype_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'dev_sha256':hashlib.sha256((dev/rel).read_bytes()).hexdigest() if (dev/rel).exists() else None}
e['prototype_scope']='Installed accepted composition plus only the accepted DEV004 _ensure_webhook_birth_queue_schema function; no runtime installation.'
(out/'manifest.json').write_text(json.dumps(e,indent=2)+'\n');print(out)
