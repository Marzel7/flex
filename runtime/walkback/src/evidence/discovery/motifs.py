"""Deterministic, identity-free canonicalization of discovery candidates."""

from __future__ import annotations

import hashlib
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ..contracts import canonical_json_bytes
from ..operation_contracts.input_windows import plain
from ..primitives.contracts import PrimitiveObservation
from .contracts import DiscoveryCandidate


def _digest(kind: str, value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes([kind, value])).hexdigest()


@dataclass(frozen=True)
class MotifOccurrence:
    occurrence_id: str
    motif_id: str
    candidate_id: str
    supporting_evidence_ids: tuple[str, ...]
    supporting_primitive_ids: tuple[str, ...]
    observed_population: tuple[str, ...]
    time_start: int | None
    time_end: int | None

    @classmethod
    def create(cls, *, motif_id: str, candidate: DiscoveryCandidate) -> "MotifOccurrence":
        body = {
            "motif_id": motif_id, "candidate_id": candidate.candidate_id,
            "supporting_evidence_ids": sorted(set(candidate.supporting_evidence_ids)),
            "supporting_primitive_ids": sorted(set(candidate.supporting_primitive_ids)),
            "observed_population": sorted(set(candidate.population)),
            "time_start": candidate.time_start, "time_end": candidate.time_end,
        }
        return cls(
            _digest("MotifOccurrence", body), motif_id, candidate.candidate_id,
            tuple(body["supporting_evidence_ids"]),
            tuple(body["supporting_primitive_ids"]),
            tuple(body["observed_population"]), candidate.time_start, candidate.time_end,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "occurrence_id": self.occurrence_id, "motif_id": self.motif_id,
            "candidate_id": self.candidate_id,
            "supporting_evidence_ids": list(self.supporting_evidence_ids),
            "supporting_primitive_ids": list(self.supporting_primitive_ids),
            "observed_population": list(self.observed_population),
            "observation_window": {"start": self.time_start, "end": self.time_end},
        }


@dataclass(frozen=True)
class OperationMotif:
    motif_id: str
    canonicalization_version: str
    replay_version: str
    canonical_graph: Mapping[str, Any]
    occurrences: tuple[MotifOccurrence, ...]
    supporting_candidate_ids: tuple[str, ...]
    supporting_evidence_ids: tuple[str, ...]
    supporting_primitive_ids: tuple[str, ...]
    observed_populations: tuple[tuple[str, ...], ...]
    time_start: int | None
    time_end: int | None

    @classmethod
    def create(cls, *, canonicalization_version: str, replay_version: str,
               canonical_graph: Mapping[str, Any],
               candidates: Sequence[DiscoveryCandidate]) -> "OperationMotif":
        graph = plain(canonical_graph)
        identity = {
            "canonicalization_version": canonicalization_version,
            "primitive_versions": graph.get("primitive_versions", []),
            "canonical_graph": graph,
        }
        motif_id = _digest("OperationMotif", identity)
        occurrences = tuple(sorted(
            (MotifOccurrence.create(motif_id=motif_id, candidate=item)
             for item in candidates), key=lambda item: item.occurrence_id,
        ))
        starts = [item.time_start for item in candidates if item.time_start is not None]
        ends = [item.time_end for item in candidates if item.time_end is not None]
        return cls(
            motif_id, canonicalization_version, replay_version, graph, occurrences,
            tuple(sorted(item.candidate_id for item in candidates)),
            tuple(sorted({value for item in candidates for value in item.supporting_evidence_ids})),
            tuple(sorted({value for item in candidates for value in item.supporting_primitive_ids})),
            tuple(sorted({tuple(sorted(item.population)) for item in candidates})),
            min(starts) if starts else None, max(ends) if ends else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "motif_id": self.motif_id,
            "canonicalization_version": self.canonicalization_version,
            "replay_version": self.replay_version,
            "canonical_graph": dict(self.canonical_graph),
            "occurrence_count": len(self.occurrences),
            "occurrences": [item.to_dict() for item in self.occurrences],
            "supporting_candidate_ids": list(self.supporting_candidate_ids),
            "supporting_evidence_ids": list(self.supporting_evidence_ids),
            "supporting_primitive_ids": list(self.supporting_primitive_ids),
            "observed_populations": [list(item) for item in self.observed_populations],
            "observation_window": {"start": self.time_start, "end": self.time_end},
        }


