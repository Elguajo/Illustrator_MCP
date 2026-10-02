"""doc_model.jsx, native-uuid resolution, and the typed tools built on them.

The JSX runs in Node against a small Illustrator-shaped DOM fixture. Two
fixture behaviours mirror what Illustrator 30.8.1 actually does (probed live,
see resolvePageItemByUuid in mcp_id.jsx):

  - getPageItemFromUuid() throws on an unknown uuid;
  - for a CompoundPathItem it returns a *different* object typed GroupItem
    with the same uuid and parent.

Coordinates in the fixture are Illustrator's Y-up document space; everything
the library returns is canvas Y-down.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from illustrator_mcp.protocol import TargetSelector, UuidTarget
from illustrator_mcp.tools.doc_model_tools import (
    ArtboardsInput,
    InspectInput,
    dm_script,
    illustrator_artboards,
    illustrator_inspect,
    resolve_preset,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "illustrator_mcp" / "resources" / "scripts"

# A minimal DOM: build(spec) returns {doc, byName}. Items are Y-up.
_FIXTURE = r"""
function coll(arr) { return arr; }
function makeItem(spec, parent, layer) {
  var it = {
    typename: spec.type || "PathItem", name: spec.name || "", uuid: spec.uuid,
    note: spec.note || "", hidden: !!spec.hidden, locked: !!spec.locked,
    parent: parent, layer: layer, visibleBounds: spec.vb || [0, 0, 10, -10],
    geometricBounds: spec.gb || spec.vb || [0, 0, 10, -10],
    opacity: 100, blendingMode: "BlendModes.NORMAL",
    pageItems: [], pathItems: [], compoundPathItems: [], layers: []
  };
  if (spec.fill) { it.filled = true; it.fillColor = spec.fill; } else { it.filled = false; }
  it.stroked = false;
  if (spec.contents !== undefined) {
    it.contents = spec.contents; it.kind = "TextType.POINTTEXT"; it.paragraphs = [1];
    var runs = spec.runs || [];
    it.textRanges = [];
    for (var r = 0; r < runs.length; r++) {
      it.textRanges.push({ characterAttributes: { size: runs[r].size,
        textFont: { name: runs[r].font, family: runs[r].family || runs[r].font, style: "Regular" } } });
    }
    it.textRange = { characterAttributes: { fillColor: { typename: "GrayColor", gray: 100 },
      strokeColor: { typename: "NoColor" } } };
  }
  var kids = spec.children || [];
  for (var k = 0; k < kids.length; k++) {
    var child = makeItem(kids[k], it, layer);
    if (it.typename === "CompoundPathItem") it.pathItems.push(child);
    else it.pageItems.push(child);
    if (child.typename === "CompoundPathItem") it.compoundPathItems.push(child);
  }
  if (spec.pathPoints) { it.pathPoints = new Array(spec.pathPoints); it.closed = true; it.area = -50; }
  return it;
}
function build(spec) {
  var doc = { layers: [], artboards: [], _active: 0, selection: spec.selection || [] };
  var byName = {}, byUuid = {};
  function index(it) {
    if (it.name) byName[it.name] = it;
    if (it.uuid) byUuid[it.uuid] = it;
    for (var i = 0; i < it.pageItems.length; i++) index(it.pageItems[i]);
    for (var j = 0; j < it.pathItems.length; j++) index(it.pathItems[j]);
  }
  function makeLayer(ls, parent) {
    var L = { typename: "Layer", name: ls.name, visible: ls.visible !== false, locked: !!ls.locked,
      parent: parent, layers: [], pageItems: [], compoundPathItems: [] };
    var subs = ls.layers || [];
    for (var s = 0; s < subs.length; s++) L.layers.push(makeLayer(subs[s], L));
    var items = ls.items || [];
    for (var i = 0; i < items.length; i++) {
      var it = makeItem(items[i], L, L);
      L.pageItems.push(it);
      if (it.typename === "CompoundPathItem") L.compoundPathItems.push(it);
      index(it);
    }
    return L;
  }
  for (var l = 0; l < spec.layers.length; l++) doc.layers.push(makeLayer(spec.layers[l], doc));
  var abs = spec.artboards || [{ name: "Artboard 1", rect: [0, 0, 600, -400] }];
  for (var a = 0; a < abs.length; a++) doc.artboards.push({ name: abs[a].name, artboardRect: abs[a].rect });
  doc.artboards.getActiveArtboardIndex = function () { return doc._active; };
  doc.artboards.setActiveArtboardIndex = function (i) { doc._active = i; };
  doc.artboards.add = function (rect) {
    var ab = { name: "Artboard " + (doc.artboards.length + 1), artboardRect: rect };
    doc.artboards.push(ab); return ab;
  };
  doc.artboards.remove = function (i) { doc.artboards.splice(i, 1); };
  doc.getPageItemFromUuid = function (u) {
    var hit = byUuid[u];
    if (!hit) throw new Error("an Illustrator error occurred: 1346458189 ('MRAP')");
    if (hit.typename === "CompoundPathItem") {
      return { typename: "GroupItem", uuid: hit.uuid, parent: hit.parent, pageItems: hit.pathItems };
    }
    return hit;
  };
  doc.visibleBounds = spec.docBounds || [0, 0, 600, -400];
  return { doc: doc, byName: byName };
}
var app = { textFonts: { getByName: function (n) {
  if (n.indexOf("Missing") === 0) throw new Error("No such element"); return {}; } } };
