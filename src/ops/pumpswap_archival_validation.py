"""Pure temporal qualification helpers for bounded PumpSwap archival validation."""
from __future__ import annotations
from typing import Mapping


def isolatable_sell(candidate: Mapping[str, object]) -> bool:
    return bool(candidate.get('is_sell') and candidate.get('unique_pumpswap_outer_instruction')
                and candidate.get('complete_transaction_balances')
                and candidate.get('pool_identity') and candidate.get('fee_config_identity')
                and not candidate.get('other_pool_or_vault_mutation'))


def temporal_classification(*, archival_pre_instruction: bool, deterministic_transaction_prestate: bool,
                            versioned_fee_config: bool) -> str:
    if archival_pre_instruction and versioned_fee_config:
        return 'PRE_INSTRUCTION_STATE_QUALIFIED'
    if deterministic_transaction_prestate and versioned_fee_config:
        return 'DETERMINISTICALLY_DERIVABLE_PRE_STATE'
    return 'HISTORICAL_STATE_UNAVAILABLE'


def submission_capability() -> str:
    return 'NONE'
