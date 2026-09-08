"""
test_doc_version_drift.py — the protocol version an agent reads must be true.

The tool docstrings are not prose: they are what the model reads before it
composes a payload. When execute_task's docstring said "Task Protocol v2.1",
the README said v2.3 and TASK_PROTOCOL_VERSION said 3.0.0, three different
answers were in circulation and the one the agent saw was the most wrong.

This pins the docstrings to the constant. Documentation prose is deliberately
not checked: it explains structures rather than declaring the wire version.
"""

import re
from pathlib import Path

from illustrator_mcp.schemas.contracts import TASK_PROTOCOL_VERSION

ROOT = Path(__file__).resolve().parent.parent
TASK_EXECUTION = ROOT / "illustrator_mcp" / "tools" / "task_execution.py"

# "3.0.0" -> "3.0", the form the docstrings use.
_MAJOR_MINOR = ".".join(TASK_PROTOCOL_VERSION.split(".")[:2])


def _protocol_versions_mentioned(text: str) -> set:
    """Every 'Task Protocol vX.Y' the file claims."""
    return set(re.findall(r"Task Protocol v(\d+\.\d+)", text))


class TestToolDocstringsMatchTheConstant:
    def test_execute_task_docstrings_use_the_current_version(self):
        mentioned = _protocol_versions_mentioned(TASK_EXECUTION.read_text(encoding="utf-8"))
        wrong = {v for v in mentioned if v != _MAJOR_MINOR}
        assert not wrong, (
            f"task_execution.py advertises Task Protocol {sorted(wrong)} while "
            f"TASK_PROTOCOL_VERSION is {TASK_PROTOCOL_VERSION}. The docstring is "
            "what the model reads before building a payload — update it, or the "
            "constant if the version really changed."
        )

    def test_the_version_is_actually_mentioned(self):
        """Guards against the check passing because nothing says a version."""
        mentioned = _protocol_versions_mentioned(TASK_EXECUTION.read_text(encoding="utf-8"))
        assert mentioned, "no Task Protocol version in task_execution.py to verify"


class TestPayloadDefaultMatchesTheConstant:
    def test_task_payload_defaults_to_the_constant(self):
        """A payload built with no explicit version must carry the real one."""
        from illustrator_mcp.protocol import TaskPayload

        assert TaskPayload(task="element_create").version == TASK_PROTOCOL_VERSION
