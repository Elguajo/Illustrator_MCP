"""Selected-object grounding for annotated Illustrator previews.

This module deliberately owns no vision or object-search logic.  It joins an
annotated preview to the PageItem snapshot used to draw it, so a multimodal
client can turn a visible overlay label into a verified ``@mcp:id``.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any, Optional, Union

from mcp.types import ImageContent, TextContent
from pydantic import Field

from illustrator_mcp.errors import ErrorCode, make_envelope
from illustrator_mcp.proxy_client import execute_script_with_context
from illustrator_mcp.shared import mcp
from illustrator_mcp.tools.base import TOOL_ANNOTATIONS, ToolInputBase
from illustrator_mcp.tools.preview import (
    _annotate_preview,
    _capture_artboard,
    _validate_clip_box,
)

logger = logging.getLogger(__name__)

_GROUND_NAME = "illustrator_ground_object"


class GroundObjectInput(ToolInputBase):
    """Input for grounding an overlay label to one current PageItem."""

    label: int = Field(
        ...,
        ge=1,
        le=500,
        description=(
            "Numeric label from the most recent annotated preview. Labels are "
            "valid only while the document remains unchanged."
        ),
    )
    assign_id: bool = Field(
        default=False,
        description=(
            "Explicit opt-in: assign a new @mcp:id to the selected untagged "
            "item. Default false leaves the document unchanged. This tool never "
            "saves the document."
        ),
    )
    preview_max_items: int = Field(
        default=200,
        ge=1,
        le=500,
        description="Maximum visible PageItems to include in the returned annotated preview.",
    )
    preview_max_dim: int = Field(
        default=1024,
        ge=64,
        le=4096,
        description="Maximum width or height of the returned PNG preview in pixels.",
    )
    clip_box: Optional[list[float]] = Field(
        default=None,
        description=(
            "Optional [xmin, ymin, xmax, ymax] screen-space Y-down crop in "
            "artboard points. Labels and metadata retain global coordinates."
        ),
    )
    timeout: Optional[float] = Field(
        default=None,
        gt=0,
        le=300,
        description="Illustrator bridge timeout in seconds (default 30).",
    )


def _grounding_collect_script(max_items: int, clip_box: Optional[list[float]]) -> str:
    """Build one DOM read for overlay candidates plus stable PageItem metadata."""
    if clip_box is None:
        cull = "var cL = abL, cT = abT, cR = abR, cB = abB;"
    else:
        xmin, ymin, xmax, ymax = clip_box
        cull = (
            f"var cL = abL + {xmin}; var cT = abT - {ymin}; "
            f"var cR = abL + {xmax}; var cB = abT - {ymax};"
        )

    return f"""
