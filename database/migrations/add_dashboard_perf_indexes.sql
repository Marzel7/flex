-- Speed up dashboard creator enrichment and farm-cluster lookups.
CREATE INDEX IF NOT EXISTS idx_token_analysis_earliest_creator
ON token_analysis(earliest_tx_creator);

CREATE INDEX IF NOT EXISTS idx_farm_cluster_members_wallet_role
ON farm_cluster_members(wallet_address, wallet_role);
