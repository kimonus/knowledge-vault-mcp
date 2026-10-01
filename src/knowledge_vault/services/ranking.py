from collections.abc import Hashable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RankedItem[T: Hashable]:
    item: T
    score: float


def reciprocal_rank_fusion[T: Hashable](
    text_results: Sequence[T],
    vector_results: Sequence[T],
    *,
    text_weight: float = 0.55,
    vector_weight: float = 0.45,
    rank_constant: int = 60,
) -> list[RankedItem[T]]:
    """Fuse result identities using weighted reciprocal-rank fusion."""
    scores: dict[T, float] = {}
    for rank, item in enumerate(text_results, start=1):
        scores[item] = scores.get(item, 0.0) + text_weight / (rank_constant + rank)
    for rank, item in enumerate(vector_results, start=1):
        scores[item] = scores.get(item, 0.0) + vector_weight / (rank_constant + rank)
    return [
        RankedItem(item, score)
        for item, score in sorted(scores.items(), key=lambda pair: (-pair[1], str(pair[0])))
    ]
