import json
from pathlib import Path

import pytest

from src.acquisition.b2bt_execution_boundary import AppendOnlyJsonl, B2BTRunner, HeliusB2BTTransport
from src.acquisition.b2n_qualification import B2NManifest, B2NMember
from src.acquisition.b2w_projection import B2WInputProjection, B2WRequestInput


def manifest():
    return B2NManifest(tuple(B2NMember(i, f"mint-{i}", f"event-{i}", True) for i in range(1, 21)))


def projection():
    return B2WInputProjection(tuple(B2WRequestInput(i, f"mint-{i}", f"event-{i}", f"migration-{i}") for i in range(1, 21)))


class FakeTransport:
    def __init__(self, no_candidate=False):
        self.physical_request_count = 0; self.calls = []; self.no_candidate = no_candidate

    def get_transaction(self, signature):
        self.physical_request_count += 1; self.calls.append(("rpc", signature))
        if signature.startswith("migration-"):
            i = signature.split("-")[1]
            return {"result":{"blockTime":20,"transaction":{"message":{"accountKeys":[
                {"pubkey":f"mint-{i}","signer":False},{"pubkey":f"creator-{i}","signer":True}]}}}}
        return {"result":{"blockTime":19,"transaction":{"message":{"instructions":[{
            "program":"system","parsed":{"type":"transfer","info":{"source":"funder","destination":signature.replace("funding","creator"),"lamports":10}}}]}}}}

    def get_oldest_enhanced_transaction(self, address):
        self.physical_request_count += 1; self.calls.append(("oldest-enhanced", address, 1, "asc", "finalized"))
        if self.no_candidate: return []
        return [{"signature":address.replace("creator","funding"),"timestamp":19,"nativeTransfers":[{
            "fromUserAccount":"funder","toUserAccount":address,"amount":10}]}]


def runner(tmp_path, transport):
    return B2BTRunner(manifest=manifest(),projection=projection(),transport=transport,
        attempts=AppendOnlyJsonl(tmp_path/'attempts.jsonl'),projections=AppendOnlyJsonl(tmp_path/'projections.jsonl'),run_id='new-run')


def test_full_fake_run_is_60_ordered_attempts_and_projections(tmp_path):
    transport=FakeTransport(); results=runner(tmp_path,transport).run()
    attempts=AppendOnlyJsonl(tmp_path/'attempts.jsonl').rows(); projections=AppendOnlyJsonl(tmp_path/'projections.jsonl').rows()
    assert len(results)==20 and len(attempts)==len(projections)==transport.physical_request_count==60
    assert [row['request_kind'] for row in attempts[:3]]==['getTransaction','getOldestEnhancedAddressTransaction','getTransaction']
    assert all('endpoint' not in row and 'credential' not in row and 'raw' not in row for row in projections)
    assert projections[1]['source']=='funder' and projections[1]['destination']=='creator-1'


def test_no_candidate_stops_after_two_with_stop_safe_ledgers(tmp_path):
    transport=FakeTransport(no_candidate=True)
    with pytest.raises(RuntimeError,match='NO_PRE_MIGRATION_INBOUND_SOL_CANDIDATE'): runner(tmp_path,transport).run()
    assert transport.physical_request_count==2
    assert len(AppendOnlyJsonl(tmp_path/'attempts.jsonl').rows())==2
    assert len(AppendOnlyJsonl(tmp_path/'projections.jsonl').rows())==2


def test_construction_is_inert_and_existing_ledger_fails_closed(tmp_path):
    transport=FakeTransport(); built=runner(tmp_path,transport)
    assert transport.calls==[] and transport.physical_request_count==0
    (tmp_path/'attempts.jsonl').write_text('{"existing":true}\n')
    with pytest.raises(RuntimeError,match='LEDGER_MUST_BE_EMPTY'): built.run()
    assert transport.calls==[]


def test_real_transport_construction_is_inert_and_endpoint_bounded(monkeypatch):
    calls=[]; monkeypatch.setattr('urllib.request.urlopen',lambda *args,**kwargs:calls.append(args))
    transport=HeliusB2BTTransport(rpc_endpoint='https://mainnet.helius-rpc.com/?api-key=redacted',
        enhanced_base='https://api-mainnet.helius-rpc.com/v0/addresses',api_key='redacted')
    assert transport.physical_request_count==0 and calls==[]
    with pytest.raises(ValueError,match='REVIEWED_ENHANCED_ENDPOINT'):
        HeliusB2BTTransport(rpc_endpoint='https://mainnet.helius-rpc.com/?api-key=x',
            enhanced_base='https://example.invalid',api_key='x')
