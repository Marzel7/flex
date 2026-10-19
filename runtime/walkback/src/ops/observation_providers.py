"""Pluggable, read-only providers over intelligence already persisted locally."""
from __future__ import annotations

import sqlite3
from typing import Protocol

from src.ops.operator_observation import OperatorObservation


class ObservationProvider(Protocol):
    name: str

    def materialize(self, operator_id: str, entities: list[dict]) -> list[OperatorObservation]: ...


class _SQLiteProvider:
    name = "sqlite"

    def __init__(self, ops_db: str, live_db: str) -> None:
        self.ops_db = ops_db
        self.live_db = live_db

    @staticmethod
    def _connect(path: str) -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        return conn

    @staticmethod
    def _exists(conn: sqlite3.Connection, table: str) -> bool:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone() is not None

    @staticmethod
    def _addresses(entities: list[dict]) -> list[str]:
        return sorted({str(e["entity_address"]) for e in entities if e.get("entity_address")})

    @staticmethod
    def _observation(operator_id: str, kind: str, entity: str | None, timestamp: int | None,
                     table: str, key: str, row: dict, confidence: float = 0.8,
                     database: str = "operations") -> OperatorObservation:
        return OperatorObservation(
            operator_id=operator_id, observation_type=kind, entity=entity,
            timestamp=int(timestamp or 0), source=f"database:{table}", confidence=confidence,
            provenance={"database": database, "table": table, "record_key": str(key)},
            metadata=row,
        )


class IdentityEntityObservationProvider(_SQLiteProvider):
    name = "identity_entities"

    def materialize(self, operator_id: str, entities: list[dict]) -> list[OperatorObservation]:
        observations = []
        for entity in entities:
            role = str(entity.get("entity_type") or "UNKNOWN").upper()
            kind = "TREASURY" if role == "TREASURY" else (
                "RELAY" if role in {"RELAY", "COLLECTOR"} else "INFRASTRUCTURE"
            )
            observations.append(self._observation(
                operator_id, kind, entity.get("entity_address"),
                entity.get("first_seen") or entity.get("added_at"),
                "operator_entities", f"{operator_id}:{entity.get('entity_address')}",
                {**entity, "wallet": entity.get("entity_address"), "role": role,
                 "operation_uuid": entity.get("source_operation") or operator_id},
                confidence=1.0 if entity.get("confidence") == "HIGH" else 0.7,
            ))
        return observations


class LaunchObservationProvider(_SQLiteProvider):
    name = "launches"

    def materialize(self, operator_id: str, entities: list[dict]) -> list[OperatorObservation]:
        addresses = self._addresses(entities)
        if not addresses:
            return []
        with self._connect(self.ops_db) as conn:
            if not self._exists(conn, "wt_watchtower_launches"):
                return []
            ph = ",".join("?" for _ in addresses)
            rows = conn.execute(
                f"SELECT rowid AS _rowid,* FROM wt_watchtower_launches WHERE "
                f"treasury_wallet IN ({ph}) OR subprov_wallet IN ({ph}) OR creator_wallet IN ({ph})",
                addresses * 3,
            ).fetchall()
        return [self._observation(
            operator_id, "LAUNCH", row["creator_wallet"], row["create_time"],
            "wt_watchtower_launches", row["_rowid"], dict(row), 0.95,
        ) for row in rows]


class FundingObservationProvider(_SQLiteProvider):
    name = "funding"

    def materialize(self, operator_id: str, entities: list[dict]) -> list[OperatorObservation]:
        addresses = self._addresses(entities)
        if not addresses:
            return []
        with self._connect(self.ops_db) as conn:
            if not self._exists(conn, "wt_watchtower_launches"):
                return []
            ph = ",".join("?" for _ in addresses)
            rows = conn.execute(
                f"SELECT rowid AS _rowid,* FROM wt_watchtower_launches WHERE "
                f"treasury_wallet IN ({ph}) OR subprov_wallet IN ({ph}) OR creator_wallet IN ({ph})",
                addresses * 3,
            ).fetchall()
        result = []
        for row in rows:
            raw = dict(row)
            if row["subprov_funding_sol"] is not None or row["wrap_close_sol"] is not None:
                result.append(self._observation(
                    operator_id, "FUNDING", row["subprov_wallet"] or row["creator_wallet"],
                    row["fanout_time"] or row["create_time"], "wt_watchtower_launches",
                    f"funding:{row['_rowid']}", raw, 0.9,
                ))
            if row["funding_mechanism"]:
                result.append(self._observation(
                    operator_id, "WRAP_CLOSE", row["creator_wallet"],
                    row["create_time"], "wt_watchtower_launches",
                    f"mechanism:{row['_rowid']}", raw, 0.9,
                ))
        return result


