# EB1.0H Immutable Eligibility Bundle

EB1.0H writes canonical run, manifest, corpus and hash documents only to an explicit new empty directory. It binds caller engineering revision, extractor fingerprints/results, manifest lineage, per-file hashes and one bundle digest. Verification rejects overwrite, missing, extra, altered, noncanonical or lineage-inconsistent files. This closes only the frozen safe-local EB1.0 stack.
