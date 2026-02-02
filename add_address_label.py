#!/usr/bin/env python3
"""
Quick tool to add labeled addresses to the database.

Usage:
    python3 add_address_label.py <address> <label_name> [category] [description]

Examples:
    python3 add_address_label.py 5g7yNHy... "GMGN Fees Vault 5" feevault
    python3 add_address_label.py 2snHHre... "deBridge" bridge "Cross-chain bridge"
"""

import sys
from solscan_address_tagger import save_address_label, tag_address_if_needed, format_address_with_label

def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return

    address = sys.argv[1]
    label_name = sys.argv[2]
    category = sys.argv[3] if len(sys.argv) > 3 else "unknown"
    description = " ".join(sys.argv[4:]) if len(sys.argv) > 4 else f"{label_name}"

    print(f"Adding address label...")
    print(f"  Address: {address[:16]}...")
    print(f"  Label:   {label_name}")
    print(f"  Category: {category}")
    print(f"  Desc:    {description}")

    # Add it
    save_address_label(address, label_name, category, description, "manual")

    # Verify
    label = tag_address_if_needed(address)
    if label:
        formatted = format_address_with_label(address, label)
        print(f"\n✓ Success! {formatted}")
    else:
        print(f"\n✗ Failed to add label")

if __name__ == "__main__":
    main()
