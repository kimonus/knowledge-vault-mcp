# Starting and Resuming the Standalone Deployment Supervisor

## Prerequisites
Use a dedicated development checkout with Codex CLI, git, GitHub authorization, and an isolated Docker/Podman runtime. Do not run unattended changes against a homelab or corporate machine. Verify Codex version supports project-scoped custom subagents; update it if necessary. On macOS ARM64, tests require real hardware/VM access—not platform claims based on a Linux runner.

## Start
From repository root, start an interactive Codex session with the following message:

> Act as engineering supervisor. Read AGENTS.md, docs/roadmaps/standalone-deployment.md, docs/roadmaps/standalone-supervisor.md and docs/roadmaps/standalone-status.yaml. First reconcile current GitHub/CI state. Coordinate project-scoped architect, implementer, tester and reviewer subagents with a maximum of three active. Begin Phase 0 immediately. Create an isolated branch/worktree and draft PR for its architecture review and ADR. Continue through unblocked phases under the documented approval boundaries. Do not merge, deploy, alter corporate resources, publish releases or spend money without owner authorization. Persist verified evidence and next actions for recovery. Only interrupt me for genuine approval boundaries.

Enable Codex's supported subagent/multi-agent mode per the installed version's documentation before starting. No custom orchestrator service is required for an interactive supervision run.

## Resume
After a session ends, start Codex again and instruct it:

> Resume the standalone supervisor from GitHub issues/PRs/CI and the roadmap/status manifest. Verify current HEAD, reconcile stale status, determine the next unblocked task and continue without repeating completed writes. Respect all approval boundaries.

A supervisor started interactively does not guarantee autonomous continuation after a session ends or a Mac sleeps. Always-on operation requires a separate scheduled runner and authentication/permission review.

## Owner checkpoints
Only architecture changes beyond the roadmap, security reductions, paid infrastructure, corporate Mac access, merges that require owner approval, production changes and release publication require explicit authorization. All standard fixes, reviews and retries should remain autonomous.

## Project agent definitions
The project roles live in `.codex/agents/` and follow the Codex custom-subagent TOML format. These files specify roles and instructions, **not** independent permission sandboxes; inherited tool access and repository security controls still apply.
