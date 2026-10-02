/**
 * preflight.jsx - Scoped preflight fact report for illustrator_preflight_check.
 * Part of Illustrator MCP Standard Library
 * @version 2.0.0
 *
 * CONTRACT
 *   Every check has an id "<scope>.<name>". Each requested check ends up
 *   in exactly one of:
 *     checks_run      - it ran over every object it applies to;
 *     checks_skipped  - {check, reason}: not requested, disabled, failed,
 *                       or only partly done (budget, unreadable objects).
 *   So an empty category is only "clean" when its checks are in
 *   checks_run. Findings are aggregated per tag with affected_objects
 *   [{uuid, name, type, layer_path, ...raw facts}]; facts, not verdicts.
 *   check_notes records known blind spots of checks that did run.
 *
 * Bounds are canvas-global points, Y-down (see doc_model.jsx).
 * Hidden objects (themselves or through a layer/group) are not checked,
 * except for the informational objects.hidden / objects.locked tags.
 * Read-only: nothing in the document is modified.
 *
 * ES3 only (see docs/ARCHITECTURE_DOCTRINE.md).
 */

var PF_CHECKS = [
    { id: "document.empty_artboard", severity: "warning" },
    { id: "document.raster_effects_resolution", severity: "warning" },
    { id: "objects.off_artboard", severity: "warning" },
    { id: "objects.partially_off_artboard", severity: "info" },
    { id: "objects.zero_size", severity: "warning" },
    { id: "objects.hairline_stroke", severity: "warning" },
    { id: "objects.stray_point", severity: "warning" },
    { id: "objects.locked", severity: "info" },
    { id: "objects.hidden", severity: "info" },
    { id: "text.missing_font", severity: "error" },
    { id: "text.overset", severity: "error" },
    { id: "text.empty_text", severity: "warning" },
    { id: "images.low_ppi", severity: "warning" },
    { id: "links.missing", severity: "error" },
    { id: "links.modified", severity: "warning" },
    { id: "colors.registration", severity: "error" },
    { id: "colors.overprint_white", severity: "error" },
    { id: "colors.rich_black_text", severity: "warning" },
    { id: "colors.total_ink", severity: "warning" },
    { id: "colors.color_model_mismatch", severity: "warning" },
    { id: "colors.spot_colors", severity: "info" }
];

var PF_SCOPES = ["document", "objects", "text", "images", "links", "colors"];

var PF_RASTER_EXT = /\.(png|jpe?g|tiff?|psd|gif|bmp|webp|heic|avif|jp2)$/i;

// ==================== State ====================

function pfState(P) {
    var scopes = P.scopes && P.scopes.length ? P.scopes : PF_SCOPES;
    var wanted = {};
    for (var s = 0; s < scopes.length; s++) wanted[scopes[s]] = true;
    var disabled = {};
    if (P.check_zero_size === false) disabled["objects.zero_size"] = true;
    if (P.check_empty_text === false) disabled["text.empty_text"] = true;
    if (P.check_locked === false) disabled["objects.locked"] = true;
    var checks = {};
    for (var i = 0; i < PF_CHECKS.length; i++) {
        var c = PF_CHECKS[i];
        var scope = c.id.split(".")[0];
        var st = "run";
        if (!wanted[scope]) st = "scope_not_requested";
        else if (disabled[c.id]) st = "disabled_by_parameter";
        checks[c.id] = { id: c.id, scope: scope, severity: c.severity, status: st,
            affected: [], count: 0, unreadable: 0, unreadable_samples: [] };
    }
    return { P: P, checks: checks, notes: {}, maxAffected: P.max_affected || 50 };
}

function pfOn(S, id) {
    return S.checks[id].status === "run";
}

function pfAdd(S, id, facts) {
    var c = S.checks[id];
    c.count++;
    if (c.affected.length < S.maxAffected) c.affected.push(facts);
}

function pfUnreadable(S, id, item, e) {
    var c = S.checks[id];
    c.unreadable++;
    if (c.unreadable_samples.length < 5) {
        c.unreadable_samples.push({ uuid: dmUuid(item), error: String(e && e.message || e) });
    }
}

function pfBase(item, ctx) {
    var f = { uuid: dmUuid(item), name: item.name || "", type: item.typename, layer_path: ctx.layer_path };
    var mcpId = dmMcpId(item);
    if (mcpId) f.mcp_id = mcpId;
    return f;
}

