"""Registry-owned first-stage resolver for bounded Living events."""
from __future__ import annotations
from dataclasses import dataclass
import os, sqlite3

@dataclass(frozen=True,order=True)
class ResolverKey:
    key_type:str
    key_value:str
    def __post_init__(self):
        if not self.key_type or not self.key_value or self.key_type not in {'mint','creator','funder','upstream','source_family','fingerprint','architecture'}: raise ValueError('invalid resolver key')
def event_keys(event):
    aliases={'mint':'mint','creator':'creator','funder':'funder','upstream':'upstream','source_family':'source_family','fingerprint':'fingerprint','architecture':'architecture'}
    return tuple(sorted(ResolverKey(t,str(event[k]).strip()) for k,t in aliases.items() if event.get(k) is not None and str(event[k]).strip()))
class LivingCandidateReverseIndex:
    def __init__(self,registry):
        pairs=[]
        for spec in registry.values():
            for raw in spec.get('resolver_keys',()):
                key=raw if isinstance(raw,ResolverKey) else ResolverKey(*raw)
                pairs.append((key,spec['potential_operation_id']))
        self._map={key:tuple(sorted({op for k,op in pairs if k==key})) for key,_ in pairs}
    def resolve(self,event): return sorted({op for key in event_keys(event) for op in self._map.get(key,())})
    def rebuild(self,registry): self.__init__(registry); return self
    @classmethod
    def from_association_ledger(cls,registry,db_path):
        """Optional read-only bootstrap from configured Living association metadata."""
        c=sqlite3.connect('file:'+os.path.abspath(db_path)+'?mode=ro',uri=True)
        try:
            ops=[s['potential_operation_id'] for s in registry.values()]; q=','.join('?'*len(ops)); rows=c.execute('SELECT potential_operation_id,evidence_key FROM potential_operation_evidence_association WHERE potential_operation_id IN ('+q+')',ops).fetchall()
        finally: c.close()
        cloned={k:{**s,'resolver_keys':tuple(s.get('resolver_keys',()))} for k,s in registry.items()}
        owners={s['potential_operation_id']:s for s in cloned.values()}
        for op,key in rows:
            if ':' in key:
                typ,value=key.split(':',1)
                if typ in {'mint','funder'}: owners[op]['resolver_keys']+=((typ,value),)
        return cls(cloned)
