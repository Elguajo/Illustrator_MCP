/**
 * swatches.jsx - Document swatches, swatch groups and swatch libraries for
 * the typed illustrator_swatches tool.
 * Part of Illustrator MCP Standard Library
 * @version 1.0.0
 *
 * DOM FACTS (verified live on Illustrator 30.8.1)
 *   - doc.swatches holds every swatch; swatch.parent is always the Document,
 *     so group membership is read from doc.swatchGroups[i].getAllSwatches().
 *     swatchGroups[0] is the unnamed default group.
 *   - A global swatch is a Spot with colorType PROCESS; a spot swatch is a
 *     Spot with colorType SPOT. Both show up in doc.swatches with a SpotColor.
 *   - swatches.add() and swatchGroups.add() accept a duplicate name without
 *     complaint, so names are checked here before creating anything.
 *   - SwatchGroup.addSwatch() moves a swatch between groups;
 *     SwatchGroup.remove() deletes the swatches inside it.
 *   - Removing "[None]" or "[Registration]" throws nothing and does nothing;
 *     every delete is read back.
 *   - Deleting a spot or global swatch that is in use converts the objects
 *     that used it to an equivalent process color.
 *   - A color set on a swatch is stored in the document color mode (a CMYK
 *     spot in an RGB document reads back as RGB).
 *   - Swatch libraries are files: .ai libraries open as documents (read with
 *     alerts suppressed, then closed); .ase cannot be opened by app.open
 *     ("the operation was cancelled") and is parsed here instead; color books
 *     (.acb/.acbl) are proprietary and reported as not readable.
 *
 * Names are matched exactly and never guessed: a miss returns the existing
 * names that look similar, clearly marked as alternatives.
 *
 * ES3 only (see docs/ARCHITECTURE_DOCTRINE.md).
 * @requires doc_model
 */

// ==================== Primitives ====================

var SW_RESERVED = { "[None]": true, "[Registration]": true };

/** process | spot | global | registration | gradient | pattern | none */
function swKind(color) {
    var t = color ? color.typename : "";
    if (t === "SpotColor") {
        var ct = "";
        try { ct = String(color.spot.colorType); } catch (e) {}
        if (ct.indexOf("REGISTRATION") >= 0) return "registration";
        if (ct.indexOf("PROCESS") >= 0) return "global";
        return "spot";
    }
    if (t === "GradientColor") return "gradient";
    if (t === "PatternColor") return "pattern";
    if (t === "NoColor") return "none";
    return "process";
}

function swEntry(swatch, group) {
    var e = { name: swatch.name, kind: swKind(swatch.color), group: group };
    try { e.color = dmColor(swatch.color); } catch (x) { e.color = null; }
    return e;
}

/** Map swatch name -> group name (null for the default group). */
function swGroupOf(doc) {
    var map = {};
    for (var g = 1; g < doc.swatchGroups.length; g++) {
        var grp = doc.swatchGroups[g];
        var members = grp.getAllSwatches();
        for (var i = 0; i < members.length; i++) map["n:" + members[i].name] = grp.name;
    }
    return map;
}

function swFindGroup(doc, name) {
    for (var g = 1; g < doc.swatchGroups.length; g++) {
        if (doc.swatchGroups[g].name === name) return doc.swatchGroups[g];
    }
    return null;
}

/** First swatch with exactly this name, or null. */
function swFind(doc, name) {
    for (var i = 0; i < doc.swatches.length; i++) {
        if (doc.swatches[i].name === name) return doc.swatches[i];
    }
    return null;
}

function swAllNames(doc) {
    var out = [];
    for (var i = 0; i < doc.swatches.length; i++) out.push(doc.swatches[i].name);
    return out;
}

/** Levenshtein distance, giving up (returns max + 1) once it exceeds max. */
function swEditDistance(a, b, max) {
    if (Math.abs(a.length - b.length) > max) return max + 1;
    var prev = [];
    var j;
    for (j = 0; j <= b.length; j++) prev.push(j);
    for (var i = 1; i <= a.length; i++) {
        var cur = [i];
        var rowMin = i;
        for (j = 1; j <= b.length; j++) {
            var cost = a.charAt(i - 1) === b.charAt(j - 1) ? 0 : 1;
            var v = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost);
            cur.push(v);
            if (v < rowMin) rowMin = v;
        }
        if (rowMin > max) return max + 1;
        prev = cur;
    }
    return prev[b.length];
}