"""


def _run(expr: str, spec: dict | None = None) -> dict:
    """Load mcp_id + targets deps + doc_model, build the fixture, eval ``expr``."""
    src = "\n".join(
        (SCRIPTS / name).read_text(encoding="utf-8") for name in ("mcp_id.jsx", "doc_model.jsx")
    )
    harness = f"""
{_FIXTURE}
{src}
var built = build({json.dumps(spec or {"layers": []})});
var doc = built.doc;
try {{
  console.log(JSON.stringify({{ ok: true, value: (function () {{ return {expr}; }})() }}));
}} catch (e) {{
  console.log(JSON.stringify({{ ok: false, message: String(e.message || e) }}));
}}
"""
    out = subprocess.run(["node", "-e", harness], check=True, capture_output=True, text=True, cwd=ROOT)
    return json.loads(out.stdout)


def _value(expr: str, spec: dict | None = None):
    res = _run(expr, spec)
    assert res["ok"], res
    return res["value"]


RGB_BLUE = {"typename": "RGBColor", "red": 66, "green": 133, "blue": 244}

SCENE = {
    "layers": [{
        "name": "Base",
        "layers": [{"name": "Sub", "items": [{"name": "deep", "uuid": "20", "vb": [5, -5, 15, -15]}]}],
        "items": [
            {"name": "card", "uuid": "10", "vb": [20, -20, 220, -120], "fill": RGB_BLUE,
             "pathPoints": 4, "note": "@mcp:id=card_1"},
            {"name": "grp", "type": "GroupItem", "uuid": "11", "vb": [300, -150, 380, -230],
             "children": [
                 {"name": "dot", "uuid": "12", "vb": [300, -150, 320, -170]},
                 {"name": "label", "type": "TextFrame", "uuid": "13", "vb": [330, -150, 380, -170],
                  "contents": "Hello", "runs": [{"font": "MyriadPro-Regular", "size": 12},
                                               {"font": "MissingFont-Bold", "size": 30}]},
             ]},
            {"name": "ring", "type": "CompoundPathItem", "uuid": "14", "vb": [20, -250, 80, -310],
             "children": [{"uuid": "15", "fill": {"typename": "RGBColor", "red": 200, "green": 0, "blue": 0},
                           "pathPoints": 4},
                          {"uuid": "16", "pathPoints": 4}]},
            {"name": "offboard", "uuid": "17", "vb": [700, -50, 740, -90]},
        ],
    }],
}


# ==================== uuid resolution ====================

class TestResolvePageItemByUuid:
    def test_unknown_uuid_returns_null_instead_of_throwing(self):
        assert _value('resolvePageItemByUuid(doc, "999") === null', SCENE) is True

    def test_compound_path_wrapper_is_replaced_by_the_real_item(self):
        # Illustrator hands back a GroupItem wrapper for compound paths.
        assert _value('doc.getPageItemFromUuid("14").typename', SCENE) == "GroupItem"
        assert _value('resolvePageItemByUuid(doc, "14").typename', SCENE) == "CompoundPathItem"
        assert _value('resolvePageItemByUuid(doc, "14") === built.byName.ring', SCENE) is True

    def test_genuine_group_is_returned_as_is(self):
        assert _value('resolvePageItemByUuid(doc, "11") === built.byName.grp', SCENE) is True


# ==================== inspection ====================

class TestStructure:
    def test_default_depth_lists_layer_children_with_summaries(self):
        v = _value("dmStructure(doc, {})", SCENE)
        base = v["nodes"][0]
        assert base["kind"] == "layer" and base["layer_path"] == "Base"
        kinds = [c.get("uuid") or c.get("layer_path") for c in base["children"]]
        assert kinds == ["Base/Sub", "10", "11", "14", "17"]
        grp = base["children"][2]
        assert "children" not in grp  # beyond max_depth=1
        assert grp["child_count"] == 2
        assert grp["child_types"] == {"PathItem": 1, "TextFrame": 1}
        assert v["truncated"] is False

    def test_bounds_are_canvas_y_down(self):
        v = _value("dmStructure(doc, {})", SCENE)
        card = v["nodes"][0]["children"][1]
        assert card["bounds"] == [20, 20, 220, 120]
        assert card["mcp_id"] == "card_1"

    def test_unlimited_depth_reaches_nested_items(self):
        v = _value("dmStructure(doc, {max_depth: -1})", SCENE)
        grp = v["nodes"][0]["children"][2]
        assert [c["uuid"] for c in grp["children"]] == ["12", "13"]
        assert grp["children"][1]["text_preview"] == "Hello"

    def test_start_from_uuid(self):
        v = _value('dmStructure(doc, {uuids: ["11"], max_depth: 1})', SCENE)
        assert v["nodes"][0]["uuid"] == "11"
        assert len(v["nodes"][0]["children"]) == 2

    def test_unknown_start_nodes_are_reported_not_thrown(self):
        v = _value('dmStructure(doc, {uuids: ["11", "nope"], layers: ["Ghost"]})', SCENE)
        assert v["failed_objects"] == [
            {"uuid": "nope", "reason": "not_found"},
            {"layer": "Ghost", "reason": "layer_not_found"},
        ]

    def test_nested_layer_path_and_bare_name(self):
        v = _value('dmStructure(doc, {layers: ["Base/Sub", "Sub"], max_depth: 1})', SCENE)
        assert [n["layer_path"] for n in v["nodes"]] == ["Base/Sub", "Base/Sub"]
        assert v["nodes"][0]["children"][0]["uuid"] == "20"

    def test_budget_never_lists_a_container_partially(self):
        # Root (1) + Base children (5) = 6. A budget of 4 must not half-list Base.
        v = _value("dmStructure(doc, {max_nodes: 4})", SCENE)
        base = v["nodes"][0]
        assert "children" not in base
        assert base["child_count"] == 5
        assert v["truncated"] is True
        assert v["resume_hint"]["layers"] == ["Base"]

    def test_resume_hint_lists_unexpanded_group_uuids(self):
        v = _value("dmStructure(doc, {max_depth: -1, max_nodes: 7})", SCENE)
        assert v["truncated"] is True
        assert "11" in v["resume_hint"]["uuids"]


class TestArtboardView:
    def test_only_items_overlapping_the_artboard(self):
        v = _value("dmArtboardView(doc, {})", SCENE)
        assert v["artboard"]["bounds"] == [0, 0, 600, 400]
        uuids = [n["uuid"] for n in v["nodes"]]
        assert "17" not in uuids  # offboard
        assert set(uuids) == {"20", "10", "11", "14"}

    def test_out_of_range_index_is_a_clear_error(self):
        res = _run("dmArtboardView(doc, {artboard_index: 5})", SCENE)
        assert res["ok"] is False
        assert "out of range" in res["message"]


class TestDetails:
    def test_compound_path_paint_comes_from_first_child(self):
        v = _value('dmDetails(doc, {uuids: ["14"]})', SCENE)
        obj = v["objects"][0]
        assert obj["type"] == "CompoundPathItem"
        assert obj["appearance"]["fill"]["hex"] == "#c80000"
        assert obj["appearance"]["paint_source"] == "first_child_path"
        assert obj["geometry"]["path_count"] == 2
        assert obj["geometry"]["anchor_count"] == 8

    def test_missing_fonts_are_flagged(self):
        v = _value('dmDetails(doc, {uuids: ["13"], aspects: ["text"]})', SCENE)
        runs = v["objects"][0]["text"]["font_runs"]
        assert {r["font"]: r["available"] for r in runs} == {
            "MyriadPro-Regular": True, "MissingFont-Bold": False,
        }
        assert "appearance" not in v["objects"][0]

    def test_unknown_uuids_go_to_failed_objects(self):
        v = _value('dmDetails(doc, {uuids: ["10", "404"]})', SCENE)
        assert v["success_count"] == 1
        assert v["fail_count"] == 1
        assert v["failed_objects"] == [{"uuid": "404", "reason": "not_found"}]
        assert v["objects"][0]["appearance"]["fill"]["hex"] == "#4285f4"


# ==================== artboards ====================

TWO_BOARDS = {
    "layers": SCENE["layers"],
    "artboards": [
        {"name": "A", "rect": [0, 0, 600, -400]},
        {"name": "B", "rect": [620, 0, 1020, -300]},
    ],
}


class TestArtboards:
    def test_create_defaults_right_of_the_right_most_board(self):
        v = _value("dmArtboardCreate(doc, {width: 100, height: 50, name: 'new'})", TWO_BOARDS)
        assert v["artboard"]["bounds"] == [1040, 0, 1140, 50]
        assert v["artboard"]["name"] == "new"

    def test_update_resizes_around_the_anchor(self):
        v = _value("dmArtboardUpdate(doc, {artboard_index: 0, width: 800, anchor: 'top-left'})", TWO_BOARDS)
        assert v["artboard"]["bounds"] == [0, 0, 800, 400]
        v = _value("dmArtboardUpdate(doc, {artboard_index: 0, width: 400, height: 200})", TWO_BOARDS)
        assert v["artboard"]["bounds"] == [100, 100, 500, 300]  # centered

    def test_update_moves_and_renames(self):
        v = _value("dmArtboardUpdate(doc, {artboard_name: 'B', left: 0, top: 500, new_name: 'C'})", TWO_BOARDS)
        assert v["artboard"]["bounds"] == [0, 500, 400, 800]
        assert v["artboard"]["name"] == "C"
        assert v["changed"] == ["position", "name"]

    def test_update_with_nothing_to_change_errors(self):
        res = _run("dmArtboardUpdate(doc, {artboard_index: 0})", TWO_BOARDS)
        assert res["ok"] is False and "Nothing to update" in res["message"]

    def test_match_orientation_keeps_a_portrait_board_portrait(self):
        spec = {"layers": [], "artboards": [{"name": "P", "rect": [0, 0, 400, -600]}]}
        v = _value("dmMatchOrientation(doc, {artboard_index: 0, width: 1920, height: 1080})", spec)
        assert (v["width"], v["height"]) == (1080, 1920)

    def test_delete_refuses_the_last_artboard(self):
        res = _run("dmArtboardDelete(doc, {})", SCENE)
        assert res["ok"] is False and "only artboard" in res["message"]

    def test_delete_reports_removed_and_shifted_indices(self):
        v = _value("dmArtboardDelete(doc, {artboard_name: 'A'})", TWO_BOARDS)
        assert v["removed"]["name"] == "A"
        assert [a["name"] for a in v["artboards"]] == ["B"]

    def test_fit_to_uuids_with_padding(self):
        v = _value("dmArtboardFit(doc, {scope: 'uuids', uuids: ['10', '404'], padding: 10})", TWO_BOARDS)
        assert v["artboard"]["bounds"] == [10, 10, 230, 130]
        assert v["failed_objects"] == [{"uuid": "404", "reason": "not_found"}]

    def test_fit_with_nothing_to_fit_errors(self):
        res = _run("dmArtboardFit(doc, {scope: 'selection'})", TWO_BOARDS)
        assert res["ok"] is False and "Nothing to fit" in res["message"]


# ==================== uuid task targets ====================

def _collect(target: dict, spec: dict) -> dict:
    src = "\n".join(
        (SCRIPTS / name).read_text(encoding="utf-8") for name in ("mcp_id.jsx", "targets.jsx")
    )
    harness = f"""
{_FIXTURE}
var ErrorCodes = {{ R_COLLECT_FAILED: "R001", R_ELEMENT_NOT_FOUND: "R008" }};
function makeError(code, message, stage, itemRef, details) {{
  return {{ ok: false, error: {{ code: code, message: message, details: details || null }} }};
}}
function describeItemV2(item) {{ return {{ itemType: item.typename }}; }}
{src}
var built = build({json.dumps(spec)});
try {{
  var items = collectTargets(built.doc, {json.dumps(target)});
  var names = [];
  for (var i = 0; i < items.length; i++) names.push(items[i].name + ":" + items[i].typename);
  console.log(JSON.stringify({{ ok: true, items: names }}));
}} catch (e) {{
  console.log(JSON.stringify({{ ok: false, code: e.code, message: e.message, details: e.meta }}));
}}
"""
    out = subprocess.run(["node", "-e", harness], check=True, capture_output=True, text=True, cwd=ROOT)
    return json.loads(out.stdout)


class TestUuidTargets:
    def test_resolves_in_request_order_and_dedupes(self):
        res = _collect({"type": "uuid", "uuids": ["12", "10", "12"]}, SCENE)
        assert res == {"ok": True, "items": ["dot:PathItem", "card:PathItem"]}

    def test_compound_path_target_is_the_real_compound(self):
        res = _collect({"type": "uuid", "uuids": ["14"]}, SCENE)
        assert res["items"] == ["ring:CompoundPathItem"]

    def test_missing_uuid_fails_collect(self):
        res = _collect({"type": "uuid", "uuids": ["10", "404"]}, SCENE)
        assert res["ok"] is False
        assert "not found: 404" in res["message"]

    @pytest.mark.parametrize("flag", ["hidden", "locked"])
    def test_hidden_or_locked_item_fails_collect(self, flag):
        spec = {"layers": [{"name": "L", "items": [{"name": "x", "uuid": "1", flag: True}]}]}
        res = _collect({"type": "uuid", "uuids": ["1"]}, spec)
        assert res["ok"] is False
        assert flag in res["message"]

    def test_locked_layer_fails_collect(self):
        spec = {"layers": [{"name": "L", "locked": True, "items": [{"name": "x", "uuid": "1"}]}]}
        res = _collect({"type": "uuid", "uuids": ["1"]}, spec)
        assert res["ok"] is False and "locked" in res["message"]

    def test_protocol_model_accepts_uuid_target(self):
        sel = TargetSelector(target={"type": "uuid", "uuids": ["412"]})
        assert isinstance(sel.target, UuidTarget)
        with pytest.raises(ValidationError):
            TargetSelector(target={"type": "uuid", "uuids": []})


# ==================== Python tool layer ====================

class TestDmScript:
    def test_payload_is_a_json_literal_not_code(self):
        script = dm_script("dmStructure(doc, P)", {"layers": ['"); app.quit(); ("']})
        assert 'var P = {"layers": ["\\"); app.quit(); (\\""]};' in script

    def test_line_separators_are_escaped_for_es3(self):
        script = dm_script("dmStructure(doc, P)", {"layers": ["a b"]})
        assert " " not in script
        assert "\\u2028" in script

    def test_no_document_guard_only_when_needed(self):
        assert "app.documents.length === 0" in dm_script("dmListArtboards(doc)", {})
        assert "app.documents.length === 0" not in dm_script("dmListDocuments()", {}, needs_doc=False)


class TestInputValidation:
    def test_details_requires_uuids(self):
        with pytest.raises(ValidationError):
            InspectInput(view="details")

    def test_create_requires_size_or_preset(self):
        with pytest.raises(ValidationError):
            ArtboardsInput(action="create", width=100)
        ArtboardsInput(action="create", preset="A4")

    def test_preset_and_explicit_size_conflict(self):
        with pytest.raises(ValidationError):
            ArtboardsInput(action="update", preset="A4", width=10)

    def test_fit_uuids_scope_requires_uuids(self):
        with pytest.raises(ValidationError):
            ArtboardsInput(action="fit", scope="uuids")


class TestPresets:
    def test_lookup_is_case_insensitive(self):
        assert resolve_preset("a4", None) == (595.28, 841.89)

    def test_orientation_swaps(self):
        assert resolve_preset("A4", "landscape") == (841.89, 595.28)
        assert resolve_preset("HD 1080p", "portrait") == (1080.0, 1920.0)

    def test_unknown_preset_lists_known_ones(self):
        with pytest.raises(ValueError, match="Known presets: A3"):
            resolve_preset("Napkin", None)


def _mock_jsx():
    return patch(
        "illustrator_mcp.tools.doc_model_tools.execute_jsx_tool",
        AsyncMock(return_value='{"ok": true}'),
    )


class TestToolDispatch:
    @pytest.mark.asyncio
    async def test_inspect_injects_doc_model(self):
        with _mock_jsx() as m:
            await illustrator_inspect(InspectInput(view="artboard", artboard_index=1))
        kwargs = m.call_args.kwargs
        assert kwargs["includes"] == ["doc_model"]
        assert "dmArtboardView(doc, P)" in kwargs["script"]
        assert '"artboard_index": 1' in kwargs["script"]

    @pytest.mark.asyncio
    async def test_presets_action_needs_no_illustrator(self):
        with _mock_jsx() as m:
            raw = await illustrator_artboards(ArtboardsInput(action="presets"))
        m.assert_not_called()
        env = json.loads(raw)
        assert env["ok"] is True
        assert {"name": "A4", "width": 595.28, "height": 841.89} in env["result"]["presets"]

    @pytest.mark.asyncio
    async def test_preset_update_keeps_orientation_unless_forced(self):
        with _mock_jsx() as m:
            await illustrator_artboards(ArtboardsInput(action="update", preset="HD 1080p"))
        assert "dmMatchOrientation" in m.call_args.kwargs["script"]
        with _mock_jsx() as m:
            await illustrator_artboards(
                ArtboardsInput(action="update", preset="HD 1080p", orientation="landscape"))
        script = m.call_args.kwargs["script"]
        assert "dmMatchOrientation" not in script
        assert '"width": 1920.0' in script

    @pytest.mark.asyncio
    async def test_document_list_and_switch(self):
        from illustrator_mcp.tools._models import DocumentInput
        from illustrator_mcp.tools.documents import illustrator_document
        with patch("illustrator_mcp.tools.doc_model_tools.execute_jsx_tool",
                   AsyncMock(return_value="{}")) as m:
            await illustrator_document(DocumentInput(action="switch", name="poster.ai"))
        kwargs = m.call_args.kwargs
        assert "dmSwitchDocument(P)" in kwargs["script"]
        assert "app.documents.length === 0" not in kwargs["script"]
        assert kwargs["includes"] == ["doc_model"]
        with pytest.raises(ValidationError):
            DocumentInput(action="switch")


class TestRequestErrors:
    """dmFail() request errors become V011 with their own message; script bugs do not."""

    def _exec(self, body: str) -> dict:
        script = dm_script("probe(doc, P)", {"x": 1})
        harness = f"""
