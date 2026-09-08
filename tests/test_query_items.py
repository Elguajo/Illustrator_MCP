"""
test_query_items.py — illustrator_query_items response handling.

query.py had zero direct tests (30% coverage) despite being the tool every
"find items before modifying them" workflow goes through. This covers what
happens to every shape execute_script_with_context can hand back: a clean
report, a pipeline-level error, a report-level error in either the flat
{code, message} or the nested makeError() {ok, error: {...}} shape, and a
malformed JSON body.

Pattern follows tests/test_documents.py's TestExportDocument: patch
execute_script_with_context at the module that calls it.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from illustrator_mcp.tools.query import QueryItemsInput, illustrator_query_items


def _esc(return_value):
    return patch(
        "illustrator_mcp.tools.query.execute_script_with_context",
        AsyncMock(return_value=return_value),
    )


def _envelope(raw: str) -> dict:
    return json.loads(raw)


class TestPayloadConstruction:
    """What gets sent to Illustrator must reflect the caller's targets."""

    @pytest.mark.asyncio
    async def test_targets_are_embedded_in_the_script(self):
        report = {"ok": True, "stats": {}, "warnings": [], "errors": []}
        with _esc({"result": json.dumps(report)}) as esc:
            await illustrator_query_items(QueryItemsInput(
                targets={"type": "layer", "layer": "Background"}
            ))
        script = esc.call_args.kwargs["script"]
        assert '"layer": "Background"' in script
        assert '"type": "layer"' in script

    @pytest.mark.asyncio
    async def test_default_target_is_selection(self):
        report = {"ok": True, "stats": {}, "warnings": [], "errors": []}
        with _esc({"result": json.dumps(report)}) as esc:
            await illustrator_query_items(QueryItemsInput())
        script = esc.call_args.kwargs["script"]
        assert '"type": "selection"' in script

    @pytest.mark.asyncio
    async def test_query_is_always_a_dry_run(self):
        """A read-only tool must never let the JSX side apply mutations."""
        report = {"ok": True, "stats": {}, "warnings": [], "errors": []}
        with _esc({"result": json.dumps(report)}) as esc:
            await illustrator_query_items(QueryItemsInput())
        script = esc.call_args.kwargs["script"]
        assert '"dryRun": true' in script

    @pytest.mark.asyncio
    async def test_include_trace_reaches_the_payload(self):
        report = {"ok": True, "stats": {}, "warnings": [], "errors": []}
        with _esc({"result": json.dumps(report)}) as esc:
            await illustrator_query_items(QueryItemsInput(include_trace=True))
        script = esc.call_args.kwargs["script"]
        assert '"trace": true' in script

    @pytest.mark.asyncio
    async def test_task_pipeline_is_declared_as_an_include(self):
        report = {"ok": True, "stats": {}, "warnings": [], "errors": []}
        with _esc({"result": json.dumps(report)}) as esc:
            await illustrator_query_items(QueryItemsInput())
        assert esc.call_args.kwargs["includes"] == ["task_pipeline"]


class TestSuccessfulQuery:
    @pytest.mark.asyncio
    async def test_ok_report_becomes_ok_envelope(self):
        report = {
            "ok": True,
            "stats": {"itemsProcessed": 2},
            "warnings": [],
            "errors": [],
            "artifacts": {"items": [{"name": "rect_1"}, {"name": "rect_2"}]},
        }
        with _esc({"result": json.dumps(report)}):
            raw = await illustrator_query_items(QueryItemsInput())
        env = _envelope(raw)
        assert env["ok"] is True
        assert env["result"]["stats"]["itemsProcessed"] == 2

    @pytest.mark.asyncio
    async def test_result_may_already_be_a_dict_not_a_json_string(self):
        """execute_script_with_context can hand back a parsed dict directly."""
        report = {"ok": True, "stats": {}, "warnings": [], "errors": []}
        with _esc({"result": report}):
            raw = await illustrator_query_items(QueryItemsInput())
        assert _envelope(raw)["ok"] is True

    @pytest.mark.asyncio
    async def test_warnings_as_plain_strings_pass_through(self):
        report = {"ok": True, "stats": {}, "warnings": ["heads up"], "errors": []}
        with _esc({"result": json.dumps(report)}):
            raw = await illustrator_query_items(QueryItemsInput())
        assert _envelope(raw)["warnings"] == ["heads up"]

    @pytest.mark.asyncio
    async def test_warnings_as_dicts_are_flattened_to_their_message(self):
        report = {
            "ok": True, "stats": {}, "errors": [],
            "warnings": [{"message": "structured warning", "code": "W001"}],
        }
        with _esc({"result": json.dumps(report)}):
            raw = await illustrator_query_items(QueryItemsInput())
        assert _envelope(raw)["warnings"] == ["structured warning"]


class TestDebugMode:
    @pytest.mark.asyncio
    async def test_debug_returns_the_raw_response_unparsed(self):
        raw_response = {"result": json.dumps({"ok": True, "stats": {}, "warnings": [], "errors": []})}
        with _esc(raw_response):
            raw = await illustrator_query_items(QueryItemsInput(debug=True))
        env = _envelope(raw)
        assert env["ok"] is True
        assert env["result"]["raw_response"] == raw_response
        assert "script_preview" in env["result"]

    @pytest.mark.asyncio
    async def test_debug_does_not_parse_the_report_at_all(self):
        """Debug mode must survive a report shape that would fail normal parsing."""
        with _esc({"result": "not even json"}):
            raw = await illustrator_query_items(QueryItemsInput(debug=True))
        assert _envelope(raw)["ok"] is True


