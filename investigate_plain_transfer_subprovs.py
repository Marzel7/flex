#!/usr/bin/env python3
"""
Investigate: Treasury → plain SOL transfer → recipient that became a wrap-close subprov
"""

import sqlite3

OPS_DB = "database/wt_ops_v2.db"
ARCHIVE_DB = "database/flex_investigation_archive.db"
LIVE_DB = "database/flex_complete_database.db"

def conn(path):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    return c

ops = conn(OPS_DB)
# attach archive
ops.execute(f"ATTACH '{ARCHIVE_DB}' AS arch")
ops.execute(f"ATTACH '{LIVE_DB}' AS live")

print("="*70)
print("STEP 1: Confirmed Treasuries")
print("="*70)
treasuries = ops.execute("""
    SELECT treasury, transfer_pct, out_sol, recipients, confirmed_at, provenance
    FROM wt_confirmed_treasuries
    ORDER BY out_sol DESC
""").fetchall()
print(f"Total confirmed treasuries: {len(treasuries)}")
for t in treasuries:
    print(f"  {t['treasury'][:12]}… | out={t['out_sol'] or 0:.1f} SOL | rcpts={t['recipients']} | prov={t['provenance']}")

treasury_set = {t['treasury'] for t in treasuries}

print("\n" + "="*70)
print("STEP 2: wt_treasury_funders — outbound transfers FROM confirmed treasuries")
print("="*70)
# wt_treasury_funders tracks (funder→treasury) linkage; we need (treasury→recipient)
# Let's check what's in there
sample = ops.execute("""
    SELECT funder, treasury, fund_count, total_sol, max_sol, is_subprov_sweep
    FROM wt_treasury_funders
    LIMIT 10
""").fetchall()
print("Sample wt_treasury_funders rows:")
for r in sample:
    print(f"  funder={r['funder'][:12]}… treasury={r['treasury'][:12]}… total={r['total_sol']:.1f} sweep={r['is_subprov_sweep']}")

# wt_treasury_funders is funder→treasury direction.
# For treasury→subprov plain transfers, look at wt_webhook_hits (direction=OUTBOUND, tx_type=TRANSFER)
# and wt_discovered_subprovs immediate_funder
print("\n" + "="*70)
print("STEP 3: wt_webhook_hits — large outbound plain transfers from confirmed treasuries")
print("="*70)

tx_types = ops.execute("""
    SELECT DISTINCT tx_type FROM wt_webhook_hits LIMIT 30
""").fetchall()
print("tx_type values:", [r['tx_type'] for r in tx_types])

directions = ops.execute("""
    SELECT DISTINCT direction FROM wt_webhook_hits LIMIT 20
""").fetchall()
print("direction values:", [r['direction'] for r in directions])

# Large outbound transfers from known treasuries
plain_transfers = ops.execute("""
    SELECT wallet_address, counterparty, amount_sol, tx_type, direction, block_time
    FROM wt_webhook_hits
    WHERE wallet_address IN (SELECT treasury FROM wt_confirmed_treasuries)
      AND direction = 'OUTBOUND'
      AND amount_sol > 10
      AND (tx_type LIKE '%TRANSFER%' OR tx_type LIKE '%transfer%')
    ORDER BY amount_sol DESC
    LIMIT 100
""").fetchall()
print(f"\nLarge (>10 SOL) outbound TRANSFER hits from confirmed treasuries: {len(plain_transfers)}")
for r in plain_transfers[:20]:
    print(f"  {r['wallet_address'][:12]}… → {str(r['counterparty'])[:12]}… | {r['amount_sol']:.1f} SOL | type={r['tx_type']}")

print("\n" + "="*70)
print("STEP 4: wt_discovered_subprovs — immediate_funder = confirmed treasury")
print("="*70)

