"""
Task Protocol Query Tools - Pilot refactor using new Task Protocol.

Demonstrates how to use the task protocol with declarative target selection
for more structured, observable, and debuggable operations.
"""

import json
from typing import Dict, Any, List, Literal, Optional
from pydantic import Field

import logging
from illustrator_mcp.shared import mcp
from illustrator_mcp.proxy_client import execute_script_with_context, format_envelope
from illustrator_mcp.libraries import get_injection_metadata
from illustrator_mcp.errors import ErrorCode, make_envelope
from illustrator_mcp.tools.base import ToolInputBase, TOOL_ANNOTATIONS
from illustrator_mcp.tools.doc_model_tools import dm_script, unwrap_dm_response
from illustrator_mcp.tools.task_execution import _taskreport_first_error
from illustrator_mcp.utils.response import unwrap_jsx_result

logger = logging.getLogger("illustrator_mcp")


class QueryItemsInput(ToolInputBase):
    """Input for querying items using declarative target selector.
    
    The targets parameter accepts a Task Protocol target selector dict:
    
    Target types:
    - {"type": "selection"} - Current selection (default)
    - {"type": "layer", "layer": "Layer 1"} - All items on a specific layer
    - {"type": "all"} - All items in document
    - {"type": "query", "itemType": "PathItem", "pattern": "rect_*"} - Filter by type/name
    
    Compound selectors (advanced):
    - {"type": "union", "selectors": [...]} - Union of multiple selectors
    - {"type": "intersection", "selectors": [...]} - Intersection of selectors
    
    Example payloads from living_test.md can be used directly.
    """

    targets: Dict[str, Any] = Field(
        default={"type": "selection"},
        description=(
            "Task Protocol target selector. Examples: "
            "{'type': 'selection'}, "
            "{'type': 'layer', 'layer': 'Layer 1'}, "
            "{'type': 'all'}, "
            "{'type': 'query', 'itemType': 'PathItem', 'pattern': 'rect_*'}"
        )
    )

    include_trace: bool = Field(
        default=False,
        description="Include execution trace in response"
    )

    debug: bool = Field(
        default=False,
        description="Return raw response for debugging"
    )


_QUERY_NAME = "illustrator_query_items"


