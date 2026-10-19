"""OF-DV34-P1: bounded, single-request-per-edge raw verification of the
Dv34prGm2BT7Ph2n6qKLgzeLgjnii87RJJ7Db6ZQQvKM 23-member direct-funding
family's 6 representative edges.

Reuses the qualified B2Z durable execution machinery directly (B2ZEventLedger,
B2ZAuthorization, DurableB2ZClient, proves_inbound_sol_funding) rather than
building a weaker executor. Unlike B2Z-P1's 3-stage MIGRATION_TX ->
CREATOR_HISTORY -> FUNDING_TX sequence, this hypothesis (does Dv34 genuinely
fund the CREATE creator?) only requires ONE stage per edge --
getTransaction(funding_signature) -- because the creator and candidate
signature are ALREADY known from local transfer_index evidence, not
discovered live.

Explicitly OUT OF SCOPE: operation identity, Watchtower membership,
canonical attribution. This module only answers whether the frozen local
direct-funding edges are raw-on-chain-supported.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from src.acquisition.b2z_durable_execution import (
    B2ZAuthorization,
    B2ZEventLedger,
    B2ZP1Error,
    B2ZStageOutputLedger,
    DurableB2ZClient,
    JsonRpcTransport,
    proves_inbound_sol_funding,
)

RUN_ID = "of-dv34-p1-selective-verification"
STAGE_DIRECT_FUNDING_TX = "DIRECT_FUNDING_TX"
PROVIDER = "helius_rpc"
ENDPOINT_FAMILY = "helius-mainnet-json-rpc"
MAX_TOTAL_REQUESTS = 6  # exactly the 6 representative edges, 1 request each -- not 18
CREDENTIAL_ENV_VAR = "OF_DV34_P1_HELIUS_ENDPOINT"


def build_dv34_authorization(*, prediction_freeze_digest: str) -> B2ZAuthorization:
    """Dv34-specific authorization -- deliberately NOT B2Z's old 50-request
    authorization. Bound to exactly 6 requests, 1 per member, 1 per stage,
    zero retries/pagination/fallback, candidate-evidence-only, mutation
    forbidden."""
    return B2ZAuthorization(
        run_id=RUN_ID,
        manifest_digest=prediction_freeze_digest,  # reuses this field to bind the Dv34 prediction freeze, not a B2N manifest
        projection_digest=prediction_freeze_digest,
        b2n_closure_digest="N/A_DV34_P1_NOT_B2N_DERIVED",
        p0_preflight_digest="N/A_DV34_P1_STANDALONE",
        provider=PROVIDER,
        endpoint_family=ENDPOINT_FAMILY,
        allowed_methods=("getTransaction",),
        max_total_requests=MAX_TOTAL_REQUESTS,
        max_requests_per_member=1,
        max_requests_per_stage=1,
        retries=0,
        pagination_budget=0,
        fallback_budget=0,
        production_db_read=False,
        production_db_write=False,
        candidate_evidence_only=True,
        existing_operation_mutation_forbidden=True,
    )


def verify_one_edge(*, sample_ordinal: int, mint: str, prediction: dict[str, Any],
                     transport: JsonRpcTransport, event_ledger: B2ZEventLedger,
                     stage_output_ledger: B2ZStageOutputLedger,
                     authorization: B2ZAuthorization) -> dict[str, Any]:
    """Dispatch exactly ONE getTransaction call for this edge's frozen
    funding_signature, then validate: transaction exists, succeeded,
    Dv34 (predicted_source) is a valid inbound-funding source to
    predicted_destination (the CREATE creator), amount/timing agree.

    Raises B2ZP1Error via proves_inbound_sol_funding's caller-side check if
    the edge cannot be validated -- caller decides how to record that
    (analogous to B2Z's RAW_VERIFICATION_NEGATIVE_TERMINAL contract, but
    that state machinery is intentionally NOT duplicated here since this
    module never retries or advances past a single edge automatically)."""
    client = DurableB2ZClient(transport=transport, event_ledger=event_ledger, authorization=authorization)
    signature = prediction["predicted_funding_signature"]
    destination = prediction["predicted_destination"]

    result = client.dispatch(
        sample_ordinal=sample_ordinal, mint=mint, stage=STAGE_DIRECT_FUNDING_TX,
        method="getTransaction",
        params=[signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
        dependency_digest=None,
    )

    raw_block_time = result.get("blockTime")
    edge_proven = proves_inbound_sol_funding(result, destination)

    output = {
        "mint": mint,
        "predicted_source": prediction["predicted_source"],
        "predicted_destination": destination,
        "predicted_amount_lamports": prediction["predicted_amount_lamports"],
        "predicted_block_time": prediction["predicted_block_time"],
        "raw_block_time": raw_block_time,
        "raw_transaction_exists": True,
        "raw_edge_proven": edge_proven,
        "migration_time": prediction["migration_time"],
        "pre_launch": (raw_block_time is not None and raw_block_time < prediction["migration_time"]),
    }
    stage_output_ledger.record_stage_output(sample_ordinal=sample_ordinal, stage=STAGE_DIRECT_FUNDING_TX, output=output)
    return output
