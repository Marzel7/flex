#!/usr/bin/env python3
"""
Fetch outgoing SOL transfers for WATCH token creators and find shared destinations.
"""
import asyncio
import aiohttp
import json
from collections import defaultdict

API_KEY = "16f1a5fc-2592-466c-a5d4-b5799ae8da96"
RPC_URL = f"https://mainnet.helius-rpc.com/?api-key={API_KEY}"

CREATORS = [
    ("DX8BEumMvnpKo7DXtPGveJGbxFsXfCT9dCYNcauXS2nC", "#1424"),
    ("364S6QfQL1g9vsRvybUgJ6eNHZtgKGymW4KdWdthPY6J", "CLEAR #1419"),
    ("9V8Lz4wDyRRgeGuPkFz6nWMpeCe3JjexKPbL7rRkDouy", "#1417"),
    ("Cc1JsrA3QXwR7sLo99DbKmGHwKXy8AvhwvABoXqeRz1Q", "SOLAZY #1415"),
    ("EsH593ZfK3qBHeAfasxvmC9QqrTq4of8qqPtnJVKQhxc", "COOK #1414"),
    ("DV3uLoARMRZiG7iDWUrFjJkf4cLoxC4x4hSLsfF1dZyb", "#1408"),
    ("7ijZXDXcyjFkmFXRLpG5jmEtkZYJKRVETgLJ7A5ZtAH4", "Azrael #1403"),
    ("8zkutXXQds5j3oEbPyEtjQfWapFeAjR8mkLrFgnpnjZz", "#1398"),
    ("8LzNcBpp11ipF3wob4DS3w8w2PTQ7EicDWS788oADUAP", "#1397"),
    ("41HNHGYUg5oRZXw1ivK2CKZdC6QFGeQnWFB21em69XFv", "LARP #1395"),
    ("DtNPzQWMwgE6GU4Dfc4tJAUZurL6PYJSu8REczLucVW8", "PSA #1394"),
    ("BKCaSQziNZTNtYYwcGnSKt7cdvpHSJySDM5VwBi2kuvN", "USNS #1393"),
    ("29XR8qSeMVgSSSkL51fDHdMffG1ejd2VTGh1wUtpkWPf", "THEFIND #1388"),
    ("3PxF4BYgjaKx6VTEi2w47UZyn9mnp9uW4ZdAU6pY6U59", "Pikacoin #1387"),
    ("GcH7wCPmWX2XTT9gws5Zwmj55VpF6TwpppKb9D8cjjgs", "xpaymind #1385"),
    ("2nDfdXLXrckNmGpXAo1cmBS9kiU7nFYTLC3jk9qse5LA", "COLLECTOOR #1383"),
    ("2pSY2MScxBTQuT7AA8iCmui671bkNyrBvvbYkrbm9R2c", "CHSN #1381"),
    ("5cT19DwHUVR6MmPcZY5AKtSei1MHYakAPxaof79z5SLB", "#1380"),
    ("Amt9JaxNvJ6SqrTP2GvdnhzjutAgbzAqHHEWEuo5MSQs", "#1378"),
    ("8QyQaEyvGAHZR7vHittfEPs8LKewZ6o7rGYcc9yka97W", "PvA #1371"),
    ("4bRGco8U8aSFNboGUANASAfiVLi5fzQHuXXf1jaZrYu5", "#1370"),
    ("GyxiMRxPHSNYKLweD7PEDhARA6UrQkcPLQCQhw2ZC3hZ", "#1368"),
    ("9kLAAApgPZTnCoHZSfRH68KDsuYtQgNrNzR2jschrCm3", "Poop #1367"),
    ("FekbRVYSRsf7zLYzrSPMWfLqFkD72UZCaMgbQ2JBzSUG", "#1365"),
    ("ASWRz9gN6bNVFfZy2qtvvPFDxz5YpQZrGW53cxtesJfb", "#1360"),
    ("FF2uUg4uhNS7Ua1zKq3Zvj2X4YTYh97VwPDrPV2RijAW", "#1358"),
    ("BMum7SNhqFcLrGGqspc66kDsWVfFRt4yECMJMQPjstx4", "#1350"),
    ("9xTEo7P5ogapakaB1B349H2M5LdkqGqH7kdCSxrMXejP", "#1346"),
    ("XgU25BgK81tW8v2i2UtK2Yrnf2knkjS82eyhGHgG45x", "#1344"),
    ("EDhWorFgtKLq4acFTYrDc2MDME3sUcSDWxVNYnv29RhM", "WINKO #1340"),
    ("97J8pznLGYT825ZsJoTCLrF1rXMtuFYgD96dEENTQWev", "#1337"),
    ("59tckAiuo4d4JdckZw2SgG8DFxamqE4XfDqMB6s41XhM", "#1336"),
    ("VvB2f2f1zswWPgnBBD8TRwEjcHuEa6skJaDQfRtbvum", "POKEX #1333"),
    ("dstpY4TPr1Wofmhzc4UxPq1CWwhoNbc3KSek78ee1EC", "HARAMBEX #1327"),
    ("12M8uwhvGaTyoRxsiyT9wqaTZmvkEAu7k8wXG2NXRJLw", "#1325"),
    ("2WkY3sbGhcfWpr4Cjn783ouj6VfSaeAuuJwSxb7yKnqC", "#1323"),
    ("2SedwRD5T9NmDfVD8UrF4wmt1BfaRjmG5TDUYh4WSuJJ", "#1321"),
    ("DC99qH3jXiq5pPWQd6PjjJcCxTV593s58CLPxWGpEywt", "#1317"),
    ("5R1pXQuoZknMJytCAEpj62LWQ2wrM46PmVsLRYYSQXA6", "Perps #1314"),
    ("GQpvQNLW7xS8jgFGPvNTHPaS2X954GSqHsUs7inoEPcN", "#1308"),
    ("8BbwsjThauDK961JR4aHrguRvjq24BA4KbBUFLxX8fES", "#1307"),
    ("8xSeTaWg4m2nSG7L2yRRGh2jAgfwSjzgNxGBatdT2rpf", "#1305"),
    ("75FDWvNvdLrEYw5AY3pwDVkmeMxvHqTxZLyVedMvptrP", "TUNGALA #1302"),
    ("8TktnhgBHoEMetrjzuwi7uVswNMpvUV7J4Myhng1QaA6", "Giggles #1301"),
    ("BmBpkdYtqADEo9SdEyMg7riQQvV3DiF7aeniv7t6zKG3", "MOGMOJIS #1297"),
    ("GncGNCYShr8vUJ8zN34BRiPQjZWZRuB5w6bmRcyjLU8K", "#1295"),
    ("CZVW3d3isA2GfFxcj6Mg9cRRfQjtfs76aL2Ao4ZETxmJ", "#1292"),
    ("DB8Qb3wPeiJtJAb5mDVXueoT1KcXMsn6yEZduCTGZezb", "Mona #1286"),
    ("H2XpMmW3bbUSokCHvHKCmBwPnUUwgf9fNvfGxbonxZom", "#1284"),
    ("6rHJbtubFnWynbgTGjZcCu52ER5qaJhLovBXC6LVcbtp", "VOICE #1282"),
    ("Fy5A6tHhUEvEoBaToMNJ41CYJrXsPT89pzDGZZu6PiTF", "#1281"),
]