function pfExtend(base, extra) {
    var o = {};
    var k;
    for (k in base) if (base.hasOwnProperty(k)) o[k] = base[k];
    for (k in extra) if (extra.hasOwnProperty(k)) o[k] = extra[k];
    return o;
}

// ==================== Geometry ====================

function pfItemBounds(item, P) {
    if (P.bounds_source === "clipping_path" && item.typename === "GroupItem") {
        try {
            if (item.clipped) {
                for (var i = 0; i < item.pageItems.length; i++) {
                    var k = item.pageItems[i];
                    if (k.clipping || (k.typename === "CompoundPathItem" && k.pathItems.length && k.pathItems[0].clipping)) {
                        return dmToYDown(k.geometricBounds);
                    }
                }
            }
        } catch (e) {}
    }
    return dmToYDown(P.bounds_type === "geometric" ? item.geometricBounds : item.visibleBounds);
}

function pfContains(outer, inner) {
    return inner[0] >= outer[0] && inner[1] >= outer[1] && inner[2] <= outer[2] && inner[3] <= outer[3];
}

function pfCenterIn(b, rect) {
    var cx = (b[0] + b[2]) / 2;
    var cy = (b[1] + b[3]) / 2;
    return cx >= rect[0] && cx <= rect[2] && cy >= rect[1] && cy <= rect[3];
}

// ==================== Colors ====================

function pfIsWhite(c) {
    if (!c) return false;
    var t = c.typename;
    if (t === "CMYKColor") return c.cyan === 0 && c.magenta === 0 && c.yellow === 0 && c.black === 0;
    if (t === "GrayColor") return c.gray === 0;
    if (t === "RGBColor") return c.red >= 255 && c.green >= 255 && c.blue >= 255;
    if (t === "SpotColor") return c.tint === 0;
    return false;
}

function pfIsRegistration(c) {
    if (!c || c.typename !== "SpotColor") return false;
    try {
        if (String(c.spot.colorType).indexOf("REGISTRATION") >= 0) return true;
        return c.spot.name === "[Registration]";
    } catch (e) {
        return false;
    }
}

function pfInk(c) {
    if (!c || c.typename !== "CMYKColor") return null;
    return c.cyan + c.magenta + c.yellow + c.black;
}

function pfNoteSpot(S, c) {
    if (!c || c.typename !== "SpotColor") return;
    try {
        if (pfIsRegistration(c)) return;
        var name = c.spot.name;
        S.spots[name] = (S.spots[name] || 0) + 1;
    } catch (e) {}
}

// ==================== Per-item checks ====================

function pfCheckPaint(S, item, ctx, base) {
    var src = dmPaintSource(item);
    if (!src) return;
    var fill = null;
    var stroke = null;
    try {
        if (src.filled) fill = src.fillColor;
        if (src.stroked) stroke = src.strokeColor;
    } catch (e) {
        if (pfOn(S, "colors.registration")) pfUnreadable(S, "colors.registration", item, e);
        return;
    }

    if (pfOn(S, "objects.hairline_stroke") && stroke) {
        try {
            var w = src.strokeWidth;
            if (w < S.P.hairline_width) pfAdd(S, "objects.hairline_stroke", pfExtend(base, { stroke_width: dmRound(w) }));
        } catch (e2) { pfUnreadable(S, "objects.hairline_stroke", item, e2); }
    }
    if (pfOn(S, "colors.registration")) {
        var reg = [];
        if (pfIsRegistration(fill)) reg.push("fill");
        if (pfIsRegistration(stroke)) reg.push("stroke");
        if (reg.length) pfAdd(S, "colors.registration", pfExtend(base, { paint: reg }));
    }
    if (pfOn(S, "colors.overprint_white")) {
        try {
            var wo = [];
            if (fill && src.fillOverprint && pfIsWhite(fill)) wo.push("fill");
            if (stroke && src.strokeOverprint && pfIsWhite(stroke)) wo.push("stroke");
            if (wo.length) pfAdd(S, "colors.overprint_white", pfExtend(base, { paint: wo }));
        } catch (e3) { pfUnreadable(S, "colors.overprint_white", item, e3); }
    }
    if (pfOn(S, "colors.total_ink")) {
        var inks = [];
        var fi = pfInk(fill);
        var si = pfInk(stroke);
        if (fi !== null && fi > S.P.total_ink_limit) inks.push({ paint: "fill", total_ink: dmRound(fi), color: dmColor(fill) });
        if (si !== null && si > S.P.total_ink_limit) inks.push({ paint: "stroke", total_ink: dmRound(si), color: dmColor(stroke) });
        if (inks.length) pfAdd(S, "colors.total_ink", pfExtend(base, { inks: inks }));
    }
    if (pfOn(S, "colors.spot_colors")) {
        pfNoteSpot(S, fill);
        pfNoteSpot(S, stroke);
    }
}

