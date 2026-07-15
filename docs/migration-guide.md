# Migration Guide

## Any 0.x to 1.0

`v1.0` data contract currently uses schema `10`, introduced during `v0.9`
release prep to store no-raw security audit metadata on derived tool events.

Recommended upgrade path:

```bash
pip install -U aicg
python -m aicg rebuild --since all
python -m aicg doctor --self-check --json
```

Expected result:

- `python -m aicg --version` reports the package version, schema `10`, and the
  review ruleset version.
- `schema_meta.schema_version` is `10`.
- `schema_meta.hash_salt` exists and remains local.
- user-state tables such as `policy_acks`, `git_links`, `report_snapshots`, and
  `alert_events` are preserved.
- derived tables such as `sessions`, `turns`, `issues`, `review_findings`, and
  `format_observations` are rebuilt from available local sources.

Release rehearsal checklist:

- v0.5-like DB: rebuild succeeds, old derived rows may be regenerated, user
  acknowledgements and alert history remain.
- v0.9 DB: rebuild succeeds with the schema 10 derived-column bump, support bundle generation still
  passes privacy self-check.
- `doctor --self-check --json` returns exit `0` on both rehearsals.

## v0.8 to v0.9+

`v0.9` release prep upgrades the SQLite schema through version 10. Schema 9
adds the derived `format_observations` table for reader format drift visibility;
schema 10 adds no-raw security audit metadata on `tool_events`.

Run:

```bash
python -m aicg rebuild --since all
python -m aicg doctor --self-check --json
```

Expected result:

- `schema_meta.schema_version` is `10`.
- `format_observations` exists and is rebuilt from available source logs.
- `tool_events.security_flags`, `tool_events.security_detail`, and
  `tool_events.command_hash` exist and are rebuilt from available source logs.
- user-state tables such as `policy_acks`, `report_snapshots`, `git_links`, and
  `alert_events` are preserved.
- local pricing files no longer contain `reasoning_output_per_mtok_usd`.

## v0.7 to v0.8

`v0.8` upgrades the SQLite schema to version 8 and changes derived hash
semantics. Each local database receives a `schema_meta.hash_salt` value created
with `secrets.token_hex(32)`.

Run:

```bash
python -m aicg rebuild --since all
python -m aicg doctor --self-check --json
```

Expected result:

- `schema_meta.schema_version` is `8`.
- `schema_meta.hash_salt` exists.
- `report_snapshots.report_hash` is recomputed from `report_json`.
- historical `alert_events` hash fields are HMAC-wrapped with the local salt.
- user-state tables such as `policy_acknowledgements` and `alert_events` are
  preserved.

## Why Rebuild Is Required

Old raw SHA values cannot be made equivalent to salted HMAC values without the
original source material. AgentOps Guard does not attempt an in-place exact
history migration. The supported path is to keep a local backup and rebuild
derived metadata from normalized rows and available local source files.

## Recommended Backup

```bash
cp ~/.aicg/aicg.sqlite ~/.aicg/aicg.sqlite.pre-rebuild.bak
python -m aicg rebuild --since all
```

## Redacted Artifact Export

Use redacted outputs before sharing reports:

```bash
python -m aicg summary --since 24h --redact-paths --out daily.md
python -m aicg export --kind sessions --format json --redact-paths --out sessions.json
python -m aicg dashboard --redact-paths --out dashboard.html
```

`--redact-paths` replaces `/Users/<name>` and `/home/<name>` prefixes with `~`.

## Provider Plugins

Third-party providers must pass local conformance before default loading:

```bash
python -m aicg providers verify <module_or_package> --fixtures <fixtures_dir>
python -m aicg providers
```

During plugin development, `--allow-unverified` can be used explicitly. It is
experimental and should not be used in automated team workflows.
