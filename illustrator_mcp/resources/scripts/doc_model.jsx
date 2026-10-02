/**
 * doc_model.jsx - Document model for the typed inspection, artboard and
 * document tools (illustrator_inspect, illustrator_artboards,
 * illustrator_document list/switch).
 * Part of Illustrator MCP Standard Library
 * @version 1.0.0
 *
 * IDENTITY
 *   PageItems are addressed by Illustrator's native PageItem.uuid (AI 24+),
 *   resolved with resolvePageItemByUuid() (mcp_id.jsx), which works around
 *   three getPageItemFromUuid() quirks. Observed on AI 30.8.1: uuid is a short
 *   numeric string, survives moves and regrouping, and is new on
 *   duplicate(). Layers and artboards have no uuid: layers are addressed by name
 *   path ("Layer 1/Sub"), artboards by index or name.
 *   A uuid is a per-document counter renumbered on every reopen; @mcp:id in
 *   item.note remains the identity that survives close/reopen, so both are
 *   reported.
 *
 * COORDINATES
 *   Every bounds value returned or accepted here is canvas-global points,
 *   Y-down: [left, top, right, bottom] with top < bottom. Converted from
 *   Illustrator's Y-up document coordinates by negating y.
 *
 * ERRORS
 *   Request errors go through dmFail(); anything else that throws is a
 *   script failure and keeps the generic error path.
 *
 * RESULT CONTRACT
 *   Batch operations report success_count, fail_count and
 *   failed_objects[{uuid, reason}]. Size-capped listings report
 *   truncated=true plus a resume_hint saying how to continue.
 *
 * ES3 only (see docs/ARCHITECTURE_DOCTRINE.md).
 */

// ==================== Primitives ====================

/**
 * Throw a request error: the caller asked for something the document can't
 * satisfy (unknown artboard, deleting the last one, nothing to fit). The
 * Python layer reports these as V011 with this message, apart from genuine
 * script failures.
 */
function dmFail(message) {
    var e = new Error(message);
    e.dmUserError = true;
    throw e;
}


function dmRound(n) {
    if (typeof n !== "number" || isNaN(n)) return n;
    return Math.round(n * 100) / 100;
}

/** Y-up [L, T, R, B] -> Y-down [L, T, R, B], rounded. */
function dmToYDown(b) {
    return [dmRound(b[0]), dmRound(-b[1]), dmRound(b[2]), dmRound(-b[3])];
}

/** Y-down [L, T, R, B] -> Illustrator Y-up rect. */
function dmToYUp(b) {
    return [b[0], -b[1], b[2], -b[3]];
}

function dmUuid(item) {
    try {
        var u = item.uuid;
        if (u === undefined || u === null || u === "") return null;
        return String(u);
    } catch (e) {
        return null;
    }
}

function dmMcpId(item) {
    try {
        var note = item.note;
        if (!note) return null;
        var m = String(note).match(/@mcp:id=([^\s@]+)/);
        return m ? m[1] : null;
    } catch (e) {
        return null;
    }
}

/**
 * Identify the document a result describes. uuids are numbered per document
 * and collide across open documents, so a {type: "uuid"} task target must
 * carry this name back (see resolveUuidTargets in targets.jsx).
 * path is null for a document that was never saved.
 */
function dmDocumentRef(doc) {
    var ref = { name: String(doc.name), path: null };
    try { ref.path = (doc.fullName && doc.fullName.exists) ? doc.fullName.fsName : null; } catch (e) {}
    return ref;
}

/** Attach dmDocumentRef(doc) to an inspect view result. */
function dmWithDocument(doc, result) {
    result.document = dmDocumentRef(doc);
    return result;
}

/** Resolve uuids to PageItems. Unknown uuids are reported, never thrown. @requires mcp_id */
function dmResolveUuids(doc, uuids) {
    var items = [];
    var missing = [];
    for (var i = 0; i < uuids.length; i++) {
        var key = String(uuids[i]);
        var found = resolvePageItemByUuid(doc, key);
        if (found) {
            items.push(found);
        } else {
            missing.push({ uuid: key, reason: "not_found" });
        }
    }
    return { items: items, missing: missing };
}

function dmLayerPath(layer) {
    var parts = [];
    var cur = layer;
    while (cur && cur.typename === "Layer") {
        parts.unshift(cur.name);
        cur = cur.parent;
    }
    return parts.join("/");
}

