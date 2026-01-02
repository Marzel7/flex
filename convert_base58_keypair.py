#!/usr/bin/env python3
"""
Convert Base58 private key to keypair.json and environment variable format
"""

import json
import sys
import os

def convert_base58_to_keypair(base58_string):
    """Convert Base58 private key to keypair format"""
    try:
        from solders.keypair import Keypair
        import base58

        print(f"\n{'='*70}")
        print("Converting Base58 Private Key to Keypair")
        print(f"{'='*70}\n")

        print(f"Input (first 20 chars): {base58_string[:20]}...")

        # Decode Base58 to bytes
        print("Decoding Base58...")
        private_key_bytes = base58.b58decode(base58_string)

        print(f"✓ Decoded to {len(private_key_bytes)} bytes")

        # Create keypair from private key
        print("Creating keypair from secret key...")
        keypair = Keypair.from_bytes(private_key_bytes)

        # Get the array format (for env var)
        keypair_array = list(private_key_bytes)

        # Get public key (wallet address)
        wallet_address = str(keypair.pubkey())

        print(f"✓ Keypair created successfully\n")

        # Save as keypair.json file
        print("Saving to test_keypair.json...")
        with open("test_keypair.json", "w") as f:
            json.dump(keypair_array, f)

        print("✓ File saved: test_keypair.json\n")

        # Show results
        print(f"{'='*70}")
        print("RESULTS")
        print(f"{'='*70}\n")

        print(f"✅ Your wallet address:")
        print(f"   {wallet_address}\n")

        print(f"✅ Keypair file created: test_keypair.json")
        print(f"   Size: {len(keypair_array)} numbers\n")

        print(f"{'='*70}")
        print("ENVIRONMENT VARIABLE")
        print(f"{'='*70}\n")

        env_var_value = json.dumps(keypair_array)
        print(f"Copy and paste this command:\n")
        print(f"export TRADING_KEYPAIR='{env_var_value}'")
        print(f"export HELIUS_API_KEY=\"0ae07551-32df-4d9d-af2a-1925fb7f561f\"\n")

        print(f"{'='*70}")
        print("NEXT STEPS")
        print(f"{'='*70}\n")

        print("1. Fund your wallet with 0.5+ SOL:")
        print(f"   https://solscan.io/account/{wallet_address}\n")

        print("2. Once funded, run one of these:\n")

        print("   Option A (with env var - recommended):")
        print("   export TRADING_KEYPAIR='[...]'")
        print("   export HELIUS_API_KEY=\"...\"")
        print("   python3 test_buy_only.py\n")

        print("   Option B (with file):")
        print("   export HELIUS_API_KEY=\"...\"")
        print("   python3 test_buy_only.py")
        print("   # Then enter: test_keypair.json\n")

        print("   Option C (setup helper):")
        print("   python3 setup_trading_env.py\n")

        return wallet_address

    except Exception as e:
        print(f"❌ Error: {e}")
        print(f"\nMake sure your Base58 string is correct.")
        print(f"It should start with a number (like 4rcKun...)")
        return None


def main():
    """Main function"""

    # Check if key provided as argument
    if len(sys.argv) > 1:
        base58_key = sys.argv[1]
    else:
        # Prompt for input
        print("\n" + "="*70)
        print("Base58 Private Key Converter")
        print("="*70 + "\n")

        base58_key = input("Enter your Base58 private key: ").strip()

        if not base58_key:
            print("❌ No key provided")
            return

    if len(base58_key) < 20:
        print("❌ Key seems too short. Expected Base58 string (80+ characters)")
        return

    # Convert
    wallet = convert_base58_to_keypair(base58_key)

    if wallet:
        print(f"{'='*70}")
        print("✅ Conversion complete! Wallet address: {wallet[:16]}...")
        print(f"{'='*70}\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⏹️ Cancelled")
    except Exception as e:
        print(f"\n❌ Error: {e}")
