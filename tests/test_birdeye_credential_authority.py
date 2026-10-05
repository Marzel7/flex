"""Provider-free authority controls for the sole Birdeye credential label."""
from __future__ import annotations

import pytest

from src.ops.token_data_provider_bindings import (
    AUTHORITATIVE_BIRDEYE_CREDENTIAL_LABEL,
    BirdeyeProductionBinding,
    birdeye_credential,
    validate_birdeye_credential_label,
)


def test_authoritative_birdeye_label_is_the_only_resolver_input():
    assert AUTHORITATIVE_BIRDEYE_CREDENTIAL_LABEL == "BIRDEYE"
    assert birdeye_credential({"BIRDEYE": "paid", "BIRDEYE_KK": "legacy"}) == "paid"


def test_missing_authoritative_birdeye_fails_closed_without_legacy_fallback():
    with pytest.raises(RuntimeError, match="MISSING_AUTHORITATIVE_BIRDEYE_CREDENTIAL"):
        birdeye_credential({"BIRDEYE_KK": "legacy", "BIRDEYE_KKHOT": "older"})


@pytest.mark.parametrize("label", ("BIRDEYE_KK", "BIRDEYE_KKHOT", "BIRDEYE_API_KEY", ""))
def test_legacy_birdeye_selector_is_rejected(label):
    with pytest.raises(ValueError, match="UNAUTHORIZED_BIRDEYE_CREDENTIAL_LABEL"):
        validate_birdeye_credential_label(label)
    with pytest.raises(ValueError, match="UNAUTHORIZED_BIRDEYE_CREDENTIAL_LABEL"):
        BirdeyeProductionBinding(api_key="fixture", credential_label=label)