direct_subprovs = ops.execute("""
    SELECT ds.subprov, ds.immediate_funder, ds.funding_mechanism, ds.wrap_close_count,
           ds.creator_count, ds.create_count, ds.state, ds.treasury, ds.treasury_known,
           ds.buy_swarm_count
    FROM wt_discovered_subprovs ds
    WHERE ds.immediate_funder IN (SELECT treasury FROM wt_confirmed_treasuries)
    ORDER BY ds.wrap_close_count DESC
""").fetchall()
print(f"Subprovs whose immediate_funder is a confirmed treasury: {len(direct_subprovs)}")
for r in direct_subprovs:
    print(f"  subprov={r['subprov'][:12]}… funder={r['immediate_funder'][:12]}… mech={r['funding_mechanism']} wc={r['wrap_close_count']} cc={r['creator_count']} state={r['state']}")

# Also check: subprovs where treasury column = confirmed treasury
print("\n--- Also: subprovs whose treasury column is a confirmed treasury ---")
treasury_col_subprovs = ops.execute("""
    SELECT ds.subprov, ds.immediate_funder, ds.funding_mechanism, ds.wrap_close_count,
           ds.creator_count, ds.state, ds.treasury
    FROM wt_discovered_subprovs ds
    WHERE ds.treasury IN (SELECT treasury FROM wt_confirmed_treasuries)
    ORDER BY ds.wrap_close_count DESC
    LIMIT 30
""").fetchall()
print(f"Subprovs with treasury in confirmed set: {len(treasury_col_subprovs)}")
for r in treasury_col_subprovs[:20]:
    print(f"  subprov={r['subprov'][:12]}… imm_funder={str(r['immediate_funder'])[:12]}… mech={r['funding_mechanism']} wc={r['wrap_close_count']} state={r['state']}")

print("\n" + "="*70)
print("STEP 5: funding_mechanism breakdown for all subprovs")
print("="*70)
mech_dist = ops.execute("""
    SELECT funding_mechanism, COUNT(*) as cnt, SUM(wrap_close_count) as total_wc
    FROM wt_discovered_subprovs
    GROUP BY funding_mechanism
    ORDER BY cnt DESC
""").fetchall()
for r in mech_dist:
    print(f"  {r['funding_mechanism']} | count={r['cnt']} | total_wrap_close={r['total_wc']}")

print("\n" + "="*70)
print("STEP 6: wt_wrap_close_candidates — subprov_wallets that funded many creators")
print("        Cross-ref with wt_treasury_funders to see if subprov's funder is a treasury")
print("="*70)

wcc_stats = ops.execute("""
    SELECT wcc.subprov_wallet,
           COUNT(DISTINCT wcc.creator) as creators_funded,
           SUM(wcc.base_amount_sol) as total_sol_deployed,
           MIN(wcc.funded_at) as first_fund,
           MAX(wcc.funded_at) as last_fund
    FROM wt_wrap_close_candidates wcc
    GROUP BY wcc.subprov_wallet
    ORDER BY creators_funded DESC
    LIMIT 30
""").fetchall()
print(f"Top subprov_wallets by creators funded (from wrap_close_candidates):")
for r in wcc_stats:
    print(f"  {r['subprov_wallet'][:12]}… | creators={r['creators_funded']} | sol={r['total_sol_deployed']:.1f}")

# Now check if those top subprov_wallets have a confirmed treasury as their funder
top_subprov_wallets = [r['subprov_wallet'] for r in wcc_stats]

print("\n--- Checking if top wrap-close subprovs have a confirmed treasury as immediate_funder ---")
if top_subprov_wallets:
    placeholders = ','.join(['?']*len(top_subprov_wallets))
    cross_ref = ops.execute(f"""
        SELECT ds.subprov, ds.immediate_funder, ds.funding_mechanism, ds.wrap_close_count,
               ds.creator_count, ds.state,
               CASE WHEN ds.immediate_funder IN (SELECT treasury FROM wt_confirmed_treasuries) THEN 1 ELSE 0 END as funder_is_treasury
        FROM wt_discovered_subprovs ds
        WHERE ds.subprov IN ({placeholders})
        ORDER BY ds.wrap_close_count DESC
    """, top_subprov_wallets).fetchall()
    for r in cross_ref:
        flag = " <-- TREASURY-FUNDED" if r['funder_is_treasury'] else ""
        print(f"  {r['subprov'][:12]}… imm_funder={str(r['immediate_funder'])[:12]}… mech={r['funding_mechanism']} wc={r['wrap_close_count']}{flag}")