/** Find a layer by "A/B/C" path or by a bare top-level or nested name. */
function dmFindLayer(doc, path) {
    var parts = String(path).split("/");
    var coll = doc.layers;
    var cur = null;
    for (var p = 0; p < parts.length; p++) {
        cur = null;
        for (var i = 0; i < coll.length; i++) {
            if (coll[i].name === parts[p]) {
                cur = coll[i];
                break;
            }
        }
        if (!cur) break;
        coll = cur.layers;
    }
    if (cur) return cur;
    // Bare name anywhere in the tree (first match, depth-first)
    if (parts.length === 1) return dmFindLayerByName(doc.layers, parts[0]);
    return null;
}

function dmFindLayerByName(coll, name) {
    for (var i = 0; i < coll.length; i++) {
        if (coll[i].name === name) return coll[i];
        var hit = dmFindLayerByName(coll[i].layers, name);
        if (hit) return hit;
    }
    return null;
}

function dmIsHidden(item) {
    try {
        if (item.typename === "Layer") return !item.visible;
        return !!item.hidden;
    } catch (e) {
        return false;
    }
}

function dmIsLocked(item) {
    try {
        return !!item.locked;
    } catch (e) {
        return false;
    }
}

function dmVisibleBounds(item) {
    try {
        return dmToYDown(item.visibleBounds);
    } catch (e) {
        return null;
    }
}

/** Child containers an item exposes for structure browsing. */
function dmChildren(item) {
    var out = [];
    var i;
    if (item.typename === "Layer") {
        for (i = 0; i < item.layers.length; i++) out.push(item.layers[i]);
        for (i = 0; i < item.pageItems.length; i++) out.push(item.pageItems[i]);
    } else if (item.typename === "GroupItem") {
        for (i = 0; i < item.pageItems.length; i++) out.push(item.pageItems[i]);
    } else if (item.typename === "CompoundPathItem") {
        for (i = 0; i < item.pathItems.length; i++) out.push(item.pathItems[i]);
    }
    return out;
}

function dmChildTypes(children) {
    var counts = {};
    for (var i = 0; i < children.length; i++) {
        var t = children[i].typename;
        counts[t] = (counts[t] || 0) + 1;
    }
    return counts;
}

/** Basic node description shared by every inspection view. */
function dmNode(item) {
    var node;
    if (item.typename === "Layer") {
        node = {
            kind: "layer",
            name: item.name,
            layer_path: dmLayerPath(item),
            locked: dmIsLocked(item),
            hidden: dmIsHidden(item)
        };
    } else {
        node = {
            kind: "item",
            uuid: dmUuid(item),
            type: item.typename,
            name: item.name || "",
            bounds: dmVisibleBounds(item),
            locked: dmIsLocked(item),
            hidden: dmIsHidden(item)
        };
        var mcpId = dmMcpId(item);
        if (mcpId) node.mcp_id = mcpId;
        if (item.typename === "GroupItem") {
            try { node.clipped = !!item.clipped; } catch (e) {}
        }
        if (item.typename === "TextFrame") {
            try {
                var txt = String(item.contents);
                node.text_preview = txt.length > 80 ? txt.substring(0, 80) + "..." : txt;
            } catch (e) {}
        }
    }
    var kids = dmChildren(item);
    if (kids.length > 0) {
        node.child_count = kids.length;
        node.child_types = dmChildTypes(kids);
    }
    return node;
}

// ==================== Inspection ====================

/**
 * Breadth-first layer/group tree.
 * P.uuids / P.layers: optional start nodes (default: top-level layers)
 * P.max_depth: 0 = start nodes only, N = N levels of children, -1 = unlimited
 * P.max_nodes: response budget; expansion stops when reached
 */
function dmStructure(doc, P) {
    var maxDepth = (P.max_depth === undefined || P.max_depth === null) ? 1 : P.max_depth;
    var maxNodes = P.max_nodes || 300;
    var roots = [];
    var failed = [];
    var i;

    if (P.uuids && P.uuids.length) {
        var res = dmResolveUuids(doc, P.uuids);
        roots = res.items;
        failed = res.missing;
    }
    if (P.layers && P.layers.length) {
        for (i = 0; i < P.layers.length; i++) {
            var lyr = dmFindLayer(doc, P.layers[i]);
            if (lyr) roots.push(lyr);
            else failed.push({ layer: P.layers[i], reason: "layer_not_found" });
        }
    }
    if (!(P.uuids && P.uuids.length) && !(P.layers && P.layers.length)) {
        for (i = 0; i < doc.layers.length; i++) roots.push(doc.layers[i]);
    }

    var out = [];
    var queue = [];
    var count = 0;
    var truncated = false;
    var unexpanded = [];

    for (i = 0; i < roots.length; i++) {
        if (count >= maxNodes) { truncated = true; break; }
        var rn = dmNode(roots[i]);
        out.push(rn);
        count++;
        queue.push({ item: roots[i], node: rn, depth: 0 });
    }

    while (queue.length > 0) {
        var entry = queue.shift();
        if (!entry.node.child_count) continue;
        if (maxDepth !== -1 && entry.depth >= maxDepth) continue;
        if (count >= maxNodes) {
            truncated = true;
            unexpanded.push(entry.node);
            continue;
        }
        var kids = dmChildren(entry.item);
        // Only expand when every child fits: a half-listed container is
        // indistinguishable from a complete one.
        if (count + kids.length > maxNodes) {
            truncated = true;
            unexpanded.push(entry.node);
            continue;
        }
        entry.node.children = [];
        for (var k = 0; k < kids.length; k++) {
            var cn = dmNode(kids[k]);
            entry.node.children.push(cn);
            count++;
            queue.push({ item: kids[k], node: cn, depth: entry.depth + 1 });
        }
    }

    var result = { nodes: out, node_count: count, truncated: truncated };
    if (failed.length) result.failed_objects = failed;
    if (truncated) result.resume_hint = dmResumeHint(unexpanded);
    return result;
}