/**
 * Existing names close to a wanted one: case-insensitive equal, substring,
 * or within two edits (typos). Only names from the list are ever returned.
 */
function swSimilar(names, wanted, limit) {
    var w = String(wanted).toLowerCase();
    var out = [];
    for (var i = 0; i < names.length && out.length < (limit || 5); i++) {
        var n = String(names[i]).toLowerCase();
        if (n === w || n.indexOf(w) >= 0 || (w.length > 2 && w.indexOf(n) >= 0 && n.length > 2) ||
            (w.length > 3 && swEditDistance(n, w, 2) <= 2)) {
            out.push(names[i]);
        }
    }
    return out;
}

/** {model:"rgb", r,g,b 0..255} | {model:"cmyk", c,m,y,k 0..100} -> DOM color. */
function swMakeColor(c) {
    if (c.model === "cmyk") {
        var k = new CMYKColor();
        k.cyan = c.c; k.magenta = c.m; k.yellow = c.y; k.black = c.k;
        return k;
    }
    var r = new RGBColor();
    r.red = c.r; r.green = c.g; r.blue = c.b;
    return r;
}

/** The base color a swatch stores, for read-back (spot/global swatches wrap a Spot). */
function swBaseColor(swatch) {
    var c = swatch.color;
    if (c.typename === "SpotColor") return dmColor(c.spot.color);
    return dmColor(c);
}

/** True when a read-back color matches the requested one (same model, within rounding). */
function swColorMatches(want, got) {
    if (!got || got.model !== want.model) return false;
    var keys = want.model === "cmyk" ? ["c", "m", "y", "k"] : ["r", "g", "b"];
    for (var i = 0; i < keys.length; i++) {
        if (Math.abs(got[keys[i]] - want[keys[i]]) > 0.6) return false;
    }
    return true;
}

// ==================== Document swatches ====================

/**
 * List swatches with their group, kind and color.
 * P: {group?: name filter ("" = default group), offset?, max_swatches?}
 */
function swList(doc, P) {
    var groupOf = swGroupOf(doc);
    var groups = [{ name: null, swatch_count: doc.swatchGroups[0].getAllSwatches().length }];
    for (var g = 1; g < doc.swatchGroups.length; g++) {
        groups.push({ name: doc.swatchGroups[g].name, swatch_count: doc.swatchGroups[g].getAllSwatches().length });
    }
    var filter = P.group;
    if (filter !== undefined && filter !== null && filter !== "" && !swFindGroup(doc, filter)) {
        dmFail("No swatch group named '" + filter + "'. Groups: " + swGroupNames(doc).join(", "));
    }
    var offset = P.offset || 0;
    var cap = P.max_swatches || 500;
    var out = [];
    var matched = 0;
    var truncated = false;
    for (var i = 0; i < doc.swatches.length; i++) {
        var sw = doc.swatches[i];
        var grp = groupOf.hasOwnProperty("n:" + sw.name) ? groupOf["n:" + sw.name] : null;
        if (filter !== undefined && filter !== null) {
            if (filter === "" ? grp !== null : grp !== filter) continue;
        }
        matched++;
        if (matched <= offset) continue;
        if (out.length >= cap) { truncated = true; continue; }
        out.push(swEntry(sw, grp));
    }
    var res = { swatch_count: doc.swatches.length, groups: groups, swatches: out, truncated: truncated };
    if (truncated) res.resume_hint = { offset: offset + out.length };
    return res;
}

function swGroupNames(doc) {
    var out = [];
    for (var g = 1; g < doc.swatchGroups.length; g++) out.push(doc.swatchGroups[g].name);
    return out;
}

