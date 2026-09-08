"""
test_bridge_message_routing.py — what the bridge does with what the panel sends.

_handle_message is the entry point for every byte the CEP panel sends back, and
execute_script is the path every tool takes outbound. Both were almost entirely
uncovered, in the module that produced the last two bug-fix commits on this
branch's parent (heartbeat on connect, panel-health diagnostics).

These tests drive the real WebSocketBridge object with a stubbed transport, so
the routing, registry bookkeeping and error envelopes are exercised for real;
only the socket is faked.
"""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from illustrator_mcp.config import config
from illustrator_mcp.websocket_bridge import WebSocketBridge


@pytest.fixture
async def bridge():
    """A bridge object bound to the running loop.

    No server thread is started and no session file is written: start() owns
    both, and these tests exercise routing, not the listener.
    """
    b = WebSocketBridge(port=8999)
    b.loop = asyncio.get_running_loop()
    yield b
    b.registry.cancel_all("test teardown")
    if b._watchdog_task and not b._watchdog_task.done():
        b._watchdog_task.cancel()


@pytest.fixture
def offline_bridge():
    """Same object for synchronous tests that never touch the loop."""
    return WebSocketBridge(port=8999)


def _pending_request(bridge, script="doc.pathItems.length"):
    """Register a request the way execute_script does."""
    return bridge.registry.create_request(bridge.loop, script, None, None)


class TestCompleteMessages:
    @pytest.mark.asyncio
    async def test_result_resolves_the_waiting_future(self, bridge):
        request_id, future = _pending_request(bridge)
        await bridge._handle_message(json.dumps({"id": request_id, "result": 42}))
        assert future.done()
        assert (await future)["result"] == 42

    @pytest.mark.asyncio
    async def test_response_for_unknown_id_is_ignored(self, bridge):
        """A late reply after a cancel must not raise or corrupt the registry."""
        await bridge._handle_message(json.dumps({"id": 99999, "result": "late"}))
        assert bridge.registry.pending_count == 0

    @pytest.mark.asyncio
    async def test_message_without_id_is_ignored(self, bridge):
        request_id, future = _pending_request(bridge)
        await bridge._handle_message(json.dumps({"result": "orphan"}))
        assert not future.done(), "a message with no id resolved an unrelated request"

    @pytest.mark.asyncio
    async def test_invalid_json_does_not_raise(self, bridge):
        """A malformed frame must not take down the receive loop."""
        await bridge._handle_message("{not json at all")

    @pytest.mark.asyncio
    async def test_oversized_message_is_dropped(self, bridge):
        """Guards against a panel flooding memory with one huge frame."""
        request_id, future = _pending_request(bridge)
        limit = config.max_message_size_mb * 1024 * 1024
        oversized = json.dumps({"id": request_id, "result": "x" * (limit + 100)})
        await bridge._handle_message(oversized)
        assert not future.done(), "oversized message was processed instead of dropped"


class TestStreamingMessages:
    @pytest.mark.asyncio
    async def test_progress_is_pushed_not_completed(self, bridge):
        request_id, _queue = bridge.registry.create_streaming_request(
            bridge.loop, "script", None, None
        )
        await bridge._handle_message(
            json.dumps({"id": request_id, "type": "progress", "index": 1})
        )
        assert bridge.registry.is_streaming(request_id), (
            "a progress frame ended the stream"
        )

    @pytest.mark.asyncio
    async def test_non_progress_frame_completes_the_stream(self, bridge):
        """A terminal frame must put the sentinel on the queue.

        complete_streaming deliberately leaves the id registered so a consumer
        that starts late can still drain the queue, so is_streaming stays True;
        the sentinel is what marks the stream finished.
        """
        request_id, queue = bridge.registry.create_streaming_request(
            bridge.loop, "script", None, None
        )
        await bridge._handle_message(
            json.dumps({"id": request_id, "type": "complete", "result": "done"})
        )

        frames = []
        while not queue.empty():
            frames.append(queue.get_nowait())

        assert frames, "terminal frame produced nothing on the queue"
        assert frames[-1].get("type") == "complete", (
            f"expected a completion sentinel last, got {frames[-1]}"
        )


