"""Offline, synthetic-only gold freeze and structured comparison; no vault connection."""

import argparse
import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from knowledge_vault.domain.secrets import detect_secret

ROOT = Path(__file__).resolve().parents[1]
MAX_FILE_BYTES = 100_000
FIELDS = {"id", "kind", "state", "scope", "effective_date", "question", "answerable"}


def read_json(path):
    with path.open("rb") as stream:
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("input too large")
    text = data.decode("utf-8")
    if detect_secret(text) or re.search(r"\b(?:https?|postgres(?:ql)?)://", text, re.I):
        raise ValueError("input refused by safety filter")
    return json.loads(text)


def validate_claims(claims, *, allow_empty=False):
    if not isinstance(claims, list) or not (0 if allow_empty else 1) <= len(claims) <= 100:
        raise ValueError("invalid claims")
    seen = set()
    for claim in claims:
        if not isinstance(claim, dict) or set(claim) != FIELDS:
            raise ValueError("invalid claim fields")
        if any(
            not isinstance(claim[k], str) or not claim[k].strip() for k in FIELDS - {"answerable"}
        ):
            raise ValueError("invalid claim value")
        if type(claim["answerable"]) is not bool or claim["id"] in seen:
            raise ValueError("invalid answerability or duplicate ID")
        seen.add(claim["id"])


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def freeze(source_path, gold_path, output_path):
    # This phase never reads an ingestion result, even if a later comparison fails.
    source = read_json(source_path)
    gold = read_json(gold_path)
    if (
        not isinstance(source, dict)
        or set(source) != {"synthetic", "source_id", "excerpt"}
        or source["synthetic"] is not True
        or not isinstance(source["excerpt"], str)
        or not source["excerpt"].strip()
        or not isinstance(source["source_id"], str)
        or not re.fullmatch(r"synthetic-[a-f0-9]{16,64}", source["source_id"])
    ):
        raise ValueError("invalid synthetic source")
    if not isinstance(gold, dict) or set(gold) != {"claims"}:
        raise ValueError("invalid gold")
    validate_claims(gold["claims"])
    # Resolve the parent, not the leaf: O_NOFOLLOW must see a final symlink.
    output = output_path.parent.resolve() / output_path.name
    if output.is_relative_to(ROOT):
        raise ValueError("output must be outside repository")
    record = {
        "source_id": source["source_id"],
        "frozen_at": datetime.now(UTC).isoformat(),
        "source_sha256": hashlib.sha256(source["excerpt"].encode()).hexdigest(),
        "claims": gold["claims"],
    }
    envelope = {"record": record, "sha256": hashlib.sha256(canonical(record).encode()).hexdigest()}
    serialized = (canonical(envelope) + "\n").encode("utf-8")
    if len(serialized) > MAX_FILE_BYTES:
        raise ValueError("frozen record too large")
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(serialized)


def score(frozen_path, result_path):
    frozen = read_json(frozen_path)
    if not isinstance(frozen, dict) or set(frozen) != {"record", "sha256"}:
        raise ValueError("invalid freeze")
    record = frozen["record"]
    if not isinstance(record, dict) or set(record) != {
        "source_id",
        "frozen_at",
        "source_sha256",
        "claims",
    }:
        raise ValueError("invalid record")
    if hashlib.sha256(canonical(record).encode()).hexdigest() != frozen["sha256"]:
        raise ValueError("freeze hash mismatch")
    validate_claims(record["claims"])
    result = read_json(result_path)
    if not isinstance(result, dict) or set(result) != {"claims"}:
        raise ValueError("invalid result")
    validate_claims(result["claims"], allow_empty=True)
    actual = {item["id"]: item for item in result["claims"]}
    counts = {
        "missing": 0,
        "state_mismatch": 0,
        "date_mismatch": 0,
        "other_mismatch": 0,
        "unanswerable_answered": 0,
    }
    for expected in record["claims"]:
        item = actual.get(expected["id"])
        if item is None:
            counts["missing"] += 1
            continue
        counts["state_mismatch"] += item["state"] != expected["state"]
        counts["date_mismatch"] += item["effective_date"] != expected["effective_date"]
        counts["other_mismatch"] += any(
            item[k] != expected[k] for k in ("kind", "scope", "question")
        )
        counts["unanswerable_answered"] += not expected["answerable"] and item["answerable"]
    return {
        "gold_claims": len(record["claims"]),
        "submitted_claims": len(result["claims"]),
        "matched_ids": sum(c["id"] in actual for c in record["claims"]),
        "unanswerable_questions": sum(not c["answerable"] for c in record["claims"]),
        "errors": counts,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    f = sub.add_parser("freeze")
    f.add_argument("source", type=Path)
    f.add_argument("gold", type=Path)
    f.add_argument("output", type=Path)
    s = sub.add_parser("score")
    s.add_argument("frozen", type=Path)
    s.add_argument("result", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "freeze":
            freeze(args.source, args.gold, args.output)
        else:
            print(canonical(score(args.frozen, args.result)))
    except (ValueError, OSError, json.JSONDecodeError, TypeError, KeyError):
        parser.exit(2, "Input refused or local file operation failed.\n")


if __name__ == "__main__":
    main()
