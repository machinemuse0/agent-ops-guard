# Provider Plugin Guide

`v0.9` exposes a small provider API for local log readers. Plugins are loaded
through the `aicg.providers` entry point group and must pass local conformance
before they are loaded by default.

## Public API

Plugins should import only the stable public types:

```python
from aicg.models import ParsedRecords, NormalizedSession, NormalizedTurn
from aicg.readers.base import Capabilities, ProviderReader
```

Required reader shape:

```python
class ExampleReader:
    provider = "example"

    def capabilities(self) -> Capabilities:
        return Capabilities(
            token_usage=True,
            cache_semantics="disjoint",
            tool_events=False,
            turn_status=True,
            session_resume=False,
            background_flag=False,
            native_cost=False,
        )

    def discover(self, since, paths=None, known_sources=None):
        yield from ()

    def read(self, path):
        return ParsedRecords()
```

A module may expose `create_provider_reader`, `provider_reader`,
`PROVIDER_READER`, or a reader class. Entry points should point at one of those.

## Entry Point

Example `pyproject.toml`:

```toml
[project.entry-points."aicg.providers"]
example = "aicg_example_provider:create_provider_reader"
```

## Conformance

Every provider should ship at least one fixture JSONL file.

```bash
python -m aicg providers verify aicg_example_provider --fixtures fixtures/
```

The verifier checks:

- provider name exists
- `capabilities()` returns `Capabilities`
- fixture directory contains JSONL files
- `read(path)` returns `ParsedRecords`
- fixture reads are deterministic
- fixtures produce at least one normalized record
- existing record ids remain stable after appending malformed JSONL
- appended malformed lines do not crash the reader and increase malformed counts
- appended unknown event types do not crash the reader and are counted in
  `ParsedRecords.unknown_event_types`
- providers with `turn_status=True` include an interrupted or truncated fixture
  whose records contain `status="interrupted"`
- token buckets are non-negative
- normalized string fields do not match built-in secret patterns

When verification passes, AgentOps Guard writes local
`provider-verifications.json` with the provider name, entry point, distribution
version, module file hash, fixture hashes, and verification timestamp. After
that, `scan`, `providers`, and `doctor` may load the plugin as verified. If the
plugin code, distribution version, or entry point changes, verification must be
run again.

## Unverified Local Experiments

Use `--allow-unverified` only for local development:

```bash
python -m aicg providers --allow-unverified
python -m aicg scan --provider example --allow-unverified
```

Unverified providers are reported as experimental. They are Python code and run
with the same local privileges as the CLI process. Provider verification itself
imports the plugin module and executes its reader factory, so run
`providers verify` only for local code you are willing to execute.

`doctor` does not import unverified provider entry points by default.
Provider load failures are reported as warnings instead of being silently
ignored.

## Privacy Requirements

Provider readers must not store raw prompt, raw code, raw command output, or raw
secrets in normalized records. Use hashes, metric counts, source line ranges,
and coarse status fields instead.

Use the fixture redaction helper before contributing real logs:

```bash
python -m aicg fixture redact raw.jsonl --out fixture.jsonl --keep-structure
```
