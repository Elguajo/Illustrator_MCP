"""
Typed effects and swatch tools backed by ``effects.jsx`` and ``swatches.jsx``.

Both follow the doc_model pattern (see doc_model_tools.py): a validated
Pydantic input, a JSON payload embedded by dm_script, and dmFail() request
errors reported as V011. Effects address objects by native PageItem uuid
from illustrator_inspect.
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from illustrator_mcp.shared import mcp
from illustrator_mcp.tools.base import ToolInputBase, TOOL_ANNOTATIONS
from illustrator_mcp.tools.doc_model_tools import run_dm


class ColorSpec(BaseModel):
    """One color: exactly one of hex, rgb or cmyk."""
    model_config = ConfigDict(str_strip_whitespace=True)

    hex: Optional[str] = Field(
        None, pattern=r"^#?[0-9A-Fa-f]{6}$", description="'#RRGGBB'",
    )
    rgb: Optional[List[float]] = Field(
        None, min_length=3, max_length=3, description="[r, g, b], each 0-255",
    )
    cmyk: Optional[List[float]] = Field(
        None, min_length=4, max_length=4, description="[c, m, y, k], each 0-100",
    )

    @model_validator(mode="after")
    def _exactly_one(self):
        given = [k for k in ("hex", "rgb", "cmyk") if getattr(self, k) is not None]
        if len(given) != 1:
            raise ValueError("Give exactly one of hex, rgb or cmyk")
        if self.rgb is not None and not all(0 <= v <= 255 for v in self.rgb):
            raise ValueError("rgb values must be within 0-255")
        if self.cmyk is not None and not all(0 <= v <= 100 for v in self.cmyk):
            raise ValueError("cmyk values must be within 0-100")
        return self

    def to_payload(self) -> dict:
        if self.cmyk is not None:
            c, m, y, k = self.cmyk
            return {"model": "cmyk", "c": c, "m": m, "y": y, "k": k}
        if self.hex is not None:
            h = self.hex.lstrip("#")
            r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        else:
            r, g, b = self.rgb
        return {"model": "rgb", "r": r, "g": g, "b": b}


# ==================== illustrator_effects ====================

_SHADOW_FIELDS = ("offset_x", "offset_y", "blur", "opacity", "blend_mode", "color", "darkness")
_EFFECT_FIELDS = _SHADOW_FIELDS + ("radius", "effect", "replace")


class EffectsInput(ToolInputBase):
    """Input for live effects on items addressed by uuid."""
    action: Literal["apply", "remove"] = Field(
        ..., description="'apply' adds one effect; 'remove' clears every live effect",
    )
    uuids: List[str] = Field(
        ..., min_length=1, max_length=500,
        description="PageItem uuids from illustrator_inspect",
    )
    document: Optional[str] = Field(
        None,
        description="Name of the document the uuids came from (illustrator_inspect result.document.name); "
                    "the call fails instead of acting if another document is active",
    )
    effect: Optional[Literal["drop_shadow", "gaussian_blur"]] = Field(
        None, description="Effect to apply (required for apply)",
    )
    offset_x: float = Field(7, ge=-1000, le=1000, description="drop_shadow: X offset in points, + = right")
    offset_y: float = Field(7, ge=-1000, le=1000, description="drop_shadow: Y offset in points, + = down")
    blur: float = Field(5, ge=0, le=144, description="drop_shadow: blur in points")
    opacity: float = Field(75, ge=0, le=100, description="drop_shadow: opacity in percent")
    blend_mode: Literal["normal", "multiply", "screen"] = Field(
        "multiply", description="drop_shadow: blend mode",
    )
    color: Optional[ColorSpec] = Field(
        None, description="drop_shadow: shadow color (default black)",
    )
    darkness: Optional[float] = Field(
        None, ge=0, le=100,
        description="drop_shadow: darkness mode instead of a color (percent of black mixed into the object's own color)",
    )
    radius: float = Field(5, gt=0, le=250, description="gaussian_blur: radius in points")
    replace: bool = Field(
        False, description="apply: clear existing effects first instead of stacking",
    )
    allow_stroke_realign: bool = Field(
        False,
        description="remove/replace: also process stroked paths, accepting that their stroke may be re-aligned to the inside",
    )

    @model_validator(mode="after")
    def _check_action_fields(self):
        given = self.model_fields_set
        if self.action == "apply" and self.effect is None:
            raise ValueError("apply requires effect ('drop_shadow' or 'gaussian_blur')")
        if self.action == "remove":
            extra = sorted(given & set(_EFFECT_FIELDS))
            if extra:
                raise ValueError(f"remove takes only uuids, document and allow_stroke_realign, not {', '.join(extra)}")
        if self.effect == "gaussian_blur":
            extra = sorted(given & set(_SHADOW_FIELDS))
            if extra:
                raise ValueError(f"gaussian_blur takes radius, not {', '.join(extra)}")
        if self.effect == "drop_shadow" and "radius" in given:
            raise ValueError("drop_shadow takes blur, not radius")
        if self.color is not None and self.darkness is not None:
            raise ValueError("Pass either color or darkness, not both")
        return self


def effects_payload(params: EffectsInput) -> dict:
    payload: dict = {
        "action": params.action,
        "uuids": params.uuids,
        "allow_stroke_realign": params.allow_stroke_realign,
    }
    if params.document:
        payload["document"] = params.document
    if params.action == "apply":
        payload["effect"] = params.effect
        payload["replace"] = params.replace
        if params.effect == "drop_shadow":
            shadow = {
                "offset_x": params.offset_x,
                "offset_y": params.offset_y,
                "blur": params.blur,
                "opacity": params.opacity,
                "blend_mode": params.blend_mode,
            }
            if params.darkness is not None:
                shadow["darkness"] = params.darkness
            else:
                shadow["color"] = (params.color.to_payload() if params.color
                                   else {"model": "rgb", "r": 0, "g": 0, "b": 0})
            payload["drop_shadow"] = shadow
        else:
            payload["gaussian_blur"] = {"radius": params.radius}
    return payload


_EFFECTS_NAME = "illustrator_effects"

_EFFECTS_SUGGESTIONS = [
    "This is a request error, not a script error: fix the value named in the message",
    "Get uuids with illustrator_inspect(view='structure') or view='selection'",
]


@mcp.tool(name=_EFFECTS_NAME, annotations=TOOL_ANNOTATIONS[_EFFECTS_NAME])
async def illustrator_effects(params: EffectsInput) -> str:
    """Apply a live drop shadow or Gaussian blur, or remove live effects, by uuid.

    CONTRACT: readOnly=False, destructive=True, idempotent=False, openWorld=False

    WHEN TO USE:
      - Add a soft shadow or blur that stays editable in the Appearance panel
      - Strip effects from objects before restyling them
      - Not for fill, stroke, opacity or gradients: use illustrator_execute_task style ops

    KEY CONCEPTS:
      Effects are live: the path geometry is unchanged and the effect sits on
      the object's appearance. uuids are valid for the active document only
      (numbers collide across open documents): pass document=result.document.name
      from illustrator_inspect and the call fails rather than touching another
      document's objects. apply stacks on top of existing effects unless
      replace=true. Compound paths get the effect on the compound itself,
      groups on the group (children untouched), text on the text object.
      Reading effects back is not possible from script: no DOM property
      exposes them. Every result therefore reports visible_bounds_before and
      visible_bounds_after (canvas Y-down); a shadow or blur always enlarges
      the visible bounds, and an apply that leaves them unchanged is reported
      as failed. Use illustrator_inspect for the rest of the appearance.

    MUTATION SAFETY:
      - Locked or hidden objects, and objects inside a locked or hidden group or
        layer, are never touched: they come back in skipped_objects
      - remove (and replace) re-apply the document's [Default] graphic style,
        then restore and read back fill, stroke color/width/dashes/cap/join,
        opacity, blend mode, isolation and knockout. Extra fills or strokes
        added in the Appearance panel are removed too.
      - Stroke alignment cannot be scripted and [Default] re-aligns strokes to
        the inside, so stroked paths are skipped unless allow_stroke_realign=true
      - Results: success_count, fail_count, failed_objects[{uuid, reason}], skipped_objects

    COORDINATE SYSTEM:
      - visible bounds are canvas-global points, Y-down: [left, top, right, bottom]
      - offset_y > 0 moves the shadow down, offset_x > 0 moves it right

    EXAMPLES:
      illustrator_effects(action="apply", effect="drop_shadow", uuids=["412"], document="poster.ai")
      illustrator_effects(action="apply", effect="drop_shadow", uuids=["412"], offset_x=0, offset_y=4, blur=8, opacity=30, color={"hex": "#1a1a2e"})
      illustrator_effects(action="apply", effect="gaussian_blur", uuids=["415"], radius=3, replace=True)
      illustrator_effects(action="remove", uuids=["412", "415"])

    NOTES:
      - drop_shadow defaults match Illustrator's dialog: 7/7 pt offset, 5 pt blur, 75% opacity, multiply, black
      - darkness=N uses the object's own color darkened by N% instead of a shadow color
    """
    return await run_dm(
        "fxRun(doc, P)",
        effects_payload(params),
        command_type=f"effects_{params.action}",
        tool_name=_EFFECTS_NAME,
        includes=["effects"],
        suggestions=_EFFECTS_SUGGESTIONS,
    )


# ==================== illustrator_swatches ====================

class SwatchSpec(BaseModel):
    """A swatch to create."""
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(..., min_length=1, max_length=255, description="Exact swatch name")
    kind: Literal["process", "spot", "global"] = Field(
        "process", description="'process' plain color, 'spot' spot color, 'global' global process color",
    )
    color: ColorSpec

    @model_validator(mode="after")
    def _not_reserved(self):
        if self.name.startswith("["):
            raise ValueError("Swatch names starting with '[' are reserved by Illustrator ([None], [Registration])")
        return self


_SWATCH_ACTIONS = Literal[
    "list", "get", "create", "create_group", "delete", "delete_group", "libraries", "library",
]


class SwatchesInput(ToolInputBase):
    """Input for swatch, swatch group and swatch library operations."""
    action: _SWATCH_ACTIONS = Field(..., description="Swatch operation")
    names: Optional[List[str]] = Field(
        None, min_length=1, max_length=500,
        description="Exact swatch names (get, delete; optional filter for library)",
    )
    swatches: Optional[List[SwatchSpec]] = Field(
        None, min_length=1, max_length=200, description="Swatches to create (create)",
    )
    group: Optional[str] = Field(
        None, max_length=255,
        description="Swatch group name: target of create/create_group/delete_group; filter for list ('' = ungrouped)",
    )
    move_swatches: Optional[List[str]] = Field(
        None, max_length=500, description="create_group: existing swatches to move into the new group",
    )
    keep_swatches: bool = Field(
        True, description="delete_group: keep the group's swatches (moved to the ungrouped list)",
    )
    library: Optional[str] = Field(
        None, description="Exact library name from action='libraries' (library)",
    )
    offset: int = Field(0, ge=0, description="list/library: skip this many swatches (from resume_hint)")
    max_swatches: int = Field(500, ge=1, le=5000, description="list/library: response budget")

    @model_validator(mode="after")
    def _check_action_fields(self):
        a = self.action
        if a in ("get", "delete") and not self.names:
            raise ValueError(f"{a} requires names")
        if a == "create" and not self.swatches:
            raise ValueError("create requires swatches")
        if a in ("create_group", "delete_group") and not self.group:
            raise ValueError(f"{a} requires group")
        if a == "library" and not self.library:
            raise ValueError("library requires library (see action='libraries')")
        return self


_SWATCHES_NAME = "illustrator_swatches"

_SWATCH_CALLS = {
    "list": "swList(doc, P)",
    "get": "swGet(doc, P)",
    "create": "swCreate(doc, P)",
    "create_group": "swCreateGroup(doc, P)",
    "delete": "swDelete(doc, P)",
    "delete_group": "swDeleteGroup(doc, P)",
    "libraries": "swLibraries()",
    "library": "swLibrary(P)",
}

_NO_DOC_ACTIONS = {"libraries", "library"}

_SWATCH_SUGGESTIONS = [
    "This is a request error, not a script error: fix the value named in the message",
    "List what exists with illustrator_swatches(action='list') or action='libraries'",
]


@mcp.tool(name=_SWATCHES_NAME, annotations=TOOL_ANNOTATIONS[_SWATCHES_NAME])
async def illustrator_swatches(params: SwatchesInput) -> str:
    """List, look up, create and delete swatches and swatch groups; read swatch libraries.

    CONTRACT: readOnly=False, destructive=True, idempotent=False, openWorld=True

    WHEN TO USE:
      - Find the exact swatch name before applying a brand color
      - Build a palette: create process, spot or global swatches in a group
      - Pull colors from a swatch library that ships with Illustrator (Web, Corporate, ...)
      - Not for painting objects: apply the color with illustrator_execute_task style ops

    KEY CONCEPTS:
      Names are matched exactly and case-sensitively, never guessed. A miss
      lands in failed_objects with similar_existing: names that really exist
      and look close, for you to choose from. kind is process | spot | global
      (global = a global process color) | gradient | pattern | registration | none.
      Colors come back as {model: rgb|cmyk|gray|spot|gradient|pattern, ...}.
      Ungrouped swatches have group=null.

    DECISION RULES:
      - Use spot for colors that print as their own ink, global for process
        colors you want to edit everywhere at once, process otherwise
      - A swatch library is read-only here: read a color, then create it in the document

    MUTATION SAFETY:
      - create refuses an existing name (Illustrator itself would accept a
        duplicate) and reads every swatch back; a mismatch is removed and reported
      - Colors are stored in the document color mode: a CMYK color in an RGB
        document comes back as RGB and the entry says converted_to
      - delete removes every swatch with that exact name; [None] and
        [Registration] are skipped. Objects using a deleted spot or global
        swatch keep their look as a plain process color
      - delete_group keeps its swatches by default (keep_swatches=true moves
        them to the ungrouped list); keep_swatches=false deletes them with it
      - Batch results: success_count, fail_count, failed_objects[{name, reason}]

    OPTIONS:
      - libraries: every library file with name, format (ai/ase/acb/acbl), readable
      - library: .ai libraries are opened briefly as a document and closed again
        (alerts suppressed, your active document restored); .ase files are parsed
        directly; .acb/.acbl color books cannot be read by scripts
      - list/library pages with offset/max_swatches; truncated=true comes with resume_hint.offset

    EXAMPLES:
      illustrator_swatches(action="list")
      illustrator_swatches(action="get", names=["Brand Red"])
      illustrator_swatches(action="create_group", group="Brand")
      illustrator_swatches(action="create", group="Brand", swatches=[{"name": "Brand Red", "kind": "global", "color": {"hex": "#d62828"}}])
      illustrator_swatches(action="delete_group", group="Brand", keep_swatches=False)
      illustrator_swatches(action="libraries")
      illustrator_swatches(action="library", library="Corporate", names=["C=93 M=35 Y=0 K=15"])
    """
    payload = params.model_dump(exclude_none=True, exclude={"swatches"})
    if params.swatches:
        payload["swatches"] = [
            {"name": s.name, "kind": s.kind, "color": s.color.to_payload()} for s in params.swatches
        ]
    return await run_dm(
        _SWATCH_CALLS[params.action],
        payload,
        command_type=f"swatches_{params.action}",
        tool_name=_SWATCHES_NAME,
        needs_doc=params.action not in _NO_DOC_ACTIONS,
        includes=["swatches"],
        suggestions=_SWATCH_SUGGESTIONS,
    )