function dmResumeHint(unexpanded) {
    var uuids = [];
    var layers = [];
    for (var i = 0; i < unexpanded.length && (uuids.length + layers.length) < 20; i++) {
        if (unexpanded[i].kind === "layer") layers.push(unexpanded[i].layer_path);
        else if (unexpanded[i].uuid) uuids.push(unexpanded[i].uuid);
    }
    return {
        message: "Node budget reached. Containers listed here kept child_count/child_types " +
            "but no children. Call view='structure' again with these uuids or layers as start " +
            "nodes, or raise max_nodes.",
        uuids: uuids,
        layers: layers,
        more: unexpanded.length > uuids.length + layers.length
    };
}

function dmIntersects(a, b) {
    // Y-down rects
    return a[0] <= b[2] && a[2] >= b[0] && a[1] <= b[3] && a[3] >= b[1];
}

function dmArtboardEntry(doc, i) {
    var ab = doc.artboards[i];
    var b = dmToYDown(ab.artboardRect);
    return {
        index: i,
        name: ab.name,
        bounds: b,
        width: dmRound(b[2] - b[0]),
        height: dmRound(b[3] - b[1])
    };
}

function dmResolveArtboardIndex(doc, P) {
    if (P.artboard_index !== undefined && P.artboard_index !== null) {
        if (P.artboard_index < 0 || P.artboard_index >= doc.artboards.length) {
            dmFail("artboard_index " + P.artboard_index + " is out of range (document has " +
                doc.artboards.length + " artboards, 0-based)");
        }
        return P.artboard_index;
    }
    if (P.artboard_name) {
        for (var i = 0; i < doc.artboards.length; i++) {
            if (doc.artboards[i].name === P.artboard_name) return i;
        }
        dmFail("No artboard named '" + P.artboard_name + "'");
    }
    return doc.artboards.getActiveArtboardIndex();
}

/** Every top-level layer item (groups not expanded) overlapping an artboard. */
function dmArtboardView(doc, P) {
    var idx = dmResolveArtboardIndex(doc, P);
    var abEntry = dmArtboardEntry(doc, idx);
    var maxNodes = P.max_nodes || 300;
    var hits = [];
    var truncated = false;

    function scanLayer(layer) {
        for (var s = 0; s < layer.layers.length; s++) scanLayer(layer.layers[s]);
        for (var i = 0; i < layer.pageItems.length; i++) {
            var it = layer.pageItems[i];
            var b = dmVisibleBounds(it);
            if (!b || !dmIntersects(b, abEntry.bounds)) continue;
            if (hits.length >= maxNodes) { truncated = true; return; }
            var n = dmNode(it);
            n.layer_path = dmLayerPath(layer);
            hits.push(n);
        }
    }
    for (var l = 0; l < doc.layers.length && !truncated; l++) scanLayer(doc.layers[l]);

    var result = { artboard: abEntry, nodes: hits, node_count: hits.length, truncated: truncated };
    if (truncated) {
        result.resume_hint = {
            message: "Item budget reached before the scan finished. Raise max_nodes, or browse " +
                "one layer at a time with view='structure' and layers=[...]."
        };
    }
    return result;
}

function dmSelectionView(doc, P) {
    var sel = doc.selection;
    var nodes = [];
    var maxNodes = P.max_nodes || 300;
    // While editing text, doc.selection is a TextRange, not an array.
    if (!sel || sel.typename === "TextRange") {
        return { nodes: [], node_count: 0, truncated: false, text_editing: !!sel };
    }
    for (var i = 0; i < sel.length; i++) {
        if (nodes.length >= maxNodes) {
            return {
                nodes: nodes, node_count: nodes.length, truncated: true,
                resume_hint: { message: "Selection exceeds max_nodes; raise max_nodes." }
            };
        }
        nodes.push(dmNode(sel[i]));
    }
    return { nodes: nodes, node_count: nodes.length, truncated: false };
}

