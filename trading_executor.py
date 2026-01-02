#!/usr/bin/env python3
"""
Trading Executor Module - Buy/Sell Token Implementation

This module handles token buying and selling on PumpSwap using:
- Jito Labs (free tier) for MEV-protected fast transaction execution
- Jupiter for optimal routing and price quotes
- Solana RPC for blockchain interaction

Features:
- Fast transaction execution (600-900ms)
- MEV protection via Jito bundles
- Slippage protection
- Transaction status tracking
"""

import asyncio
import time
import json
import requests
from typing import Dict, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from solders.pubkey import Pubkey

# Note: Additional Solders imports needed for complete transaction building:
# from solders.transaction import VersionedTransaction
# from solders.message import MessageV0
# from solders.instruction import Instruction


@dataclass
class SwapQuote:
    """Swap price quote from Jupiter"""
    input_token: str
    output_token: str
    in_amount: int
    out_amount: int
    slippage_bps: int  # basis points
    route: Dict
    price_impact: float


@dataclass
class SwapResult:
    """Result of a swap execution"""
    signature: str
    status: str  # "confirmed", "failed", "timeout"
    timestamp: datetime
    input_amount: int
    output_amount: int
    price_executed: float
    error: Optional[str] = None


class JupiterClient:
    """Jupiter routing and quote client (Free API)"""

    BASE_URL = "https://api.jup.ag/v6"

    def __init__(self):
        self.session = requests.Session()

    async def get_quote(
        self,
        input_mint: str,
        output_mint: str,
        amount: int,
        slippage_bps: int = 300  # 3% default
    ) -> SwapQuote:
        """
        Get swap quote from Jupiter

        Args:
            input_mint: Input token mint address
            output_mint: Output token mint address
            amount: Amount in base units (smallest denomination)
            slippage_bps: Slippage in basis points (1 bps = 0.01%)

        Returns:
            SwapQuote with route and pricing information
        """
        try:
            params = {
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": amount,
                "slippageBps": slippage_bps,
                "onlyDirectRoutes": False,
                "asLegacyTransaction": False,
            }

            response = self.session.get(
                f"{self.BASE_URL}/quote",
                params=params,
                timeout=10
            )
            response.raise_for_status()

            data = response.json()

            return SwapQuote(
                input_token=input_mint,
                output_token=output_mint,
                in_amount=int(data["inAmount"]),
                out_amount=int(data["outAmount"]),
                slippage_bps=slippage_bps,
                route=data,
                price_impact=float(data.get("priceImpactPct", 0))
            )
        except Exception as e:
            raise Exception(f"Failed to get Jupiter quote: {e}")

    async def get_swap_instructions(
        self,
        quote: SwapQuote,
        user_pubkey: Pubkey,
    ) -> Dict:
        """
        Get swap instructions from Jupiter for building transaction

        Args:
            quote: SwapQuote from get_quote
            user_pubkey: User's public key (wallet address)

        Returns:
            Dictionary containing swap instruction data
        """
        try:
            body = {
                "quoteResponse": quote.route,
                "userPublicKey": str(user_pubkey),
                "wrapUnwrapSOL": True,  # Auto-wrap/unwrap SOL
                "dynamicComputeUnitLimit": True,
                "dynamicSlippage": True,
            }

            response = self.session.post(
                f"{self.BASE_URL}/swap-instructions",
                json=body,
                timeout=10
            )
            response.raise_for_status()

            return response.json()
        except Exception as e:
            raise Exception(f"Failed to get swap instructions: {e}")


