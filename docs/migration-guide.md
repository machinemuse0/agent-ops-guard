# Migration Guide

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

- `schema_meta.version` is `8`.
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
cp ~/.aicg/aicg.sqlite ~/.aicg/aicg.sqlite.v0.7.bak
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
