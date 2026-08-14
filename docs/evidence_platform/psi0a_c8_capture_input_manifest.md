# PSI0A-C8 Canonical Capture-Input Manifest

PSI0A-C8 explicitly supersedes the uncommitted ad-hoc PSI0A-B/C input tuples. It does not claim that those historical tuples were recoverable. The replacement freezes five allow-listed relations, their logical database identifiers and filenames, query-required columns and committed affinities, query-justified index prefixes, and a stable inclusive `rowid`-only high-water policy.

All five relations use `rowid <= captured_upper_bound`; none uses an event-time high-water. This freezes row membership without inventing normalization semantics for heterogeneous timestamp columns. Temporal interpretation remains a later query-contract concern and cannot alter captured membership.

Index requirements follow the committed EB query predicates and orderings: mint lookup for creator and watchtower membership, mint/capture ordering for price snapshots, migrated cohort ordering plus mint lookup for token analysis, and fact-family filtering for normalized evidence. A subsequent production schema audit must fail closed if those prefixes are absent; PSI0A-C8 does not create indexes or authorize DDL.

The manifest retains the PSI0A-A boundary and PSI0A-B audit digests as historical inputs, binds its own engineering revision and canonical digest, and grants neither extraction nor activation authority. PSI0A-C8 is fixture-only and authorizes no production recapture.
