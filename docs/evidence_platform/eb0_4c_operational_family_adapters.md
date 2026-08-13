# EB0.4C normalized operational-runtime adapters

EB0.4C accepts one exact frozen normalized-runtime export. It requires an
explicit `PLATFORM_OPERATION_ID` binding and primary role; versioned operation
contract, behaviour module and topology/observation/input identifiers; explicit
edge, mechanism and temporal arrays; and explicit quality, completeness and
conflict state. It maps deterministically into EB0.4A facts.

The adapter never infers an operation from wallets or observation subjects and
does not inspect generic measured-value mappings. Wallet/subject/operator/owner,
confidence, score, rank and policy fields, schema drift, topology-only inputs,
missing provenance and identity-basis promotion fail closed. It has no I/O or
live/runtime path.
