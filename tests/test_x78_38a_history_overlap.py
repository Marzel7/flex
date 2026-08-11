from src.core.creator_history_overlap import continuation_request, verify_overlap


def page(*pairs):
    return [{"signature": signature, "slot": slot} for signature, slot in pairs]


def test_penultimate_before_reproduces_oldest_boundary_as_overlap():
    request = continuation_request(page(("s5", 5), ("s4", 4), ("s3", 3)))
    assert request is not None
    assert request.before_signature == "s4"
    assert request.expected_overlap_signature == "s3"
    verdict = verify_overlap(request, page(("s3", 3), ("s2", 2), ("s1", 1)))
    assert verdict.contiguous and not verdict.gap


def test_head_mutation_is_irrelevant_when_continuation_is_anchored_below_head():
    request = continuation_request(page(("s5", 5), ("s4", 4), ("s3", 3)))
    # New s7/s6 at the mutable head cannot affect an exclusive before=s4 page.
    assert verify_overlap(request, page(("s3", 3), ("s2", 2))).contiguous


def test_missing_or_reordered_overlap_is_a_gap_not_completion():
    request = continuation_request(page(("s5", 5), ("s4", 4), ("s3", 3)))
    verdict = verify_overlap(request, page(("s2", 2), ("s3", 3)))
    assert verdict.gap and verdict.reason == "expected_overlap_not_first_result"


def test_duplicate_or_slot_mismatch_never_proves_continuity():
    request = continuation_request(page(("s2", 2), ("s1", 1)))
    duplicate = verify_overlap(request, page(("s1", 1), ("s1", 1)))
    assert duplicate.gap and duplicate.reason == "overlap_signature_duplicate"
    mismatch = verify_overlap(request, page(("s1", 99), ("s0", 0)))
    assert mismatch.gap and mismatch.reason == "overlap_slot_mismatch"


def test_one_row_page_and_empty_continuation_do_not_overstate_deep_coverage():
    assert continuation_request(page(("s1", 1))) is None
    request = continuation_request(page(("s2", 2), ("s1", 1)))
    verdict = verify_overlap(request, [])
    assert not verdict.contiguous and not verdict.gap
    assert verdict.reason == "provider_exhaustion_after_prior_boundary"
