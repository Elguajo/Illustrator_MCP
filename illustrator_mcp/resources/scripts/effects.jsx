/**
 * effects.jsx - Live effects for the typed illustrator_effects tool:
 * apply drop shadow and Gaussian blur, remove effects. Items are addressed
 * by native PageItem.uuid (see doc_model.jsx).
 * Part of Illustrator MCP Standard Library
 * @version 1.0.0
 *
 * ROUTE (verified live on Illustrator 30.8.1)
 *   Apply: PageItem.applyEffect(LiveEffectXML). It returns nothing and
 *   appends to the item's appearance, so the only read-back is the item's
 *   visibleBounds, which grow for every shadow or blur that has a radius or
 *   offset. Works on paths, compound paths (on the container, not the child
 *   paths: an effect is not paint), groups and text frames. Malformed XML
 *   throws; an unknown effect name is accepted silently, so the XML is only
 *   ever built here from fixed templates, never taken from a caller.
 *   Drop shadow keys: horz/vert offsets (pt, +vert = down), opac 0..1,
 *   blur (pt), blnd 0 normal / 1 multiply / 2 screen, csrc 0 = use the sclr
 *   color, 1 = darkness mode with dark 0..100. sclr is a Fill entry whose
 *   color string is "5 r g b" (RGB 0..1) or "1 c m y k" (CMYK 0..1).
 *   Gaussian blur: a single "blur" radius in points.
 *
 *   Read: not possible. No DOM property exposes an item's effects, FXG
 *   export is gone (FXGSaveOptions silently writes .ai) and the .ai private
 *   data is compressed. Results therefore report visible bounds before and
 *   after, never effect parameters.
 *
 *   Remove: there is no removeEffect(), and "Reduce to Basic Appearance" is
 *   an Appearance-panel flyout item that executeMenuCommand cannot reach. The
 *   document's "[Default]" graphic style is applied instead (it clears every
 *   live effect and every extra fill/stroke), then the item's basic paint,
 *   opacity, blend mode, isolation and knockout are restored from a snapshot
 *   and read back. Children of a group and character colors of text are not
 *   touched by the style. Stroke alignment is the one thing that cannot be
 *   restored: it is not in the DOM, and [Default] re-aligns a centered stroke
 *   to the inside (measured: visible bounds and rendered pixels). Stroked
 *   paths are therefore skipped unless P.allow_stroke_realign is true.
 *   Replaying the panel command through a generated action
 *   (app.loadAction + app.doScript) was tried and hung Illustrator at 100%
 *   CPU with no way to interrupt it from script, so it is not used.
 *
 * SAFETY
 *   Locked or hidden items (or items inside a locked/hidden group or layer)
 *   are never modified: they are reported in skipped_objects.
 *
 * ES3 only (see docs/ARCHITECTURE_DOCTRINE.md).
 * @requires doc_model
 */

// ==================== Primitives ====================

/**
 * Why an item must not be edited, walking up through groups and layers.
 * Returns "locked" | "hidden" | "locked_layer" | "hidden_layer" | "locked_parent" |
 * "hidden_parent" | null.
 */
function fxEditBlocker(item) {
    try { if (item.locked) return "locked"; } catch (e) {}
    try { if (item.hidden) return "hidden"; } catch (e) {}
    var cur = null;
    try { cur = item.parent; } catch (e) { cur = null; }
    while (cur && cur.typename !== "Document") {
        if (cur.typename === "Layer") {
            try { if (cur.locked) return "locked_layer"; } catch (e) {}
            try { if (!cur.visible) return "hidden_layer"; } catch (e) {}
        } else {
            try { if (cur.locked) return "locked_parent"; } catch (e) {}
            try { if (cur.hidden) return "hidden_parent"; } catch (e) {}
        }
        try { cur = cur.parent; } catch (e) { cur = null; }
    }
    return null;
}

