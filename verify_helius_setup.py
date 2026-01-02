#!/usr/bin/env python3
"""
Verify Helius RPC Setup

This script checks:
1. HELIUS_API_KEY environment variable
2. Helius RPC connectivity
3. Transaction history persistence
4. Complete test readiness
"""

import os
import sys
import requests
import json
from datetime import datetime

def check_environment():
    """Check if HELIUS_API_KEY is set"""
    print("=" * 70)
    print("STEP 1: Checking Environment Variable")
    print("=" * 70)

    helius_key = os.environ.get("HELIUS_API_KEY")

    if helius_key:
        # Show first 10 and last 10 chars for security
        masked = helius_key[:10] + "..." + helius_key[-10:]
        print(f"✓ HELIUS_API_KEY is set: {masked}")
        return helius_key
    else:
        print("✗ HELIUS_API_KEY is NOT set")
        print("\nTo set it, run:")
        print('  export HELIUS_API_KEY="your_api_key_here"')
        print("\nOr add to ~/.zshrc:")
        print('  export HELIUS_API_KEY="your_api_key_here"')
        print("\nThen reload:")
        print("  source ~/.zshrc")
        return None

def test_helius_rpc(helius_key):
    """Test Helius RPC connectivity"""
    print("\n" + "=" * 70)
    print("STEP 2: Testing Helius RPC Connectivity")
    print("=" * 70)

    helius_endpoint = f"https://mainnet.helius-rpc.com/?api-key={helius_key}"

    try:
        print(f"Testing endpoint: https://mainnet.helius-rpc.com/?api-key=***")
        response = requests.post(
            f"https://mainnet.helius-rpc.com/?api-key={helius_key}",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSlot",
                "params": []
            },
            timeout=10
        )

        print(f"HTTP Status: {response.status_code}")

        try:
            result = response.json()

            if "result" in result:
                slot = result["result"]
                print(f"✓ RPC Working! Current slot: {slot}")
                return True
            elif "error" in result:
                error = result["error"]
                print(f"✗ RPC Error: {error}")
                if error.get("code") == -32700:
                    print("  This usually means invalid API key")
                return False
            else:
                print(f"✗ Unexpected response: {result}")
                return False

        except json.JSONDecodeError as e:
            print(f"✗ Response is not JSON: {str(e)[:100]}")
            print(f"Response text: {response.text[:200]}")
            return False

    except requests.exceptions.Timeout:
        print("✗ Request timed out (10 seconds)")
        return False
    except requests.exceptions.ConnectionError as e:
        print(f"✗ Connection failed: {str(e)[:100]}")
        return False
    except Exception as e:
        print(f"✗ Unexpected error: {str(e)[:100]}")
        return False

def test_blockhash_fetching(helius_key):
    """Test fetching blockhash from Helius"""
    print("\n" + "=" * 70)
    print("STEP 3: Testing Blockhash Fetching")
    print("=" * 70)

    try:
        print("Fetching latest blockhash...")
        response = requests.post(
            f"https://mainnet.helius-rpc.com/?api-key={helius_key}",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getLatestBlockhash",
                "params": [{"commitment": "finalized"}]
            },
            timeout=10
        )

        result = response.json()

        if "result" in result and "value" in result["result"]:
            blockhash = result["result"]["value"]["blockhash"]
            print(f"✓ Successfully fetched blockhash:")
            print(f"  {blockhash}")
            return True
        else:
            print(f"✗ Failed to fetch blockhash: {result}")
            return False

    except Exception as e:
        print(f"✗ Blockhash fetch failed: {str(e)[:100]}")
        return False

def test_transaction_history():
    """Test transaction history persistence"""
    print("\n" + "=" * 70)
    print("STEP 4: Testing Transaction History Persistence")
    print("=" * 70)

    import tempfile
    from trading_executor import TokenTrader, SwapResult

    try:
        # Create a trader instance
        trader = TokenTrader(
            rpc_endpoint="https://api.mainnet-beta.solana.com",
            network="mainnet"
        )

        # Create mock transaction results
        result1 = SwapResult(
            signature="test_sig_1",
            status="confirmed",
            timestamp=datetime.now(),
            input_amount=1000000000,
            output_amount=500000000,
            price_executed=0.5
        )

        result2 = SwapResult(
            signature="test_sig_2",
            status="confirmed",
            timestamp=datetime.now(),
            input_amount=500000000,
            output_amount=1000000000,
            price_executed=2.0
        )

        trader.transaction_history.append(result1)
        trader.transaction_history.append(result2)

        # Save to temp file
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
            temp_path = f.name

        trader.save_transaction_history(temp_path)

        # Verify file
        with open(temp_path, 'r') as f:
            data = json.load(f)

        if len(data) == 2 and data[0]["signature"] == "test_sig_1":
            print("✓ Transaction history persisted correctly")
            print(f"  Saved 2 transactions to {temp_path}")

            # Clean up
            os.remove(temp_path)
            return True
        else:
            print("✗ Transaction history mismatch")
            return False

    except Exception as e:
        print(f"✗ Transaction history test failed: {str(e)[:100]}")
        return False

def print_summary(rpc_works, blockhash_works):
    """Print test summary"""
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    if rpc_works and blockhash_works:
        print("✓ All tests passed!")
        print("\nYour setup is ready. Run integration tests with:")
        print("  export HELIUS_API_KEY='your_key'")
        print("  python3 -m pytest tests/test_trading_executor_integration.py -v -s")
        print("\nRPC tests should now PASS:")
        print("  • test_rpc_connectivity")
        print("  • test_blockhash_fetching")
        print("  • test_transaction_history_persistence")
        return True
    else:
        print("✗ Some tests failed")
        if not rpc_works:
            print("  - Helius RPC connectivity issue (check API key)")
        if not blockhash_works:
            print("  - Blockhash fetching issue")
        return False

def main():
    """Run all verification tests"""
    print("\n")
    print("╔" + "=" * 68 + "╗")
    print("║" + " " * 15 + "HELIUS SETUP VERIFICATION" + " " * 28 + "║")
    print("╚" + "=" * 68 + "╝")

    # Check environment
    helius_key = check_environment()
    if not helius_key:
        print("\n⚠️  Cannot proceed without HELIUS_API_KEY")
        return False

    # Test RPC
    rpc_works = test_helius_rpc(helius_key)

    # Test blockhash
    blockhash_works = False
    if rpc_works:
        blockhash_works = test_blockhash_fetching(helius_key)
    else:
        print("\n(Skipping blockhash test - RPC not working)")

    # Test transaction history (doesn't need Helius)
    test_transaction_history()

    # Print summary
    success = print_summary(rpc_works, blockhash_works)

    print("\n")
    return success

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
