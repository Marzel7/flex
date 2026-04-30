-- Add time_span_seconds to upstream_network_bridge for slow-bridge suppression.
-- Safe to re-run (ALTER TABLE is idempotent via try/ignore at application layer).
ALTER TABLE upstream_network_bridge ADD COLUMN time_span_seconds INTEGER DEFAULT NULL;
ALTER TABLE upstream_network_bridge ADD COLUMN funders_bridged_count INTEGER DEFAULT NULL;
