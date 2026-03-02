"""
PumpFun Token Manipulation Analyzer - Version 2

Detects manipulation, wash trading, wallet concentration, and calculates a dynamic manipulation score
for pump.fun / bonding-curve tokens.
"""

import requests
from collections import defaultdict, Counter
from typing import List, Dict
import time
import sys

# Import RPC metrics recorder for monitoring
try:
    from rpc_metrics_recorder import record_request, initialize_recorder
    initialize_recorder(plan_monthly_credits=50_000_000)
except ImportError:
    def record_request(*args, **kwargs):
        pass  # No-op if metrics recorder not available


class PumpFunTokenAnalyzer:
    """
    Analyzer for pump.fun / bonding-curve tokens.
    Detects manipulation, wash trading, wallet concentration, and calculates a dynamic manipulation score.
    """

    def __init__(self, token_mint: str, rpc_url: str = "https://api.mainnet-beta.solana.com"):
        self.token_mint = token_mint
        self.rpc_url = rpc_url
        self.transactions: List[Dict] = []  # {wallet, type, amount, timestamp}
        self.cached_signatures: List[str] = []  # Cache signatures to ensure consistency
        self.cached_transactions: List[Dict] = []  # Cache parsed transactions for full consistency

    def reset(self):
        """Reset transactions but keep cached signatures and parsed transactions"""
        self.transactions = []

    # -------------------------------
    # 1. Fetch transactions from Solana RPC
    # -------------------------------
    def fetch_transactions(self, limit: int = 1000, fetch_all: bool = True, use_cache: bool = True):
        """Fetch SPL token transactions for the mint using Solana RPC with pagination

        Args:
            limit: Number of signatures to fetch per RPC request (max 1000)
            fetch_all: If True, fetch all available signatures with pagination; if False, only fetch first batch
            use_cache: If True, reuse cached signatures if available; if False, always fetch fresh
        """
        # Use cached signatures if available and requested
        if use_cache and self.cached_signatures:
            print(f"[ANALYZER] Using {len(self.cached_signatures)} cached signatures", flush=True)
            sys.stdout.flush()
            all_signatures = self.cached_signatures
        else:
            print(f"[ANALYZER] Fetching transactions for {self.token_mint[:16]}... (limit={limit}, fetch_all={fetch_all})", flush=True)
            sys.stdout.flush()

            all_signatures = []
            before_cursor = None
            page = 0

            while True:
                page += 1
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getSignaturesForAddress",
                    "params": [self.token_mint, {"limit": limit}]
                }

                # Add pagination cursor if we have one
                if before_cursor:
                    payload["params"][1]["before"] = before_cursor

                try:
                    start_time = time.time()
                    http_resp = requests.post(self.rpc_url, json=payload, timeout=10)
                    latency_ms = (time.time() - start_time) * 1000
                    resp = http_resp.json()

                    # Record metrics
                    record_request(
                        section="ui_api",
                        provider="solana_rpc",
                        method="getSignaturesForAddress",
                        status_code=http_resp.status_code,
                        latency_ms=latency_ms,
                        mode="background",
                    )
                except Exception as e:
                    print(f"[ANALYZER] ❌ Failed to fetch signatures: {e}", flush=True)
                    record_request(
                        section="ui_api",
                        provider="solana_rpc",
                        method="getSignaturesForAddress",
                        status_code=0,
                        latency_ms=(time.time() - start_time) * 1000,
                        mode="background",
                        source_file="pump_fun_analyzer",

                        error=str(e),
                    )
                    break

                if "result" not in resp or not resp["result"]:
                    print(f"[ANALYZER] ⚠ No more signatures found", flush=True)
                    break

                signatures = [x["signature"] for x in resp["result"]]
                all_signatures.extend(signatures)
                print(f"[ANALYZER] Page {page}: Found {len(signatures)} signatures (total: {len(all_signatures)})", flush=True)
                sys.stdout.flush()

                # If fetch_all is False, only fetch the first batch
                if not fetch_all:
                    break

                # If we got fewer signatures than the limit, we've reached the end
                if len(signatures) < limit:
                    print(f"[ANALYZER] Reached end of transaction history", flush=True)
                    break

                # Set cursor for next page (use the last signature as the cursor)
                before_cursor = signatures[-1]
                time.sleep(0.1)  # avoid rate limits between pagination requests

            # Cache the signatures for future use
            self.cached_signatures = all_signatures

        # Use cached transactions if available and requested
        if use_cache and self.cached_transactions:
            print(f"[ANALYZER] Using {len(self.cached_transactions)} cached parsed transactions", flush=True)
            sys.stdout.flush()
            self.transactions = self.cached_transactions.copy()
        else:
            print(f"[ANALYZER] Found {len(all_signatures)} total signatures, parsing transactions...", flush=True)
            sys.stdout.flush()

            for idx, sig in enumerate(all_signatures):
                if idx % 100 == 0 and idx > 0:
                    print(f"[ANALYZER] Processed {idx}/{len(all_signatures)} transactions...", flush=True)
                    sys.stdout.flush()

                payload_tx = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTransaction",
                    "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
                }
                try:
                    start_time = time.time()
                    tx_http_resp = requests.post(self.rpc_url, json=payload_tx, timeout=10)
                    latency_ms = (time.time() - start_time) * 1000
                    tx_resp = tx_http_resp.json()

                    # Record metrics
                    record_request(
                        section="ui_api",
                        provider="solana_rpc",
                        method="getTransaction",
                        status_code=tx_http_resp.status_code,
                        latency_ms=latency_ms,
                        mode="background",
                    )

                    tx_info = tx_resp.get("result")
                    if tx_info:
                        self._parse_transaction(tx_info)
                except Exception as e:
                    record_request(
                        section="ui_api",
                        provider="solana_rpc",
                        method="getTransaction",
                        status_code=0,
                        latency_ms=(time.time() - start_time) * 1000,
                        mode="background",
                        source_file="pump_fun_analyzer",

                        error=str(e),
                    )
                    pass
                time.sleep(0.05)  # avoid rate limits

            # Cache the parsed transactions for future use
            self.cached_transactions = self.transactions.copy()

        print(f"[ANALYZER] ✅ Parsed {len(self.transactions)} transactions", flush=True)
        sys.stdout.flush()

    # -------------------------------
    # 2. Parse buy/sell activity
    # -------------------------------
    def _parse_transaction(self, tx_info: Dict):
        """Parse transactions and detect bonding curve buys/sells"""
        try:
            meta = tx_info.get("meta", {})
            if not meta:
                return

            timestamp = tx_info.get("blockTime", int(time.time()))
            pre_balances = meta.get("preTokenBalances", [])
            post_balances = meta.get("postTokenBalances", [])

            for pre, post in zip(pre_balances, post_balances):
                if pre.get("mint") != self.token_mint:
                    continue

                wallet = post.get("owner")
                if not wallet:
                    continue

                pre_amount = int(pre.get("uiTokenAmount", {}).get("amount", 0))
                post_amount = int(post.get("uiTokenAmount", {}).get("amount", 0))
                delta = post_amount - pre_amount

                if delta > 0:
                    tx_type = "buy"
                elif delta < 0:
                    tx_type = "sell"
                else:
                    continue

                # Detect bonding curve: if price movement matches mint/burn pattern
                is_bonding_curve = self._detect_bonding_curve(tx_info)
                self.transactions.append({
                    "wallet": wallet,
                    "type": tx_type,
                    "amount": abs(delta),
                    "timestamp": timestamp,
                    "bonding_curve": is_bonding_curve
                })
        except Exception:
            pass

    def _detect_bonding_curve(self, tx_info: Dict) -> bool:
        """
        Detect bonding curve interaction for this token mint.
        
        Heuristic:
        - Detect transactions calling known Pump.fun AMM program IDs (e.g., GARY, WSOL pair)
        - Detect mintTo / burn instructions for this token mint
        - Check inner instructions as some AMMs wrap mint/burn inside them
        """

        # Add all known pump.fun AMM program IDs for your token
        pumpfun_programs = [
            "GARY_PROGRAM_ID_HERE",   # Replace with the real GARY program ID
            "WSOL_AMM_PROGRAM_ID_HERE"  # Add other AMM IDs if needed
        ]

        instructions = tx_info.get("transaction", {}).get("message", {}).get("instructions", [])

        for ix in instructions:
            program_id = ix.get("programId")
            if program_id in pumpfun_programs:
                return True

            # Check inner instructions (mint/burn may be wrapped)
            inner = ix.get("innerInstructions", [])
            for inner_ix in inner:
                inner_program = inner_ix.get("programId")
                if inner_program in pumpfun_programs:
                    return True

            # Check parsed instructions for mint/burn
            parsed = ix.get("parsed", {})
            if parsed:
                typ = parsed.get("type", "")
                info = parsed.get("info", {})
                if typ in ["mintTo", "burn"] and info.get("mint") == self.token_mint:
                    return True

        return False



    # -------------------------------
    # 3. Metrics
    # -------------------------------
    def wallet_concentration(self) -> float:
        """Calculate concentration of volume in top 5 wallets"""
        wallet_totals = Counter()
        for tx in self.transactions:
            wallet_totals[tx["wallet"]] += tx["amount"]
        total_volume = sum(wallet_totals.values())
        if total_volume == 0:
            return 0
        top_5_volume = sum([v for _, v in wallet_totals.most_common(5)])
        return top_5_volume / total_volume

    def wash_trading_ratio(self) -> float:
        """
        Detect repeated buy/sell cycles by same wallets (wash trading)
        """
        wallet_cycles = defaultdict(list)
        for tx in sorted(self.transactions, key=lambda x: x["timestamp"]):
            wallet_cycles[tx["wallet"]].append(tx["type"])

        wash_count = 0
        total_count = len(self.transactions)
        for cycles in wallet_cycles.values():
            for i in range(1, len(cycles)):
                if cycles[i] != cycles[i-1]:
                    wash_count += 1

        return wash_count / total_count if total_count > 0 else 0

    def exit_asymmetry(self) -> float:
        """Calculate concentration of sells in top 3 wallets"""
        sells = [tx for tx in self.transactions if tx["type"] == "sell"]
        wallet_totals = Counter()
        for tx in sells:
            wallet_totals[tx["wallet"]] += tx["amount"]
        total_sell = sum(wallet_totals.values())
        if total_sell == 0:
            return 0
        top_3_sells = sum([v for _, v in wallet_totals.most_common(3)])
        return top_3_sells / total_sell

    def capital_efficiency(self, market_cap_change: float, net_sol_inflow: float) -> float:
        """Calculate market cap change per SOL invested"""
        if net_sol_inflow <= 0:
            return 0
        return market_cap_change / net_sol_inflow

    def bonding_curve_ratio(self) -> float:
        """Fraction of transactions that interact via bonding curve"""
        if not self.transactions:
            return 0
        bc_count = sum(1 for tx in self.transactions if tx["bonding_curve"])
        return bc_count / len(self.transactions)

    # -------------------------------
    # 4. Manipulation score
    # -------------------------------
    def compute_score(self,
                      wallet_weight=0.25,
                      wash_weight=0.25,
                      exit_weight=0.2,
                      capital_weight=0.2,
                      bc_weight=0.1,
                      market_cap_change: float = 0,
                      net_sol_inflow: float = 1) -> float:
        """
        Compute dynamic manipulation score (0.0 = low risk, 1.0 = high risk)

        Factors:
        - wallet_concentration: High concentration = manipulation
        - wash_trading_ratio: High ratio = wash trading activity
        - exit_asymmetry: High asymmetry = pump and dump pattern
        - capital_efficiency: Market cap change per SOL = efficiency
        - bonding_curve_ratio: Bonding curve heavy = less manipulation
        """
        wc = self.wallet_concentration()
        wt = self.wash_trading_ratio()
        ea = self.exit_asymmetry()
        ce = self.capital_efficiency(market_cap_change, net_sol_inflow)
        bc = self.bonding_curve_ratio()

        # Normalize capital efficiency (high efficiency = possible manipulation)
        ce_norm = min(ce / 5, 1.0)

        # Compute weighted score
        score = (wc * wallet_weight +
                 wt * wash_weight +
                 ea * exit_weight +
                 ce_norm * capital_weight +
                 bc * bc_weight)
        return round(score, 3)

    def summary(self, market_cap_change: float = 0, net_sol_inflow: float = 1) -> Dict:
        """Generate comprehensive analysis summary"""
        return {
            "token_mint": self.token_mint,
            "transactions_parsed": len(self.transactions),
            "wallet_concentration": round(self.wallet_concentration(), 3),
            "wash_trading_ratio": round(self.wash_trading_ratio(), 3),
            "exit_asymmetry": round(self.exit_asymmetry(), 3),
            "capital_efficiency": round(self.capital_efficiency(market_cap_change, net_sol_inflow), 3),
            "bonding_curve_ratio": round(self.bonding_curve_ratio(), 3),
            "manipulation_score": self.compute_score(market_cap_change=market_cap_change,
                                                     net_sol_inflow=net_sol_inflow)
        }

    def print_summary(self, market_cap_change: float = 0, net_sol_inflow: float = 1):
        """Print a formatted summary"""
        summary = self.summary(market_cap_change, net_sol_inflow)

        print("\n" + "="*70)
        print(f"PUMP.FUN TOKEN MANIPULATION ANALYSIS - {self.token_mint[:16]}...")
        print("="*70)
        print(f"Transactions Parsed:      {summary['transactions_parsed']}")
        print(f"Wallet Concentration:     {summary['wallet_concentration']:.1%} (top 5 wallets)")
        print(f"Wash Trading Ratio:       {summary['wash_trading_ratio']:.1%}")
        print(f"Exit Asymmetry:           {summary['exit_asymmetry']:.1%} (concentration in top 3 sells)")
        print(f"Capital Efficiency:       {summary['capital_efficiency']:.2f} (mcap change per SOL)")
        print(f"Bonding Curve Ratio:      {summary['bonding_curve_ratio']:.1%}")

        # Risk assessment
        score = summary['manipulation_score']
        if score < 0.3:
            risk = "🟢 LOW RISK"
        elif score < 0.6:
            risk = "🟡 MEDIUM RISK"
        else:
            risk = "🔴 HIGH RISK"

        print(f"\n{'MANIPULATION SCORE':.<40} {score:.3f}")
        print(f"{'RISK ASSESSMENT':.<40} {risk}")
        print("="*70 + "\n")