PUMP_FUN = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
SYSTEM_PROGRAM = "11111111111111111111111111111111"
COMPUTE_BUDGET = "ComputeBudget111111111111111111111111111111"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"

SKIP_PROGRAMS = {PUMP_FUN, SYSTEM_PROGRAM, COMPUTE_BUDGET, TOKEN_PROGRAM,
                 "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJe1bmd",
                 "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"}

async def get_signatures(session, address, limit=40):
    payload = {
        "jsonrpc": "2.0", "id": 1,
        "method": "getSignaturesForAddress",
        "params": [address, {"limit": limit, "commitment": "confirmed"}]
    }
    async with session.post(RPC_URL, json=payload) as r:
        data = await r.json()
        return [s["signature"] for s in (data.get("result") or [])]

async def get_transaction(session, sig):
    payload = {
        "jsonrpc": "2.0", "id": 1,
        "method": "getTransaction",
        "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0, "commitment": "confirmed"}]
    }
    async with session.post(RPC_URL, json=payload) as r:
        data = await r.json()
        return data.get("result")

def extract_outgoing(tx, creator):
    """Extract net SOL outflows from creator to other wallets."""
    if not tx or tx.get("meta", {}).get("err"):
        return []

    meta = tx["meta"]
    pre = {a: b for a, b in zip(
        tx["transaction"]["message"]["accountKeys"] if isinstance(tx["transaction"]["message"]["accountKeys"][0], str)
        else [k["pubkey"] for k in tx["transaction"]["message"]["accountKeys"]],
        meta["preBalances"]
    )}
    post = {a: b for a, b in zip(
        tx["transaction"]["message"]["accountKeys"] if isinstance(tx["transaction"]["message"]["accountKeys"][0], str)
        else [k["pubkey"] for k in tx["transaction"]["message"]["accountKeys"]],
        meta["postBalances"]
    )}

    # Get all account keys
    accts = tx["transaction"]["message"]["accountKeys"]
    if accts and isinstance(accts[0], dict):
        accts = [k["pubkey"] for k in accts]

    results = []
    creator_pre = pre.get(creator, 0)
    creator_post = post.get(creator, 0)

    if creator_pre <= creator_post:
        return []  # net inflow, skip

    for acct in accts:
        if acct == creator or acct in SKIP_PROGRAMS:
            continue
        acct_pre = pre.get(acct, 0)
        acct_post = post.get(acct, 0)
        delta = acct_post - acct_pre
        if delta > 1_000_000:  # received > 0.001 SOL
            sol = delta / 1e9
            results.append((acct, round(sol, 4)))

    return results

