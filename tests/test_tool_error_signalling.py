"""Tool failures reach the MCP client as isError, without a duplicate structuredContent."""

import json
from unittest.mock import AsyncMock, patch

import pytest
from mcp.types import CallToolResult, ImageContent, TextContent

from illustrator_mcp.app import is_failure_envelope, mark_tool_error
from illustrator_mcp.server import mcp

FAILED = json.dumps({"ok": False, "warnings": [], "error": {"code": "C001", "message": "x"}})
PASSED = json.dumps({"ok": True, "warnings": [], "error": None, "result": {}})


class TestMarkToolError:
    def test_failure_envelope_becomes_is_error(self):
        out = mark_tool_error([TextContent(type="text", text=FAILED)])
        assert isinstance(out, CallToolResult)
        assert out.isError is True
        assert out.content[0].text == FAILED
        assert out.structuredContent is None

    def test_preview_images_are_kept(self):
        image = ImageContent(type="image", data="AAAA", mimeType="image/png")
        out = mark_tool_error([TextContent(type="text", text=FAILED), image])
        assert isinstance(out, CallToolResult)
        assert out.content[1] == image

    @pytest.mark.parametrize("text", [
        PASSED,
        "plain text result",
        "[1, 2]",
        '{"ok": "false"}',
        '{"result": {"ok": false}}',
        "{not json",
    ])
    def test_everything_else_passes_through(self, text):
        content = [TextContent(type="text", text=text)]
        assert mark_tool_error(content) is content

    def test_only_the_first_text_block_decides(self):
        content = [TextContent(type="text", text=PASSED), TextContent(type="text", text=FAILED)]
        assert mark_tool_error(content) is content

    def test_is_failure_envelope_tolerates_leading_whitespace(self):
        assert is_failure_envelope("\n  " + FAILED)


class TestServer:
    @pytest.mark.asyncio
    async def test_no_tool_advertises_an_output_schema(self):
        tools = await mcp.list_tools()
        assert tools
        assert [t.name for t in tools if t.outputSchema is not None] == []

    @pytest.mark.asyncio
    async def test_success_is_plain_content(self):
        out = await mcp.call_tool("illustrator_artboards", {"params": {"action": "presets"}})
        assert not isinstance(out, CallToolResult)
        assert json.loads(out[0].text)["ok"] is True

    @pytest.mark.asyncio
    async def test_failure_is_flagged(self):
        inner = json.dumps({"ok": True, "warnings": [], "error": None, "diagnostics": {},
                            "result": {"__dm_request_error": "Cannot delete the only artboard"}})
        with patch("illustrator_mcp.tools.doc_model_tools.execute_jsx_tool",
                   AsyncMock(return_value=inner)):
            out = await mcp.call_tool("illustrator_artboards", {"params": {"action": "delete"}})
        assert isinstance(out, CallToolResult)
        assert out.isError is True
        assert json.loads(out.content[0].text)["error"]["code"] == "V011"
