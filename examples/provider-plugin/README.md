# Example Provider Plugin

This directory contains a minimal provider plugin module and a fixture that can
be verified without installing external dependencies.

From this directory:

```bash
PYTHONPATH=. python -m aicg providers verify aicg_example_provider --fixtures fixtures/
```

The module exposes `create_provider_reader`, which returns a local reader using
the stable `ProviderReader`, `Capabilities`, and `ParsedRecords` API.
