"""illustrator_export_document: which formats get the long script timeout.

Found by a live run: an SVG of a document with a few live text frames embeds its fonts and
outlasted the 30 s default (R005) even though the file was written.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from illustrator_mcp.tools._export import illustrator_export_document
from illustrator_mcp.tools._models import ExportDocumentInput


async def _timeout_used(fmt: str):
    params = ExportDocumentInput(file_path=f"/tmp/mcp_export_timeout_test.{fmt}", format=fmt)
    with patch("illustrator_mcp.tools._export.execute_script_with_context",
               new_callable=AsyncMock, return_value={"result": json.dumps({"ok": True})}) as run:
        await illustrator_export_document(params)
    return run.call_args.kwargs["timeout"]


@pytest.mark.asyncio
@pytest.mark.parametrize("fmt,expected", [("pdf", 60.0), ("svg", 120.0), ("png", None), ("jpg", None)])
async def test_long_timeouts_only_for_pdf_and_svg(fmt, expected):
    assert await _timeout_used(fmt) == expected


async def _script_used(fmt: str) -> str:
    params = ExportDocumentInput(file_path=f"/tmp/mcp_export_script_test.{fmt}", format=fmt)
    with patch("illustrator_mcp.tools._export.execute_script_with_context",
               new_callable=AsyncMock, return_value={"result": json.dumps({"ok": True})}) as run:
        await illustrator_export_document(params)
    return run.call_args.kwargs["script"]


@pytest.mark.asyncio
async def test_svg_embeds_only_the_glyphs_used():
    # ExportOptionsSVG.fontSubsetting defaults to ALLGLYPHS: ~949 KB and 1-15 s for one text
    # frame on Illustrator 30.8.1, against 3 KB and 50 ms with GLYPHSUSED (measured live).
    script = await _script_used("svg")
    assert "opts.fontSubsetting = SVGFontSubsetting.GLYPHSUSED;" in script
    assert script.index("new ExportOptionsSVG()") < script.index("SVGFontSubsetting.GLYPHSUSED") < script.index("doc.exportFile")


@pytest.mark.asyncio
@pytest.mark.parametrize("fmt", ["png", "jpg", "pdf"])
async def test_other_formats_do_not_get_svg_options(fmt):
    assert "SVGFontSubsetting" not in await _script_used(fmt)
