import copy
import importlib.util
import json
import socket
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "prospective_synthetic",
    Path(__file__).resolve().parents[2] / "scripts/prospective_synthetic.py",
)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
freeze = module.freeze
score = module.score


def write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


@pytest.fixture
def inputs(tmp_path):
    claim = {
        "id": "c1",
        "kind": "decision",
        "state": "considered",
        "scope": "toy",
        "effective_date": "2030-01-01",
        "question": "Was it purchased?",
        "answerable": False,
    }
    source = write(
        tmp_path / "source.json",
        {
            "synthetic": True,
            "source_id": "synthetic-0123456789abcdef",
            "excerpt": "A toy was considered.",
        },
    )
    gold = write(tmp_path / "gold.json", {"claims": [claim]})
    return source, gold, claim


def test_freeze_before_result_and_immutable(tmp_path, inputs):
    source, gold, claim = inputs
    frozen = tmp_path / "frozen.jsonl"
    freeze(source, gold, frozen)
    original = frozen.read_bytes()
    with pytest.raises(FileExistsError):
        freeze(source, gold, frozen)
    assert frozen.read_bytes() == original
    result = write(tmp_path / "result.json", {"claims": [claim]})
    assert not any(score(frozen, result)["errors"].values())
    assert score(frozen, result) == score(frozen, result)
    assert b"A toy was considered" not in original
    assert frozen.stat().st_mode & 0o777 == 0o600


def test_distinct_negative_controls_and_denominators(tmp_path, inputs):
    source, gold, claim = inputs
    frozen = tmp_path / "frozen.jsonl"
    freeze(source, gold, frozen)
    second = dict(claim, id="c2")
    cases = [
        (dict(claim, state="purchased"), "state_mismatch"),
        (dict(claim, effective_date="2031-01-01"), "date_mismatch"),
        (dict(claim, answerable=True), "unanswerable_answered"),
        (second, "missing"),
    ]
    for item, expected in cases:
        result = write(tmp_path / "result.json", {"claims": [item]})
        report = score(frozen, result)
        assert report["errors"][expected] == 1
        assert sum(report["errors"].values()) == 1
        assert report["gold_claims"] == report["submitted_claims"] == 1
        assert report["unanswerable_questions"] == 1


def test_rejects_tamper_malformed_and_secrets_without_disclosure(tmp_path, inputs, capsys):
    source, gold, claim = inputs
    frozen = tmp_path / "frozen.jsonl"
    freeze(source, gold, frozen)
    data = json.loads(frozen.read_text())
    data["record"]["claims"][0]["state"] = "purchased"
    write(frozen, data)
    result = write(tmp_path / "result.json", {"claims": [claim]})
    with pytest.raises(ValueError, match="hash mismatch"):
        score(frozen, result)
    for bad in ("password=" + "z" * 16, "https://example.invalid/path"):
        write(
            source, {"synthetic": True, "source_id": "synthetic-0123456789abcdef", "excerpt": bad}
        )
        with pytest.raises(ValueError, match="safety filter"):
            freeze(source, gold, tmp_path / "refused")
        assert bad not in capsys.readouterr().out
        assert not (tmp_path / "refused").exists()
    malformed = copy.deepcopy(claim)
    del malformed["scope"]
    write(gold, {"claims": [malformed]})
    write(source, {"synthetic": True, "source_id": "synthetic-0123456789abcdef", "excerpt": "toy"})
    with pytest.raises(ValueError, match="claim fields"):
        freeze(source, gold, tmp_path / "refused")


def test_no_network_and_no_repository_output(tmp_path, inputs, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network attempted")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    source, gold, claim = inputs
    frozen = tmp_path / "frozen.jsonl"
    freeze(source, gold, frozen)
    score(frozen, write(tmp_path / "result.json", {"claims": [claim]}))
    with pytest.raises(ValueError, match="outside repository"):
        freeze(source, gold, Path(__file__).parent / "forbidden.jsonl")
