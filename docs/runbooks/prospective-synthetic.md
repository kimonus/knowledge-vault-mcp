# Offline prospective evaluation prototype

This is a synthetic-only structured comparison, not a client integration or an accuracy study.
It never calls the vault or any network service. Supply generated test material only, with no
real conversations, URLs, credentials, or personal data. Keep inputs and output in a private
operator-local directory outside Git; delete them when the experiment is finished. Secret
detection is heuristic and cannot guarantee that input is safe.
Gold claims remain plaintext in the freeze. Mode 0600 limits ordinary local file access but does
not protect against the same user, privileged users, backups, malware, or inadvertent copying;
the source digest can also be guessed for predictable inputs. This tool is not a confidentiality
boundary and must not be used with sensitive material.

Create three UTF-8 JSON files separately. The source has exactly `synthetic` (true), `source_id`
(`synthetic-` followed by 16–64 lowercase hexadecimal characters), and `excerpt` (nonempty text).
Gold and result each have exactly `claims`, an array of objects with `id`, `kind`, `state`,
`scope`, `effective_date`, `question` (nonempty strings), and `answerable` (boolean). Gold must
contain at least one claim; result may be empty. IDs must be unique within each file. Dates and
labels are compared literally: the operator defines and independently reviews their meaning.

First prepare gold independently of the result, and freeze it before producing the result:

```sh
uv run python scripts/prospective_synthetic.py freeze /private/source.json /private/gold.json /private/gold.jsonl
# Only after freeze succeeds, prepare the synthetic ingestion-result file independently.
uv run python scripts/prospective_synthetic.py score /private/gold.jsonl /private/result.json
```

The exclusive, mode-0600 JSONL output contains a timestamp, opaque test ID, source digest,
gold claims and content digest; it does not contain the excerpt. A second freeze to the same
path fails. The digest detects accidental modification, not malicious rewriting: a person who
can alter the file can recompute it. The timestamp and commands cannot prove that the operator
actually authored gold before seeing a result. Input filtering refuses common secret patterns
and network URLs without printing their contents. Paths must be chosen by the operator; the
output must be outside this checkout.
Input files and the complete serialized freeze, including the newline, are limited to 100,000
bytes and must be valid UTF-8. Freeze refuses an oversized record before creating the output,
even if the input files fit individually. Output creation refuses a
final symlink, including a dangling one. Parent directories are resolved for the checkout check;
use a private directory controlled by the operator, not one shared with an adversary.

The report counts missing IDs, state and effective-date mismatches, other metadata mismatches,
and answering a question marked unanswerable. It separately reports gold, submitted, matched,
and unanswerable-question denominators. These are literal comparisons of manually structured
synthetic records, not semantic judgments, retrieval scores, or measured client answer quality.
Matching is by fixture ID: semantically equivalent claims with different IDs count as missing.
The tool does not check whether gold follows from the excerpt; unsupported gold paired with an
identical result can produce zero errors. Independently review gold against the synthetic source.
The metrics do not measure actual ingestion, retrieval, temporal reasoning or answer quality.
No supported unattended ChatGPT capture or Codex pre-flush hook was established in the inspected
interfaces; this is not a claim about every possible client or future interface. Any real cohort
requires a separately approved source-access method, privacy review and retention schedule.