function pfCheckText(S, tf, ctx, base) {
    var wantFonts = pfOn(S, "text.missing_font");
    var wantRich = pfOn(S, "colors.rich_black_text");
    var wantReg = pfOn(S, "colors.registration");
    var contents = null;
    try { contents = String(tf.contents); } catch (e) {}

    if (pfOn(S, "text.empty_text") && contents !== null && contents.replace(/[\s\u0003]/g, "").length === 0) {
        pfAdd(S, "text.empty_text", base);
    }
    if (pfOn(S, "text.overset")) {
        try {
            var ov = dmTextOverflow(tf);
            if (ov && ov.overset) {
                pfAdd(S, "text.overset", pfExtend(base, { kind: String(tf.kind).replace("TextType.", "").toLowerCase(),
                    overset_chars: ov.overset_chars, overset_preview: ov.overset_preview, bounds: dmVisibleBounds(tf) }));
            }
        } catch (e2) { pfUnreadable(S, "text.overset", tf, e2); }
    }
    if (!wantFonts && !wantRich && !wantReg) return;

    var budget = S.textBudget - S.textChars;
    if (budget <= 0) {
        S.textUnscanned++;
        return;
    }
    var fr;
    try {
        fr = dmFontRuns(tf, { max_chars: budget, fill: wantRich || wantReg });
    } catch (e3) {
        if (wantFonts) pfUnreadable(S, "text.missing_font", tf, e3);
        return;
    }
    S.textChars += fr.scanned;
    if (fr.truncated) S.textUnscanned++;

    var missing = {};
    var missingList = [];
    var rich = [];
    var regChars = 0;
    for (var r = 0; r < fr.runs.length; r++) {
        var run = fr.runs[r];
        var used = S.fonts[run.font];
        if (!used) {
            used = S.fonts[run.font] = { font: run.font, family: run.family, style: run.style,
                available: run.available, chars: 0, frames: {} };
            if (!run.available) used.missing_reason = run.missing_reason;
        }
        used.chars += run.length;
        used.frames[base.uuid] = true;
        if (!run.available) {
            if (!missing[run.font]) {
                missing[run.font] = { font: run.font, family: run.family, reason: run.missing_reason, chars: 0 };
                missingList.push(missing[run.font]);
            }
            missing[run.font].chars += run.length;
        }
        if (run.fill) {
            var f = run.fill;
            if (wantRich && f.model === "cmyk" && f.k >= 90 && (f.c + f.m + f.y) > 0 && run.size <= S.P.rich_black_max_size) {
                rich.push({ start: run.start, length: run.length, size: run.size, color: f });
            }
            if (f.model === "spot" && (f.name === "[Registration]")) regChars += run.length;
        }
    }
    if (wantFonts && missingList.length) pfAdd(S, "text.missing_font", pfExtend(base, { fonts: missingList }));
    if (wantRich && rich.length) pfAdd(S, "colors.rich_black_text", pfExtend(base, { runs: rich.slice(0, 10) }));
    if (wantReg && regChars) pfAdd(S, "colors.registration", pfExtend(base, { paint: ["text_fill"], chars: regChars }));
}

