#!/usr/bin/env python3
"""
Quick token seller - sells tokens for SOL
Usage: python3 sell_token.py <TOKEN_MINT> <AMOUNT> [SYMBOL]
"""

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Load environment variables from .env file
sys.path.insert(0, str(Path(__file__).parent.parent))  # Parent (root)
sys.path.insert(0, str(Path(__file__).parent))         # Current (utils)

from load_env import load_env
load_env()

from trading_executor import TokenTrader
from solders.keypair import Keypair


async def main():
    if len(sys.argv) < 3:
        print("Usage: python3 sell_token.py <TOKEN_MINT> <AMOUNT>")
        print("\nExamples:")
        print("  python3 sell_token.py 8y45AJzCUBSZL1UDFQRzCKovQBLQFudBrpPeg5yNpump 1000000")
        print("  python3 sell_token.py EPjFWaLb3eTRSAujiFvvrDFiNQ15ghTjciXTo7j5X8f 50000000")
        return

    token_mint = sys.argv[1]
    token_amount = int(sys.argv[2])
    token_symbol = token_mint[:8]  # Use first 8 chars of mint as symbol

    # Validate mint (should be 43-44 chars for Solana base58)
    if len(token_mint) < 40 or len(token_mint) > 44:
        print(f"❌ Invalid mint address (got {len(token_mint)} chars, need 40-44)")
        return

    # Tokens that require legacy transaction format due to size constraints
    COMPLEX_TOKENS = {
        "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263": "BONK",  # Requires 3 ALTs, uses legacy to reduce to 2
    }
    use_legacy = token_mint in COMPLEX_TOKENS

    # Get API key
    helius_key = os.environ.get("HELIUS_API_KEY")
    if not helius_key:
        print("❌ HELIUS_API_KEY not set")
        return

    # Get keypair
    trading_keypair_env = os.environ.get("TRADING_KEYPAIR")
    if not trading_keypair_env:
        print("❌ TRADING_KEYPAIR not set")
        return

    try:
        keypair_array = json.loads(trading_keypair_env)
        keypair_bytes = bytes(keypair_array)
        keypair = Keypair.from_bytes(keypair_bytes)
    except Exception as e:
        print(f"❌ Failed to load keypair: {e}")
        return

    # Initialize trader
    rpc_endpoint = f"https://mainnet.helius-rpc.com/?api-key={helius_key}"
    jupiter_key = os.environ.get("JUPITER_API_KEY")
    trader = TokenTrader(
        rpc_endpoint=rpc_endpoint,
        network="mainnet",
        default_slippage_bps=500,
        default_tip_amount=50000,
        jupiter_api_key=jupiter_key,
    )

    print(f"\n{'='*70}")
    print(f"Selling {token_symbol}")
    print(f"{'='*70}")
    print(f"Token: {token_mint}")
    print(f"Wallet: {str(keypair.pubkey())}")
    print(f"Amount: {token_amount} tokens")
    print(f"Slippage: 5%")
    if use_legacy:
        print(f"⚠️  Using legacy transaction format (complex token)")
    print()

    try:
        print("[1/4] Getting quote from Jupiter...")
        result = await trader.sell_token(
            token_mint=token_mint,
            token_amount=token_amount,
            user_keypair=keypair,
            slippage_bps=500,
            use_legacy_transaction=use_legacy
        )

        print(f"[2/4] Transaction signed")
        print(f"[3/4] Result:")
        print(f"  Status: {result.status}")
        print(f"  Signature: {result.signature}")
        sol_received = result.output_amount / 10**9
        print(f"  Output: {sol_received:.6f} SOL ({result.output_amount} lamports)")
        if result.error:
            print(f"  Error: {result.error}")

        # Log trade
        trade_record = {
            "timestamp": datetime.now().isoformat(),
            "type": "sell",
            "symbol": token_symbol,
            "token_mint": token_mint,
            "input_amount_tokens": token_amount,
            "output_amount_sol": sol_received,
            "output_amount_lamports": result.output_amount,
            "signature": result.signature,
            "status": result.status,
            "error": result.error,
        }

        trades_log = "test_trades.json"
        trades = []
        if Path(trades_log).exists():
            with open(trades_log) as f:
                trades = json.load(f)

        trades.append(trade_record)
        with open(trades_log, "w") as f:
            json.dump(trades, f, indent=2)

        print(f"[4/4] Trade recorded in test_trades.json")
        print(f"\n✅ Check on Solscan:")
        print(f"   https://solscan.io/tx/{result.signature}")
        print(f"   https://solscan.io/token/{token_mint}\n")

    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
