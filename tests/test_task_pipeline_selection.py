"""task_pipeline.jsx: the Phase 4 selection guard.

Found by a live run: executeTask cleared app.selection right after the collect stage, but the ops
stage resolves {type: "selection"} again from doc.selection, so every selection-targeted task
("Resolved 0 targets") did nothing, and read-only queries wiped the user's selection. The behavior
itself is pinned by tests_live ("selection target ..." steps); this pins the guard's condition,
because the pipeline needs a real Illustrator DOM and the Node fixture has none.
"""

import re
from pathlib import Path

SRC = (Path(__file__).resolve().parent.parent / "illustrator_mcp/resources/scripts/task_pipeline.jsx").read_text(encoding="utf-8")


def _guard() -> str:
    start = SRC.index("var targetsSelection")
    return SRC[start:start + 400]


def test_selection_is_kept_when_the_task_targets_it():
    assert re.search(r'var targetsSelection = !!\(targetObj && targetObj\.type === "selection"\);', _guard())
    assert "if (!targetsSelection && !options.dryRun)" in _guard()


def test_the_only_selection_clear_is_behind_that_condition():
    clears = [m.start() for m in re.finditer(r"app\.selection\s*=\s*null", SRC)]
    assert len(clears) == 1
    assert SRC.index("if (!targetsSelection && !options.dryRun)") < clears[0]
