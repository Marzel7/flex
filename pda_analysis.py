#!/usr/bin/env python3
"""
Decode pump.fun CREATE instruction data and test PDA derivations
"""
import base64, struct, sys

try:
    import base58
except ImportError:
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "base58", "-q"])
    import base58

try:
    from solders.pubkey import Pubkey
except ImportError:
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "solders", "-q"])
    from solders.pubkey import Pubkey

PUMP = Pubkey.from_string("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P")

# CREATE instruction data (first pump.fun instruction in each CREATE tx)
create_datas = {
    "Gaynald": "3y7hsuWzMx93Kbs582pEZsBWjPdhAJ4vy2uP4biWne6F2bQy7Qj7KmrC1x747nxWxaNQGbG5G8BEiEWAFMU6j3svN6897AtRXc7n",
    "TRUMPCUM": "5QAozyahaTZXnzeVwcNT2qSE4DLV21HoU9shtj3u9qet3zEnrxm28AxZ3ST3YUqJZd5z7fUnacT1j6jB6XJ4kTEj7emFWF6ewh58",
}

print("=" * 70)
print("DECODE CREATE INSTRUCTION DATA")
print("=" * 70)

for name, data_b58 in create_datas.items():
    raw = base58.b58decode(data_b58)
    print(f"\n{name}: raw instruction ({len(raw)} bytes)")
    print(f"  discriminator (8 bytes): {raw[:8].hex()}")

    offset = 8
    try:
        name_len = struct.unpack_from('<I', raw, offset)[0]; offset += 4
        token_name = raw[offset:offset+name_len].decode('utf-8', errors='replace'); offset += name_len
        sym_len = struct.unpack_from('<I', raw, offset)[0]; offset += 4
        symbol = raw[offset:offset+sym_len].decode('utf-8', errors='replace'); offset += sym_len
        uri_len = struct.unpack_from('<I', raw, offset)[0]; offset += 4
        uri = raw[offset:offset+uri_len].decode('utf-8', errors='replace'); offset += uri_len

        print(f"  token_name: {token_name!r}")
        print(f"  symbol: {symbol!r}")
        print(f"  uri: {uri[:120]!r}")
        remaining = len(raw) - offset
        print(f"  remaining bytes after uri: {remaining}")
        if remaining > 0:
            extra = raw[offset:]
            print(f"  extra hex: {extra.hex()}")
            # Could be additional params like initial_buy amount
            if remaining == 8:
                val = struct.unpack_from('<Q', extra)[0]
                print(f"  extra as u64 (initial buy lamports?): {val}")
    except Exception as e:
        print(f"  parse error: {e}")

print()
print("=" * 70)
print("PDA DERIVATION TESTS")
print("=" * 70)

launches = [
    ("Gaynald", "8RW8MeyB9AzBS9TiZtTtuCh6yzib6PLrC7bRtmh3bfJe", "CUdwRcEH2fqEuKQkALbHzpv81XUKbEamCEPreHSBpump"),
    ("TRUMPCUM", "6NV84W76QUxAicY4dGACtuuTfCr6QJU3ZfyRmRP6CgY5", "8AYsSaPyptd6dgQ1dvXsEbPZMuzM6MMRQXAJM9pQpump"),
    ("Sellategy", "HLucJQyQy6XmiudWYE5XA4t5y8o5WAJr1CoFE2BsFA2a", "3Cj1XSskaWrKMo2xN4ucnUi94JFZXTSePGAv4sZApump"),
]

# Known account in every CREATE tx = TSLvdd1pWpHVjahSpsvCXUbgwsL3JAcvokwaKt1eokM
# Let's confirm this is "global" PDA
global_pda, g_bump = Pubkey.find_program_address([b"global"], PUMP)
print(f"\nGlobal PDA: {global_pda} bump={g_bump}")
print(f"Expected:   TSLvdd1pWpHVjahSpsvCXUbgwsL3JAcvokwaKt1eokM")
print(f"Match: {str(global_pda) == 'TSLvdd1pWpHVjahSpsvCXUbgwsL3JAcvokwaKt1eokM'}")

for name, creator_str, mint_str in launches:
    creator = Pubkey.from_string(creator_str)
    mint = Pubkey.from_string(mint_str)
    creator_bytes = bytes(creator)
    mint_bytes = bytes(mint)

    print(f"\n{name}:")
    print(f"  creator: {creator_str}")
    print(f"  actual mint: {mint_str}")

    seed_combos = [
        ("mint+creator", [b"mint", creator_bytes]),
        ("mint", [b"mint"]),
        ("creator", [creator_bytes]),
        ("bonding-curve+mint", [b"bonding-curve", mint_bytes]),
        ("token+creator", [b"token", creator_bytes]),
    ]

    for label, seeds in seed_combos:
        try:
            derived, bump = Pubkey.find_program_address(seeds, PUMP)
            match = str(derived) == mint_str
            print(f"  [{label}]: {derived} bump={bump} MATCH={match}")
        except Exception as e:
            print(f"  [{label}]: error {e}")

# The pump.fun mint is NOT a PDA derived from creator alone - it's signed as a keypair
# (the mint is a signer in the tx). Let's verify this from the tx account data.
# In a Solana tx, the signer bit is set for accounts that signed.
# The mint IS [1] in accountKeys and appears as writable/signer in some pump.fun versions.

print()
print("=" * 70)
print("MINT SIGNER ANALYSIS")
print("=" * 70)
print()
print("From Gaynald CREATE tx accounts (accountData):")
print("  [0] 8RW8MeyB9... (creator/fee_payer) — SIGNER")
print("  [1] CUdwRcEH2... (MINT) — is it a signer?")
print()
print("If the mint is in accountKeys as a signer (not writable-only),")
print("it was generated as a keypair on the client, not as a PDA.")
print()
print("The pump.fun create instruction signature:")
print("  discriminator for 'create' = sha256('global:create')[:8]")
import hashlib
disc = hashlib.sha256(b"global:create").digest()[:8]
print(f"  Expected: {disc.hex()}")
print()
# From Gaynald decode: discriminator is raw[:8]
raw = base58.b58decode("3y7hsuWzMx93Kbs582pEZsBWjPdhAJ4vy2uP4biWne6F2bQy7Qj7KmrC1x747nxWxaNQGbG5G8BEiEWAFMU6j3svN6897AtRXc7n")
print(f"  Actual from tx: {raw[:8].hex()}")
print(f"  Match: {raw[:8].hex() == disc.hex()}")