class MotifCanonicalizer:
    """Canonicalize candidate subgraphs without retaining concrete identifiers."""

    VERSION = "1.0.0"
    REPLAY_VERSION = "1"

    _DIRECTIONAL_FIELDS = {
        "SYSTEM_TRANSFER": (("source", "SOURCE", "destination", "DESTINATION"),),
        "DIRECT_COUNTERPARTY": (("source", "SOURCE", "destination", "DESTINATION"),),
        "REPEATED_COUNTERPARTY": (("source", "SOURCE", "destination", "DESTINATION"),),
        "LAUNCH_SIGNER": (("wallet", "SIGNER", "mint", "LAUNCH"),),
        "LAUNCH_ACTIVATION": (
            ("activation_sender", "ACTIVATOR", "creator", "CREATOR"),
            ("creator", "CREATOR", "mint", "LAUNCH"),
        ),
        "ECONOMIC_FUNDING": (("funder", "FUNDER", "recipient", "RECIPIENT"),),
        "WSOL_CLOSE": (
            ("owner", "OWNER", "temporary_wsol_account", "TEMPORARY_ACCOUNT"),
            ("temporary_wsol_account", "TEMPORARY_ACCOUNT", "destination", "DESTINATION"),
        ),
    }

    def __init__(self) -> None:
        self._metrics: Counter[str] = Counter()

    @staticmethod
    def _temporal_ranks(primitives: Sequence[PrimitiveObservation]) -> dict[str, int | None]:
        windows = sorted({
            (item.observation_window.start, item.observation_window.end)
            for item in primitives
            if item.observation_window.start is not None or item.observation_window.end is not None
        }, key=lambda value: (
            value[0] is None, value[0] if value[0] is not None else 0,
            value[1] is None, value[1] if value[1] is not None else 0,
        ))
        rank = {window: index for index, window in enumerate(windows)}
        return {
            item.primitive_id: rank.get(
                (item.observation_window.start, item.observation_window.end)
            ) for item in primitives
        }

    def canonical_graph(self, candidate: DiscoveryCandidate,
                        primitive_index: Mapping[str, PrimitiveObservation]) -> dict[str, Any]:
        primitives = []
        for primitive_id in candidate.supporting_primitive_ids:
            if primitive_id not in primitive_index:
                raise KeyError(f"missing primitive observation: {primitive_id}")
            primitives.append(primitive_index[primitive_id])
        ranks = self._temporal_ranks(primitives)
        roles: dict[str, set[str]] = defaultdict(set)
        edges: list[tuple[str, str, str, str, str, int | None]] = []
        unary: list[tuple[str, str, str, int | None]] = []
        virtual_index = 0
        for item in sorted(primitives, key=lambda value: value.primitive_id):
            payload = item.output_payload
            mapped = False
            for source_key, source_role, target_key, target_role in self._DIRECTIONAL_FIELDS.get(
                item.primitive_type, ()
            ):
                source, target = payload.get(source_key), payload.get(target_key)
                if isinstance(source, str) and isinstance(target, str):
                    roles[source].add(source_role); roles[target].add(target_role)
                    edges.append((source, target, item.primitive_type,
                                  item.primitive_version,
                                  f"{source_role}>{target_role}", ranks[item.primitive_id]))
                    mapped = True
            if item.primitive_type == "SHARED_TRANSACTION":
                members = sorted(value for value in payload.get("wallets", ())
                                 if isinstance(value, str))
                virtual = f"\x00shared-event-{virtual_index}"; virtual_index += 1
                roles[virtual].add("SHARED_EVENT")
                for member in members:
                    roles[member].add("PARTICIPANT")
                    edges.append((member, virtual, item.primitive_type,
                                  item.primitive_version, "PARTICIPANT>SHARED_EVENT",
                                  ranks[item.primitive_id]))
                mapped = bool(members)
            if not mapped:
                for subject in item.subjects:
                    roles[subject].add("SUBJECT")
                    unary.append((subject, item.primitive_type,
                                  item.primitive_version, ranks[item.primitive_id]))
        for subject in candidate.population:
            roles[subject].add("SUBJECT")

        outgoing: dict[str, list[tuple[str, str, str, str, int | None]]] = defaultdict(list)
        incoming: dict[str, list[tuple[str, str, str, str, int | None]]] = defaultdict(list)
        properties_by_node: dict[str, list[tuple[str, str, int | None]]] = defaultdict(list)
        for source, target, kind, version, relation, temporal_rank in edges:
            outgoing[source].append((target, kind, version, relation, temporal_rank))
            incoming[target].append((source, kind, version, relation, temporal_rank))
        for subject, kind, version, temporal_rank in unary:
            properties_by_node[subject].append((kind, version, temporal_rank))
        colours = {node: tuple(sorted(values)) for node, values in roles.items()}
        for _ in range(max(1, len(colours))):
            previous_class_count = len(set(colours.values()))
            signatures = {}
            for node in sorted(colours):
                incident = [
                    ("OUT", kind, version, relation, temporal_rank, colours[target])
                    for target, kind, version, relation, temporal_rank in outgoing[node]
                ] + [
                    ("IN", kind, version, relation, temporal_rank, colours[source])
                    for source, kind, version, relation, temporal_rank in incoming[node]
                ]
                signatures[node] = (colours[node], tuple(sorted(incident)),
                                    tuple(sorted(properties_by_node[node])))
            ordered = {signature: index for index, signature in enumerate(
                sorted(set(signatures.values()), key=canonical_json_bytes)
            )}
            refined = {node: (ordered[signature],) for node, signature in signatures.items()}
            colours = refined
            # Refinement includes the previous colour, so classes never merge.
            # An unchanged class count therefore proves the partition is stable;
            # continuing would only renumber equivalent structural classes.
            if len(set(refined.values())) == previous_class_count:
                break

        classes: dict[tuple[Any, ...], list[str]] = defaultdict(list)
        for node, colour in colours.items(): classes[colour].append(node)
        class_order = {colour: index for index, colour in enumerate(sorted(classes))}
        node_rows = []
        for colour in sorted(classes):
            members = classes[colour]
            role_counts = Counter(role for member in members for role in roles[member])
            node_rows.append({
                "class": class_order[colour], "multiplicity": len(members),
                "role_counts": dict(sorted(role_counts.items())),
            })
        edge_counts = Counter(
            (class_order[colours[source]], class_order[colours[target]], kind,
             version, relation, temporal_rank)
            for source, target, kind, version, relation, temporal_rank in edges
        )
        edge_rows = [{
            "source_class": value[0], "target_class": value[1],
            "primitive_type": value[2], "primitive_version": value[3],
            "role_order": value[4], "temporal_rank": value[5], "count": count,
        } for value, count in sorted(edge_counts.items(), key=lambda item: canonical_json_bytes(item[0]))]
        unary_counts = Counter(
            (class_order[colours[subject]], kind, version, temporal_rank)
            for subject, kind, version, temporal_rank in unary
        )
        unary_rows = [{
            "node_class": value[0], "primitive_type": value[1],
            "primitive_version": value[2], "temporal_rank": value[3], "count": count,
        } for value, count in sorted(unary_counts.items(), key=lambda item: canonical_json_bytes(item[0]))]
        return {
            "graph_model": "STRUCTURAL_QUOTIENT_V1",
            "nodes": node_rows, "directed_edges": edge_rows,
            "node_observations": unary_rows,
            "primitive_sequence": [
                {"temporal_rank": rank, "primitive_types": dict(sorted(counts.items()))}
                for rank, counts in sorted(
                    ((rank, Counter(item.primitive_type for item in primitives
                                    if ranks[item.primitive_id] == rank))
                     for rank in set(ranks.values())),
                    key=lambda value: (value[0] is None, value[0] or 0),
                )
            ],
            "primitive_versions": sorted({
                f"{item.primitive_type}@{item.primitive_version}" for item in primitives
            }),
        }

    def consolidate(self, candidates: Sequence[DiscoveryCandidate],
                    primitives: Sequence[PrimitiveObservation]) -> tuple[OperationMotif, ...]:
        started = time.perf_counter()
        primitive_index = {item.primitive_id: item for item in primitives}
        grouped: dict[str, tuple[dict[str, Any], list[DiscoveryCandidate]]] = {}
        for candidate in sorted(candidates, key=lambda item: item.candidate_id):
            graph = self.canonical_graph(candidate, primitive_index)
            key = _digest("CanonicalGraph", graph)
            if key not in grouped: grouped[key] = (graph, [])
            grouped[key][1].append(candidate)
        motifs = tuple(sorted((
            OperationMotif.create(
                canonicalization_version=self.VERSION, replay_version=self.REPLAY_VERSION,
                canonical_graph=graph, candidates=members,
            ) for graph, members in grouped.values()
        ), key=lambda item: item.motif_id))
        candidate_count = len(candidates)
        distribution = Counter(len(item.occurrences) for item in motifs)
        self._metrics["evaluations"] += 1
        self._metrics["candidates"] = candidate_count
        self._metrics["motifs"] = len(motifs)
        self._metrics["occurrences"] = sum(len(item.occurrences) for item in motifs)
        self._metrics["largest_motif"] = max(distribution, default=0)
        self._metrics["singleton_motifs"] = distribution.get(1, 0)
        self._metrics["generation_latency_ms"] = round(
            (time.perf_counter()-started)*1000, 3
        )
        self._occurrence_distribution = dict(sorted(distribution.items()))
        return motifs

    def health(self) -> dict[str, Any]:
        candidates = self._metrics["candidates"]; motifs = self._metrics["motifs"]
        return {
            "status": "HEALTHY", "canonicalization_version": self.VERSION,
            "candidate_count": candidates, "motif_count": motifs,
            "compression_ratio": (candidates / motifs if motifs else 0.0),
            "largest_motif": self._metrics["largest_motif"],
            "singleton_rate": (
                self._metrics["singleton_motifs"] / motifs if motifs else 0.0
            ),
            "occurrence_distribution": dict(getattr(self, "_occurrence_distribution", {})),
            "metrics": dict(sorted(self._metrics.items())),
            "authoritative": False, "identity_enabled": False,
            "governance_enabled": False,
        }
