# EP3.2A — Bounded 3SW2 Shadow Corpus Materialization

The frozen population contains exactly 13 launches, 13 creators, one controller,
and 13 direct controller-to-creator activation edges. No successor, unrelated
address, or new launch was admitted.

Twenty-four of 25 known transactions were recovered from the retained cache. The
bounded live phase used one signature-history request and two transaction requests
(3 calls / 30 credits). A separately frozen freshness phase acquired one history
page for each of the thirteen creators (13 calls / 130 credits). Total live use was
16 calls / 160 credits. Both ceilings were established before their respective
execution phases.

The isolated corpus contains 40 acquisition observations, 1,601 immutable Evidence
records, and 858 append-only Primitive observations. All 13 launches have launch
facts, proven launch signers, activation and economic-funding primitives. Replay in
the actual two projection checkpoints is byte-semantically identical and performs
zero additional RPC.

No detector, runtime evaluation, governance action, identity change, production
write, or production queue was used.

## Blocking defect

All thirteen creator histories are materialized. Primitive v1 nevertheless marks
the creators `NOT_FRESH` because its history comparison does not restrict returned
signatures to events preceding the activation reference transaction. Post-event
activity is therefore counted as prior activity. This is an implementation defect,
not missing Evidence. EP3.2A does not modify the frozen Primitive Engine.