@mcp.tool(name=_QUERY_NAME, annotations=TOOL_ANNOTATIONS[_QUERY_NAME])
async def illustrator_query_items(params: QueryItemsInput) -> str:
    """Query items using the Task Protocol with declarative target selection.

    CONTRACT: readOnly=True, destructive=False, idempotent=True, openWorld=False

    WHEN TO USE:
      - Finding items by type, name pattern, or location before modification
      - Inspecting current selection
      - Listing all items on a layer or in the document

    TARGET SELECTORS:
      {type: "selection"} — current selection (default)
      {type: "layer", layer: "Layer 1"} — all items on layer
      {type: "all", recursive: true} — all items in document
      {type: "query", itemType: "PathItem", pattern: "axis_*"} — filter by type/name

    NOTES:
      - Returns ItemRef for each matched item, enabling stable references
      - Set include_trace=True for debugging
    """
    
    # Use targets directly from input (already matches Task Protocol format)
    targets = params.targets
    
    # Build payload
    payload = {
        "task": "query_items",
        "targets": targets,
        "params": {},
        "options": {
            "dryRun": True,  # Read-only query
            "trace": params.include_trace
        }
    }
    
    payload_json = json.dumps(payload)
    
    script = f"""
// Pre-flight check: verify library functions are available
var _libraryCheck = {{
    executeTask: typeof executeTask,
    validatePayload: typeof validatePayload,
    collectTargets: typeof collectTargets,
    describeItemV2: typeof describeItemV2,
    makeError: typeof makeError
}};

// If any function is undefined, return diagnostic info
if (typeof executeTask !== "function" || typeof validatePayload !== "function") {{
    JSON.stringify({{
        ok: false,
        errors: [{{
            code: "LIB_NOT_LOADED",
            message: "task_pipeline.jsx library not properly loaded",
            stage: "preflight",
            details: _libraryCheck
        }}],
        stats: {{ itemsProcessed: 0 }},
        timing: {{ total_ms: 0 }}
    }});
}} else {{
    // Compute function - gather item info AND store in artifacts
    // (store here because apply is skipped in dryRun mode)
    function compute(items, params, report) {{
        var actions = [];
        report.artifacts = report.artifacts || {{}};
        report.artifacts.items = [];
        
        for (var i = 0; i < items.length; i++) {{
            var item = items[i];
            var itemRef = describeItemV2(item, {{includeIdentity: true, includeTags: true}});
            var itemData = {{
                itemRef: itemRef,
                name: item.name || "(unnamed)",
                type: item.typename,
                bounds: {{
                    left: item.left,
                    top: item.top,
                    width: item.width,
                    height: item.height
                }}
            }};
            actions.push(itemData);
            report.artifacts.items.push(itemData);
            report.stats.itemsProcessed++;
        }}
        return actions;
    }}

    // Apply function - no-op for query (results already stored in compute)
    function apply(actions, report) {{
        // No-op: items stored in compute stage for dryRun compatibility
    }}

    // Execute task
    var payload = {payload_json};
    var report = executeTask(payload, collectTargets, compute, apply);
    JSON.stringify(report);
}}
"""
    
    # Get canonicalized includes metadata for diagnostics
    meta = get_injection_metadata(["task_pipeline"])
    diagnostics = {
        "targets": params.targets,
        "includes": meta["includes_canonical"],
        "prelude_hash": meta["prelude_hash"]
    }

    response = await execute_script_with_context(
        script=script,
        command_type="query_items",
        tool_name="illustrator_query_items",
        params=params.model_dump(),
        includes=["task_pipeline"]
    )

    # Check for pipeline-level errors (connection, library injection, etc.)
    if response.get("error"):
        return format_envelope(response, context="query_items", diagnostics=diagnostics)

    # Debug mode: return raw response
    if params.debug:
        debug_output = {
            "raw_response": response,
            "script_length": len(script),
            "script_preview": script[:500] + "..." if len(script) > 500 else script
        }
        return make_envelope(
            ok=True,
            result=debug_output,
            diagnostics=diagnostics,
        )

    # Parse response and return standardized envelope
    try:
        result = response.get("result", "{}")
        if isinstance(result, str):
            report = json.loads(result)
        else:
            report = result

        # Extract warnings from report (if any)
        warnings = []
        for w in report.get("warnings", []):
            if isinstance(w, dict):
                warnings.append(w.get("message", str(w)))
            else:
                warnings.append(str(w))

        # ok is authoritative: reflect report status in envelope. Error
        # extraction is shared with execute_task's _taskreport_first_error —
        # it unwraps makeError()'s nested {ok, error:{...}} shape and, unlike
        # this tool's previous inline duplicate of that logic, actually
        # preserves `suggestions` (the duplicate silently dropped it; a
        # second "fallback" branch that did keep suggestions could never
        # run, because it only executed when `errors` was already empty).
        if report.get("ok", True) and not report.get("errors"):
            return make_envelope(
                ok=True,
                result=report,
                warnings=warnings,
                diagnostics=diagnostics,
            )

        err = _taskreport_first_error(report, "query_items")
        return make_envelope(
            ok=False,
            error=err,
            warnings=warnings,
            diagnostics={**diagnostics, "stats": report.get("stats", {})},
        )

    except json.JSONDecodeError as e:
        return make_envelope(
            ok=False,
            error={"code": ErrorCode.C_JSON_PARSE.value, "message": str(e)},
            diagnostics=diagnostics,
        )


# ==================== Preflight Check Tool ====================


PreflightScope = Literal["document", "objects", "text", "images", "links", "colors"]


