"""
test_geo_boolean_style.py — style transfer must reach CompoundPathItem children.

Regression guard for a silent-no-op bug in reconstructRegions():

  A CompoundPathItem has no `filled` / `fillColor` of its own. Assigning them
  raises no error, and reading the value back returns what was just written —
  but the document is never touched, so a `subtract` that produced a hole came
  back unfilled while the tool reported success.

  Verified in Illustrator 30.5.1:
      cp.filled            -> undefined       (before assignment)
      cp.filled = true     -> no exception
      cp.fillColor = blue  -> no exception
      cp.fillColor         -> [40, 120, 200]  (the JS wrapper, not the DOM)
      cp.pathItems[0]      -> still white

  The fix routes every style application through _applyStyleDeep, which walks
  into pathItems for compound paths. These tests pin that structure, since the
  JSX itself only runs inside Illustrator.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GEO_BOOLEAN = ROOT / "illustrator_mcp" / "resources" / "scripts" / "geo_boolean.jsx"


@pytest.fixture(scope="module")
def source() -> str:
    return GEO_BOOLEAN.read_text(encoding="utf-8")


class TestCompoundAwareStyle:
    """reconstructRegions must never style a compound container directly."""

    def test_deep_applier_exists(self, source):
        assert "function _applyStyleDeep(" in source, (
            "_applyStyleDeep is gone — compound paths would be styled on the "
            "container again, which is a silent no-op."
        )

    def test_deep_applier_walks_child_paths(self, source):
        body = source.split("function _applyStyleDeep(", 1)[1].split("\n    }", 1)[0]
        assert 'typename === "CompoundPathItem"' in body, (
            "_applyStyleDeep no longer special-cases CompoundPathItem."
        )
        assert "pathItems" in body, (
            "_applyStyleDeep must apply style to the member paths of a "
            "compound path, not to the compound itself."
        )

    def test_leaf_applier_is_not_called_directly(self, source):
        """Only _applyStyleDeep may dispatch to the leaf applier."""
        deep_body = source.split("function _applyStyleDeep(", 1)[1].split("\n    }", 1)[0]
        all_calls = re.findall(r"_applyStyleLeaf\(", source)
        definitions = re.findall(r"function _applyStyleLeaf\(", source)
        inside_deep = re.findall(r"_applyStyleLeaf\(", deep_body)

        assert len(definitions) == 1, "expected exactly one _applyStyleLeaf definition"
        assert len(all_calls) - len(definitions) == len(inside_deep), (
            "_applyStyleLeaf is called outside _applyStyleDeep. Route it through "
            "_applyStyleDeep so compound paths keep their fill."
        )

    def test_every_reconstruct_call_site_uses_deep(self, source):
        """All four style applications in reconstructRegions go through the wrapper."""
        reconstruct = source.split("function reconstructRegions(", 1)[1]
        deep_calls = re.findall(r"_applyStyleDeep\(", reconstruct)
        # simple region, compound, compound-failed fallback, fallback holes
        assert len(deep_calls) >= 4, (
            f"expected at least 4 _applyStyleDeep call sites in reconstructRegions, "
            f"found {len(deep_calls)}"
        )