print("\n" + "="*70)
print("STEP 7: wt_treasury_funders — entries where 'funder' is a confirmed treasury")
print("        (i.e., treasury funding another wallet that was later classified as treasury)")
print("="*70)
tf_treasury_to_treasury = ops.execute("""
    SELECT tf.funder, tf.treasury, tf.fund_count, tf.total_sol, tf.max_sol, tf.is_subprov_sweep
    FROM wt_treasury_funders tf
    WHERE tf.funder IN (SELECT treasury FROM wt_confirmed_treasuries)
    ORDER BY tf.total_sol DESC
    LIMIT 30
""").fetchall()
print(f"wt_treasury_funders rows where funder=confirmed treasury: {len(tf_treasury_to_treasury)}")
for r in tf_treasury_to_treasury:
    print(f"  {r['funder'][:12]}… → {r['treasury'][:12]}… | total={r['total_sol']:.1f} SOL | max={r['max_sol']:.1f} | sweep={r['is_subprov_sweep']}")

print("\n" + "="*70)
print("STEP 8: wt_fanout_events — any subprovs whose treasury_wallet is a confirmed treasury")
print("="*70)
fanout_treasury = ops.execute("""
    SELECT fe.subprov_wallet, fe.treasury_wallet, fe.fanout_count, fe.total_sol, fe.creates_fired, fe.buy_swarms
    FROM wt_fanout_events fe
    WHERE fe.treasury_wallet IN (SELECT treasury FROM wt_confirmed_treasuries)
    ORDER BY fe.total_sol DESC
    LIMIT 20
""").fetchall()
print(f"Fanout events linked to confirmed treasury: {len(fanout_treasury)}")
for r in fanout_treasury:
    print(f"  {r['subprov_wallet'][:12]}… treasury={r['treasury_wallet'][:12]}… fanout={r['fanout_count']} sol={r['total_sol']:.1f} creates={r['creates_fired']}")

print("\n" + "="*70)
print("STEP 9: EFKVdK / FCKTp7 pattern — check if FCKTp7 appears in our DBs")
print("="*70)
target_treasury = "EFKVdK"
target_recipient = "FCKTp7"

# Check partial matches
for wallet, label in [(target_treasury, "EFKVdK"), (target_recipient, "FCKTp7")]:
    rows = ops.execute(f"""
        SELECT 'wt_confirmed_treasuries' as tbl, treasury as wallet FROM wt_confirmed_treasuries WHERE treasury LIKE '{wallet}%'
        UNION ALL
        SELECT 'wt_discovered_subprovs', subprov FROM wt_discovered_subprovs WHERE subprov LIKE '{wallet}%'
        UNION ALL
        SELECT 'wt_discovered_subprovs(imm_funder)', immediate_funder FROM wt_discovered_subprovs WHERE immediate_funder LIKE '{wallet}%'
        UNION ALL
        SELECT 'wt_wrap_close_candidates(subprov)', subprov_wallet FROM wt_wrap_close_candidates WHERE subprov_wallet LIKE '{wallet}%'
        UNION ALL
        SELECT 'wt_confirmed_treasury_webhooks', treasury FROM wt_confirmed_treasury_webhooks WHERE treasury LIKE '{wallet}%'
    """).fetchall()
    print(f"  {label}… found in: {[r['tbl'] for r in rows] if rows else 'NONE'}")

