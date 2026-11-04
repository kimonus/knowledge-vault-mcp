# Feasibility tooling coverage baseline

This is an informational statement-and-branch coverage snapshot for the feasibility Python tools.
It is kept separate from the application's enforced coverage gate. It has **no minimum threshold**;
zero-coverage modules remain visible and are not excluded to improve the aggregate.

## Measurement

The snapshot is from Draft PR #73 CI run [41861219062](https://github.com/kimonus/knowledge-vault-mcp/actions/runs/41861219062), target source commit `2b2952a51b1c042cd57dcf88c866be38855c99ea`. The dedicated run executed `tests/unit/feasibility` (69 passed), enabled branch measurement, and used `.coveragerc-feasibility` with explicit coverage sources for `scripts` and its namespace-only `feasibility_support` directory.

Statement coverage is covered statements divided by total statements. Branch coverage is covered branch arcs divided by total branch arcs. The aggregate is 332/591 statements (56.18%) and 116/186 branch arcs (62.37%). The aggregate is descriptive only and is not an acceptance gate.

## Module results

| Module | Statements covered | Statement coverage | Branches covered | Branch coverage |
| --- | ---: | ---: | ---: | ---: |
| `scripts/feasibility_fetch_image.py` | 57/61 | 93.44% | 14/18 | 77.78% |
| `scripts/feasibility_gateway.py` | 112/113 | 99.12% | 42/44 | 95.45% |
| `scripts/feasibility_image.py` | 41/41 | 100.00% | 22/22 | 100.00% |
| `scripts/feasibility_lifecycle.py` | 53/59 | 89.83% | 25/26 | 96.15% |
| `scripts/feasibility_spike.py` | 0/87 | 0.00% | 0/24 | 0.00% |
| `scripts/feasibility_support/client.py` | 0/24 | 0.00% | 0/8 | 0.00% |
| `scripts/feasibility_support/server.py` | 0/106 | 0.00% | 0/12 | 0.00% |
| `scripts/feasibility_support/unpack.py` | 0/25 | 0.00% | 0/16 | 0.00% |
| `scripts/feasibility_workspace.py` | 69/75 | 92.00% | 13/16 | 81.25% |

The measured Python modules are the five `feasibility_*.py` helpers, the `feasibility_spike.py` runner, and all three Python files under `feasibility_support`. The support directory's `server.sh`, `group`, and `passwd` are shell/data files and are not measured by Python coverage.

## Interpretation

The focused suite exercises the image verifier, gateway, lifecycle and workspace helpers. It does not execute the top-level spike runner or its client/server/unpack support modules; their 0% results are intentionally reported. This report alone does not establish that those modules are safe or correct, and it does not authorize adding a tooling threshold. Any future minimum or additional tests require a separate review of the relevant code paths and an explicit policy decision.
