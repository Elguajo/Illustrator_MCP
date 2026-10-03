"""
Typed text editing tool backed by ``doc_text.jsx``.

One tool, four actions, all addressed by native PageItem uuids from
``illustrator_inspect``: style-preserving find/replace, font replacement,
character-range and paragraph styling, convert to outlines. Creating text
stays with ``illustrator_execute_task`` (text_create); this tool edits
text that exists.
"""

from typing import List, Literal, Optional

from pydantic import Field, model_validator

from illustrator_mcp.shared import mcp
from illustrator_mcp.tools.base import ToolInputBase, TOOL_ANNOTATIONS
from illustrator_mcp.tools.doc_model_tools import run_dm

_INCLUDES = ["doc_model", "doc_text"]


class TextColor(ToolInputBase):
    """A fill color in exactly one model."""
    hex: Optional[str] = Field(None, pattern=r"^#?[0-9a-fA-F]{6}$", description="RGB as '#RRGGBB'")
    r: Optional[float] = Field(None, ge=0, le=255)
    g: Optional[float] = Field(None, ge=0, le=255)
    b: Optional[float] = Field(None, ge=0, le=255)
    c: Optional[float] = Field(None, ge=0, le=100)
    m: Optional[float] = Field(None, ge=0, le=100)
    y: Optional[float] = Field(None, ge=0, le=100)
    k: Optional[float] = Field(None, ge=0, le=100)
    gray: Optional[float] = Field(None, ge=0, le=100, description="Gray ink percentage")

    @model_validator(mode="after")
    def _one_model(self):
        rgb = [self.r, self.g, self.b]
        cmyk = [self.c, self.m, self.y, self.k]
        models = [
            self.hex is not None,
            any(v is not None for v in rgb),
            any(v is not None for v in cmyk),
            self.gray is not None,
        ]
        if sum(models) != 1:
            raise ValueError("color needs exactly one of: hex, r/g/b, c/m/y/k, gray")
        if models[1] and any(v is None for v in rgb):
            raise ValueError("r, g and b are all required")
        if models[2] and any(v is None for v in cmyk):
            raise ValueError("c, m, y and k are all required")
        return self


class ReplacePair(ToolInputBase):
    """One find/replace request inside a batch; applied in list order."""
    find: str = Field(..., min_length=1, description="literal text to find")
    replace: Optional[str] = Field(None, description="replacement text ('' deletes the match)")
    replace_runs: Optional[List[str]] = Field(
        None,
        description=(
            "one replacement per style run of the match, each written in that run's own "
            "style (for a match that spans bold and regular text, say). Use instead of replace."
        ),
    )
    case_sensitive: Optional[bool] = Field(None, description="overrides the call's case_sensitive")
    whole_word: Optional[bool] = Field(None, description="overrides the call's whole_word")

    @model_validator(mode="after")
    def _one_replacement(self):
        if (self.replace is None) == (self.replace_runs is None):
            raise ValueError("give exactly one of replace and replace_runs")
        if self.replace_runs is not None and not self.replace_runs:
            raise ValueError("replace_runs must not be empty")
        return self


class TextInput(ToolInputBase):
    """Input for text editing."""
    action: Literal["replace", "replace_font", "style", "outline"] = Field(
        ..., description="Text operation",
    )
    uuids: Optional[List[str]] = Field(
        None,
        description=(
            "TextFrame uuids from illustrator_inspect. Required for style/outline; "
            "replace/replace_font default to every text frame in the document."
        ),
    )
    document: Optional[str] = Field(
        None,
        description=(
            "result.document.name from the illustrator_inspect call that returned the uuids. "
            "Required with uuids: uuids are numbered per document, so the call is refused "
            "when another document is active."
        ),
    )
    find: Optional[str] = Field(None, min_length=1, description="replace: literal text to find")
    replace: Optional[str] = Field(None, description="replace: replacement text ('' deletes the match)")
    replace_runs: Optional[List[str]] = Field(
        None,
        description=(
            "replace: one replacement per style run of the match, each written in that run's own "
            "style. Use instead of replace for a match that spans differently styled text; "
            "dry_run lists the runs of every match."
        ),
    )
    replacements: Optional[List[ReplacePair]] = Field(
        None, min_length=1, max_length=500,
        description=(
            "replace: a batch of find/replace pairs applied in order in one call, each on the "
            "text the previous pair left. Use instead of find/replace."
        ),
    )
    dry_run: bool = Field(
        False,
        description="replace: change nothing; report every match, its style runs and whether it would be replaced",
    )
    case_sensitive: bool = Field(True, description="replace: match case")
    whole_word: bool = Field(False, description="replace: only matches not inside a word")
    from_font: Optional[str] = Field(
        None, description="replace_font: PostScript name in use, as illustrator_inspect reports it (missing fonts too)",
    )
    to_font: Optional[str] = Field(None, description="replace_font: installed PostScript name, e.g. 'MyriadPro-Regular'")
    start: Optional[int] = Field(None, ge=0, description="style: first character, 0-based within the frame (default 0)")
    length: Optional[int] = Field(None, ge=1, description="style: character count (default: to the end)")
    font: Optional[str] = Field(None, description="style: installed PostScript font name")
    size: Optional[float] = Field(None, gt=0, le=1296, description="style: font size in points")
    color: Optional[TextColor] = Field(None, description="style: fill color")
    tracking: Optional[int] = Field(None, ge=-1000, le=10000, description="style: tracking in 1/1000 em")
    space_before: Optional[float] = Field(None, ge=0, le=1296, description="style: paragraph space before, points")
    space_after: Optional[float] = Field(None, ge=0, le=1296, description="style: paragraph space after, points")

    @model_validator(mode="after")
    def _check_action_fields(self):
        if self.uuids and not self.document:
            raise ValueError("uuids require document (result.document.name from illustrator_inspect)")
        if self.dry_run and self.action != "replace":
            raise ValueError("dry_run applies to action='replace' only")
        if self.action == "replace":
            if self.replacements:
                if self.find is not None or self.replace is not None or self.replace_runs is not None:
                    raise ValueError("replacements replaces find/replace/replace_runs; give one or the other")
            else:
                if self.find is None:
                    raise ValueError("replace requires find with replace or replace_runs, or replacements")
                if (self.replace is None) == (self.replace_runs is None):
                    raise ValueError("replace requires exactly one of replace and replace_runs")
                if self.replace_runs is not None and not self.replace_runs:
                    raise ValueError("replace_runs must not be empty")
        elif self.action == "replace_font":
            if not self.from_font or not self.to_font:
                raise ValueError("replace_font requires from_font and to_font")
        elif self.action == "style":
            if not self.uuids:
                raise ValueError("style requires uuids")
            attrs = [self.font, self.size, self.color, self.tracking, self.space_before, self.space_after]
            if all(a is None for a in attrs):
                raise ValueError("style needs at least one of font, size, color, tracking, space_before, space_after")
        elif self.action == "outline" and not self.uuids:
            raise ValueError("outline requires uuids")
        return self


