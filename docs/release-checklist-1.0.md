# Release Checklist 1.0

Beta gate: pending
Bug bar: pending

## Blocking Gates

- [ ] External beta: at least 5 users complete at least 2 weeks of use.
- [ ] P0/P1 bug bar: zero open P0/P1 issues for two consecutive weeks.
- [ ] Accuracy audit: `docs/accuracy-report-1.0.md` status is `passed`.
- [ ] Experimental decisions: every item in `docs/experimental-decisions.md`
  has a final decision and evidence link.
- [ ] Migration rehearsal: v0.5-like and v0.9 DBs rebuild with user-state tables
  preserved.
- [ ] macOS/Linux clean install smoke passes.

## Artifact Steps

- [ ] Bump package version to `1.0.0`.
- [ ] Build wheel and sdist.
- [ ] Generate `SHA256SUMS`.
- [ ] Create signed git tag.
- [ ] Publish PyPI package.
- [ ] Publish release notes with accuracy report link.
- [ ] Start 48h P0 observation window.
- [ ] Keep 1.0.1 hotfix path ready.

## Local Commands

```bash
python scripts/check_release_ready.py --target 1.0.0
python -m pytest -q
python -m pip wheel . --no-build-isolation -w /tmp/aicg-v10-wheel
```

The readiness command is expected to remain `blocked` until beta and accuracy
evidence are complete.
