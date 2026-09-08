"""Regression tests for the JSX -> TaskReport contract.

Background: ``makeError()`` returns a *result envelope*
``{ok: false, error: {...}}``.  Pushing that into ``report.errors`` (which
``protocol.TaskError`` requires to be flat ``{stage, code, message}``) made
``TaskReport.model_validate`` fail for every task failure, so execute_task
silently fell back to a degraded summary and logged
``Failed to parse TaskReport``.  The same applied to bare-string entries in
``report.warnings`` (``protocol.TaskWarning`` is a model, not a string).
"""

import json
from pathlib import Path

import pytest

from illustrator_mcp.protocol import TaskReport

SCRIPTS = Path(__file__).parent.parent / "illustrator_mcp" / "resources" / "scripts"
TEMPLATES = Path(__file__).parent.parent / "illustrator_mcp" / "resources" / "templates"


# ── Source-level guards (ExtendScript cannot run under pytest) ──────────


def test_task_pipeline_never_pushes_envelope_shaped_errors():
    src = (SCRIPTS / "task_pipeline.jsx").read_text()
    assert "report.errors.push(makeError(" not in src, (
        "report.errors entries must be flat TaskErrors — use makeTaskError()"
    )
    assert src.count("report.errors.push(makeTaskError(") == 5


def test_ops_core_validate_op_emits_flat_task_errors():
    """validateOp errors flow into results[i].error and then report.errors."""
    src = (SCRIPTS / "ops_core.jsx").read_text()
    start = src.index("function validateOp(op, strict) {")
    end = src.index("\nfunction ", start + 10)
    assert "errors.push(makeError(" not in src[start:end]
    assert src[start:end].count("errors.push(makeTaskError(") == 5


def test_make_task_error_is_exported_by_manifest():
    manifest = json.loads((SCRIPTS / "manifest.json").read_text())
    assert "makeTaskError" in manifest["libraries"]["task_pipeline"]["exports"]


def test_soc_batch_pushes_task_warning_objects_not_strings():
    src = (TEMPLATES / "compute_soc_batch.jsx").read_text()
    idx = src.index("report.warnings.push(")
    window = src[idx: idx + 320]
    assert "report.warnings.push({" in window, (
        "report.warnings entries must be TaskWarning objects, not bare strings"
    )
    assert 'stage: "compute"' in window


# ── Contract-level guards (the shapes the JSX now emits must validate) ──


def test_collect_failure_report_validates_as_taskreport():
    """The exact shape task_pipeline.jsx emits for a grounded-ID rejection."""
    report = {
        "ok": False,
        "stats": {"itemsProcessed": 0, "itemsCreated": 0,
                  "itemsModified": 0, "itemsSkipped": 0},
        "timing": {"collect_ms": 12, "compute_ms": 0, "apply_ms": 0, "total_ms": 12},
        "warnings": [],
        "errors": [{
            "code": "R001",
            "message": "Grounded ID target no longer satisfies its precondition: mcp_x1",
            "stage": "collect",
            "itemRef": None,
            "details": {"reason": "precondition_failed", "mcp_id": "mcp_x1"},
        }],
        "resolvedTargets": [],
    }
    parsed = TaskReport.model_validate(report)
    assert parsed.ok is False
    assert parsed.errors[0].code == "R001"
    assert parsed.errors[0].stage == "collect"
    assert parsed.errors[0].details["reason"] == "precondition_failed"


def test_soc_batch_zero_target_warning_validates_as_taskreport():
    report = {
        "ok": True,
        "stats": {"itemsProcessed": 1, "itemsCreated": 0,
                  "itemsModified": 0, "itemsSkipped": 0},
        "timing": {"collect_ms": 0, "compute_ms": 5, "apply_ms": 0, "total_ms": 5},
        "warnings": [{
            "stage": "compute",
            "message": "Resolved 0 targets for task 'style_set_fill'; no items modified.",
            "suggestion": "Check target IDs/selectors.",
        }],
        "errors": [],
    }
    parsed = TaskReport.model_validate(report)
    assert parsed.warnings[0].stage == "compute"


# ── End-to-end: no degraded fallback for a well-formed failure ──────────


@pytest.mark.asyncio
async def test_grounded_rejection_takes_no_degraded_fallback(monkeypatch, caplog):
    """A well-formed grounded-ID rejection must parse cleanly.

    It must keep the structured error *and* must not emit the misleading
    'validation failed / some op-level errors may be missing' warning.
    """
    from unittest.mock import AsyncMock

    from illustrator_mcp.tools.task_execution import (
        ExecuteTaskInput,
        illustrator_execute_task,
    )
    from illustrator_mcp.protocol import TaskPayload

    raw_report = {
        "ok": False,
        "stats": {"itemsProcessed": 0, "itemsCreated": 0,
                  "itemsModified": 0, "itemsSkipped": 0},
        "timing": {"collect_ms": 12, "compute_ms": 0, "apply_ms": 0, "total_ms": 12},
        "warnings": [],
        "errors": [{
            "code": "R001",
            "message": "Grounded ID target no longer satisfies its precondition: mcp_x1",
            "stage": "collect",
            "itemRef": None,
            "details": {"reason": "precondition_failed", "mcp_id": "mcp_x1"},
        }],
        "resolvedTargets": [],
    }
    monkeypatch.setattr(
        "illustrator_mcp.tools.task_execution.execute_script_with_context",
        AsyncMock(return_value={"result": json.dumps(raw_report)}),
    )

    with caplog.at_level("WARNING"):
        response = await illustrator_execute_task(ExecuteTaskInput(
            payload=TaskPayload(task="live_grounded_handoff",
                                targets={"type": "id", "ids": ["mcp_x1"]}),
            compute_fn="return [];",
            apply_fn="return;",
            return_preview=False,
        ))

    envelope = json.loads(response)

    # Structured error preserved
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "R001"
    assert envelope["error"]["details"]["reason"] == "precondition_failed"

    # No degraded fallback
    assert "Failed to parse TaskReport" not in caplog.text
    assert "taskreport_parse" not in envelope["diagnostics"]
    joined = " ".join(envelope["warnings"])
    assert "validation failed" not in joined
    assert "[op-error]" not in joined

    # The proper (non-degraded) failure branch records stats in diagnostics;
    # the degraded fallback never did.  make_envelope drops `result` on
    # failures by contract, so diagnostics is the observable signal here.
    assert envelope["diagnostics"]["stats"]["itemsProcessed"] == 0
    assert "fallback_used" not in json.dumps(envelope["diagnostics"])