print("\n" + "="*70)
print("STEP 10: Full plain-transfer pattern search")
print("         wt_webhook_hits outbound >10 SOL from treasury → recipient not yet a subprov")
print("="*70)
# Find all outbound hits from confirmed treasuries
all_outbound = ops.execute("""
    SELECT wh.wallet_address as treasury, wh.counterparty as recipient, wh.amount_sol, wh.tx_type, wh.block_time
    FROM wt_webhook_hits wh
    WHERE wh.wallet_address IN (SELECT treasury FROM wt_confirmed_treasuries)
      AND wh.direction = 'OUTBOUND'
      AND wh.amount_sol > 10
    ORDER BY wh.amount_sol DESC
""").fetchall()
print(f"All outbound >10 SOL hits from confirmed treasuries: {len(all_outbound)}")
type_counts = {}
for r in all_outbound:
    t = r['tx_type'] or 'NULL'
    type_counts[t] = type_counts.get(t, 0) + 1
print("  By tx_type:", type_counts)

# Filter for non-wrap-close types
non_wc = [r for r in all_outbound if r['tx_type'] and 'WRAP' not in r['tx_type'].upper() and 'CLOSE' not in r['tx_type'].upper()]
print(f"  Non-wrap-close outbound: {len(non_wc)}")
for r in non_wc[:20]:
    recipient = r['recipient'] or 'None'
    # Check if recipient is in subprovs
    in_subprov = ops.execute("SELECT 1 FROM wt_discovered_subprovs WHERE subprov=? LIMIT 1", (recipient,)).fetchone()
    in_wcc = ops.execute("SELECT COUNT(DISTINCT creator) as c FROM wt_wrap_close_candidates WHERE subprov_wallet=?", (recipient,)).fetchone()
    wcc_count = in_wcc['c'] if in_wcc else 0
    subprov_flag = " IN_SUBPROVS" if in_subprov else ""
    wcc_flag = f" WCC={wcc_count}" if wcc_count > 0 else ""
    print(f"  {r['treasury'][:12]}… → {recipient[:12]}… | {r['amount_sol']:.1f} SOL | {r['tx_type']}{subprov_flag}{wcc_flag}")

print("\n" + "="*70)
print("STEP 11: wt_capital_reloads — may track large treasury→subprov funding events")
print("="*70)
try:
    cr_cols = ops.execute("PRAGMA table_info(wt_capital_reloads)").fetchall()
    print("wt_capital_reloads columns:", [c['name'] for c in cr_cols])
    cr_rows = ops.execute("SELECT * FROM wt_capital_reloads LIMIT 5").fetchall()
    print(f"Row count: {ops.execute('SELECT COUNT(*) FROM wt_capital_reloads').fetchone()[0]}")
    for r in cr_rows:
        print("  ", dict(r))
except Exception as e:
    print(f"  Error: {e}")

print("\n" + "="*70)
print("STEP 12: wt_subprov_evidence — check funding mechanism evidence")
print("="*70)
try:
    se_cols = ops.execute("PRAGMA table_info(wt_subprov_evidence)").fetchall()
    print("wt_subprov_evidence columns:", [c['name'] for c in se_cols])
    se_count = ops.execute("SELECT COUNT(*) FROM wt_subprov_evidence").fetchone()[0]
    print(f"Row count: {se_count}")
    se_sample = ops.execute("SELECT * FROM wt_subprov_evidence LIMIT 5").fetchall()
    for r in se_sample:
        print("  ", dict(r))
except Exception as e:
    print(f"  Error: {e}")

print("\n" + "="*70)
print("STEP 13: State of all discovered subprovs by funding_mechanism + state")
print("="*70)
state_mech = ops.execute("""
    SELECT funding_mechanism, state, COUNT(*) as cnt,
           AVG(wrap_close_count) as avg_wc, SUM(wrap_close_count) as total_wc,
           AVG(creator_count) as avg_cc
    FROM wt_discovered_subprovs
    GROUP BY funding_mechanism, state
    ORDER BY total_wc DESC
""").fetchall()
for r in state_mech:
    print(f"  mech={r['funding_mechanism']} state={r['state']} cnt={r['cnt']} total_wc={r['total_wc']} avg_wc={r['avg_wc']:.1f}")