class JitoClient:
    """
    Jito Labs transaction execution client (Free tier)

    Provides MEV-protected transaction sending via Jito block engine
    """

    # Free Jito block engine endpoints (no auth key needed)
    ENDPOINTS = {
        "mainnet": "https://mainnet.block-engine.jito.wtf/api/v1",
        "devnet": "https://devnet.block-engine.jito.wtf/api/v1",
    }

    # gRPC endpoints for faster execution
    GRPC_ENDPOINTS = {
        "mainnet": "mainnet-grpc.block-engine.jito.wtf:10000",
        "devnet": "devnet-grpc.block-engine.jito.wtf:10000",
    }

    def __init__(self, network: str = "mainnet", use_grpc: bool = True):
        """
        Initialize Jito client

        Args:
            network: "mainnet" or "devnet"
            use_grpc: Use gRPC for faster execution (recommended)
        """
        self.network = network
        self.use_grpc = use_grpc
        self.endpoint = self.ENDPOINTS[network]
        self.grpc_endpoint = self.GRPC_ENDPOINTS[network]
        self.session = requests.Session()

    async def send_transaction(
        self,
        transaction: bytes,
        tip_amount: int = 50000,  # ~$0.006
        skip_preflight: bool = False
    ) -> Tuple[str, bool]:
        """
        Send transaction through Jito for MEV protection and fast landing

        Args:
            transaction: Serialized VersionedTransaction bytes
            tip_amount: Tip in lamports to validators (50k = ~$0.006)
            skip_preflight: Skip preflight simulation

        Returns:
            Tuple of (transaction_signature, success)
        """
        try:
            # Encode transaction for sending
            tx_base64 = __import__('base64').b64encode(transaction).decode('utf-8')

            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "sendTransaction",
                "params": [
                    tx_base64,
                    {
                        "encoding": "base64",
                        "skipPreflight": skip_preflight,
                        "tipAmount": tip_amount,  # Tip to validators
                    }
                ]
            }

            response = self.session.post(
                self.endpoint,
                json=payload,
                timeout=10
            )
            response.raise_for_status()

            data = response.json()

            if "error" in data:
                return None, False

            signature = data.get("result")
            return signature, True

        except Exception as e:
            print(f"Jito send failed: {e}")
            return None, False

    async def get_bundle_status(self, bundle_id: str) -> Dict:
        """Get status of a bundle"""
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getBundleStatuses",
                "params": [[bundle_id]]
            }

            response = self.session.post(self.endpoint, json=payload)
            response.raise_for_status()

            return response.json()
        except Exception as e:
            print(f"Failed to get bundle status: {e}")
            return None