async def process_creator(session, creator, label, sem):
    async with sem:
        try:
            sigs = await get_signatures(session, creator, limit=30)
            outflows = []  # (dest, sol)

            for sig in sigs:
                tx = await get_transaction(session, sig)
                if tx:
                    for dest, sol in extract_outgoing(tx, creator):
                        outflows.append((dest, sol))
                await asyncio.sleep(0.05)

            return creator, label, outflows
        except Exception as e:
            return creator, label, []

async def main():
    sem = asyncio.Semaphore(5)

    # dest -> [(creator_label, sol), ...]
    dest_map = defaultdict(list)
    # dest -> set of tokens
    dest_tokens = defaultdict(set)

    async with aiohttp.ClientSession() as session:
        tasks = [process_creator(session, c, l, sem) for c, l in CREATORS]
        results = await asyncio.gather(*tasks)

    for creator, label, outflows in results:
        print(f"  {label}: {len(outflows)} outgoing transfers")
        for dest, sol in outflows:
            dest_map[dest].append((label, sol))
            dest_tokens[dest].add(label)

    print("\n" + "="*70)
    print("SHARED DESTINATIONS (2+ WATCH token creators → same wallet)")
    print("="*70)

    shared = [(dest, tokens) for dest, tokens in dest_tokens.items() if len(tokens) > 1]
    shared.sort(key=lambda x: -len(x[1]))

    for dest, tokens in shared:
        entries = dest_map[dest]
        amounts = [sol for _, sol in entries]
        same_amount = len(set(amounts)) == 1
        flag = " *** EXACT SAME AMOUNT ***" if same_amount else ""
        print(f"\n{dest[:20]}..")
        print(f"  Tokens: {len(tokens)} → {', '.join(sorted(tokens))}")
        print(f"  Amounts: {sorted(amounts)}{flag}")

asyncio.run(main())
