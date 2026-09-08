"""
test_preflight_check.py — illustrator_preflight_check response handling.

This tool had zero direct tests and 30% coverage — and turned out to be
completely broken. Confirmed live in Illustrator 30.5.1 against a document
carrying a real off-artboard item and a real empty text frame:

    illustrator_preflight_check() -> {"ok": true, "result": {}, ...}

The check that should have found `off_items_sample: ["off_artboard_probe"]`
reported nothing at all, on every call, regardless of document state. Root
cause, isolated by running the exact same countItemsOnArtboard() JSX call
through illustrator_execute_script (which correctly returned the real
off_artboard count) instead of through this tool's own parsing:

    host.jsx's executeScript() wraps a bare script return value as
        {"ok": true, "data": <value>}
    but the Python side read
        cep_result.get("result", "{}")
    — a key that envelope never has — so `preflight_data` defaulted to `{}`
    on every single call.

The fix replaces the hand-rolled double-unwrap with unwrap_jsx_result, the
shared helper execute.py already uses correctly for the same envelope shape.

Envelope shapes below are the real, observed wire format — not guessed:

  success:  {"result": {"ok": true, "data": "<preflight JSON string>"}}
  script threw inside the IIFE: useMCP.ts hoists parsedResult.error to the
      OUTGOING message's top-level "error" field independent of "result", so
      it already reaches this tool as response["error"] — confirmed live by
      passing artboard_index=99: {"ok": false, "error": {"code": "E999", ...}}
      via the pre-existing `if response.get("error")` branch, unrelated to
      the unwrap bug.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from illustrator_mcp.tools.query import (
    PreflightCheckInput,
    illustrator_preflight_check,
)


def _preflight_envelope(preflight_data: dict) -> dict:
    """The real wire shape: host.jsx wraps a bare return as {ok, data}."""
    return {"result": {"ok": True, "data": json.dumps(preflight_data)}}


def _base_preflight_data(**overrides) -> dict:
    data = {
        "document": "Untitled-1",
        "artboard_index": 0,
        "checks": {
            "bounds": {"on_artboard": 1, "off_artboard": 0, "items_checked": 1},
        },
        "issues": [],
        "summary": {"total_items": 1, "issues_found": 0},
    }
    data.update(overrides)
    return data


def _esc(return_value):
    return patch(
        "illustrator_mcp.tools.query.execute_script_with_context",
        AsyncMock(return_value=return_value),
    )


def _envelope(raw: str) -> dict:
    return json.loads(raw)


class TestRealDataReachesTheEnvelope:
    """Regression: preflight_data used to always be {} regardless of input."""

    @pytest.mark.asyncio
    async def test_off_artboard_issue_reaches_the_result(self):
        data = _base_preflight_data(
            checks={"bounds": {"on_artboard": 1, "off_artboard": 1, "items_checked": 2}},
            issues=[{
                "type": "off_artboard", "count": 1,
                "message": "1 items outside artboard bounds",
                "samples": ["off_artboard_probe"],
            }],
            summary={"total_items": 2, "issues_found": 1},
        )
        with _esc(_preflight_envelope(data)):
            raw = await illustrator_preflight_check(PreflightCheckInput())
        env = _envelope(raw)
        assert env["result"]["issues"][0]["samples"] == ["off_artboard_probe"]
        assert env["result"]["summary"]["issues_found"] == 1

    @pytest.mark.asyncio
    async def test_clean_document_has_no_issues_and_is_ok(self):
        with _esc(_preflight_envelope(_base_preflight_data())):
            raw = await illustrator_preflight_check(PreflightCheckInput())
        env = _envelope(raw)
        assert env["ok"] is True
        assert env["result"]["issues"] == []

    @pytest.mark.asyncio
    async def test_result_survives_even_when_real_issues_are_found(self):
        """Guards against the fix regressing to always-empty in a new way.

        This is also a regression guard for a second, dormant defect: ok was
        previously computed from issue severity, and make_envelope's contract
        is `result if ok else None` — so a real finding produced
        {ok:false, error:null, result:null}, discarding the very data the
        tool exists to report. That path was never exercised in production
        because the unwrap bug above always fed it an empty {}. See
        query.py's preflight ok= comment for the full account.
        """
        data = _base_preflight_data(
            issues=[{"type": "zero_size", "count": 3, "message": "3 items have zero width or height"}],
        )
        with _esc(_preflight_envelope(data)):
            raw = await illustrator_preflight_check(PreflightCheckInput())
        env = _envelope(raw)
        assert env["result"] != {}
        assert env["result"]["issues"][0]["type"] == "zero_size"
        assert env["error"] is None


class TestOkReflectsWhetherTheCheckRan:
    """A read-only diagnostic's `ok` means "the check completed", per its own
    documented contract — not "the document is issue-free". Findings surface
    through `warnings` and the full `result` payload instead.
    """

    @pytest.mark.asyncio
    async def test_a_real_finding_still_reports_ok_true(self):
        data = _base_preflight_data(
            issues=[{"type": "empty_text", "message": "2 empty text frames"}],
        )
        with _esc(_preflight_envelope(data)):
            raw = await illustrator_preflight_check(PreflightCheckInput())
        env = _envelope(raw)
        assert env["ok"] is True
        assert "2 empty text frames" in env["warnings"]

    @pytest.mark.asyncio
    async def test_info_severity_issue_produces_no_warning(self):
        """Locked layers are informational, not a finding to act on."""
        data = _base_preflight_data(
            issues=[{"type": "locked", "message": "1 locked layers, 0 locked items", "severity": "info"}],
        )
        with _esc(_preflight_envelope(data)):
            raw = await illustrator_preflight_check(PreflightCheckInput())
        env = _envelope(raw)
        assert env["ok"] is True
        assert env["warnings"] == []

    @pytest.mark.asyncio
    async def test_mixed_info_and_real_issues_only_warns_on_real_ones(self):
        data = _base_preflight_data(
            issues=[
                {"type": "locked", "message": "locked stuff", "severity": "info"},
                {"type": "off_artboard", "message": "1 items outside artboard bounds"},
            ],
        )
        with _esc(_preflight_envelope(data)):
            raw = await illustrator_preflight_check(PreflightCheckInput())
        env = _envelope(raw)
        assert env["ok"] is True
        assert env["warnings"] == ["1 items outside artboard bounds"]


class TestScriptLevelFailure:
    """A JSX exception inside the IIFE — confirmed live with artboard_index=99."""

    @pytest.mark.asyncio
    async def test_hoisted_top_level_error_short_circuits_before_parsing(self):
        response = {
            "result": {"ok": False, "error": {"code": "E999", "message": "boom"}},
            "error": {"code": "E999", "message": "boom", "line": 234},
        }
        with _esc(response):
            raw = await illustrator_preflight_check(PreflightCheckInput(artboard_index=99))
        env = _envelope(raw)
        assert env["ok"] is False
        assert env["error"]["code"] == "E999"

    @pytest.mark.asyncio
    async def test_connection_error_short_circuits_the_same_way(self):
        with _esc({"error": {"code": "C001", "message": "Illustrator is not connected"}}):
            raw = await illustrator_preflight_check(PreflightCheckInput())
        env = _envelope(raw)
        assert env["ok"] is False
        assert env["error"]["code"] == "C001"


class TestMalformedResponse:
    @pytest.mark.asyncio
    async def test_unparsable_data_string_is_caught_not_raised(self):
        with _esc({"result": {"ok": True, "data": "{not valid json"}}):
            raw = await illustrator_preflight_check(PreflightCheckInput())
        env = _envelope(raw)
        # unwrap_jsx_result swallows the parse failure and returns {}; the
        # tool must still produce a well-formed envelope, not raise.
        assert isinstance(env["ok"], bool)

    @pytest.mark.asyncio
    async def test_response_shaped_so_none_of_the_gets_apply_hits_the_outer_except(self):
        """A response that is a list, not a dict, reaches the outer except."""
        with _esc(["not", "a", "dict"]):
            raw = await illustrator_preflight_check(PreflightCheckInput())
        env = _envelope(raw)
        assert env["ok"] is False
        assert env["error"]["code"] == "R013"


class TestScriptParameterInjection:
    """User-supplied strings must be JSON-escaped, not string-interpolated."""

    @pytest.mark.asyncio
    async def test_bounds_type_is_json_escaped_in_the_script(self):
        with _esc(_preflight_envelope(_base_preflight_data())) as esc:
            await illustrator_preflight_check(PreflightCheckInput(bounds_type="visible"))
        script = esc.call_args.kwargs["script"]
        assert '"visible"' in script

    @pytest.mark.asyncio
    async def test_a_quote_in_a_string_param_cannot_break_out_of_the_script(self):
        malicious = 'x"; app.activeDocument.pageItems[0].remove(); var y = "'
        with _esc(_preflight_envelope(_base_preflight_data())) as esc:
            await illustrator_preflight_check(PreflightCheckInput(policy=malicious))
        script = esc.call_args.kwargs["script"]
        # A naive f'"{malicious}"' interpolation would let the embedded quote
        # close the string literal early, exposing the payload as executable
        # code. json.dumps escapes it instead, so the raw closing sequence
        # (unescaped quote immediately before the semicolon) must be absent
        # while the properly-escaped form is present.
        naive_interpolation = f'"{malicious}"'
        assert naive_interpolation not in script
        assert json.dumps(malicious) in script

    @pytest.mark.asyncio
    async def test_null_artboard_index_becomes_the_bare_js_null(self):
        with _esc(_preflight_envelope(_base_preflight_data())) as esc:
            await illustrator_preflight_check(PreflightCheckInput(artboard_index=None))
        script = esc.call_args.kwargs["script"]
        assert "var abIdx = null;" in script

    @pytest.mark.asyncio
    async def test_explicit_artboard_index_is_embedded_as_a_number(self):
        with _esc(_preflight_envelope(_base_preflight_data())) as esc:
            await illustrator_preflight_check(PreflightCheckInput(artboard_index=2))
        script = esc.call_args.kwargs["script"]
        assert "var abIdx = 2;" in script

    @pytest.mark.asyncio
    async def test_check_flags_gate_their_script_sections(self):
        with _esc(_preflight_envelope(_base_preflight_data())) as esc:
            await illustrator_preflight_check(PreflightCheckInput(
                check_zero_size=False, check_empty_text=False, check_locked=False,
            ))
        script = esc.call_args.kwargs["script"]
        assert "if (false) {" in script
