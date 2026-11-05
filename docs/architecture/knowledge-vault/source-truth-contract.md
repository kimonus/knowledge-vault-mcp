# Synthetic source-truth contract

M1 defaults to `solo_operator`; `independent_review` remains the optional strict two-human mode.
This contract applies to both and to separately reported lexical-only and hybrid experiments.

## Evidence and fixture metadata

Source text must support the assertion's attribution, value, observation time and effective
bounds. An effective date does not establish an observation time. UTC midnight boundaries are
explicit in the transition sources; corrections record observations without inventing validity.
Conflicting reports remain `reported` competing evidence, with neither chosen as verified truth.
Multilingual sources explicitly express the user's preference; a bare item/feature note is
insufficient to infer a preference.

`state_request.identity`, already included in blinded client packets, is a supplied synthetic
namespace, including the `current-`, `history-`, `correction-` or `conflict-` prefix. It prevents
sibling scenarios from colliding. It is not a claim about the source's terminology. Gold evidence
keys are stable fixture/scoring labels; reviewers map actual committed assertions to those keys.
Clients generate their own evidence keys and must preserve actual correction references. No
expected values or outcomes are added to the blinded packet. A zero-claim no-record case means
no supplied evidence, not proof that something never happened in the real world.

## 2026-11-05 source correction

The archived source-truth audit was checked against the corpus at `f5bffd4e2495323191b0eb4ee5195440b9466189`.
Its 17 revision verdicts exposed unsupported observation timestamps in current/history,
correction and conflict sources, plus implicit preference attribution in multilingual sources.
Sources now state the already-authored gold observations and preferences explicitly. The same
template defects are corrected in held-out siblings without inspecting their retrieval outcomes.
All 63 case IDs, 41 development / 22 held-out split assignments, gold payloads, relevance labels,
temporal expectations and 123 planned development repetitions are preserved. These are deliberate
synthetic source repairs, not evidence that real clients previously extracted missing facts.

| Corpus | Previous SHA-256 | Corrected SHA-256 |
| --- | --- | --- |
| Development (41) | `1afc3456c9f47938e45b33c1d5497c5861d961c219dd91a5a78081bb5d8a279a` | `2764de846bffe48b769e0f4b1d92386018936ca7f3941d81898229a084387128` |
| Full (63) | `9969247c78cf0063df6008586011639eb1d342d0b1a96d3c708be43fb131127a` | `410683e368ce8f47e318bd672a460166ca4e30159963176c7c0b0d74efbd7a47` |

Regenerate packets and truth-audit templates before running the corrected experiment. Old
packets, source audits and client receipts are incompatible with this revision and must not be
relabeled, pooled or reused as corrected-corpus acceptance evidence. Keep historical artifacts
under their original digest. The existing scorer verifies the entire packet manifest and
source/truth-card bindings and rejects stale inputs. Unchanged gold payloads preserve backend
fixture comparability; changed extraction inputs require a new client baseline.

The archived audit is a model source-only review, not independent human review, actual MCP-client
execution, or a completed current-schema audit receipt. Missing generation/source-revision
metadata cannot be reconstructed as a verified claim. Exact Git commit and content hashes attest
publication identity only. New corrected cards need their own recorded evaluation. No M1 quality
gate is passed by these repairs or by generated packets.