(function() {{
    if (app.documents.length === 0) throw new Error("No active Illustrator document");
    var doc = app.activeDocument;
    var abIdx = doc.artboards.getActiveArtboardIndex();
    var ab = doc.artboards[abIdx].artboardRect;
    var abL = ab[0], abT = ab[1], abR = ab[2], abB = ab[3];
    {cull}
    var MAX = {max_items};
    var idCounts = {{}};
    var items = [];

    function mcpId(item) {{
        try {{ return extractMcpId(item.note || "") || ""; }} catch (e) {{ return ""; }}
    }}

    function isVisibleInHierarchy(item) {{
        var current = item;
        while (current) {{
            try {{ if (current.hidden) return false; }} catch (e) {{}}
            try {{
                if (current.typename === "Layer" && !current.visible) return false;
            }} catch (e) {{}}
            try {{ current = current.parent; }} catch (e) {{ break; }}
            if (current && current.typename === "Document") break;
        }}
        return true;
    }}

    function hierarchyState(item) {{
        var current = item;
        var state = {{
            hidden: false, locked: false, layer_hidden: false, layer_locked: false,
            inside_clipping_group: false, clipping_group: false, clipping_path: false
        }};
        while (current) {{
            try {{ if (current.hidden) state.hidden = true; }} catch (e) {{}}
            try {{ if (current.locked) state.locked = true; }} catch (e) {{}}
            try {{
                if (current.typename === "GroupItem" && current.clipped) {{
                    state.clipping_group = true;
                    if (current !== item) state.inside_clipping_group = true;
                }}
            }} catch (e) {{}}
            try {{
                if (current.typename === "Layer") {{
                    if (!current.visible) state.layer_hidden = true;
                    if (current.locked) state.layer_locked = true;
                }}
            }} catch (e) {{}}
            try {{ current = current.parent; }} catch (e) {{ break; }}
            if (current && current.typename === "Document") break;
        }}
        try {{ state.clipping_path = !!item.clipping; }} catch (e) {{}}
        // Reuse the standard target helper as the authoritative check for
        // descendants of clipped groups (the loop above also preserves the
        // richer distinction between the group and its child).
        try {{
            if (typeof isInsideClippingMask === "function" && isInsideClippingMask(item)) {{
                state.inside_clipping_group = true;
            }}
        }} catch (e) {{}}
        state.visible_in_preview = !(state.hidden || state.layer_hidden);
        state.editable = !(state.locked || state.layer_locked);
        return state;
    }}

    // Count IDs across the full document, including hidden and locked items.
    // A duplicate tag cannot be presented as a reliable stable reference.
    for (var ci = 0; ci < doc.pageItems.length; ci++) {{
        var countedId = mcpId(doc.pageItems[ci]);
        if (countedId) idCounts[countedId] = (idCounts[countedId] || 0) + 1;
    }}

    for (var i = 0; i < doc.pageItems.length && items.length < MAX; i++) {{
        var it = doc.pageItems[i];
        if (!isVisibleInHierarchy(it)) continue;
        try {{ if (it.guides) continue; }} catch (e) {{}}
        var visibleBounds, geometricBounds;
        try {{ visibleBounds = it.visibleBounds; }} catch (e) {{ continue; }}
        try {{ geometricBounds = it.geometricBounds; }} catch (e) {{ geometricBounds = null; }}
        if (visibleBounds[2] - visibleBounds[0] < 0.5 || visibleBounds[1] - visibleBounds[3] < 0.5) continue;
        if (visibleBounds[2] < cL || visibleBounds[0] > cR || visibleBounds[3] > cT || visibleBounds[1] < cB) continue;

        var id = mcpId(it);
        var state = hierarchyState(it);
        var ref = describeItemV2(it, {{includeIdentity: true, includeTags: true}});
        items.push({{
            _ground_source_index: i,
            name: it.name || it.typename,
            type: it.typename,
            bounds: [visibleBounds[0], visibleBounds[1], visibleBounds[2], visibleBounds[3]],
            mcp_id: id,
            mcp_id_occurrences: id ? (idCounts[id] || 0) : 0,
            item_ref: ref,
            state: state,
            geometric_bounds_ai: geometricBounds,
            visible_bounds_ai: [visibleBounds[0], visibleBounds[1], visibleBounds[2], visibleBounds[3]],
            bounds_screen: [
                visibleBounds[0] - abL,
                abT - visibleBounds[1],
                visibleBounds[2] - visibleBounds[0],
                visibleBounds[1] - visibleBounds[3]
            ]
        }});
    }}
    return JSON.stringify({{
        artboard: ab,
        artboard_index: abIdx,
        items: items,
        coordinate_system: "artboard screen space: origin top-left, x right, y down, points"
    }});
}})();
"""


def _assignment_script(source_index: int, expected_type: str, expected_bounds: list[float]) -> str:
    """Build a guarded, explicit single-item @mcp:id assignment script."""
    return f"""
