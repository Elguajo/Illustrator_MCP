"""Illustrator-shaped DOM with a small text engine, for running JSX in Node.

Shared by test_text_tools.py and test_preflight_v2.py. The fixture mirrors
behaviour observed live on Illustrator 30.8.1, not the documentation:

  - TextFrame.textRanges is per *character*;
  - characters[i] is a 1-char range whose .length can be set;
  - assigning .contents to a range gives the new text the attributes of the
    range's first character;
  - getPageItemFromUuid() throws on an unknown uuid;
  - a missing font can come back from app.textFonts.getByName() as a
    placeholder record whose family differs from the run's font family;
  - the last frame of a thread reports itself as its nextFrame.

Specs use canvas Y-down bounds ``b: [left, top, right, bottom]``; the fixture
stores Illustrator's Y-up values, so the library's conversion is exercised.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "illustrator_mcp" / "resources" / "scripts"

FIXTURE = r"""
var FONTS = {};          // installed: name -> record
var PLACEHOLDERS = {};   // missing but registered: name -> record
function fontObj(name, family, style) { return { name: name, family: family || name, style: style || "Regular", typename: "TextFont" }; }
function useFont(name) {
  if (FONTS[name]) return FONTS[name];
  if (FONTS.__used[name]) return FONTS.__used[name];
  throw new Error("fixture: unknown font " + name);
}
Object.defineProperty(FONTS, "__used", { value: {}, enumerable: false });
var app = { textFonts: { getByName: function (n) {
  if (FONTS.hasOwnProperty(n)) return FONTS[n];
  if (PLACEHOLDERS.hasOwnProperty(n)) return PLACEHOLDERS[n];
  throw new Error("No such element");
} } };

function RGBColor() { this.typename = "RGBColor"; this.red = 0; this.green = 0; this.blue = 0; }
function CMYKColor() { this.typename = "CMYKColor"; this.cyan = 0; this.magenta = 0; this.yellow = 0; this.black = 0; }
function GrayColor() { this.typename = "GrayColor"; this.gray = 0; }
function NoColor() { this.typename = "NoColor"; }
function makeColor(spec) {
  if (!spec) return new NoColor();
  var c;
  if (spec.rgb) { c = new RGBColor(); c.red = spec.rgb[0]; c.green = spec.rgb[1]; c.blue = spec.rgb[2]; return c; }
  if (spec.cmyk) { c = new CMYKColor(); c.cyan = spec.cmyk[0]; c.magenta = spec.cmyk[1]; c.yellow = spec.cmyk[2]; c.black = spec.cmyk[3]; return c; }
  if (spec.gray !== undefined) { c = new GrayColor(); c.gray = spec.gray; return c; }
  if (spec.spot) { return { typename: "SpotColor", tint: spec.tint === undefined ? 100 : spec.tint,
    spot: { name: spec.spot, colorType: spec.spot === "[Registration]" ? "ColorModel.REGISTRATION" : "ColorModel.SPOT",
            color: makeColor({ cmyk: [0, 0, 0, 100] }) } }; }
  return new NoColor();
}
function cloneColor(c) { var o = {}; for (var k in c) o[k] = c[k]; return o; }

// ---------- text engine ----------
var ATTRS = ["textFont", "size", "tracking", "horizontalScale", "verticalScale", "baselineShift",
             "underline", "strikeThrough", "capitalization", "fillColor", "strokeColor", "strokeWeight"];
function baseAttrs(spec) {
  return { textFont: useFont(spec.font || "MyriadPro-Regular"), size: spec.size || 12, tracking: spec.tracking || 0,
    horizontalScale: 100, verticalScale: 100, baselineShift: 0, underline: false, strikeThrough: false,
    capitalization: "FontCapsOption.NORMALCAPS", fillColor: makeColor(spec.fill || { gray: 100 }),
    strokeColor: new NoColor(), strokeWeight: 1 };
}
function copyAttrs(a) { var o = {}; for (var k in a) o[k] = a[k]; return o; }

