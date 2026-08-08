"""Append-only persistence for cross-motif relationship intelligence."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any,Mapping

from ..contracts import canonical_json_bytes
from .relationship_intelligence import RelationshipEvolutionSnapshot,RelationshipSnapshot


SCHEMA_PATH=Path(__file__).with_name("relationship_schema.sql")


class CrossMotifRelationshipStore:
    def __init__(self,path:Path)->None:self.path=Path(path);self.connection:sqlite3.Connection|None=None

    def open(self)->None:
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.connection=sqlite3.connect(self.path,isolation_level=None);self.connection.row_factory=sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL");self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.executescript(SCHEMA_PATH.read_text())

    def close(self)->None:
        if self.connection is not None:self.connection.close();self.connection=None

    def _conn(self):
        if self.connection is None:raise RuntimeError("Cross-motif relationship store is not open")
        return self.connection

    @staticmethod
    def _payload(value:Mapping[str,Any])->tuple[str,str]:
        payload=canonical_json_bytes(value).decode().rstrip("\n")
        return payload,hashlib.sha256(payload.encode()).hexdigest()

    @staticmethod
    def _insert(connection,table,id_column,identity,values,digest)->bool:
        cursor=connection.execute(f"INSERT OR IGNORE INTO {table} VALUES({','.join('?' for _ in values)})",values)
        if cursor.rowcount:return True
        row=connection.execute(f"SELECT payload_digest FROM {table} WHERE {id_column}=?",
                               (identity,)).fetchone()
        if row is None or row[0]!=digest:raise sqlite3.IntegrityError(f"{table} identity collision")
        return False

    def append_relationships(self,snapshot:RelationshipSnapshot)->dict[str,int]:
        connection=self._conn();connection.execute("BEGIN IMMEDIATE")
        try:
            value=snapshot.to_dict();payload,digest=self._payload(value)
            inserted=self._insert(connection,"motif_relationship_snapshots","relationship_snapshot_id",
                snapshot.relationship_snapshot_id,(snapshot.relationship_snapshot_id,
                snapshot.relationship_version,snapshot.replay_version,snapshot.landscape_snapshot_id,
                payload,digest),digest)
            observations=0
            for item in snapshot.relationships:
                value=item.to_dict();item_payload,item_digest=self._payload(value)
                observations+=self._insert(connection,"motif_relationship_observations","observation_id",
                    item.observation_id,(item.observation_id,item.relationship_id,
                    snapshot.relationship_snapshot_id,item.relationship_type,item_payload,item_digest),item_digest)
            connection.commit()
        except BaseException:connection.rollback();raise
        return {"inserted_snapshots":int(inserted),"duplicate_snapshots":int(not inserted),
                "inserted_observations":observations}

    def append_evolution(self,snapshot:RelationshipEvolutionSnapshot)->dict[str,int]:
        connection=self._conn();value=snapshot.to_dict();payload,digest=self._payload(value)
        inserted=self._insert(connection,"motif_relationship_evolution_snapshots","evolution_snapshot_id",
            snapshot.evolution_snapshot_id,(snapshot.evolution_snapshot_id,snapshot.evolution_version,
            snapshot.previous_relationship_snapshot_id,snapshot.current_relationship_snapshot_id,
            snapshot.operational_evolution_snapshot_id,payload,digest),digest)
        return {"inserted_evolution":int(inserted),"duplicate_evolution":int(not inserted)}

    def health(self)->dict[str,Any]:
        connection=self._conn()
        return {"status":"HEALTHY","snapshots":int(connection.execute(
            "SELECT COUNT(*) FROM motif_relationship_snapshots").fetchone()[0]),
            "relationships":int(connection.execute(
            "SELECT COUNT(*) FROM motif_relationship_observations").fetchone()[0]),
            "evolution_snapshots":int(connection.execute(
            "SELECT COUNT(*) FROM motif_relationship_evolution_snapshots").fetchone()[0]),
            "authoritative":False}
