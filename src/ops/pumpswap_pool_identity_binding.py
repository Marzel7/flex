"""Fail-closed direct retained PumpSwap mint/pool binding for materializations."""
import hashlib,json,sqlite3
from pathlib import Path
from src.ops.pumpswap_boundary import PUMPSWAP_PROGRAM
def bind(payload,db='database/flex_complete_database.db'):
 ins=(payload.get('instructions')or[{}])[0];accounts=set(ins.get('accounts')or[]);mints=[a for a in accounts if isinstance(a,str) and a.endswith('pump')]
 if len(mints)!=1 or not Path(db).exists():return {'status':'POOL_IDENTITY_UNRESOLVED'}
 c=sqlite3.connect(f'file:{Path(db).resolve()}?mode=ro',uri=True);c.row_factory=sqlite3.Row
 try:r=c.execute("SELECT mint,pool_address,base_account,quote_account,discovery_method FROM token_pool_accounts WHERE mint=? AND pool_program=? AND pool_address IS NOT NULL",(mints[0],PUMPSWAP_PROGRAM)).fetchall()
 finally:c.close()
 candidates=[x for x in r if x['pool_address'] in accounts]
 if len(candidates)!=1:return {'status':'POOL_IDENTITY_UNRESOLVED','mint':mints[0]}
 x=candidates[0];body={'materialized_id':payload.get('id'),'mint':x['mint'],'pool':x['pool_address'],'base_vault':x['base_account'],'quote_vault':x['quote_account'],'method':'DIRECT_RETAINED_POOL_BINDING','source':'token_pool_accounts.standard_extraction','status':'POOL_IDENTITY_QUALIFIED_DIRECT'};body['id']=hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest();return body