// ==================== Details ====================

function dmHex2(n) {
    var h = Math.round(n).toString(16);
    return h.length < 2 ? "0" + h : h;
}

function dmColor(c) {
    if (!c) return null;
    var t = c.typename;
    if (t === "RGBColor") {
        return { model: "rgb", hex: "#" + dmHex2(c.red) + dmHex2(c.green) + dmHex2(c.blue),
            r: dmRound(c.red), g: dmRound(c.green), b: dmRound(c.blue) };
    }
    if (t === "CMYKColor") {
        return { model: "cmyk", c: dmRound(c.cyan), m: dmRound(c.magenta),
            y: dmRound(c.yellow), k: dmRound(c.black) };
    }
    if (t === "GrayColor") return { model: "gray", gray: dmRound(c.gray) };
    if (t === "NoColor") return { model: "none" };
    if (t === "SpotColor") {
        var spot = { model: "spot", tint: dmRound(c.tint) };
        try { spot.name = c.spot.name; spot.base = dmColor(c.spot.color); } catch (e) {}
        return spot;
    }
    if (t === "PatternColor") {
        var pat = { model: "pattern" };
        try { pat.name = c.pattern.name; } catch (e) {}
        return pat;
    }
    if (t === "GradientColor") {
        var g = { model: "gradient" };
        try {
            g.angle = dmRound(c.angle);
            g.name = c.gradient.name;
            g.gradient_type = String(c.gradient.type).indexOf("RADIAL") >= 0 ? "radial" : "linear";
            g.stops = [];
            var stops = c.gradient.gradientStops;
            for (var i = 0; i < stops.length; i++) {
                g.stops.push({ position: dmRound(stops[i].rampPoint),
                    midpoint: dmRound(stops[i].midPoint), opacity: dmRound(stops[i].opacity),
                    color: dmColor(stops[i].color) });
            }
        } catch (e) {}
        return g;
    }
    return { model: "unknown", typename: t };
}

/** The path that carries paint: a CompoundPathItem paints through its first child. */
function dmPaintSource(item) {
    if (item.typename === "PathItem") return item;
    if (item.typename === "CompoundPathItem" && item.pathItems.length > 0) return item.pathItems[0];
    return null;
}

function dmAppearance(item) {
    var a = {};
    try { a.opacity = dmRound(item.opacity); } catch (e) {}
    try { a.blend_mode = String(item.blendingMode).replace("BlendModes.", "").toLowerCase(); } catch (e) {}
    var src = dmPaintSource(item);
    if (src) {
        try {
            a.fill = src.filled ? dmColor(src.fillColor) : { model: "none" };
            a.stroke = src.stroked ? dmColor(src.strokeColor) : { model: "none" };
            if (src.stroked) a.stroke_width = dmRound(src.strokeWidth);
            if (src.stroked && src.strokeDashes && src.strokeDashes.length) a.stroke_dashes = src.strokeDashes;
        } catch (e) {}
        try { a.clipping = !!src.clipping; } catch (e) {}
        if (item.typename === "CompoundPathItem") a.paint_source = "first_child_path";
    } else if (item.typename === "TextFrame") {
        try {
            var ca = item.textRange.characterAttributes;
            a.fill = dmColor(ca.fillColor);
            a.stroke = dmColor(ca.strokeColor);
            a.paint_source = "first_character";
        } catch (e) {}
    }
    return a;
}

var _dmFontAvailability = {};

function dmFontAvailable(psName) {
    if (_dmFontAvailability.hasOwnProperty(psName)) return _dmFontAvailability[psName];
    var ok;
    try {
        app.textFonts.getByName(psName);
        ok = true;
    } catch (e) {
        ok = false;
    }
    _dmFontAvailability[psName] = ok;
    return ok;
}

var _dmFontStatusCache = {};

/**
 * Whether a run's TextFont is really installed. getByName() alone is not
 * enough: for a missing font Illustrator registers a placeholder in
 * app.textFonts, so the lookup succeeds (observed on AI 30.8.1).
 * Two signals are reliable:
 *   - the run's font carries an embedded-subset family ("XPUYQY+Name"),
 *     or differs from the record app.textFonts returns for its name;
 *   - getByName() throws.
 * Blind spot: in a file saved without PDF compatibility the run uses the
 * placeholder record itself and nothing in the DOM distinguishes it.
 */
