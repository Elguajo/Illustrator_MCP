"""preflight.jsx: scoped findings, coverage (checks_run / checks_skipped), raw facts.

The seeded scene mirrors the live document used to verify the tool on
Illustrator 30.8.1 (missing font, overset text, off-artboard item, low-PPI
image, hairline, missing link, Registration, white overprint, rich black...).
Runs in Node against tests/jsx_doc_fixture.py.
"""

from __future__ import annotations

import pytest

from tests.jsx_doc_fixture import jsx_value, run_jsx

LIBS = ["mcp_id", "doc_model", "preflight"]
K = {"cmyk": [0, 0, 0, 100]}

SEED = {
    "cmyk": True,
    "missing_fonts": [{"name": "ZzqProbeSans-Regular", "family": "XPUYQY+ZzqProbeSans-Regular",
                       "placeholder_family": "ZzqProbeSans Regular"}],
    "layers": [{"name": "Art", "items": [
        {"type": "TextFrame", "uuid": "1", "name": "probe_headline", "b": [20, 20, 200, 40],
         "runs": [{"text": "Headline in probe font", "font": "ZzqProbeSans-Regular", "size": 18}]},
        {"type": "TextFrame", "uuid": "2", "name": "long_mixed", "b": [20, 60, 400, 70],
         "runs": [{"text": "Body copy in Georgia. " * 11, "font": "Georgia", "size": 6},
                  {"text": "TAIL", "font": "ZzqProbeSans-Regular", "size": 6}]},
        {"type": "TextFrame", "uuid": "3", "name": "overset_box", "b": [20, 100, 100, 130],
         "kind": "TextType.AREATEXT", "visible": 20,
         "runs": [{"text": "This paragraph is far too long to fit inside the frame.", "font": "Georgia"}]},
        {"type": "TextFrame", "uuid": "4", "name": "rich_black_text", "b": [20, 150, 120, 160],
         "runs": [{"text": "Rich black small text", "font": "Georgia", "size": 8, "fill": {"cmyk": [60, 40, 40, 100]}}]},
        {"type": "TextFrame", "uuid": "5", "name": "empty_text", "b": [300, 150, 301, 160], "runs": [{"text": " "}]},
        {"uuid": "6", "name": "off_artboard_box", "b": [800, 50, 840, 90], "fill": {"cmyk": [0, 0, 0, 50]}},
        {"uuid": "7", "name": "partial_box", "b": [580, 300, 640, 340], "fill": {"cmyk": [0, 0, 0, 30]}},
        {"uuid": "8", "name": "hairline_box", "b": [200, 200, 300, 250], "stroke": K, "strokeWidth": 0.1},
        {"type": "PlacedItem", "uuid": "9", "name": "low_ppi_image", "b": [350, 180, 550, 380],
         "pixels": [100, 100], "file": "/imgs/img100_72.png"},
        {"type": "PlacedItem", "uuid": "10", "name": "ok_ppi_image", "b": [350, 20, 422, 92],
         "pixels": [300, 300], "file": "/imgs/img300_300.png"},
        {"type": "PlacedItem", "uuid": "11", "name": "missing_link_image", "b": [450, 20, 500, 70], "file": None},
        {"uuid": "12", "name": "registration_box", "b": [20, 250, 50, 280], "fill": {"spot": "[Registration]"}},
        {"uuid": "13", "name": "white_overprint_box", "b": [80, 250, 110, 280],
         "fill": {"cmyk": [0, 0, 0, 0]}, "fillOverprint": True},
        {"uuid": "14", "name": "stray_point", "b": [150, 260, 150, 260], "points": 1, "width": 0, "height": 0},
        {"uuid": "15", "name": "locked_box", "b": [150, 300, 170, 320], "locked": True},
        {"uuid": "16", "name": "hidden_box", "b": [900, 300, 920, 320], "hidden": True, "points": 1},
    ]}],
}


def _pf(spec=SEED, **P):
    import json
    return jsx_value(LIBS, spec, f"pfRun(doc, {json.dumps(P)})")


def _finding(rep, tag):
    for f in rep["findings"]:
        if f["tag"] == tag:
            return f
    return None


def _uuids(rep, tag):
    f = _finding(rep, tag)
    return sorted((o.get("uuid") or o.get("layer_path") for o in f["affected_objects"]), key=str) if f else []


