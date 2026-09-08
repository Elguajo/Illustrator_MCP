"""
test_bridge_auth.py — the bridge must not accept an unauthenticated socket.

The bridge executes arbitrary ExtendScript, and ExtendScript can read and
write files. Loopback is not a trust boundary: any local process can reach
127.0.0.1, and a WebSocket handshake is not covered by the same-origin policy,
so a page in an open browser tab can connect to a localhost port with no CORS
check standing in the way.

These tests drive a real server over a real socket rather than mocking the
handshake. That matters here: the first implementation used the legacy
process_request(path, headers) signature, passed every mock-based check that
was written against it, and failed with HTTP 500 on the first real connection
because installed websockets uses process_request(connection, request).
"""

import asyncio
import json
import os
import stat

import pytest
import websockets

from illustrator_mcp.bridge.server import WebSocketServer
from illustrator_mcp.bridge import session as session_mod
from illustrator_mcp.bridge.session import (
    expected_subprotocol,
    generate_token,
    read_session_file,
    remove_session_file,
    write_session_file,
)

TEST_PORT = 8397
TOKEN = "f" * 64


async def _noop_message(_msg):
    pass


class _RunningBridge:
    """Starts a real WebSocketServer and tears it down."""

    def __init__(self, token, port):
        self.server = WebSocketServer(port=port, on_message=_noop_message, token=token)
        self.task = None

    async def __aenter__(self):
        self.task = asyncio.create_task(self.server.run())
        for _ in range(50):
            await asyncio.sleep(0.02)
            if self.server.server is not None:
                break
        else:
            raise RuntimeError("bridge did not start")
        return self.server

    async def __aexit__(self, *exc):
        self.server.stop()
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):
                pass


async def _connect(port, **kwargs):
    """Return None on success, or the rejected HTTP status code."""
    try:
        async with websockets.connect(
            f"ws://localhost:{port}", open_timeout=5, **kwargs
        ):
            return None
    except websockets.exceptions.InvalidStatus as exc:
        return exc.response.status_code


class TestHandshakeToken:
    @pytest.mark.asyncio
    async def test_valid_token_connects(self):
        async with _RunningBridge(TOKEN, TEST_PORT):
            status = await _connect(TEST_PORT, subprotocols=[expected_subprotocol(TOKEN)])
        assert status is None, f"valid token was rejected with HTTP {status}"

    @pytest.mark.asyncio
    async def test_missing_token_is_rejected(self):
        async with _RunningBridge(TOKEN, TEST_PORT + 1):
            status = await _connect(TEST_PORT + 1)
        assert status == 401

    @pytest.mark.asyncio
    async def test_wrong_token_is_rejected(self):
        async with _RunningBridge(TOKEN, TEST_PORT + 2):
            status = await _connect(
                TEST_PORT + 2, subprotocols=[expected_subprotocol("0" * 64)]
            )
        assert status == 401

    @pytest.mark.asyncio
    async def test_server_echoes_the_subprotocol(self):
        """An unanswered subprotocol offer makes a browser close the socket."""
        async with _RunningBridge(TOKEN, TEST_PORT + 3):
            async with websockets.connect(
                f"ws://localhost:{TEST_PORT + 3}",
                subprotocols=[expected_subprotocol(TOKEN)],
                open_timeout=5,
            ) as ws:
                assert ws.subprotocol == expected_subprotocol(TOKEN)


class TestWebOriginRejected:
    @pytest.mark.asyncio
    async def test_web_origin_rejected_even_with_valid_token(self):
        """Defence in depth: a page that somehow learned the token still fails."""
        async with _RunningBridge(TOKEN, TEST_PORT + 4):
            status = await _connect(
                TEST_PORT + 4,
                subprotocols=[expected_subprotocol(TOKEN)],
                origin="https://evil.example",
            )
        assert status == 403

    @pytest.mark.asyncio
    async def test_no_origin_is_allowed(self):
        """The CEP panel is loaded from disk and sends no http(s) Origin."""
        async with _RunningBridge(TOKEN, TEST_PORT + 5):
            status = await _connect(
                TEST_PORT + 5, subprotocols=[expected_subprotocol(TOKEN)]
            )
        assert status is None


