from __future__ import annotations

import json
import uuid
from typing import Any

from .models import AlertEvent
from .util import sha256_text, stable_id, utc_now_iso


ALERT_SCHEMA_VERSION = 1


def evaluate_alerts(
    report: dict[str, Any],
    config: dict[str, Any],
    *,
    period_type: str,
    period_start: str,
    report_hash: str | None,
) -> dict[str, Any]:
    alert_config = config.get("alerts") if isinstance(config.get("alerts"), dict) else {}
    config_hash = _config_hash(alert_config)
    model = {
        "schemaVersion": ALERT_SCHEMA_VERSION,
        "configured": bool(alert_config),
        "period": {"type": period_type, "start": period_start},
        "reportHash": report_hash,
        "configHash": config_hash,
        "findings": [],
        "skipped": [],
    }
    if not alert_config:
        return model

    definitions = {
        "daily_cost_usd_max": ("overview.estimatedCostUsd", _overview_value(report, "estimatedCostUsd")),
        "daily_waste_rate_max": ("wasteBreakdown.wasteRate", _waste_value(report, "wasteRate")),
        "new_policy_violations_max": (
            "policyLifecycle.newByLevel.violation",
            _policy_level_value(report, "newByLevel", "violation"),
        ),
        "interrupted_sessions_max": ("overview.interruptedSessions", _overview_value(report, "interruptedSessions")),
    }
    findings: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for key, threshold_value in sorted(alert_config.items()):
        if key not in definitions:
            skipped.append({"key": key, "reason": "unknown_alert_key"})
            continue
        threshold = _numeric_threshold(key, threshold_value)
        metric_name, actual = definitions[key]
        if actual is None:
            skipped.append({"key": key, "metric": metric_name, "reason": "metric_unavailable"})
            continue
        if float(actual) > threshold:
            findings.append(
                {
                    "key": key,
                    "metric": metric_name,
                    "threshold": threshold,
                    "actual": float(actual),
                    "message": f"{metric_name}={float(actual):.6g} exceeds {threshold:.6g}",
                }
            )
    model["findings"] = findings
    model["skipped"] = skipped
    return model


def alert_events_from_model(model: dict[str, Any]) -> list[AlertEvent]:
    created_at = utc_now_iso()
    period = model.get("period") if isinstance(model.get("period"), dict) else {}
    period_type = str(period.get("type") or "unknown")
    period_start = str(period.get("start") or "unknown")
    report_hash = model.get("reportHash")
    config_hash = str(model.get("configHash") or "")
    events = []
    for finding in model.get("findings") or []:
        event_id = stable_id(
            "alert",
            uuid.uuid4().hex,
            created_at,
            period_type,
            period_start,
            finding.get("key"),
            finding.get("threshold"),
            finding.get("actual"),
            report_hash,
            config_hash,
        )
        events.append(
            AlertEvent(
                id=event_id,
                period_type=period_type,
                period_start=period_start,
                alert_key=str(finding["key"]),
                threshold_value=float(finding["threshold"]),
                actual_value=float(finding["actual"]) if finding.get("actual") is not None else None,
                report_hash=str(report_hash) if report_hash else None,
                config_hash=config_hash,
                created_at=created_at,
            )
        )
    return events


def render_alerts_text(model: dict[str, Any]) -> str:
    if not model.get("configured"):
        return "alerts: no [alerts] thresholds configured"
    findings = model.get("findings") or []
    if not findings:
        return "alerts: no thresholds exceeded"
    lines = ["alerts: thresholds exceeded"]
    for finding in findings:
        lines.append(
            f"- {finding['key']}: actual={finding['actual']:.6g} threshold={finding['threshold']:.6g}"
        )
    return "\n".join(lines)


def _overview_value(report: dict[str, Any], key: str) -> float | None:
    overview = report.get("overview") if isinstance(report.get("overview"), dict) else {}
    return _number_or_none(overview.get(key))


def _waste_value(report: dict[str, Any], key: str) -> float | None:
    waste = report.get("wasteBreakdown") if isinstance(report.get("wasteBreakdown"), dict) else {}
    return _number_or_none(waste.get(key))


def _policy_level_value(report: dict[str, Any], group: str, level: str) -> float | None:
    lifecycle = report.get("policyLifecycle") if isinstance(report.get("policyLifecycle"), dict) else {}
    levels = lifecycle.get(group) if isinstance(lifecycle.get(group), dict) else {}
    return _number_or_none(levels.get(level, 0))


def _number_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _numeric_threshold(key: str, value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"[alerts].{key} must be numeric") from exc


def _config_hash(alert_config: dict[str, Any]) -> str:
    return sha256_text(json.dumps(alert_config, ensure_ascii=False, sort_keys=True))
