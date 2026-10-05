"""Thin transport-free adapters over qualified token-data derivations."""
from __future__ import annotations
from .token_data_chain_facts import tx_summary
from .token_data_lifecycle_facts import history_summary
from .token_data_fx_facts import fx_summary
from .operation_opening_history import materialize_opening_history

CREATE_FACT_ADAPTER_VERSION='CREATE_FACT_ADAPTER_V1'
OPENING_SEQUENCE_ADAPTER_VERSION='OPENING_SEQUENCE_ADAPTER_V1'
MIGRATION_FACT_ADAPTER_VERSION='MIGRATION_FACT_ADAPTER_V1'
LIFECYCLE_SERIES_ADAPTER_VERSION='LIFECYCLE_SERIES_ADAPTER_V1'
FX_FACT_ADAPTER_VERSION='FX_FACT_ADAPTER_V1'
TOKEN_DATA_FACT_RESULT_VERSION='TOKEN_DATA_FACT_RESULT_V1'

def create_fact(response,signature,mint): return tx_summary(response,signature,mint)
def opening_fact(profile,events): return materialize_opening_history(profile,events)
def migration_fact(response,signature,mint,pool): return tx_summary(response,signature,mint,pool)
def lifecycle_fact(response): return history_summary(response)
def fx_fact(response,target_timestamp): return fx_summary(response,target_timestamp)
def token_fact_result(*,mint,create,opening,migration,lifecycle,fx,actionability):
 return {'schema_version':TOKEN_DATA_FACT_RESULT_VERSION,'mint':mint,'create':create,'opening':opening,'migration':migration,'lifecycle':lifecycle,'fx':fx,'actionability':actionability}