function pfCheckImage(S, item, ctx, base) {
    var isPlaced = item.typename === "PlacedItem";
    var embedded = false;
    var path = null;
    var linkState = null;

    if (isPlaced) {
        try {
            var file = item.file;
            path = file.fsName;
            if (!file.exists) linkState = "file_not_found";
        } catch (e) {
            linkState = "no_file";
        }
    } else {
        try { embedded = !!item.embedded; } catch (e2) {}
        if (!embedded) {
            try { path = item.file.fsName; } catch (e3) {}
            try {
                var st = String(item.status);
                if (st.indexOf("NODATA") >= 0) linkState = "no_data";
                else if (st.indexOf("DATAMODIFIED") >= 0) linkState = "modified";
            } catch (e4) {}
            if (!linkState && path) {
                try { if (!item.file.exists) linkState = "file_not_found"; } catch (e5) {}
            }
        }
    }
    S.imageCount++;
    if (isPlaced) S.placedCount++;

    if (linkState === "modified") {
        if (pfOn(S, "links.modified")) pfAdd(S, "links.modified", pfExtend(base, { file: path }));
    } else if (linkState) {
        if (pfOn(S, "links.missing")) pfAdd(S, "links.missing", pfExtend(base, { reason: linkState, file: path }));
    }

    if (pfOn(S, "images.low_ppi")) {
        // Raster content only: a placed PDF/AI/EPS has no pixel grid.
        var raster = !isPlaced || (path && PF_RASTER_EXT.test(path));
        if (!raster) {
            if (!path) S.ppiUnknown++;
        } else {
            try {
                var m = item.matrix;
                var sx = Math.sqrt(m.mValueA * m.mValueA + m.mValueB * m.mValueB);
                var sy = Math.sqrt(m.mValueC * m.mValueC + m.mValueD * m.mValueD);
                var bb = item.boundingBox;
                var ppiX = 72 / sx;
                var ppiY = 72 / sy;
                var ppi = Math.min(ppiX, ppiY);
                if (ppi < S.P.min_ppi) {
                    pfAdd(S, "images.low_ppi", pfExtend(base, {
                        effective_ppi: dmRound(ppi), ppi_x: dmRound(ppiX), ppi_y: dmRound(ppiY),
                        pixel_width: Math.round(Math.abs(bb[2] - bb[0])), pixel_height: Math.round(Math.abs(bb[1] - bb[3])),
                        width_pt: dmRound(item.width), height_pt: dmRound(item.height),
                        embedded: !isPlaced && embedded, file: path, bounds: dmVisibleBounds(item)
                    }));
                }
            } catch (e6) { pfUnreadable(S, "images.low_ppi", item, e6); }
        }
    }

    if (!isPlaced && pfOn(S, "colors.color_model_mismatch")) {
        try {
            var cs = String(item.imageColorSpace).replace("ImageColorSpace.", "");
            var docCmyk = S.docColorMode === "CMYK";
            if ((docCmyk && cs === "RGB") || (!docCmyk && cs === "CMYK")) {
                pfAdd(S, "colors.color_model_mismatch", pfExtend(base, { image_color_space: cs, document_color_mode: S.docColorMode }));
            }
        } catch (e7) { pfUnreadable(S, "colors.color_model_mismatch", item, e7); }
    }
}

function pfCheckObject(S, item, ctx, base) {
    var t = item.typename;
    if (ctx.top_level && (pfOn(S, "objects.off_artboard") || pfOn(S, "objects.partially_off_artboard"))) {
        try {
            var b = pfItemBounds(item, S.P);
            var touches = false;
            var inside = false;
            for (var a = 0; a < S.refBoards.length; a++) {
                if (dmIntersects(b, S.refBoards[a].bounds)) touches = true;
                if (pfContains(S.refBoards[a].bounds, b)) inside = true;
            }
            if (!touches) {
                if (pfOn(S, "objects.off_artboard")) pfAdd(S, "objects.off_artboard", pfExtend(base, { bounds: b }));
            } else if (!inside && S.P.policy !== "intersects") {
                if (pfOn(S, "objects.partially_off_artboard")) pfAdd(S, "objects.partially_off_artboard", pfExtend(base, { bounds: b }));
            }
        } catch (e) { pfUnreadable(S, "objects.off_artboard", item, e); }
    }
    if (pfOn(S, "objects.zero_size") && t !== "GroupItem" && t !== "TextFrame") {
        try {
            if (!item.guides && (item.width === 0 || item.height === 0)) {
                pfAdd(S, "objects.zero_size", pfExtend(base, { width: dmRound(item.width), height: dmRound(item.height) }));
            }
        } catch (e2) { pfUnreadable(S, "objects.zero_size", item, e2); }
    }
    if (pfOn(S, "objects.stray_point") && t === "PathItem") {
        try {
            if (item.pathPoints.length === 1 && !item.guides) {
                var pp = item.pathPoints[0].anchor;
                pfAdd(S, "objects.stray_point", pfExtend(base, { anchor: [dmRound(pp[0]), dmRound(-pp[1])] }));
            }
        } catch (e3) { pfUnreadable(S, "objects.stray_point", item, e3); }
    }
}

