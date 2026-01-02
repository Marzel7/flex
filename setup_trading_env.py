#!/usr/bin/env python3
"""
Helper script to set up environment variables for trading

This script helps you safely set up TRADING_KEYPAIR and HELIUS_API_KEY
environment variables for automated testing.
"""

import json
import os
from pathlib import Path


def setup_trading_keypair():
    """Set up TRADING_KEYPAIR from a keypair file"""
    print("\n" + "="*70)
    print("TRADING_KEYPAIR Setup")
    print("="*70)

    keypair_path = input("\nEnter path to your keypair JSON file: ").strip()

    if not keypair_path:
        print("❌ No path provided")
        return None

    try:
        with open(keypair_path) as f:
            keypair_data = json.load(f)

        # Convert to JSON string for env var
        keypair_json = json.dumps(keypair_data)

        print(f"\n✅ Keypair loaded successfully")
        print(f"   File: {keypair_path}")
        print(f"   Length: {len(keypair_json)} characters")

        return keypair_json

    except FileNotFoundError:
        print(f"❌ File not found: {keypair_path}")
        return None
    except json.JSONDecodeError:
        print(f"❌ Invalid JSON in {keypair_path}")
        return None
    except Exception as e:
        print(f"❌ Error: {e}")
        return None


def setup_helius_key():
    """Set up HELIUS_API_KEY"""
    print("\n" + "="*70)
    print("HELIUS_API_KEY Setup")
    print("="*70)

    helius_key = input("\nEnter your Helius API key: ").strip()

    if not helius_key:
        print("❌ No key provided")
        return None

    if len(helius_key) < 20:
        print("⚠️  Warning: API key seems too short")

    print(f"✅ API key accepted")
    print(f"   Length: {len(helius_key)} characters")

    return helius_key


def show_shell_commands(keypair_json: str, helius_key: str, shell_type: str = "bash"):
    """Show the commands to run in shell"""
    print("\n" + "="*70)
    print("Shell Commands to Execute")
    print("="*70)

    if shell_type == "bash":
        print("\n📋 Copy and paste these commands in your terminal:\n")
        print(f'export TRADING_KEYPAIR=\'{keypair_json}\'')
        print(f'export HELIUS_API_KEY="{helius_key}"')
        print("\n# Then run:")
        print("python3 test_buy_only.py")

    elif shell_type == "zsh":
        print("\n📋 Copy and paste these commands in your terminal:\n")
        print(f'export TRADING_KEYPAIR=\'{keypair_json}\'')
        print(f'export HELIUS_API_KEY="{helius_key}"')
        print("\n# Then run:")
        print("python3 test_buy_only.py")


def save_to_profile(keypair_json: str, helius_key: str):
    """Optionally save to shell profile for persistence"""
    print("\n" + "="*70)
    print("Save to Shell Profile (Optional)")
    print("="*70)

    save = input("\nSave to ~/.zshrc or ~/.bash_profile? (y/n): ").lower()

    if save != "y":
        print("⏭️  Skipping profile save")
        return

    profile_path = Path.home() / ".zshrc"
    if not profile_path.exists():
        profile_path = Path.home() / ".bash_profile"

    if not profile_path.exists():
        print(f"⚠️  Could not find shell profile")
        return

    print(f"\n📝 Appending to {profile_path}...")

    with open(profile_path, "a") as f:
        f.write(f"\n# Trading environment variables\n")
        f.write(f'export TRADING_KEYPAIR=\'{keypair_json}\'\n')
        f.write(f'export HELIUS_API_KEY="{helius_key}"\n')

    print(f"✅ Saved to {profile_path}")
    print(f"\n   ⚠️  Important: Run 'source {profile_path}' in new terminals")
    print(f"   Or restart your terminal for changes to take effect")


def main():
    """Main setup flow"""
    print("\n🔑 Trading Environment Setup Helper")
    print("="*70)
    print("\nThis tool helps you set up environment variables for")
    print("secure, non-interactive trading with test_buy_only.py")

    # Get keypair
    keypair_json = setup_trading_keypair()
    if not keypair_json:
        print("\n❌ Setup cancelled - keypair required")
        return

    # Get Helius key
    helius_key = setup_helius_key()
    if not helius_key:
        print("\n❌ Setup cancelled - Helius key required")
        return

    # Detect shell
    shell = os.environ.get("SHELL", "bash")
    shell_type = "zsh" if "zsh" in shell else "bash"

    # Show commands
    show_shell_commands(keypair_json, helius_key, shell_type)

    # Ask to save
    save_to_profile(keypair_json, helius_key)

    print("\n" + "="*70)
    print("✅ Setup complete!")
    print("="*70)
    print("\n📌 Next steps:")
    print("   1. Export the variables shown above")
    print("   2. Run: python3 test_buy_only.py")
    print("   3. The script will use TRADING_KEYPAIR automatically!")
    print("\n🔒 Security reminder:")
    print("   - Never share your TRADING_KEYPAIR value")
    print("   - Only use for testing with small amounts")
    print("   - Use a separate test wallet (not your main wallet)\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⏹️  Setup cancelled by user")
    except Exception as e:
        print(f"\n❌ Error: {e}")