class PreflightCheckInput(ToolInputBase):
    """Input for the scoped preflight report."""

    scopes: Optional[List[PreflightScope]] = Field(
        default=None,
        description=(
            "Check categories to run (default: all): document, objects, text, images, links, colors. "
            "Checks outside these scopes are listed in checks_skipped."
        ),
    )

    artboard_index: Optional[int] = Field(
        default=None,
        ge=0,
        description="Reference artboard for off-artboard checks (None = every artboard counts)",
    )

    bounds_type: str = Field(
        default="visible",
        description="Bounds type for validation: 'visible' (includes strokes/effects) or 'geometric' (path only)"
    )

    bounds_source: str = Field(
        default="group_visible",
        description="Bounds source: 'group_visible' (default) or 'clipping_path' (use clipping path bounds for clipped groups)"
    )

    policy: str = Field(
        default="fully-contained",
        description=(
            "'fully-contained' also reports objects crossing an artboard edge (objects.partially_off_artboard); "
            "'intersects' reports only objects entirely off the artboards"
        ),
    )

    scope: str = Field(
        default="document",
        description=(
            "Item filter (legacy name, not the check categories): 'document' (all items) or "
            "'artboard' (only items centred on artboard_index, default the active artboard)"
        ),
    )

    min_ppi: float = Field(default=300, gt=0, le=2400, description="images.low_ppi threshold (effective PPI at placed size)")
    hairline_width: float = Field(default=0.25, ge=0, le=10, description="objects.hairline_stroke threshold in points")
    total_ink_limit: float = Field(default=300, gt=0, le=400, description="colors.total_ink threshold (C+M+Y+K percent)")
    max_affected: int = Field(default=50, ge=1, le=1000, description="affected_objects listed per finding (count stays exact)")
    max_items: int = Field(default=20000, ge=1, le=500000, description="Objects to walk before checks become partial")
    max_text_chars: int = Field(default=200000, ge=1, le=5000000, description="Characters to scan for fonts/colors before text checks become partial")

    check_zero_size: bool = Field(
        default=True,
        description="Run objects.zero_size"
    )

    check_empty_text: bool = Field(
        default=True,
        description="Run text.empty_text"
    )

    check_locked: bool = Field(
        default=True,
        description="Run objects.locked (informational)"
    )


_PREFLIGHT_NAME = "illustrator_preflight_check"
_PREFLIGHT_INCLUDES = ["doc_model", "preflight"]