// ==================== Walk ====================

function pfVisit(S, item, ctx) {
    S.itemCount++;
    var base = pfBase(item, ctx);
    var selfLocked = false;
    var selfHidden = false;
    try { selfLocked = !!item.locked; } catch (e) {}
    try { selfHidden = !!item.hidden; } catch (e2) {}
    if (pfOn(S, "objects.locked") && selfLocked) pfAdd(S, "objects.locked", base);
    if (pfOn(S, "objects.hidden") && selfHidden && !ctx.hidden) pfAdd(S, "objects.hidden", base);
    if (ctx.hidden || selfHidden) return false;
    if (S.P.item_scope === "artboard") {
        try {
            if (!pfCenterIn(dmVisibleBounds(item), S.scopeBoard)) return true;
        } catch (e3) { return true; }
    }
    pfCheckObject(S, item, ctx, base);
    var t = item.typename;
    if (t === "PathItem" || t === "CompoundPathItem") pfCheckPaint(S, item, ctx, base);
    else if (t === "TextFrame") {
        S.textFrameCount++;
        pfCheckText(S, item, ctx, base);
    } else if (t === "PlacedItem" || t === "RasterItem") pfCheckImage(S, item, ctx, base);
    return true;
}

function pfWalkItems(S, items, ctx) {
    for (var i = 0; i < items.length; i++) {
        if (S.itemCount >= S.maxItems) {
            S.walkTruncated = true;
            return;
        }
        var it = items[i];
        var hiddenHere = ctx.hidden;
        try { if (it.hidden) hiddenHere = true; } catch (e) {}
        pfVisit(S, it, ctx);
        if (it.typename === "GroupItem") {
            pfWalkItems(S, it.pageItems, { layer_path: ctx.layer_path, hidden: hiddenHere, top_level: false });
        }
    }
}

function pfWalkLayer(S, layer, ancHidden, ancLocked) {
    var path = dmLayerPath(layer);
    var hidden = ancHidden || !layer.visible;
    var locked = ancLocked || layer.locked;
    if (pfOn(S, "objects.locked") && layer.locked) pfAdd(S, "objects.locked", { kind: "layer", layer_path: path });
    if (pfOn(S, "objects.hidden") && !layer.visible && !ancHidden) pfAdd(S, "objects.hidden", { kind: "layer", layer_path: path });
    for (var s = 0; s < layer.layers.length; s++) pfWalkLayer(S, layer.layers[s], hidden, locked);
    pfWalkItems(S, layer.pageItems, { layer_path: path, hidden: hidden, top_level: true });
}

// ==================== Document checks ====================

function pfDocumentInfo(doc) {
    var info = { name: doc.name };
    try { info.color_mode = String(doc.documentColorSpace).indexOf("CMYK") >= 0 ? "CMYK" : "RGB"; } catch (e) {}
    try { info.ruler_units = String(doc.rulerUnits).replace("RulerUnits.", ""); } catch (e) {}
    try { info.saved = !!doc.saved; } catch (e) {}
    try { info.path = (doc.fullName && doc.fullName.exists) ? doc.fullName.fsName : null; } catch (e) { info.path = null; }
    try { info.raster_effects_ppi = doc.rasterEffectSettings.resolution; } catch (e) {}
    info.artboards = dmListArtboards(doc).artboards;
    return info;
}

function pfDocumentChecks(S, doc) {
    if (pfOn(S, "document.raster_effects_resolution")) {
        try {
            var res = doc.rasterEffectSettings.resolution;
            if (res < S.P.min_ppi) {
                pfAdd(S, "document.raster_effects_resolution", { kind: "document", raster_effects_ppi: res, min_ppi: S.P.min_ppi });
            }
        } catch (e) {
            S.checks["document.raster_effects_resolution"].status = "error: " + String(e.message || e);
        }
    }
    if (pfOn(S, "document.empty_artboard")) {
        for (var a = 0; a < doc.artboards.length; a++) {
            var view = dmArtboardView(doc, { artboard_index: a, max_nodes: 5000 });
            var visible = 0;
            for (var n = 0; n < view.nodes.length; n++) if (!view.nodes[n].hidden) visible++;
            if (!visible) pfAdd(S, "document.empty_artboard", { kind: "artboard", artboard: view.artboard });
        }
    }
}

