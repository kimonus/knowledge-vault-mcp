# Temporal knowledge implementation report

Implementation is isolated on `feat/temporal-knowledge` in
`.tooling/worktrees/temporal-knowledge`. The initial repository audit and selected design were
written in [decision 0007](decisions/0007-temporal-evidence.md) before behavior changes.
[Temporal semantics and examples](temporal-knowledge.md) document the complete interface/policy.

## Technical conclusions

| Question | Result |
|---|---|
| Is current-state resolution independent of conversation import order? | Yes, for the same structured evidence set and query time. A pure resolver evaluates all evidence visible in one PostgreSQL statement snapshot. Permutation, reverse-import, backfill, concurrent import and correction tests prove equivalence without comparing database IDs or receipt timestamps. Legacy unstructured text is not automatically classified. |
| How are discussion dates distinguished from effective dates? | Original `observed_at` and explicit `valid_from`/`valid_to` are preserved separately in every immutable source payload. Precision and observation basis qualify dates. Transaction timestamps never substitute for either. Effective anchors take precedence; observation-only results explicitly say latest observed. |
| How are historical observations preserved? | Every source claim has a stable evidence key and independent provenance, even when exact text shares a legacy assertion/embedding. The resolver labels supporting, competing, historical, excluded and corrected evidence, with paginated history and original dates. It never rewrites an old assertion to create a timeline. |
| How are corrections distinguished from changes? | `change` describes an implemented transition. `correction` references a stable `corrects_key`, including a target imported later, and invalidates that claim. Replacement applicability inherits the target unless explicitly corrected. Correction chains and competing corrections are resolved from the whole set. |
| What happens when temporal evidence is missing? | Dates remain null/unknown. Missing chronology, unconfirmed evidence and incomplete correction references produce ambiguity; no insertion-order winner is elected. Historical lookup does not turn an observation into an invented open interval. |
| How are genuine contradictions represented? | Incompatible applicable evidence returns conflict/ambiguity, values, competing source keys and reason codes, without supporting keys. Proposals, hypotheses and historical recollections cannot silently become current. Confidence or confirmation counts never break a tie. |
| Can existing clients continue unchanged? | Existing unstructured flush/search/get/legacy-correction contracts remain available, with additive response fields. Structured state requires refreshed schemas and the new resolver. Legacy supersession of structured evidence is refused. Old one-call correction receipts lacking a digest must be retrieved by original batch ID through commit. |
| How is derived state rebuilt? | There is no stale materialized projection to repair. Re-read each complete exact identity and run resolver version 1 over persisted evidence plus content-free retirement metadata. Embeddings are unnecessary for resolution and remain independently rebuildable. |

## Implementation artifacts

- [Migration 0006](../migrations/versions/0006_temporal_evidence.py) adds immutable evidence,
  content-free retirements, lookup indexes/checks/FK cascades, an update-refusal trigger and an
  optional keyed correction-request digest on flush receipts. Existing assertions are unchanged.
- [Domain input](../src/knowledge_vault/domain/temporal.py) provides explicit identity, mode,
  stable source key, precision and temporal query contracts. Shared responses remain in
  [domain/responses.py](../src/knowledge_vault/domain/responses.py).
- [Temporal service](../src/knowledge_vault/services/temporal.py) implements deterministic
  read-time reconciliation and diagnostics. Existing ingestion persists evidence transactionally;
  sorted stable-key/content locks and rollback/retry boundaries preserve concurrency/idempotency.
- MCP adds `resolve_knowledge_state` and `get_knowledge_temporal_diagnostics`; equivalent HTTP
  routes are `/api/v1/state/resolve` and `/api/v1/state/diagnostics`. Get advertises exact identities.
  [Tool reference](mcp-tools.md) describes the contracts.
- One-call correction retries now return their original receipt after commit, including concurrent
  retries; keyed request digests reject changed-payload key reuse without retaining a reversible
  content hash. Generic begin/append/commit and per-item secret rejection remain intact.
- [Flush skill](../plugin/knowledge-vault/skills/flush-knowledge/SKILL.md), initialization guidance
  and golden examples preserve new temporal observations despite exact text matches. They
  distinguish implemented changes from proposals and corrections, and retain readback limits.

## Acceptance evidence

| Requested scenario | Automated evidence |
|---|---|
| 1. Out-of-order import | PostgreSQL and authenticated Streamable HTTP MCP import January 31 before January 1; MikroTik stays selected. |
| 2. Reverse-order equivalence | Both orders have equivalent values, temporal anchors, dispositions and historical results; pure permutations compare complete logical outcomes. |
| 3. Backdated effective change | January discussion with December validity resolves the December/January state while retaining both dates and source references. |
| 4. Unexecuted proposal | A later proposal is preserved and excluded from state resolution. |
| 5. Historical recollection | Later discussion of an old configuration cannot roll current state back; historical applicability remains bounded/uncertain. |
| 6. Missing timestamps | Incompatible undated values yield ambiguity, never an arbitrary winner. |
| 7. Correction versus transition | Corrections inherit target applicability, including through chains; an intervening real change remains the newer state. |
| 8. Same fact repeated | Separate sources preserve independent evidence while sharing exact text; same-key source reimports have no storage/counter side effects. |
| 9. Unrelated scopes | Production and homelab properties resolve independently. |
| 10. Genuine conflict | Equally credible overlapping values retain both competitors, no supporting winner. |
| 11. Concurrent imports | Concurrent new sources and same-key reimports converge; concurrent one-call corrections return identical receipts. |
| 12. Legacy assertions | Upgrade snapshots compare complete legacy rows, provenance, source rows, dates, IDs and correction pointers; enrichment retains retired history/IDs. |
| 13. Historical retrieval | PostgreSQL and MCP queries return past values with original source dates/provenance. |
| 14. Rebuild determinism | Permutation/property tests and repeated projections yield equivalent logical results. |
| 15. API compatibility | The complete existing contract/e2e suite, previous-application compatibility, tool discovery and HTTP/MCP parity pass. |

