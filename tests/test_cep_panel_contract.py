"""Source-level guards for the CEP panel (no JS test runner in this repo).

Two defects these protect against:

1. Heartbeat started only via ``setInterval(fn, 5000)``, so the first one
   landed ~5s after connect. For that window the bridge could not tell a
   healthy freshly connected panel from a dead one, and the watchdog — which
   arms on the first heartbeat — could not arm at all.
2. ``onclose`` reconnected only when ``event.code !== 1000``. The MCP server
   closes gracefully with 1000, so every server restart left the panel
   permanently disconnected until it was manually reopened.
"""

from pathlib import Path

import pytest

PANEL = Path(__file__).parent.parent / "cep-extension"
SRC = PANEL / "src" / "hooks" / "useMCP.ts"
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
        assert DIST.stat().st_mtime >= SRC.stat().st_mtime, (
            "dist/ is stale — run `npm run build` in cep-extension/"
        )
