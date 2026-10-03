/**
 * doc_text.jsx - Text editing for the typed illustrator_text tool:
 * style-preserving find/replace, document-wide font replacement,
 * character-range and paragraph styling, convert to outlines.
 * Part of Illustrator MCP Standard Library
 * @version 1.0.0
 *
 * Builds on doc_model.jsx: text frames are addressed by native
 * PageItem.uuid, results follow the batch contract (success_count,
 * fail_count, failed_objects[{uuid, reason}], skipped_objects).
 *
 * SAFETY
 *   A frame that is locked or hidden, itself or through any enclosing
 *   group or layer, is never modified: it is listed in skipped_objects.
 *   Every write is read back; a value that did not stick is a failure.
 *
 * Observed on AI 30.8.1 and relied on here:
 *   - characters[i] with .length = n addresses an n-character range;
 *   - assigning .contents to a range keeps the attributes of its first
 *     character, so a uniformly styled match keeps its style;
 *   - textRanges is per character (see dmFontRuns).
 *
 * ES3 only (see docs/ARCHITECTURE_DOCTRINE.md).
 */

// ==================== Targets ====================

/**
 * uuids are numbered per document and collide across open documents, so a
 * request that names uuids must also name the document they came from
 * (result.document.name of illustrator_inspect). Checked before any lookup.
 */
function dtCheckDocument(doc, P) {
    var name = String(doc.name);
    if (P.uuids && P.uuids.length && !P.document) {
        dmFail("uuids require document: pass result.document.name from the illustrator_inspect call " +
            "that returned them (uuids are numbered per document)");
    }
    if (P.document && P.document !== name) {
        var isOpen = true;   // unknown unless app.documents can be read
        try {
            isOpen = false;
            for (var i = 0; i < app.documents.length; i++) {
                if (String(app.documents[i].name) === P.document) { isOpen = true; break; }
            }
        } catch (e) { isOpen = true; }
        if (!isOpen) {
            dmFail("No open document is named '" + P.document + "' (renamed or closed since the inspect?); the " +
                "active document is '" + name + "'. uuids are numbered per document, so nothing was changed. " +
                "Run illustrator_inspect on the intended document and pass its result.document.name.");
        }
        dmFail("Request names document '" + P.document + "' but the active document is '" + name +
            "'. uuids are numbered per document, so nothing was changed. Switch with " +
            "illustrator_document(action='switch', name='" + P.document + "') or inspect this document again.");
    }
}

/** Every text result names its document. */
function dtResult(doc, result) {
    result.document = dmDocumentRef(doc);
    return result;
}

/** Text frames for a request: P.uuids, or every text frame in the document. */
function dtCollectFrames(doc, P) {
    var frames = [];
    var failed = [];
    var i;
    if (P.uuids && P.uuids.length) {
        var res = dmResolveUuids(doc, P.uuids);
        failed = res.missing;
        for (i = 0; i < res.items.length; i++) {
            if (res.items[i].typename === "TextFrame") frames.push(res.items[i]);
            else failed.push({ uuid: dmUuid(res.items[i]), reason: "not_a_text_frame", type: res.items[i].typename });
        }
    } else {
        for (i = 0; i < doc.textFrames.length; i++) frames.push(doc.textFrames[i]);
    }
    return { frames: frames, failed: failed };
}

/** Why a frame must not be touched, or null. */
function dtBlockReason(item) {
    var st = dmEffectiveState(item);
    if (st.locked) return "locked";
    if (st.hidden) return "hidden";
    return null;
}

function dtSkip(item, reason) {
    return { uuid: dmUuid(item), name: item.name || "", reason: reason,
        note: "Not modified. Unlock or unhide it (and its layer or group) to include it; do not retry as is." };
}

/** Uuid of the first frame of a story: one key per story across threaded frames. */
function dtStoryKey(tf) {
    try {
        var frames = tf.story.textFrames;
        if (frames.length) return dmUuid(frames[0]);
    } catch (e) {}
    return dmUuid(tf);
}

