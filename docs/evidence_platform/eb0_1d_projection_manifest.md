# EB0.1D immutable canonical projection manifest

EB0.1D packages canonical EB0.1A observations produced by EB0.1C adapters into a deterministic, replay-verifiable manifest. It is a pure local boundary with no file, database, service, network, provider, or clock dependency.

The manifest binds:

- manifest schema `eb0.1d.v1`;
- birth/valuation contract `eb0.1a.v1`;
- adapter boundary `eb0.1c.v1`;
- a canonical input-set digest and projected-observation digest;
- ordered immutable observations;
- per-event, quality, and completeness counts;
- explicit conflict and missing-valuation counts; and
- a digest of the complete manifest body.

Input order cannot change identity. Empty inputs, duplicates, extra/missing fields, non-normalized values, identity collisions, version mismatches, and replay changes fail closed with named EB0.1D errors. Exact duplicate records are rejected rather than silently removed, making denominator accounting explicit.

Qualification uses the existing frozen EB0.1C fixture only. This milestone does not read or materialize any production or runtime database and does not authorize collection, deployment, GMGN work, scoring, ranking, attribution, Evidence Mirror, Cohort Mode, or activation.
