# PSI0C-B shadow assessment contract

PSI0C-B qualifies a fixture-only, non-authoritative contract for assessing the five frozen PSI0B shadow surfaces. Its contract digest is `3f2d112ba18b190e7acdf9c0dd9ddf552258b7ed75295ccb7cc470a981cc70e1`.

The contract accepts only caller-injected synthetic rows with the exact committed PSI0A-D result schemas. It binds the PSI0C-A reconciliation, PSI0B-G closure, PSI0B bundle identity, PSI0A-D query contract, PSI0A-E resource ceilings, and PSI0A-G abort/isolation identity. The PSI0B bundle digest is an identity binding only; the implementation does not open or inspect its result files.

For each surface, the assessment records row and distinct-mint counts, explicit coverage numerators and denominators, duplicates, and unmatched rows. Every cohort mint receives a per-surface `PRESENT` or `ABSENT_NOT_NEGATIVE` classification. Absence never becomes a negative outcome. Creator assertions from creator, evidence, main, and operations surfaces are retained individually; differing values produce an unresolved conflict record and no selected value.

Successful fixture assessments publish only `contract.json`, `assessment.json`, and `hashes.json` into a new output directory. Canonical bytes, per-file hashes, the assessment digest, and bundle digest are replay verified. Missing, extra, altered, stale, malformed, over-ceiling, production-marked, or authority-changing inputs fail closed without publication.

The contract grants no policy, ranking, integration, Evidence Mirror, Cohort Mode, production activation, or EB2 authority. Applying it to the real immutable PSI0B bundle is a separate PSI0C-C approval.
