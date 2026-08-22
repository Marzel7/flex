import sqlite3
from src.discovery.mint_linked_evidence import ep3_for_mint, migration_signer_from_record

def test_ep3_adapter_does_not_claim_identity_from_observed_edges():
    c=sqlite3.connect(":memory:"); c.executescript("CREATE TABLE tf_launches(mint,creator,creation_signature,creation_time); CREATE TABLE tf_edges(edge_id,sender,recipient,signature,block_time,relationship_type,mechanism,hop_depth,evidence_source,launch_context);")
    c.execute("INSERT INTO tf_launches VALUES('m','c','s',1)"); c.execute("INSERT INTO tf_edges VALUES('e','a','c','s',1,'X','Y',1,'cache','m')")
    row=ep3_for_mint(c,'m'); assert row['state']=='COMPLETE' and row['authority']=='NON_AUTHORITATIVE'

def test_migration_signer_adapter_fails_closed_on_ambiguity():
    assert migration_signer_from_record({'migration_signature':'s','signers':['a','b']})['migration_signer'] is None
