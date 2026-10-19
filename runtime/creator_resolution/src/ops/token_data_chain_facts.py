"""Pure compact Create/migration and PumpSwap pool facts."""
from .token_data_lifecycle_facts import as_number

SOL = "So11111111111111111111111111111111111111112"
def tx_summary(response,signature,mint,pool=None):
    result=(response or {}).get('result')
    if not isinstance(result,dict):return {'status':'INSUFFICIENT_EVIDENCE_NULL_OR_MALFORMED_TRANSACTION','signature':signature}
    tx=result.get('transaction') or {};message=tx.get('message') or {};signatures=tx.get('signatures') or []
    if signature not in signatures or mint not in [x.get('pubkey') if isinstance(x,dict) else x for x in message.get('accountKeys') or []]:return {'status':'INSUFFICIENT_EVIDENCE_IDENTITY_MISMATCH','signature':signature}
    text=' '.join(str(x) for x in ((result.get('meta') or {}).get('logMessages') or [])).lower();value={'status':'QUALIFIED','signature':signature,'slot':result.get('slot'),'timestamp':result.get('blockTime'),'transaction_error':(result.get('meta') or {}).get('err'),'instruction_markers':{'migrate':'migrate' in text,'create_pool':'createpool' in text or 'create pool' in text,'pump_swap':'pumpswap' in text or 'pump swap' in text}}
    if value['slot'] is None or value['timestamp'] is None or value['transaction_error'] is not None:value['status']='INSUFFICIENT_EVIDENCE_UNSUCCESSFUL_OR_UNTIMED_TRANSACTION'
    if pool:
      token_ui=sol_ui=None
      for b in (result.get('meta') or {}).get('postTokenBalances') or []:
       amount=as_number((b.get('uiTokenAmount') or {}).get('uiAmount'))
       if b.get('owner')==pool and b.get('mint')==mint:token_ui=amount
       if b.get('owner')==pool and b.get('mint')==SOL:sol_ui=amount
      value['first_pumpswap_pool_mc_sol']=(sol_ui/token_ui)*1_000_000_000 if token_ui and sol_ui else None;value['pool_valuation_status']='QUALIFIED_FROM_POST_TRANSACTION_POOL_TOKEN_BALANCES' if token_ui and sol_ui else 'INSUFFICIENT_EVIDENCE_POOL_BALANCES_NOT_DECODED'
    return value
