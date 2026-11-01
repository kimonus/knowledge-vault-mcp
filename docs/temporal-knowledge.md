# Temporal knowledge

One-call `correct_knowledge` retries, including concurrent identical requests, return their
original commit result. A changed request with the same key is refused. Flush receipts store only
a keyed HMAC request digest; its pepper stays outside the database. Keep the pepper stable during
the receipt retention window. Pre-upgrade one-call correction receipts have no payload digest:
retry their original batch ID with `commit_knowledge_flush` to retrieve the prior result rather
than resubmitting the correction. Generic begin/append/commit contracts remain unchanged.

The vault preserves source evidence separately from its answer to a state question. See the
[audit and architecture decision](decisions/0007-temporal-evidence.md).

## Review legacy history without changing the assertion

Use `attach_reviewed_temporal_evidence` or `POST /api/v1/state/evidence/attach` with a separate
admin credential after reviewing a target UUID, its current content hash, the primary source and
the successor link. The request contains the target UUID and expected content hash plus a complete
`AssertionInput` evidence payload. Its content, kind, origin and status must equal the target's.
The payload must be verified, have `last_verified_at`, a source URL, `observed_at`, known observation
precision and `temporal.observation_basis=source`. A globally stable evidence key makes exact
retries safe; changed payloads or associations fail. This operation only inserts into the existing
`temporal_evidence` table. It does not confirm, enrich, reactivate or otherwise edit legacy rows.
An identical retry returns the stored receipt even if the legacy row's metadata changed later;
new attachments recheck the target and successor under row locks. A concurrent row edit can
return a retryable conflict instead of accepting a stale review.

For a superseded legacy assertion, historical eligibility additionally requires
`temporal.legacy_supersession_reason=historical_transition`, `mode=historical`, known precision
`valid_from` and `valid_to`, and `temporal.transition_assertion_id` referencing a successor whose
`supersedes_id` is the target UUID. A factual correction or an unknown supersession reason must
not be labelled as a transition. Such records remain excluded until independent review establishes
historical truth; an unknown transition date is not a validity interval. A source URL and review
date record the review claim, but the service cannot prove that a source actually supports the
assertion. The operator must inspect it. Proposals and hypotheses do not establish completed
state. Ordinary flush rejects legacy supersession review fields.

This endpoint needs admin scope because labelling a superseded claim as historically true changes
answers even though the source assertion is untouched. Ordinary write clients, including the
Cloudflare hosted identity, cannot invoke it. Identity subject, property, scope and value remain
reviewer assertions; the server checks type and target equality but cannot infer their meaning.

## Submit evidence

The established begin/append/commit sequence accepts an optional `temporal` field:

```json
{
  "content": "The WireGuard endpoint migrated to MikroTik.",
  "kind": "configuration",
  "origin": "user",
  "evidence_state": "reported",
  "observed_at": "2026-01-31T00:00:00Z",
  "valid_from": "2025-12-15T00:00:00Z",
  "provenance": [{
    "kind": "conversation",
    "locator": "source:example-jan31",
    "conversation_id": "example-jan31",
    "message_ids": ["example-message-7"],
    "timestamp_precision": "day",
    "timestamp_basis": "conversation"
  }],
  "temporal": {
    "identity": {
      "subject": "homelab.primary_router",
      "property": "wireguard.endpoint",
      "scope": "home_network",
      "state_type": "configuration"
    },
    "evidence_key": "source:example-jan31:wireguard",
    "value": "mikrotik",
    "mode": "change",
    "observed_precision": "day",
    "valid_from_precision": "day",
    "observation_basis": "conversation"
  }
}
```

This example explicitly knows the change date. If it is unknown, omit `valid_from`. Do not copy
the discussion date there. Unknown observation time also stays null. Precision defaults to
`instant`; clients with date-only information must explicitly select `day`, `month` or `year`.
`unknown` means a supplied timestamp cannot establish chronology. Comparisons use UTC calendar
windows; local date sources should be converted carefully and uncertainty retained.

Identity is exact, including state type (`configuration`, `preference`, `fact`), subject, property
and scope. Use reviewed identifiers; aliases are not automatically resolved. `value` is exact and
case-sensitive. Different spellings remain different values until explicitly corrected. The
server does not infer identity, chronology, whether a plan was implemented, or external verification
from natural language. `reported` is appropriate for a user's statement; `verified` requires
verification provenance/date. A larger confidence number never defeats incompatible evidence.

Modes: `snapshot` is an observation; `change` confirms an implemented transition; `historical`
describes an old state; `proposal`/`hypothesis` cannot establish state. `correction` explicitly
corrects `corrects_key`, which may be imported later. Corrections inherit the target's applicability
unless replacement validity is explicitly supplied, while retaining their own discussion date.
Multiple competing corrections, cycles, missing/cross-identity targets remain unresolved.

An evidence key identifies one immutable source claim, globally within the single-user vault.
Reuse it with identical arguments on source reimport; a mismatched payload is rejected per item.
Different source observations get different keys, even if the content/value is identical. Keys
must be opaque references without secrets or assertion bodies. Without a stable source identifier,
the client must preserve its assigned key; a new random key cannot deduplicate an unseen reimport.

Exact text still shares one legacy assertion and embedding. Every new temporal source keeps its
own complete, validated atomic assertion payload, dates, sources, epistemic state and provenance.
Commit `items` add `evidence_key` and `evidence_outcome=inserted|replayed`. They verify evidence
persistence; `ignored_fields` describes only the legacy text record, not lost temporal metadata.
Repeated evidence has no storage/counter side effects; legacy commit counts still describe the
text-level operation. Existing readback rules remain unchanged.

