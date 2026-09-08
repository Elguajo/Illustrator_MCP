"""Regression tests for ExtendScript injection via tool parameters.

``illustrator_document(action="create")`` interpolated the caller-supplied
document name straight into a double-quoted ExtendScript string literal, so a
name containing a quote could close the literal and run arbitrary code.
"""

import json

import pytest

from illustrator_mcp import templates
from illustrator_mcp.tools._models import DocumentInput


BREAKOUT = 'x"; app.documents[0].close(SaveOptions.DONOTSAVECHANGES); var q="'


def _title_line(name: str) -> str:
    """Mirror the title_line construction in documents.illustrator_document."""
    return f"preset.title = {json.dumps(name)};"


@pytest.mark.parametrize("name", [
    BREAKOUT,
    'quote " inside',
    "back\\slash",
    "new\nline",
    'both "\\ mixed',
    "unicode   separator",
])
def test_document_name_cannot_escape_the_js_string_literal(name):
    script = templates.DOC_CREATE.substitute(
        width=800, height=600, color_space="RGB", title_line=_title_line(name),
    )
    lines = [l.strip() for l in script.splitlines() if "preset.title" in l]

    # The whole payload stays inside ONE fully-escaped string literal:
    # the emitted statement is exactly `preset.title = <literal>;`.
    assert len(lines) == 1
    literal = json.dumps(name)
    assert lines[0] == f"preset.title = {literal};"

    # ...and that literal decodes back to the original name, unchanged.
    assert json.loads(literal) == name

    # Nothing escaped into executable position.
    assert "app.documents[0].close" not in lines[0].replace(literal, "")


def test_document_create_uses_escaped_title_line():
    """Guard the call site itself, not just the helper."""
    src = (
        __import__("pathlib").Path(__file__).parent.parent
        / "illustrator_mcp" / "tools" / "documents.py"
    ).read_text()
    assert 'f\'preset.title = "{name}";\'' not in src
    assert "json.dumps(name)" in src


def test_document_name_length_is_still_bounded():
    with pytest.raises(Exception):
        DocumentInput(action="create", name="x" * 256)
