# M1 client lab security review follow-up

An independent read-only review of Draft PR #71 identified three reachable paths:

1. A `docker exec ... pg_isready` probe had no subprocess timeout. A stalled CLI could hold
   `.parent-lock` indefinitely and delay the detached watcher's cleanup.
2. Smoke mode used no output directory by default. A crash during an uncertain launch therefore
   left no retained owner and lifecycle record for the documented cleanup command.
3. The internal `watch` command accepted a socket path after checking only its marker. It did not
   require the private `/tmp` naming, ownership, and matching recovery state before Docker launch.

The PR follow-up bounds each readiness probe, creates a private recovery directory before every
smoke Docker startup, and requires the watcher to validate and claim the generated run before
launch. The existing missing-socket confirmation, process-identity exclusion, owner-labeled
cleanup, and detached launcher remain in place. The operator procedure is in
[`docs/runbooks/m1-client-lab.md`](../runbooks/m1-client-lab.md).

The default smoke recovery directory is retained after completion so manual cleanup remains
idempotent and failed-run evidence is not silently discarded. A missing or contradictory record
still fails closed and needs operator investigation. These changes do not begin the genuine M1
evaluation study.