class TestSeededDocument:
    """Every seeded problem is found, with the right object and raw facts."""

    @pytest.fixture(scope="class")
    def rep(self):
        return _pf()

    def test_missing_font_lists_frames_and_fonts(self, rep):
        f = _finding(rep, "text.missing_font")
        assert f["severity"] == "error" and f["count"] == 2
        objs = {o["uuid"]: o for o in f["affected_objects"]}
        assert set(objs) == {"1", "2"}
        assert objs["2"]["fonts"] == [{"font": "ZzqProbeSans-Regular", "family": "XPUYQY+ZzqProbeSans-Regular",
                                       "reason": "embedded_subset_only", "chars": 4}]
        assert objs["1"]["name"] == "probe_headline" and objs["1"]["layer_path"] == "Art"

    def test_fonts_used_summary(self, rep):
        used = {f["font"]: f for f in rep["fonts_used"]}
        assert used["ZzqProbeSans-Regular"]["available"] is False
        assert used["ZzqProbeSans-Regular"]["frames"] == 2
        assert used["Georgia"]["available"] is True

    def test_overset_text(self, rep):
        f = _finding(rep, "text.overset")
        assert _uuids(rep, "text.overset") == ["3"]
        assert f["affected_objects"][0]["overset_chars"] == 35
        assert f["affected_objects"][0]["kind"] == "areatext"

    def test_off_and_partially_off_artboard(self, rep):
        assert _uuids(rep, "objects.off_artboard") == ["6"]
        assert _finding(rep, "objects.off_artboard")["affected_objects"][0]["bounds"] == [800, 50, 840, 90]
        assert _uuids(rep, "objects.partially_off_artboard") == ["7"]
        assert _finding(rep, "objects.partially_off_artboard")["severity"] == "info"

    def test_low_ppi_image_with_raw_facts(self, rep):
        f = _finding(rep, "images.low_ppi")
        assert _uuids(rep, "images.low_ppi") == ["9"]
        o = f["affected_objects"][0]
        assert o["effective_ppi"] == 36 and (o["pixel_width"], o["pixel_height"]) == (100, 100)
        assert (o["width_pt"], o["height_pt"]) == (200, 200)
        assert o["file"] == "/imgs/img100_72.png"

    def test_hairline_stroke(self, rep):
        f = _finding(rep, "objects.hairline_stroke")
        assert _uuids(rep, "objects.hairline_stroke") == ["8"]
        assert f["affected_objects"][0]["stroke_width"] == 0.1

    def test_missing_link(self, rep):
        f = _finding(rep, "links.missing")
        assert _uuids(rep, "links.missing") == ["11"]
        assert f["affected_objects"][0]["reason"] == "no_file"

    def test_color_findings(self, rep):
        assert _uuids(rep, "colors.registration") == ["12"]
        assert _uuids(rep, "colors.overprint_white") == ["13"]
        rich = _finding(rep, "colors.rich_black_text")["affected_objects"][0]
        assert rich["uuid"] == "4" and rich["runs"][0]["color"]["k"] == 100

    def test_object_findings(self, rep):
        assert _uuids(rep, "objects.stray_point") == ["14"]
        assert "14" in _uuids(rep, "objects.zero_size")
        assert _uuids(rep, "text.empty_text") == ["5"]
        assert _uuids(rep, "objects.locked") == ["15"]
        assert _uuids(rep, "objects.hidden") == ["16"]

    def test_hidden_items_are_not_checked(self, rep):
        # hidden_box is off-artboard and a stray point, yet appears only under objects.hidden
        for f in rep["findings"]:
            if f["tag"] != "objects.hidden":
                assert "16" not in [o.get("uuid") for o in f["affected_objects"]], f["tag"]

    def test_every_check_ran_and_findings_are_ordered(self, rep):
        assert rep["checks_skipped"] == []
        assert len(rep["checks_run"]) == 21
        order = [f["severity"] for f in rep["findings"]]
        assert order == sorted(order, key=["error", "warning", "info"].index)
        assert rep["summary"]["by_severity"]["error"] == sum(
            f["count"] for f in rep["findings"] if f["severity"] == "error")

    def test_known_blind_spots_are_stated(self, rep):
        assert "text.missing_font" in rep["check_notes"]
        assert "links.modified" in rep["check_notes"]  # placed items have no link status

    def test_document_info(self, rep):
        info = rep["document_info"]
        assert info["color_mode"] == "CMYK" and info["raster_effects_ppi"] == 300
        assert info["artboards"][0]["bounds"] == [0, 0, 600, 400]


