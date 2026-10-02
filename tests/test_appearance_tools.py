"""effects.jsx, swatches.jsx and the illustrator_effects / illustrator_swatches tools.

The JSX runs in Node against a small Illustrator-shaped DOM. Every fixture
behaviour below was observed live on Illustrator 30.8.1, not taken from the
documentation:

  - DOM enums (BlendModes.*, StrokeCap.*) have a *string* .typename equal to
    their own name; treating them as colors recursed forever ("Stack overrun");
  - applyEffect() returns nothing and grows visibleBounds; malformed XML throws;
  - the [Default] graphic style resets fill/stroke/opacity/blend of a path
    (every child of a compound path), resets only the container of a group or
    text frame, clears effects, and re-aligns a stroke to the inside;
  - getPageItemFromUuid() throws on an unknown uuid;
  - swatches.add() / swatchGroups.add() accept duplicate names;
  - SwatchGroup.addSwatch() moves a swatch; SwatchGroup.remove() deletes the
    swatches inside it;
  - removing [None] or [Registration] is a silent no-op;
  - a spot defined with CMYK in an RGB document reads back as RGB.
"""

from __future__ import annotations

import json
import struct
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from illustrator_mcp.tools.appearance_tools import (
    ColorSpec,
    EffectsInput,
    SwatchSpec,
    SwatchesInput,
    effects_payload,
    illustrator_effects,
    illustrator_swatches,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "illustrator_mcp" / "resources" / "scripts"

_FIXTURE = r"""
function E(name) { return { typename: name, toString: function () { return name; } }; }
var BlendModes = { NORMAL: E("BlendModes.NORMAL"), MULTIPLY: E("BlendModes.MULTIPLY") };
var ColorModel = { SPOT: E("ColorModel.SPOT"), PROCESS: E("ColorModel.PROCESS"), REGISTRATION: E("ColorModel.REGISTRATION") };
function RGBColor() { this.typename = "RGBColor"; this.red = 0; this.green = 0; this.blue = 0; }
function CMYKColor() { this.typename = "CMYKColor"; this.cyan = 0; this.magenta = 0; this.yellow = 0; this.black = 0; }
function rgb(r, g, b) { var c = new RGBColor(); c.red = r; c.green = g; c.blue = b; return c; }
var GROWTH = 10;  // each effect enlarges visible bounds by this much per side

function basePaint(it) {
  it.filled = true; it.fillColor = rgb(255, 255, 255);
  it.stroked = true; it.strokeColor = rgb(0, 0, 0); it.strokeWidth = 1;
  it.strokeDashes = []; it.strokeDashOffset = 0; it.strokeCap = E("StrokeCap.BUTTENDCAP");
  it.strokeJoin = E("StrokeJoin.MITERENDJOIN"); it.strokeMiterLimit = 10;
  it.fillOverprint = false; it.strokeOverprint = false;
}
function container(it) {
  it.opacity = 100; it.blendingMode = BlendModes.NORMAL; it.isIsolated = false;
  it.artworkKnockout = E("KnockoutState.DISABLED"); it._fx = [];
}
function bounds(it) {
  var gb = it.geometricBounds, pad = 0;
  if (it.typename === "PathItem" && it.stroked && !it._inside) pad = it.strokeWidth / 2;
  if (it.typename === "CompoundPathItem") {
    for (var i = 0; i < it.pathItems.length; i++) {
      var c = it.pathItems[i];
      if (c.stroked && !c._inside) pad = Math.max(pad, c.strokeWidth / 2);
    }
  }
  pad += GROWTH * it._fx.length;
  return [gb[0] - pad, gb[1] + pad, gb[2] + pad, gb[3] - pad];
}
function makeItem(spec, parent) {
  var it = { typename: spec.type || "PathItem", uuid: spec.uuid, name: spec.name || "",
    locked: !!spec.locked, hidden: !!spec.hidden, parent: parent,
    geometricBounds: spec.gb || [0, 100, 100, 0], pathItems: [], pageItems: [] };
  container(it);
  if (it.typename === "PathItem") {
    basePaint(it);
    it.filled = spec.fill !== undefined; if (spec.fill) it.fillColor = spec.fill;
    it.stroked = spec.stroke !== undefined; if (spec.stroke) { it.strokeColor = spec.stroke; it.strokeWidth = spec.sw || 1; }
  }
  for (var f = 0; f < (spec.fx || 0); f++) it._fx.push("<pre-existing/>");
  if (spec.opacity !== undefined) it.opacity = spec.opacity;
  if (spec.multiply) it.blendingMode = BlendModes.MULTIPLY;
  var kids = spec.children || [];
  for (var k = 0; k < kids.length; k++) {
    var child = makeItem(kids[k], it);
    if (it.typename === "CompoundPathItem") it.pathItems.push(child); else it.pageItems.push(child);
  }
  Object.defineProperty(it, "visibleBounds", { get: function () { return bounds(it); } });
  it.applyEffect = function (xml) {
    if (!/^<LiveEffect name="[^"]+"><Dict data="/.test(xml)) throw new Error("an Illustrator error occurred: 1346458189 ('MRAP')");
    if (!spec.inert) it._fx.push(xml);
  };
  return it;
}
function defaultStyle() {
  return { name: "[Default]", applyTo: function (it) {
    var keepFx = it._fx; container(it); if (DEFAULT_KEEPS_FX) it._fx = keepFx;
    if (it.typename === "PathItem") { basePaint(it); it._inside = true; }
    if (it.typename === "CompoundPathItem") {
      for (var i = 0; i < it.pathItems.length; i++) { basePaint(it.pathItems[i]); it.pathItems[i]._inside = true; }
    }
  } };
}
var DEFAULT_KEEPS_FX = false;

function swatchDoc(doc, rgbDoc) {
  doc.swatches = []; doc.spots = []; doc.swatchGroups = [];
  function group(name) {
    var g = { name: name, _m: [] };
    g.getAllSwatches = function () { return g._m.slice(0); };
    g.addSwatch = function (sw) {
      for (var i = 0; i < doc.swatchGroups.length; i++) {
        var m = doc.swatchGroups[i]._m, at = m.indexOf(sw); if (at >= 0) m.splice(at, 1);
      }
      g._m.push(sw);
    };
    g.addSpot = function (sp) { g.addSwatch(sp._swatch); };
    g.remove = function () {
      var m = g._m.slice(0);
      for (var i = 0; i < m.length; i++) m[i].remove();
      doc.swatchGroups.splice(doc.swatchGroups.indexOf(g), 1);
    };
    return g;
  }
  doc.swatchGroups.push(group(""));
  doc.swatchGroups.add = function () { var g = group(""); doc.swatchGroups.push(g); return g; };
  function newSwatch(name, color) {
    var sw = { name: name, color: color };
    sw.remove = function () {
      if (sw.name === "[None]" || sw.name === "[Registration]") return;  // silent no-op, as observed
      doc.swatches.splice(doc.swatches.indexOf(sw), 1);
      for (var i = 0; i < doc.swatchGroups.length; i++) {
        var m = doc.swatchGroups[i]._m, at = m.indexOf(sw); if (at >= 0) m.splice(at, 1);
      }
    };
    doc.swatches.push(sw); doc.swatchGroups[0]._m.push(sw);
    return sw;
  }
  doc.swatches.add = function () { return newSwatch("New Swatch", rgb(0, 0, 0)); };
  doc.spots.add = function () {
    var sp = { name: "", colorType: ColorModel.SPOT, _c: rgb(0, 0, 0) };
    Object.defineProperty(sp, "color", {
      get: function () { return sp._c; },
      set: function (c) {
        if (rgbDoc && c.typename === "CMYKColor") {
          sp._c = rgb(Math.round(255 * (1 - c.cyan / 100) * (1 - c.black / 100)),
                      Math.round(255 * (1 - c.magenta / 100) * (1 - c.black / 100)),
                      Math.round(255 * (1 - c.yellow / 100) * (1 - c.black / 100)));
        } else sp._c = c;
      } });
    var sw = newSwatch("", { typename: "SpotColor", spot: sp, tint: 100 });
    Object.defineProperty(sp, "name", { get: function () { return sw.name; }, set: function (n) { sw.name = n; } });
    sp._swatch = sw;
    doc.spots.push(sp);
    return sp;
  };
  var none = newSwatch("[None]", { typename: "NoColor" });
  var reg = doc.spots.add(); reg.name = "[Registration]"; reg.colorType = ColorModel.REGISTRATION;
  newSwatch("White", rgb(255, 255, 255));
  newSwatch("Black", rgb(0, 0, 0));
}

function build(spec) {
  var doc = { typename: "Document", name: spec.docName || "test.ai", layers: [], graphicStyles: spec.noDefault ? [] : [defaultStyle()] };
  var byUuid = {};
  function index(it) { if (it.uuid) byUuid[it.uuid] = it; for (var i = 0; i < it.pageItems.length; i++) index(it.pageItems[i]); for (var j = 0; j < it.pathItems.length; j++) index(it.pathItems[j]); }
  var layers = spec.layers || [];
  for (var l = 0; l < layers.length; l++) {
    var L = { typename: "Layer", name: layers[l].name, visible: layers[l].visible !== false, locked: !!layers[l].locked, parent: doc, pageItems: [] };
    var items = layers[l].items || [];
    for (var i = 0; i < items.length; i++) { var it = makeItem(items[i], L); L.pageItems.push(it); index(it); }
    doc.layers.push(L);
  }
  doc.getPageItemFromUuid = function (u) {
    if (!byUuid[u]) throw new Error("an Illustrator error occurred: 1346458189 ('MRAP')");
    return byUuid[u];
  };
  swatchDoc(doc, spec.rgbDoc !== false);
  return doc;
}
"""

RED = {"typename": "RGBColor", "red": 220, "green": 30, "blue": 30}
BLUE = {"typename": "RGBColor", "red": 0, "green": 0, "blue": 200}

SCENE = {
    "layers": [
        {"name": "Art", "items": [
            {"uuid": "1", "name": "plain", "fill": RED, "opacity": 60, "multiply": True},
            {"uuid": "2", "name": "stroked", "fill": RED, "stroke": BLUE, "sw": 4},
            {"uuid": "3", "type": "CompoundPathItem", "name": "ring", "children": [
                {"uuid": "31", "fill": RED}, {"uuid": "32", "fill": RED}]},
            {"uuid": "4", "type": "GroupItem", "name": "grp", "opacity": 70, "children": [
                {"uuid": "41", "fill": BLUE, "stroke": RED, "sw": 6}]},
            {"uuid": "5", "name": "locked", "fill": RED, "locked": True},
            {"uuid": "6", "name": "hidden", "fill": RED, "hidden": True},
            {"uuid": "7", "name": "inert", "fill": RED, "inert": True},
            {"uuid": "10", "name": "padded", "fill": RED, "inert": True, "fx": 1},
            {"uuid": "8", "type": "GroupItem", "name": "locked grp", "locked": True, "children": [
                {"uuid": "81", "fill": RED}]},
        ]},
        {"name": "Frozen", "locked": True, "items": [{"uuid": "9", "fill": RED}]},
    ]
}

SHADOW = {"action": "apply", "effect": "drop_shadow", "drop_shadow": {
    "offset_x": 7, "offset_y": 7, "blur": 5, "opacity": 75, "blend_mode": "multiply",
    "color": {"model": "rgb", "r": 0, "g": 0, "b": 0}}}


def _run(expr: str, spec: dict | None = None, *, libs=("polyfills", "mcp_id", "doc_model", "effects", "swatches"),
         prelude: str = "") -> dict:
    src = "\n".join((SCRIPTS / f"{name}.jsx").read_text(encoding="utf-8") for name in libs)
    harness = f"""
{_FIXTURE}
{src}
{prelude}
var doc = build({json.dumps(spec or {"layers": []})});
try {{
  console.log(JSON.stringify({{ ok: true, value: (function () {{ return {expr}; }})() }}));
}} catch (e) {{
  console.log(JSON.stringify({{ ok: false, message: String(e.message || e) }}));
}}
"""
    out = subprocess.run(["node", "-e", harness], check=True, capture_output=True, text=True, cwd=ROOT)
    return json.loads(out.stdout)


def _value(expr: str, spec: dict | None = None, **kw):
    res = _run(expr, spec, **kw)
    assert res["ok"], res
    return res["value"]


def _fx(payload: dict, spec: dict = SCENE, **kw):
    return _value(f"fxRun(doc, {json.dumps(payload)})", spec, **kw)


# ==================== effects: XML ====================

class TestEffectXml:
    def test_drop_shadow_uses_the_live_verified_keys(self):
        xml = _value("fxDropShadowXml(" + json.dumps(SHADOW["drop_shadow"]) + ")")
        assert xml == (
            '<LiveEffect name="Adobe Drop Shadow"><Dict data="R horz 7 R vert 7 R opac 0.75 R blur 5 '
            'R dark 100 I blnd 1 I csrc 0 B pair 1 B usePSLBlur 1 ">'
            '<Entry name="sclr" valueType="F"><Fill color="5 0 0 0"/></Entry></Dict></LiveEffect>'
        )

    def test_shadow_color_models_and_blend_codes(self):
        s = dict(SHADOW["drop_shadow"], blend_mode="screen", color={"model": "cmyk", "c": 0, "m": 100, "y": 0, "k": 0})
        xml = _value(f"fxDropShadowXml({json.dumps(s)})")
        assert "I blnd 2 " in xml and '<Fill color="1 0 1 0 0"/>' in xml
        s = dict(SHADOW["drop_shadow"], blend_mode="normal", color={"model": "rgb", "r": 66, "g": 133, "b": 244})
        xml = _value(f"fxDropShadowXml({json.dumps(s)})")
        assert "I blnd 0 " in xml and '<Fill color="5 0.2588 0.5216 0.9569"/>' in xml

    def test_darkness_mode_switches_color_source(self):
        s = dict(SHADOW["drop_shadow"], darkness=40)
        del s["color"]
        xml = _value(f"fxDropShadowXml({json.dumps(s)})")
        assert "R dark 40 " in xml and "I csrc 1 " in xml

    def test_gaussian_blur(self):
        assert _value("fxGaussianBlurXml({radius: 2.5})") == (
            '<LiveEffect name="Adobe PSL Gaussian Blur"><Dict data="R blur 2.5 "/></LiveEffect>')

    def test_unknown_effect_is_a_request_error(self):
        res = _run('fxRun(doc, {action: "apply", effect: "glow", uuids: ["1"]})', SCENE)
        assert res["ok"] is False and "Unknown effect 'glow'" in res["message"]


# ==================== effects: apply ====================

class TestApply:
    def test_batch_contract_and_skips(self):
        v = _fx(dict(SHADOW, uuids=["1", "3", "4", "5", "6", "81", "9", "404"]))
        assert v["success_count"] == 3
        assert v["fail_count"] == 1
        assert v["failed_objects"] == [{"uuid": "404", "reason": "not_found"}]
        assert v["skipped_objects"] == [
            {"uuid": "5", "reason": "locked"},
            {"uuid": "6", "reason": "hidden"},
            {"uuid": "81", "reason": "locked_parent"},
            {"uuid": "9", "reason": "locked_layer"},
        ]
        plain = v["objects"][0]
        assert plain["verified"] is True
        # canvas Y-down: fixture gb [0,100,100,0] Y-up -> [0,-100,100,0]
        assert plain["visible_bounds_before"] == [0, -100, 100, 0]
        assert plain["visible_bounds_after"] == [-10, -110, 110, 10]

    def test_document_guard(self):
        # uuids collide across open documents: a stale document name must
        # stop the call before anything is touched.
        res = _run(f"fxRun(doc, {json.dumps(dict(SHADOW, uuids=['1'], document='other.ai'))})", SCENE)
        assert res["ok"] is False and "active document is 'test.ai'" in res["message"]
        v = _fx(dict(SHADOW, uuids=["1"], document="test.ai"))
        assert v["success_count"] == 1
        assert v["document"]["name"] == "test.ai"

    def test_compound_path_gets_the_effect_on_the_container(self):
        doc_expr = (
            f"(function () {{ fxRun(doc, {json.dumps(dict(SHADOW, uuids=['3']))}); var c = doc.getPageItemFromUuid('3');"
            " return [c._fx.length, c.pathItems[0]._fx.length]; })()"
        )
        assert _value(doc_expr, SCENE) == [1, 0]

    def test_an_effect_that_leaves_bounds_unchanged_is_reported_failed(self):
        v = _fx(dict(SHADOW, uuids=["7"]))
        assert v["success_count"] == 0
        assert v["failed_objects"][0]["reason"].startswith("effect_not_applied")

    def test_unchanged_bounds_on_an_already_padded_item_is_unverified_not_failed(self):
        # Live: a 7 pt shadow on an item that already had a 2 pt blur left the
        # visible bounds unchanged (the blur pads ~36 pt) although it applied.
        v = _fx(dict(SHADOW, uuids=["10"]))
        assert v["fail_count"] == 0 and v["success_count"] == 1
        assert v["objects"][0]["verified"] is False
        assert "already padded" in v["objects"][0]["note"]

    def test_zero_offset_zero_blur_is_unverified_not_failed(self):
        s = dict(SHADOW["drop_shadow"], offset_x=0, offset_y=0, blur=0)
        v = _fx({"action": "apply", "effect": "drop_shadow", "drop_shadow": s, "uuids": ["7"]})
        assert v["success_count"] == 1 and v["objects"][0]["verified"] is False


# ==================== effects: remove ====================

def _appearance(uuid: str) -> str:
    return f"dmAppearance(doc.getPageItemFromUuid('{uuid}'))"


class TestRemove:
    def test_restores_paint_and_transparency(self):
        expr = (
            "(function () { var before = " + _appearance("1") + ";"
            f" fxRun(doc, {json.dumps(dict(SHADOW, uuids=['1']))});"
            " var res = fxRun(doc, {action: 'remove', uuids: ['1']});"
            " return {res: res, before: before, after: " + _appearance("1") + "}; })()"
        )
        v = _value(expr, SCENE)
        assert v["res"]["success_count"] == 1
        assert v["after"] == v["before"]
        assert v["before"]["opacity"] == 60 and v["before"]["blend_mode"] == "multiply"
        assert v["res"]["objects"][0]["visible_bounds_after"] == [0, -100, 100, 0]

    def test_compound_children_paint_is_restored(self):
        expr = (
            "(function () { fxRun(doc, {action: 'remove', uuids: ['3']});"
            " var c = doc.getPageItemFromUuid('3');"
            " return [dmColor(c.pathItems[0].fillColor).hex, dmColor(c.pathItems[1].fillColor).hex, c.pathItems[0].stroked]; })()"
        )
        assert _value(expr, SCENE) == ["#dc1e1e", "#dc1e1e", False]

    def test_group_restores_container_opacity_and_leaves_children_alone(self):
        expr = (
            "(function () { var res = fxRun(doc, {action: 'remove', uuids: ['4']});"
            " var g = doc.getPageItemFromUuid('4'), k = g.pageItems[0];"
            " return [res.success_count, g.opacity, k.strokeWidth, !!k._inside]; })()"
        )
        assert _value(expr, SCENE) == [1, 70, 6, False]

    def test_stroked_path_is_skipped_without_opt_in(self):
        v = _fx({"action": "remove", "uuids": ["2"]})
        assert v["success_count"] == 0
        assert v["skipped_objects"][0]["reason"].startswith("stroked_path")

    def test_replace_also_respects_the_stroke_guard(self):
        v = _fx({"action": "apply", "effect": "gaussian_blur", "gaussian_blur": {"radius": 2},
                 "replace": True, "uuids": ["2"]})
        assert v["success_count"] == 0 and v["skipped_objects"][0]["reason"].startswith("stroked_path")

    def test_opt_in_restores_stroke_color_and_width(self):
        expr = (
            "(function () { var res = fxRun(doc, {action: 'remove', uuids: ['2'], allow_stroke_realign: true});"
            " var p = doc.getPageItemFromUuid('2');"
            " return [res.success_count, dmColor(p.strokeColor).hex, p.strokeWidth]; })()"
        )
        assert _value(expr, SCENE) == [1, "#0000c8", 4]

    def test_enum_properties_compare_without_recursing(self):
        # BlendModes/StrokeCap enums carry a string typename; treating them
        # as colors recursed until "Stack overrun" on every remove.
        expr = "(function () { var s = fxSnapshot(doc.getPageItemFromUuid('2')); return fxDiff(s, s); })()"
        assert _value(expr, SCENE) == []

    def test_paint_that_does_not_stick_is_reported(self):
        # A carrier whose fill write is ignored must not be reported as removed.
        prelude = """
var _origBuild = build;
build = function (spec) {
  var d = _origBuild(spec);
  var p = d.getPageItemFromUuid("1");
  var style = d.graphicStyles[0], apply = style.applyTo;
  style.applyTo = function (it) {
    apply(it);
    Object.defineProperty(it, "fillColor", { get: function () { return rgb(255, 255, 255); }, set: function () {} });
  };
  return d;
};
"""
        v = _fx({"action": "remove", "uuids": ["1"]}, prelude=prelude)
        assert v["success_count"] == 0
        assert "appearance_not_restored: " in v["failed_objects"][0]["reason"]
        assert "fillColor" in v["failed_objects"][0]["reason"]

    def test_default_style_that_keeps_effects_is_detected(self):
        expr = (f"(function () {{ fxRun(doc, {json.dumps(dict(SHADOW, uuids=['1']))});"
                " return fxRun(doc, {action: 'remove', uuids: ['1']}); })()")
        v = _value(expr, SCENE, prelude="DEFAULT_KEEPS_FX = true;")
        assert v["success_count"] == 0
        assert v["failed_objects"][0]["reason"].startswith("effects_remain")

    def test_missing_default_style_is_a_request_error(self):
        res = _run("fxRun(doc, {action: 'remove', uuids: ['1']})", dict(SCENE, noDefault=True))
        assert res["ok"] is False and "[Default]" in res["message"]


# ==================== swatches: document ====================

def _sw(expr: str, **kw):
    return _value(expr, {"layers": []}, **kw)


class TestSwatchList:
    def test_list_reports_kind_group_and_pages(self):
        v = _sw("swList(doc, {max_swatches: 2})")
        assert v["swatch_count"] == 4
        assert [s["name"] for s in v["swatches"]] == ["[None]", "[Registration]"]
        assert [s["kind"] for s in v["swatches"]] == ["none", "registration"]
        assert v["truncated"] is True and v["resume_hint"] == {"offset": 2}
        v = _sw("swList(doc, {offset: 2})")
        assert [s["name"] for s in v["swatches"]] == ["White", "Black"]
        assert v["swatches"][0]["group"] is None

    def test_get_is_exact_and_offers_only_existing_names(self):
        v = _sw('swGet(doc, {names: ["White", "white", "Whte", "Teal"]})')
        assert [s["name"] for s in v["swatches"]] == ["White"]
        assert v["failed_objects"] == [
            {"name": "white", "reason": "not_found", "similar_existing": ["White"]},
            {"name": "Whte", "reason": "not_found", "similar_existing": ["White"]},
            {"name": "Teal", "reason": "not_found", "similar_existing": []},
        ]

    def test_unknown_group_filter_is_a_request_error(self):
        res = _run('swList(doc, {group: "Brand"})', {"layers": []})
        assert res["ok"] is False and "No swatch group named 'Brand'" in res["message"]


BRAND = [
    {"name": "Brand Red", "kind": "process", "color": {"model": "rgb", "r": 214, "g": 40, "b": 40}},
    {"name": "Brand Spot", "kind": "spot", "color": {"model": "cmyk", "c": 0, "m": 100, "y": 0, "k": 0}},
    {"name": "Brand Global", "kind": "global", "color": {"model": "rgb", "r": 10, "g": 20, "b": 30}},
]


class TestSwatchCreate:
    def _create(self, extra: str = ""):
        return _sw(
            "(function () { swCreateGroup(doc, {group: 'Brand'});"
            f" var r = swCreate(doc, {{group: 'Brand', swatches: {json.dumps(BRAND)}}}); {extra} return r; }})()"
        )

    def test_creates_each_kind_in_the_group(self):
        v = self._create()
        assert v["success_count"] == 3
        assert [(c["name"], c["kind"], c["group"]) for c in v["created"]] == [
            ("Brand Red", "process", "Brand"), ("Brand Spot", "spot", "Brand"), ("Brand Global", "global", "Brand"),
        ]
        assert v["created"][1]["converted_to"] == "rgb"  # CMYK spot in an RGB document
        assert "converted_to" not in v["created"][2]

    def test_duplicate_names_are_refused(self):
        # Illustrator accepts a duplicate swatch name; the tool must not.
        v = _sw(f"swCreate(doc, {{swatches: {json.dumps([dict(BRAND[0], name='White'), BRAND[0], BRAND[0]])}}})")
        assert v["success_count"] == 1
        assert v["failed_objects"] == [{"name": "White", "reason": "name_exists"},
                                       {"name": "Brand Red", "reason": "name_exists"}]
        assert _sw("(function () { var n = 0; for (var i = 0; i < doc.swatches.length; i++)"
                   " if (doc.swatches[i].name === 'White') n++; return n; })()") == 1

    def test_missing_group_and_duplicate_group_are_request_errors(self):
        res = _run(f"swCreate(doc, {{group: 'Nope', swatches: {json.dumps(BRAND[:1])}}})", {"layers": []})
        assert res["ok"] is False and "create_group" in res["message"]
        res = _run("(function () { swCreateGroup(doc, {group: 'B'}); return swCreateGroup(doc, {group: 'B'}); })()",
                   {"layers": []})
        assert res["ok"] is False and "already exists" in res["message"]


class TestSwatchDelete:
    def test_reserved_swatches_are_skipped_not_reported_deleted(self):
        v = _sw('swDelete(doc, {names: ["[None]", "White", "Teal"]})')
        assert v["skipped_objects"][0]["name"] == "[None]"
        assert v["deleted"] == [{"name": "White", "kind": "process", "removed": 1}]
        assert v["failed_objects"][0]["name"] == "Teal"

    def test_silent_remove_is_caught_by_read_back(self):
        prelude = """
var _b = build;
build = function (spec) { var d = _b(spec); var w = d.swatches[2]; w.remove = function () {}; return d; };
"""
        v = _sw('swDelete(doc, {names: ["White"]})', prelude=prelude)
        assert v["failed_objects"] == [{"name": "White", "reason": "still_present_after_remove"}]

    def test_spot_delete_notes_the_process_fallback(self):
        v = _sw("(function () { swCreate(doc, {swatches: " + json.dumps(BRAND[1:2]) + "});"
                " return swDelete(doc, {names: ['Brand Spot']}); })()")
        assert v["deleted"][0]["kind"] == "spot" and "process color" in v["deleted"][0]["note"]

    def test_delete_group_keeps_swatches_by_default(self):
        v = _sw("(function () { swCreateGroup(doc, {group: 'Brand'});"
                f" swCreate(doc, {{group: 'Brand', swatches: {json.dumps(BRAND)}}});"
                " var r = swDeleteGroup(doc, {group: 'Brand'});"
                " r.after = swGet(doc, {names: ['Brand Red', 'Brand Spot', 'Brand Global']}); return r; })()")
        assert v["kept_swatches"] == ["Brand Red", "Brand Spot", "Brand Global"]
        assert v["after"]["success_count"] == 3
        assert {s["group"] for s in v["after"]["swatches"]} == {None}

    def test_delete_group_without_keep_deletes_members(self):
        v = _sw("(function () { swCreateGroup(doc, {group: 'Tmp', move_swatches: ['White', 'Nope']});"
                " return swDeleteGroup(doc, {group: 'Tmp', keep_swatches: false}); })()")
        assert v["deleted_swatches"] == ["White"]


# ==================== swatches: ASE parser ====================

def _ase(blocks: list[tuple[int, bytes]]) -> bytes:
    out = b"ASEF" + struct.pack(">HHI", 1, 0, len(blocks))
    for kind, body in blocks:
        out += struct.pack(">HI", kind, len(body)) + body
    return out


def _utf16(name: str) -> bytes:
    data = (name + "\0").encode("utf-16-be")
    return struct.pack(">H", len(data) // 2) + data


def _color(name: str, model: bytes, values: list[float], kind: int) -> bytes:
    return _utf16(name) + model + struct.pack(">" + "f" * len(values), *values) + struct.pack(">H", kind)


def _parse(data: bytes):
    src = "\n".join((SCRIPTS / f"{n}.jsx").read_text(encoding="utf-8") for n in ("doc_model", "swatches"))
    harness = src + "\nvar s = Buffer.from(process.argv[1], 'base64').toString('latin1');\n" \
                    "try { console.log(JSON.stringify({ok: true, value: swParseAse(s)})); }" \
                    " catch (e) { console.log(JSON.stringify({ok: false, message: String(e.message)})); }"
    import base64
    out = subprocess.run(["node", "-e", harness, base64.b64encode(data).decode()],
                         check=True, capture_output=True, text=True)
    return json.loads(out.stdout)


class TestAseParser:
    def test_groups_models_and_kinds(self):
        data = _ase([
            (0x0001, _color("Loose", b"RGB ", [1.0, 0.5, 0.0], 2)),
            (0xC001, _utf16("Brand")),
            (0x0001, _color("Ink", b"CMYK", [0.93, 0.35, 0.0, 0.15], 1)),
            (0x0001, _color("Glob", b"RGB ", [0.2588, 0.5216, 0.9569], 0)),
            (0xC002, b""),
        ])
        v = _parse(data)["value"]
        assert v["groups"] == [{"name": "Brand", "swatch_count": 2}]
        assert v["swatches"] == [
            {"name": "Loose", "kind": "process", "group": None,
             "color": {"model": "rgb", "hex": "#ff8000", "r": 255, "g": 128, "b": 0}},
            {"name": "Ink", "kind": "spot", "group": "Brand",
             "color": {"model": "cmyk", "c": 93, "m": 35, "y": 0, "k": 15}},
            {"name": "Glob", "kind": "global", "group": "Brand",
             "color": {"model": "rgb", "hex": "#4285f4", "r": 66, "g": 133, "b": 244}},
        ]

    def test_non_ase_is_rejected(self):
        res = _parse(b"GIF89a" + b"\0" * 20)
        assert res["ok"] is False and "Not an ASE" in res["message"]

    @pytest.mark.skipif(
        not Path("/Applications/Adobe Illustrator 2026/Presets.localized/en_US/Swatches/Corporate.ase").exists(),
        reason="Illustrator 2026 swatch libraries not installed",
    )
    def test_shipped_library(self):
        data = Path("/Applications/Adobe Illustrator 2026/Presets.localized/en_US/Swatches/Corporate.ase").read_bytes()
        v = _parse(data)["value"]
        assert len(v["swatches"]) == 40
        hit = [s for s in v["swatches"] if s["name"] == "C=93 M=35 Y=0 K=15"][0]
        assert hit["color"] == {"model": "cmyk", "c": 93, "m": 35, "y": 0, "k": 15}
        assert hit["group"] == "Corporate 2"


# ==================== Python tool layer ====================

class TestColorSpec:
    def test_exactly_one_model(self):
        with pytest.raises(ValidationError):
            ColorSpec()
        with pytest.raises(ValidationError):
            ColorSpec(hex="#ffffff", rgb=[1, 2, 3])

    def test_payloads(self):
        assert ColorSpec(hex="2a9d8f").to_payload() == {"model": "rgb", "r": 42, "g": 157, "b": 143}
        assert ColorSpec(cmyk=[0, 100, 0, 0]).to_payload() == {"model": "cmyk", "c": 0, "m": 100, "y": 0, "k": 0}

    def test_ranges(self):
        with pytest.raises(ValidationError):
            ColorSpec(rgb=[0, 0, 256])
        with pytest.raises(ValidationError):
            ColorSpec(cmyk=[0, 0, 0, 101])


class TestEffectsInput:
    def test_apply_requires_effect(self):
        with pytest.raises(ValidationError):
            EffectsInput(action="apply", uuids=["1"])

    def test_remove_rejects_effect_parameters(self):
        with pytest.raises(ValidationError):
            EffectsInput(action="remove", uuids=["1"], blur=3)
        EffectsInput(action="remove", uuids=["1"], allow_stroke_realign=True)

    def test_blur_rejects_shadow_parameters(self):
        with pytest.raises(ValidationError):
            EffectsInput(action="apply", effect="gaussian_blur", uuids=["1"], opacity=50)
        with pytest.raises(ValidationError):
            EffectsInput(action="apply", effect="drop_shadow", uuids=["1"], radius=3)

    def test_color_and_darkness_conflict(self):
        with pytest.raises(ValidationError):
            EffectsInput(action="apply", effect="drop_shadow", uuids=["1"], color={"hex": "#000000"}, darkness=50)

    def test_document_is_forwarded_only_when_given(self):
        assert "document" not in effects_payload(EffectsInput(action="remove", uuids=["1"]))
        p = effects_payload(EffectsInput(action="remove", uuids=["1"], document="poster.ai"))
        assert p["document"] == "poster.ai"

    def test_default_shadow_payload_matches_illustrator_dialog(self):
        p = effects_payload(EffectsInput(action="apply", effect="drop_shadow", uuids=["1"]))
        assert p["drop_shadow"] == SHADOW["drop_shadow"]
        assert p["replace"] is False

    def test_darkness_payload_has_no_color(self):
        p = effects_payload(EffectsInput(action="apply", effect="drop_shadow", uuids=["1"], darkness=30))
        assert p["drop_shadow"]["darkness"] == 30 and "color" not in p["drop_shadow"]


class TestSwatchesInput:
    def test_action_requirements(self):
        for kwargs in ({"action": "get"}, {"action": "delete"}, {"action": "create"},
                       {"action": "create_group"}, {"action": "delete_group"}, {"action": "library"}):
            with pytest.raises(ValidationError):
                SwatchesInput(**kwargs)

    def test_reserved_names_cannot_be_created(self):
        with pytest.raises(ValidationError):
            SwatchSpec(name="[Brand]", color={"hex": "#000000"})


def _mock_jsx():
    return patch("illustrator_mcp.tools.doc_model_tools.execute_jsx_tool", AsyncMock(return_value='{"ok": true}'))


class TestToolDispatch:
    @pytest.mark.asyncio
    async def test_effects_injects_effects_library(self):
        with _mock_jsx() as m:
            await illustrator_effects(EffectsInput(action="apply", effect="gaussian_blur", uuids=["7"], radius=2))
        kw = m.call_args.kwargs
        assert kw["includes"] == ["effects"]
        assert "fxRun(doc, P)" in kw["script"]
        assert '"gaussian_blur": {"radius": 2.0}' in kw["script"]

    @pytest.mark.asyncio
    async def test_swatch_libraries_need_no_document(self):
        with _mock_jsx() as m:
            await illustrator_swatches(SwatchesInput(action="libraries"))
        kw = m.call_args.kwargs
        assert kw["includes"] == ["swatches"]
        assert "app.documents.length === 0" not in kw["script"]
        with _mock_jsx() as m:
            await illustrator_swatches(SwatchesInput(action="list"))
        assert "app.documents.length === 0" in m.call_args.kwargs["script"]

    @pytest.mark.asyncio
    async def test_create_sends_normalized_colors(self):
        with _mock_jsx() as m:
            await illustrator_swatches(SwatchesInput(action="create", swatches=[
                {"name": "A", "kind": "global", "color": {"hex": "#0a141e"}}]))
        assert '"swatches": [{"name": "A", "kind": "global", "color": {"model": "rgb", "r": 10, "g": 20, "b": 30}}]' \
            in m.call_args.kwargs["script"]

    @pytest.mark.asyncio
    async def test_request_error_uses_swatch_suggestions(self):
        inner = json.dumps({"ok": True, "result": {"__dm_request_error": "No swatch group named 'Brand'"}})
        with patch("illustrator_mcp.tools.doc_model_tools.execute_jsx_tool", AsyncMock(return_value=inner)):
            env = json.loads(await illustrator_swatches(SwatchesInput(action="list", group="Brand")))
        assert env["error"]["code"] == "V011"
        assert any("illustrator_swatches" in s for s in env["error"]["suggestions"])


def test_libraries_resolve_with_their_dependencies():
    from illustrator_mcp.libraries import get_resolver
    code = get_resolver().resolve(["effects", "swatches"])
    for fn in ("function fxRun", "function swList", "function dmFail", "function resolvePageItemByUuid"):
        assert fn in code
