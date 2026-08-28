CREATE TABLE IF NOT EXISTS operator_subtypes (
    subtype_id TEXT PRIMARY KEY,
    parent_operator_id TEXT NOT NULL REFERENCES operators(operator_id),
    candidate_id TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    relationship_type TEXT NOT NULL,
    qualification_status TEXT NOT NULL,
    attribution_mode TEXT NOT NULL,
    monitoring_mode TEXT NOT NULL,
    automation_state TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    evidence_digest TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS operator_subtype_projection (
    subtype_id TEXT NOT NULL REFERENCES operator_subtypes(subtype_id),
    mint TEXT NOT NULL,
    branch TEXT NOT NULL,
    evidence_reference TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    projected_at INTEGER NOT NULL,
    PRIMARY KEY(subtype_id,mint)
);
CREATE INDEX IF NOT EXISTS ix_operator_subtype_projection_mint ON operator_subtype_projection(mint);