var app = {{ documents: {{ length: 1 }}, activeDocument: {{}} }};
function dmFail(m) {{ var e = new Error(m); e.dmUserError = true; throw e; }}
function probe(doc, P) {{ {body} }}
try {{ console.log(JSON.stringify({{ returned: {script} }})); }}
catch (e) {{ console.log(JSON.stringify({{ threw: String(e.message) }})); }}
"""
        out = subprocess.run(["node", "-e", harness], check=True, capture_output=True, text=True)
        return json.loads(out.stdout)

    def test_request_error_is_returned_as_a_marked_result(self):
        res = self._exec('dmFail("No artboard named \'x\'");')
        assert json.loads(res["returned"]) == {"__dm_request_error": "No artboard named 'x'"}

    def test_script_failure_still_throws(self):
        res = self._exec("undefinedThing.call();")
        assert "threw" in res

    @pytest.mark.asyncio
    async def test_marked_result_maps_to_v011(self):
        inner = json.dumps({"ok": True, "warnings": [], "error": None,
                            "diagnostics": {"tool": "t"},
                            "result": {"__dm_request_error": "Cannot delete the only artboard"}})
        with patch("illustrator_mcp.tools.doc_model_tools.execute_jsx_tool",
                   AsyncMock(return_value=inner)):
            raw = await illustrator_artboards(ArtboardsInput(action="delete"))
        env = json.loads(raw)
        assert env["ok"] is False
        assert env["error"]["code"] == "V011"
        assert env["error"]["message"] == "Cannot delete the only artboard"
        assert not any("syntax" in s.lower() for s in env["error"]["suggestions"])

    @pytest.mark.asyncio
    async def test_normal_results_pass_through_unchanged(self):
        inner = json.dumps({"ok": True, "result": {"artboards": []}})
        with patch("illustrator_mcp.tools.doc_model_tools.execute_jsx_tool",
                   AsyncMock(return_value=inner)):
            assert await illustrator_artboards(ArtboardsInput(action="list")) == inner


def test_doc_model_resolves_alongside_the_task_libraries():
    from illustrator_mcp.libraries import get_resolver
    code = get_resolver().resolve(["doc_model", "ops_core", "ops_element", "geometry"])
    assert "function dmStructure" in code
    assert "function resolvePageItemByUuid" in code


# ==================== compound-path paint (ops_style / element_modify) ====================

def _style_op(task: str, params: dict, extra: str = "") -> dict:
    """Run one ops_style handler against a compound path and a plain path.

    The fixture's CompoundPathItem silently accepts fillColor/strokeColor the
    way Illustrator's does (the write lands on the wrapper, not the art), so a
    handler that paints the container reports success yet leaves children unpainted.
    """
    core = (SCRIPTS / "ops_core.jsx").read_text(encoding="utf-8")
    helpers = core[core.index("function paintTargetsOf("):core.index("// ==================== Op Handler Registry")]
    style = (SCRIPTS / "ops_style.jsx").read_text(encoding="utf-8")
    harness = f"""