class TestPipelineLevelError:
    """A connection/injection failure from execute_script_with_context itself."""

    @pytest.mark.asyncio
    async def test_pipeline_error_short_circuits_before_parsing(self):
        with _esc({"error": {"code": "C001", "message": "Illustrator is not connected"}}):
            raw = await illustrator_query_items(QueryItemsInput())
        env = _envelope(raw)
        assert env["ok"] is False
        assert env["error"]["code"] == "C001"


class TestReportLevelErrors:
    """Errors surfaced inside the JSX report, after a successful round trip.

    Extraction is delegated to _taskreport_first_error (shared with
    execute_task), which is what makes `suggestions` reach the envelope at
    all — see TestSuggestionsArePreserved below for the regression this
    replaced.
    """

    @pytest.mark.asyncio
    async def test_flat_error_shape(self):
        report = {
            "ok": False, "stats": {}, "warnings": [],
            "errors": [{"code": "R001", "message": "layer not found"}],
        }
        with _esc({"result": json.dumps(report)}):
            raw = await illustrator_query_items(QueryItemsInput())
        env = _envelope(raw)
        assert env["ok"] is False
        assert env["error"]["code"] == "R001"
        assert env["error"]["message"] == "layer not found"

    @pytest.mark.asyncio
    async def test_nested_makeerror_shape_is_unwrapped(self):
        """makeError() produces {ok, error: {code, message}} nested one level deeper."""
        report = {
            "ok": False, "stats": {}, "warnings": [],
            "errors": [{"ok": False, "error": {"code": "R002", "message": "bad target"}}],
        }
        with _esc({"result": json.dumps(report)}):
            raw = await illustrator_query_items(QueryItemsInput())
        env = _envelope(raw)
        assert env["ok"] is False
        assert env["error"]["code"] == "R002"
        assert env["error"]["message"] == "bad target"

    @pytest.mark.asyncio
    async def test_ok_false_with_empty_errors_gets_a_generic_message(self):
        """ok: false with errors: [] has nothing to extract from."""
        report = {"ok": False, "stats": {"itemsProcessed": 0}, "warnings": [], "errors": []}
        with _esc({"result": json.dumps(report)}):
            raw = await illustrator_query_items(QueryItemsInput())
        env = _envelope(raw)
        assert env["ok"] is False
        assert "query_items" in env["error"]["message"]
        assert env["diagnostics"]["stats"] == {"itemsProcessed": 0}

    @pytest.mark.asyncio
    async def test_ok_true_but_errors_present_is_still_reported_as_failure(self):
        """errors takes precedence over a stale/wrong ok:true."""
        report = {
            "ok": True, "stats": {}, "warnings": [],
            "errors": [{"code": "R004", "message": "inconsistent report"}],
        }
        with _esc({"result": json.dumps(report)}):
            raw = await illustrator_query_items(QueryItemsInput())
        env = _envelope(raw)
        assert env["ok"] is False
        assert env["error"]["code"] == "R004"


class TestSuggestionsArePreserved:
    """Regression: the JSX error's `suggestions` used to be silently dropped.

    The reachable branch built `error={"code": ..., "message": ...}` with no
    `suggestions` key at all. A second branch that did keep `suggestions`
    could never run, because it only executed when `errors` was already
    empty — at which point there was nothing left to extract suggestions
    from. Confirmed by reverting to that logic and watching this test fail.
    """

    @pytest.mark.asyncio
    async def test_suggestions_from_a_flat_error_reach_the_envelope(self):
        report = {
            "ok": False, "stats": {}, "warnings": [],
            "errors": [{"code": "R003", "message": "bad target",
                        "suggestions": ["Check the layer name", "List layers with type: 'all'"]}],
        }
        with _esc({"result": json.dumps(report)}):
            raw = await illustrator_query_items(QueryItemsInput())
        env = _envelope(raw)
        assert env["error"]["suggestions"] == [
            "Check the layer name", "List layers with type: 'all'"
        ]

    @pytest.mark.asyncio
    async def test_suggestions_from_a_nested_makeerror_reach_the_envelope(self):
        report = {
            "ok": False, "stats": {}, "warnings": [],
            "errors": [{"ok": False, "error": {
                "code": "R003", "message": "nested", "suggestions": ["retry"],
            }}],
        }
        with _esc({"result": json.dumps(report)}):
            raw = await illustrator_query_items(QueryItemsInput())
        env = _envelope(raw)
        assert env["error"]["code"] == "R003"
        assert env["error"]["suggestions"] == ["retry"]

    @pytest.mark.asyncio
    async def test_an_error_with_no_suggestions_gets_an_empty_list_not_a_missing_key(self):
        report = {
            "ok": False, "stats": {}, "warnings": [],
            "errors": [{"code": "R005", "message": "plain error"}],
        }
        with _esc({"result": json.dumps(report)}):
            raw = await illustrator_query_items(QueryItemsInput())
        env = _envelope(raw)
        assert env["error"]["suggestions"] == []


class TestMalformedResponse:
    @pytest.mark.asyncio
    async def test_unparsable_result_string_becomes_json_parse_error(self):
        with _esc({"result": "{not valid json"}):
            raw = await illustrator_query_items(QueryItemsInput())
        env = _envelope(raw)
        assert env["ok"] is False
        assert env["error"]["code"] == "C005"

    @pytest.mark.asyncio
    async def test_missing_result_key_defaults_to_empty_object(self):
        """No 'result' key at all — proxy_client contract allows this."""
        with _esc({}):
            raw = await illustrator_query_items(QueryItemsInput())
        env = _envelope(raw)
        # {} parses as an empty report: no errors, ok defaults to True.
        assert env["ok"] is True