class TestSessionFile:
    @pytest.fixture(autouse=True)
    def isolated_home(self, tmp_path, monkeypatch):
        monkeypatch.setattr(session_mod.Path, "home", staticmethod(lambda: tmp_path))
        yield tmp_path

    def test_roundtrip(self):
        token = generate_token()
        path = write_session_file(8081, token)
        assert path is not None
        data = read_session_file()
        assert data["port"] == 8081
        assert data["token"] == token
        assert data["pid"] == os.getpid()

    def test_file_is_private(self):
        """The secret must not be readable by other users on the machine."""
        path = write_session_file(8081, generate_token())
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode & 0o077 == 0, f"session file is group/world accessible: {oct(mode)}"

    def test_directory_is_private(self):
        path = write_session_file(8081, generate_token())
        mode = stat.S_IMODE(path.parent.stat().st_mode)
        assert mode & 0o077 == 0, f"session dir is group/world accessible: {oct(mode)}"

    def test_no_partial_file_left_behind(self):
        """Atomic replace: no stray temp files after a write."""
        write_session_file(8081, generate_token())
        leftovers = [p.name for p in session_mod.session_dir().iterdir()
                     if p.name.startswith(".session-")]
        assert leftovers == [], f"temp files left behind: {leftovers}"

    def test_rewrite_replaces_token(self):
        write_session_file(8081, "a" * 64)
        write_session_file(9000, "b" * 64)
        data = read_session_file()
        assert data["token"] == "b" * 64
        assert data["port"] == 9000

    def test_remove_is_idempotent(self):
        write_session_file(8081, generate_token())
        remove_session_file()
        remove_session_file()  # must not raise
        assert read_session_file() is None

    def test_old_bridge_cannot_remove_newer_bridge_session(self):
        """An old process stopping after a restart must not strand the panel."""
        old_token = "a" * 64
        current_token = "b" * 64
        write_session_file(8081, old_token)
        write_session_file(9000, current_token)

        remove_session_file(old_token)

        assert read_session_file() == {
            "version": 1,
            "port": 9000,
            "token": current_token,
            "pid": os.getpid(),
        }

    def test_read_missing_returns_none(self):
        assert read_session_file() is None

    def test_read_malformed_returns_none(self):
        session_mod.session_dir().mkdir(parents=True, exist_ok=True)
        session_mod.session_file().write_text("{not json", encoding="utf-8")
        assert read_session_file() is None


class TestTokenQuality:
    def test_tokens_are_unique(self):
        assert len({generate_token() for _ in range(100)}) == 100

    def test_token_is_long_enough(self):
        assert len(generate_token()) >= 32

    def test_token_is_subprotocol_safe(self):
        """RFC 6455 subprotocol names are tokens; hex keeps us inside that set."""
        token = generate_token()
        assert all(c in "0123456789abcdef" for c in token)
        assert " " not in expected_subprotocol(token)


class TestBridgeLifecycle:
    """start()/stop() must publish and retract the handshake file.

    A session file that outlives its server points the panel at a dead port
    with a secret that authenticates nothing; one written before the listener
    is up sends the panel into a retry loop against a closed socket.
    """

    @pytest.fixture(autouse=True)
    def isolated_home(self, tmp_path, monkeypatch):
        monkeypatch.setattr(session_mod.Path, "home", staticmethod(lambda: tmp_path))
        yield tmp_path

    def test_start_publishes_the_file_and_stop_removes_it(self):
        from illustrator_mcp.websocket_bridge import WebSocketBridge

        bridge = WebSocketBridge(port=TEST_PORT + 6)
        try:
            bridge.start()
            assert bridge.wait_until_ready(timeout=10), "bridge never became ready"

            published = read_session_file()
            assert published is not None, "start() did not publish the handshake file"
            assert published["port"] == TEST_PORT + 6
            assert published["token"] == bridge.token
            assert session_mod.file_mode_is_private(session_mod.session_file())
        finally:
            bridge.stop()

        assert read_session_file() is None, "stop() left a stale handshake file"

    def test_each_run_issues_a_fresh_secret(self):
        """A restart must not keep authenticating an old panel's captured token."""
        from illustrator_mcp.websocket_bridge import WebSocketBridge

        first = WebSocketBridge(port=TEST_PORT + 7)
        second = WebSocketBridge(port=TEST_PORT + 7)
        assert first.token != second.token

    def test_file_names_the_port_the_bridge_actually_uses(self):
        """The panel reads the port from here, so a mismatch strands it."""
        from illustrator_mcp.websocket_bridge import WebSocketBridge

        bridge = WebSocketBridge(port=TEST_PORT + 8)
        try:
            bridge.start()
            assert bridge.wait_until_ready(timeout=10)
            assert read_session_file()["port"] == bridge.port
        finally:
            bridge.stop()
