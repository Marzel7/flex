"""Pure, read-only exact-signature matcher for qualified Potential candidates."""
from __future__ import annotations
from dataclasses import dataclass

Signature = tuple[tuple[int, str, int], ...]

@dataclass(frozen=True)
class PotentialCandidateMatchSpec:
    candidate_id: str
    signature: Signature
    qualification_state: str = "MATCHER_QUALIFIED"
    provenance: str = "frozen member selected-route evidence"

@dataclass(frozen=True)
class PotentialCandidateMatchResult:
    state: str
    candidate_ids: tuple[str, ...]
    matched_signature: Signature | None
    provenance: str

def match_signature(signature: Signature | None, specs: tuple[PotentialCandidateMatchSpec, ...]) -> PotentialCandidateMatchResult:
    if not signature:
        return PotentialCandidateMatchResult("INSUFFICIENT_INPUT", (), None, "no selected-route signature")
    hits=tuple(sorted(spec.candidate_id for spec in specs if spec.qualification_state == "MATCHER_QUALIFIED" and spec.signature == signature))
    if len(hits) == 1:
        return PotentialCandidateMatchResult("UNIQUE_MATCH", hits, signature, "qualified exact selected-route signature")
    if len(hits) > 1:
        return PotentialCandidateMatchResult("MULTI_MATCH", hits, signature, "cross-candidate signature collision")
    return PotentialCandidateMatchResult("NO_MATCH", (), signature, "no qualified exact signature")