/** Exact lookup by name. P: {names: [...]} */
function swGet(doc, P) {
    var groupOf = swGroupOf(doc);
    var all = swAllNames(doc);
    var found = [];
    var failed = [];
    var names = P.names || [];
    for (var i = 0; i < names.length; i++) {
        var sw = swFind(doc, names[i]);
        if (sw) {
            found.push(swEntry(sw, groupOf.hasOwnProperty("n:" + sw.name) ? groupOf["n:" + sw.name] : null));
        } else {
            failed.push({ name: names[i], reason: "not_found", similar_existing: swSimilar(all, names[i], 5) });
        }
    }
    return { swatches: found, success_count: found.length, fail_count: failed.length, failed_objects: failed };
}

/**
 * Create swatches. P: {swatches: [{name, kind: process|spot|global, color}], group?}
 * Each one is read back by name: kind and color must match, else it is
 * removed again and reported as failed.
 */
function swCreate(doc, P) {
    var grp = null;
    if (P.group) {
        grp = swFindGroup(doc, P.group);
        if (!grp) dmFail("No swatch group named '" + P.group + "'. Create it first with action='create_group'.");
    }
    var specs = P.swatches || [];
    var created = [];
    var failed = [];
    var seen = {};
    for (var i = 0; i < specs.length; i++) {
        var s = specs[i];
        var kind = s.kind || "process";
        if (seen["n:" + s.name] || swFind(doc, s.name)) {
            failed.push({ name: s.name, reason: "name_exists" });
            continue;
        }
        seen["n:" + s.name] = true;
        try {
            var color = swMakeColor(s.color);
            if (kind === "process") {
                var sw = doc.swatches.add();
                sw.name = s.name;
                sw.color = color;
            } else {
                var spot = doc.spots.add();
                spot.name = s.name;
                spot.color = color;
                spot.colorType = kind === "global" ? ColorModel.PROCESS : ColorModel.SPOT;
            }
            var back = swFind(doc, s.name);
            if (!back) {
                failed.push({ name: s.name, reason: "not_created" });
                continue;
            }
            var gotKind = swKind(back.color);
            if (gotKind !== kind) {
                try { back.remove(); } catch (e) {}
                failed.push({ name: s.name, reason: "kind_mismatch: asked " + kind + ", got " + gotKind });
                continue;
            }
            if (grp) grp.addSwatch(back);
            var entry = swEntry(back, grp ? grp.name : null);
            var stored = swBaseColor(back);
            if (!swColorMatches(s.color, stored)) {
                if (stored && stored.model === s.color.model) {
                    try { back.remove(); } catch (e2) {}
                    failed.push({ name: s.name, reason: "color_mismatch", stored: stored });
                    continue;
                }
                entry.converted_to = stored ? stored.model : null;
            }
            if (grp && swGroupOf(doc)["n:" + s.name] !== grp.name) {
                entry.group_error = "swatch was created but is not in group '" + grp.name + "'";
            }
            created.push(entry);
        } catch (err) {
            failed.push({ name: s.name, reason: "error: " + String(err.message || err) });
        }
    }
    return { created: created, success_count: created.length, fail_count: failed.length, failed_objects: failed };
}

/** Create a swatch group and optionally move existing swatches into it. P: {group, move_swatches?} */
function swCreateGroup(doc, P) {
    if (swFindGroup(doc, P.group)) dmFail("A swatch group named '" + P.group + "' already exists");
    var grp = doc.swatchGroups.add();
    grp.name = P.group;
    var moved = [];
    var failed = [];
    var all = null;
    var names = P.move_swatches || [];
    for (var i = 0; i < names.length; i++) {
        var sw = swFind(doc, names[i]);
        if (!sw) {
            if (!all) all = swAllNames(doc);
            failed.push({ name: names[i], reason: "not_found", similar_existing: swSimilar(all, names[i], 5) });
            continue;
        }
        if (SW_RESERVED[sw.name]) {
            failed.push({ name: names[i], reason: "reserved" });
            continue;
        }
        grp.addSwatch(sw);
        moved.push(names[i]);
    }
    var groupOf = swGroupOf(doc);
    var verified = [];
    for (var j = 0; j < moved.length; j++) {
        if (groupOf["n:" + moved[j]] === P.group) verified.push(moved[j]);
        else failed.push({ name: moved[j], reason: "not_moved" });
    }
    if (!swFindGroup(doc, P.group)) dmFail("Swatch group '" + P.group + "' was not created");
    return { group: P.group, moved: verified, success_count: verified.length, fail_count: failed.length, failed_objects: failed };
}

