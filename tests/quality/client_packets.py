"""Export blinded synthetic source packs for independent client evaluation."""

import hashlib
import json
from dataclasses import asdict
from typing import Any

from .corpus import Case


def build_client_packets(
    cases: tuple[Case, ...], *, split: str = "dev", repetitions: int = 1
) -> dict[str, Any]:
    """Return source and task inputs only; gold claims and expected answers stay hidden."""
    if split not in {"dev", "held_out"} or not 1 <= repetitions <= 10:
        raise ValueError("split must be dev or held_out and repetitions must be 1 through 10")
    selected = tuple(case for case in cases if case.split == split)
    if not selected:
        raise ValueError("the selected split has no cases")
    digest = hashlib.sha256(
        json.dumps([asdict(case) for case in selected], sort_keys=True, default=str).encode()
    ).hexdigest()
    return {
        "schema_version": 1,
        "split": split,
        "corpus_sha256": digest,
        "case_count": len(selected),
        "run_count": len(selected) * repetitions,
        "packets": [
            {
                "case_id": case.id,
                "run_id": f"r{number:02d}",
                "source": case.source,
                "question": case.query,
                "state_request": case.state.request if case.state is not None else None,
            }
            for case in selected
            for number in range(1, repetitions + 1)
        ],
    }
