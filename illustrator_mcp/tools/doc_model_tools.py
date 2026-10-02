"""
Typed inspection and artboard tools backed by ``doc_model.jsx``.

These are the first typed tools of the hybrid toolset (see
docs/ADOBE_MCPTOOLKIT_STUDY.md): discoverable, schema-validated entry points
that return native PageItem uuids, which ``illustrator_execute_task`` then
accepts through the ``{"type": "uuid"}`` target selector.
"""

import json
from typing import List, Literal, Optional

from pydantic import Field, model_validator

from illustrator_mcp.errors import make_envelope
from illustrator_mcp.shared import mcp
from illustrator_mcp.tools.base import ToolInputBase, execute_jsx_tool, TOOL_ANNOTATIONS

_INCLUDES = ["doc_model"]

_NO_DOC_GUARD = (
    'if (app.documents.length === 0) '
    '{ throw new Error("No document is open. Create or open one with illustrator_document."); }'
)


_REQUEST_ERROR_KEY = "__dm_request_error"


def dm_script(call: str, payload: dict, *, needs_doc: bool = True) -> str:
    """Build the IIFE that runs one doc_model call.

    ``payload`` is embedded as a JSON literal, never interpolated into code.
    ``ensure_ascii`` keeps U+2028/U+2029 escaped: both are line terminators
    inside an ES3 string literal and would break the script.

    Request errors raised with dmFail() come back as a marked result so
    run_dm can report them as V011; anything else is rethrown untouched.
    """
    literal = json.dumps(payload, ensure_ascii=True)
    lines = ["(function () {", f"var P = {literal};"]
    if needs_doc:
        lines.append(_NO_DOC_GUARD)
        lines.append("var doc = app.activeDocument;")
    lines.append("try {")
    lines.append(f"return JSON.stringify({call});")
    lines.append("} catch (e) {")
    lines.append(
        f"if (e && e.dmUserError) {{ return JSON.stringify({{ {_REQUEST_ERROR_KEY}: String(e.message) }}); }}"
    )
    lines.append("throw e;")
    lines.append("}")
    lines.append("})()")
    return "\n".join(lines)


async def run_dm(
    call: str,
    payload: dict,
    *,
    command_type: str,
    tool_name: str,
    needs_doc: bool = True,
    log_params: Optional[dict] = None,
) -> str:
    """Execute one doc_model call and map dmFail() request errors to V011."""
    raw = await execute_jsx_tool(
        script=dm_script(call, payload, needs_doc=needs_doc),
        command_type=command_type,
        tool_name=tool_name,
        params=log_params if log_params is not None else payload,
        includes=_INCLUDES,
    )
    try:
        env = json.loads(raw)
    except (TypeError, ValueError):
        return raw
    result = env.get("result") if isinstance(env, dict) else None
    if env.get("ok") and isinstance(result, dict) and _REQUEST_ERROR_KEY in result:
        return make_envelope(
            ok=False,
            error={
                "code": "V011",
                "message": result[_REQUEST_ERROR_KEY],
                "suggestions": [
                    "This is a request error, not a script error: fix the value named in the message",
                    "List valid targets with illustrator_artboards(action='list') or illustrator_inspect",
                ],
            },
            warnings=env.get("warnings") or [],
            diagnostics=env.get("diagnostics"),
        )
    return raw


# ==================== illustrator_inspect ====================

class InspectInput(ToolInputBase):
    """Input for progressive document inspection."""
    view: Literal["structure", "artboard", "selection", "details"] = Field(
        "structure",
        description=(
            "'structure': layer/group tree. 'artboard': everything overlapping one artboard, "
            "across layers. 'selection': current selection. 'details': appearance, text and "
            "geometry for specific uuids."
        ),
    )
    uuids: Optional[List[str]] = Field(
        None,
        description="PageItem uuids. Start nodes for 'structure'; required for 'details'.",
    )
    layers: Optional[List[str]] = Field(
        None,
        description="Layer names or 'Parent/Child' paths to start 'structure' from.",
    )
    max_depth: int = Field(
        1, ge=-1, le=50,
        description="'structure' depth: 0 = start nodes only, 1 = plus children, -1 = unlimited.",
    )
    max_nodes: int = Field(300, ge=1, le=5000, description="Response budget in nodes.")
    artboard_index: Optional[int] = Field(
        None, ge=0, description="0-based artboard for view='artboard' (default: active).",
    )
    artboard_name: Optional[str] = Field(None, description="Artboard name for view='artboard'.")
    aspects: Optional[List[Literal["appearance", "text", "geometry"]]] = Field(
        None, description="'details' aspects to return (default: all three).",
    )

    @model_validator(mode="after")
    def _details_need_uuids(self):
        if self.view == "details" and not self.uuids:
            raise ValueError("view='details' requires uuids")
        return self


_INSPECT_NAME = "illustrator_inspect"

