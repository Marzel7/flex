"""Operation-agnostic completion gate for token-data playbook controls."""
from __future__ import annotations


COMPLETE = "TOKEN_DATA_COMPLETE"
COMPLETE_WITH_LEGITIMATE_GAPS = "TOKEN_DATA_COMPLETE_WITH_LEGITIMATE_GAPS"
INCOMPLETE_LIFECYCLE = "TOKEN_DATA_INCOMPLETE_LIFECYCLE"
INCOMPLETE_ACTIONABILITY = "TOKEN_DATA_INCOMPLETE_ACTIONABILITY"
INCOMPLETE_SAMPLE = "TOKEN_DATA_INCOMPLETE_SAMPLE"
BLOCKED = "TOKEN_DATA_BLOCKED"


def evaluate_completion(*, lifecycle_values_qualified: bool,
                        lifecycle_hierarchy_exhausted: bool,
                        fx_values_qualified: bool,
                        fx_hierarchy_exhausted: bool,
                        theoretical_entry_qualified: bool,
                        sample_sufficient: bool,
                        blocked: bool = False) -> str:
    """Return a completion state without treating a primary-source miss as final.

    A lifecycle or FX source can be a legitimate gap only after its approved
    recovery hierarchy has been exhausted.  Conservative/exact actionability is
    deliberately not an input: historical linkage/exclusivity may be absent
    while theoretical first executable entry is qualified.
    """
    if blocked:
        return BLOCKED
    if not lifecycle_values_qualified and not lifecycle_hierarchy_exhausted:
        return INCOMPLETE_LIFECYCLE
    if not fx_values_qualified and not fx_hierarchy_exhausted:
        return INCOMPLETE_LIFECYCLE
    if not theoretical_entry_qualified:
        return INCOMPLETE_ACTIONABILITY
    if not sample_sufficient:
        return INCOMPLETE_SAMPLE
    if lifecycle_values_qualified and fx_values_qualified:
        return COMPLETE
    return COMPLETE_WITH_LEGITIMATE_GAPS
