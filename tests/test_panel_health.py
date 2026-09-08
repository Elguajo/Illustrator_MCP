"""Panel health must distinguish 'no heartbeat yet' from 'heartbeat went stale'.

The CEP panel starts its heartbeat with ``setInterval(fn, 5000)``, so the first
heartbeat arrives ~5s AFTER connect.  During that window ``_last_heartbeat`` is
still 0.0.  Reporting that as plain ``stale`` made the timeout handler tell the
user the panel "may be frozen or crashed. Try reconnecting" — wrong and
actively misleading for a healthy, freshly connected panel.
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from illustrator_mcp.websocket_bridge import WebSocketBridge


def _bridge() -> WebSocketBridge:
    b = WebSocketBridge(port=18099)
    b.server = MagicMock()
    return b


class TestPanelHealthStates:

    def test_never_received_heartbeat_is_flagged_separately(self):
        b = _bridge()
        assert b._last_heartbeat == 0.0
        h = b.get_panel_health()
        assert h["heartbeat_seen"] is False
        assert h["last_heartbeat_ago_ms"] is None

    def test_fresh_heartbeat_is_seen_and_not_stale(self):
        b = _bridge()
        b._last_heartbeat = time.time()
        h = b.get_panel_health()
        assert h["heartbeat_seen"] is True
        assert h["stale"] is False

    def test_old_heartbeat_is_seen_and_stale(self):
        b = _bridge()
        b._last_heartbeat = time.time() - 3600
        h = b.get_panel_health()
        assert h["heartbeat_seen"] is True
        assert h["stale"] is True


class TestTimeoutDiagnostics:
    """The message the user actually reads on a timeout."""

    @pytest.mark.asyncio
    async def _timeout_detail(self, health: dict) -> str:
        import json

        from illustrator_mcp import proxy_client

        bridge = MagicMock()
        bridge.is_connected.return_value = True
        bridge.get_panel_health.return_value = health
        bridge.loop = MagicMock()

        with patch.object(proxy_client, "_get_bridge", return_value=bridge), \
             patch.object(proxy_client, "check_connection_or_error",
                          return_value=(True, None)), \
             patch("asyncio.run_coroutine_threadsafe", side_effect=TimeoutError()):
            resp = await proxy_client._execute_via_bridge(script="x", timeout=5.0)
        return resp["error"]

    @pytest.mark.asyncio
    async def test_no_heartbeat_yet_does_not_claim_panel_is_dead(self):
        detail = await self._timeout_detail({
            "busy": False, "stale": True,
            "heartbeat_seen": False, "last_heartbeat_ago_ms": None,
        })
        low = detail.lower()
        assert "frozen or crashed" not in low
        assert "try reconnecting" not in low
        assert "heartbeat" in low  # still explains the situation

    @pytest.mark.asyncio
    async def test_genuinely_stale_still_warns_about_dead_panel(self):
        detail = await self._timeout_detail({
            "busy": False, "stale": True,
            "heartbeat_seen": True, "last_heartbeat_ago_ms": 60000,
        })
        assert "frozen or crashed" in detail.lower()

    @pytest.mark.asyncio
    async def test_busy_panel_message_unchanged(self):
        detail = await self._timeout_detail({
            "busy": True, "stale": False, "heartbeat_seen": True,
            "active_request_id": 7, "last_heartbeat_ago_ms": 100,
        })
        assert "still executing" in detail.lower()


class TestWatchdogGuard:

    @pytest.mark.asyncio
    async def test_watchdog_never_disconnects_before_first_heartbeat(self):
        """Defensive: a never-seen heartbeat must not look like a dead panel."""
        b = _bridge()
        b.server.is_connected.return_value = True
        b._last_heartbeat = 0.0
        assert await b._watchdog_tick() is False


class TestStreamingIdCoercion:
    """`complete_request` and `complete_streaming` coerce request_id to int;
    `is_streaming` did not, so a string id would route a streaming response
    into the non-streaming path and be dropped."""

    def test_is_streaming_accepts_string_id(self):
        import asyncio

        from illustrator_mcp.bridge.request_registry import RequestRegistry

        async def go():
            reg = RequestRegistry()
            rid, _ = reg.create_streaming_request(asyncio.get_running_loop(), "s")
            assert reg.is_streaming(rid) is True
            assert reg.is_streaming(str(rid)) is True     # JSON may hand back a str
            assert reg.is_streaming("not-a-number") is False
            assert reg.is_streaming(None) is False

        asyncio.run(go())

    def test_push_update_accepts_string_id(self):
        """is_streaming() coercion is useless if push_update still misses."""
        import asyncio

        from illustrator_mcp.bridge.request_registry import RequestRegistry

        async def go():
            reg = RequestRegistry()
            rid, q = reg.create_streaming_request(asyncio.get_running_loop(), "s")
            assert reg.push_update(str(rid), {"type": "progress", "index": 1}) is True
            assert q.get_nowait()["index"] == 1
            assert reg.push_update("nope", {"type": "progress"}) is False

        asyncio.run(go())
