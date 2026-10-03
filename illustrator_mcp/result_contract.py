"""Additive evidence for execution and recovery; never infer rollback or read-back."""

from typing import Any


def execution(state: str, request_id: int | None = None) -> dict:
    record = {"state": state, "safe_to_retry": state == "not_started"}
    if request_id is not None:
        record["request_id"] = request_id
    return record


def add_result_evidence(envelope: dict[str, Any]) -> dict[str, Any]:
    """Keep legacy ok/result intact; add an evidence summary to diagnostics."""
    diagnostics = envelope.setdefault("diagnostics", {})
    phase = diagnostics.setdefault("execution", execution("unknown"))
    error = envelope.get("error")
    if isinstance(error, dict):
        error.setdefault("safe_to_retry", phase.get("safe_to_retry") is True)
    result = envelope.get("result")
    report = result.get("report", result) if isinstance(result, dict) else None
    summary: dict[str, Any] = {"status": "unverified", "verification": "not_performed"}
    if isinstance(report, dict):
        def count(value: Any) -> bool:
            return type(value) is int and value >= 0

        if count(report.get("success_count")) and count(report.get("fail_count")):
            completed = report["success_count"]
            failed = report["fail_count"]
            skipped = report.get("skipped_objects") or []
            if not isinstance(skipped, list):
                skipped = []
            summary.update(
                status="partial" if failed or skipped else "full",
                completed=completed, failed=failed, skipped=len(skipped),
                affected=report.get("changed", report.get("objects", [])),
                failed_objects=report.get("failed_objects", []),
                skipped_objects=skipped,
            )
        batch = report.get("batchReport", report)
        stats = batch.get("stats") if isinstance(batch, dict) else None
        if (isinstance(stats, dict) and count(stats.get("executed")) and count(stats.get("total"))
                and count(stats.get("passed", 0)) and count(stats.get("failed", 0))):
            summary.update(
                status="full" if batch.get("ok") else "partial",
                completed=stats.get("passed", 0), failed=stats.get("failed", 0),
                unattempted=max(0, stats["total"] - stats["executed"]),
                created_ids=batch.get("createdIds", []),
                rolled_back=batch.get("rolledBack", 0),
                operations=batch.get("opSummary", batch.get("ops", [])),
                operations_truncated=batch.get("opSummaryTruncated", False),
            )
        if report.get("dry_run"):
            summary.update(status="dry_run", verification="not_performed")
        # Verification is supplied explicitly by the code doing the read-back.
        if isinstance(report.get("verification"), dict):
            summary["verification"] = report["verification"]
    diagnostics.setdefault("changes", summary)
    return envelope