## Resolve state and inspect history

Call `resolve_knowledge_state` with `request.identity`, optionally `as_of`, `known_at`,
`history_limit` (1–100, default 20) and `history_after`. HTTP uses the identical body at
`POST /api/v1/state/resolve`. A read scope is required. `get_knowledge` exposes up to 32 distinct
`temporal_identities` and an overflow flag; search/fetch remains useful for discovering assertion
IDs. Generic semantic search, including legacy `statuses=[current]` and `valid_at`, is evidence
discovery and retains its old behavior. It does not answer temporal state questions.

Resolution returns `outcome`, `values`, `supporting_keys`, `competing_keys`, fixed-code `reasons`,
`resolver_version` and provenance-bearing `history`. Only `resolved` is a supported answer.
`conflict` means incompatible applicable evidence; `ambiguous` means insufficient confirmation,
chronology or correction context; `unknown` means no applicable evidence. Never silently promote
one of the values in an unresolved result. Legacy numeric confidence and transaction-time
confirmation counts are not chronology or independent verification.

History preserves every source observation. `ordering_at` and `ordering_basis` expose the derived
anchor separately from the source dates; corrected evidence is labeled `corrected`. History pages
are sorted by stable evidence key for reproducible pagination. To display chronology, collect the
pages and sort their ordering anchors, keeping unknown/overlapping times explicitly unordered.
Pagination always resolves the complete property first; a historical page cannot elect a winner.

The resolver permits strictly ordered explicit changes among equally reported/verified evidence.
Independent observation-only snapshots can establish the latest observed state, with reason
`latest_observed_state`; this does not invent an effective date or validity interval. Overlapping
explicit validity, equal/overlapping timestamp precision, unequal evidence strengths, and undated
competing values remain conflicts/ambiguities. Historical recollections never displace current
evidence. Ended/future states cannot become current. Historical lookup uses explicit validity or
the actual observation precision window; observation-only evidence does not establish every date
between observations. No insertion timestamp or UUID breaks a tie.

`as_of` is real-world time. `known_at` restricts evidence to its vault transaction timestamp before
resolution, so historical corrections are invisible before receipt. It is practical bitemporal
retrieval, not a database commit-visibility time machine: PostgreSQL transaction timestamps are
assigned before commit, and long/concurrent transactions may commit after that timestamp. The
current state is read from one MVCC statement snapshot. Import-order equivalence concerns the same
evidence set and query time, not queries deliberately restricted to different receipt histories.

## Legacy data and operational limits

Migration 0006 creates the evidence table, FK cascade, exact identity/time and assertion indexes,
hash/key checks, and an update-refusal trigger. It does not change any legacy assertion, date,
source, identity, reference or status, nor automatically classify `current` as verified.
`get_knowledge_temporal_diagnostics` / `GET /api/v1/state/diagnostics` reports assertion counts:
`fully_classified` has structured evidence with an observation date of known precision,
`partially_classified` has structured evidence without that metadata, and `unresolved` has none.
These are metadata coverage counts, never a claim that all properties resolve.

Gradually enrich legacy records by submitting reviewed evidence through the same flush workflow.
Text dedup preserves existing IDs and transaction dates. Review legacy supersession chains first:
enriching a superseded legacy row as current is refused; reviewed enrichment with explicit
`status=superseded` preserves its ID/history as invalidated evidence. Legacy `supersedes_id` cannot retire an assertion
already holding structured evidence. Use `corrects_key` for its temporal correction. This boundary
prevents a legacy writer from silently invalidating only half of the new model. Old clients remain
usable for unstructured knowledge but need refreshed schemas to manipulate structured state.

Metadata maintenance edits the legacy text record; immutable temporal evidence is changed only
through new evidence/corrections. Confirmed hard deletion cascades evidence with its assertion.
If a deleted target leaves a retained correction dangling, resolution becomes ambiguous rather
than reviving an invalidated fact. Forgetting still requires admin scope, dry run and confirmation.

If the correction itself is forgotten, a content-free retirement keeps its retained target
invalidated. It references only the retained evidence key and retirement time, never deleted
content, values, source keys or hashes. Preview reports temporal evidence/retirement counts;
forgetting the target cascades this metadata too. Hard deletion deliberately removes evidence
from historical and known-at queries as well; these queries cannot recover forgotten content.

Resolution reads at most 1,001 rows per exact identity and fails with `invalid_request` above
1,000, returning no partial winner. The pure resolver is quadratic in that bounded set. Large
properties require a future streaming or cached projection, not deletion of history to fit a bound.
There is no asynchronously stale cache and no LLM on the read path. Rebuild all derived state by
enumerating distinct identity hashes from `temporal_evidence` and rerunning resolver version 1
over each complete evidence set. Embeddings remain rebuildable text-discovery data only.

Monitor `knowledge_vault_temporal_resolutions_total` outcomes (`resolved`, `ambiguous`, `conflict`,
`unknown`, `overflow`). No subject, value, source key, or content is a metric label or log field.
Inspect unresolved reasons through authenticated resolution, and diagnostics before/after gradual
enrichment. No background duty or new queue requires a heartbeat.

For deployment and rollback, follow [migration instructions](runbooks/migrations.md). Backups
include evidence in the same PostgreSQL dump; restore before enabling writers. Application rollback
retains the additive schema. A schema downgrade drops new evidence and requires a verified backup
and stopped writers; it preserves the original assertions. No production data is reclassified by
the migration, and no live data enrichment is part of application installation.
