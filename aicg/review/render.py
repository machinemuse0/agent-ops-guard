from __future__ import annotations

import json
from typing import Any

from ..models import ReviewEvidence, ReviewFinding
from ..util import detect_privacy_flags, escape_markdown_text, markdown_code
from .rules import CONFIDENCE_ORDER, REVIEW_RULESET_VERSION, SessionReview


REVIEW_SCHEMA_VERSION = 1


def build_session_review_model(review: SessionReview) -> dict[str, Any]:
    session = review.session
    return {
        "schemaVersion": REVIEW_SCHEMA_VERSION,
        "rulesetVersion": REVIEW_RULESET_VERSION,
        "kind": "session",
        "session": {
            "id": session.id,
            "provider": session.provider,
            "status": session.status,
            "startedAt": session.started_at,
            "endedAt": session.ended_at,
            "sourceFileHash": session.source_file_hash,
            "sourceLineStart": session.source_line_start,
            "sourceLineEnd": session.source_line_end,
            "turns": len(session.turns),
            "toolEvents": len(session.tools),
        },
        "findings": [_finding_model(finding) for finding in review.findings],
        "beforeNextRun": _before_next_run(review.findings),
    }


def build_batch_review_model(reviews: list[SessionReview], *, since: str | None, top: int | None) -> dict[str, Any]:
    return {
        "schemaVersion": REVIEW_SCHEMA_VERSION,
        "rulesetVersion": REVIEW_RULESET_VERSION,
        "kind": "batch",
        "since": since,
        "top": top,
        "sessions": [build_session_review_model(review)["session"] | {"findingCount": len(review.findings)} for review in reviews],
        "findings": [
            _finding_model(finding)
            for review in reviews
            for finding in review.findings
        ],
        "beforeNextRun": _before_next_run([finding for review in reviews for finding in review.findings]),
    }


def build_project_review_model(
    project_path: str,
    reviews: list[SessionReview],
    *,
    since: str,
    limit: int | None = None,
    matched_sessions: int | None = None,
    truncated: bool = False,
) -> dict[str, Any]:
    grouped: dict[str, dict[str, Any]] = {}
    for review in reviews:
        for finding in review.findings:
            bucket = grouped.setdefault(
                finding.code,
                {
                    "code": finding.code,
                    "confidence": finding.confidence,
                    "findings": 0,
                    "sessionIds": set(),
                    "firstSeenAt": None,
                    "lastSeenAt": None,
                },
            )
            if CONFIDENCE_ORDER[finding.confidence] > CONFIDENCE_ORDER[bucket["confidence"]]:
                bucket["confidence"] = finding.confidence
            bucket["findings"] += 1
            bucket["sessionIds"].add(finding.session_id)
            started_at = review.session.started_at or review.session.ended_at
            if started_at:
                if bucket["firstSeenAt"] is None or started_at < bucket["firstSeenAt"]:
                    bucket["firstSeenAt"] = started_at
                if bucket["lastSeenAt"] is None or started_at > bucket["lastSeenAt"]:
                    bucket["lastSeenAt"] = started_at
    patterns = []
    for item in sorted(grouped.values(), key=lambda row: (-int(row["findings"]), row["code"])):
        patterns.append(
            {
                "code": item["code"],
                "confidence": item["confidence"],
                "findings": item["findings"],
                "sessions": len(item["sessionIds"]),
                "firstSeenAt": item["firstSeenAt"],
                "lastSeenAt": item["lastSeenAt"],
            }
        )
    return {
        "schemaVersion": REVIEW_SCHEMA_VERSION,
        "rulesetVersion": REVIEW_RULESET_VERSION,
        "kind": "project",
        "projectPath": project_path,
        "since": since,
        "limit": limit,
        "matchedSessions": matched_sessions if matched_sessions is not None else len(reviews),
        "truncated": truncated,
        "sessionsReviewed": len(reviews),
        "patterns": patterns,
        "beforeNextRun": _before_next_run([finding for review in reviews for finding in review.findings]),
    }


def render_review_json(model: dict[str, Any]) -> str:
    return json.dumps(model, ensure_ascii=False, indent=2, sort_keys=True)


