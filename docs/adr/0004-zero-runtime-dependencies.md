# ADR 0004: Zero Runtime Dependencies

Status: accepted

## Decision

AgentOps Guard keeps runtime code on the Python standard library unless a future
ADR justifies an exception.

## Rationale

The tool is a local trust boundary around sensitive logs. A stdlib-only runtime
keeps installation simple and supply-chain audit cost low.

## Consequences

New features should prefer simple local formats and avoid adding dependencies.
Test, build, or documentation tooling may use development dependencies when they
do not become runtime requirements.