class TestThreadedText:
    def test_missing_font_in_a_continuation_frame_is_reported_not_unreadable(self):
        a = {"type": "TextFrame", "uuid": "1", "name": "a", "b": [10, 10, 90, 40], "kind": "TextType.AREATEXT",
             "visible": 6, "runs": [{"text": "AAAAAA", "font": "Georgia"}, {"text": "BBBB", "font": "ZzqProbeSans-Regular"}]}
        b = {"type": "TextFrame", "uuid": "2", "name": "b", "b": [10, 60, 90, 90], "kind": "TextType.AREATEXT",
             "runs": [{"text": ""}]}
        spec = {"missing_fonts": SEED["missing_fonts"],
                "layers": [{"name": "L", "items": [a, b]}], "threads": [["1", "2"]]}
        rep = _pf(spec, scopes=["text"])
        assert "text.missing_font" in rep["checks_run"] and all(c["check"] != "text.missing_font" for c in rep["checks_skipped"])
        assert _uuids(rep, "text.missing_font") == ["2"]  # BBBB flowed into the second frame


class TestCoverage:
    def test_unrequested_scopes_are_skipped_not_silently_empty(self):
        rep = _pf(scopes=["links"])
        assert rep["checks_run"] == ["links.missing", "links.modified"]
        reasons = {s["check"]: s["reason"] for s in rep["checks_skipped"]}
        assert reasons["text.missing_font"] == "scope_not_requested"
        assert [f["tag"] for f in rep["findings"]] == ["links.missing"]

    def test_legacy_flags_disable_their_checks(self):
        rep = _pf(check_zero_size=False, check_empty_text=False, check_locked=False)
        reasons = {s["check"]: s["reason"] for s in rep["checks_skipped"]}
        assert reasons == {"objects.zero_size": "disabled_by_parameter",
                           "text.empty_text": "disabled_by_parameter",
                           "objects.locked": "disabled_by_parameter"}
        assert _finding(rep, "text.empty_text") is None

    def test_unreadable_object_makes_the_check_partial(self):
        spec = {"layers": [{"name": "L", "items": [{"uuid": "1", "stroke": K}]}]}
        expr = ("(function(){ var it = doc.getPageItemFromUuid('1');"
                " Object.defineProperty(it, 'strokeWidth', { get: function(){ throw new Error('boom'); } });"
                " return pfRun(doc, {scopes: ['objects']}); })()")
        rep = jsx_value(LIBS, spec, expr)
        skipped = {s["check"]: s for s in rep["checks_skipped"]}
        assert skipped["objects.hairline_stroke"]["reason"] == "partial"
        assert skipped["objects.hairline_stroke"]["unreadable_objects"] == 1
        assert "objects.hairline_stroke" not in rep["checks_run"]

    def test_text_budget_makes_font_check_partial(self):
        rep = _pf(scopes=["text"], max_text_chars=30)
        reasons = {s["check"]: s["reason"] for s in rep["checks_skipped"]}
        assert reasons["text.missing_font"].startswith("partial:")
        assert "text.overset" in rep["checks_run"]  # does not depend on the char budget

    def test_item_budget_makes_object_checks_partial(self):
        rep = _pf(scopes=["objects"], max_items=3)
        reasons = {s["check"]: s["reason"] for s in rep["checks_skipped"]}
        assert reasons["objects.off_artboard"].startswith("partial: stopped after max_items=3")

    def test_affected_objects_are_capped_but_count_is_exact(self):
        items = [{"uuid": str(i), "b": [800 + i, 0, 810 + i, 10]} for i in range(1, 8)]
        rep = _pf({"layers": [{"name": "L", "items": items}]}, scopes=["objects"], max_affected=3)
        f = _finding(rep, "objects.off_artboard")
        assert f["count"] == 7 and len(f["affected_objects"]) == 3 and f["truncated"] is True

    def test_empty_category_in_checks_run_means_clean(self):
        spec = {"layers": [{"name": "L", "items": [{"uuid": "1", "b": [10, 10, 50, 50], "fill": K}]}]}
        rep = _pf(spec, scopes=["text", "images", "links"])
        assert rep["findings"] == []
        assert set(rep["checks_run"]) >= {"text.missing_font", "images.low_ppi", "links.missing"}