print("\n" + "="*70)
print("STEP 14: Subprovs with UNKNOWN or NULL funding_mechanism but high wrap_close_count")
print("         These are candidates for the 'plain transfer capitalised' pattern")
print("="*70)
unknown_mech = ops.execute("""
    SELECT ds.subprov, ds.immediate_funder, ds.funding_mechanism, ds.wrap_close_count,
           ds.creator_count, ds.state, ds.treasury,
           CASE WHEN ds.immediate_funder IN (SELECT treasury FROM wt_confirmed_treasuries) THEN 1 ELSE 0 END as funder_is_confirmed_treasury
    FROM wt_discovered_subprovs ds
    WHERE (ds.funding_mechanism IS NULL OR ds.funding_mechanism = 'UNKNOWN' OR ds.funding_mechanism NOT LIKE '%WRAP%')
      AND ds.wrap_close_count > 0
    ORDER BY ds.wrap_close_count DESC
    LIMIT 30
""").fetchall()
print(f"Subprovs with non-wrap-close funding_mechanism but have wrap_close_count>0: {len(unknown_mech)}")
for r in unknown_mech:
    flag = " <-- CONFIRMED TREASURY FUNDER" if r['funder_is_confirmed_treasury'] else ""
    print(f"  {r['subprov'][:12]}… mech={r['funding_mechanism']} wc={r['wrap_close_count']} imm_funder={str(r['immediate_funder'])[:12]}…{flag}")

print("\n" + "="*70)
print("STEP 15: wt_treasury_approval_audit")
print("="*70)
try:
    taa_cols = ops.execute("PRAGMA table_info(wt_treasury_approval_audit)").fetchall()
    print("columns:", [c['name'] for c in taa_cols])
    taa_sample = ops.execute("SELECT * FROM wt_treasury_approval_audit LIMIT 10").fetchall()
    for r in taa_sample:
        print("  ", dict(r))
except Exception as e:
    print(f"  Error: {e}")

print("\n" + "="*70)
print("STEP 16: Wallets in wt_wrap_close_candidates with many creators")
print("         but NOT in wt_discovered_subprovs at all — fully untracked")
print("="*70)
untracked = ops.execute("""
    SELECT wcc.subprov_wallet,
           COUNT(DISTINCT wcc.creator) as creators,
           SUM(wcc.base_amount_sol) as total_sol
    FROM wt_wrap_close_candidates wcc
    WHERE wcc.subprov_wallet NOT IN (SELECT subprov FROM wt_discovered_subprovs)
    GROUP BY wcc.subprov_wallet
    HAVING creators >= 5
    ORDER BY creators DESC
    LIMIT 20
""").fetchall()
print(f"High-volume wrap-close subprovs NOT in wt_discovered_subprovs: {len(untracked)}")
for r in untracked:
    print(f"  {r['subprov_wallet'][:12]}… | creators={r['creators']} | sol={r['total_sol']:.1f}")

print("\n" + "="*70)
print("SUMMARY: Recurring Pattern Assessment")
print("="*70)
total_subprovs = ops.execute("SELECT COUNT(*) FROM wt_discovered_subprovs").fetchone()[0]
treasury_funded_direct = ops.execute("""
    SELECT COUNT(*) FROM wt_discovered_subprovs
    WHERE immediate_funder IN (SELECT treasury FROM wt_confirmed_treasuries)
""").fetchone()[0]
plain_transfer_treasury_funded = ops.execute("""
    SELECT COUNT(*) FROM wt_discovered_subprovs
    WHERE immediate_funder IN (SELECT treasury FROM wt_confirmed_treasuries)
      AND (funding_mechanism IS NULL OR funding_mechanism NOT LIKE '%WRAP%')
      AND wrap_close_count > 0
""").fetchone()[0]
total_wcc_subprovs = ops.execute("SELECT COUNT(DISTINCT subprov_wallet) FROM wt_wrap_close_candidates").fetchone()[0]
print(f"Total discovered subprovs: {total_subprovs}")
print(f"With confirmed treasury as immediate_funder: {treasury_funded_direct}")
print(f"Treasury-funded via non-wrap-close mechanism (plain transfer pattern): {plain_transfer_treasury_funded}")
print(f"Distinct subprov_wallets in wrap_close_candidates: {total_wcc_subprovs}")

ops.close()