// ==================== Report ====================

var PF_MESSAGES = {
    "document.empty_artboard": "artboards contain no visible artwork",
    "document.raster_effects_resolution": "document raster effects resolution is below min_ppi",
    "objects.off_artboard": "objects lie entirely outside every artboard",
    "objects.partially_off_artboard": "objects cross an artboard edge (fine for intentional bleed)",
    "objects.zero_size": "objects have zero width or height",
    "objects.hairline_stroke": "paths have a stroke thinner than hairline_width",
    "objects.stray_point": "paths consist of a single anchor point",
    "objects.locked": "locked objects or layers",
    "objects.hidden": "hidden objects or layers (not checked further)",
    "text.missing_font": "text frames use fonts that are not installed",
    "text.overset": "text frames have overset (hidden) text",
    "text.empty_text": "text frames are empty",
    "images.low_ppi": "images are below min_ppi at their placed size",
    "links.missing": "linked files are missing",
    "links.modified": "linked images changed on disk since they were placed",
    "colors.registration": "objects are painted with the Registration color",
    "colors.overprint_white": "white fills or strokes are set to overprint (they disappear in print)",
    "colors.rich_black_text": "small text uses rich black (CMYK black plus other inks)",
    "colors.total_ink": "colors exceed total_ink_limit",
    "colors.color_model_mismatch": "embedded images use a color model different from the document",
    "colors.spot_colors": "spot colors are used"
};

var PF_SEVERITY_ORDER = { error: 0, warning: 1, info: 2 };

function pfReport(S, doc) {
    var findings = [];
    var run = [];
    var skipped = [];
    var bySeverity = { error: 0, warning: 0, info: 0 };
    var issuesFound = 0;
    for (var i = 0; i < PF_CHECKS.length; i++) {
        var c = S.checks[PF_CHECKS[i].id];
        if (c.status !== "run") {
            skipped.push({ check: c.id, reason: c.status });
        } else if (c.unreadable) {
            skipped.push({ check: c.id, reason: "partial", unreadable_objects: c.unreadable,
                samples: c.unreadable_samples, findings_so_far: c.count });
        } else {
            run.push(c.id);
        }
        if (c.count) {
            findings.push({
                tag: c.id, scope: c.scope, severity: c.severity, count: c.count,
                message: c.count + " " + PF_MESSAGES[c.id],
                affected_objects: c.affected,
                truncated: c.count > c.affected.length
            });
            bySeverity[c.severity] += c.count;
            if (c.severity !== "info") issuesFound += c.count;
        }
    }
    findings.sort(function (a, b) { return PF_SEVERITY_ORDER[a.severity] - PF_SEVERITY_ORDER[b.severity]; });
    return { findings: findings, run: run, skipped: skipped, bySeverity: bySeverity, issuesFound: issuesFound };
}

/**
 * Run the preflight.
 * P.scopes: subset of document/objects/text/images/links/colors (default all)
 * P.artboard_index: reference artboard (default: all artboards)
 * P.item_scope: "document" | "artboard" (only items centred on the artboard)
 * P.policy: "fully-contained" (also report partially_off_artboard) | "intersects"
 * P.bounds_type: "visible" | "geometric"; P.bounds_source: "group_visible" | "clipping_path"
 * Thresholds: min_ppi (300), hairline_width (0.25), total_ink_limit (300),
 * rich_black_max_size (12). Budgets: max_items (20000), max_text_chars
 * (200000), max_affected per tag (50).
 * Legacy flags: check_zero_size, check_empty_text, check_locked.
 */
