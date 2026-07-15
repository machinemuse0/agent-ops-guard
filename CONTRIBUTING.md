# Contributing

AgentOps Guard accepts changes that keep the local-first, no-raw, stable-CLI
contract intact. Use `python -m aicg` in docs and examples.

## PR Classes

Choose one class in every PR description:

- `bugfix`: fixes incorrect behavior and includes a regression test.
- `docs`: changes documentation only.
- `tests/fixture`: adds or updates tests or redacted fixtures.
- `privacy/security`: tightens redaction, leak detection, injection safety, or
  parser resilience.
- `performance`: improves runtime without changing behavior.
- `release engineering`: packaging, CI, readiness checks, or release docs.
- `behavior change`: requires an issue discussion and a stability impact note.

During `v0.9` release prep, default-visible behavior changes should be avoided
unless a maintainer records an explicit exception.

## Stability Impact Note

For any change touching CLI, JSON output, SQLite schema, provider interfaces, or
conformance behavior, include:

```text
Stability surface: CLI|Data|Plugin|None
Impact: PATCH|MINOR|MAJOR|None
Migration needed: yes|no
Raw data risk: none|mitigated|needs review
```

## Fixtures

Fixture contributions must come from real logs after redaction:

```bash
python -m aicg fixture redact raw.jsonl --out fixture.jsonl --keep-structure
python -m aicg providers verify <module_or_package> --fixtures fixtures/
```

Rules:

- never commit raw prompt, code, command output, secrets, or sensitive paths;
- malformed lines must be redacted markers, not copied raw;
- fixture names should describe provider and scenario, not user/project names;
- if a fixture triggers format drift, include the expected `format_observations`
  behavior in tests.

## Provider Readers

New readers should usually start as third-party plugins. A built-in reader needs:

- at least one redacted real fixture;
- deterministic `providers verify` pass;
- token bucket semantics documented against `docs/semantics.md`;
- malformed JSONL resilience;
- no raw payload persistence;
- visible provider load errors in `providers` or `doctor`.

Use `--allow-unverified` only for local development. It is intentionally
experimental and should not appear in automated team workflows.

## Local Checks

Before opening a PR:

```bash
python -m pytest -q
python scripts/check_release_ready.py
```

For packaging-sensitive work, also build and smoke-test a wheel from a temporary
`AICG_HOME`.