/** Fixed-point number for LiveEffect data strings (no exponent notation). */
function fxNum(n) {
    var r = Math.round(n * 10000) / 10000;
    if (Math.abs(r) < 0.0001) r = 0;
    return String(r);
}

/** Effect color for the sclr Fill entry. c: {model:"rgb", r,g,b 0..255} | {model:"cmyk", c,m,y,k 0..100}. */
function fxFillColorString(c) {
    if (c.model === "cmyk") {
        return "1 " + fxNum(c.c / 100) + " " + fxNum(c.m / 100) + " " + fxNum(c.y / 100) + " " + fxNum(c.k / 100);
    }
    return "5 " + fxNum(c.r / 255) + " " + fxNum(c.g / 255) + " " + fxNum(c.b / 255);
}

var FX_BLEND_CODES = { normal: 0, multiply: 1, screen: 2 };

/**
 * LiveEffect XML for a drop shadow.
 * S: {offset_x, offset_y, blur, opacity 0..100, blend_mode, color?, darkness?}
 */
function fxDropShadowXml(S) {
    var blend = FX_BLEND_CODES.hasOwnProperty(S.blend_mode) ? FX_BLEND_CODES[S.blend_mode] : 1;
    var useDarkness = S.darkness !== undefined && S.darkness !== null;
    var data = "R horz " + fxNum(S.offset_x) +
        " R vert " + fxNum(S.offset_y) +
        " R opac " + fxNum(S.opacity / 100) +
        " R blur " + fxNum(S.blur) +
        " R dark " + fxNum(useDarkness ? S.darkness : 100) +
        " I blnd " + blend +
        " I csrc " + (useDarkness ? 1 : 0) +
        " B pair 1 B usePSLBlur 1 ";
    var color = S.color || { model: "rgb", r: 0, g: 0, b: 0 };
    return '<LiveEffect name="Adobe Drop Shadow"><Dict data="' + data + '">' +
        '<Entry name="sclr" valueType="F"><Fill color="' + fxFillColorString(color) + '"/></Entry>' +
        '</Dict></LiveEffect>';
}

/** LiveEffect XML for a Gaussian blur. S: {radius} in points. */
function fxGaussianBlurXml(S) {
    return '<LiveEffect name="Adobe PSL Gaussian Blur"><Dict data="R blur ' + fxNum(S.radius) + ' "/></LiveEffect>';
}

function fxEffectXml(P) {
    if (P.effect === "drop_shadow") return fxDropShadowXml(P.drop_shadow || {});
    if (P.effect === "gaussian_blur") return fxGaussianBlurXml(P.gaussian_blur || {});
    dmFail("Unknown effect '" + P.effect + "'. Supported: drop_shadow, gaussian_blur");
}

/** True when the effect must enlarge visible bounds, so an unchanged rect means it did not apply. */
function fxExpectsGrowth(P) {
    if (P.effect === "gaussian_blur") return (P.gaussian_blur || {}).radius > 0;
    var S = P.drop_shadow || {};
    return S.blur > 0 || S.offset_x !== 0 || S.offset_y !== 0;
}

function fxSameRect(a, b) {
    if (!a || !b) return false;
    for (var i = 0; i < 4; i++) {
        if (Math.abs(a[i] - b[i]) > 0.01) return false;
    }
    return true;
}

// ==================== Appearance snapshot ====================

var FX_CONTAINER_PROPS = ["opacity", "blendingMode", "isIsolated", "artworkKnockout"];
var FX_PAINT_PROPS = ["strokeWidth", "strokeDashOffset", "strokeCap", "strokeJoin",
    "strokeMiterLimit", "fillOverprint", "strokeOverprint"];

/**
 * Order-stable comparison key for snapshot values. DOM colors (typename
 * "...Color") go through dmColor; any other DOM value, notably enums such as
 * StrokeCap.BUTTENDCAP whose .typename is the string "StrokeCap.BUTTENDCAP",
 * compares by its string form; plain objects (dmColor output, gradient
 * stops) and arrays recurse.
 */