/** Delete swatches by exact name (every swatch with that name). P: {names} */
function swDelete(doc, P) {
    var deleted = [];
    var failed = [];
    var skipped = [];
    var all = null;
    var names = P.names || [];
    for (var i = 0; i < names.length; i++) {
        var name = names[i];
        if (SW_RESERVED[name]) {
            skipped.push({ name: name, reason: "reserved: Illustrator keeps this swatch" });
            continue;
        }
        var kind = null;
        var count = 0;
        for (var j = doc.swatches.length - 1; j >= 0; j--) {
            if (doc.swatches[j].name === name) {
                if (kind === null) kind = swKind(doc.swatches[j].color);
                doc.swatches[j].remove();
                count++;
            }
        }
        if (!count) {
            if (!all) all = swAllNames(doc);
            failed.push({ name: name, reason: "not_found", similar_existing: swSimilar(all, name, 5) });
            continue;
        }
        if (swFind(doc, name)) {
            failed.push({ name: name, reason: "still_present_after_remove" });
            continue;
        }
        var e = { name: name, kind: kind, removed: count };
        if (kind === "spot" || kind === "global") {
            e.note = "Objects that used it keep the same look as a non-global process color";
        }
        deleted.push(e);
    }
    return { deleted: deleted, success_count: deleted.length, fail_count: failed.length,
        failed_objects: failed, skipped_objects: skipped };
}

/**
 * Delete a swatch group. keep_swatches (default true) moves its swatches to
 * the default group first, because SwatchGroup.remove() deletes them.
 * P: {group, keep_swatches}
 */
function swDeleteGroup(doc, P) {
    var grp = swFindGroup(doc, P.group);
    if (!grp) dmFail("No swatch group named '" + P.group + "'. Groups: " + swGroupNames(doc).join(", "));
    var keep = P.keep_swatches !== false;
    var members = grp.getAllSwatches();
    var names = [];
    for (var i = 0; i < members.length; i++) names.push(members[i].name);
    if (keep) {
        for (var j = 0; j < members.length; j++) doc.swatchGroups[0].addSwatch(members[j]);
    }
    grp.remove();
    if (swFindGroup(doc, P.group)) dmFail("Swatch group '" + P.group + "' is still present after remove");
    var missing = [];
    var present = [];
    for (var k = 0; k < names.length; k++) {
        if (swFind(doc, names[k])) present.push(names[k]);
        else missing.push(names[k]);
    }
    var out = { group: P.group, keep_swatches: keep };
    if (keep) {
        out.kept_swatches = present;
        if (missing.length) out.lost_swatches = missing;
    } else {
        out.deleted_swatches = missing;
        if (present.length) out.still_present = present;
    }
    return out;
}

// ==================== Swatch libraries ====================

var SW_LIBRARY_FORMATS = { ai: true, ase: true, acb: true, acbl: true };

function swLibraryRoots() {
    var roots = [];
    var appRoots = ["/Presets.localized/", "/Presets/"];
    for (var i = 0; i < appRoots.length; i++) {
        var f = Folder(app.path + appRoots[i] + app.locale + "/Swatches");
        if (f.exists) { roots.push({ folder: f, prefix: "", source: "app" }); break; }
    }
    try {
        var major = parseInt(app.version, 10);
        var user = Folder(Folder.userData + "/Adobe/Adobe Illustrator " + major + "/" + app.locale + "/Swatches");
        if (user.exists) roots.push({ folder: user, prefix: "User/", source: "user" });
    } catch (e) {}
    return roots;
}

