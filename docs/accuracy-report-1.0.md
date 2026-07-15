# Accuracy Report 1.0

Status: pending

This file is a release-gate template. Do not mark it `passed` until the 30-day
audit in `docs/accuracy-audit.md` has completed.

## Summary

- Audit window: pending
- Participants: pending
- Local version: pending
- Median 30-day token deviation: pending
- Release gate result: pending

## Reproduction

```bash
python -m aicg rebuild --since all
python -m aicg summary --since 30d --format json --out accuracy-local.json
python -m aicg export --kind sessions --format json --since 30d --redact-paths --out accuracy-sessions.json
python -m aicg support bundle --out accuracy-bundle.json
```

## Participant Results

| Participant | Provider mix | Local tokens | Official tokens | Deviation | Attribution |
| --- | --- | ---: | ---: | ---: | --- |
| pending | pending | pending | pending | pending | pending |

## Release Decision

Pending. `v1.0` must not be released from this report until the status is
changed to `passed` with evidence.
