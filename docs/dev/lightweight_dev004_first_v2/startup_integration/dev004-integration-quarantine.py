from pathlib import Path
s=Path('/private/tmp/dev004-schema-bootstrap-child.py').read_text();exec(compile(s.split('results=[]')[0],'isolation','exec'))
from src.core import pumpfun_curve_listener as listener,birth_retry_quarantine as quarantine
for i in range(2):listener._ensure_webhook_birth_queue_schema(str(out/'core.sqlite'))
c=sqlite3.connect(str(out/'core.sqlite'));assert 'quarantine_reason' in [r[1] for r in c.execute('pragma table_info(webhook_birth_queue)')];c.close()
print('PASS exact installed quarantine package and repeat-safe queue owner')