function pfRun(doc, P) {
    P.min_ppi = P.min_ppi || 300;
    P.hairline_width = P.hairline_width === undefined ? 0.25 : P.hairline_width;
    P.total_ink_limit = P.total_ink_limit || 300;
    P.rich_black_max_size = P.rich_black_max_size || 12;
    var S = pfState(P);
    S.maxItems = P.max_items || 20000;
    S.textBudget = P.max_text_chars || 200000;
    S.textChars = 0;
    S.textUnscanned = 0;
    S.itemCount = 0;
    S.textFrameCount = 0;
    S.imageCount = 0;
    S.placedCount = 0;
    S.ppiUnknown = 0;
    S.walkTruncated = false;
    S.fonts = {};
    S.spots = {};

    var info = pfDocumentInfo(doc);
    S.docColorMode = info.color_mode;
    var abIdx = null;
    if (P.artboard_index !== undefined && P.artboard_index !== null) {
        abIdx = dmResolveArtboardIndex(doc, P);
        S.refBoards = [dmArtboardEntry(doc, abIdx)];
    } else {
        S.refBoards = info.artboards;
    }
    if (P.item_scope === "artboard") {
        if (abIdx === null) abIdx = doc.artboards.getActiveArtboardIndex();
        S.scopeBoard = dmArtboardEntry(doc, abIdx).bounds;
    }

    pfDocumentChecks(S, doc);
    for (var l = 0; l < doc.layers.length; l++) pfWalkLayer(S, doc.layers[l], false, false);

    // Spot colors are a document-level summary, reported as one finding.
    if (pfOn(S, "colors.spot_colors")) {
        for (var sp in S.spots) {
            if (S.spots.hasOwnProperty(sp)) pfAdd(S, "colors.spot_colors", { kind: "spot", spot: sp, uses: S.spots[sp] });
        }
    }

    // Budgets and coverage gaps turn a check into "partial".
    var c;
    if (S.walkTruncated) {
        for (c in S.checks) {
            if (S.checks.hasOwnProperty(c) && S.checks[c].status === "run" && c.indexOf("document.") !== 0) {
                S.checks[c].status = "partial: stopped after max_items=" + S.maxItems + " objects";
            }
        }
    }
    if (S.textUnscanned) {
        var textBudgetChecks = ["text.missing_font", "colors.rich_black_text"];
        for (var t = 0; t < textBudgetChecks.length; t++) {
            if (pfOn(S, textBudgetChecks[t])) {
                S.checks[textBudgetChecks[t]].status = "partial: " + S.textUnscanned +
                    " text frames not fully scanned (max_text_chars=" + S.textBudget + ")";
            }
        }
    }
    if (pfOn(S, "text.missing_font")) {
        S.notes["text.missing_font"] = "Detects fonts Illustrator reports as embedded-subset-only or not installed. " +
            "In files saved without PDF compatibility a missing font can look installed to scripts; " +
            "confirm with Type > Find/Replace Font if the result matters.";
    }
    if (S.placedCount && pfOn(S, "links.modified")) {
        S.notes["links.modified"] = "Placed (linked) files expose no link status to scripts; only linked raster items were checked.";
    }
    if (S.placedCount && pfOn(S, "colors.color_model_mismatch")) {
        S.notes["colors.color_model_mismatch"] = "The color space of linked placed images is not exposed; only embedded images were checked.";
    }
    if (S.ppiUnknown && pfOn(S, "images.low_ppi")) {
        S.notes["images.low_ppi"] = S.ppiUnknown + " placed item(s) have no readable file path, so whether they are raster is unknown; they were not measured.";
    }

    var rep = pfReport(S, doc);
    var fonts = [];
    for (var fn in S.fonts) {
        if (!S.fonts.hasOwnProperty(fn)) continue;
        var fe = S.fonts[fn];
        var frames = 0;
        for (var k in fe.frames) if (fe.frames.hasOwnProperty(k)) frames++;
        var entry = { font: fe.font, family: fe.family, style: fe.style, available: fe.available, chars: fe.chars, frames: frames };
        if (fe.missing_reason) entry.missing_reason = fe.missing_reason;
        fonts.push(entry);
    }

    return {
        document: doc.name,
        document_info: info,
        artboard_index: abIdx,
        scopes: P.scopes && P.scopes.length ? P.scopes : PF_SCOPES,
        findings: rep.findings,
        checks_run: rep.run,
        checks_skipped: rep.skipped,
        check_notes: S.notes,
        fonts_used: fonts,
        thresholds: { min_ppi: P.min_ppi, hairline_width: P.hairline_width,
            total_ink_limit: P.total_ink_limit, rich_black_max_size: P.rich_black_max_size },
        summary: {
            total_items: S.itemCount,
            text_frames: S.textFrameCount,
            images: S.imageCount,
            text_chars_scanned: S.textChars,
            issues_found: rep.issuesFound,
            by_severity: rep.bySeverity
        }
    };
}