def render_review_markdown(model: dict[str, Any]) -> str:
    kind = model.get("kind")
    if kind == "project":
        return _render_project_markdown(model)
    if kind == "batch":
        return _render_batch_markdown(model)
    return _render_session_markdown(model)


def render_issue_template(review: SessionReview) -> str:
    model = build_session_review_model(review)
    session = model["session"]
    lines = [
        "# AgentOps Guard Review Issue",
        "",
        "This file contains no prompt/code/output raw text.",
        "",
        "## Session Metadata",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Session | {_md_code(session['id'])} |",
        f"| Provider | {_md_text(session['provider'])} |",
        f"| Status | {_md_text(session.get('status') or 'unknown')} |",
        f"| Started | {_md_text(session.get('startedAt') or 'unknown')} |",
        f"| Source hash | {_md_code(session.get('sourceFileHash') or 'unknown')} |",
        f"| Source lines | {_md_text(_line_range(session))} |",
        "",
        "## Diagnostics",
        "",
    ]
    findings = model["findings"]
    if not findings:
        lines.append("- none")
    else:
        for finding in findings:
            lines.append(f"- `{finding['code']}` [{finding['confidence']}]: {_md_text(finding['detail'])}")
            lines.append(f"  Recommendation: {_md_text(finding['recommendation'])}")
    lines.extend(["", "## Evidence Pointers", ""])
    lines.extend(_evidence_markdown(findings))
    lines.extend(["", "## Reproduce", "", "```bash", f"python -m aicg inspect session {session['id']}", "```", ""])
    lines.extend([
        "## Resolve Source",
        "",
        "```bash",
        f"python -m aicg inspect source {session.get('sourceFileHash') or 'unknown'}",
        "```",
        "",
    ])
    text = "\n".join(lines)
    _assert_no_secret_like_output(text)
    return text


def _render_session_markdown(model: dict[str, Any]) -> str:
    session = model["session"]
    lines = [
        "# AgentOps Guard Review",
        "",
        f"- Session: {_md_code(session['id'])}",
        f"- Provider: {_md_text(session['provider'])}",
        f"- Status: {_md_text(session.get('status') or 'unknown')}",
        f"- Ruleset version: {model['rulesetVersion']}",
        f"- Source hash: {_md_code(session.get('sourceFileHash') or 'unknown')}",
        f"- Resolve source: `python -m aicg inspect source {session.get('sourceFileHash') or 'unknown'}`",
        f"- Source lines: {_md_text(_line_range(session))}",
        "",
        "## Findings",
        "",
    ]
    findings = model["findings"]
    if not findings:
        lines.append("No review findings detected.")
    else:
        for finding in findings:
            lines.append(f"- `{finding['code']}` [{finding['confidence']}]: {_md_text(finding['detail'])}")
            lines.append(f"  Recommendation: {_md_text(finding['recommendation'])}")
    lines.extend(["", "## Evidence pointers", ""])
    lines.extend(_evidence_markdown(findings))
    lines.extend(["", "## Before the next run", ""])
    lines.extend(_before_next_run_markdown(model.get("beforeNextRun") or []))
    return "\n".join(lines)


def _render_batch_markdown(model: dict[str, Any]) -> str:
    lines = [
        "# AgentOps Guard Review Batch",
        "",
        f"- Since: {_md_text(model.get('since') or 'unknown')}",
        f"- Top: {_md_text(model.get('top') or 'all')}",
        f"- Sessions reviewed: {len(model.get('sessions') or [])}",
        "",
        "## Findings",
        "",
    ]
    findings = model.get("findings") or []
    if not findings:
        lines.append("No review findings detected.")
    else:
        for finding in findings:
            lines.append(
                f"- `{finding['sessionId']}` `{finding['code']}` [{finding['confidence']}]: {_md_text(finding['detail'])}"
            )
    lines.extend(["", "## Before the next run", ""])
    lines.extend(_before_next_run_markdown(model.get("beforeNextRun") or []))
    return "\n".join(lines)


