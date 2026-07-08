# AgentOps Guard Threat Model

AgentOps Guard is a local-first CLI. It reads local AI coding agent logs and
stores normalized metadata in SQLite. It is not a daemon, cloud sync service,
billing scraper, notification service, or web app.

## Assets

- Local AI agent logs.
- Local normalized SQLite metadata.
- Local policy acknowledgements.
- Local alert history.
- Local provider verification cache.
- Local reports under `~/.aicg/reports`.

## Trust Boundaries

- Built-in readers are trusted project code.
- Third-party provider plugins are untrusted until they pass local conformance.
- Local log files are untrusted input.
- Markdown, JSON, HTML, SVG, CSV, and plist outputs are treated as generated
  artifacts and must not include raw prompt, raw code, raw command output, or
  raw secrets.
- No network destination is trusted because the runtime does not need network
  access for normal operation.

## Main Risks

Path disclosure:

- Reports may reveal `/Users/<name>` or `/home/<name>` paths.
- Mitigation: `summary`, `export`, and `dashboard` support `--redact-paths`.

Cross-machine correlation:

- Raw SHA hashes can make identical logs linkable across machines.
- Mitigation: schema v8 initializes `schema_meta.hash_salt` and uses local
  HMAC-SHA256 for derived `*_hash` values.

HTML injection:

- Log-derived strings may contain HTML or Markdown syntax.
- Mitigation: HTML renderers escape all log-derived strings, use inline SVG
  only, and emit a restrictive Content Security Policy.

Malformed logs:

- JSONL inputs may be malformed, too long, or partially written.
- Mitigation: readers continue past malformed lines, emit scan errors, and keep
  raw payloads out of SQLite.

Plugin execution:

- Third-party providers are Python code and run with local process privileges.
- Mitigation: plugins are not loaded by default until local conformance passes.
  `doctor` does not import unverified providers by default. `--allow-unverified`
  is explicit and marked experimental.
- Verification imports the plugin module and executes its reader factory. Treat
  `providers verify` like importing a local Python package.
- Provider load failures are reported as warnings instead of being silently
  ignored.

## Non-Goals

- No cloud account integration.
- No email, Slack, webhook, or desktop notification integration.
- No localhost dashboard server.
- No plugin marketplace.
- No attempt to recover or migrate old raw SHA values exactly. The supported
  path is backup plus `aicg rebuild`.

## Operator Checklist

Before sharing an artifact outside the machine:

```bash
python -m aicg summary --since 24h --redact-paths --out daily.md
python -m aicg export --kind sessions --format json --redact-paths --out sessions.json
python -m aicg dashboard --redact-paths --out dashboard.html
```

Before using a third-party provider:

```bash
python -m aicg providers verify <module_or_package> --fixtures <fixtures_dir>
python -m aicg providers
```
