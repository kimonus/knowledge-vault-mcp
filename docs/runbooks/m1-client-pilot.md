# Bounded lexical-only assistant pilot

This is a preparation and execution procedure, not evidence of client execution. Use
`solo_operator` and keep the optional strict `independent_review` protocol separate. Follow the
[lab safety procedure](m1-client-lab.md) and [source contract](../architecture/knowledge-vault/source-truth-contract.md).
No embedded generative model or embedding weights are needed.

## Freeze inputs and capability

From a clean reviewed server commit, prepare outside Git:

```bash
python scripts/quality_prepare_client_pilot.py --output /operator-local/m1/new-pilot
python scripts/quality_truth_audit.py --output /operator-local/m1/new-source-cards
python scripts/quality_score_judgments.py --schema client-runs > /operator-local/m1/client-run-schema.json
python scripts/quality_score_judgments.py --schema judgments > /operator-local/m1/judgment-schema.json
```

The preparer reuses the existing packet builder and six existing development cases:
`current-0`, `decision-0`, `correction-0`, `multilingual-0`, `conflict-3`, `no-record-0`.
It creates six one-repetition pilot packets and the unchanged 41 × 3 planned development set.
The pilot subset has its own corpus digest; never relabel pilot runs as development repetitions.
The seed-0 index order is frozen separately from manifest order. Empty judgments/receipts and
`status=NOT RUN` are intentional. No client model/settings are invented. Regenerate from a clean
commit if `server_worktree_clean=false`; record the selected model/settings and client version
before any run. Do not launch all 123 runs as part of preparation.

Require accepted source audits bound to this corpus. Import unchanged prior semantic decisions
only with explicit source/card hash comparison, reviewer provenance and actual review-artifact
hashes. Reassess corrected cards; do not rewrite old needs-revision receipts as accepted.

A separate authenticated assistant profile is required. Installed CLI binaries, a Python MCP
SDK, this agent's current context or its fixed production connector are insufficient. Never
copy/read personal Codex auth files, keyrings or MCP profiles. A capability probe may check
login status only in a newly created empty profile with file-based credential storage. A failed
probe is a capability blocker, not a failed quality case or a pilot execution.

## Execute one packet at a time

1. Run existing `quality_client_lab.py preflight`, then `serve --output NEW_OWNED_DIRECTORY`
   with its permitted clean environment. Give each packet a fresh disposable lab and fresh
   client conversation. Record the lab manifest before source ingestion: owner, loopback URL,
   server commit, PostgreSQL/schema version, lexical-only mode and null embedding model.
2. Configure only the lab MCP server in the new evaluation profile. Validate the endpoint from
   the owned manifest is `http://127.0.0.1:PORT/mcp`; never accept a public/private production
   hostname, inherited connector or proxy. Load only that run's `lab-token` into the child
   client's named bearer variable, never into a command line, transcript or Git file.
3. For Codex CLI, the isolated profile's config shape is:

   ```toml
   cli_auth_credentials_store = "file"
   [mcp_servers.m1_lab]
   url = "http://127.0.0.1:PORT/mcp" # replace only with the owned manifest's endpoint
   bearer_token_env_var = "KV_M1_LAB_TOKEN"
   required = true
   enabled_tools = ["check_knowledge_candidates", "begin_knowledge_flush", "append_knowledge", "commit_knowledge_flush", "get_knowledge", "search_knowledge", "resolve_knowledge_state", "report_knowledge_usage"]
   ```

   Use a child-only environment/profile, separate evaluation authentication, explicit model and
   recorded settings. Start in an empty client working directory with no gold files or personal
   configuration. Do not disable sandbox/approval safeguards. The installed client's configuration
   must be validated before execution; its helper-path warning under `/tmp` is not authentication.
   A future credential/profile should be provisioned at an approved durable operator-local path
   supported by that client, without changing an existing profile.
4. Present only the selected blinded packet source and supplied temporal identity. Explicitly
   request a flush **to the lab MCP** using the advertised workflow. Then ask its question/state
   request. No gold claims, expected outcomes or reviewer artifacts may enter the client session.
5. Capture actual client JSONL events, final answer, tool arguments/results, append/commit/readback
   receipts, retrieval IDs/context and temporal evidence. Codex `exec --json --output-last-message`
   can capture client events and the final answer; preserve both stdout/stderr privately with
   UTC start/end, monotonic elapsed time, exit status and model settings. Apply a bounded timeout
   (five minutes per case initially), stop on resource/isolation failures, and retain failures.
6. Stop only that owned lab via its normal termination path; verify reset, owner-checked cleanup
   and token deletion. Never select a process/container to kill by name. Use a fresh run for any
   retry, preserve attempt identity and the original failed evidence.

The Codex configuration and event-capture fields follow
[official MCP documentation](https://developers.openai.com/codex/mcp/) and
[non-interactive execution](https://developers.openai.com/codex/noninteractive/).
[Authentication documentation](https://developers.openai.com/codex/auth/) describes the separate
file credential store. Client credentials are distinct from the disposable lab bearer.

## Independently check actual evidence

Use a separate deterministic verification process and a recorded semantic judge, without
claiming independent human review. Before scoring:

- Verify packet/corpus/source/card hashes and the exact server/schema/client settings against
  the frozen plan. Hash the complete captured trace bytes; bind every receipt/check/judgment to
  that digest. Do not create a `ClientRun` merely from a prepared packet or login-status probe.
- Check actual client events show completed MCP calls to `m1_lab`, not prose claiming calls.
  Check initialization/connection to the owned endpoint, tool errors, accepted/rejected counts,
  committed IDs and required readback, actual retrieval and temporal results. Successful SDK
  smoke alone is not assistant evidence. Reject missing/truncated/tool-free traces, reused
  traces, wrong endpoints or mismatched hashes. Preserve the unredacted trace outside Git.
- Map emitted assertions to preauthored gold keys from the actual committed contents, including
  attribution/evidence state. Evaluate source coverage, unsupported claims, answer grounding,
  citations and abstention separately. Conflict answers must expose both reports without
  selecting a confirmed location; missing evidence must not become a fabricated fact.
- Record failed labels and failed attempts explicitly. A timeout, unsupported claim, wrong
  conflict answer, missing retrieval/readback or unresolved judgment remains visible. Optional
  manual review supplements the machine judgment with its own provenance; no fabricated second
  human or adjudication is permitted.

Score the pilot with the existing scorer by supplying the six selected cases to its Python API
and the generated `pilot-packets.json`; select source-audit receipts for those six IDs. Bind
`ClientRun.corpus_sha256` to the pilot digest, not the 41-case parent digest. The API call is:

```python
selected = tuple(c for c in cases() if c.id in plan["pilot_case_ids"])
report = score_judgments(
    selected,
    judgments,
    packet_manifest=pilot_packets,
    evaluation_mode="solo_operator",
    retrieval_mode="lexical_only",
    truth_audits=selected_audits,
    client_runs=receipts,
)
```

Pilot readiness requires reproducible owned-resource lifecycle, complete real traces/labels and
resolution of observed correctness failures. It is a prerequisite to expansion, not a new M1
numeric acceptance threshold. Keep the existing benchmark criteria unchanged. After a passing
pilot and resource check, execute the separate 123-run development manifest in its frozen order,
with fresh sessions/databases and honest missing-run accounting. Score that full manifest with
the existing CLI. Do not transfer pilot executions into it. Operator acceptance remains separate
from report completeness and requires actual scored results. Publish only sanitized aggregate
results, failure IDs, immutable references and trace digests through coordination; never raw
confidential traces or credentials.
