PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS operation_motifs (
    motif_id TEXT PRIMARY KEY,
    canonicalization_version TEXT NOT NULL,
    replay_version TEXT NOT NULL,
    canonical_graph_json TEXT NOT NULL,
    canonical_graph_digest TEXT NOT NULL,
    definition_digest TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS motif_occurrences (
    occurrence_id TEXT PRIMARY KEY,
    motif_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL,
    payload_digest TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS motif_occurrences_by_motif
ON motif_occurrences(motif_id, occurrence_id);

CREATE TABLE IF NOT EXISTS motif_references (
    motif_id TEXT NOT NULL,
    reference_type TEXT NOT NULL,
    reference_id TEXT NOT NULL,
    PRIMARY KEY(motif_id, reference_type, reference_id)
);

CREATE TRIGGER IF NOT EXISTS operation_motifs_no_update BEFORE UPDATE ON operation_motifs
BEGIN SELECT RAISE(ABORT, 'immutable operation motif cannot be updated'); END;
CREATE TRIGGER IF NOT EXISTS operation_motifs_no_delete BEFORE DELETE ON operation_motifs
BEGIN SELECT RAISE(ABORT, 'immutable operation motif cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS motif_occurrences_no_update BEFORE UPDATE ON motif_occurrences
BEGIN SELECT RAISE(ABORT, 'immutable motif occurrence cannot be updated'); END;
CREATE TRIGGER IF NOT EXISTS motif_occurrences_no_delete BEFORE DELETE ON motif_occurrences
BEGIN SELECT RAISE(ABORT, 'immutable motif occurrence cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS motif_references_no_update BEFORE UPDATE ON motif_references
BEGIN SELECT RAISE(ABORT, 'immutable motif reference cannot be updated'); END;
CREATE TRIGGER IF NOT EXISTS motif_references_no_delete BEFORE DELETE ON motif_references
BEGIN SELECT RAISE(ABORT, 'immutable motif reference cannot be deleted'); END;