function dmFontStatus(font) {
    var name, family;
    try {
        name = String(font.name);
        family = String(font.family);
    } catch (e) {
        return { available: false, reason: "unreadable" };
    }
    var key = name + "|" + family;
    if (_dmFontStatusCache.hasOwnProperty(key)) return _dmFontStatusCache[key];
    var st;
    if (/^[A-Z]{6}\+/.test(family)) {
        st = { available: false, reason: "embedded_subset_only" };
    } else {
        try {
            var rec = app.textFonts.getByName(name);
            st = String(rec.family) === family ? { available: true } : { available: false, reason: "not_installed" };
        } catch (e2) {
            st = { available: false, reason: "not_installed" };
        }
    }
    _dmFontStatusCache[key] = st;
    return st;
}

function dmColorKey(c) {
    if (!c) return "null";
    var t = c.typename;
    try {
        if (t === "RGBColor") return "rgb:" + dmRound(c.red) + "," + dmRound(c.green) + "," + dmRound(c.blue);
        if (t === "CMYKColor") return "cmyk:" + dmRound(c.cyan) + "," + dmRound(c.magenta) + "," + dmRound(c.yellow) + "," + dmRound(c.black);
        if (t === "GrayColor") return "gray:" + dmRound(c.gray);
        if (t === "SpotColor") return "spot:" + c.spot.name + "@" + dmRound(c.tint);
        if (t === "PatternColor") return "pattern:" + c.pattern.name;
        if (t === "GradientColor") return "gradient:" + c.gradient.name;
    } catch (e) {}
    return t;
}

/**
 * Contiguous style runs of a text frame or story.
 * TextFrame.textRanges yields one range per *character*, not per run
 * (observed on AI 30.8.1), so runs are rebuilt by comparing neighbours.
 * Indexing the cached collection is cheap (~40us per character); going
 * through characters[i] is ~15x slower.
 * In the continuation frame of a threaded story, tf.textRanges is indexed by
 * position in the *story* (valid indices start at textRange.start although
 * length is the frame's), so a frame is always read through its story over
 * the frame's own window. Run starts are relative to the frame (or story).
 * opts.max_chars: scan budget (default 100000); opts.fill: split runs on
 * fill color too and report it.
 */
function dmFontRuns(textObj, opts) {
    opts = opts || {};
    var maxChars = opts.max_chars || 100000;
    var ranges, offset = 0, n;
    if (textObj.typename === "TextFrame") {
        ranges = textObj.story.textRanges;
        offset = textObj.textRange.start;
        n = textObj.characters.length;
    } else {
        ranges = textObj.textRanges;
        n = ranges.length;
    }
    var limit = Math.min(n, maxChars);
    var runs = [];
    var cur = null;
    for (var i = 0; i < limit; i++) {
        var ca = ranges[offset + i].characterAttributes;
        var font = ca.textFont;
        var fname = String(font.name);
        var size = ca.size;
        var fill = opts.fill ? ca.fillColor : null;
        var key = fname + "|" + size + (opts.fill ? "|" + dmColorKey(fill) : "");
        if (cur && cur.key === key) {
            cur.length++;
            continue;
        }
        cur = { key: key, start: i, length: 1, font: fname, family: String(font.family),
            style: String(font.style), size: dmRound(size), _font: font };
        if (opts.fill) cur.fill = dmColor(fill);
        runs.push(cur);
    }
    for (var r = 0; r < runs.length; r++) {
        var st = dmFontStatus(runs[r]._font);
        runs[r].available = st.available;
        if (!st.available) runs[r].missing_reason = st.reason;
        delete runs[r]._font;
        delete runs[r].key;
    }
    return { runs: runs, char_count: n, scanned: limit, truncated: limit < n };
}

/**
 * Overset (overflowing) text of an area or path text frame.
 * Composed lines cover only the visible text; anything in the story past
 * the last line of the last frame is hidden. Only non-whitespace counts,
 * so a trailing paragraph return is not overset. Point text never
 * overflows (returns null). A frame that threads on into another frame
 * reports overset=false: its overflow continues there.
 * Verified live on AI 30.8.1 for area text, threaded area text and path text.
 */