var OP_HANDLERS = {{}};
function registerOpHandler(n, h) {{ OP_HANDLERS[n] = h; }}
var ErrorCodes = {{ V_MISSING_REQUIRED_PARAM: "V002", V_INVALID_TARGETS: "V003" }};
function makeError(code, message) {{ return {{ ok: false, error: {{ code: code, message: message }} }}; }}
function RGBColor() {{ this.typename = "RGBColor"; this.red = 0; this.green = 0; this.blue = 0; }}
function path(name, r) {{
  var c = new RGBColor(); c.red = r;
  return {{ typename: "PathItem", name: name, filled: true, fillColor: c, stroked: false,
            strokeColor: new RGBColor(), strokeWidth: 1, opacity: 100 }};
}}
var compound = {{ typename: "CompoundPathItem", name: "ring", opacity: 100,
                  pathItems: [path("outer", 200), path("inner", 200)] }};
var plain = path("card", 10);
{helpers}
{style}
{extra}
var res = OP_HANDLERS[{json.dumps(task)}]({json.dumps(params)}, [compound, plain], {{ doc: {{}} }});
console.log(JSON.stringify({{ res: res, children: compound.pathItems, plain: plain,
  containerFill: compound.fillColor === undefined ? null : compound.fillColor }}));
"""
    out = subprocess.run(["node", "-e", harness], check=True, capture_output=True, text=True, cwd=ROOT)
    return json.loads(out.stdout)


class TestCompoundPathPaint:
    def test_set_fill_paints_every_child_path(self):
        v = _style_op("style_set_fill", {"r": 0, "g": 160, "b": 80})
        assert [c["fillColor"]["green"] for c in v["children"]] == [160, 160]
        assert v["plain"]["fillColor"]["green"] == 160
        assert v["containerFill"] is None  # never written to the wrapper
        assert v["res"]["data"]["modified"] == 2

    def test_set_stroke_and_remove_fill_reach_children(self):
        v = _style_op("style_set_stroke", {"r": 1, "g": 2, "b": 3, "width": 4})
        assert all(c["stroked"] and c["strokeWidth"] == 4 for c in v["children"])
        v = _style_op("style_remove_fill", {})
        assert [c["filled"] for c in v["children"]] == [False, False]

    def test_snapshot_reads_paint_from_the_first_child(self):
        v = _style_op("style_snapshot", {})
        compound_entry = v["res"]["data"]["items"][0]
        assert compound_entry["type"] == "CompoundPathItem"
        assert compound_entry["fill"]["type"] == "rgb"

    def test_clone_from_a_compound_onto_a_compound(self):
        extra = "function heapResolve() { return compound; }"
        v = _style_op("style_clone", {"from": "ring", "properties": ["fill"]}, extra)
        # plain receives the compound's child paint (red 200), not the wrapper's nothing
        assert v["plain"]["fillColor"]["red"] == 200


def test_element_modify_paints_compound_children():
    src = (SCRIPTS / "ops_element.jsx").read_text(encoding="utf-8")
    start = src.index("// Fill and stroke go to the paint carriers")
    end = src.index("// Opacity", start)
    assert "paintTargetsOf(item)" in src[start:end]
    assert "item.fillColor" not in src[start:end]
    assert "item.strokeColor" not in src[start:end]