class CampaignObservationProvider(_SQLiteProvider):
    name = "campaigns"

    def materialize(self, operator_id: str, entities: list[dict]) -> list[OperatorObservation]:
        addresses = self._addresses(entities)
        if not addresses:
            return []
        with self._connect(self.ops_db) as conn:
            if not self._exists(conn, "wt_ops_v2"):
                return []
            ph = ",".join("?" for _ in addresses)
            wallet_clause = ""
            params = list(addresses)
            if self._exists(conn, "wt_ops_v2_wallets"):
                wallet_clause = (
                    f" OR operation_uuid IN (SELECT operation_uuid FROM wt_ops_v2_wallets "
                    f"WHERE wallet IN ({ph}))"
                )
                params += addresses
            rows = conn.execute(
                f"SELECT * FROM wt_ops_v2 WHERE treasury_root IN ({ph}){wallet_clause}", params
            ).fetchall()
        return [self._observation(
            operator_id, "CAMPAIGN", row["treasury_root"], row["first_seen"],
            "wt_ops_v2", row["operation_uuid"], dict(row), 0.9,
        ) for row in rows]


class CoordinationObservationProvider(_SQLiteProvider):
    name = "coordination"

    def materialize(self, operator_id: str, entities: list[dict]) -> list[OperatorObservation]:
        addresses = self._addresses(entities)
        if not addresses:
            return []
        with self._connect(self.ops_db) as conn:
            if not self._exists(conn, "wt_fanout_events"):
                return []
            ph = ",".join("?" for _ in addresses)
            rows = conn.execute(
                f"SELECT rowid AS _rowid,* FROM wt_fanout_events WHERE "
                f"subprov_wallet IN ({ph}) OR treasury_wallet IN ({ph})", addresses * 2
            ).fetchall()
        return [self._observation(
            operator_id, "COORDINATION", row["subprov_wallet"], row["fanout_time"],
            "wt_fanout_events", row["_rowid"], dict(row), 0.85,
        ) for row in rows]


class MigrationObservationProvider(_SQLiteProvider):
    name = "migrations"

    def materialize(self, operator_id: str, entities: list[dict]) -> list[OperatorObservation]:
        addresses = self._addresses(entities)
        if not addresses:
            return []
        with self._connect(self.ops_db) as conn:
            if not self._exists(conn, "wt_token_lifecycle"):
                return []
            columns = {r[1] for r in conn.execute("PRAGMA table_info(wt_token_lifecycle)")}
            timestamp_col = "migrated_at" if "migrated_at" in columns else "migration_time"
            if timestamp_col not in columns:
                return []
            entity_cols = [c for c in ("treasury", "subprov", "creator") if c in columns]
            if not entity_cols:
                return []
            ph = ",".join("?" for _ in addresses)
            where = " OR ".join(f"{col} IN ({ph})" for col in entity_cols)
            rows = conn.execute(
                f"SELECT rowid AS _rowid,* FROM wt_token_lifecycle WHERE {timestamp_col} IS NOT NULL "
                f"AND ({where})", addresses * len(entity_cols),
            ).fetchall()
        return [self._observation(
            operator_id, "MIGRATION", row["creator"] if "creator" in row.keys() else None,
            row[timestamp_col], "wt_token_lifecycle", row["_rowid"], dict(row), 0.9,
        ) for row in rows]