function swWalk(folder, rel, root, out) {
    var entries = folder.getFiles();
    for (var i = 0; i < entries.length; i++) {
        var it = entries[i];
        var nm = decodeURI(it.name);
        if (it instanceof Folder) {
            swWalk(it, rel + nm + "/", root, out);
            continue;
        }
        var dot = nm.lastIndexOf(".");
        if (dot < 0) continue;
        var ext = nm.substring(dot + 1).toLowerCase();
        if (!SW_LIBRARY_FORMATS[ext]) continue;
        out.push({
            name: root.prefix + rel + nm.substring(0, dot),
            format: ext,
            source: root.source,
            readable: ext === "ai" || ext === "ase",
            file: it
        });
    }
}

function swLibraryFiles() {
    var roots = swLibraryRoots();
    var out = [];
    for (var r = 0; r < roots.length; r++) swWalk(roots[r].folder, "", roots[r], out);
    out.sort(function (a, b) { return a.name < b.name ? -1 : (a.name > b.name ? 1 : 0); });
    return out;
}

function swLibraries() {
    var files = swLibraryFiles();
    var libs = [];
    for (var i = 0; i < files.length; i++) {
        libs.push({ name: files[i].name, format: files[i].format, source: files[i].source, readable: files[i].readable });
    }
    return { count: libs.length, libraries: libs };
}

// ---- ASE (Adobe Swatch Exchange) parser over a binary string ----

function swU16(s, o) {
    return ((s.charCodeAt(o) & 255) << 8) | (s.charCodeAt(o + 1) & 255);
}

function swU32(s, o) {
    return swU16(s, o) * 65536 + swU16(s, o + 2);
}

/** IEEE 754 big-endian float32. */
function swF32(s, o) {
    var b = swU32(s, o);
    var sign = b >= 2147483648 ? -1 : 1;
    var exp = Math.floor(b / 8388608) & 255;
    var frac = b % 8388608;
    if (exp === 0) return sign * frac * Math.pow(2, -149);
    if (exp === 255) return frac ? NaN : sign * Infinity;
    return sign * (1 + frac / 8388608) * Math.pow(2, exp - 127);
}

/** UTF-16BE string of n code units, trailing NUL dropped. */
function swUtf16(s, o, n) {
    var out = "";
    for (var i = 0; i < n; i++) {
        var c = swU16(s, o + i * 2);
        if (c === 0) break;
        out += String.fromCharCode(c);
    }
    return out;
}

function swAseColor(model, s, o) {
    var i;
    if (model === "RGB ") {
        var rgb = [];
        for (i = 0; i < 3; i++) rgb.push(Math.round(swF32(s, o + i * 4) * 255));
        return { model: "rgb", hex: "#" + dmHex2(rgb[0]) + dmHex2(rgb[1]) + dmHex2(rgb[2]), r: rgb[0], g: rgb[1], b: rgb[2] };
    }
    if (model === "CMYK") {
        var v = [];
        for (i = 0; i < 4; i++) v.push(dmRound(swF32(s, o + i * 4) * 100));
        return { model: "cmyk", c: v[0], m: v[1], y: v[2], k: v[3] };
    }
    if (model === "LAB ") {
        return { model: "lab", l: dmRound(swF32(s, o) * 100), a: dmRound(swF32(s, o + 4)), b: dmRound(swF32(s, o + 8)) };
    }
    if (model === "Gray") {
        // ASE stores gray as a 0..1 lightness value; reported unconverted.
        return { model: "gray", ase_value: dmRound(swF32(s, o)) };
    }
    return { model: "unknown", ase_model: model };
}

var SW_ASE_KINDS = ["global", "spot", "process"];

/** Parse an ASE file held in a binary string. Returns {swatches, groups}. */
function swParseAse(s) {
    if (s.substring(0, 4) !== "ASEF") dmFail("Not an ASE swatch file (bad signature)");
    var count = swU32(s, 8);
    var o = 12;
    var group = null;
    var groups = [];
    var swatches = [];
    for (var b = 0; b < count && o + 6 <= s.length; b++) {
        var type = swU16(s, o);
        var len = swU32(s, o + 2);
        var body = o + 6;
        if (type === 0xC001) {
            group = swUtf16(s, body + 2, swU16(s, body));
            groups.push({ name: group, swatch_count: 0 });
        } else if (type === 0xC002) {
            group = null;
        } else if (type === 0x0001) {
            var n = swU16(s, body);
            var name = swUtf16(s, body + 2, n);
            var p = body + 2 + n * 2;
            var model = s.substring(p, p + 4);
            var color = swAseColor(model, s, p + 4);
            var nvals = model === "CMYK" ? 4 : (model === "Gray" ? 1 : 3);
            var kindCode = swU16(s, p + 4 + nvals * 4);
            swatches.push({ name: name, kind: SW_ASE_KINDS[kindCode] || "process", color: color, group: group });
            if (group !== null) groups[groups.length - 1].swatch_count++;
        }
        o = body + len;
    }
    return { swatches: swatches, groups: groups };
}

