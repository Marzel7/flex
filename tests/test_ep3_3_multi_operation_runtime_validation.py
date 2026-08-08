from __future__ import annotations

import json
from pathlib import Path

from src.evidence.operation_contracts.formalization import ContractLifecycle, ContractRegistryModel
from src.evidence.operation_contracts.input_windows import EvidenceInputWindow, PrimitiveInputWindow, RuntimeEvaluationSnapshot
from src.evidence.operation_contracts.loader import OperationContractLoader
from src.evidence.operation_contracts.multi_operation_validation import MultiOperationEvaluator, OperationEvaluationJob, replay_identity
from src.evidence.operation_contracts.registry import RuntimeRegistries
from src.evidence.operation_contracts.runtime import OperationRuntime
from src.evidence.operation_contracts.storage import OperationRuntimeStore
from src.evidence.operation_contracts.three_sw2_v1 import register_three_sw2_v1
from src.evidence.operation_contracts.watchtower_v1 import register_watchtower_v1
from src.evidence.primitives.contracts import ObservationWindow, PrimitiveObservation, PrimitiveQuality, PrimitiveType


CONTROLLER = "3SW2zquY2mVTbNuw1ZCGgtoehq2evfU36PFd6TTqSXdK"


def primitive(kind, payload, subjects):
    return PrimitiveObservation.create(primitive_type=kind, primitive_version="1",
        evidence_ids=(), subjects=subjects, parameters={},
        observation_window=ObservationWindow(1,2), output_payload=payload,
        quality_state=PrimitiveQuality.PROVEN, generated_at=3)


def build_runtime(tmp_path, name, contract_path, register, primitives, subjects):
    contract=json.loads(Path(contract_path).read_text()); registries=RuntimeRegistries(); register(registries)
    modules,detectors=registries.dependency_versions()
    registry=ContractRegistryModel(evidence_versions={"TransactionFact":("1",),"LaunchFact":("1",)},
        primitive_versions={kind.value:("1",) for kind in PrimitiveType}, behaviour_versions=modules,
        detector_versions=detectors, presentation_versions=registries.presentations.versions())
    loaded=OperationContractLoader(registry).load_mapping(contract)
    store=OperationRuntimeStore(tmp_path/f"{name}.db"); store.open(); store.append_contract(loaded,registered_at=1)
    evidence=EvidenceInputWindow.create(subjects=subjects,start=1,end=2,watermark="a"*64,observations=())
    primitive_window=PrimitiveInputWindow.create(subjects=subjects,start=1,end=2,watermark="b"*64,observations=primitives)
    snapshot=RuntimeEvaluationSnapshot.create(contract=loaded,subjects=subjects,observation_start=1,
        observation_end=2,evidence_window=evidence,primitive_window=primitive_window,generated_at=4)
    store.close()
    return OperationRuntime(contracts=registry,registries=registries,store=store),snapshot,registry,store


def setup_jobs(tmp_path):
    wt_primitives=(primitive(PrimitiveType.SYSTEM_TRANSFER,{"source":"wt-source","destination":"wt-destination","amount":1,"signature":"wt","timestamp":1},("wt-source","wt-destination")),primitive(PrimitiveType.LAUNCH_SIGNER,{"wallet":"wt-destination","mint":"wt-mint","signer":True,"launch_signature":"wt-launch"},("wt-destination","wt-mint")))
    sw_primitives=(primitive(PrimitiveType.SYSTEM_TRANSFER,{"source":CONTROLLER,"destination":"sw-creator","amount":1000,"signature":"sw","timestamp":1},(CONTROLLER,"sw-creator")),primitive(PrimitiveType.LAUNCH_SIGNER,{"wallet":"sw-creator","mint":"sw-mint","signer":True,"launch_signature":"sw-launch"},("sw-creator","sw-mint")))
    wt=build_runtime(tmp_path,"wt","src/evidence/operation_contracts/contracts/watchtower_v1.json",register_watchtower_v1,wt_primitives,("wt-source",))
    sw=build_runtime(tmp_path,"sw","src/evidence/operation_contracts/contracts/three_sw2_v1.json",register_three_sw2_v1,sw_primitives,(CONTROLLER,))
    jobs=(OperationEvaluationJob("watchtower",wt[0],wt[1]),OperationEvaluationJob("three_sw2",sw[0],sw[1]))
    return jobs,wt,sw


def test_parallel_replay_is_order_independent_and_isolated(tmp_path):
    jobs,wt,sw=setup_jobs(tmp_path); evaluator=MultiOperationEvaluator()
    first=evaluator.evaluate(jobs); second=evaluator.evaluate(tuple(reversed(jobs)))
    assert replay_identity(first)==replay_identity(second)
    assert first["watchtower"].contract_id=="watchtower"
    assert first["three_sw2"].contract_id=="three_sw2"
    assert {node.local_role for node in first["watchtower"].result.topology.nodes}=={"funding_source","funded_wallet"}
    assert {node.local_role for node in first["three_sw2"].result.topology.nodes}=={"controller","creator","launch"}
    assert first["watchtower"].snapshot_digest != first["three_sw2"].snapshot_digest
    wt[3].open(); sw[3].open()
    assert wt[3].count("detector_results")==1 and sw[3].count("detector_results")==1
    wt[3].close(); sw[3].close()


def test_contract_lifecycle_and_presentations_are_independent(tmp_path):
    jobs,wt,sw=setup_jobs(tmp_path)
    assert wt[2].state("watchtower","1.0.0") is ContractLifecycle.SHADOW
    assert sw[2].state("three_sw2","1.0.0") is ContractLifecycle.SHADOW
    assert wt[0].presentation("watchtower","1.0.0")["schema_version"]=="1.0.0"
    assert sw[0].presentation("three_sw2","1.0.0")["schema_version"]=="3.2.0"
    assert wt[0].governance_policy("watchtower","1.0.0")==sw[0].governance_policy("three_sw2","1.0.0")


def test_duplicate_contract_jobs_fail_closed(tmp_path):
    jobs,wt,sw=setup_jobs(tmp_path)
    try:
        MultiOperationEvaluator().evaluate((jobs[0],OperationEvaluationJob("other",jobs[0].runtime,jobs[0].snapshot)))
    except ValueError as exc: assert "duplicate contract version" in str(exc)
    else: raise AssertionError("duplicate contract evaluation accepted")