_TEXT_NAME = "illustrator_text"

_TEXT_CALLS = {
    "replace": "dtReplaceText(doc, P)",
    "replace_font": "dtReplaceFont(doc, P)",
    "style": "dtStyleRange(doc, P)",
    "outline": "dtOutline(doc, P)",
}


@mcp.tool(name=_TEXT_NAME, annotations=TOOL_ANNOTATIONS[_TEXT_NAME])
async def illustrator_text(params: TextInput) -> str:
    """Edit existing text by uuid: find/replace, replace fonts, style ranges, outline.

    CONTRACT: readOnly=False, destructive=True, idempotent=False, openWorld=False

    WHEN TO USE:
      - Change wording without losing formatting: action='replace'
      - Swap a missing or unwanted font everywhere: action='replace_font'
      - Restyle part of a text frame (font, size, color, tracking) or its
        paragraph spacing: action='style'
      - Freeze text as vector shapes before handoff: action='outline'
      Creating new text belongs to illustrator_execute_task (text_create);
      reading fonts, runs and overflow belongs to illustrator_inspect(view='details').

    KEY CONCEPTS:
      replace keeps each match's own character attributes. A match whose
      characters are styled differently is not changed; it is listed in
      skipped_occurrences with its style runs. Give replace_runs (one string
      per run, in order) to replace such a match, each run in its own style.
      replacements applies a batch of find/replace pairs in one call, in
      order; the result carries one entry per pair. dry_run changes nothing
      and returns every match with its runs and what would happen.
      Matching works per story, so threaded frames are searched as one text.
      A match is one string: \\n and \\r are a paragraph break, \\u0003 is a
      forced (soft) line break.
      replace_font changes only runs in from_font (use the name inspect
      reports; missing fonts are accepted) and leaves every other run alone.
      style ranges are 0-based character offsets within the frame;
      paragraph spacing applies to every paragraph the range touches.
      outline replaces each frame with a group of glyph paths and returns
      the group's uuid; the frame's name and note (@mcp:id) move to the group.

    MUTATION SAFETY:
      - uuids are numbered per document: pass document=result.document.name from
        the illustrator_inspect call; the call changes nothing if another document
        is active. Every result names its document.
      - Locked or hidden frames (also via their layer or group) are never
        modified; they are listed in skipped_objects. Unlock them first.
      - Every write is read back; a value that did not stick is reported in
        failed_objects with reason 'verify_failed'.
      - Results: success_count, fail_count, failed_objects[{uuid, reason}],
        skipped_objects. Undo with illustrator_history.

    EXAMPLES:
      illustrator_text(action="replace", uuids=["473"], document="poster.ai", find="2025", replace="2026")
      illustrator_text(action="replace", uuids=["473"], document="poster.ai", dry_run=True,
                       find="Our mission - to give")
      illustrator_text(action="replace", uuids=["473"], document="poster.ai",
                       find="Our mission - to give", replace_runs=["Our mission ", "is to give"])
      illustrator_text(action="replace", document="poster.ai",
                       replacements=[{"find": "Garage", "replace": "Gate"}, {"find": "Dacha", "replace": "Cottage"}])
      illustrator_text(action="replace_font", from_font="Helvetica", to_font="ArialMT")
      illustrator_text(action="style", uuids=["473"], document="poster.ai", start=6, length=3,
                       size=20, color={"hex": "#C80000"}, tracking=50)
      illustrator_text(action="style", uuids=["473"], document="poster.ai", space_after=12)
      illustrator_text(action="outline", uuids=["473", "481"], document="poster.ai")

    NOTES:
      - Font names are PostScript names (illustrator_inspect lists them per run)
      - In a CMYK document an RGB color is converted on assignment; the
        read-back value is reported as is
    """
    payload = params.model_dump(exclude_none=True)
    payload.pop("action", None)
    return await run_dm(
        _TEXT_CALLS[params.action],
        payload,
        command_type=f"text_{params.action}",
        tool_name=_TEXT_NAME,
        includes=_INCLUDES,
    )