function dmTextOverflow(tf) {
    var kind = String(tf.kind);
    if (kind.indexOf("POINTTEXT") >= 0) return null;
    if (kind.indexOf("AREATEXT") >= 0) {
        try {
            // The last frame of a thread reports itself as its own nextFrame.
            var nx = tf.nextFrame;
            if (nx && dmUuid(nx) !== dmUuid(tf)) return { overset: false, continues_in: dmUuid(nx) };
        } catch (e) {}
    }
    var lines = tf.lines;
    var hiddenFrom;
    if (lines.length) {
        var last = lines[lines.length - 1];
        hiddenFrom = last.start + last.length;
    } else {
        hiddenFrom = tf.textRange.start;
    }
    var hidden = String(tf.story.textRange.contents).substring(hiddenFrom);
    var visible = hidden.replace(/[\s\u0003]/g, "");
    if (!visible.length) return { overset: false };
    return { overset: true, overset_chars: hidden.length,
        overset_preview: hidden.length > 60 ? hidden.substring(0, 60) + "..." : hidden };
}

/** Locked/hidden state including every enclosing group and layer. */
function dmEffectiveState(item) {
    var st = { locked: false, hidden: false };
    var cur = item;
    var guard = 0;
    while (cur && cur.typename !== "Document" && guard++ < 100) {
        try {
            if (cur.typename === "Layer") {
                if (!cur.visible) st.hidden = true;
            } else if (cur.hidden) {
                st.hidden = true;
            }
        } catch (e) {}
        try { if (cur.locked) st.locked = true; } catch (e2) {}
        try { cur = cur.parent; } catch (e3) { break; }
    }
    return st;
}

function dmTextDetails(tf) {
    var t = {};
    try {
        var contents = String(tf.contents);
        t.length = contents.length;
        t.contents = contents.length > 2000 ? contents.substring(0, 2000) : contents;
        if (contents.length > 2000) t.contents_truncated = true;
    } catch (e) {}
    try { t.kind = String(tf.kind).replace("TextType.", "").toLowerCase(); } catch (e) {}
    try { t.paragraph_count = tf.paragraphs.length; } catch (e) {}
    // Distinct font/size pairs over the whole frame (runs rebuilt per character).
    var runs = [];
    var seen = {};
    try {
        var fr = dmFontRuns(tf);
        for (var i = 0; i < fr.runs.length; i++) {
            var r = fr.runs[i];
            var key = r.font + "|" + r.size;
            if (seen[key]) continue;
            seen[key] = true;
            if (runs.length >= 200) { t.font_runs_truncated = true; break; }
            var entry = { font: r.font, family: r.family, style: r.style, size: r.size,
                available: r.available, first_char: r.start };
            if (!r.available) entry.missing_reason = r.missing_reason;
            runs.push(entry);
        }
        if (fr.truncated) t.font_runs_truncated = true;
    } catch (e) {
        t.font_runs_error = String(e.message || e);
    }
    t.font_runs = runs;
    try {
        var ov = dmTextOverflow(tf);
        if (ov) t.overflow = ov;
    } catch (e) {
        t.overflow_error = String(e.message || e);
    }
    return t;
}

function dmGeometry(item) {
    var g = {};
    try { g.geometric_bounds = dmToYDown(item.geometricBounds); } catch (e) {}
    if (item.typename === "PathItem") {
        try { g.closed = !!item.closed; g.anchor_count = item.pathPoints.length; } catch (e) {}
        try { g.area = dmRound(Math.abs(item.area)); } catch (e) {}
    } else if (item.typename === "CompoundPathItem") {
        try {
            g.path_count = item.pathItems.length;
            var total = 0;
            for (var i = 0; i < item.pathItems.length; i++) total += item.pathItems[i].pathPoints.length;
            g.anchor_count = total;
        } catch (e) {}
    }
    return g;
}

/** Per-object details. P.uuids required; P.aspects subset of appearance/text/geometry. */
function dmDetails(doc, P) {
    var aspects = P.aspects && P.aspects.length ? P.aspects : ["appearance", "text", "geometry"];
    var want = {};
    for (var a = 0; a < aspects.length; a++) want[aspects[a]] = true;
    var res = dmResolveUuids(doc, P.uuids || []);
    var objects = [];
    for (var i = 0; i < res.items.length; i++) {
        var it = res.items[i];
        var o = dmNode(it);
        try { o.layer_path = dmLayerPath(it.layer); } catch (e) {}
        if (want.appearance) o.appearance = dmAppearance(it);
        if (want.text && it.typename === "TextFrame") o.text = dmTextDetails(it);
        if (want.geometry) o.geometry = dmGeometry(it);
        objects.push(o);
    }
    return {
        objects: objects,
        success_count: objects.length,
        fail_count: res.missing.length,
        failed_objects: res.missing
    };
}

// ==================== Artboards ====================

function dmListArtboards(doc) {
    var list = [];
    for (var i = 0; i < doc.artboards.length; i++) list.push(dmArtboardEntry(doc, i));
    return { active_index: doc.artboards.getActiveArtboardIndex(), artboards: list };
}

