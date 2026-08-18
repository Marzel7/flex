#!/usr/bin/env python3
"""PSI0H-H4R1 bounded human review preparation runner.

Reads ONLY the already-qualified, immutable PSI0H-H4R packet and index.
Generates no new candidates, no new evidence, no backfill, no
provider/RPC calls, and no automatic human dispositions.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evidence.contracts.psi0h_h4r1_bounded_human_review import (
    Psi0hH4R1BoundedHumanReviewError,
    prepare_bounded_human_review,
    verify_bounded_human_review,
)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def run(*, h4r_packet: str, h4r_index: str, output: str | None = None) -> dict:
    packet_path = Path(h4r_packet)
    index_path = Path(h4r_index)
    if not packet_path.is_file():
        raise Psi0hH4R1BoundedHumanReviewError("PSI0H_H4R1_H4R_PACKET_MISSING")
    if not index_path.is_file():
        raise Psi0hH4R1BoundedHumanReviewError("PSI0H_H4R1_H4R_INDEX_MISSING")

    packet_data = _read_json(packet_path)
    index_data = _read_json(index_path)

    review_material = prepare_bounded_human_review(h4r_packet=packet_data, h4r_index=index_data)
    verify_bounded_human_review(review_material)

    output_path = Path(output or "docs/audits/psi0h_h4r1_bounded_human_review.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(review_material, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")

    return {
        "artifact": output_path.as_posix(),
        "artifact_digest": review_material["artifact_digest"],
        "status": review_material["status"],
        "verdict": review_material["verdict"],
        "candidate_count": review_material["candidate_count"],
        "pending_human_decisions": review_material["pending_human_decisions"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare PSI0H-H4R1 bounded human review material.")
    parser.add_argument(
        "--h4r-packet",
        default="docs/audits/psi0h_h4r_historical_continuity_review_packet.json",
        help="Path to the immutable PSI0H-H4R review packet JSON.",
    )
    parser.add_argument(
        "--h4r-index",
        default="docs/audits/psi0h_h4r_historical_continuity_review_index.json",
        help="Path to the immutable PSI0H-H4R review index JSON.",
    )
    parser.add_argument(
        "--output",
        default="docs/audits/psi0h_h4r1_bounded_human_review.json",
        help="Output review-sheet path.",
    )
    args = parser.parse_args()

    try:
        result = run(h4r_packet=args.h4r_packet, h4r_index=args.h4r_index, output=args.output)
    except Psi0hH4R1BoundedHumanReviewError as exc:
        raise SystemExit(f"{exc}") from exc
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
