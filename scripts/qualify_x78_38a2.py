#!/usr/bin/env python3
"""Two-request bounded stability probe for the X78.38A1 same-slot case."""
from __future__ import annotations
import asyncio, gzip, hashlib, json, os
from pathlib import Path
import aiohttp
from src.acquisition.factory import build_transaction_acquisition
from src.acquisition.transaction import acquisition_scope

CREATOR="Gygj9QQby4j2jryqyqBHvLP7ctv2SaANgh4sCb69BUpA"
FIRST_ARTIFACT="docs/audits/x78_38a1_provider_artifacts/7929565477ccf06d9f0d6e745f2a801bd37734d1124210d53dd066d96a791c55.json.gz"
OUT=Path("docs/audits/x78_38a2_provider_experiment.json")

async def main():
    first=json.load(gzip.open(FIRST_ARTIFACT,'rt'))
    # Five signatures below this anchor are expected to reappear in each window.
    anchor=str(first[-6]['signature']); expected=[str(r['signature']) for r in first[-5:]]
    url=(f"https://api-mainnet.helius-rpc.com/v0/addresses/{CREATOR}/transactions"
         f"?api-key={os.environ['HELIUS_API_KEY']}&limit=100&sort-order=desc&commitment=finalized&before={anchor}")
    raw=[]
    async with aiohttp.ClientSession() as session:
      client=build_transaction_acquisition(session,semaphore=asyncio.Semaphore(8))
      for n in (1,2):
       with acquisition_scope(purpose='x78_38a2_shadow_same_slot',creator=CREATOR):
        r=await client.request_once(http_method='GET',url=url,timeout_seconds=30,request_type='enhanced_address_page',method='helius_enhanced_addresses_transactions',page_number=n,cursor=anchor,cache_state='shadow_no_cache')
       body=r.raw_body or b''; digest=hashlib.sha256(body).hexdigest(); p=Path('docs/audits/x78_38a1_provider_artifacts')/(digest+'.json.gz'); p.parent.mkdir(exist_ok=True,parents=True)
       if not p.exists(): gzip.open(p,'wb').write(body)
       page=r.data if isinstance(r.data,list) else []
       raw.append({'status':r.status,'sha256':digest,'count':len(page),'signatures':[x.get('signature') for x in page],'slots':[x.get('slot') for x in page]})
    report={'milestone':'X78.38A2','creator':CREATOR,'anchor':anchor,'expected_window':expected,'responses':raw,
      'membership_stable':set(raw[0]['signatures'])==set(raw[1]['signatures']),
      'order_stable':raw[0]['signatures']==raw[1]['signatures'],
      'window_present_each':all(set(expected).issubset(set(x['signatures'])) for x in raw)}
    OUT.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n'); print(json.dumps({k:report[k] for k in ('membership_stable','order_stable','window_present_each')}))
if __name__=='__main__': asyncio.run(main())