function dtStoryFrames(tf) {
    var out = [];
    try {
        var frames = tf.story.textFrames;
        for (var i = 0; i < frames.length; i++) out.push(frames[i]);
    } catch (e) {
        out.push(tf);
    }
    return out;
}

/** n-character TextRange starting at index start of a frame or story. */
function dtRange(textObj, start, n) {
    var r = textObj.characters[start];
    r.length = n;
    return r;
}

/** Every character attribute that a replacement must carry over. */
function dtStyleKey(ca) {
    var parts = [];
    function add(f) {
        try { parts.push(String(f())); } catch (e) { parts.push("?"); }
    }
    add(function () { return ca.textFont.name; });
    add(function () { return dmRound(ca.size); });
    add(function () { return ca.tracking; });
    add(function () { return dmRound(ca.horizontalScale); });
    add(function () { return dmRound(ca.verticalScale); });
    add(function () { return dmRound(ca.baselineShift); });
    add(function () { return ca.underline; });
    add(function () { return ca.strikeThrough; });
    add(function () { return ca.capitalization; });
    add(function () { return dmColorKey(ca.fillColor); });
    add(function () { return dmColorKey(ca.strokeColor); });
    add(function () { return dmRound(ca.strokeWeight); });
    return parts.join("|");
}

// ==================== Find / replace ====================

function dtIsWordChar(ch) {
    if (!ch) return false;
    if (/[0-9_]/.test(ch)) return true;
    return ch.toLowerCase() !== ch.toUpperCase();
}

/** Non-overlapping match starts, left to right. */
function dtFindAll(text, find, caseSensitive, wholeWord) {
    var hay = caseSensitive ? text : text.toLowerCase();
    var needle = caseSensitive ? find : find.toLowerCase();
    var hits = [];
    var from = 0;
    while (true) {
        var at = hay.indexOf(needle, from);
        if (at < 0) break;
        var ok = true;
        if (wholeWord) {
            ok = !dtIsWordChar(text.charAt(at - 1)) && !dtIsWordChar(text.charAt(at + needle.length));
        }
        if (ok) {
            hits.push(at);
            from = at + needle.length;
        } else {
            from = at + 1;
        }
    }
    return hits;
}

/**
 * Normalize line breaks to Illustrator's paragraph separator. A forced
 * (soft) line break is the control character U+0003 and passes through.
 */
function dtNormalizeBreaks(s) {
    return String(s).replace(/\r\n|\n/g, "\r");
}

/** Style runs of the n characters at `at`: [{off, len, key, font, size}]. */
function dtMatchRuns(ranges, at, n) {
    var runs = [];
    for (var c = 0; c < n; c++) {
        var ca = ranges[at + c].characterAttributes;
        var key = dtStyleKey(ca);
        if (runs.length && runs[runs.length - 1].key === key) {
            runs[runs.length - 1].len++;
            continue;
        }
        var font = "";
        try { font = String(ca.textFont.name); } catch (e) {}
        runs.push({ off: c, len: 1, key: key, font: font, size: dmRound(ca.size) });
    }
    return runs;
}

/** Runs of a match as the caller sees them: their text, font and size. */
function dtRunReport(text, at, runs) {
    var out = [];
    for (var i = 0; i < runs.length; i++) {
        out.push({ text: text.substr(at + runs[i].off, runs[i].len), font: runs[i].font, size: runs[i].size });
    }
    return out;
}

/** The find/replace requests of a call: P.replacements, or the single P.find. */
function dtReplacePairs(P) {
    var pairs = [];
    var i;
    function build(src) {
        var find = dtNormalizeBreaks(src.find || "");
        if (!find.length) dmFail("find must not be empty");
        var pair = {
            find: find,
            repl: null,
            runs: null,
            caseSensitive: src.case_sensitive === undefined || src.case_sensitive === null
                ? P.case_sensitive !== false : src.case_sensitive !== false,
            wholeWord: src.whole_word === undefined || src.whole_word === null
                ? !!P.whole_word : !!src.whole_word
        };
        if (src.replace_runs && src.replace_runs.length) {
            pair.runs = [];
            for (var k = 0; k < src.replace_runs.length; k++) pair.runs.push(dtNormalizeBreaks(src.replace_runs[k]));
        } else {
            pair.repl = dtNormalizeBreaks(src.replace === undefined || src.replace === null ? "" : src.replace);
        }
        return pair;
    }
    if (P.replacements && P.replacements.length) {
        for (i = 0; i < P.replacements.length; i++) pairs.push(build(P.replacements[i]));
    } else {
        pairs.push(build(P));
    }
    return pairs;
}

