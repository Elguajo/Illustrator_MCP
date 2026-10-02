"""
Regression: user strings embedded in generated JSX must not carry raw
line terminators.

ExtendScript is ES3, where U+2028 and U+2029 (like \\n and \\r) are line
terminators. Unescaped inside a string literal they raise
"SyntaxError: Unterminated string constant" before the script runs —
confirmed live in Illustrator 30.8. JSON produced with ensure_ascii=False
leaves both raw, so every literal must be emitted escaped.
"""

import json
import re
from unittest.mock import AsyncMock, patch

import pytest

from illustrator_mcp.tools._history import HistoryInput, _handle_checkpoint
from illustrator_mcp.tools.execute import ExecuteScriptInput
from illustrator_mcp.utils.load_script import load_script

LS, PS = " ", " "
TRICKY = f"a{LS}b{PS}c\nd\re\"f\\g é ж \U0001F600"


def _assert_no_raw_terminators(script: str) -> None:
    assert LS not in script
    assert PS not in script


def _params_literal(script: str) -> dict:
    match = re.search(r"var __PARAMS__ = (.*);\n", script)
    assert match, script[:200]
    return json.loads(match.group(1))


class TestExecuteScriptParams:
    @pytest.mark.parametrize("mode", ["__PARAMS_ONLY__", "EXPOSE_VARS"])
    def test_params_are_escaped_and_round_trip(self, mode):
        inp = ExecuteScriptInput(
            script="__PARAMS__.t;", params={"t": TRICKY}, params_mode=mode
        )
        _assert_no_raw_terminators(inp.script)
        assert "\\u2028" in inp.script and "\\u2029" in inp.script
        assert _params_literal(inp.script) == {"t": TRICKY}


class TestLoadScriptParams:
    def test_params_are_escaped_and_round_trip(self):
        script = load_script("polyfills", params={"t": TRICKY})
        _assert_no_raw_terminators(script)
        assert _params_literal(script) == {"t": TRICKY}


class TestCheckpointName:
    @pytest.mark.asyncio
    async def test_name_is_a_safe_string_literal(self):
        mock = AsyncMock(return_value="{}")
        with patch("illustrator_mcp.tools._history.execute_jsx_tool", mock):
            await _handle_checkpoint(
                HistoryInput(action="checkpoint_save", name=TRICKY)
            )
        script = mock.call_args.kwargs["script"]
        _assert_no_raw_terminators(script)
        assert f"checkpointSave({json.dumps(TRICKY)}, " in script