class EntityRelationshipObservationProvider(_SQLiteProvider):
    """Follow persisted entity→creator→operation relationships in the live DB."""
    name = "entity_relationships"

    def materialize(self, operator_id: str, entities: list[dict]) -> list[OperatorObservation]:
        addresses = self._addresses(entities)
        if not addresses:
            return []
        observations: list[OperatorObservation] = []
        with self._connect(self.live_db) as conn:
            ph = ",".join("?" for _ in addresses)
            if self._exists(conn, "wt_provisioning_hubs"):
                rows = conn.execute(
                    f"SELECT * FROM wt_provisioning_hubs WHERE hub_address IN ({ph})",
                    addresses,
                ).fetchall()
                observations.extend(self._observation(
                    operator_id, "PROVISIONING", row["hub_address"],
                    row["born_at"] or row["discovered_at"], "wt_provisioning_hubs",
                    row["hub_address"], dict(row), float(row["confidence"] or 0.7),
                    database="live",
                ) for row in rows)

            creators: list[str] = []
            if self._exists(conn, "creator_funders"):
                fundings = conn.execute(
                    f"SELECT rowid AS _rowid,* FROM creator_funders "
                    f"WHERE funder_address IN ({ph})",
                    addresses,
                ).fetchall()
                creators = sorted({row["creator_address"] for row in fundings})
                for row in fundings:
                    raw = dict(row)
                    raw_timestamp = row["first_detected_at"]
                    if isinstance(raw_timestamp, (int, float)):
                        timestamp = int(raw_timestamp)
                    else:
                        timestamp = conn.execute(
                            "SELECT CAST(strftime('%s', ?) AS INTEGER)",
                            (raw_timestamp,),
                        ).fetchone()[0] or 0
                    raw.setdefault("wrap_close_sol", row["amount_sol"])
                    raw.setdefault("subprov_funding_sol", row["amount_sol"])
                    raw.setdefault("funding_mechanism", row["source_type"] or "LOCAL_TRANSFER")
                    observations.append(self._observation(
                        operator_id, "FUNDING", row["creator_address"], timestamp,
                        "creator_funders", row["_rowid"], raw, 0.8, database="live",
                    ))
                    observations.append(self._observation(
                        operator_id, "CREATOR", row["creator_address"], timestamp,
                        "creator_funders", f"creator:{row['_rowid']}", raw, 0.8,
                        database="live",
                    ))

            actual_creators: set[str] = set()
            if creators and self._exists(conn, "wt_operation_members"):
                creator_ph = ",".join("?" for _ in creators)
                members = conn.execute(
                    f"SELECT rowid AS _rowid,* FROM wt_operation_members "
                    f"WHERE creator_wallet IN ({creator_ph})", creators,
                ).fetchall()
                for row in members:
                    raw = dict(row)
                    raw.update({
                        "create_time": row["migrated_at"],
                        "creator_wallet": row["creator_wallet"],
                        "mint": row["token_mint"],
                    })
                    observations.append(self._observation(
                        operator_id, "LAUNCH", row["creator_wallet"], row["migrated_at"],
                        "wt_operation_members", row["_rowid"], raw, 0.75, database="live",
                    ))
                    if row["migrated_at"]:
                        observations.append(self._observation(
                            operator_id, "MIGRATION", row["creator_wallet"], row["migrated_at"],
                            "wt_operation_members", f"migration:{row['_rowid']}",
                            {**raw, "migrated": True}, 0.85, database="live",
                        ))

                operation_ids = sorted({row["operation_id"] for row in members})
                if operation_ids and self._exists(conn, "wt_operations"):
                    op_ph = ",".join("?" for _ in operation_ids)
                    operations = conn.execute(
                        f"SELECT * FROM wt_operations WHERE operation_id IN ({op_ph})",
                        operation_ids,
                    ).fetchall()
                    observations.extend(self._observation(
                        operator_id, "CAMPAIGN", None, row["window_start"],
                        "wt_operations", row["operation_id"],
                        {**dict(row), "operation_uuid": str(row["operation_id"]),
                         "first_seen": row["window_start"], "last_seen": row["window_end"]},
                        float(row["confidence"] or 0.7), database="live",
                    ) for row in operations)
                actual_creators = {row["creator_wallet"] for row in members}
            observations = [
                observation for observation in observations
                if observation.observation_type != "CREATOR"
                or observation.entity in actual_creators
            ]
        return observations


def default_observation_providers(ops_db: str, live_db: str) -> list[ObservationProvider]:
    return [
        IdentityEntityObservationProvider(ops_db, live_db),
        CampaignObservationProvider(ops_db, live_db),
        LaunchObservationProvider(ops_db, live_db),
        FundingObservationProvider(ops_db, live_db),
        CoordinationObservationProvider(ops_db, live_db),
        MigrationObservationProvider(ops_db, live_db),
        EntityRelationshipObservationProvider(ops_db, live_db),
    ]