function swReadAse(file) {
    file.encoding = "BINARY";
    if (!file.open("r")) dmFail("Cannot read swatch library file " + file.fsName);
    var s;
    try { s = file.read(); } finally { file.close(); }
    return swParseAse(s);
}

/**
 * Read an .ai swatch library by opening it as a document. Alerts are
 * suppressed while it is open (a library with missing links would otherwise
 * block on a modal dialog), the user's active document is restored, and a
 * library that was already open is read in place and left open.
 */
function swReadAiLibrary(file) {
    var already = null;
    for (var i = 0; i < app.documents.length; i++) {
        try {
            if (app.documents[i].fullName.fsName === file.fsName) { already = app.documents[i]; break; }
        } catch (e) {}
    }
    var prev = app.documents.length ? app.activeDocument : null;
    var uil = app.userInteractionLevel;
    var lib = already;
    try {
        app.userInteractionLevel = UserInteractionLevel.DONTDISPLAYALERTS;
        if (!lib) lib = app.open(file);
        var listing = swList(lib, { max_swatches: 100000 });
        return { swatches: listing.swatches, groups: listing.groups.slice(1) };
    } finally {
        if (lib && !already) {
            try { lib.close(SaveOptions.DONOTSAVECHANGES); } catch (e2) {}
        }
        app.userInteractionLevel = uil;
        if (prev) { try { prev.activate(); } catch (e3) {} }
    }
}

/**
 * Read one swatch library by its exact name from swLibraries().
 * P: {library, names?, offset?, max_swatches?}
 */
function swLibrary(P) {
    var files = swLibraryFiles();
    var hit = null;
    var names = [];
    for (var i = 0; i < files.length; i++) {
        names.push(files[i].name);
        if (files[i].name === P.library) hit = files[i];
    }
    if (!hit) {
        var similar = swSimilar(names, P.library, 8);
        dmFail("No swatch library named '" + P.library + "'." +
            (similar.length ? " Similar existing libraries: " + similar.join(", ") + "." : "") +
            " List them with action='libraries'.");
    }
    if (!hit.readable) {
        dmFail("Library '" + hit.name + "' is a ." + hit.format + " color book, which scripts cannot read");
    }
    var data = hit.format === "ase" ? swReadAse(hit.file) : swReadAiLibrary(hit.file);
    var list = data.swatches;
    var failed = [];
    if (P.names && P.names.length) {
        var all = [];
        var byName = {};
        for (var j = 0; j < list.length; j++) {
            all.push(list[j].name);
            if (!byName.hasOwnProperty("n:" + list[j].name)) byName["n:" + list[j].name] = list[j];
        }
        var picked = [];
        for (var k = 0; k < P.names.length; k++) {
            var want = P.names[k];
            if (byName.hasOwnProperty("n:" + want)) picked.push(byName["n:" + want]);
            else failed.push({ name: want, reason: "not_found", similar_existing: swSimilar(all, want, 5) });
        }
        list = picked;
    }
    var offset = P.offset || 0;
    var cap = P.max_swatches || 500;
    var page = list.slice(offset, offset + cap);
    var res = {
        library: hit.name,
        format: hit.format,
        swatch_count: data.swatches.length,
        groups: data.groups,
        swatches: page,
        truncated: offset + page.length < list.length
    };
    if (res.truncated) res.resume_hint = { offset: offset + page.length };
    if (P.names && P.names.length) {
        res.success_count = list.length;
        res.fail_count = failed.length;
        res.failed_objects = failed;
    }
    return res;
}
