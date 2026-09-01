# RA0 — retained-acquisition storage amplification diagnosis and deduplicated retention preflight

## Immediate objective
Diagnose why `retained_acquisition.db` reached ~60+ GB and define a bounded, no-write preflight that proves whether safe deduplication exists before H11.

## Read-only observations (authoritative)
- Database: `database/evidence_platform/production/retained_acquisition.db`
- File size from filesystem: **57G**
- SQLite metadata: `page_size=4096`, `page_count≈15,022,750`, `freelist_count=0`
- Live retention rows: `retained_acquisition_observations` approximately **1,806,935**
- Outcomes rows: approximately **1,806,5xx**
- Gaps rows: **0**
- Distinct IDs:
  - acquisition IDs: **1,806,862**
  - correlation IDs: **9,038**
  - launch mints: **5,897**
- Retained time window: **2026-08-11 11:27:53** → **~2026-08-18 10:04:16** (~7 days)
- 10,000-row payload sample: avg `85.05 KiB`, min `1.34 KiB`, max `4312 KiB` (not globally representative)

## Root-cause candidate from writer
In `src/acquisition/retained_observations.py`, `observation_id` is SHA-256 over:
- `schema_version`
- full `metadata`
- http method
- sanitized URL
- request payload
- response status
- `artifact_digest`

`metadata` comes from `AcquisitionMetadata`, which includes non-idempotent fields like `timestamp` (and `retry_count`, request-specific identifiers). Therefore retries and normal re-requests naturally produce distinct identities even for semantically identical traffic.

This means current `INSERT OR IGNORE` deduplication is effectively scoped to exact-attempt identity rather than semantic/response deduplication.

## RA0 preflight findings
- `freelist_count=0` indicates the database is not bloated by reclaimable dead pages.
- The growth appears to be **live payload growth**, not recoverable fragmentation.
- Correlation space is much smaller than observation space, which is consistent with repeated attempts under shared correlation contexts, and therefore a primary dedup leverage point.
- Given timestamp participation in the hash identity, dedup across retries is currently not possible without changing identity strategy.

## Proposed RA0 bounded analysis-only experiments (no production mutation)
1. **Identity stability audit**
   - Confirm all fields currently preventing dedup (`metadata.timestamp` and potentially `acquisition_id`/`acquisition_id` uniqueness) by tracing `AcquisitionMetadata` generation paths.
2. **Entropy pressure audit (offline SQL + sampled rows)**
   - For each time slice, compute:
     - attempts per `acquisition_id`
     - attempts per `correlation_id`
     - attempts per `(correlation_id, launch_mint, request_payload, http_method, url, response_status)`
3. **Payload amplification audit**
   - Quantify what fraction of bytes is carried in `payload_json` versus artifact references
   - Check high-percentile payload sizes in production to validate if long-tail responses dominate
4. **Safe preflight gate design**
   - Define a deterministic, bounded semantic key (for this table only):
     - `(launch_mint, correlation_id, http_method, sanitized_url, request_payload, response_status, http_body_digest)`
   - Keep raw payload retention for replay fidelity but normalize into a content-addressed payload side table for shared content.

## Stop conditions for RA0 (do not execute during RA0)
- No production write, VACUUM, migration, or service-restart action in this milestone.
- If the schema is shown to be growing faster than bounded retry rate under same semantic key, classify as `HOLD_BLOCKER_RETAINED_ACQUISITION_AMPLIFICATION_UNBOUNDED` and require capacity gate before H11.
