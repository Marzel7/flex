from src.ops.byzantine_event_mint_resolver import PUMPSWAP, resolve_event_mint, select_earliest_target_event
TARGET='Ah7xh8F2auwkZWh1KHDEjwuabdhKKJt2sCxqBH8mpump'
def event(mint=TARGET, **extra): return {'program_id':PUMPSWAP,'event_type':'BUY','event_mint':mint,'token_side_mints':[mint],'slot':453241280,'instruction_index':2,**extra}
def test_pepeinu_match(): assert resolve_event_mint(event(),TARGET)['state']=='MATCH'
def test_transaction_reference_is_not_proof(): assert resolve_event_mint({'program_id':PUMPSWAP,'event_type':'BUY','target_referenced':True},TARGET)['state']=='UNPROVEN'
def test_wrong_mint_rejected(): assert resolve_event_mint(event('other'),TARGET)['state']=='NON_MATCH'
def test_multi_event_selects_target(): assert select_earliest_target_event([event('other',instruction_index=1),event(instruction_index=2)],TARGET)['instruction_index']==2
def test_no_target_fails_closed(): assert select_earliest_target_event([event('other')],TARGET) is None
def test_non_economic_fails_closed(): assert resolve_event_mint(event(event_type='CREATE'),TARGET)['state']=='UNPROVEN'
