"""Focused unit tests for the Phase 2 annotated-preview grounding contract."""

from __future__ import annotations

import base64
import json
from unittest.mock import AsyncMock, patch

import pytest

from illustrator_mcp.tools.grounding import (
    GroundObjectInput,
    _grounding_collect_script,
    _selected_metadata,
    illustrator_ground_object,
)


def _snapshot(*, mcp_id: str = "", occurrences: int = 0) -> dict:
    return {
        "artboard": [100, 700, 500, 300],
        "artboard_index": 2,
        "coordinate_system": "artboard screen space: origin top-left, x right, y down, points",
        "items": [{
            "_ground_source_index": 7,
            "name": "badge",
            "type": "PathItem",
            "bounds": [150, 650, 250, 550],
            "mcp_id": mcp_id,
            "mcp_id_occurrences": occurrences,
            "item_ref": {"identity": {"itemId": mcp_id or None, "idSource": "note" if mcp_id else "none"}},
            "state": {"visible_in_preview": True, "editable": True, "inside_clipping_group": False},
            "visible_bounds_ai": [150, 650, 250, 550],
            "geometric_bounds_ai": [151, 649, 249, 551],
            "bounds_screen": [50, 50, 100, 100],
        }],
    }


def _annotation_map(mcp_id: str | None) -> dict:
    return {
        "meta": {"bounds_kind": "visibleBounds", "annotated_count": 1},
        "annotations": [{
            "label": "1",
            "mcp_id": mcp_id,
            "has_mcp_id": bool(mcp_id),
            "name": "badge",
            "type": "PathItem",
            "bounds_pt": [150, 650, 250, 550],
            "_ground_source_index": 7,
        }],
        "warnings": [],
    }


@pytest.mark.asyncio
async def test_untagged_item_is_reported_without_document_mutation():
    snapshot = _snapshot()
    with (
        patch("illustrator_mcp.tools.grounding.execute_script_with_context", new=AsyncMock(return_value={"result": json.dumps(snapshot)})) as execute,
        patch("illustrator_mcp.tools.grounding._capture_artboard", new=AsyncMock(return_value=b"raw")),
        patch("illustrator_mcp.tools.grounding._annotate_preview", new=AsyncMock(return_value=(b"annotated", _annotation_map(None)))),
    ):
        result = await illustrator_ground_object(GroundObjectInput(label=1))

    envelope = json.loads(result[0].text)
    selected = envelope["result"]["selected_object"]
    assert envelope["ok"] is True
    assert selected["stable_id"] == {"value": None, "status": "missing", "occurrences": 0}
    assert envelope["result"]["label_to_mcp_id"] == {"1": None}
    assert envelope["result"]["id_assignment"] == {"requested": False, "assigned": False, "document_saved": False, "error": None}
    assert len(execute.await_args_list) == 1
    assert "no stable @mcp:id" in envelope["warnings"][0]
    assert result[1].data == base64.b64encode(b"annotated").decode("ascii")


@pytest.mark.asyncio
async def test_explicit_assignment_updates_selected_metadata_and_label_map():
    snapshot = _snapshot()
    assignment = {"assigned": True, "mcp_id": "mcp_assigned_123"}
    with (
        patch(
            "illustrator_mcp.tools.grounding.execute_script_with_context",
            new=AsyncMock(side_effect=[{"result": json.dumps(snapshot)}, {"result": json.dumps(assignment)}]),
        ) as execute,
        patch("illustrator_mcp.tools.grounding._capture_artboard", new=AsyncMock(return_value=b"raw")),
        patch("illustrator_mcp.tools.grounding._annotate_preview", new=AsyncMock(return_value=(b"annotated", _annotation_map(None)))),
    ):
        result = await illustrator_ground_object(GroundObjectInput(label=1, assign_id=True))

    envelope = json.loads(result[0].text)
    selected = envelope["result"]["selected_object"]
    assert len(execute.await_args_list) == 2
    assert selected["mcp_id"] == "mcp_assigned_123"
    assert selected["stable_id"]["status"] == "assigned"
    assert selected["item_ref"]["identity"]["itemId"] == "mcp_assigned_123"
    assert envelope["result"]["label_to_mcp_id"] == {"1": "mcp_assigned_123"}
    assert envelope["result"]["id_assignment"] == {"requested": True, "assigned": True, "document_saved": False, "error": None}


@pytest.mark.asyncio
async def test_unknown_label_returns_current_preview_and_no_assignment():
    snapshot = _snapshot(mcp_id="mcp_badge", occurrences=1)
    with (
        patch("illustrator_mcp.tools.grounding.execute_script_with_context", new=AsyncMock(return_value={"result": json.dumps(snapshot)})) as execute,
        patch("illustrator_mcp.tools.grounding._capture_artboard", new=AsyncMock(return_value=b"raw")),
        patch("illustrator_mcp.tools.grounding._annotate_preview", new=AsyncMock(return_value=(b"annotated", _annotation_map("mcp_badge")))),
    ):
        result = await illustrator_ground_object(GroundObjectInput(label=2, assign_id=True))

    envelope = json.loads(result[0].text)
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "R008"
    assert envelope["diagnostics"]["label_to_mcp_id"] == {"1": "mcp_badge"}
    assert len(execute.await_args_list) == 1


def test_duplicate_id_is_explicitly_not_stable():
    metadata = _selected_metadata(_snapshot(mcp_id="duplicate", occurrences=2)["items"][0], "1", [0, 100, 100, 0], 0)
    assert metadata["stable_id"] == {"value": "duplicate", "status": "duplicate", "occurrences": 2}


def test_grounding_collector_reuses_item_ref_mcp_id_and_clipping_state():
    script = _grounding_collect_script(50, [10, 20, 30, 40])
    assert "extractMcpId" in script
    assert "describeItemV2" in script
    assert "inside_clipping_group" in script
    assert "isInsideClippingMask(item)" in script
    assert "layer_hidden" in script
    assert "idCounts" in script
    assert "var cL = abL + 10" in script


def test_preview_collector_uses_hierarchy_visibility_and_full_mcp_id_pattern():
    from illustrator_mcp.tools.preview import _COLLECT_ITEMS_JSX

    assert "isVisibleInHierarchy" in _COLLECT_ITEMS_JSX
    assert "@mcp:id=([^\\s@]+)" in _COLLECT_ITEMS_JSX
    assert "substring(idx + 8" not in _COLLECT_ITEMS_JSX
