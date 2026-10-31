"""Score math and fixture invariants; these tests need no database or model."""

from knowledge_vault.domain.models import AssertionInput

from .corpus import cases
from .runner import _bootstrap_interval, _ndcg, _percentile


def test_corpus_is_source_first_and_bounded():
    corpus = cases()
    assert len(corpus) == 63
    assert len({case.id for case in corpus}) == 63
    assert len({case.category for case in corpus}) == 11
    assert {case.split for case in corpus} == {"dev", "held_out"}
    assert sum(case.split == "held_out" for case in corpus) == 22
    assert sum(case.requires_abstention for case in corpus) == 6
    keys = [claim.key for case in corpus for claim in case.claims]
    assert len(keys) == len(set(keys))
    for case in corpus:
        assert case.source
        assert set(case.relevant).issubset({claim.key for claim in case.claims})
        for claim in case.claims:
            AssertionInput.model_validate(claim.payload)


def test_rank_math_uses_gold_keys_only():
    assert _ndcg(["right", "wrong"], {"right"}) == 1.0
    assert 0 < _ndcg(["wrong", "right"], {"right"}) < 1
    assert _ndcg(["wrong"], {"right"}) == 0
    assert _percentile([1, 2, 3, 4], 0.95) == 4
    assert _percentile([], 0.95) is None
    assert _bootstrap_interval([]) is None
    assert _bootstrap_interval([1.0, 1.0, 1.0]) == [1.0, 1.0]