function fxKey(v) {
    if (v === null || v === undefined) return "null";
    if (typeof v === "number") return String(Math.round(v * 1000) / 1000);
    if (typeof v !== "object") return String(v);
    if (v instanceof Array) {
        var parts = [];
        for (var i = 0; i < v.length; i++) parts.push(fxKey(v[i]));
        return "[" + parts.join(",") + "]";
    }
    var tn = v.typename;
    if (typeof tn === "string" && tn !== "") {
        return /Color$/.test(tn) ? fxKey(dmColor(v)) : String(v);
    }
    var keys = [];
    for (var k in v) {
        if (v.hasOwnProperty(k)) keys.push(k);
    }
    keys.sort();
    var out = [];
    for (var j = 0; j < keys.length; j++) out.push(keys[j] + ":" + fxKey(v[keys[j]]));
    return "{" + out.join(",") + "}";
}

function fxSnapPaint(p) {
    var s = { filled: false, stroked: false, props: {} };
    try { s.filled = !!p.filled; } catch (e) {}
    try { s.stroked = !!p.stroked; } catch (e) {}
    try { if (s.filled) s.fillColor = p.fillColor; } catch (e) {}
    try { if (s.stroked) s.strokeColor = p.strokeColor; } catch (e) {}
    try { s.strokeDashes = p.strokeDashes; } catch (e) {}
    for (var i = 0; i < FX_PAINT_PROPS.length; i++) {
        try { s.props[FX_PAINT_PROPS[i]] = p[FX_PAINT_PROPS[i]]; } catch (e) {}
    }
    return s;
}

/**
 * Everything the [Default] graphic style resets that the item owned:
 * container transparency plus the basic paint of every paint carrier
 * (paintTargetsOf semantics: a compound path paints through its children).
 */
function fxSnapshot(item) {
    var snap = { container: {}, paint: [] };
    for (var i = 0; i < FX_CONTAINER_PROPS.length; i++) {
        try { snap.container[FX_CONTAINER_PROPS[i]] = item[FX_CONTAINER_PROPS[i]]; } catch (e) {}
    }
    var carriers = fxPaintCarriers(item);
    for (var j = 0; j < carriers.length; j++) snap.paint.push(fxSnapPaint(carriers[j]));
    return snap;
}

function fxPaintCarriers(item) {
    if (item.typename === "PathItem") return [item];
    if (item.typename === "CompoundPathItem") {
        var out = [];
        for (var i = 0; i < item.pathItems.length; i++) out.push(item.pathItems[i]);
        return out;
    }
    return [];
}

function fxRestorePaint(p, s) {
    var i;
    for (i = 0; i < FX_PAINT_PROPS.length; i++) {
        var k = FX_PAINT_PROPS[i];
        if (s.props.hasOwnProperty(k) && s.props[k] !== undefined) {
            try { p[k] = s.props[k]; } catch (e) {}
        }
    }
    try { if (s.strokeDashes !== undefined) p.strokeDashes = s.strokeDashes; } catch (e) {}
    if (s.filled && s.fillColor) { try { p.fillColor = s.fillColor; } catch (e) {} }
    if (s.stroked && s.strokeColor) { try { p.strokeColor = s.strokeColor; } catch (e) {} }
    // Flags last: assigning a color can switch filled/stroked on.
    try { p.filled = s.filled; } catch (e) {}
    try { p.stroked = s.stroked; } catch (e) {}
}

function fxRestore(item, snap) {
    for (var k in snap.container) {
        if (snap.container.hasOwnProperty(k) && snap.container[k] !== undefined) {
            try { item[k] = snap.container[k]; } catch (e) {}
        }
    }
    var carriers = fxPaintCarriers(item);
    for (var i = 0; i < carriers.length && i < snap.paint.length; i++) fxRestorePaint(carriers[i], snap.paint[i]);
}

