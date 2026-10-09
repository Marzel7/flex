"""Compact, atomic DEV-only provider dispatch budget."""
from __future__ import annotations
from contextlib import contextmanager
import fcntl, json, os, time
from pathlib import Path

GLOBAL_LIMIT=20; TOKEN_LIMIT=4; WINDOW=60

class BudgetDenied(RuntimeError): pass

class DevProviderBudget:
 def __init__(self,root,now=time.time): self.path=Path(root)/'provider_budget.json';self.lock=Path(root)/'provider_budget.lock';self.now=now
 @contextmanager
 def locked(self):
  """Expose the existing ledger lock for one coupled queue/budget decision."""
  self.path.parent.mkdir(parents=True,exist_ok=True)
  with open(self.lock,'a+') as guard:
   fcntl.flock(guard,fcntl.LOCK_EX)
   try:
    yield
   finally: fcntl.flock(guard,fcntl.LOCK_UN)
 def admit_locked(self,mint,request_class,*,now=None,global_limit=GLOBAL_LIMIT,token_limit=TOKEN_LIMIT):
  """Debit while ``locked`` is already held; callers must not nest locks."""
  stamp=int(self.now() if now is None else now)
  try: state=json.loads(self.path.read_text())
  except (OSError,ValueError): state={}
  calls=[x for x in state.get('calls',[]) if int(x.get('at',0))>stamp-WINDOW]
  token=[x for x in calls if x.get('mint')==mint]
  if len(calls)>=global_limit: raise BudgetDenied('GLOBAL_PROVIDER_BUDGET_EXHAUSTED')
  if len(token)>=token_limit: raise BudgetDenied('TOKEN_PROVIDER_BUDGET_EXHAUSTED')
  calls.append({'at':stamp,'mint':mint,'class':request_class})
  payload={'calls':calls,'updated_at':stamp,'version':1}
  temp=self.path.with_name('.'+self.path.name+'.tmp');temp.write_text(json.dumps(payload,separators=(',',':'))+'\n');os.replace(temp,self.path)
  return {'global_calls':len(calls),'token_calls':len(token)+1,'remaining_global':global_limit-len(calls),'remaining_token':token_limit-len(token)-1}
 def admit(self,mint,request_class,*,now=None,global_limit=GLOBAL_LIMIT,token_limit=TOKEN_LIMIT):
  with self.locked():
   return self.admit_locked(mint,request_class,now=now,global_limit=global_limit,token_limit=token_limit)