/**
 * One find/replace pair over the frames in `col`. Returns its own tallies;
 * dtReplaceText merges them.
 */
function dtReplaceOne(doc, col, pair, dry) {
    var find = pair.find;
    var out = { failed: [], skipped: [], changed: [], skippedOcc: [], replaced: 0, success: 0,
        matches: [], matchCount: 0, wouldReplace: 0 };
    var seenStory = {};

    for (var f = 0; f < col.frames.length; f++) {
        var tf = col.frames[f];
        var key = dtStoryKey(tf);
        if (seenStory[key]) continue;
        seenStory[key] = true;

        var frames = dtStoryFrames(tf);
        var block = null;
        for (var b = 0; b < frames.length && !block; b++) block = dtBlockReason(frames[b]);
        var story = tf.story;
        var text = String(story.textRange.contents);
        var hits = dtFindAll(text, find, pair.caseSensitive, pair.wholeWord);
        if (block) {
            // Only worth reporting when the frame actually contains a match.
            if (hits.length) {
                var sk = dtSkip(tf, block);
                sk.matches = hits.length;
                out.skipped.push(sk);
            }
            continue;
        }
        try {
            var ranges = story.textRanges;
            var plan = [];
            var h;
            for (h = 0; h < hits.length; h++) {
                var at = hits[h];
                var runs = dtMatchRuns(ranges, at, find.length);
                var pieces = null;
                var reason = null;
                var note = null;
                if (pair.runs) {
                    if (pair.runs.length === runs.length) {
                        pieces = pair.runs;
                    } else {
                        reason = "run_count_mismatch";
                        note = "replace_runs has " + pair.runs.length + " string(s) but the match has " + runs.length +
                            " style run(s) (listed in runs); give one string per run, in order.";
                    }
                } else if (runs.length === 1) {
                    pieces = [pair.repl];
                } else {
                    reason = "mixed_styles";
                    note = "The match spans " + runs.length + " differently styled runs (listed in runs). Pass " +
                        "replace_runs with one string per run to replace each in its own style, or replace the " +
                        "uniformly styled parts separately.";
                }
                out.matchCount++;
                if (dry && out.matches.length < 200) {
                    var m = { uuid: key, index: at, text: text.substr(at, find.length),
                        would: pieces ? "replace" : "skip", runs: dtRunReport(text, at, runs) };
                    if (reason) m.reason = reason;
                    out.matches.push(m);
                }
                if (!pieces) {
                    if (out.skippedOcc.length < 100) {
                        out.skippedOcc.push({ uuid: key, index: at, text: text.substr(at, find.length),
                            reason: reason, runs: dtRunReport(text, at, runs), note: note });
                    }
                    continue;
                }
                plan.push({ at: at, runs: runs, pieces: pieces });
            }
            if (dry) {
                out.wouldReplace += plan.length;
                continue;
            }
            // Right to left, so earlier indices stay valid; inside a match, last run first.
            var expected = text;
            var k;
            for (h = plan.length - 1; h >= 0; h--) {
                var p = plan[h];
                for (k = p.runs.length - 1; k >= 0; k--) {
                    var r = dtRange(story, p.at + p.runs[k].off, p.runs[k].len);
                    if (p.pieces[k].length) r.contents = p.pieces[k];
                    else r.remove();
                }
                expected = expected.substring(0, p.at) + p.pieces.join("") + expected.substring(p.at + find.length);
            }
            // Read back: the story text, then the style of each replaced run.
            var actual = String(story.textRange.contents);
            if (actual !== expected) {
                out.failed.push({ uuid: key, reason: "verify_failed",
                    detail: "Story text after replacement differs from the expected result" });
                continue;
            }
            var styleLost = 0;
            if (plan.length) {
                var after = story.textRanges;
                var shift = 0;
                for (h = 0; h < plan.length; h++) {
                    var pos = plan[h].at + shift;
                    for (k = 0; k < plan[h].runs.length; k++) {
                        var plen = plan[h].pieces[k].length;
                        if (plen) {
                            if (dtStyleKey(after[pos].characterAttributes) !== plan[h].runs[k].key ||
                                dtStyleKey(after[pos + plen - 1].characterAttributes) !== plan[h].runs[k].key) {
                                styleLost++;
                            }
                        }
                        pos += plen;
                    }
                    shift += plan[h].pieces.join("").length - find.length;
                }
            }
            if (styleLost) {
                out.failed.push({ uuid: key, reason: "style_not_preserved", count: styleLost });
                continue;
            }
            out.success++;
            out.replaced += plan.length;
            if (plan.length) out.changed.push({ uuid: key, replaced: plan.length, frames: frames.length });
        } catch (e) {
            out.failed.push({ uuid: key, reason: "error", detail: String(e.message || e) });
        }
    }
    return out;
}