(function() {{
    if (app.documents.length === 0) throw new Error("No active Illustrator document");
    var doc = app.activeDocument;
    var sourceIndex = {source_index};
    var expectedType = {json.dumps(expected_type)};
    var expectedBounds = {json.dumps(expected_bounds)};
    var item = doc.pageItems[sourceIndex];
    if (!item || item.typename !== expectedType) {{
        throw new Error("Grounded PageItem changed before ID assignment");
    }}
    var actualBounds;
    try {{ actualBounds = item.visibleBounds; }} catch (e) {{ throw new Error("Grounded PageItem bounds are unavailable"); }}
    for (var bi = 0; bi < 4; bi++) {{
        if (Math.abs(actualBounds[bi] - expectedBounds[bi]) > 0.01) {{
            throw new Error("Grounded PageItem changed before ID assignment");
        }}
    }}
    var currentId = extractMcpId(item.note || "");
    if (currentId) {{
        return JSON.stringify({{assigned: false, mcp_id: currentId, reason: "already_tagged"}});
    }}
    try {{ if (item.locked) throw new Error("Grounded PageItem is locked"); }} catch (e) {{ throw e; }}
    var parent = item.parent;
    while (parent && parent.typename !== "Document") {{
        try {{
            if (parent.typename === "Layer" && parent.locked) {{
                throw new Error("Grounded PageItem is on a locked layer");
            }}
        }} catch (e) {{ throw e; }}
        try {{ parent = parent.parent; }} catch (e) {{ break; }}
    }}

    function idExists(candidate) {{
        for (var i = 0; i < doc.pageItems.length; i++) {{
            try {{ if (extractMcpId(doc.pageItems[i].note || "") === candidate) return true; }} catch (e) {{}}
        }}
        return false;
    }}
    var candidate = "";
    do {{
        candidate = "mcp_" + (new Date().getTime()) + "_" + Math.floor(Math.random() * 1000000000);
    }} while (idExists(candidate));
    setMcpId(item, candidate);
    if (extractMcpId(item.note || "") !== candidate) {{
        throw new Error("Illustrator did not persist the assigned @mcp:id");
    }}
    return JSON.stringify({{assigned: true, mcp_id: candidate}});
}})();
"""


def _unwrap_snapshot(response: dict[str, Any]) -> dict[str, Any]:
    """Unwrap the CEP result variants used by preview collection."""
    raw = response.get("result")
    if not raw:
        raise ValueError("Item collection returned empty")
    snapshot: Any = json.loads(raw) if isinstance(raw, str) else raw
    for _ in range(2):
        if isinstance(snapshot, dict) and snapshot.get("ok") is True and "data" in snapshot:
            snapshot = snapshot["data"]
        elif isinstance(snapshot, dict) and snapshot.get("success") and "result" in snapshot:
            snapshot = snapshot["result"]
        else:
            break
    if isinstance(snapshot, str):
        snapshot = json.loads(snapshot)
    if not isinstance(snapshot, dict):
        raise ValueError("Invalid item collection result")
    return snapshot


def _selected_metadata(item: dict[str, Any], label: str, artboard: list[float], artboard_index: Any) -> dict[str, Any]:
    """Normalize the PageItem fields exposed by the grounding contract."""
    mcp_id = item.get("mcp_id") or None
    occurrences = int(item.get("mcp_id_occurrences") or 0)
    if not mcp_id:
        status = "missing"
    elif occurrences > 1:
        status = "duplicate"
    else:
        status = "existing"

    return {
        "label": label,
        "mcp_id": mcp_id,
        "stable_id": {"value": mcp_id, "status": status, "occurrences": occurrences},
        "item_ref": item.get("item_ref", {}),
        "type": item.get("type", "PageItem"),
        "name": item.get("name", ""),
        "bounds": {
            "visible_ai": item.get("visible_bounds_ai", item.get("bounds")),
            "geometric_ai": item.get("geometric_bounds_ai"),
            "screen": item.get("bounds_screen"),
            "units": "pt",
        },
        "artboard": {
            "index": artboard_index,
            "rect_ai": artboard,
            "screen_rect": [0, 0, abs(artboard[2] - artboard[0]), abs(artboard[1] - artboard[3])],
        },
        "state": item.get("state", {}),
    }


@mcp.tool(name=_GROUND_NAME, annotations=TOOL_ANNOTATIONS[_GROUND_NAME])
async def illustrator_ground_object(params: GroundObjectInput) -> Union[str, list]:
    """Ground one annotated-preview label to its current Illustrator PageItem.

    CONTRACT: readOnly=False, destructive=True, idempotent=False, openWorld=False

    WHEN TO USE:
      - A multimodal client identifies [N] in the latest annotated preview and needs a PageItem reference
      - Before a follow-up operation that must use a verified @mcp:id

    COORDINATE SYSTEM:
      - Returned screen coordinates use active-artboard top-left origin with y increasing downward
      - Returned *_ai bounds are Illustrator [left, top, right, bottom] coordinates
      - Units are points (1 pt = 1/72 inch)

    OPTIONS:
      label: numeric [N] from the latest annotated preview
      assign_id: explicit opt-in for an untagged selected PageItem; never saves the document
      clip_box: optional high-resolution screen-space crop

    NOTES:
      - Default assign_id=false does not alter the document. Untagged items return stable_id.status='missing'.
      - Duplicate @mcp:id tags are reported as stable_id.status='duplicate' and are not reliable follow-up targets.
      - Hidden items and items on hidden layers are excluded because they cannot be selected from the preview; locked items remain visible and are reported.
    """
    clip_box = _validate_clip_box(params.clip_box) if params.clip_box is not None else None
    timeout = params.timeout or 30.0
    label = str(params.label)
    warnings: list[str] = []

    try:
        response = await execute_script_with_context(
            script=_grounding_collect_script(params.preview_max_items, clip_box),
            command_type="ground_object_collect",
            tool_name=_GROUND_NAME,
            params={"label": params.label, "assign_id": params.assign_id},
            timeout=timeout,
            includes=["item_ref", "mcp_id", "targets"],
        )
        if response.get("error"):
            return make_envelope(
                ok=False,
                error={"code": ErrorCode.R_COLLECT_FAILED.value, "message": str(response["error"])},
                diagnostics={"tool": _GROUND_NAME},
            )
        snapshot = _unwrap_snapshot(response)
    except Exception as exc:
        logger.warning("Grounding collection failed: %s", exc)
        return make_envelope(
            ok=False,
            error={"code": ErrorCode.R_COLLECT_FAILED.value, "message": str(exc)},
            diagnostics={"tool": _GROUND_NAME},
        )

    # Export after the snapshot: no visual state is changed by collection or
    # by an optional note tag, and the overlay uses this exact metadata snapshot.
    image_bytes = await _capture_artboard(
        max_dim=params.preview_max_dim,
        timeout=timeout,
        fmt="png",
        clip_box=clip_box,
    )
    if not image_bytes:
        return make_envelope(
            ok=False,
            error={"code": ErrorCode.S_IO_ERROR.value, "message": "Could not export annotated preview"},
            diagnostics={"tool": _GROUND_NAME},
        )

    annotated_bytes, annotation_map = await _annotate_preview(
        img_bytes=image_bytes,
        max_items=params.preview_max_items,
        timeout=timeout,
        clip_box=clip_box,
        snapshot=snapshot,
    )

    items_by_source = {
        item.get("_ground_source_index"): item
        for item in snapshot.get("items", [])
        if "_ground_source_index" in item
    }
    selected_annotation = None
    for annotation in annotation_map.get("annotations", []):
        source_index = annotation.pop("_ground_source_index", None)
        if annotation.get("label") == label:
            selected_annotation = (annotation, items_by_source.get(source_index))

    label_to_mcp_id = {
        entry["label"]: entry.get("mcp_id")
        for entry in annotation_map.get("annotations", [])
    }
    common_result = {
        "label_to_mcp_id": label_to_mcp_id,
        "annotation_meta": annotation_map.get("meta", {}),
        "coordinate_system": snapshot.get("coordinate_system"),
    }

    if selected_annotation is None or selected_annotation[1] is None:
        envelope = make_envelope(
            ok=False,
            error={
                "code": ErrorCode.R_ELEMENT_NOT_FOUND.value,
                "message": f"Label [{label}] is not present in this annotated preview",
                "suggestions": ["Use a label from the returned annotation map", "Increase preview_max_items for dense artwork"],
            },
            diagnostics={"tool": _GROUND_NAME, **common_result},
        )
        return [
            TextContent(type="text", text=envelope),
            ImageContent(type="image", data=base64.b64encode(annotated_bytes).decode("ascii"), mimeType="image/png"),
            TextContent(type="text", text=json.dumps(annotation_map, indent=2)),
        ]

    annotation, selected_item = selected_annotation
    assigned = False
    assignment_error = None
    if params.assign_id and not selected_item.get("mcp_id"):
        try:
            assignment_response = await execute_script_with_context(
                script=_assignment_script(
                    int(selected_item["_ground_source_index"]),
                    str(selected_item.get("type", "")),
                    list(selected_item.get("visible_bounds_ai", selected_item["bounds"])),
                ),
                command_type="ground_object_assign_id",
                tool_name=_GROUND_NAME,
                params={"label": params.label, "assign_id": True},
                timeout=timeout,
                includes=["mcp_id"],
            )
            assignment = _unwrap_snapshot(assignment_response)
            assigned_id = assignment.get("mcp_id")
            if assigned_id:
                assigned = bool(assignment.get("assigned"))
                selected_item["mcp_id"] = assigned_id
                selected_item["mcp_id_occurrences"] = 1
                selected_item["item_ref"].setdefault("identity", {})["itemId"] = assigned_id
                selected_item["item_ref"]["identity"]["idSource"] = "note"
                annotation["mcp_id"] = assigned_id
                annotation["has_mcp_id"] = True
                label_to_mcp_id[label] = assigned_id
            else:
                assignment_error = "ID assignment returned no @mcp:id"
        except Exception as exc:
            assignment_error = str(exc)
            warnings.append(f"Explicit ID assignment failed: {assignment_error}")

    selected = _selected_metadata(
        selected_item,
        label,
        list(snapshot.get("artboard", [])),
        snapshot.get("artboard_index"),
    )
    if assigned:
        selected["stable_id"]["status"] = "assigned"
    elif selected["stable_id"]["status"] == "missing":
        warnings.append(
            "Selected PageItem has no stable @mcp:id. Set assign_id=true to opt in to tagging; no document change was made."
        )
    elif selected["stable_id"]["status"] == "duplicate":
        warnings.append("Selected PageItem's @mcp:id is duplicated elsewhere and is not a reliable target.")
    if assignment_error:
        selected["id_assignment_error"] = assignment_error

    result = {
        **common_result,
        "selected_object": selected,
        "id_assignment": {
            "requested": params.assign_id,
            "assigned": assigned,
            "document_saved": False,
            "error": assignment_error,
        },
    }
    envelope = make_envelope(
        ok=True,
        result=result,
        warnings=warnings,
        diagnostics={"tool": _GROUND_NAME, "preview_state": "current_document"},
    )
    return [
        TextContent(type="text", text=envelope),
        ImageContent(type="image", data=base64.b64encode(annotated_bytes).decode("ascii"), mimeType="image/png"),
        TextContent(type="text", text=json.dumps(annotation_map, indent=2)),
    ]
