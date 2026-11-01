# Synthetic client evaluation protocol

The backend benchmark and judgment scorer exist, but they do not run a real MCP assistant. This
protocol prepares **blinded synthetic input packets** so an operator can observe extraction,
retrieval, answer grounding and abstention in a client without exposing gold labels to it.

## Prepare packets

```bash
uv run python scripts/quality_client_packets.py --split dev --repetitions 3 \
  --output /tmp/kv-client-dev-packets.json
```

The output contains `case_id`, `run_id`, source text, an optional question and an optional
temporal request, plus a corpus digest and counts. It contains no gold claims, relevance IDs,
expected temporal values or abstention label. The script only exports committed synthetic cases;
it does not connect to a vault or read private knowledge. It refuses to overwrite a prior packet
file. `--split held_out` is available for a final evaluation, but these held-out templates were
already inspected during M2 and must not be treated as fresh blind ranking evidence.

## Run a client

For each packet and repetition, start a **fresh client context and disposable Knowledge Vault**.
Never flush synthetic cases into the personal production vault. Supply only the packet's source,
ask the client explicitly to flush knowledge to the disposable MCP, then ask its `question` or
current/historical state request. Capture the client's tool sequence, final flush report, answer
and citations in an access-controlled operator-local store. Record client/skill version, model,
model settings, and the disposable server commit or image digest. Keep gold cards out of the
client's context. A fresh vault prevents earlier packet facts from contaminating later answers;
repetitions test variation from the same input.

Two reviewers independently read the original source and observed client output, label each
`case_id`/`run_id`, and adjudicate without seeing backend retrieval scores or each other's first
pass. A judgment can include `source_outcome`, `matched_gold_keys`, `unsupported_claims`,
`answer_correct`, `grounded`, `citations_correct` and `abstained`. Reviewers must also audit the
preauthored synthetic truth card; it is not independent just because it predates the run. Store
real private source material and raw client traces outside Git and CI.

```bash
uv run python scripts/quality_score_judgments.py /operator-local/judgments.json \
  --report /tmp/kv-client-judgments-summary.json
```

Each judgment entry has `case_id`, `run_id`, exactly two distinct `reviewers`, and an
`adjudicated` review. Older single-run cards may omit `run_id` and use `default`. The scorer
rejects repeated case/run IDs and reports unique judged cases, judged runs, minimum/maximum runs
per case, and whether repetitions are balanced. Aggregate scores are run-weighted; compare
systems only with the same cases and number of repetitions. Missing labels remain unmeasured.
The summary contains no source, answer, citation text or assertion content.

No real client samples have been run by this change. Until two-reviewer judgments exist, M4/M5
and a server source-receipt table remain conditional rather than justified implementation work.