_VIEW_CALLS = {
    "structure": "dmStructure(doc, P)",
    "artboard": "dmArtboardView(doc, P)",
    "selection": "dmSelectionView(doc, P)",
    "details": "dmDetails(doc, P)",
}


@mcp.tool(name=_INSPECT_NAME, annotations=TOOL_ANNOTATIONS[_INSPECT_NAME])
async def illustrator_inspect(params: InspectInput) -> str:
    """Inspect the document progressively and get native uuids for follow-up calls.

    CONTRACT: readOnly=True, destructive=False, idempotent=True, openWorld=False

    WHEN TO USE:
      - Before editing: find what exists and get the uuid of each object
      - Drill into a group or layer without dumping the whole document
      - Read exact colors, fonts (including missing ones) and anchor counts

    KEY CONCEPTS:
      Start broad, then narrow: view='structure' (or 'artboard') returns basic
      nodes; containers beyond max_depth show child_count/child_types instead of
      children. Pass their uuids back as start nodes to go deeper. Use
      view='details' only for the few objects you need to read closely.
      Every PageItem node carries 'uuid' (session-scoped) and 'mcp_id' when one
      was assigned (survives save and reopen). Layers have no uuid; they are
      identified by 'layer_path'. uuids are numbered per document and collide
      across open documents, so every result names its 'document' {name, path}.

    COORDINATE SYSTEM:
      - bounds are canvas-global points, Y-down: [left, top, right, bottom]
      - Artboard bounds use the same space, so artboard-relative x = bounds[0] - artboard.bounds[0]

    EXAMPLES:
      illustrator_inspect(view="structure")
      illustrator_inspect(view="structure", uuids=["412"], max_depth=2)
      illustrator_inspect(view="artboard", artboard_index=1)
      illustrator_inspect(view="details", uuids=["412", "415"], aspects=["text"])

    NOTES:
      - Size-capped results set truncated=true and a resume_hint naming where to continue
      - Unknown uuids are listed in failed_objects instead of failing the call
      - Act on the returned uuids with illustrator_execute_task targets
        {"type": "uuid", "uuids": [...], "document": result.document.name}; the task
        fails instead of acting if another document has become active
    """
    payload = params.model_dump(exclude_none=True)
    return await run_dm(
        f"dmWithDocument(doc, {_VIEW_CALLS[params.view]})",
        payload,
        command_type=f"inspect_{params.view}",
        tool_name=_INSPECT_NAME,
    )


# ==================== illustrator_artboards ====================

# Canonical portrait (or native) size in points. 1 px = 1 pt at the 72-PPI baseline.
ARTBOARD_PRESETS: dict[str, tuple[float, float]] = {
    "A3": (841.89, 1190.55),
    "A4": (595.28, 841.89),
    "A5": (419.53, 595.28),
    "Letter": (612.0, 792.0),
    "Legal": (612.0, 1008.0),
    "Tabloid": (792.0, 1224.0),
    "Business Card US": (252.0, 144.0),
    "Business Card EU": (240.94, 155.91),
    "Instagram Post": (1080.0, 1080.0),
    "Instagram Portrait": (1080.0, 1350.0),
    "Instagram Story": (1080.0, 1920.0),
    "X Post": (1600.0, 900.0),
    "YouTube Thumbnail": (1280.0, 720.0),
    "HD 720p": (1280.0, 720.0),
    "HD 1080p": (1920.0, 1080.0),
    "4K UHD": (3840.0, 2160.0),
}

_PRESET_LOOKUP = {name.lower(): name for name in ARTBOARD_PRESETS}


def resolve_preset(name: str, orientation: Optional[str]) -> tuple[float, float]:
    """Return (width, height) for a preset, optionally forced to an orientation."""
    key = _PRESET_LOOKUP.get(name.strip().lower())
    if key is None:
        raise ValueError(
            f"Unknown preset '{name}'. Known presets: {', '.join(ARTBOARD_PRESETS)}"
        )
    w, h = ARTBOARD_PRESETS[key]
    if orientation == "portrait" and w > h or orientation == "landscape" and h > w:
        w, h = h, w
    return w, h


_ANCHORS = Literal[
    "top-left", "top-center", "top-right",
    "center-left", "center", "center-right",
    "bottom-left", "bottom-center", "bottom-right",
]