/**
 * Style-preserving find/replace.
 * P.find (required) with P.replace (may be "") or P.replace_runs; or
 * P.replacements, a list of such pairs applied in order, each on the text
 * the previous pair left. P.case_sensitive (default true), P.whole_word
 * (default false), P.uuids (default: every frame), P.dry_run (report the
 * matches and what would happen, change nothing).
 * Works per story, so a match that crosses threaded frames is found.
 * A match whose characters do not share one style is skipped and reported
 * unless the caller gave replace_runs: one string per style run of the
 * match, each written in that run's own style.
 */
function dtReplaceText(doc, P) {
    dtCheckDocument(doc, P);
    var pairs = dtReplacePairs(P);
    var batch = !!(P.replacements && P.replacements.length);
    var dry = !!P.dry_run;
    var col = dtCollectFrames(doc, P);
    var failed = col.failed.slice(0);
    var skipped = [];
    var changed = [];
    var skippedOcc = [];
    var matches = [];
    var results = [];
    var replacedTotal = 0;
    var success = 0;
    var matchCount = 0;
    var wouldReplace = 0;

    for (var i = 0; i < pairs.length; i++) {
        var one = dtReplaceOne(doc, col, pairs[i], dry);
        var j;
        for (j = 0; j < one.failed.length; j++) { if (batch) one.failed[j].pair = i; failed.push(one.failed[j]); }
        for (j = 0; j < one.skipped.length; j++) { if (batch) one.skipped[j].pair = i; skipped.push(one.skipped[j]); }
        for (j = 0; j < one.changed.length; j++) { if (batch) one.changed[j].pair = i; changed.push(one.changed[j]); }
        for (j = 0; j < one.skippedOcc.length; j++) { if (batch) one.skippedOcc[j].pair = i; skippedOcc.push(one.skippedOcc[j]); }
        for (j = 0; j < one.matches.length; j++) { if (batch) one.matches[j].pair = i; matches.push(one.matches[j]); }
        replacedTotal += one.replaced;
        success += one.success;
        matchCount += one.matchCount;
        wouldReplace += one.wouldReplace;
        results.push({ index: i, find: pairs[i].find, replaced_count: one.replaced,
            match_count: one.matchCount, skipped_occurrences: one.skippedOcc.length,
            failed: one.failed.length });
    }
    var result = {
        replaced_count: replacedTotal,
        changed: changed,
        skipped_occurrences: skippedOcc,
        success_count: success,
        fail_count: failed.length,
        failed_objects: failed,
        skipped_objects: skipped
    };
    if (batch) result.results = results;
    if (dry) {
        result.dry_run = true;
        result.match_count = matchCount;
        result.would_replace = wouldReplace;
        result.matches = matches;
    }
    return dtResult(doc, result);
}

// ==================== Font replacement ====================

/**
 * Replace one font with another wherever it is used.
 * P.from_font: PostScript name as reported by illustrator_inspect
 *   (missing fonts included). P.to_font: an installed font.
 * P.uuids: limit to these frames (default: every frame).
 * Only runs in from_font change; every other run is left as it was.
 */
