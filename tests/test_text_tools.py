"""doc_text.jsx (illustrator_text) and the text helpers it shares with doc_model.jsx.

The JSX runs in Node against tests/jsx_doc_fixture.py, whose text engine
mirrors behaviour observed live on Illustrator 30.8.1 (per-character
textRanges, settable range length, first-character style on replacement,
placeholder records for missing fonts).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from illustrator_mcp.tools.text_tools import TextInput, illustrator_text
from tests.jsx_doc_fixture import jsx_value, run_jsx

LIBS = ["mcp_id", "doc_model", "doc_text"]
RED = {"rgb": [200, 0, 0]}


def _frame(uuid, runs, **kw):
    spec = {"type": "TextFrame", "uuid": uuid, "name": kw.pop("name", f"t{uuid}"), "runs": runs}
    spec.update(kw)
    return spec


def _doc(*items, layer=None, **kw):
    lay = {"name": "L", "items": list(items)}
    if layer:
        lay.update(layer)
    spec = {"layers": [lay]}
    spec.update(kw)
    return spec


# Read helper: contents and per-run styles of frame `u` after running `call`.
def _after(call: str, u: str = "1") -> str:
    return (
        "(function(){ var r = " + call + "; var tf = doc.getPageItemFromUuid('" + u + "');"
        " return { res: r, text: tf.story.textRange.contents,"
        " runs: dmFontRuns(tf.story, {fill: true}).runs }; })()"
    )


HELLO = _doc(_frame("1", [
    {"text": "Hello "},
    {"text": "big", "font": "Georgia", "size": 20, "fill": RED, "tracking": 50},
    {"text": " world, hello"},
]))


# ==================== find / replace ====================

class TestReplace:
    def test_replacement_keeps_the_matched_style(self):
        v = jsx_value(LIBS, HELLO, _after('dtReplaceText(doc, {find: "big", replace: "enormous"})'))
        assert v["text"] == "Hello enormous world, hello"
        runs = v["runs"]
        assert [(r["start"], r["length"], r["font"], r["size"]) for r in runs] == [
            (0, 6, "MyriadPro-Regular", 12),
            (6, 8, "Georgia", 20),
            (14, 13, "MyriadPro-Regular", 12),
        ]
        assert runs[1]["fill"]["hex"] == "#c80000"
        assert v["res"]["replaced_count"] == 1
        assert v["res"]["success_count"] == 1 and v["res"]["fail_count"] == 0

    def test_case_insensitive_and_whole_word(self):
        spec = _doc(_frame("1", [{"text": "Cat catalog CAT cat"}]))
        v = jsx_value(LIBS, spec, _after(
            'dtReplaceText(doc, {find: "cat", replace: "dog", case_sensitive: false, whole_word: true})'))
        assert v["text"] == "dog catalog dog dog"
        assert v["res"]["replaced_count"] == 3

    def test_mixed_style_match_is_skipped_and_reported(self):
        spec = _doc(_frame("1", [
            {"text": "ab"}, {"text": "cd", "font": "Georgia"}, {"text": " abcd"},
        ]))
        # first "abcd" spans two styles; the second is uniform
        v = jsx_value(LIBS, spec, _after('dtReplaceText(doc, {find: "abcd", replace: "X"})'))
        assert v["text"] == "abcd X"
        res = v["res"]
        assert res["replaced_count"] == 1
        assert res["skipped_occurrences"] == [{
            "uuid": "1", "index": 0, "text": "abcd", "reason": "mixed_styles",
            "runs": [{"text": "ab", "font": "MyriadPro-Regular", "size": 12},
                     {"text": "cd", "font": "Georgia", "size": 12}],
            "note": res["skipped_occurrences"][0]["note"],
        }]
        assert "replace_runs" in res["skipped_occurrences"][0]["note"]

    def test_replace_runs_writes_each_run_in_its_own_style(self):
        spec = _doc(_frame("1", [
            {"text": "Mission ", "font": "Georgia", "size": 20, "fill": RED},
            {"text": "is to give"},
        ]))
        v = jsx_value(LIBS, spec, _after(
            'dtReplaceText(doc, {find: "Mission is to give", replace_runs: ["Our mission ", "is to provide"]})'))
        assert v["text"] == "Our mission is to provide"
        assert [(r["start"], r["length"], r["font"], r["size"]) for r in v["runs"]] == [
            (0, 12, "Georgia", 20), (12, 13, "MyriadPro-Regular", 12)]
        assert v["runs"][0]["fill"]["hex"] == "#c80000"
        assert v["res"]["replaced_count"] == 1 and v["res"]["skipped_occurrences"] == []
        assert v["res"]["success_count"] == 1 and v["res"]["fail_count"] == 0

    def test_replace_runs_handles_several_matches_and_length_changes(self):
        spec = _doc(_frame("1", [
            {"text": "ab", "font": "Georgia"}, {"text": "cd"}, {"text": " x ab"}, {"text": "cd", "size": 20},
        ]))
        v = jsx_value(LIBS, spec, _after(
            'dtReplaceText(doc, {find: "abcd", replace_runs: ["LONGER", "z"]})'))
        assert v["text"] == "LONGERz x LONGERz"
        assert [(r["start"], r["length"], r["font"], r["size"]) for r in v["runs"]] == [
            (0, 6, "Georgia", 12), (6, 10, "MyriadPro-Regular", 12), (16, 1, "MyriadPro-Regular", 20)]
        assert v["res"]["replaced_count"] == 2

    def test_replace_runs_with_an_empty_piece_removes_only_that_run(self):
        spec = _doc(_frame("1", [{"text": "A-", "font": "Georgia"}, {"text": "B"}]))
        v = jsx_value(LIBS, spec, _after('dtReplaceText(doc, {find: "A-B", replace_runs: ["", "Q"]})'))
        assert v["text"] == "Q"
        assert [(r["start"], r["length"], r["font"]) for r in v["runs"]] == [(0, 1, "MyriadPro-Regular")]

    def test_replace_runs_with_the_wrong_run_count_is_skipped(self):
        spec = _doc(_frame("1", [{"text": "ab"}, {"text": "cd", "font": "Georgia"}]))
        v = jsx_value(LIBS, spec, _after('dtReplaceText(doc, {find: "abcd", replace_runs: ["only one"]})'))
        assert v["text"] == "abcd"
        occ = v["res"]["skipped_occurrences"]
        assert [o["reason"] for o in occ] == ["run_count_mismatch"]
        assert [r["text"] for r in occ[0]["runs"]] == ["ab", "cd"]
        assert v["res"]["replaced_count"] == 0

    def test_replace_runs_on_a_uniform_match_needs_exactly_one_string(self):
        spec = _doc(_frame("1", [{"text": "plain old text"}]))
        v = jsx_value(LIBS, spec, _after('dtReplaceText(doc, {find: "old", replace_runs: ["new"]})'))
        assert v["text"] == "plain new text"

    def test_lost_style_in_a_run_is_caught_by_read_back(self):
        spec = _doc(_frame("1", [{"text": "a", "font": "Georgia"}, {"text": "b"}]))
        expr = ("(function(){ var orig = Object.getOwnPropertyDescriptor(Range.prototype, 'contents');"
                " Object.defineProperty(Range.prototype, 'contents', { get: orig.get,"
                " set: function(v){ orig.set.call(this, v);"
                "   for (var i = this._start; i < this._start + v.length; i++) this._s.chars[i].a.textFont = FONTS['ArialMT']; } });"
                " return dtReplaceText(doc, {find: 'ab', replace_runs: ['x', 'y']}); })()")
        res = jsx_value(LIBS, spec, expr)
        assert res["failed_objects"][0]["reason"] == "style_not_preserved"

    def test_dry_run_changes_nothing_and_lists_matches_with_runs(self):
        spec = _doc(_frame("1", [{"text": "ab"}, {"text": "cd", "font": "Georgia"}, {"text": " abcd"}]))
        v = jsx_value(LIBS, spec, _after('dtReplaceText(doc, {find: "abcd", replace: "X", dry_run: true})'))
        assert v["text"] == "abcd abcd"
        res = v["res"]
        assert res["dry_run"] is True and res["match_count"] == 2 and res["would_replace"] == 1
        assert res["replaced_count"] == 0 and res["changed"] == []
        assert [(m["index"], m["would"]) for m in res["matches"]] == [(0, "skip"), (5, "replace")]
        assert [r["font"] for r in res["matches"][0]["runs"]] == ["MyriadPro-Regular", "Georgia"]
        assert res["matches"][0]["reason"] == "mixed_styles"

    def test_batch_applies_pairs_in_order_and_reports_each(self):
        spec = _doc(_frame("1", [{"text": "Garage and dacha"}]), _frame("2", [{"text": "dacha"}]))
        expr = ('dtReplaceText(doc, {replacements: ['
                '{find: "Garage", replace: "Gate"}, {find: "Gate and", replace: "Gate or"},'
                '{find: "dacha", replace: "cottage"}, {find: "absent", replace: "x"}]})')
        res = jsx_value(LIBS, spec, expr)
        assert [(r["index"], r["replaced_count"]) for r in res["results"]] == [(0, 1), (1, 1), (2, 2), (3, 0)]
        assert res["replaced_count"] == 4
        assert {c["pair"] for c in res["changed"]} == {0, 1, 2}

    def test_batch_pair_can_carry_replace_runs_and_its_own_flags(self):
        spec = _doc(_frame("1", [{"text": "Hi ", "font": "Georgia"}, {"text": "THERE hi there"}]))
        expr = ('(function(){ var r = dtReplaceText(doc, {replacements: ['
                '{find: "Hi THERE", replace_runs: ["Hello ", "YOU"]},'
                '{find: "HI THERE", replace: "zzz", case_sensitive: false}]});'
                ' return { res: r, text: doc.getPageItemFromUuid("1").story.textRange.contents }; })()')
        v = jsx_value(LIBS, spec, expr)
        # the second pair is case-insensitive on its own and sees the first pair's output
        assert v["text"] == "Hello YOU zzz"
        assert [r["replaced_count"] for r in v["res"]["results"]] == [1, 1]

    def test_batch_failures_name_their_pair(self):
        spec = _doc(_frame("1", [{"text": "ab"}, {"text": "cd", "font": "Georgia"}]))
        res = jsx_value(LIBS, spec, 'dtReplaceText(doc, {replacements: [{find: "zz", replace: "y"}, {find: "abcd", replace: "X"}]})')
        assert [o["pair"] for o in res["skipped_occurrences"]] == [1]

    def test_forced_line_break_passes_through_find_and_replace(self):
        br = "String.fromCharCode(3)"
        spec = _doc(_frame("1", [{"text": "one"}, {"text": "\u0003"}, {"text": "two\rthree"}]))
        v = jsx_value(LIBS, spec, _after(
            'dtReplaceText(doc, {find: "one" + ' + br + ' + "two", replace: "1" + ' + br + ' + "2\\n"})'))
        assert v["text"] == "1\u00032\r\rthree"

    def test_empty_replacement_deletes(self):
        spec = _doc(_frame("1", [{"text": "remove THIS please"}]))
        v = jsx_value(LIBS, spec, _after('dtReplaceText(doc, {find: "THIS ", replace: ""})'))
        assert v["text"] == "remove please"

    def test_newline_in_find_matches_a_paragraph_break(self):
        spec = _doc(_frame("1", [{"text": "one\rtwo"}]))
        v = jsx_value(LIBS, spec, _after('dtReplaceText(doc, {find: "one\\ntwo", replace: "one two"})'))
        assert v["text"] == "one two"

    def test_all_frames_by_default(self):
        spec = _doc(_frame("1", [{"text": "old"}]), _frame("2", [{"text": "old and old"}]))
        res = jsx_value(LIBS, spec, 'dtReplaceText(doc, {find: "old", replace: "new"})')
        assert res["replaced_count"] == 3
        assert sorted(c["uuid"] for c in res["changed"]) == ["1", "2"]

    @pytest.mark.parametrize("where", ["item", "layer", "group"])
    def test_locked_frames_are_never_modified(self, where):
        frame = _frame("1", [{"text": "old"}])
        if where == "item":
            spec = _doc(dict(frame, locked=True))
        elif where == "layer":
            spec = _doc(frame, layer={"locked": True})
        else:
            spec = _doc({"type": "GroupItem", "uuid": "9", "locked": True, "children": [frame]})
        v = jsx_value(LIBS, spec, _after('dtReplaceText(doc, {find: "old", replace: "new"})'))
        assert v["text"] == "old"
        assert v["res"]["replaced_count"] == 0
        assert v["res"]["skipped_objects"][0]["reason"] == "locked"
        assert v["res"]["skipped_objects"][0]["matches"] == 1

    def test_hidden_layer_frame_is_skipped(self):
        spec = _doc(_frame("1", [{"text": "old"}]), layer={"visible": False})
        res = jsx_value(LIBS, spec, 'dtReplaceText(doc, {find: "old", replace: "new"})')
        assert res["skipped_objects"][0]["reason"] == "hidden"

    def test_threaded_story_is_one_text_and_processed_once(self):
        a = _frame("1", [{"text": "split across frames"}], kind="TextType.AREATEXT", visible=8)
        b = _frame("2", [{"text": ""}], kind="TextType.AREATEXT")
        spec = _doc(a, b, threads=[["1", "2"]])
        # "across" starts in frame 1 (chars 0-7 visible) and ends in frame 2
        v = jsx_value(LIBS, spec, _after('dtReplaceText(doc, {document: doc.name, uuids: ["1", "2"], find: "across", replace: "over"})'))
        assert v["text"] == "split over frames"
        assert v["res"]["replaced_count"] == 1
        assert v["res"]["changed"] == [{"uuid": "1", "replaced": 1, "frames": 2}]

    def test_unknown_and_non_text_uuids_fail_individually(self):
        spec = _doc(_frame("1", [{"text": "old"}]), {"uuid": "5", "name": "box"})
        res = jsx_value(LIBS, spec, 'dtReplaceText(doc, {document: doc.name, uuids: ["1", "404", "5"], find: "old", replace: "new"})')
        assert res["replaced_count"] == 1
        assert {f["uuid"]: f["reason"] for f in res["failed_objects"]} == {
            "404": "not_found", "5": "not_a_text_frame"}
        assert res["fail_count"] == 2

    def test_silent_no_op_write_is_caught_by_read_back(self):
        # Illustrator can accept an assignment without applying it.
        spec = _doc(_frame("1", [{"text": "old"}]))
        expr = ("(function(){ Object.defineProperty(Range.prototype, 'contents', {"
                " get: function(){ return this._s.text().substr(this._start, this._len); },"
                " set: function(v){} });"
                " return dtReplaceText(doc, {find: 'old', replace: 'new'}); })()")
        res = jsx_value(LIBS, spec, expr)
        assert res["replaced_count"] == 0
        assert res["failed_objects"][0]["reason"] == "verify_failed"

    def test_lost_style_is_caught_by_read_back(self):
        # A replacement that comes back in another font is not a success.
        spec = _doc(_frame("1", [{"text": "a "}, {"text": "old", "font": "Georgia"}]))
        expr = ("(function(){ var orig = Object.getOwnPropertyDescriptor(Range.prototype, 'contents');"
                " Object.defineProperty(Range.prototype, 'contents', { get: orig.get,"
                " set: function(v){ orig.set.call(this, v);"
                "   for (var i = this._start; i < this._start + v.length; i++) this._s.chars[i].a.textFont = FONTS['ArialMT']; } });"
                " return dtReplaceText(doc, {find: 'old', replace: 'new'}); })()")
        res = jsx_value(LIBS, spec, expr)
        assert res["failed_objects"][0]["reason"] == "style_not_preserved"
        assert res["success_count"] == 0

    def test_uuids_without_document_are_refused(self):
        res = run_jsx(LIBS, HELLO, 'dtReplaceText(doc, {uuids: ["1"], find: "big", replace: "x"})')
        assert res["ok"] is False and res["user_error"] is True and "require document" in res["message"]

    def test_other_document_is_refused_before_any_change(self):
        res = run_jsx(LIBS, HELLO, _after('dtReplaceText(doc, {document: "other.ai", uuids: ["1"], find: "big", replace: "x"})'))
        assert res["ok"] is False and res["user_error"] is True and "active document is 'test.ai'" in res["message"]
        text = jsx_value(LIBS, HELLO, 'doc.getPageItemFromUuid("1").contents')
        assert text == "Hello big world, hello"

    def test_renamed_or_closed_document_is_not_offered_as_a_switch_target(self):
        # The named document is no longer open: do not suggest switching to it.
        call = 'dtReplaceText(doc, {document: "old.ai", uuids: ["1"], find: "big", replace: "x"})'
        res = run_jsx(LIBS, HELLO, _after("(function(){ app.documents = [doc]; return " + call + "; })()"))
        assert res["ok"] is False and res["user_error"] is True
        assert "No open document is named 'old.ai'" in res["message"] and "active document is 'test.ai'" in res["message"]
        assert "illustrator_document(action='switch'" not in res["message"]

    def test_open_but_inactive_document_is_offered_as_a_switch_target(self):
        call = 'dtReplaceText(doc, {document: "other.ai", uuids: ["1"], find: "big", replace: "x"})'
        expr = "(function(){ app.documents = [doc, {name: 'other.ai'}]; return " + call + "; })()"
        res = run_jsx(LIBS, HELLO, _after(expr))
        assert res["ok"] is False and "illustrator_document(action='switch', name='other.ai')" in res["message"]

    def test_results_name_the_document(self):
        res = jsx_value(LIBS, HELLO, 'dtReplaceText(doc, {find: "big", replace: "x"})')
        assert res["document"] == {"name": "test.ai", "path": None}

    def test_empty_find_is_a_request_error(self):
        res = run_jsx(LIBS, HELLO, 'dtReplaceText(doc, {find: "", replace: "x"})')
        assert res["ok"] is False and res["user_error"] is True


# ==================== font replacement ====================

MISSING = {"name": "ZzqMissing-Bold", "family": "XPUYQY+ZzqMissing-Bold"}


class TestReplaceFont:
    def _spec(self, **frame_kw):
        return _doc(
            _frame("1", [{"text": "Head ", "font": "ZzqMissing-Bold", "size": 30},
                         {"text": "body", "font": "Georgia"},
                         {"text": " tail", "font": "ZzqMissing-Bold", "size": 9}], **frame_kw),
            _frame("2", [{"text": "untouched", "font": "Georgia"}]),
            missing_fonts=[MISSING],
        )

    def test_missing_font_is_replaced_and_other_runs_are_untouched(self):
        v = jsx_value(LIBS, self._spec(), _after(
            'dtReplaceFont(doc, {from_font: "ZzqMissing-Bold", to_font: "ArialMT"})'))
        assert [(r["font"], r["size"], r["length"]) for r in v["runs"]] == [
            ("ArialMT", 30, 5), ("Georgia", 12, 4), ("ArialMT", 9, 5)]
        res = v["res"]
        assert (res["runs_replaced"], res["chars_replaced"]) == (2, 10)
        assert res["changed"] == [{"uuid": "1", "runs": 2, "chars": 10}]
        assert res["success_count"] == 2  # both stories examined, one changed

    def test_to_font_must_be_installed(self):
        res = run_jsx(LIBS, self._spec(), 'dtReplaceFont(doc, {from_font: "ZzqMissing-Bold", to_font: "NopeFont"})')
        assert res["ok"] is False and res["user_error"] is True
        assert "not an installed font" in res["message"]

    def test_locked_frame_is_reported_with_its_runs(self):
        v = jsx_value(LIBS, self._spec(locked=True), _after(
            'dtReplaceFont(doc, {from_font: "ZzqMissing-Bold", to_font: "ArialMT"})'))
        assert v["runs"][0]["font"] == "ZzqMissing-Bold"
        sk = v["res"]["skipped_objects"]
        assert sk[0]["uuid"] == "1" and sk[0]["reason"] == "locked" and sk[0]["runs"] == 2

    def test_silent_no_op_font_write_is_caught(self):
        expr = ("(function(){ var orig = charAttrs; charAttrs = function(s, st, len){ var o = orig(s, st, len);"
                " Object.defineProperty(o, 'textFont', { get: function(){ return s.chars[st].a.textFont; }, set: function(){} });"
                " return o; };"
                " return dtReplaceFont(doc, {from_font: 'ZzqMissing-Bold', to_font: 'ArialMT'}); })()")
        res = jsx_value(LIBS, self._spec(), expr)
        assert res["runs_replaced"] == 0
        assert res["failed_objects"][0]["reason"] == "verify_failed"
        assert res["failed_objects"][0]["chars_still_in_from_font"] == 10


# ==================== styling ====================

class TestStyle:
    def test_character_range_styling_reads_back(self):
        spec = _doc(_frame("1", [{"text": "Hello big world"}]))
        call = ('dtStyleRange(doc, {document: doc.name, uuids: ["1"], start: 6, length: 3, font: "Georgia", size: 20,'
                ' tracking: 50, color: {hex: "#C80000"}})')
        v = jsx_value(LIBS, spec, _after(call))
        rb = v["res"]["objects"][0]["read_back"]
        assert rb["font"] == "Georgia" and rb["size"] == 20 and rb["tracking"] == 50
        assert rb["color"]["hex"] == "#c80000"
        assert [(r["start"], r["length"], r["font"]) for r in v["runs"]] == [
            (0, 6, "MyriadPro-Regular"), (6, 3, "Georgia"), (9, 6, "MyriadPro-Regular")]

    def test_paragraph_spacing_applies_to_touched_paragraphs_only(self):
        spec = _doc(_frame("1", [{"text": "one\rtwo\rthree"}]))
        expr = ("(function(){ var r = dtStyleRange(doc, {document: doc.name, uuids: ['1'], start: 5, length: 1, space_before: 6, space_after: 12});"
                " return { res: r, paras: doc.getPageItemFromUuid('1').story_.paraAttrs }; })()")
        v = jsx_value(LIBS, spec, expr)
        assert v["paras"] == [{"spaceBefore": 0, "spaceAfter": 0},
                              {"spaceBefore": 6, "spaceAfter": 12},
                              {"spaceBefore": 0, "spaceAfter": 0}]
        assert v["res"]["objects"][0]["read_back"]["paragraphs"] == [{"space_before": 6, "space_after": 12}]

    def test_range_out_of_bounds_fails_that_frame(self):
        spec = _doc(_frame("1", [{"text": "short"}]))
        res = jsx_value(LIBS, spec, 'dtStyleRange(doc, {document: doc.name, uuids: ["1"], start: 3, length: 10, size: 9})')
        assert res["failed_objects"][0]["reason"] == "range_out_of_bounds"

    def test_rgb_in_cmyk_document_is_converted_not_failed(self):
        spec = _doc(_frame("1", [{"text": "ink"}]), cmyk=True)
        expr = ("(function(){ var orig = charAttrs; charAttrs = function(s, st, len){ var o = orig(s, st, len);"
                " Object.defineProperty(o, 'fillColor', { get: function(){ return s.chars[st].a.fillColor; },"
                "  set: function(v){ var c = new CMYKColor(); c.magenta = 100; c.yellow = 100;"
                "   for (var i = st; i < st + len; i++) s.chars[i].a.fillColor = c; } });"
                " return o; };"
                " return dtStyleRange(doc, {document: doc.name, uuids: ['1'], color: {hex: '#ff0000'}}); })()")
        res = jsx_value(LIBS, spec, expr)
        assert res["success_count"] == 1
        rb = res["objects"][0]["read_back"]
        assert rb["color"]["model"] == "cmyk" and "color_converted" in rb

    def test_silent_no_op_size_is_caught(self):
        spec = _doc(_frame("1", [{"text": "abc"}]))
        expr = ("(function(){ var orig = charAttrs; charAttrs = function(s, st, len){ var o = orig(s, st, len);"
                " Object.defineProperty(o, 'size', { get: function(){ return s.chars[st].a.size; }, set: function(){} });"
                " return o; };"
                " return dtStyleRange(doc, {document: doc.name, uuids: ['1'], size: 30}); })()")
        res = jsx_value(LIBS, spec, expr)
        assert res["success_count"] == 0
        assert res["failed_objects"][0]["attributes"] == ["size", "size"]

    def test_locked_frame_is_skipped(self):
        spec = _doc(_frame("1", [{"text": "abc"}], locked=True))
        res = jsx_value(LIBS, spec, 'dtStyleRange(doc, {document: doc.name, uuids: ["1"], size: 30})')
        assert res["skipped_objects"][0]["reason"] == "locked"
        assert res["success_count"] == 0

    def test_font_must_be_installed(self):
        spec = _doc(_frame("1", [{"text": "abc"}]))
        res = run_jsx(LIBS, spec, 'dtStyleRange(doc, {document: doc.name, uuids: ["1"], font: "Gone-Bold"})')
        assert res["ok"] is False and res["user_error"] is True
        assert "is not an installed font" in res["message"]


# ==================== outlines ====================

class TestOutline:
    def test_outline_returns_the_group_and_carries_identity(self):
        spec = _doc(_frame("1", [{"text": "Out"}], name="logo", note="@mcp:id=logo_1", b=[10, 10, 60, 30]))
        expr = ("(function(){ var r = dtOutline(doc, {document: doc.name, uuids: ['1']});"
                " return { res: r, gone: resolvePageItemByUuid(doc, '1') === null,"
                " group: dmNode(doc.getPageItemFromUuid(r.objects[0].uuid)) }; })()")
        v = jsx_value(LIBS, spec, expr)
        obj = v["res"]["objects"][0]
        assert obj["uuid_before"] == "1" and obj["type"] == "GroupItem"
        assert obj["child_count"] == 2 and len(obj["child_uuids"]) == 2
        assert obj["bounds"] == [10, 10, 60, 30]
        assert v["gone"] is True
        assert v["group"]["name"] == "logo" and v["group"]["mcp_id"] == "logo_1"

    def test_locked_and_hidden_frames_are_skipped(self):
        spec = _doc(_frame("1", [{"text": "a"}], locked=True), _frame("2", [{"text": "b"}], hidden=True))
        res = jsx_value(LIBS, spec, 'dtOutline(doc, {document: doc.name, uuids: ["1", "2"]})')
        assert {s["uuid"]: s["reason"] for s in res["skipped_objects"]} == {"1": "locked", "2": "hidden"}
        assert res["success_count"] == 0


# ==================== shared helpers in doc_model.jsx ====================

class TestFontStatus:
    @pytest.mark.parametrize("missing,reason", [
        ({"name": "Zz-Bold", "family": "ABCDEF+Zz-Bold", "placeholder_family": "Zz Bold"}, "embedded_subset_only"),
        ({"name": "Zz-Bold", "family": "Zz", "placeholder_family": "Zz Bold"}, "not_installed"),
        ({"name": "Zz-Bold", "family": "Zz"}, "not_installed"),
    ])
    def test_missing_fonts_are_detected_even_when_getbyname_succeeds(self, missing, reason):
        spec = _doc(_frame("1", [{"text": "x", "font": "Zz-Bold"}]), missing_fonts=[missing])
        run = jsx_value(LIBS, spec, 'dmFontRuns(doc.getPageItemFromUuid("1")).runs[0]')
        assert run["available"] is False and run["missing_reason"] == reason

    def test_known_blind_spot_placeholder_identical_to_the_run(self):
        # Saved without PDF compatibility, the run uses the placeholder record
        # itself; nothing in the DOM tells it apart (see dmFontStatus).
        spec = _doc(_frame("1", [{"text": "x", "font": "Zz-Bold"}]),
                    missing_fonts=[{"name": "Zz-Bold", "family": "Zz Bold", "placeholder_family": "Zz Bold"}])
        run = jsx_value(LIBS, spec, 'dmFontRuns(doc.getPageItemFromUuid("1")).runs[0]')
        assert run["available"] is True


class TestFontRuns:
    def test_runs_are_rebuilt_from_per_character_ranges(self):
        spec = _doc(_frame("1", [{"text": "aa"}, {"text": "bbb", "size": 20}, {"text": "c"}]))
        fr = jsx_value(LIBS, spec, 'dmFontRuns(doc.getPageItemFromUuid("1"))')
        assert [(r["start"], r["length"], r["size"]) for r in fr["runs"]] == [(0, 2, 12), (2, 3, 20), (5, 1, 12)]

    def test_details_find_a_missing_font_after_character_200(self):
        # Phase 1 read only textRanges[0..199], assuming one range per run.
        spec = _doc(_frame("1", [{"text": "x" * 250, "font": "Georgia"}, {"text": "tail", "font": "Zz-Bold"}]),
                    missing_fonts=[{"name": "Zz-Bold", "family": "Zz"}])
        t = jsx_value(LIBS, spec, 'dmTextDetails(doc.getPageItemFromUuid("1"))')
        fonts = {r["font"]: r for r in t["font_runs"]}
        assert fonts["Zz-Bold"]["available"] is False
        assert fonts["Zz-Bold"]["first_char"] == 250
        assert "font_runs_truncated" not in t

    def test_continuation_frame_of_a_thread_is_read_through_its_story(self):
        # Live on AI 30.8.1: in the second frame of a thread, tf.textRanges is
        # indexed by story position, so textRanges[0] throws "The specified
        # text range is invalid" and a missing font there went unreported.
        a = _frame("1", [{"text": "AAAAAA", "font": "Georgia"}, {"text": "BBBB", "font": "Zz-Bold"}],
                   kind="TextType.AREATEXT", visible=6)
        b = _frame("2", [{"text": ""}], kind="TextType.AREATEXT")
        spec = _doc(a, b, threads=[["1", "2"]], missing_fonts=[{"name": "Zz-Bold", "family": "Zz"}])
        fr = jsx_value(LIBS, spec, 'dmFontRuns(doc.getPageItemFromUuid("2"))')
        assert [(r["start"], r["length"], r["font"], r["available"]) for r in fr["runs"]] == [
            (0, 4, "Zz-Bold", False)]
        t = jsx_value(LIBS, spec, 'dmTextDetails(doc.getPageItemFromUuid("2"))')
        assert "font_runs_error" not in t and t["font_runs"][0]["available"] is False

    def test_scan_budget_reports_truncation(self):
        spec = _doc(_frame("1", [{"text": "abcdef"}]))
        fr = jsx_value(LIBS, spec, 'dmFontRuns(doc.getPageItemFromUuid("1"), {max_chars: 4})')
        assert fr["truncated"] is True and fr["scanned"] == 4 and fr["char_count"] == 6


class TestOverflow:
    def _ov(self, spec, u="1"):
        return jsx_value(LIBS, spec, f'dmTextOverflow(doc.getPageItemFromUuid("{u}"))')

    def test_area_text_with_hidden_words_is_overset(self):
        spec = _doc(_frame("1", [{"text": "fits here but this does not"}], kind="TextType.AREATEXT", visible=9))
        ov = self._ov(spec)
        assert ov["overset"] is True and ov["overset_chars"] == 18
        assert ov["overset_preview"].startswith(" but")

    def test_trailing_paragraph_return_is_not_overset(self):
        spec = _doc(_frame("1", [{"text": "Fits\r\r"}], kind="TextType.AREATEXT", visible=4))
        assert self._ov(spec) == {"overset": False}

    def test_point_text_never_overflows(self):
        assert self._ov(_doc(_frame("1", [{"text": "point"}]))) is None

    def test_path_text_can_overflow(self):
        spec = _doc(_frame("1", [{"text": "around the circle and beyond"}], kind="TextType.PATHTEXT", visible=6))
        assert self._ov(spec)["overset"] is True

    def test_threaded_frames_overflow_only_at_the_end(self):
        a = _frame("1", [{"text": "first part, second part, lost words"}], kind="TextType.AREATEXT", visible=12)
        b = _frame("2", [{"text": ""}], kind="TextType.AREATEXT", visible=12)
        spec = _doc(a, b, threads=[["1", "2"]])
        assert self._ov(spec, "1") == {"overset": False, "continues_in": "2"}
        ov = self._ov(spec, "2")
        assert ov["overset"] is True and ov["overset_preview"] == " lost words"

    def test_details_report_overflow(self):
        spec = _doc(_frame("1", [{"text": "fits here but this does not"}], kind="TextType.AREATEXT", visible=9))
        t = jsx_value(LIBS, spec, 'dmTextDetails(doc.getPageItemFromUuid("1"))')
        assert t["overflow"]["overset"] is True


class TestEffectiveState:
    def test_hidden_parent_group_and_locked_layer(self):
        spec = _doc({"type": "GroupItem", "uuid": "9", "hidden": True, "children": [{"uuid": "1"}]},
                    layer={"locked": True})
        st = jsx_value(LIBS, spec, 'dmEffectiveState(doc.getPageItemFromUuid("1"))')
        assert st == {"locked": True, "hidden": True}


# ==================== Python tool layer ====================

class TestTextInput:
    def test_action_specific_requirements(self):
        with pytest.raises(ValidationError):
            TextInput(action="replace", find="a")
        with pytest.raises(ValidationError):
            TextInput(action="replace_font", from_font="A")
        with pytest.raises(ValidationError):
            TextInput(action="style", document="d.ai", uuids=["1"])
        with pytest.raises(ValidationError):
            TextInput(action="outline")
        with pytest.raises(ValidationError):
            TextInput(action="outline", uuids=["1"])  # uuids without document
        TextInput(action="replace", find="a", replace="")
        TextInput(action="replace", find="a", replace_runs=["x", "y"])
        TextInput(action="replace", replacements=[{"find": "a", "replace": ""}, {"find": "b", "replace_runs": ["c"]}])
        TextInput(action="replace", find="a", replace="b", dry_run=True)
        for bad in (
            dict(find="a", replace="b", replace_runs=["c"]),           # both
            dict(find="a", replace_runs=[]),                            # empty runs
            dict(find="a", replace="b", replacements=[{"find": "x", "replace": "y"}]),
            dict(replacements=[{"find": "x"}]),                         # pair without replacement
            dict(replacements=[{"find": "x", "replace": "y", "replace_runs": ["z"]}]),
            dict(replacements=[]),
        ):
            with pytest.raises(ValidationError):
                TextInput(action="replace", **bad)
        with pytest.raises(ValidationError):
            TextInput(action="replace_font", from_font="A", to_font="B", dry_run=True)
        TextInput(action="style", document="d.ai", uuids=["1"], space_after=4)

    def test_color_needs_exactly_one_complete_model(self):
        with pytest.raises(ValidationError):
            TextInput(action="style", document="d.ai", uuids=["1"], color={"hex": "#ff0000", "gray": 10})
        with pytest.raises(ValidationError):
            TextInput(action="style", document="d.ai", uuids=["1"], color={"c": 10, "m": 0})
        with pytest.raises(ValidationError):
            TextInput(action="style", document="d.ai", uuids=["1"], color={"hex": "red"})
        TextInput(action="style", document="d.ai", uuids=["1"], color={"c": 0, "m": 100, "y": 100, "k": 0})


class TestTextDispatch:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("params,call", [
        ({"action": "replace", "find": "a", "replace": "b"}, "dtReplaceText(doc, P)"),
        ({"action": "replace_font", "from_font": "A", "to_font": "B"}, "dtReplaceFont(doc, P)"),
        ({"action": "style", "uuids": ["1"], "document": "d.ai", "size": 9}, "dtStyleRange(doc, P)"),
        ({"action": "outline", "uuids": ["1"], "document": "d.ai"}, "dtOutline(doc, P)"),
    ])
    async def test_each_action_calls_its_function_with_doc_text(self, params, call):
        with patch("illustrator_mcp.tools.doc_model_tools.execute_jsx_tool",
                   AsyncMock(return_value='{"ok": true}')) as m:
            await illustrator_text(TextInput(**params))
        kwargs = m.call_args.kwargs
        assert kwargs["includes"] == ["doc_model", "doc_text"]
        assert call in kwargs["script"]
        assert '"action"' not in kwargs["script"]

    @pytest.mark.asyncio
    async def test_request_error_maps_to_v011(self):
        inner = json.dumps({"ok": True, "result": {"__dm_request_error": "to_font 'X' is not an installed font"}})
        with patch("illustrator_mcp.tools.doc_model_tools.execute_jsx_tool", AsyncMock(return_value=inner)):
            raw = await illustrator_text(TextInput(action="replace_font", from_font="A", to_font="X"))
        env = json.loads(raw)
        assert env["ok"] is False and env["error"]["code"] == "V011"


def test_doc_text_resolves_with_its_dependencies():
    from illustrator_mcp.libraries import get_resolver
    code = get_resolver().resolve(["doc_model", "doc_text"])
    assert "function dtReplaceText" in code
    assert "function dmFontRuns" in code
    assert "function resolvePageItemByUuid" in code