class TestObjects:
    def test_guides_are_not_zero_size(self):
        spec = {"layers": [{"name": "L", "items": [
            {"uuid": "1", "b": [0, 100, 600, 100], "guides": True, "height": 0},
            {"uuid": "2", "b": [10, 10, 10, 50], "width": 0},
        ]}]}
        assert _uuids(_pf(spec, scopes=["objects"]), "objects.zero_size") == ["2"]

    def test_policy_intersects_drops_partial(self):
        rep = _pf(scopes=["objects"], policy="intersects")
        assert _finding(rep, "objects.partially_off_artboard") is None
        assert _uuids(rep, "objects.off_artboard") == ["6"]

    def test_reference_artboard_index(self):
        spec = {"artboards": [{"name": "A", "b": [0, 0, 100, 100]}, {"name": "B", "b": [200, 0, 300, 100]}],
                "layers": [{"name": "L", "items": [{"uuid": "1", "b": [210, 10, 250, 50]}]}]}
        assert _finding(_pf(spec, scopes=["objects"]), "objects.off_artboard") is None
        assert _uuids(_pf(spec, scopes=["objects"], artboard_index=0), "objects.off_artboard") == ["1"]

    def test_item_scope_artboard_filters_by_centre(self):
        rep = _pf(scopes=["objects", "text"], item_scope="artboard")
        assert _finding(rep, "objects.off_artboard") is None  # its centre is off the artboard
        assert _uuids(rep, "text.overset") == ["3"]

    def test_out_of_range_artboard_is_a_request_error(self):
        res = run_jsx(LIBS, SEED, "pfRun(doc, {artboard_index: 9})")
        assert res["ok"] is False and res["user_error"] is True

    def test_clipping_path_bounds_source(self):
        spec = {"layers": [{"name": "L", "items": [{
            "type": "GroupItem", "uuid": "1", "clipped": True, "b": [550, 10, 700, 60],
            "children": [{"uuid": "2", "clipping": True, "b": [550, 10, 590, 60]}, {"uuid": "3", "b": [550, 10, 700, 60]}],
        }]}]}
        assert _uuids(_pf(spec, scopes=["objects"]), "objects.partially_off_artboard") == ["1"]
        assert _finding(_pf(spec, scopes=["objects"], bounds_source="clipping_path"),
                        "objects.partially_off_artboard") is None

    def test_empty_artboard(self):
        spec = {"artboards": [{"name": "A", "b": [0, 0, 100, 100]}, {"name": "Empty", "b": [200, 0, 300, 100]}],
                "layers": [{"name": "L", "items": [{"uuid": "1", "b": [10, 10, 50, 50]}]}]}
        f = _finding(_pf(spec, scopes=["document"]), "document.empty_artboard")
        assert f["count"] == 1 and f["affected_objects"][0]["artboard"]["name"] == "Empty"


class TestHiddenAndLocked:
    def test_items_on_a_hidden_layer_are_not_checked(self):
        spec = {"layers": [{"name": "Off", "visible": False, "items": [{"uuid": "1", "b": [900, 0, 950, 50]}]}]}
        rep = _pf(spec, scopes=["objects"])
        assert _finding(rep, "objects.off_artboard") is None
        assert _finding(rep, "objects.hidden")["affected_objects"] == [{"kind": "layer", "layer_path": "Off"}]

    def test_children_of_a_hidden_group_are_not_checked(self):
        spec = {"layers": [{"name": "L", "items": [{"type": "GroupItem", "uuid": "1", "hidden": True,
                                                     "b": [10, 10, 20, 20],
                                                     "children": [{"uuid": "2", "stroke": K, "strokeWidth": 0.01}]}]}]}
        rep = _pf(spec, scopes=["objects"])
        assert _finding(rep, "objects.hairline_stroke") is None
        assert _uuids(rep, "objects.hidden") == ["1"]

    def test_locked_layer_is_reported(self):
        spec = {"layers": [{"name": "Base", "locked": True, "items": [{"uuid": "1", "b": [10, 10, 20, 20]}]}]}
        f = _finding(_pf(spec, scopes=["objects"]), "objects.locked")
        assert f["affected_objects"] == [{"kind": "layer", "layer_path": "Base"}]