function dtReplaceFont(doc, P) {
    dtCheckDocument(doc, P);
    var from = String(P.from_font || "");
    if (!from) dmFail("from_font is required");
    var toFont;
    try {
        toFont = app.textFonts.getByName(String(P.to_font || ""));
    } catch (e) {
        dmFail("to_font '" + P.to_font + "' is not an installed font (use its PostScript name, e.g. 'MyriadPro-Regular')");
    }
    if (!dmFontStatus(toFont).available) dmFail("to_font '" + P.to_font + "' is not installed");
    var toName = String(toFont.name);

    var col = dtCollectFrames(doc, P);
    var failed = col.failed;
    var skipped = [];
    var changed = [];
    var seenStory = {};
    var runsTotal = 0;
    var charsTotal = 0;
    var success = 0;

    for (var f = 0; f < col.frames.length; f++) {
        var tf = col.frames[f];
        var key = dtStoryKey(tf);
        if (seenStory[key]) continue;
        seenStory[key] = true;
        var story = tf.story;
        try {
            var fr = dmFontRuns(story, { max_chars: 1000000 });
            var targets = [];
            for (var r = 0; r < fr.runs.length; r++) {
                if (fr.runs[r].font === from) targets.push(fr.runs[r]);
            }
            if (!targets.length) {
                success++;
                continue;
            }
            var frames = dtStoryFrames(tf);
            var block = null;
            for (var b = 0; b < frames.length && !block; b++) block = dtBlockReason(frames[b]);
            if (block) {
                var sk = dtSkip(tf, block);
                sk.runs = targets.length;
                skipped.push(sk);
                continue;
            }
            var chars = 0;
            for (var t = 0; t < targets.length; t++) {
                dtRange(story, targets[t].start, targets[t].length).characterAttributes.textFont = toFont;
                chars += targets[t].length;
            }
            // Read back: no run of from_font may remain, and the changed
            // characters now use to_font.
            var check = dmFontRuns(story, { max_chars: 1000000 });
            var left = 0;
            for (r = 0; r < check.runs.length; r++) {
                if (check.runs[r].font === from) left += check.runs[r].length;
            }
            var ranges = story.textRanges;
            var wrong = 0;
            for (t = 0; t < targets.length; t++) {
                if (String(ranges[targets[t].start].characterAttributes.textFont.name) !== toName) wrong++;
            }
            if (left || wrong) {
                failed.push({ uuid: key, reason: "verify_failed", chars_still_in_from_font: left, runs_not_in_to_font: wrong });
                continue;
            }
            success++;
            runsTotal += targets.length;
            charsTotal += chars;
            changed.push({ uuid: key, runs: targets.length, chars: chars });
        } catch (e) {
            failed.push({ uuid: key, reason: "error", detail: String(e.message || e) });
        }
    }
    return dtResult(doc, {
        from_font: from,
        to_font: toName,
        runs_replaced: runsTotal,
        chars_replaced: charsTotal,
        changed: changed,
        success_count: success,
        fail_count: failed.length,
        failed_objects: failed,
        skipped_objects: skipped
    });
}

// ==================== Character / paragraph styling ====================

/** Build a color from {hex} | {r,g,b} | {c,m,y,k} | {gray}. */
function dtMakeColor(spec) {
    var c;
    if (spec.hex) {
        var h = String(spec.hex).replace("#", "");
        if (!/^[0-9a-fA-F]{6}$/.test(h)) dmFail("color.hex must look like #RRGGBB");
        c = new RGBColor();
        c.red = parseInt(h.substring(0, 2), 16);
        c.green = parseInt(h.substring(2, 4), 16);
        c.blue = parseInt(h.substring(4, 6), 16);
        return c;
    }
    if (spec.r !== undefined) {
        c = new RGBColor();
        c.red = spec.r; c.green = spec.g; c.blue = spec.b;
        return c;
    }
    if (spec.c !== undefined) {
        c = new CMYKColor();
        c.cyan = spec.c; c.magenta = spec.m; c.yellow = spec.y; c.black = spec.k;
        return c;
    }
    if (spec.gray !== undefined) {
        c = new GrayColor();
        c.gray = spec.gray;
        return c;
    }
    dmFail("color needs hex, r/g/b, c/m/y/k or gray");
}