class ArtboardsInput(ToolInputBase):
    """Input for artboard operations."""
    action: Literal["list", "presets", "create", "update", "delete", "activate", "fit"] = Field(
        ..., description="Artboard operation",
    )
    artboard_index: Optional[int] = Field(
        None, ge=0, description="0-based target artboard (update/delete/activate/fit; default: active)",
    )
    artboard_name: Optional[str] = Field(None, description="Target artboard by name instead of index")
    name: Optional[str] = Field(None, max_length=255, description="Name for a created artboard")
    new_name: Optional[str] = Field(None, max_length=255, description="Rename the target (update)")
    width: Optional[float] = Field(None, gt=0, le=16383, description="Width in points (create/update)")
    height: Optional[float] = Field(None, gt=0, le=16383, description="Height in points (create/update)")
    preset: Optional[str] = Field(
        None, description="Named size, e.g. 'A4', 'Instagram Story' (create/update); see action='presets'",
    )
    orientation: Optional[Literal["portrait", "landscape"]] = Field(
        None, description="Force preset orientation (default: create uses the preset's own; update keeps the artboard's)",
    )
    anchor: _ANCHORS = Field("center", description="Point kept fixed when update resizes")
    left: Optional[float] = Field(None, description="Left edge, canvas Y-down points (create/update)")
    top: Optional[float] = Field(None, description="Top edge, canvas Y-down points (create/update)")
    bounds: Optional[List[float]] = Field(
        None, min_length=4, max_length=4,
        description="Exact [left, top, right, bottom], canvas Y-down points (update)",
    )
    activate: bool = Field(False, description="Make a created artboard active")
    scope: Literal["artboard", "all", "selection", "uuids"] = Field(
        "artboard", description="fit: artwork to enclose",
    )
    uuids: Optional[List[str]] = Field(None, description="fit with scope='uuids'")
    padding: float = Field(0, ge=0, le=1000, description="fit: margin around the artwork in points")

    @model_validator(mode="after")
    def _check_action_fields(self):
        if self.preset and (self.width is not None or self.height is not None):
            raise ValueError("Pass either preset or width/height, not both")
        if self.action == "create" and not self.preset and (self.width is None or self.height is None):
            raise ValueError("create requires width and height, or a preset")
        if self.action == "fit" and self.scope == "uuids" and not self.uuids:
            raise ValueError("fit with scope='uuids' requires uuids")
        return self


_ARTBOARDS_NAME = "illustrator_artboards"

_ARTBOARD_CALLS = {
    "list": "dmListArtboards(doc)",
    "create": "dmArtboardCreate(doc, P)",
    "update": "dmArtboardUpdate(doc, P)",
    "delete": "dmArtboardDelete(doc, P)",
    "activate": "dmArtboardActivate(doc, P)",
    "fit": "dmArtboardFit(doc, P)",
}


@mcp.tool(name=_ARTBOARDS_NAME, annotations=TOOL_ANNOTATIONS[_ARTBOARDS_NAME])
async def illustrator_artboards(params: ArtboardsInput) -> str:
    """List, create, resize, rename, delete, activate or fit artboards.

    CONTRACT: readOnly=False, destructive=True, idempotent=False, openWorld=False

    WHEN TO USE:
      - A new canvas size (poster, social post, print format): add an artboard
        rather than a new document, so variants stay together
      - Resizing to a named format: preset='A4' instead of hand-typed sizes
      - Cropping an artboard to its artwork: action='fit'

    KEY CONCEPTS:
      Artboards are addressed by 0-based artboard_index or artboard_name, default
      the active one. update resizes around 'anchor' (default center) and
      moves with left/top; bounds sets the exact rectangle.
      Artboard edits never move artwork, and delete keeps the artwork that was on it.

    COORDINATE SYSTEM:
      - Canvas-global points, Y-down; bounds are [left, top, right, bottom]
      - create without left/top places the artboard 20pt right of the right-most one

    EXAMPLES:
      illustrator_artboards(action="list")
      illustrator_artboards(action="create", preset="Instagram Story", name="story")
      illustrator_artboards(action="update", artboard_index=0, preset="A4", orientation="landscape")
      illustrator_artboards(action="update", artboard_name="cover", new_name="cover-v2")
      illustrator_artboards(action="fit", scope="uuids", uuids=["412"], padding=20)

    NOTES:
      - delete refuses to remove the last artboard; indices after it shift down by one
      - fit computes bounds directly and leaves the selection untouched
    """
    if params.action == "presets":
        presets = [
            {"name": name, "width": w, "height": h}
            for name, (w, h) in ARTBOARD_PRESETS.items()
        ]
        return make_envelope(
            ok=True,
            result={"unit": "pt", "presets": presets},
            diagnostics={"tool": _ARTBOARDS_NAME, "command": "artboards_presets"},
        )

    payload = params.model_dump(exclude_none=True)
    if params.preset:
        orientation = params.orientation
        if params.action == "update" and orientation is None:
            # Keep the target's current orientation, resolved in JSX.
            payload["keep_orientation"] = True
        w, h = resolve_preset(params.preset, orientation)
        payload["width"], payload["height"] = w, h

    call = _ARTBOARD_CALLS[params.action]
    if payload.get("keep_orientation"):
        call = "dmArtboardUpdate(doc, dmMatchOrientation(doc, P))"
    return await run_dm(
        call,
        payload,
        command_type=f"artboards_{params.action}",
        tool_name=_ARTBOARDS_NAME,
    )
