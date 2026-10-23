from pathlib import Path
s=Path('/private/tmp/dev004-schema-bootstrap-child.py').read_text();exec(compile(s.split('results=[]')[0],'isolated-consumer-setup','exec'))
from src.core.price_service import TokenPriceService,TokenPrice
c=sqlite3.connect(str(out/'core.sqlite'));c.row_factory=sqlite3.Row;c.execute('pragma foreign_keys=on')
# Synthetic canonical CREATE persistence into the three scoped schema contracts.
c.execute("insert into token_analysis(mint,pf_ws_creator,earliest_tx_creator,create_tx_signature,created_at) values(?,?,?,?,?)",('DEV004_SYNTHETIC_VALUATION','DEV004_SYNTHETIC_CREATOR','DEV004_SYNTHETIC_CREATOR','DEV004_SYNTHETIC_CREATE',1792747000))
c.execute("insert into tracked_tokens(mint,created_at,updated_at) values(?,?,?)",('DEV004_SYNTHETIC_VALUATION',1792747000,1792747000));c.commit();c.close()
service=object.__new__(TokenPriceService);service.db_path=str(out/'core.sqlite')
for mc,stamp in [(250,1792747001),(200,1792747002)]:service._store_snapshot(TokenPrice(mint='DEV004_SYNTHETIC_VALUATION',price_usd=0.25,price_sol=0.01,liquidity_usd=0,volume_24h=0,market_cap=mc,source='birdeye',timestamp=stamp))
c=sqlite3.connect(str(out/'core.sqlite'));c.row_factory=sqlite3.Row
row=dict(c.execute("select pf_ws_creator,create_tx_signature,first_observed_at,first_observed_mc from token_analysis where mint='DEV004_SYNTHETIC_VALUATION'").fetchone());peak=dict(c.execute("select * from token_market_cap_peaks where mint='DEV004_SYNTHETIC_VALUATION'").fetchone());assert row['first_observed_at']==1792747001 and row['first_observed_mc']==250 and peak['peak_market_cap']==250 and peak['peak_market_cap_at']==1792747001;assert c.execute('pragma foreign_key_check').fetchall()==[];c.close()
result={'status':'PASS','canonical_create_readback':row,'actual_price_store_snapshot':peak,'provider_calls':0,'database_attempts':db_attempts,'blocked':blocked};(out/'consumer_result.json').write_text(json.dumps(result,indent=2)+'\n');print('PASS canonical CREATE readback and actual synthetic Birdeye-labelled valuation persistence;first observation and peak locked')
