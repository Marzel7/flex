"""Operation-agnostic, pure research contracts; no acquisition or execution side effects."""
from __future__ import annotations
from dataclasses import asdict, dataclass
from typing import Any

EVIDENCE_CLASSES=frozenset({'QUALIFIED_EXECUTION','MODELLED_WITH_QUALIFIED_EXECUTION','MODELLED_WITH_PARTIALLY_QUALIFIED_VERSION_BOUND_EXECUTION','HISTORICALLY_OBSERVED','ESTIMATED_WITH_EXPLICIT_ASSUMPTION','UNQUALIFIED'})

@dataclass(frozen=True)
class OperationStrategyDefinition:
    operation_id:str; version:str; cohort_identity:str; entry_trigger:str
    entry_timing_candidates:tuple[str,...]; position_sizes_lamports:tuple[int,...]
    venues:tuple[str,...]; principal_recovery_fractions:tuple[int,...]
    sell_policies:tuple[str,...]; runner_policies:tuple[str,...]; evidence_requirements:tuple[str,...]
    def validate(self):
        if not all((self.operation_id,self.version,self.cohort_identity,self.entry_trigger)) or any(x<=0 for x in self.position_sizes_lamports): raise ValueError('INVALID_OPERATION_STRATEGY_DEFINITION')

@dataclass(frozen=True)
class StrategyEntryObservation:
    operation_id:str; mint:str; trigger_boundary:str; entry_size_lamports:int; tokens_received:int; evidence_class:str; provenance:str
    def validate(self):
        if self.evidence_class not in EVIDENCE_CLASSES or self.entry_size_lamports<=0 or self.tokens_received<0: raise ValueError('INVALID_ENTRY_OBSERVATION')

@dataclass(frozen=True)
class StrategyCandidateDefinition:
    candidate_id:str; entry_sizes_lamports:tuple[int,...]; principal_fraction_percent:int|None
    sell_rule:str; runner_schedule:tuple[tuple[int,int],...]; evidence_requirements:tuple[str,...]
    def validate(self):
        if not self.candidate_id or any(x<=0 for x in self.entry_sizes_lamports) or self.principal_fraction_percent not in (None,50,75,100):
            raise ValueError('INVALID_STRATEGY_CANDIDATE')

def deterministic_strategy_id(definition:OperationStrategyDefinition, parameters:dict[str,Any])->dict[str,Any]:
    definition.validate(); return {'definition':asdict(definition),'parameters':parameters}

def submission_capability()->str:return 'NONE'
