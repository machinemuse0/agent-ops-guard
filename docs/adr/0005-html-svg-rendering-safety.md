# ADR 0005: HTML And SVG Rendering Safety

Status: accepted

## Decision

HTML reports and dashboards are static files with escaped log-derived strings,
inline CSS/SVG only, and no JavaScript dependency.

## Rationale

Reports render untrusted local log metadata. Static HTML and inline SVG provide
useful visualization without creating a web app, localhost server, or script
execution surface.

## Consequences

Any renderer change must keep generated artifacts local, no-network, and safe
when log-derived text contains Markdown, HTML, or SVG-like payloads.
