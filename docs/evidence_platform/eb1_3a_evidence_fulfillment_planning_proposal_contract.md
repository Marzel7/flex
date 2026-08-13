# EB1.3A Evidence-Fulfillment Planning-Proposal Contract

EB1.3A is a separate pure contract over immutable EB1.1 requirements and their latest valid EB1.2A review state. It accepts a requirement only when that latest disposition is `READY_FOR_SEPARATE_PLANNING`, and binds the exact requirement projection, manifest, corpus, review disposition, review history, authority lane, and cohort/window scope.

Caller-supplied candidate evidence classes and bounded assumptions are canonicalized as unordered alternatives. They are neither selected nor ranked. Every output is marked `NON_EXECUTABLE_PLANNING_PROPOSAL`, `grants_planning_authority=false`, and `grants_execution_authority=false`. Append-only sequence and supersession produce exact deterministic replay without modifying EB1.1 or EB1.2A.

The contract rejects providers, endpoints, credentials, commands, request budgets, production targets, executable acquisition or fulfillment instructions, cross-authority substitution, entity linkage, ranking/scoring, profitability/cashflow, operator identity/attribution, policy, and activation content. It performs no I/O and creates no authority to plan, acquire, access, fulfill, deploy, configure, or activate.
