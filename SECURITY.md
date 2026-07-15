# Security Policy

AgentOps Guard is a local-first CLI. It does not run a network service, phone
home, scrape billing pages, or upload diagnostics. Security response focuses on
privacy leaks, parser crashes, report injection, local DB corruption, and unsafe
plugin loading behavior.

## Reporting

Private disclosure channel: `security@example.invalid` until a project-specific
security address or GitHub private advisory channel is configured.

Please include:

- affected version from `python -m aicg --version`;
- platform and Python version;
- minimal reproduction steps;
- whether raw prompt/code/output, secrets, or sensitive paths appeared in an
  output artifact.

Do not attach raw private logs. Use:

```bash
python -m aicg fixture redact raw.jsonl --out fixture.jsonl --keep-structure
python -m aicg support bundle --out bundle.json
```

Review both files manually before sharing them.

## Response Targets

- Acknowledge valid reports within 72 hours.
- Triage severity and request missing reproduction details within 7 days.
- Coordinate disclosure for up to 90 days when a fix needs time to reach users.
- Treat privacy leaks of raw prompt, raw code, raw command output, secrets, or
  sensitive paths as P0 and release a patch as soon as practical.

## Scope

In scope:

- raw prompt/code/output leak in reports, exports, support bundles, fixtures, or
  SQLite metadata;
- secret or sensitive path leak after documented redaction options are used;
- parser crash or malformed input that blocks otherwise valid logs;
- Markdown/HTML/JSON injection through log-derived strings;
- provider load failures being hidden from operator diagnostics;
- DB schema or rebuild behavior that corrupts user-state tables.

Out of scope:

- cloud account compromise unrelated to AgentOps Guard;
- arbitrary code execution from intentionally installed third-party provider
  plugins after the user explicitly imports or verifies them;
- vulnerabilities requiring modification of the local source tree by an already
  trusted local user.

## Security Model

The detailed threat model is maintained in `docs/threat-model.md`. The permanent
release boundary is:

- no default network behavior;
- no telemetry;
- no raw prompt/code/output persistence;
- no automatic cache deletion or agent config modification;
- no default LLM-assisted diagnosis path.
