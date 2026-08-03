# Workflow Migration Evidence

## Audit

```text
Command: aiwf-audit .
Result: failures=0, warnings=1
Note: warning is the expected uncommitted migration diff.
```

## Quick verification

```text
Command: ./scripts/verify.sh quick
Result: 16 passed
```

## Full verification

```text
Command: ./scripts/verify.sh full
Result:
- 117 passed
- release-prep readiness remained visibly blocked with exit 0
- target 1.0.0 readiness remained blocked with expected exit 3
- wheel built with --no-build-isolation and --no-deps
- wheel installed into a temporary target
- isolated AICG_HOME/CODEX_HOME/CLAUDE_CONFIG_DIR doctor self-check passed
```

The first wheel-smoke run exposed that `doctor` would inspect local provider
configuration metadata when only `AICG_HOME` was isolated. The verification
script was corrected to isolate both provider homes and remove model CLIs from
`PATH`; the final passing run read only temporary paths.

## Scope

- No AgentOps Guard business code, tests, schemas, package metadata, or release
  gate semantics changed.
- No dependency was installed into the project environment.
- No network, push, merge, tag, publish, production, financial, or on-chain
  operation was performed.

## Review and operational gate

- Claude authentication was restored after the initial migration attempt.
- The real `TASK-20260730-contributor-workflow-docs` smoke task completed an
  independent Claude review with verdict `APPROVE`; its two `LOW` findings were
  remediated and deterministically re-verified.
- That task reached `DONE`, and `.ai/workflow.yaml` was promoted from
  `installed` to `operational` only after the smoke completed.
- The versioned smoke-task artifacts record quick=16 and full=117 passing tests,
  the review verdict, resolution, evidence, and state transitions.

The workflow migration is therefore operational. This does not change release
readiness: external beta, bug-bar, and 30-day accuracy evidence are still
missing, so the v1 release gates remain intentionally blocked.
