# M3 client source-coverage checkpoint

The first M3 change is a client workflow and evaluation change. It requires a local source
coverage ledger before extraction, links planned assertions to currently available source
segments, and reports reviewed, partial, unprocessed and unavailable material separately from
server commit outcomes. The ledger and local S-labels are never sent to Knowledge Vault. Genuine
source identifiers and timestamps may be used as typed provenance when available; fabricated
ones are prohibited. A successful commit still proves only persistence of submitted assertions.

The quality judgment scorer now accepts an independently adjudicated `source_outcome` for each
source pack. It reports source-processing coverage with an explicit denominator excluding
unavailable packs, plus raw outcome counts and reviewer agreement. Claim extraction coverage and
answer quality remain separate. The synthetic corpus contains source packs but no observed client
flushes, so **no source-processing or claim-coverage result is claimed yet**.

This stage changes the installed flush skill, fixed MCP initialization guidance, client setup
example, golden cases and local scorer. Existing MCP tools, database rows and write idempotency
are unchanged. Clients must reconnect after deployment and refresh the installed skill to receive
the guidance. If it causes bad reporting, revert the guidance; no data migration is needed.

The next evaluation is an operator-local, two-reviewer client run on consented cases. Compare
source-processing coverage, claim coverage, unsupported claims and abstention before considering
a server receipt table. A content-free receipt would add schema, privacy and retention obligations
and cannot by itself establish that every useful claim was extracted.
