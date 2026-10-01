## Summary

Describe the observable change and why it is needed.

## Scope and risk

- Change type: <!-- bug fix, feature, security, docs, dependency, operations -->
- Risk level: <!-- low, medium, high -->
- Affected boundaries: <!-- MCP, HTTP, auth, persistence, worker, backup, Helm, docs -->

## Verification

List commands actually run and their results. Do not mark a gate complete if it was not executed.

- [ ] Lockfile, formatting, lint, and strict typing
- [ ] Unit, property, and contract tests
- [ ] PostgreSQL integration and end-to-end tests where applicable
- [ ] Coverage thresholds
- [ ] Dependency, secret, container, and Dockerfile scans where applicable
- [ ] Helm lint, template, kubeconform, and server-side dry-run where applicable
- [ ] Backup and restore proof where applicable

## Security and privacy

- [ ] No secrets, personal assertions, private infrastructure identifiers, or unsanitized logs
- [ ] Authentication and scopes remain enforced in application code
- [ ] Logs and metrics remain content-free
- [ ] Stored/retrieved knowledge remains untrusted data
- [ ] Threat model and security documentation are updated if boundaries changed

## Database, deployment, and rollback

Describe migrations, compatibility, operator-supplied configuration, rollout order, and the tested
rollback or recovery path. Write `Not applicable` only when none of these are affected.

## Documentation

Link updated contracts, runbooks, architecture decisions, examples, or changelog entries.