function Story(runs) {
  this.chars = [];
  this.paraAttrs = [];
  for (var r = 0; r < runs.length; r++) {
    var a = baseAttrs(runs[r]);
    for (var i = 0; i < runs[r].text.length; i++) this.chars.push({ ch: runs[r].text.charAt(i), a: copyAttrs(a) });
  }
  this.frames = [];
  this.syncParas();
}
Story.prototype.text = function () { var s = ""; for (var i = 0; i < this.chars.length; i++) s += this.chars[i].ch; return s; };
Story.prototype.paraSpans = function () {
  var spans = [], start = 0, t = this.text();
  for (var i = 0; i < t.length; i++) if (t.charAt(i) === "\r") { spans.push([start, i + 1]); start = i + 1; }
  spans.push([start, t.length]);
  return spans;
};
Story.prototype.syncParas = function () {
  var n = this.paraSpans().length;
  while (this.paraAttrs.length < n) this.paraAttrs.push({ spaceBefore: 0, spaceAfter: 0 });
  this.paraAttrs.length = n;
};

function charAttrs(story, start, len) {
  var o = {};
  ATTRS.forEach(function (name) {
    Object.defineProperty(o, name, {
      get: function () { return story.chars[start].a[name]; },
      set: function (v) { for (var i = start; i < start + len; i++) story.chars[i].a[name] = v; },
      enumerable: true, configurable: true
    });
  });
  return o;
}

function Range(story, start, len) { this._s = story; this._start = start; this._len = len; this.typename = "TextRange"; }
Object.defineProperty(Range.prototype, "length", {
  configurable: true,
  get: function () { return this._len; },
  set: function (n) { this._len = n; }
});
Object.defineProperty(Range.prototype, "start", { get: function () { return this._start; } });
Object.defineProperty(Range.prototype, "contents", {
  configurable: true,
  get: function () { return this._s.text().substr(this._start, this._len); },
  set: function (v) {
    var a = this._s.chars[this._start].a, ins = [];
    for (var i = 0; i < v.length; i++) ins.push({ ch: v.charAt(i), a: copyAttrs(a) });
    Array.prototype.splice.apply(this._s.chars, [this._start, this._len].concat(ins));
    this._len = v.length;
    this._s.syncParas();
  }
});
Object.defineProperty(Range.prototype, "characterAttributes", {
  get: function () { return charAttrs(this._s, this._start, this._len); }
});
Object.defineProperty(Range.prototype, "paragraphs", {
  get: function () {
    var story = this._s, out = [], spans = story.paraSpans(), end = this._start + Math.max(this._len, 1);
    spans.forEach(function (sp, idx) {
      if (sp[0] < end && (sp[1] > this._start || (sp[0] === sp[1] && sp[0] === this._start))) {
        out.push({ paragraphAttributes: story.paraAttrs[idx] });
      }
    }, this);
    return out;
  }
});
Range.prototype.remove = function () { this._s.chars.splice(this._start, this._len); this._len = 0; this._s.syncParas(); };

function charColl(story, base, count) {
  var c = { };
  var n = count();
  for (var i = 0; i < n; i++) (function (i) {
    Object.defineProperty(c, i, { get: function () { return new Range(story, base() + i, 1); } });
  })(i);
  c.length = n;
  return c;
}

// ---------- items ----------
var BYUUID = {}, BYNAME = {}, NEXT = { uuid: 9000 };
function yup(b) { return [b[0], -b[1], b[2], -b[3]]; }

function defp(o, n, d) { d.configurable = true; Object.defineProperty(o, n, d); }
function makeTextFrame(spec, it) {
  var story = spec.story || new Story(spec.runs || [{ text: spec.contents || "" }]);
  it.story_ = story;
  story.frames.push(it);
  it.kind = spec.kind || "TextType.POINTTEXT";
  it.visibleChars = spec.visible;  // undefined = everything visible
  it.frameStart = function () {
    var idx = story.frames.indexOf(it), s = 0;
    for (var i = 0; i < idx; i++) s += story.frames[i].frameLen();
    return s;
  };
  it.frameLen = function () {
    var idx = story.frames.indexOf(it), rest = story.chars.length - it.frameStart();
    if (idx === story.frames.length - 1) return rest;   // last frame owns the overset
    return Math.min(rest, it.visibleChars === undefined ? rest : it.visibleChars);
  };
  defp(it, "contents", { get: function () { return story.text().substr(it.frameStart(), it.frameLen()); },
    set: function (v) { new Range(story, 0, story.chars.length).contents = v; } });
  defp(it, "characters", { get: function () { return charColl(story, it.frameStart, it.frameLen); } });
  defp(it, "textRanges", { get: function () { return charColl(story, it.frameStart, it.frameLen); } });
  defp(it, "textRange", { get: function () { return new Range(story, it.frameStart(), it.frameLen()); } });
  defp(it, "paragraphs", { get: function () { return new Range(story, it.frameStart(), it.frameLen()).paragraphs; } });
  defp(it, "lines", { get: function () {
    var len = it.frameLen(), vis = it.visibleChars === undefined ? len : Math.min(len, it.visibleChars);
    if (vis <= 0) return [];
    var text = story.text().substr(it.frameStart(), vis);
    // Trailing paragraph returns are not part of a composed line.
    while (text.length && text.charAt(text.length - 1) === "\r") text = text.substring(0, text.length - 1);
    return text.length ? [{ start: it.frameStart(), length: text.length }] : [];
  } });
  defp(it, "nextFrame", { get: function () {
    if (it.kind === "TextType.POINTTEXT" || it.kind === "TextType.PATHTEXT") throw new Error("The requested property is not available for this type of text frame.");
    var idx = story.frames.indexOf(it);
    return story.frames[idx + 1] || it;
  } });
  it.story = { get textRange() { return new Range(story, 0, story.chars.length); },
               get textRanges() { return charColl(story, function () { return 0; }, function () { return story.chars.length; }); },
               get characters() { return charColl(story, function () { return 0; }, function () { return story.chars.length; }); },
               get textFrames() { return story.frames.slice(0); } };
  it.createOutline = function () {
    var g = makeItem({ type: "GroupItem", b: spec.b, children: [{ type: "CompoundPathItem" }, { type: "CompoundPathItem" }] }, it.parent, it.layer);
    var sib = it.parent.pageItems; sib[sib.indexOf(it)] = g;
    delete BYUUID[it.uuid];
    return g;
  };
}

