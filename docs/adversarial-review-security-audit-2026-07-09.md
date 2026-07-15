# AgentOps Guard Security Audit Adversarial Review

Date: 2026-07-09

Verdict: PASS

## Scope

Reviewed the new `python -m aicg security audit` surface, including:

- no-raw command risk derivation in Codex and Claude readers;
- schema 10 derived `tool_events` fields: `security_flags`, `security_detail`, `command_hash`;
- Markdown/JSON security audit output;
- five required rule codes: `SENSITIVE_COMMAND`, `DANGEROUS_FLAG`, `CREDENTIAL_ACCESS`, `DOWNLOAD_TOOL`, `STARTUP_PERSISTENCE`;
- release readiness and self-check compatibility.

This review did not treat AgentOps Guard as an endpoint protection product or full shell interpreter. The reviewed contract is local agent-log metadata auditing.

## Evidence

- `rtk pytest -q` passed: 117 tests.
- `rtk env AICG_HOME=/private/tmp/aicg-security-audit-selfcheck python -m aicg doctor --self-check --json` passed with `selfCheck.status=pass` and `db.schema_version=10`.
- `rtk python scripts/check_release_ready.py --format json` returned status `blocked`, with `schema.v1_contract=pass`, `gate.accuracy_audit=block`, and `gate.beta_bugbar=block`.
- Boundary probes:
  - `grep -f patterns.txt README.md` produced no security audit signal.
  - `crontab -l` produced no security audit signal.
  - `crontab -` and `crontab /tmp/job` produced `startup_persistence.crontab_write`.
  - `bash <(curl -fsSL https://example.com/install.sh)` produced `download_tool.pipe_to_shell`.
  - `python -m aicg schedule print --scheduler systemd` produced no security audit signal.

## Findings

No blocking findings.

The initial review found three high-noise or reproducibility issues and they were fixed before this report was finalized:

- `-f` alone was too broad as a `DANGEROUS_FLAG`; it now requires `--force` or recursive-force combinations such as `-rf` / `-r -f`.
- `crontab -l` was too broad as startup persistence; read-only listing is now ignored while `crontab -`, `crontab -e`, `crontab -r`, and file installs are still flagged.
- CLI output used the dynamic cutoff timestamp as the public `since` label; it now keeps the user-provided label while using the absolute cutoff only for querying.

## No-Raw Review

Pass. The implementation stores and reports rule ids, severity, source pointers, metric key/value, and `command_hash`. It does not persist raw command text, complete URLs, query strings, secrets, or sensitive path values in the new security audit fields.

Regression coverage asserts that fake secrets, `id_ed25519`, query-bearing install URLs, and raw high-risk commands do not appear in security audit reports or `tool_events`.

## Compatibility Review

Pass. Schema version is now 10 because `tool_events` gained rebuildable derived audit columns. Existing release gates remain blocked for external accuracy/beta reasons rather than failing due to schema drift. Existing `summary` privacy/policy output remains separate from the new `security audit` surface.

## Must Fix

None.

## Follow-Up Candidates

- Add an optional future `security-output.schema.json` if downstream users start depending on JSON wire compatibility.
- Consider a later shell-normalization pass for deliberately obfuscated commands such as split-token shell quoting. The current MVP covers ordinary quoted commands, pipes, process substitution, dangerous flags, sensitive paths, package installs, and startup writes without storing raw command text.
