ALTER TABLE network_cex_infra_flags ADD COLUMN has_cex_funder INTEGER DEFAULT 0;
ALTER TABLE network_cex_infra_flags ADD COLUMN has_infra_funder INTEGER DEFAULT 0;
ALTER TABLE network_cex_infra_flags ADD COLUMN cex_funder_addresses TEXT DEFAULT '[]';
ALTER TABLE network_cex_infra_flags ADD COLUMN infra_funder_addresses TEXT DEFAULT '[]';