function dmArtboardCreate(doc, P) {
    var w = P.width;
    var h = P.height;
    if (!(w > 0) || !(h > 0)) dmFail("width and height must be positive");
    var left = P.left;
    var top = P.top;
    if (left === undefined || left === null || top === undefined || top === null) {
        // Default: right of the right-most artboard, top-aligned with it, 20pt gap.
        var maxRight = null;
        var rowTop = 0;
        for (var i = 0; i < doc.artboards.length; i++) {
            var b = dmToYDown(doc.artboards[i].artboardRect);
            if (maxRight === null || b[2] > maxRight) { maxRight = b[2]; rowTop = b[1]; }
        }
        if (left === undefined || left === null) left = maxRight === null ? 0 : maxRight + 20;
        if (top === undefined || top === null) top = rowTop;
    }
    var ab = doc.artboards.add(dmToYUp([left, top, left + w, top + h]));
    if (P.name) ab.name = P.name;
    var idx = doc.artboards.length - 1;
    if (P.activate) doc.artboards.setActiveArtboardIndex(idx);
    return { artboard: dmArtboardEntry(doc, idx), active_index: doc.artboards.getActiveArtboardIndex() };
}

/** Anchor fraction for 9-point resize anchors. */
function dmAnchorFrac(anchor) {
    var map = {
        "top-left": [0, 0], "top-center": [0.5, 0], "top-right": [1, 0],
        "center-left": [0, 0.5], "center": [0.5, 0.5], "center-right": [1, 0.5],
        "bottom-left": [0, 1], "bottom-center": [0.5, 1], "bottom-right": [1, 1]
    };
    var f = map[anchor || "center"];
    if (!f) dmFail("Unknown anchor '" + anchor + "'");
    return f;
}

function dmArtboardUpdate(doc, P) {
    var idx = dmResolveArtboardIndex(doc, P);
    var ab = doc.artboards[idx];
    var before = dmArtboardEntry(doc, idx);
    var b = before.bounds.slice(0);
    var changed = [];

    if (P.bounds) {
        b = P.bounds.slice(0);
        changed.push("bounds");
    } else {
        if (P.width !== undefined && P.width !== null || P.height !== undefined && P.height !== null) {
            var f = dmAnchorFrac(P.anchor);
            var oldW = b[2] - b[0];
            var oldH = b[3] - b[1];
            var newW = (P.width !== undefined && P.width !== null) ? P.width : oldW;
            var newH = (P.height !== undefined && P.height !== null) ? P.height : oldH;
            if (!(newW > 0) || !(newH > 0)) dmFail("width and height must be positive");
            var ax = b[0] + oldW * f[0];
            var ay = b[1] + oldH * f[1];
            b = [ax - newW * f[0], ay - newH * f[1], ax - newW * f[0] + newW, ay - newH * f[1] + newH];
            changed.push("size");
        }
        if (P.left !== undefined && P.left !== null || P.top !== undefined && P.top !== null) {
            var dx = (P.left !== undefined && P.left !== null) ? P.left - b[0] : 0;
            var dy = (P.top !== undefined && P.top !== null) ? P.top - b[1] : 0;
            b = [b[0] + dx, b[1] + dy, b[2] + dx, b[3] + dy];
            changed.push("position");
        }
    }
    if (b[2] <= b[0] || b[3] <= b[1]) dmFail("bounds must have positive width and height");
    if (changed.length) ab.artboardRect = dmToYUp(b);
    if (P.new_name) {
        ab.name = P.new_name;
        changed.push("name");
    }
    if (!changed.length) dmFail("Nothing to update: pass new_name, width/height, left/top or bounds");
    return { before: before, artboard: dmArtboardEntry(doc, idx), changed: changed };
}

/** For preset updates: swap P.width/P.height to match the target's current orientation. */
function dmMatchOrientation(doc, P) {
    var cur = dmArtboardEntry(doc, dmResolveArtboardIndex(doc, P));
    var curLandscape = cur.width > cur.height;
    var newLandscape = P.width > P.height;
    if (cur.width !== cur.height && P.width !== P.height && curLandscape !== newLandscape) {
        var w = P.width;
        P.width = P.height;
        P.height = w;
    }
    return P;
}

function dmArtboardDelete(doc, P) {
    if (doc.artboards.length <= 1) {
        dmFail("Cannot delete the only artboard; resize or rename it instead");
    }
    var idx = dmResolveArtboardIndex(doc, P);
    var removed = dmArtboardEntry(doc, idx);
    doc.artboards.remove(idx);
    var listing = dmListArtboards(doc);
    listing.removed = removed;
    listing.note = "Artwork on the removed artboard was kept. Artboard indices after " +
        removed.index + " shifted down by one.";
    return listing;
}