class TestImagesAndLinks:
    def test_rotated_or_flipped_matrix_uses_magnitudes(self):
        spec = {"layers": [{"name": "L", "items": [{"type": "PlacedItem", "uuid": "1", "b": [0, 0, 200, 200],
                                                     "pixels": [100, 100], "file": "/a.jpg"}]}]}
        expr = ("(function(){ var m = doc.getPageItemFromUuid('1').matrix; m.mValueA = 0; m.mValueB = -2; m.mValueC = 2; m.mValueD = 0;"
                " return pfRun(doc, {scopes: ['images']}); })()")
        rep = jsx_value(LIBS, spec, expr)
        assert _finding(rep, "images.low_ppi")["affected_objects"][0]["effective_ppi"] == 36

    def test_vector_placed_files_are_not_measured(self):
        spec = {"layers": [{"name": "L", "items": [{"type": "PlacedItem", "uuid": "1", "b": [0, 0, 500, 500],
                                                     "pixels": [10, 10], "file": "/logo.pdf"}]}]}
        assert _finding(_pf(spec, scopes=["images"]), "images.low_ppi") is None

    def test_embedded_raster_rgb_in_cmyk_and_low_ppi(self):
        spec = {"cmyk": True, "layers": [{"name": "L", "items": [{
            "type": "RasterItem", "uuid": "1", "b": [0, 0, 300, 300], "pixels": [300, 300],
            "colorSpace": "ImageColorSpace.RGB"}]}]}
        rep = _pf(spec, scopes=["images", "colors"])
        assert _finding(rep, "images.low_ppi")["affected_objects"][0]["embedded"] is True
        assert _finding(rep, "colors.color_model_mismatch")["affected_objects"][0]["image_color_space"] == "RGB"

    def test_linked_raster_modified_and_missing(self):
        spec = {"layers": [{"name": "L", "items": [
            {"type": "RasterItem", "uuid": "1", "embedded": False, "status": "RasterLinkState.DATAMODIFIED", "b": [0, 0, 10, 10]},
            {"type": "PlacedItem", "uuid": "2", "file": "/gone.png", "exists": False, "b": [0, 0, 10, 10]},
        ]}]}
        rep = _pf(spec, scopes=["links"])
        assert _uuids(rep, "links.modified") == ["1"]
        assert _finding(rep, "links.missing")["affected_objects"][0]["reason"] == "file_not_found"


class TestColors:
    def test_total_ink_and_spot_summary(self):
        spec = {"cmyk": True, "layers": [{"name": "L", "items": [
            {"uuid": "1", "fill": {"cmyk": [100, 100, 100, 100]}, "b": [0, 0, 10, 10]},
            {"uuid": "2", "fill": {"spot": "PANTONE 185 C"}, "b": [0, 0, 10, 10]},
            {"uuid": "3", "stroke": {"spot": "PANTONE 185 C"}, "b": [0, 0, 10, 10]},
        ]}]}
        rep = _pf(spec, scopes=["colors"])
        assert _finding(rep, "colors.total_ink")["affected_objects"][0]["inks"][0]["total_ink"] == 400
        assert _finding(rep, "colors.spot_colors")["affected_objects"] == [
            {"kind": "spot", "spot": "PANTONE 185 C", "uses": 2}]

    def test_large_rich_black_text_is_fine(self):
        spec = {"layers": [{"name": "L", "items": [{"type": "TextFrame", "uuid": "1", "b": [0, 0, 100, 40],
                "runs": [{"text": "Big", "size": 36, "fill": {"cmyk": [60, 40, 40, 100]}}]}]}]}
        assert _finding(_pf(spec, scopes=["colors"]), "colors.rich_black_text") is None


def test_preflight_resolves_with_its_dependencies():
    from illustrator_mcp.libraries import get_resolver
    code = get_resolver().resolve(["doc_model", "preflight"])
    assert "function pfRun" in code and "function dmFontRuns" in code
