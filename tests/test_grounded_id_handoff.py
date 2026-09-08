"""Focused unit tests for Phase 3 grounded ID target handoff.

The resolver itself must run in ExtendScript.  These tests execute the pure
target-resolution path in Node with a minimal Illustrator DOM fixture, so the
contract is exercised without a running Illustrator instance.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from illustrator_mcp.protocol import (
    IdTarget,
    IdTargetPrecondition,
    TaskPayload,
    TargetSelector,
    TaskReport,
)


ROOT = Path(__file__).resolve().parents[1]
TARGETS_JSX = ROOT / "illustrator_mcp" / "resources" / "scripts" / "targets.jsx"


def _resolve(target: dict, items: list[dict]) -> dict:
    """Run ``resolveGroundedIdTargets`` against a small DOM-shaped fixture."""
    source = TARGETS_JSX.read_text(encoding="utf-8")
    harness = f"""
const input = {json.dumps({"target": target, "items": items})};
var ErrorCodes = {{ R_COLLECT_FAILED: "R001", R_ELEMENT_NOT_FOUND: "R008" }};
function makeError(code, message, stage, itemRef, details) {{
  return {{ ok: false, error: {{ code: code, message: message, stage: stage,
    itemRef: itemRef || null, details: details || null }} }};
}}
function extractMcpId(note) {{
  var match = (note || "").match(/@mcp:id=([^\\s@]+)/);
  return match ? match[1] : null;
}}
function describeItemV2(item) {{
  return {{ locator: {{ layerPath: "Layer 1", indexPath: [item.index] }},
    identity: {{ itemId: extractMcpId(item.note), idSource: "note" }},
    tags: {{ tags: {{}} }}, itemType: item.typename, itemName: item.name || null }};
}}
function isInsideClippingMask(item) {{ return !!item.insideClippingMask; }}
{source}

var doc = {{
  artboards: {{ getActiveArtboardIndex: function() {{ return 0; }},
    0: {{ artboardRect: [100, 700, 500, 300] }} }},
  pageItems: []
}};
var layer = {{ typename: "Layer", visible: true, locked: false, parent: doc }};
for (var i = 0; i < input.items.length; i++) {{
  var spec = input.items[i];
  var parent = {{ typename: "Layer", visible: spec.layer_visible !== false,
    locked: !!spec.layer_locked, parent: doc }};
  doc.pageItems.push({{
    index: i,
    typename: spec.typename || "PathItem",
    name: spec.name || "item_" + i,
    note: spec.id ? "@mcp:id=" + spec.id : "",
    hidden: !!spec.hidden,
    locked: !!spec.locked,
    clipping: !!spec.clipping,
    parent: parent,
    visibleBounds: spec.visibleBounds || [150, 650, 250, 550],
    geometricBounds: spec.geometricBounds || [151, 649, 249, 551]
  }});
}}
try {{
  var resolved = resolveGroundedIdTargets(doc, input.target);
  console.log(JSON.stringify({{ ok: true, count: resolved.length,
    metadata: resolved._resolvedTargetMetadata }}));
}} catch (error) {{
  console.log(JSON.stringify({{ ok: false, code: error.code, message: error.message,
    details: error.meta, itemRef: error.itemRef }}));
}}
"""
    result = subprocess.run(
        ["node", "-e", harness],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    return json.loads(result.stdout)


def _item(mcp_id: str, **changes: object) -> dict:
    return {"id": mcp_id, **changes}


def test_successful_id_handoff_returns_normalized_metadata():
    result = _resolve(
        {
            "type": "id",
            "ids": ["mcp_badge"],
            "precondition": {
                "type": "PathItem",
                "bounds_screen": [50, 50, 100, 100],
                "tolerance_pt": 0.5,
            },
        },
        [_item("mcp_badge")],
    )

    assert result["ok"] is True
    assert result["count"] == 1
    metadata = result["metadata"][0]
    assert metadata["mcp_id"] == "mcp_badge"
    assert metadata["item_ref"]["identity"]["itemId"] == "mcp_badge"
    assert metadata["typename"] == "PathItem"
    assert metadata["bounds"]["visible_ai"] == [150, 650, 250, 550]
    assert metadata["bounds"]["screen"] == [50, 50, 100, 100]
    assert metadata["artboard"]["index"] == 0
    assert metadata["state"]["visible_in_preview"] is True
    assert metadata["state"]["editable"] is True


@pytest.mark.parametrize(
    ("items", "reason", "code"),
    [
        ([], "missing_id", "R008"),
        ([_item("mcp_badge"), _item("mcp_badge", name="copy")], "duplicate_id", "R001"),
    ],
)
def test_missing_and_duplicate_ids_fail_before_operation(items, reason, code):
    result = _resolve({"type": "id", "ids": ["mcp_badge"]}, items)

    assert result["ok"] is False
    assert result["code"] == code
    assert result["details"]["reason"] == reason
    assert result["details"]["mcp_id"] == "mcp_badge"


@pytest.mark.parametrize(
    ("changes", "reason"),
    [({"hidden": True}, "hidden_target"), ({"locked": True}, "locked_target")],
)
def test_hidden_and_locked_id_targets_fail_before_operation(changes, reason):
    result = _resolve({"type": "id", "ids": ["mcp_badge"]}, [_item("mcp_badge", **changes)])

    assert result["ok"] is False
    assert result["code"] == "R001"
    assert result["details"]["reason"] == reason
    assert result["details"]["actual"]["mcp_id"] == "mcp_badge"


@pytest.mark.parametrize(
    "precondition",
    [
        {"type": "TextFrame"},
        {"bounds_screen": [51, 50, 100, 100], "tolerance_pt": 0.5},
    ],
)
def test_stale_type_or_bounds_precondition_returns_expected_and_actual(precondition):
    result = _resolve(
        {"type": "id", "ids": ["mcp_badge"], "precondition": precondition},
        [_item("mcp_badge")],
    )

    assert result["ok"] is False
    assert result["code"] == "R001"
    assert result["details"]["reason"] == "precondition_failed"
    assert result["details"]["expected"] == precondition
    assert result["details"]["actual"]["typename"] == "PathItem"
    assert result["details"]["actual"]["bounds"]["screen"] == [50, 50, 100, 100]


def test_legacy_id_target_without_precondition_remains_valid():
    result = _resolve({"type": "id", "ids": ["mcp_badge"]}, [_item("mcp_badge")])

    assert result["ok"] is True
    assert result["metadata"][0]["mcp_id"] == "mcp_badge"

    payload = TaskPayload(task="style_set_fill", targets={"type": "id", "ids": ["mcp_badge"]})
    assert payload.model_dump(mode="json")["targets"] == {
        "type": "id", "ids": ["mcp_badge"], "precondition": None
    }


def test_id_target_schema_accepts_typed_handoff_and_report_metadata():
    target = IdTarget(
        ids=["mcp_badge"],
        precondition=IdTargetPrecondition(type="PathItem", bounds_screen=[50, 50, 100, 100]),
    )
    selector = TargetSelector(target=target)
    assert selector.target.precondition.tolerance_pt == 0.0

    report = TaskReport.model_validate({
        "ok": True,
        "resolvedTargets": [{"mcp_id": "mcp_badge", "typename": "PathItem"}],
    })
    assert report.resolvedTargets[0]["mcp_id"] == "mcp_badge"
