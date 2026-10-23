CREATE TABLE wt_treasury_review (
  treasury TEXT PRIMARY KEY,
  transfer_pct INTEGER,
  out_sol REAL,
  recipients INTEGER,
  micro_pings INTEGER,
  detected_via TEXT,           -- micro_ping | fanout_walkup | manual
  status TEXT DEFAULT 'PENDING_REVIEW',  -- PENDING_REVIEW | CONFIRMED | REJECTED
  reviewed_by TEXT,
  detected_at INTEGER,
  reviewed_at INTEGER, reviewer TEXT, confidence TEXT, notes TEXT, evidence_json TEXT, subprov_wallet TEXT, creator_wallet TEXT, token_mint TEXT, distinct_subprovs INTEGER DEFAULT 1, distinct_creators INTEGER DEFAULT 1, evidence_sigs TEXT, evidence_subprovs TEXT, evidence_creators TEXT, evidence_mints TEXT, has_walkback_evidence INTEGER DEFAULT 0, first_walkback_at INTEGER, last_walkback_at INTEGER, discovery_score INTEGER, discovery_reasons TEXT)