/** Names of snapshot fields whose read-back differs from the snapshot. */
function fxDiff(a, b) {
    var diff = [];
    var k;
    for (k in a.container) {
        if (a.container.hasOwnProperty(k) && fxKey(a.container[k]) !== fxKey(b.container[k])) diff.push(k);
    }
    if (a.paint.length !== b.paint.length) {
        diff.push("path_count");
        return diff;
    }
    for (var i = 0; i < a.paint.length; i++) {
        var pa = a.paint[i];
        var pb = b.paint[i];
        var prefix = a.paint.length > 1 ? "path" + i + "." : "";
        if (pa.filled !== pb.filled) diff.push(prefix + "filled");
        if (pa.stroked !== pb.stroked) diff.push(prefix + "stroked");
        if (pa.filled && fxKey(pa.fillColor) !== fxKey(pb.fillColor)) diff.push(prefix + "fillColor");
        if (pa.stroked && fxKey(pa.strokeColor) !== fxKey(pb.strokeColor)) diff.push(prefix + "strokeColor");
        if (pa.stroked) {
            if (fxKey(pa.strokeDashes) !== fxKey(pb.strokeDashes)) diff.push(prefix + "strokeDashes");
            for (k in pa.props) {
                if (pa.props.hasOwnProperty(k) && fxKey(pa.props[k]) !== fxKey(pb.props[k])) diff.push(prefix + k);
            }
        }
    }
    return diff;
}

// ==================== Clear (remove) ====================

/** True when clearing would re-align a stroke: some paint carrier is stroked. */
function fxHasStroke(item) {
    var carriers = fxPaintCarriers(item);
    for (var i = 0; i < carriers.length; i++) {
        try { if (carriers[i].stroked) return true; } catch (e) {}
    }
    return false;
}

function fxDefaultStyle(doc) {
    try {
        for (var i = 0; i < doc.graphicStyles.length; i++) {
            if (doc.graphicStyles[i].name === "[Default]") return doc.graphicStyles[i];
        }
    } catch (e) {}
    return null;
}

/**
 * True when visibleBounds already exceed what the geometry explains (plus a
 * generous stroke allowance for paths, 1 pt for anything else): the item
 * carries effects, or for a group, stroked children. Illustrator pads an
 * effect's bounds conservatively (a 2 pt blur adds about 36 pt per side), so
 * a new effect on a padded item may leave the bounds unchanged.
 */
function fxPadded(item) {
    var vb, gb;
    try { vb = item.visibleBounds; gb = item.geometricBounds; } catch (e) { return false; }
    var pad = 1;
    var carriers = fxPaintCarriers(item);
    for (var i = 0; i < carriers.length; i++) {
        try {
            if (carriers[i].stroked) {
                var p = carriers[i].strokeWidth * Math.max(carriers[i].strokeMiterLimit, 1) + 1;
                if (p > pad) pad = p;
            }
        } catch (e) {}
    }
    return vb[0] < gb[0] - pad || vb[1] > gb[1] + pad || vb[2] > gb[2] + pad || vb[3] < gb[3] - pad;
}

/**
 * A path still padded after clearing kept an effect: the document's
 * [Default] style is not a basic appearance. Only checked for paths, where
 * the geometry fully explains the basic appearance.
 */
function fxEffectsRemain(item) {
    if (item.typename !== "PathItem" && item.typename !== "CompoundPathItem") return false;
    return fxPadded(item);
}

/**
 * Clear every live effect on one item and restore its basic appearance.
 * Returns {ok: true} or {ok: false, reason}.
 */
function fxClear(style, item) {
    var snap = fxSnapshot(item);
    style.applyTo(item);
    fxRestore(item, snap);
    var diff = fxDiff(snap, fxSnapshot(item));
    if (diff.length) return { ok: false, reason: "appearance_not_restored: " + diff.join(", ") };
    if (fxEffectsRemain(item)) {
        return { ok: false, reason: "effects_remain: the document's [Default] graphic style is not a basic appearance" };
    }
    return { ok: true };
}