def _render_project_markdown(model: dict[str, Any]) -> str:
    lines = [
        "# AgentOps Guard Project Review",
        "",
        f"- Project: {_md_code(model.get('projectPath') or 'unknown')}",
        f"- Since: {_md_text(model.get('since') or 'unknown')}",
        f"- Limit: {_md_text(_project_limit_text(model.get('limit')))}",
        f"- Matched sessions: {model.get('matchedSessions', model.get('sessionsReviewed', 0))}",
        f"- Sessions reviewed: {model.get('sessionsReviewed', 0)}",
        f"- Truncated: {_md_text('yes' if model.get('truncated') else 'no')}",
        "",
        "## Recurring patterns",
        "",
    ]
    patterns = model.get("patterns") or []
    if not patterns:
        lines.append("No recurring review patterns detected.")
    else:
        lines.extend(["| Code | Confidence | Findings | Sessions | First seen | Last seen |", "| --- | --- | ---: | ---: | --- | --- |"])
        for row in patterns:
            lines.append(
                f"| `{row['code']}` | {_md_text(row['confidence'])} | {row['findings']} | {row['sessions']} | {_md_text(row.get('firstSeenAt') or 'unknown')} | {_md_text(row.get('lastSeenAt') or 'unknown')} |"
            )
    lines.extend(["", "## Before the next run", ""])
    lines.extend(_before_next_run_markdown(model.get("beforeNextRun") or []))
    return "\n".join(lines)


def _project_limit_text(limit: Any) -> str:
    if limit is None:
        return "unknown"
    if int(limit) == 0:
        return "all"
    return str(limit)


def _finding_model(finding: ReviewFinding) -> dict[str, Any]:
    return {
        "id": finding.id,
        "sessionId": finding.session_id,
        "rulesetVersion": finding.ruleset_version,
        "code": finding.code,
        "confidence": finding.confidence,
        "detail": finding.detail,
        "recommendation": finding.recommendation,
        "evidence": [_evidence_model(pointer) for pointer in finding.evidence_pointers],
    }


def _evidence_model(pointer: ReviewEvidence) -> dict[str, Any]:
    return {
        "id": pointer.id,
        "turnId": pointer.turn_id,
        "toolEventId": pointer.tool_event_id,
        "sourceFileHash": pointer.source_file_hash,
        "sourceLineStart": pointer.source_line_start,
        "sourceLineEnd": pointer.source_line_end,
        "metricName": pointer.metric_name,
        "metricValue": pointer.metric_value,
        "messageHash": pointer.message_hash,
    }


def _evidence_markdown(findings: list[dict[str, Any]]) -> list[str]:
    pointers = [pointer | {"code": finding["code"]} for finding in findings for pointer in finding.get("evidence", [])]
    if not pointers:
        return ["- none"]
    lines = ["| Code | Metric | Value | Source hash | Lines | Turn | Tool event |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for pointer in pointers:
        lines.append(
            f"| `{pointer['code']}` | {_md_text(pointer.get('metricName') or 'unknown')} | {_md_text(pointer.get('metricValue') or 'unknown')} | {_md_code(pointer.get('sourceFileHash') or 'unknown')} | {_md_text(_line_range(pointer))} | {_md_code(pointer.get('turnId') or 'none')} | {_md_code(pointer.get('toolEventId') or 'none')} |"
        )
    return lines


def _before_next_run(findings: list[ReviewFinding]) -> list[str]:
    seen: set[str] = set()
    actions = []
    for finding in findings:
        item = f"[{finding.code}] {finding.recommendation}"
        if item in seen:
            continue
        seen.add(item)
        actions.append(item)
    if not actions:
        actions.append("No review-specific action required before the next run.")
    return actions


def _before_next_run_markdown(items: list[str]) -> list[str]:
    return [f"- {_md_text(item)}" for item in items]


def _line_range(row: dict[str, Any]) -> str:
    start = row.get("sourceLineStart") or "unknown"
    end = row.get("sourceLineEnd") or "unknown"
    return f"{start}-{end}"


def _assert_no_secret_like_output(text: str) -> None:
    flags = detect_privacy_flags(text, raw_payload_threshold=10**9)
    if flags:
        raise ValueError("review issue template failed privacy self-check")


def _md_text(value: object) -> str:
    return escape_markdown_text(value)


def _md_code(value: object) -> str:
    return markdown_code(value)