function dtNear(a, b) {
    return typeof a === "number" && typeof b === "number" && Math.abs(a - b) <= 0.51;
}

/** True when the read-back color matches the request (same model only). */
function dtColorMatches(spec, got) {
    if (!got) return false;
    if (spec.hex || spec.r !== undefined) {
        var want = dtMakeColor(spec);
        return got.typename === "RGBColor" && dtNear(got.red, want.red) &&
            dtNear(got.green, want.green) && dtNear(got.blue, want.blue);
    }
    if (spec.c !== undefined) {
        return got.typename === "CMYKColor" && dtNear(got.cyan, spec.c) && dtNear(got.magenta, spec.m) &&
            dtNear(got.yellow, spec.y) && dtNear(got.black, spec.k);
    }
    return got.typename === "GrayColor" && dtNear(got.gray, spec.gray);
}

/** True when the document's color mode converts this color on assignment. */
function dtColorConverts(doc, spec) {
    var cmykDoc = String(doc.documentColorSpace).indexOf("CMYK") >= 0;
    if (spec.hex || spec.r !== undefined) return cmykDoc;
    if (spec.c !== undefined) return !cmykDoc;
    return false;
}

/**
 * Style a character range and/or its paragraphs in one or more frames.
 * P.uuids; P.start (0-based, frame-relative, default 0); P.length
 * (default: to the end of the frame's text).
 * Character attributes: P.font, P.size, P.color, P.tracking.
 * Paragraph attributes (every paragraph the range touches):
 * P.space_before, P.space_after.
 */
function dtStyleRange(doc, P) {
    dtCheckDocument(doc, P);
    var wantChar = P.font !== undefined || P.size !== undefined || P.color !== undefined || P.tracking !== undefined;
    var wantPara = P.space_before !== undefined || P.space_after !== undefined;
    if (!wantChar && !wantPara) dmFail("Nothing to style: pass font, size, color, tracking, space_before or space_after");
    var font = null;
    if (P.font !== undefined) {
        try {
            font = app.textFonts.getByName(String(P.font));
        } catch (e) {
            dmFail("font '" + P.font + "' is not an installed font (use its PostScript name)");
        }
        if (!dmFontStatus(font).available) dmFail("font '" + P.font + "' is not installed");
    }
    var color = P.color ? dtMakeColor(P.color) : null;

    var res = dmResolveUuids(doc, P.uuids || []);
    var failed = res.missing;
    var skipped = [];
    var objects = [];

    for (var i = 0; i < res.items.length; i++) {
        var tf = res.items[i];
        var uuid = dmUuid(tf);
        if (tf.typename !== "TextFrame") {
            failed.push({ uuid: uuid, reason: "not_a_text_frame", type: tf.typename });
            continue;
        }
        var block = dtBlockReason(tf);
        if (block) {
            skipped.push(dtSkip(tf, block));
            continue;
        }
        try {
            var total = tf.characters.length;
            var start = P.start || 0;
            var len = (P.length === undefined || P.length === null) ? total - start : P.length;
            if (start < 0 || len < 1 || start + len > total) {
                failed.push({ uuid: uuid, reason: "range_out_of_bounds",
                    detail: "start " + start + " + length " + len + " exceeds the frame's " + total + " characters" });
                continue;
            }
            var range = dtRange(tf, start, len);
            var mismatches = [];
            var readBack = {};
            if (wantChar) {
                var ca = range.characterAttributes;
                if (font) ca.textFont = font;
                if (P.size !== undefined) ca.size = P.size;
                if (P.tracking !== undefined) ca.tracking = P.tracking;
                if (color) ca.fillColor = color;
                // Read back at both ends of the range.
                var ends = [tf.characters[start], tf.characters[start + len - 1]];
                for (var e = 0; e < ends.length; e++) {
                    var got = ends[e].characterAttributes;
                    if (font && String(got.textFont.name) !== String(font.name)) mismatches.push("font");
                    if (P.size !== undefined && !dtNear(got.size, P.size)) mismatches.push("size");
                    if (P.tracking !== undefined && got.tracking !== P.tracking) mismatches.push("tracking");
                    if (color && !dtColorConverts(doc, P.color) && !dtColorMatches(P.color, got.fillColor)) mismatches.push("color");
                }
                var first = tf.characters[start].characterAttributes;
                readBack.font = String(first.textFont.name);
                readBack.size = dmRound(first.size);
                readBack.tracking = first.tracking;
                readBack.color = dmColor(first.fillColor);
                if (color && dtColorConverts(doc, P.color)) {
                    readBack.color_converted = "The document's color mode converted the requested color; see color";
                }
            }
            if (wantPara) {
                var paras = range.paragraphs;
                readBack.paragraphs = [];
                for (var p = 0; p < paras.length; p++) {
                    var pa = paras[p].paragraphAttributes;
                    if (P.space_before !== undefined) pa.spaceBefore = P.space_before;
                    if (P.space_after !== undefined) pa.spaceAfter = P.space_after;
                }
                for (p = 0; p < paras.length; p++) {
                    var pb = paras[p].paragraphAttributes;
                    if (P.space_before !== undefined && !dtNear(pb.spaceBefore, P.space_before)) mismatches.push("space_before");
                    if (P.space_after !== undefined && !dtNear(pb.spaceAfter, P.space_after)) mismatches.push("space_after");
                    readBack.paragraphs.push({ space_before: dmRound(pb.spaceBefore), space_after: dmRound(pb.spaceAfter) });
                }
            }
            if (mismatches.length) {
                failed.push({ uuid: uuid, reason: "verify_failed", attributes: mismatches, read_back: readBack });
                continue;
            }
            objects.push({ uuid: uuid, start: start, length: len,
                text: String(range.contents).substring(0, 80), read_back: readBack });
        } catch (e2) {
            failed.push({ uuid: uuid, reason: "error", detail: String(e2.message || e2) });
        }
    }
    return dtResult(doc, {
        objects: objects,
        success_count: objects.length,
        fail_count: failed.length,
        failed_objects: failed,
        skipped_objects: skipped
    });
}

