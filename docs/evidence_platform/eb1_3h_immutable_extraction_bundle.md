# EB1.3H Immutable Extraction Bundle and Closure

EB1.3H writes only to an explicitly supplied new empty directory. A `PROJECTED` result contains canonical `run.json`, `accounting.json`, `manifest.json`, `corpus.json`, and `hashes.json`. A `NO_ELIGIBLE_PROPOSALS` result contains only canonical run, accounting, and hashes files; manifest and corpus files are forbidden rather than fabricated.

The bundle binds run ID, engineering revision, EB1.3G schema/status/result digest, input fingerprint, verified EB1.1H bundle digest, exact authority class, false planning/execution grants, per-file hashes, and one bundle digest. Verification enforces the exact status-dependent file set, canonical bytes, hashes, accounting invariants, manifest/corpus lineage and digest replay, extraction-result replay, and non-authority semantics.

This closes EB1.3 as a frozen fixture-only non-executable contract stack. It makes no production/runtime compatibility claim and grants no authority to review, approve, select, plan, acquire, fulfill, access, deploy, configure, or activate anything.