function makeItem(spec, parent, layer) {
  var b = spec.b || [0, 0, 10, 10];
  var it = {
    typename: spec.type || "PathItem", name: spec.name || "", uuid: spec.uuid || String(NEXT.uuid++),
    note: spec.note || "", hidden: !!spec.hidden, locked: !!spec.locked, guides: !!spec.guides,
    parent: parent, layer: layer, visibleBounds: yup(b), geometricBounds: yup(spec.gb || b),
    width: spec.width !== undefined ? spec.width : b[2] - b[0], height: spec.height !== undefined ? spec.height : b[3] - b[1],
    opacity: 100, blendingMode: "BlendModes.NORMAL", pageItems: [], pathItems: [], compoundPathItems: [], layers: []
  };
  it.filled = !!spec.fill; it.fillColor = makeColor(spec.fill);
  it.stroked = !!spec.stroke; it.strokeColor = makeColor(spec.stroke); it.strokeWidth = spec.strokeWidth === undefined ? 1 : spec.strokeWidth;
  it.fillOverprint = !!spec.fillOverprint; it.strokeOverprint = !!spec.strokeOverprint;
  if (spec.points !== undefined) {
    it.pathPoints = []; for (var p = 0; p < spec.points; p++) it.pathPoints.push({ anchor: [b[0] + p, -b[1]] });
  } else { it.pathPoints = [{ anchor: [0, 0] }, { anchor: [1, 0] }, { anchor: [1, 1] }, { anchor: [0, 1] }]; }
  if (it.typename === "GroupItem") it.clipped = !!spec.clipped;
  if (spec.clipping) it.clipping = true;
  if (it.typename === "TextFrame") makeTextFrame(spec, it);
  if (it.typename === "PlacedItem" || it.typename === "RasterItem") {
    var px = spec.pixels || [100, 100];
    it.boundingBox = [0, px[1], px[0], 0];
    var sx = (b[2] - b[0]) / px[0], sy = (b[3] - b[1]) / px[1];
    it.matrix = { mValueA: sx, mValueB: 0, mValueC: 0, mValueD: it.typename === "PlacedItem" ? -sy : sy, mValueTX: 0, mValueTY: 0 };
    if (it.typename === "PlacedItem") {
      if (spec.file === null) defp(it, "file", { get: function () { throw new Error("There is no file associated with this item"); } });
      else it.file = { fsName: spec.file || "/img/photo.png", exists: spec.exists !== false };
    } else {
      it.embedded = spec.embedded !== false;
      it.imageColorSpace = spec.colorSpace || "ImageColorSpace.CMYK";
      it.status = spec.status || "RasterLinkState.DATAFROMFILE";
      if (!it.embedded) it.file = { fsName: spec.file || "/img/linked.tif", exists: spec.exists !== false };
    }
  }
  var kids = spec.children || [];
  for (var k = 0; k < kids.length; k++) {
    var child = makeItem(kids[k], it, layer);
    if (it.typename === "CompoundPathItem") it.pathItems.push(child);
    else it.pageItems.push(child);
    if (child.typename === "CompoundPathItem") it.compoundPathItems.push(child);
  }
  BYUUID[it.uuid] = it;
  if (it.name) BYNAME[it.name] = it;
  return it;
}

