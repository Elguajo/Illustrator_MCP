"""place_file with trace: where the @mcp:trace_target marker lands.

Found by a live run: placed.embed() replaces the PlacedItem with a new RasterItem whose note is
empty, while the old reference keeps reading and writing its own note without error. The marker
set after embed() therefore never reached the item the trace step searches for, and every
trace of an embedded (linked=False) image failed with "Trace target not found".
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from illustrator_mcp.tools._place import _place_item_impl


async def _place_script(linked: bool, trace: bool) -> str:
    with patch("illustrator_mcp.tools._place.execute_jsx_tool", new_callable=AsyncMock,
               return_value=json.dumps({"ok": True})) as run:
        await _place_item_impl("/tmp/x.png", 0, 0, linked, "place_file", "illustrator_place_file", trace=trace)
    return run.call_args_list[0].kwargs["script"]


@pytest.mark.asyncio
async def test_embedded_trace_marks_the_raster_that_embed_created():
    script = await _place_script(linked=False, trace=True)
    embed = script.index("placed.embed()")
    assert script.index("__gb = placed.geometricBounds") < embed          # bounds read while it is still placed
    assert script.index("doc.rasterItems", embed) > embed                 # marker goes to a RasterItem after embed
    assert "@mcp:trace_target=" in script[embed:]
    assert "placed.note" not in script[embed:]                            # not to the stale reference


@pytest.mark.asyncio
async def test_linked_trace_still_marks_the_placed_item():
    script = await _place_script(linked=True, trace=True)
    assert 'placed.note = "@mcp:trace_target=' in script
    assert "embed()" not in script


@pytest.mark.asyncio
async def test_embedded_without_trace_gets_no_marker():
    script = await _place_script(linked=False, trace=False)
    assert "placed.embed();" in script and "trace_target" not in script
