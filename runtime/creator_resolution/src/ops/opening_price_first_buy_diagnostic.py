"""Compact diagnostic reduction for first-buy transaction envelopes; never returns raw payloads."""
from __future__ import annotations
import hashlib,json
from .opening_price_first_buy_amounts import PUMP_FUN
from .opening_price_instruction_normalization import canonical_account_keys,normalize_outer_instructions,InstructionNormalizationError
TOKEN="TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA";SYSTEM="11111111111111111111111111111111";ATA="ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL";WSOL="So11111111111111111111111111111111111111112"
def canon(x):return json.dumps(x,sort_keys=True,separators=(",",":"))
def digest(x):return hashlib.sha256(canon(x).encode()).hexdigest()
def _inner(keys,parent,index,x):
    if 'programId' in x: p=x['programId'];a=x.get('accounts')or[];shape='PARSED_EXPLICIT'
    else:
        pi=x.get('programIdIndex'); ai=x.get('accounts')or[]
        if not isinstance(pi,int) or not 0<=pi<len(keys): raise InstructionNormalizationError('INNER_PROGRAM_ID_INDEX_OUT_OF_RANGE')
        if not all(isinstance(i,int) and 0<=i<len(keys) for i in ai): raise InstructionNormalizationError('INNER_ACCOUNT_INDEX_OUT_OF_RANGE')
        p=keys[pi];a=[keys[i] for i in ai];shape='COMPILED'
    parsed=x.get('parsed')or{};return {'parent_outer_index':parent,'inner_index':index,'resolved_program_id':p,'relevant_resolved_accounts':a,'instruction_semantic_if_already_known':parsed.get('type'),'source_shape':shape,'is_pumpfun_program':p==PUMP_FUN,'is_token_program':p==TOKEN,'is_system_program':p==SYSTEM}
def diagnostic(anchor,transaction,response_digest=None):
    try:
        msg=transaction['transaction']['message'];meta=transaction['meta'];keys=canonical_account_keys(transaction);outer=normalize_outer_instructions(transaction)
        fee_payer=keys[0];signers=sorted((x if isinstance(x,str) else x.get('pubkey')) for x in msg['accountKeys'] if isinstance(x,dict) and x.get('signer'))
        outer_s=[{'outer_index':x['outer_index'],'source_shape':x['source_shape'],'resolved_program_id':x['program_id'],'resolved_account_indices':None,'relevant_resolved_accounts':x['resolved_accounts'],'is_pumpfun_program':x['program_id']==PUMP_FUN,'is_system_program':x['program_id']==SYSTEM,'is_token_program':x['program_id']==TOKEN,'is_associated_token_program':x['program_id']==ATA} for x in outer]
        by_outer={x['outer_index']:x['program_id'] for x in outer};inner=[]
        for g in meta.get('innerInstructions')or[]:
            for i,x in enumerate(g.get('instructions')or[]): inner.append(_inner(keys,g['index'],i,x))
        mint=anchor['mint'];pre={(x['accountIndex'],x['mint']):x for x in meta.get('preTokenBalances')or[]};post={(x['accountIndex'],x['mint']):x for x in meta.get('postTokenBalances')or[]};tokens=[]
        for k in sorted(set(pre)|set(post)):
            a,b=pre.get(k,{}),post.get(k,{});
            if k[1] not in (mint,WSOL):continue
            av=int(a.get('uiTokenAmount',{}).get('amount','0'));bv=int(b.get('uiTokenAmount',{}).get('amount','0'));tokens.append({'asset':'WSOL' if k[1]==WSOL else 'TARGET_TOKEN','mint':k[1],'raw_amount':bv-av,'decimals':(b or a).get('uiTokenAmount',{}).get('decimals'),'account_index':k[0],'account_address':keys[k[0]] if k[0]<len(keys) else None,'owner':(b or a).get('owner'),'source_type':'TOKEN_BALANCE_DELTA','outer_inner_provenance':None,'event_binding_status':'UNRESOLVED'})
        sol=[]
        for i,(a,b) in enumerate(zip(meta.get('preBalances')or[],meta.get('postBalances')or[])):
            if a!=b:sol.append({'asset':'SOL','raw_amount':b-a,'source_account':keys[i] if i<len(keys) else None,'destination_account':None,'outer_inner_provenance':None,'observation_type':'LAMPORT_BALANCE_DELTA','event_binding_status':'UNRESOLVED'})
        logs=meta.get('logMessages')or[];buy=[]
        for i,line in enumerate(logs):
            if 'Instruction: Buy' in line:
                previous=next((z for z in reversed(logs[:i]) if z.startswith('Program ') and ' invoke [' in z),None);buy.append({'log_index':i,'preceding_invocation_digest':digest(previous or ''),'preceding_is_pumpfun':bool(previous and PUMP_FUN in previous)})
        inner_p=[dict(x,parent_outer_program=by_outer.get(x['parent_outer_index']),contains_expected_mint=mint in x['relevant_resolved_accounts'],contains_expected_buyer=anchor['buyer'] in x['relevant_resolved_accounts']) for x in inner if x['is_pumpfun_program']]
        result={'anchor_id':anchor.get('anchor_id'),'binding_structure':{'outer_instruction_count':len(outer_s),'inner_instruction_count':len(inner),'outer_programs':outer_s,'inner_instruction_summaries':inner,'outer_pumpfun_occurrences':sum(x['is_pumpfun_program'] for x in outer_s),'inner_pumpfun_candidates':inner_p},'buy_log_context':buy,'token_candidates':tokens,'quote_candidates':sol,'meta_fee':meta.get('fee'),'source_response_digest':response_digest or digest(transaction),'diagnostic_version':'OPENING_PRICE_FIRST_BUY_DIAGNOSTIC_V1'};result['diagnostic_digest']=digest(result);return result
    except (KeyError,TypeError,ValueError,InstructionNormalizationError) as e:
        r={'anchor_id':anchor.get('anchor_id'),'diagnostic_error':str(e),'source_response_digest':response_digest or digest(transaction),'diagnostic_version':'OPENING_PRICE_FIRST_BUY_DIAGNOSTIC_V1'};r['diagnostic_digest']=digest(r);return r
