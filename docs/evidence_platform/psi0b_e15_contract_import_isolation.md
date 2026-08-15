# PSI0B-E15 contract import isolation

PSI0B-E15 makes the public `EvidencePlatform` package export lazy while retaining eager access to lightweight `EvidenceConfig`. Importing `src.evidence.contracts` therefore no longer imports the Evidence service, mirror, acquisition transaction, provider, or `aiohttp` runtime graph.

The public names and `__all__` remain unchanged. Explicitly requesting `EvidencePlatform` imports and returns the same class from `src.evidence.service` in the configured application environment. Unknown attributes fail with the standard deterministic `AttributeError` behavior.

The PSI0B production entrypoint remains byte-for-byte unchanged and mode `100755`. Frozen tests prove its direct `--help` invocation under a dependency-minimal system interpreter from an arbitrary working directory, contract-module import isolation, and explicit public-export compatibility. This qualification grants no production execution, extraction, integration, or activation authority.