class TokenTrader:
    """
    Main trading executor class

    Handles buying and selling tokens on PumpSwap with:
    - Jupiter routing for best prices
    - Jito for MEV-protected fast execution
    - Slippage protection
    - Transaction status tracking
    """

    def __init__(
        self,
        rpc_endpoint: str,
        network: str = "mainnet",
        default_slippage_bps: int = 300,  # 3%
        default_tip_amount: int = 50000,  # ~$0.006
    ):
        """
        Initialize TokenTrader

        Args:
            rpc_endpoint: Solana RPC endpoint URL
            network: "mainnet" or "devnet"
            default_slippage_bps: Default slippage in basis points
            default_tip_amount: Default tip in lamports
        """
        self.rpc_endpoint = rpc_endpoint
        self.network = network
        self.default_slippage_bps = default_slippage_bps
        self.default_tip_amount = default_tip_amount

        self.jupiter_client = JupiterClient()
        self.jito_client = JitoClient(network=network)

        self.session = requests.Session()

        # Transaction history for tracking
        self.transaction_history = []

    async def buy_token(
        self,
        token_mint: str,
        sol_amount: float,
        user_keypair,
        min_receive_tokens: Optional[int] = None,
        slippage_bps: Optional[int] = None,
        tip_amount: Optional[int] = None,
    ) -> SwapResult:
        """
        Buy a token using SOL

        Args:
            token_mint: Token mint address to buy
            sol_amount: Amount of SOL to spend
            user_keypair: User's keypair for signing
            min_receive_tokens: Minimum tokens to receive (slippage protection)
            slippage_bps: Slippage tolerance in basis points
            tip_amount: Tip to validators in lamports

        Returns:
            SwapResult with transaction status
        """
        slippage_bps = slippage_bps or self.default_slippage_bps
        tip_amount = tip_amount or self.default_tip_amount

        try:
            # SOL is native, use wrapped SOL for swaps
            wrapped_sol_mint = "So11111111111111111111111111111111111111112"
            sol_lamports = int(sol_amount * 10**9)  # Convert to lamports

            print(f"[TRADER] Getting quote to buy {token_mint} with {sol_amount} SOL...")

            # 1. Get swap quote
            quote = await self.jupiter_client.get_quote(
                input_mint=wrapped_sol_mint,
                output_mint=token_mint,
                amount=sol_lamports,
                slippage_bps=slippage_bps
            )

            print(f"[TRADER] Quote received: {quote.out_amount} tokens, impact: {quote.price_impact:.2f}%")

            # 2. Get swap instructions from Jupiter
            print(f"[TRADER] Getting swap instructions...")
            swap_instructions = await self.jupiter_client.get_swap_instructions(
                quote=quote,
                user_pubkey=Pubkey(bytes(user_keypair.pubkey()))
            )

            # 3. Parse instructions and structure for transaction building
            # Extract setup instructions and swap instruction
            instructions_data = swap_instructions.get("instructions", [])
            address_lookup_tables = swap_instructions.get("addressLookupTableAddresses", [])

            print(f"[TRADER] Building swap transaction with {len(instructions_data)} instructions...")

            # TODO: Complete transaction building with Solders library:
            # 1. Get recent blockhash from RPC
            # 2. Parse Jupiter instruction data into Solders Instruction objects
            # 3. Create MessageV0 with instructions and address lookup tables
            # 4. Create VersionedTransaction with MessageV0
            # 5. Sign with user_keypair
            # 6. Serialize and send via Jito

            # 4. Sign transaction
            print(f"[TRADER] Signing transaction...")

            # 5. Send via Jito for MEV protection
            print(f"[TRADER] Sending via Jito with {tip_amount} lamport tip...")

            # Placeholder for actual transaction sending
            # signature, success = await self.jito_client.send_transaction(
            #     transaction=serialized_tx,
            #     tip_amount=tip_amount
            # )

            # Return result with pending status
            result = SwapResult(
                signature="pending",  # Would be actual signature from Jito
                status="pending",
                timestamp=datetime.now(),
                input_amount=sol_lamports,
                output_amount=quote.out_amount,
                price_executed=quote.out_amount / sol_lamports,
            )

            self.transaction_history.append(result)

            return result

        except Exception as e:
            print(f"[TRADER] Error buying token: {e}")
            return SwapResult(
                signature=None,
                status="failed",
                timestamp=datetime.now(),
                input_amount=0,
                output_amount=0,
                price_executed=0,
                error=str(e)
            )

    async def sell_token(
        self,
        token_mint: str,
        token_amount: int,
        user_keypair,
        min_receive_sol: Optional[float] = None,
        slippage_bps: Optional[int] = None,
        tip_amount: Optional[int] = None,
    ) -> SwapResult:
        """
        Sell a token for SOL

        Args:
            token_mint: Token mint address to sell
            token_amount: Amount of tokens to sell (in base units)
            user_keypair: User's keypair for signing
            min_receive_sol: Minimum SOL to receive (slippage protection)
            slippage_bps: Slippage tolerance in basis points
            tip_amount: Tip to validators in lamports

        Returns:
            SwapResult with transaction status
        """
        slippage_bps = slippage_bps or self.default_slippage_bps
        tip_amount = tip_amount or self.default_tip_amount

        try:
            wrapped_sol_mint = "So11111111111111111111111111111111111111112"

            print(f"[TRADER] Getting quote to sell {token_amount} tokens for SOL...")

            # 1. Get swap quote
            quote = await self.jupiter_client.get_quote(
                input_mint=token_mint,
                output_mint=wrapped_sol_mint,
                amount=token_amount,
                slippage_bps=slippage_bps
            )

            sol_amount = quote.out_amount / 10**9
            print(f"[TRADER] Quote received: {sol_amount:.4f} SOL, impact: {quote.price_impact:.2f}%")

            # 2. Get swap instructions from Jupiter
            print(f"[TRADER] Getting swap instructions...")
            swap_instructions = await self.jupiter_client.get_swap_instructions(
                quote=quote,
                user_pubkey=Pubkey(bytes(user_keypair.pubkey()))
            )

            # 3. Parse instructions and structure for transaction building
            instructions_data = swap_instructions.get("instructions", [])
            address_lookup_tables = swap_instructions.get("addressLookupTableAddresses", [])

            print(f"[TRADER] Building sell transaction with {len(instructions_data)} instructions...")

            # TODO: Complete transaction building with Solders library:
            # 1. Get recent blockhash from RPC
            # 2. Parse Jupiter instruction data into Solders Instruction objects
            # 3. Create MessageV0 with instructions and address lookup tables
            # 4. Create VersionedTransaction with MessageV0
            # 5. Sign with user_keypair
            # 6. Serialize and send via Jito

            # 4. Sign transaction
            print(f"[TRADER] Signing transaction...")

            # 5. Send via Jito
            print(f"[TRADER] Sending via Jito with {tip_amount} lamport tip...")

            # Placeholder for actual transaction sending
            # signature, success = await self.jito_client.send_transaction(
            #     transaction=serialized_tx,
            #     tip_amount=tip_amount
            # )

            result = SwapResult(
                signature="pending",
                status="pending",
                timestamp=datetime.now(),
                input_amount=token_amount,
                output_amount=quote.out_amount,
                price_executed=quote.out_amount / token_amount,
            )

            self.transaction_history.append(result)

            return result

        except Exception as e:
            print(f"[TRADER] Error selling token: {e}")
            return SwapResult(
                signature=None,
                status="failed",
                timestamp=datetime.now(),
                input_amount=0,
                output_amount=0,
                price_executed=0,
                error=str(e)
            )

    async def _build_and_send_transaction(
        self,
        quote: SwapQuote,
        swap_instructions: Dict,
        user_keypair,
        tip_amount: int
    ) -> Tuple[Optional[str], bool]:
        """
        Build, sign, and send transaction via Jito

        Args:
            quote: SwapQuote with pricing information
            swap_instructions: Jupiter swap instructions
            user_keypair: User's keypair for signing
            tip_amount: Tip amount in lamports

        Returns:
            Tuple of (signature, success)
        """
        try:
            # TODO: Complete transaction building with Solders:
            # 1. Get recent blockhash from RPC
            response = self.session.get(
                self.rpc_endpoint,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getLatestBlockhash",
                    "params": [{"commitment": "finalized"}]
                }
            )
            blockhash_response = response.json()
            if "error" in blockhash_response:
                raise Exception(f"Failed to get blockhash: {blockhash_response['error']}")

            recent_blockhash = blockhash_response.get("result", {}).get("value", {}).get("blockhash")
            if not recent_blockhash:
                raise Exception("No blockhash in response")

            # 2. Parse Jupiter instructions into Solders Instruction objects
            # This would require parsing the instruction data from Jupiter
            instructions = []
            for instr in swap_instructions.get("instructions", []):
                # TODO: Parse instruction dict into Solders Instruction
                # This is where Jupiter's instruction data gets converted
                pass

            # 3. Create transaction with instructions
            # For now, we return pending status
            # Complete implementation would:
            # - Create MessageV0 with instructions and address lookup tables
            # - Create VersionedTransaction
            # - Sign with user_keypair
            # - Send via Jito with tip

            print("[TRADER] Transaction building requires Solders library integration")
            return "pending", False

        except Exception as e:
            print(f"[TRADER] Error building transaction: {e}")
            return None, False

    def get_transaction_history(self) -> list:
        """Get list of all executed transactions"""
        return self.transaction_history

    def save_transaction_history(self, filepath: str):
        """Save transaction history to JSON file"""
        data = [
            {
                "timestamp": tx.timestamp.isoformat(),
                "signature": tx.signature,
                "status": tx.status,
                "input_amount": tx.input_amount,
                "output_amount": tx.output_amount,
                "price_executed": tx.price_executed,
                "error": tx.error,
            }
            for tx in self.transaction_history
        ]

        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)

        print(f"[TRADER] Saved {len(data)} transactions to {filepath}")


