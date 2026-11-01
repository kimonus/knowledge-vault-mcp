# M4 — Targeted lifecycle and identity repair

**Priority:** conditional. **Effort:** S–M pilot (3–10 days), medium confidence. **Dependencies:** T0 and M1 measured cases. **Owner:** maintenance/client guidance agent, distinct from temporal implementation owner.

## Problem and evidence

Legacy `status=current` means unsuperseded record, not implemented plan or verified configuration ([maintenance semantics](../../../../docs/knowledge-maintenance.md)). Existing `workflow_state`, `evidence_state`, `context_id`, `facet`, aliases and direct links can express many distinctions, but the live snapshot had zero contexts and aliases, and sampled records retained unknown metadata. Whether a new lifecycle or entity schema is needed remains unproved.

## Scope and architectural decision

Choose only cases where M1 shows wrong plan/completion, considered/purchased, entity alias, project rename, or scope results. Use current maintenance tools, explicit temporal evidence, and reviewed aliases/contexts to repair or propose repair. Keep historical claims. Treat unknown and ambiguous as unknown. Do not infer identity from embedding proximity or automatically merge entities; a graph database and broad state machine are out of scope.

## Implementation requirements

Produce a review queue with assertion IDs, revisions and safe reason codes; show candidate metadata edits and temporal enrichments for operator review. Existing `review_knowledge`, `find_knowledge_curation_candidates`, `update_knowledge_metadata`, `create_knowledge_context`, `add_knowledge_topic_alias`, links and resolver should be reused. Update skill and client setup examples so proposed vs completed and considered vs purchased are never conflated. If existing fields cannot model a measured case, write a narrowly scoped ADR and separate schema proposal before coding it. No bulk live correction under this package without explicit authorization.

## Acceptance and testing

Gold cases for plan completion, rejected option, alias across languages, identical names in different scopes and historical recollection pass without invented dates or silent supersession. Revision/idempotency races and deletion/link cleanup remain correct. Report curator decisions and inter-reviewer disagreement; compare before/after answer and temporal correctness, not only a count of metadata edits. Use disposable fixtures for automated tests and full gates if code changes.

## Migration, compatibility, risk and rollback

No new schema by default. Metadata and aliases are additive but can affect search; preview, record expected revisions and test on fixtures first. Reverse an erroneous edit through reviewed metadata update or explicit correction, preserving audit history. Do not revert by deleting historical evidence.
