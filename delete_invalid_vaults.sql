-- Find when FVNediAcMzQ69RsnYLnijFRqT7tC7u1yYmiYsx3Gpump was created
SELECT created_at FROM token_pool_accounts WHERE mint = 'FVNediAcMzQ69RsnYLnijFRqT7tC7u1yYmiYsx3Gpump' LIMIT 1;

-- Delete all vaults created BEFORE FVNediAc
-- Replace TIMESTAMP below with the actual created_at value from above query
DELETE FROM token_pool_accounts
WHERE created_at < '2026-03-24 09:29:00'  -- ADJUST THIS TIMESTAMP
AND vault_validation_status IN ('validated', 'pending');

-- Verify deletion - should show only recent valid pools
SELECT COUNT(*) as total_pools, COUNT(CASE WHEN vault_validation_status='validated' THEN 1 END) as validated FROM token_pool_accounts;