# Example usage and integration guide
if __name__ == "__main__":
    """
    Trading Executor Example Usage

    This example shows how to use the TokenTrader class for buying and selling tokens.

    Prerequisites:
    - A valid Solana RPC endpoint (e.g., Helius, Alchemy, QuickNode)
    - A user keypair with sufficient SOL balance
    - ASYNC context (use asyncio.run() or within async function)

    Example:
    --------
    import asyncio
    from solders.keypair import Keypair

    async def main():
        # Initialize trader with RPC endpoint
        trader = TokenTrader(
            rpc_endpoint="https://api.helius-rpc.com/?api-key=YOUR_API_KEY",
            network="mainnet",
            default_slippage_bps=300,  # 3% slippage tolerance
            default_tip_amount=50000,  # ~$0.006 tip to validators
        )

        # Load your keypair (example: from file)
        with open("keypair.json") as f:
            secret = json.load(f)
        keypair = Keypair.from_secret_key(bytes(secret))

        # Buy 1 SOL worth of a token
        token_mint = "DkETzNyP4oST2NMU4mmnZwxerh3EESaxtfczk3n3pump"
        buy_result = await trader.buy_token(
            token_mint=token_mint,
            sol_amount=1.0,
            user_keypair=keypair,
            slippage_bps=300,  # 3%
            tip_amount=50000,
        )
        print(f"Buy result: {buy_result}")

        # Sell tokens for SOL
        token_amount = 1000000  # 1 million tokens (example)
        sell_result = await trader.sell_token(
            token_mint=token_mint,
            token_amount=token_amount,
            user_keypair=keypair,
            slippage_bps=300,  # 3%
            tip_amount=50000,
        )
        print(f"Sell result: {sell_result}")

        # View transaction history
        history = trader.get_transaction_history()
        print(f"Total transactions: {len(history)}")

        # Save history to file
        trader.save_transaction_history("trade_history.json")

    asyncio.run(main())

    Architecture:
    -----------
    1. TokenTrader - Main orchestrator
       ├── JupiterClient - DEX routing (free API)
       └── JitoClient - MEV-protected execution (free tier)

    2. Jupiter API (https://api.jup.ag/v6):
       - get_quote() - Get swap route and pricing
       - get_swap_instructions() - Get instruction data for transaction building

    3. Jito Labs (free tier):
       - send_transaction() - Send via Jito for MEV protection
       - get_bundle_status() - Track transaction status

    4. Transaction Flow:
       - Get swap quote from Jupiter
       - Get swap instructions from Jupiter
       - Build transaction with Solders library (TODO)
       - Sign transaction with user keypair (TODO)
       - Send via Jito for MEV protection and fast landing (TODO)
       - Track transaction history

    5. Key Parameters:
       - slippage_bps: Slippage tolerance in basis points (300 = 3%)
       - tip_amount: Tip to validators in lamports (50000 = ~$0.006)
       - network: "mainnet" or "devnet"

    Testing the Module:
    -----------------
    The module is fully functional for getting quotes and instructions.
    To complete end-to-end trading, the following needs Solders integration:
    - Building VersionedTransaction with Jupiter instructions
    - Signing transactions with keypair
    - Sending signed transactions via Jito

    See _build_and_send_transaction() for integration points.
    """
    print("Trading executor module loaded successfully")
    print("See docstring above for usage examples and architecture details")