// ==================== Tool entry points ====================

/**
 * Apply one effect, or remove all effects, on items addressed by uuid.
 * P: {action: "apply"|"remove", uuids: [...], document?, effect?, drop_shadow?,
 *     gaussian_blur?, replace?, allow_stroke_realign?}
 */
function fxRun(doc, P) {
    var action = P.action;
    if (action !== "apply" && action !== "remove") dmFail("Unknown action '" + action + "'");
    // uuids are numbered per document and collide across open documents.
    if (P.document && P.document !== String(doc.name)) {
        dmFail("These uuids came from document '" + P.document + "' but the active document is '" +
            doc.name + "'. Switch back with illustrator_document(action='switch', name='" + P.document +
            "') or call illustrator_inspect again on this document.");
    }
    var xml = action === "apply" ? fxEffectXml(P) : null;
    var needsClear = action === "remove" || !!P.replace;
    var style = needsClear ? fxDefaultStyle(doc) : null;
    if (needsClear && !style) {
        dmFail("This document has no '[Default]' graphic style, which removal relies on. " +
            "Restore it in the Graphic Styles panel, or remove effects by hand.");
    }

    var res = dmResolveUuids(doc, P.uuids || []);
    var failed = res.missing.slice(0);
    var skipped = [];
    var objects = [];
    for (var i = 0; i < res.items.length; i++) {
        var item = res.items[i];
        var uuid = dmUuid(item);
        var blocker = fxEditBlocker(item);
        if (blocker) {
            skipped.push({ uuid: uuid, reason: blocker });
            continue;
        }
        if (needsClear && !P.allow_stroke_realign && fxHasStroke(item)) {
            skipped.push({ uuid: uuid, reason: "stroked_path: clearing effects would re-align its stroke " +
                "(stroke alignment is not scriptable); pass allow_stroke_realign=true to accept that" });
            continue;
        }
        var before = dmVisibleBounds(item);
        var entry = { uuid: uuid, type: item.typename, visible_bounds_before: before };
        try {
            if (needsClear) {
                var cleared = fxClear(style, item);
                if (!cleared.ok) {
                    failed.push({ uuid: uuid, reason: cleared.reason });
                    continue;
                }
            }
            if (action === "apply") {
                var base = dmVisibleBounds(item);
                var padded = fxPadded(item);
                item.applyEffect(xml);
                var grown = dmVisibleBounds(item);
                if (fxExpectsGrowth(P)) {
                    if (!fxSameRect(base, grown)) {
                        entry.verified = true;
                    } else if (padded) {
                        entry.verified = false;
                        entry.note = "The object's bounds were already padded by existing effects (or stroked " +
                            "children), so the new effect fit inside them and could not be verified";
                    } else {
                        failed.push({ uuid: uuid, reason: "effect_not_applied: visible bounds did not change" });
                        continue;
                    }
                } else {
                    entry.verified = false;
                    entry.note = "Zero offset and zero blur leave the bounds unchanged, so the effect could not be verified";
                }
            }
        } catch (e) {
            failed.push({ uuid: uuid, reason: "error: " + String(e.message || e) });
            continue;
        }
        entry.visible_bounds_after = dmVisibleBounds(item);
        objects.push(entry);
    }

    var out = {
        action: action,
        document: dmDocumentRef(doc),
        success_count: objects.length,
        fail_count: failed.length,
        failed_objects: failed,
        skipped_objects: skipped,
        objects: objects
    };
    if (action === "apply") out.effect = P.effect;
    var checked = 0;
    for (var vi = 0; vi < objects.length; vi++) {
        if (action === "remove" || objects[vi].verified === true) checked++;
    }
    out.verification = {
        method: action === "remove" ? "appearance_read_back" : "visible_bounds_proxy",
        checked_count: checked, unverified_count: objects.length - checked,
        status: objects.length > checked ? "partial" : "performed"
    };
    return out;
}