function dmArtboardActivate(doc, P) {
    var idx = dmResolveArtboardIndex(doc, P);
    doc.artboards.setActiveArtboardIndex(idx);
    return { active_index: idx, artboard: dmArtboardEntry(doc, idx) };
}

function dmUnion(rects) {
    var u = null;
    for (var i = 0; i < rects.length; i++) {
        var r = rects[i];
        if (!r) continue;
        if (!u) { u = r.slice(0); continue; }
        if (r[0] < u[0]) u[0] = r[0];
        if (r[1] < u[1]) u[1] = r[1];
        if (r[2] > u[2]) u[2] = r[2];
        if (r[3] > u[3]) u[3] = r[3];
    }
    return u;
}

/**
 * Resize an artboard to enclose artwork. Computed from bounds, so the
 * user's selection is never touched.
 * P.scope: "artboard" (items overlapping it) | "all" | "selection" | "uuids"
 */
function dmArtboardFit(doc, P) {
    var idx = dmResolveArtboardIndex(doc, P);
    var before = dmArtboardEntry(doc, idx);
    var scope = P.scope || "artboard";
    var rects = [];
    var failed = [];
    var i;

    if (scope === "uuids") {
        var res = dmResolveUuids(doc, P.uuids || []);
        failed = res.missing;
        for (i = 0; i < res.items.length; i++) rects.push(dmVisibleBounds(res.items[i]));
    } else if (scope === "selection") {
        var sel = doc.selection;
        if (sel && sel.typename !== "TextRange") {
            for (i = 0; i < sel.length; i++) rects.push(dmVisibleBounds(sel[i]));
        }
    } else if (scope === "all") {
        rects.push(dmToYDown(doc.visibleBounds));
    } else if (scope === "artboard") {
        var view = dmArtboardView(doc, { artboard_index: idx, max_nodes: 5000 });
        for (i = 0; i < view.nodes.length; i++) {
            if (!view.nodes[i].hidden) rects.push(view.nodes[i].bounds);
        }
    } else {
        dmFail("Unknown fit scope '" + scope + "'");
    }

    var u = dmUnion(rects);
    if (!u) dmFail("Nothing to fit: scope '" + scope + "' matched no visible artwork");
    var pad = P.padding || 0;
    u = [u[0] - pad, u[1] - pad, u[2] + pad, u[3] + pad];
    doc.artboards[idx].artboardRect = dmToYUp(u);
    var out = { before: before, artboard: dmArtboardEntry(doc, idx), scope: scope };
    if (failed.length) out.failed_objects = failed;
    return out;
}

// ==================== Documents ====================

function dmDocEntry(d, i, activeDoc) {
    var e = { index: i, name: d.name, active: d === activeDoc };
    try { e.saved = !!d.saved; } catch (x) {}
    // A never-saved document still reports a fullName ("/Untitled-2"); only a
    // file that exists on disk is a real path.
    try { e.path = (d.fullName && d.fullName.exists) ? d.fullName.fsName : null; } catch (x) { e.path = null; }
    try { e.color_mode = String(d.documentColorSpace).indexOf("CMYK") >= 0 ? "CMYK" : "RGB"; } catch (x) {}
    try { e.artboard_count = d.artboards.length; } catch (x) {}
    try { e.layer_count = d.layers.length; } catch (x) {}
    try { e.ruler_units = String(d.rulerUnits).replace("RulerUnits.", ""); } catch (x) {}
    return e;
}

function dmListDocuments() {
    var docs = [];
    var active = app.documents.length ? app.activeDocument : null;
    for (var i = 0; i < app.documents.length; i++) docs.push(dmDocEntry(app.documents[i], i, active));
    return { count: docs.length, documents: docs };
}

function dmSwitchDocument(P) {
    var target = null;
    var i;
    if (P.index !== undefined && P.index !== null) {
        if (P.index < 0 || P.index >= app.documents.length) {
            dmFail("Document index " + P.index + " is out of range (" + app.documents.length + " open)");
        }
        target = app.documents[P.index];
    } else if (P.name) {
        for (i = 0; i < app.documents.length; i++) {
            if (app.documents[i].name === P.name) { target = app.documents[i]; break; }
        }
        if (!target) dmFail("No open document named '" + P.name + "'");
    } else {
        dmFail("switch requires index or name");
    }
    target.activate();
    var active = app.activeDocument;
    for (i = 0; i < app.documents.length; i++) {
        if (app.documents[i] === active) return { document: dmDocEntry(active, i, active), artboards: dmListArtboards(active) };
    }
    return { document: dmDocEntry(active, -1, active), artboards: dmListArtboards(active) };
}
