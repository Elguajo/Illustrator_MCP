"""Source-level guards for the CEP panel (no JS test runner in this repo).

Two defects these protect against:

1. Heartbeat started only via ``setInterval(fn, 5000)``, so the first one
   landed ~5s after connect. For that window the bridge could not tell a
   healthy freshly connected panel from a dead one, and the watchdog — which
   arms on the first heartbeat — could not arm at all.
2. ``onclose`` reconnected only when ``event.code !== 1000``. The MCP server
   closes gracefully with 1000, so every server restart left the panel
   permanently disconnected until it was manually reopened.
3. Requests were spliced into ExtendScript as ``JSON.stringify(data)``.
   Chromium's well-formed JSON.stringify leaves U+2028/U+2029 raw, but they
   are ES3 line terminators, so any script body containing one died with
   "Unterminated string constant" (S005) before the host function ran.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

PANEL = Path(__file__).parent.parent / "cep-extension"
SRC = PANEL / "src" / "hooks" / "useMCP.ts"
LITERAL_SRC = PANEL / "src" / "extendscript.ts"
DIST = PANEL / "dist" / "assets" / "index.js"


@pytest.fixture(scope="module")
def src() -> str:
    return SRC.read_text()


class TestHeartbeat:

    def test_heartbeat_is_sent_immediately_on_connect(self, src):
        i_call = src.index("sendHeartbeat();")
        i_interval = src.index("setInterval(sendHeartbeat")
        assert i_call < i_interval, "must fire once before the interval starts"

    def test_heartbeat_still_repeats_on_the_interval(self, src):
        assert "window.setInterval(sendHeartbeat, HEARTBEAT_MS)" in src


class TestReconnect:

    def test_reconnect_is_not_gated_on_close_code(self, src):
        assert "event.code !== 1000" not in src, (
            "a graceful server close (1000) must still reconnect"
        )

    def test_reconnect_is_gated_on_explicit_user_disconnect(self, src):
        assert "manualDisconnect" in src
        assert "if (manualDisconnect.current)" in src
        # set on the way out, cleared on the way in
        assert "manualDisconnect.current = true;" in src
        assert "manualDisconnect.current = false;" in src

    @pytest.mark.skipif(not (PANEL / "node_modules" / "typescript").exists(), reason="needs panel dependencies")
    def test_busy_guard_and_recovery_against_the_real_hook(self):
        subprocess.run(["node", str(PANEL.parent / "tests_jsx" / "test_panel_execution.js")],
                       check=True, capture_output=True, text=True)


# Built with chr(): a raw separator pasted into source is invisible, and inside
# a JS regex literal it is itself a SyntaxError.
LS, PS = chr(0x2028), chr(0x2029)
ES3_LINE_TERMINATORS = ("\n", "\r", LS, PS)


def _node_strips_types() -> bool:
    node = shutil.which("node")
    if not node:
        return False
    out = subprocess.run(
        [node, "-p", "Boolean(process.features.typescript)"],
        capture_output=True, text=True,
    )
    return out.stdout.strip() == "true"


def _to_extendscript_literal(value):
    """Run the panel's real helper under Node and return its output."""
    script = (
        "const { toExtendScriptLiteral } = await import(process.argv[1]);"
        "const value = JSON.parse(process.argv[2]);"
        "process.stdout.write(JSON.stringify(toExtendScriptLiteral(value)));"
    )
    out = subprocess.run(
        ["node", "--input-type=module", "-e", script,
         LITERAL_SRC.as_uri(), json.dumps(value)],
        capture_output=True, text=True, encoding="utf-8", check=True,
    )
    return json.loads(out.stdout)


class TestExtendScriptLiteral:

    @pytest.mark.skipif(not _node_strips_types(),
                        reason="needs Node with built-in TypeScript stripping")
    @pytest.mark.parametrize("value", [
        {"type": "execute", "script": f"// a{LS}b\nvar x = 1;{PS}x;"},
        {f"key{LS}": [PS, {"nested": f"{LS}{PS}"}]},
        {"plain": "no separators", "n": 1, "ok": True, "none": None},
    ])
    def test_literal_is_single_line_es3_and_lossless(self, value):
        literal = _to_extendscript_literal(value)
        for ch in ES3_LINE_TERMINATORS:
            assert ch not in literal, f"raw U+{ord(ch):04X} breaks ES3 parsing"
        assert json.loads(literal) == value

    def test_helper_source_has_no_raw_separators(self):
        text = LITERAL_SRC.read_text(encoding="utf-8")
        assert LS not in text and PS not in text

    def test_every_evalscript_payload_goes_through_the_helper(self, src):
        assert "mcp_handle_request(${toExtendScriptLiteral(data)})" in src
        assert "toExtendScriptLiteral(payload)" in src
        assert "JSON.stringify(data)})" not in src
        assert "JSON.stringify(payload)" not in src


class TestBundleFreshness:
    """dist/ is a build artifact (gitignored); check it only when present."""

    @pytest.mark.skipif(not DIST.exists(), reason="panel not built")
    def test_built_bundle_contains_both_fixes(self):
        js = DIST.read_text()
        # minified: `ae(),j.current=window.setInterval(ae,oe)`
        assert "setInterval" in js
        assert '"heartbeat"' in js
        assert "Retrying" in js
        # the old close-code gate must be gone from the reconnect branch
        retry = js[js.index("Retrying") - 200: js.index("Retrying") + 100]
        assert "!==1000" not in retry and "!==1e3" not in retry

    @pytest.mark.skipif(not DIST.exists(), reason="panel not built")
    def test_bundle_is_not_older_than_source(self):
        for source in (SRC, LITERAL_SRC):
            assert DIST.stat().st_mtime >= source.stat().st_mtime, (
                "dist/ is stale — run `npm run build` in cep-extension/"
            )

    @pytest.mark.skipif(not DIST.exists(), reason="panel not built")
    def test_built_bundle_escapes_line_separators(self):
        js = DIST.read_text(encoding="utf-8")
        assert "\\\\u2028" in js and "\\\\u2029" in js
        assert LS not in js and PS not in js
