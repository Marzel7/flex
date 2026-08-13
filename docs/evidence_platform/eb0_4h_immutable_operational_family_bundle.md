# EB0.4H Immutable Operational-Family Bundle

EB0.4H writes EB0.4G results only to an explicitly supplied new empty directory. Canonical JSON files contain run metadata, full accounting, EB0.4D manifests and EB0.4E corpora; a hash manifest binds every file and one bundle digest.

Verification checks the exact file set, canonical bytes, per-file and bundle hashes, all manifest/corpus replay, operation accounting and extraction-result replay. Overwrite, missing, extra, altered, version-invalid or inconsistent content fails closed.

This closes only the frozen safe-local EB0.4 contract stack. Production compatibility, live census, provider access, operator attribution, ranking, scoring, policy and activation remain unqualified and require separate authorization.
