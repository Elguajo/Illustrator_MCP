"""Regression test for clip-box preview export file recovery.

Illustrator appends the artboard name to artboard-clipped exports, so the
script has to recover ``tmpX___mcp_clip.<ext>``.  The recovery hardcoded
``.png``: with ``preview_format="jpg"`` the regex was a no-op, ``suffixed``
resolved to the export file itself, and the script deleted its own output.
"""

import pytest

from illustrator_mcp.tools.preview import _build_export_script


@pytest.mark.parametrize("fmt", ["png", "jpg"])
def test_clip_export_recovery_uses_the_requested_extension(fmt):
    script = _build_export_script(f"/tmp/tmpABC.{fmt}", 1024, fmt, clip_box=[0, 0, 10, 10])

    assert f'new RegExp("\\\\.{fmt}$", "i")' in script
    assert f'"___mcp_clip.{fmt}"' in script
    assert f'getFiles(base + "*.{fmt}")' in script

    other = "jpg" if fmt == "png" else "png"
    assert f"___mcp_clip.{other}" not in script
    assert f'*.{other}"' not in script


@pytest.mark.parametrize("fmt", ["png", "jpg"])
def test_clip_export_never_removes_its_own_output(fmt):
    """`suffixed` and `file` may be the same path; deleting then renaming loses it."""
    script = _build_export_script(f"/tmp/tmpABC.{fmt}", 1024, fmt, clip_box=[0, 0, 10, 10])
    assert "suffixed.exists && suffixed.fsName !== file.fsName" in script


@pytest.mark.parametrize("fmt", ["png", "jpg"])
def test_non_clip_export_still_builds(fmt):
    script = _build_export_script(f"/tmp/tmpABC.{fmt}", 1024, fmt, clip_box=None)
    assert "exportFile" in script
    assert "__mcp_clip" not in script