class TestHeartbeatRouting:
    @pytest.mark.asyncio
    async def test_heartbeat_updates_health_without_a_request_id(self, bridge):
        before = bridge.get_panel_health()
        assert before["heartbeat_seen"] is False

        await bridge._handle_message(
            json.dumps({"type": "heartbeat", "busy": True, "activeRequestId": 7})
        )

        after = bridge.get_panel_health()
        assert after["heartbeat_seen"] is True
        assert after["busy"] is True
        assert after["active_request_id"] == 7
        assert after["stale"] is False


    @pytest.mark.asyncio
    async def test_heartbeat_does_not_touch_pending_requests(self, bridge):
        request_id, future = _pending_request(bridge)
        await bridge._handle_message(json.dumps({"type": "heartbeat", "busy": False}))
        assert not future.done()


class TestDisconnectHandling:
    @pytest.mark.asyncio
    async def test_disconnect_cancels_pending_and_resets_health(self, bridge):
        request_id, future = _pending_request(bridge)
        await bridge._handle_message(json.dumps({"type": "heartbeat", "busy": True}))

        await bridge._handle_disconnect()

        assert future.done(), "pending request survived a panel disconnect"
        health = bridge.get_panel_health()
        assert health["heartbeat_seen"] is False, "stale heartbeat state carried over"
        assert health["busy"] is False
        assert health["active_request_id"] is None


class TestExecuteScript:
    @pytest.mark.asyncio
    async def test_not_connected_returns_connection_error(self, bridge):
        response = await bridge.execute_script_async("noop", timeout=1)
        assert "error" in response
        assert str(bridge.port) in json.dumps(response), (
            "the connection error should name the port the panel must reach"
        )

    @pytest.mark.asyncio
    async def test_timeout_reports_the_timeout_and_clears_the_request(self, bridge):
        bridge.server.is_connected = lambda: True
        bridge.server.send = AsyncMock()

        response = await bridge.execute_script_async("while(true){}", timeout=0.05)

        assert "error" in response
        assert "timed out" in json.dumps(response).lower()

    @pytest.mark.asyncio
    async def test_send_failure_is_reported_not_raised(self, bridge):
        bridge.server.is_connected = lambda: True
        bridge.server.send = AsyncMock(side_effect=RuntimeError("socket gone"))

        response = await bridge.execute_script_async("noop", timeout=1)

        assert "error" in response
        assert "socket gone" in json.dumps(response)

    @pytest.mark.asyncio
    async def test_sent_payload_carries_id_script_and_trace(self, bridge):
        bridge.server.is_connected = lambda: True
        sent = {}

        async def capture(message):
            sent.update(json.loads(message))
            # Resolve straight away so the call returns.
            bridge.registry.complete_request(sent["id"], {"result": "ok"})

        bridge.server.send = capture
        await bridge.execute_script_async("var a = 1;", timeout=2, trace_id="req_abc")

        assert sent["script"] == "var a = 1;"
        assert sent["trace_id"] == "req_abc"
        assert "id" in sent


class TestConnectionInfo:
    def test_reports_port_and_state_when_disconnected(self, offline_bridge):
        info = offline_bridge.get_connection_info()
        assert info["is_connected"] is False
        assert info["port"] == 8999
        assert info["client_info"] is None
        assert info["is_running"] is False

    def test_reports_client_when_connected(self, offline_bridge):
        class _Client:
            remote_address = ("127.0.0.1", 51931)

        offline_bridge.server.is_connected = lambda: True
        offline_bridge.server.client = _Client()

        info = offline_bridge.get_connection_info()
        assert info["is_connected"] is True
        assert "51931" in info["client_info"]["remote_address"]