def _legacy_issues(findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """v1 'issues' view of v2 findings, kept for existing callers."""
    issues = []
    for f in findings:
        samples = []
        for obj in f.get("affected_objects", [])[:10]:
            samples.append(obj.get("name") or obj.get("uuid") or obj.get("layer_path") or obj.get("spot") or "")
        issue = {
            "type": f["tag"].split(".", 1)[-1],
            "tag": f["tag"],
            "count": f["count"],
            "message": f["message"],
            "samples": samples,
        }
        if f.get("severity") == "info":
            issue["severity"] = "info"
        issues.append(issue)
    return issues


@mcp.tool(name=_PREFLIGHT_NAME, annotations=TOOL_ANNOTATIONS[_PREFLIGHT_NAME])
async def illustrator_preflight_check(params: PreflightCheckInput) -> str:
    """Report print/export problems in the active document, grouped by issue tag.

    CONTRACT: readOnly=True, destructive=False, idempotent=True, openWorld=False

    WHEN TO USE:
      - Before export or handoff: missing fonts, overset text, missing links,
        low-resolution images, hairlines, off-artboard objects, risky colors
      - After a series of edits, to confirm nothing ended up off the artboard
      Fixing is done elsewhere: illustrator_text (fonts, text),
      illustrator_execute_task (objects), illustrator_place_file (links).

    KEY CONCEPTS:
      scopes picks the categories: document, objects, text, images, links, colors.
      Each finding has a tag ('text.missing_font'), severity (error/warning/info),
      an exact count, and affected_objects[{uuid, name, type, layer_path, ...facts}]
      capped at max_affected. Use the uuids with illustrator_inspect or illustrator_text.
      Coverage is explicit: checks_run lists checks that covered every object;
      checks_skipped lists the rest with a reason (scope_not_requested,
      disabled_by_parameter, partial, error). Treat a category as clean only
      when its checks are in checks_run. check_notes names known blind spots.
      Hidden objects are not checked (they are listed under objects.hidden).

    COORDINATE SYSTEM:
      - bounds are canvas-global points, Y-down: [left, top, right, bottom]

    EXAMPLES:
      illustrator_preflight_check()
      illustrator_preflight_check(scopes=["text", "links"])
      illustrator_preflight_check(scopes=["images"], min_ppi=150)
      illustrator_preflight_check(artboard_index=0, policy="intersects")

    NOTES:
      - ok=true means the check ran; findings arrive in result and as warnings
        (one per non-info finding)
      - result.issues is the v1 view of findings (type, count, message, samples)
      - Effective PPI = image pixels / placed size in inches
    """
    payload = {
        "scopes": params.scopes,
        "artboard_index": params.artboard_index,
        "bounds_type": params.bounds_type,
        "bounds_source": params.bounds_source,
        "policy": params.policy,
        "item_scope": params.scope,
        "min_ppi": params.min_ppi,
        "hairline_width": params.hairline_width,
        "total_ink_limit": params.total_ink_limit,
        "max_affected": params.max_affected,
        "max_items": params.max_items,
        "max_text_chars": params.max_text_chars,
        "check_zero_size": params.check_zero_size,
        "check_empty_text": params.check_empty_text,
        "check_locked": params.check_locked,
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    script = dm_script("pfRun(doc, P)", payload)

    preflight_meta = get_injection_metadata(_PREFLIGHT_INCLUDES)
    diagnostics = {
        "artboard_index": params.artboard_index,
        "scopes": params.scopes,
        "bounds_type": params.bounds_type,
        "bounds_source": params.bounds_source,
        "policy": params.policy,
        "scope": params.scope,
        "includes": preflight_meta["includes_canonical"],
        "prelude_hash": preflight_meta["prelude_hash"]
    }

    logger.info(f"preflight_check: scopes={params.scopes}, artboard={params.artboard_index}")

    try:
        response = await execute_script_with_context(
            script=script,
            command_type="preflight_check",
            tool_name="illustrator_preflight_check",
            params=params.model_dump(),
            includes=_PREFLIGHT_INCLUDES,
        )

        # Check for pipeline-level errors (connection, library injection, etc.)
        if response.get("error"):
            return format_envelope(response, context="preflight_check", diagnostics=diagnostics)

        # Unwrap the CEP envelope. host.jsx's executeScript() wraps a bare
        # return value as {ok: true, data: <value>} — NOT {success, result}.
        # This tool used to look for a "result" key inside that envelope,
        # which never exists there, so preflight_data was always {}: every
        # call silently reported ok=true with no checks, no issues, nothing.
        # unwrap_jsx_result is the shared, already-tested helper for this.
        preflight_data = unwrap_dm_response(response)
        if not isinstance(preflight_data, dict):
            preflight_data = unwrap_jsx_result(response, context="preflight_check")

        if isinstance(preflight_data, dict) and "__dm_request_error" in preflight_data:
            return make_envelope(
                ok=False,
                error={
                    "code": ErrorCode.V_INVALID_PARAM_VALUE.value,
                    "message": preflight_data["__dm_request_error"],
                    "suggestions": ["List valid artboards with illustrator_artboards(action='list')"],
                },
                diagnostics=diagnostics,
            )

        findings = preflight_data.get("findings", [])
        preflight_data["issues"] = _legacy_issues(findings)
        warnings = [f["message"] for f in findings if f.get("severity") != "info"]

        # This is a read-only diagnostic: ok reflects whether the check ran,
        # not whether the document is issue-free. Findings are surfaced via
        # `warnings` and in full via `result`. (ok=False would make
        # make_envelope drop `result` — its contract is `result if ok else None`.)
        return make_envelope(
            ok=True,
            result=preflight_data,
            warnings=warnings,
            diagnostics=diagnostics,
        )

    except Exception as e:
        logger.error(f"Preflight check failed: {str(e)}")
        return make_envelope(
            ok=False,
            error={"code": ErrorCode.R_PREFLIGHT_FAILED.value, "message": str(e)},
            diagnostics=diagnostics,
        )