// ==================== Outlines ====================

/**
 * Convert text frames to outlines. The frame is replaced by a group of
 * glyph paths; its name and note (including @mcp:id) move to the group.
 */
function dtOutline(doc, P) {
    dtCheckDocument(doc, P);
    var res = dmResolveUuids(doc, P.uuids || []);
    var failed = res.missing;
    var skipped = [];
    var objects = [];
    for (var i = 0; i < res.items.length; i++) {
        var tf = res.items[i];
        var uuid = dmUuid(tf);
        if (tf.typename !== "TextFrame") {
            failed.push({ uuid: uuid, reason: "not_a_text_frame", type: tf.typename });
            continue;
        }
        var block = dtBlockReason(tf);
        if (block) {
            skipped.push(dtSkip(tf, block));
            continue;
        }
        try {
            var name = tf.name;
            var note = tf.note;
            var group = tf.createOutline();
            if (name) group.name = name;
            if (note) group.note = note;
            // Read back: the frame is gone and the group exists.
            if (resolvePageItemByUuid(doc, uuid)) {
                failed.push({ uuid: uuid, reason: "verify_failed", detail: "Text frame still exists after createOutline" });
                continue;
            }
            var gu = dmUuid(group);
            var kids = [];
            var n = group.pageItems.length;
            for (var k = 0; k < n && k < 50; k++) kids.push(dmUuid(group.pageItems[k]));
            objects.push({ uuid_before: uuid, uuid: gu, type: group.typename, name: group.name || "",
                bounds: dmVisibleBounds(group), child_count: n, child_uuids: kids,
                child_uuids_truncated: n > 50 });
        } catch (e) {
            failed.push({ uuid: uuid, reason: "error", detail: String(e.message || e) });
        }
    }
    return dtResult(doc, {
        objects: objects,
        success_count: objects.length,
        fail_count: failed.length,
        failed_objects: failed,
        skipped_objects: skipped
    });
}
