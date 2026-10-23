from pathlib import Path
s=Path('/private/tmp/dev004-schema-bootstrap-child.py').read_text();exec(compile(s.split('results=[]')[0],'isolated-parent-owner','exec'))
from src.core.price_worker import PriceWorkerRegistry
PriceWorkerRegistry(str(out/'core.sqlite'));print('Accepted tracked_tokens owner initialized;no worker activated')
