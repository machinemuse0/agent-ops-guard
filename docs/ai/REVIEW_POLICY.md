# Cross-Vendor Review Policy — AgentOps Guard

## Review inputs

- Approved task and plan.
- Clean committed base-to-head diff.
- Tests and deterministic verification evidence.
- Relevant stability, semantics, threat-model, schema, and ADR documents.

Do not use hidden Writer reasoning or include raw session data in review evidence.

## Required review dimensions

- Session ownership and project attribution.
- Retry accounting, Codex delta streams, and Claude cache token semantics.
- Pricing coverage, provider drift, malformed input, and partial/interrupted logs.
- No-raw persistence, secret/path redaction, stable salted hashes, and bundles.
- SQLite concurrency, busy timeouts, rebuild/migration, idempotence, and recovery.
- Provider fixture provenance, redaction, capability declarations, and conformance.
- CLI/JSON/schema/stable-ID compatibility and version synchronization.
- HTML/SVG injection, command risk classification, and support-bundle privacy.
- Offline defaults, bounded directory scans, memory/runtime bounds, and amplification.
- Release gate truthfulness: pending external evidence must remain visibly blocked.

## Finding format

```text
Severity: BLOCKER | HIGH | MEDIUM | LOW
Location: path:line
Problem:
Evidence or counterexample:
Expected behavior:
Minimal fix direction:
```

## Noise policy

Do not report formatting, import ordering, naming preferences, or style already
handled by tools unless they create a correctness, privacy, or compatibility defect.

## Verdict

- APPROVE
- REQUEST_CHANGES
- BLOCKED_BY_MISSING_EVIDENCE

The final verdict is the last unformatted line.