Additional regressions exercise stable-key payload mismatch, atomic rollback, receipt-time
filtering, precision overlap, independent confirmation, correction cycles/missing targets,
immutable-row enforcement, exact deletion preview counts, deletion of either side of a correction,
and the lookup bound (1,000 complete evidence items resolve; 1,001 fails closed).

Tests live in [pure resolution](../tests/unit/test_temporal_resolution.py),
[PostgreSQL ingestion/resolution](../tests/integration/test_temporal.py),
[MCP transport](../tests/contract/test_temporal_transport.py) and
[migration preservation](../tests/integration/test_temporal_upgrade.py).
The existing [backup/restore rehearsal](../tests/integration/test_maintenance_upgrade.py) now
verifies evidence and retirement rows survive a real PostgreSQL dump/restore too.

## Final verification results (2026-10-31)

| Gate | Result |
|---|---|
| Complete unit/property/contract/integration/e2e suite | **253 passed**, 48.20 seconds, on main's MCP SDK 2.3.0 lockfile, real disposable PostgreSQL 17/pgvector and authenticated local HTTP/MCP transport |
| Application statement coverage | **98.14%**, required 90%; existing exclusions unchanged |
| Application branch coverage | **93.26%**, required 85% |
| `uv lock --check` | Passed; no dependency/lock changes |
| `ruff format --check .`, `ruff check .`, `pyright` | Passed |
| `bandit -q -r src` | Passed; existing unused `nosec` advisory in the API entry point remains |
| Helm lint/template, Access disabled/enabled with required public hosts | Both passed |
| `kubeconform -strict -summary -kubernetes-version 1.33.0` | 28 rendered resources valid, 0 invalid/errors/skips |
| `scripts/ci/check_rendered_numbers.sh` | Both renders passed |
| Flush skill validator and `git diff --check` | Passed |

The final full suite ran with `pytest -q --cov=knowledge_vault --cov-branch
--cov-report=term-missing --cov-report=json:/tmp/kv-temporal-pr-coverage.json --tb=short` in an isolated
locked environment. `scripts/check_coverage.py` enforces separate statement/branch thresholds.
No embedding models were downloaded. The original shared checkout/environment was preserved.
MCP verification used the official SDK client over authenticated Streamable HTTP, including
discovery, real tool calls and read/write boundaries; the separate Inspector CLI was not run
locally. Container/backup/chart definitions are unchanged, so no image build was required by the
change-specific gates. These verification runs did not modify production data or cloud resources.
Release and deployment remain separate maintainer actions.

## Migration, deployment and rollback

Migration/restore tests use disposable, pinned PostgreSQL 17/pgvector containers. A separate
database starts at 0005 with two legacy assertions (one corrected), known/null observation dates,
validity intervals, original database timestamps, provenance and URL sources. Upgrade, repeat
upgrade, downgrade and re-upgrade preserve their complete row snapshots. Alembic schema drift
checks pass. Migration creates no temporal classifications or fabricated dates.

Synthetic diagnostics verify three fully classified assertions and one unclassified legacy
assertion; reviewed enrichment and missing metadata are separately supported. These are metadata
coverage counts, not assertions that every property resolves. Production records were not
inspected, modified, reclassified or migrated as part of this work.

Before deployment, follow the [migration runbook](runbooks/migrations.md): restore-test a fresh
backup, deploy through the Helm migration Job, refresh schemas/skill and verify both import orders
through an authenticated deployed client. Read API logs and restart counts afterwards. The local
MCP transport tests are empirical interface verification, not a live-deployment claim.

For rollback, retain the additive schema and roll back application images. Pause structured
assertion modifications while old images are active because they lack the new correction guard.
A schema downgrade destroys new evidence/retirements/request digests and requires stopped writers
and a verified backup; legacy assertion rows survive. Re-upgrade recreates empty temporal tables.

## Limits and future improvements

- Callers provide identity, mode, provenance and dates; deterministic resolution cannot externally
  verify extraction or real-world truth. No automatic ontology, alias merge, LLM classification or
  semantic deduplication is introduced. Ambiguous identity stays unstructured.
- Current-state questions must use the resolver. Generic text/vector search retains its legacy
  evidence-discovery behavior, including old status/date filters.
- Observation-only support means latest observed, not continuous validity between source dates.
  Missing chronology and unequal evidence strength can leave conservative ambiguity/conflict.
- One property lookup is bounded to 1,000 evidence records, with quadratic bounded comparison and
  paginated history. Larger properties need a future streaming or cached projection; history is
  not discarded to satisfy the bound.
- `known_at` filters transaction timestamps, not exact past commit visibility. Concurrent/long
  transaction commit order can differ. Hard deletion deliberately removes historical evidence;
  known-at queries cannot recover forgotten content.
- Exact text sharing also shares assertion-level deletion: all its linked evidence is previewed
  and cascaded. There is no separate evidence-only deletion API. Retirements retain only metadata
  about a surviving claim, never deleted correction content, keys or hashes.
- Keep the token pepper stable during correction-receipt retention; rotating it invalidates
  request-digest verification. Existing generic flush retries are unaffected.
- Metadata edits affect legacy assertion views, never immutable source evidence. Refined source
  chronology requires new reviewed evidence/corrections rather than rewriting history.

Future work should be driven by measured property sizes and unresolved extraction cases:
streaming/cached projections under the same resolver version, explicit reviewed alias mappings,
and evidence-specific enrichment/deletion could help without weakening temporal invariants.