function build(spec) {
  (spec.fonts || ["MyriadPro-Regular", "Georgia", "ArialMT"]).forEach(function (f) {
    var fo = typeof f === "string" ? fontObj(f) : fontObj(f.name, f.family, f.style);
    FONTS[fo.name] = fo;
  });
  (spec.missing_fonts || []).forEach(function (m) {
    // m: {name, family, placeholder_family} - used in text, not installed
    FONTS.__used[m.name] = fontObj(m.name, m.family || m.name);
    if (m.placeholder_family) PLACEHOLDERS[m.name] = fontObj(m.name, m.placeholder_family);
  });
  var doc = { typename: "Document", name: spec.name || "test.ai", layers: [], artboards: [], _active: 0,
    selection: [], documentColorSpace: spec.cmyk ? "DocumentColorSpace.CMYK" : "DocumentColorSpace.RGB",
    rulerUnits: "RulerUnits.Points", saved: true, fullName: null,
    rasterEffectSettings: { resolution: spec.raster_ppi || 300 } };
  function makeLayer(ls, parent) {
    var L = { typename: "Layer", name: ls.name, visible: ls.visible !== false, locked: !!ls.locked,
      parent: parent, layers: [], pageItems: [], compoundPathItems: [] };
    (ls.layers || []).forEach(function (s) { L.layers.push(makeLayer(s, L)); });
    (ls.items || []).forEach(function (is) {
      var it = makeItem(is, L, L);
      L.pageItems.push(it);
      if (it.typename === "CompoundPathItem") L.compoundPathItems.push(it);
    });
    return L;
  }
  (spec.layers || []).forEach(function (l) { doc.layers.push(makeLayer(l, doc)); });
  // Threads: [[uuidA, uuidB], ...] - B continues A's story.
  (spec.threads || []).forEach(function (pair) {
    var a = BYUUID[pair[0]], b = BYUUID[pair[1]];
    b.story_.frames.splice(b.story_.frames.indexOf(b), 1);
    var vis = b.visibleChars;
    makeTextFrame({ story: a.story_, kind: b.kind, visible: vis }, b);
  });
  (spec.artboards || [{ name: "Artboard 1", b: [0, 0, 600, 400] }]).forEach(function (ab) {
    doc.artboards.push({ name: ab.name, artboardRect: yup(ab.b) });
  });
  doc.artboards.getActiveArtboardIndex = function () { return doc._active; };
  doc.artboards.setActiveArtboardIndex = function (i) { doc._active = i; };
  doc.getPageItemFromUuid = function (u) {
    var hit = BYUUID[u];
    if (!hit) throw new Error("an Illustrator error occurred: 1346458189 ('MRAP')");
    return hit;
  };
  Object.defineProperty(doc, "textFrames", { get: function () {
    var out = [];
    function walk(items) { items.forEach(function (it) {
      if (it.typename === "TextFrame" && BYUUID[it.uuid] === it) out.push(it);
      if (it.typename === "GroupItem") walk(it.pageItems);
    }); }
    function walkL(L) { L.layers.forEach(walkL); walk(L.pageItems); }
    doc.layers.forEach(walkL);
    return out;
  } });
  doc.visibleBounds = [0, 0, 600, -400];
  return doc;
}
"""


def run_jsx(libs: list[str], spec: dict, expr: str) -> dict:
    """Load ``libs`` (file stems) over the fixture, build ``spec``, eval ``expr``.

    Returns {"ok": True, "value": ...} or {"ok": False, "message", "user_error"}.
    """
    src = "\n".join((SCRIPTS / f"{name}.jsx").read_text(encoding="utf-8") for name in libs)
    harness = f"""
{FIXTURE}
{src}
var doc = build({json.dumps(spec)});
try {{
  console.log(JSON.stringify({{ ok: true, value: (function () {{ return {expr}; }})() }}));
}} catch (e) {{
  console.log(JSON.stringify({{ ok: false, message: String(e.message || e), user_error: !!e.dmUserError }}));
}}
"""
    out = subprocess.run(["node", "-e", harness], check=True, capture_output=True, text=True, cwd=ROOT)
    return json.loads(out.stdout)


def jsx_value(libs: list[str], spec: dict, expr: str):
    res = run_jsx(libs, spec, expr)
    assert res["ok"], res
    return res["value"]
