"""
WebSocket server for Illustrator CEP bridge.
"""

import asyncio
import threading
import http
import json
import logging
import secrets
import websockets
from websockets.server import WebSocketServerProtocol
from typing import Optional, Callable, Awaitable

from illustrator_mcp.bridge.session import expected_subprotocol

logger = logging.getLogger(__name__)

# Origins that identify a web page rather than the CEP panel. The panel is
# loaded from disk, so it sends no Origin (or a file:// one); a browser tab
# always sends its http(s) origin, and the same-origin policy does not stop
# it from opening a WebSocket to localhost.
_WEB_ORIGIN_PREFIXES = ("http://", "https://")


class WebSocketServer:
    """
    Manages the WebSocket server and client connection.
    Does not handle request logic, only transport.
    """
    
    def __init__(self, port: int, on_message: Callable[[str], Awaitable[None]],
                 on_disconnect: Optional[Callable[[], Awaitable[None]]] = None,
                 token: Optional[str] = None):
        self.port = port
        self.on_message = on_message
        self.on_disconnect = on_disconnect
        # When set, a client must offer the matching subprotocol to connect.
        self.token = token
        self.client: Optional[WebSocketServerProtocol] = None
        self.server = None
        self._shutdown_event: Optional[asyncio.Event] = None
        self._start_error: Optional[Exception] = None
        
    async def run(self, started_event: Optional[threading.Event] = None):
        """Run the WebSocket server."""
        self._shutdown_event = asyncio.Event()
        self._start_error = None
        
        try:
            serve_kwargs = {}
            if self.token:
                # Echo the accepted subprotocol back, or the browser closes
                # the socket for an unanswered offer.
                serve_kwargs["subprotocols"] = [expected_subprotocol(self.token)]

            self.server = await websockets.serve(
                self._handle_client,
                "localhost",
                self.port,
                ping_interval=30,
                ping_timeout=10,
                process_request=self._authorize_request,
                **serve_kwargs
            )
            
            logger.info(f"="*50)
            logger.info(f"WebSocket bridge STARTED on port {self.port}")
            logger.info(f"CEP panel should connect to: ws://localhost:{self.port}")
            logger.info(f"="*50)
            
            if started_event:
                started_event.set()

            # Keep server running until shutdown event
            await self._shutdown_event.wait()
            
            # Graceful shutdown
            logger.info("Shutting down WebSocket bridge...")
            self.server.close()
            await self.server.wait_closed()
            
            # Close active client if any
            if self.client:
                await self.client.close(1000, "Server shutting down")

        except OSError as e:
            if "address already in use" in str(e).lower() or e.errno == 10048:
                logger.error(f"Port {self.port} is already in use!")
            else:
                logger.error(f"WebSocket server OSError: {e}")
            self._start_error = e
            if started_event:
                started_event.set()
            raise
        except Exception as e:
            logger.error(f"WebSocket server error: {e}")
            self._start_error = e
            if started_event:
                started_event.set()
            raise

    def _authorize_request(self, connection, request):
        """Reject a handshake that is not the local CEP panel.

        Runs before the connection is upgraded, so a rejected caller never
        reaches _handle_client and cannot occupy the single client slot.
        Returning None lets the handshake continue.

        Signature follows the websockets asyncio server API
        (process_request(connection, request)).
        """
        headers = request.headers

        origin = headers.get("Origin") or ""
        if origin.lower().startswith(_WEB_ORIGIN_PREFIXES):
            logger.warning(
                f"Connection rejected: web origin {origin!r} may not drive Illustrator."
            )
            return connection.respond(
                http.HTTPStatus.FORBIDDEN, "origin not allowed\n"
            )

        if not self.token:
            return None

        # A client may offer several protocols in one header, or repeat the
        # header; get_all covers both.
        offered = []
        for value in headers.get_all("Sec-WebSocket-Protocol"):
            offered.extend(part.strip() for part in value.split(",") if part.strip())

        expected = expected_subprotocol(self.token)
        # compare_digest keeps the check constant-time; a plain == would leak
        # the secret one byte at a time to a local process that can retry.
        if not any(secrets.compare_digest(candidate, expected) for candidate in offered):
            logger.warning(
                "Connection rejected: missing or invalid handshake token. "
                "The CEP panel reads it from ~/.illustrator-mcp/session.json."
            )
            return connection.respond(
                http.HTTPStatus.UNAUTHORIZED, "handshake token required\n"
            )

        return None

    async def _handle_client(self, websocket: WebSocketServerProtocol):
        """Handle a connected client."""
        # Reject if there is already an active connection
        if self.client is not None and self.is_connected():
            logger.warning(
                "Connection rejected: Another client is already connected."
            )
            await websocket.close(
                4001,
                "Another MCP client is already connected to Illustrator. "
                "Please close the existing connection first."
            )
            return

        # Clean up zombie connections (disconnected but not yet cleaned)
        if self.client is not None:
            logger.info("Cleaning up stale connection")
            try:
                await self.client.close(1000, "Stale connection cleanup")
            except Exception:
                pass
            self.client = None

        logger.info("Illustrator CEP panel connected")
        self.client = websocket

        try:
            async for message in websocket:
                await self.on_message(message)

        except websockets.exceptions.ConnectionClosed:
            logger.info("Illustrator CEP panel disconnected")
        finally:
            if self.client == websocket:
                self.client = None
                if self.on_disconnect:
                    try:
                        await self.on_disconnect()
                    except Exception as e:
                        logger.error(f"on_disconnect callback error: {e}")

    async def send(self, message: str):
        """Send message to connected client."""
        ws = self.client  # snapshot to avoid TOCTOU race
        if ws is None:
            raise ConnectionError("No client connected")
        if not self.is_connected():
            self.client = None
            raise ConnectionError("Client connection is closed")
        await ws.send(message)

    def stop(self):
        """Signal shutdown."""
        # This must be called from the loop thread or via call_soon_threadsafe
        if self._shutdown_event:
            self._shutdown_event.set()

    def is_connected(self) -> bool:
        """Check if client is connected."""
        if self.client is None:
            return False
        try:
            # websockets 16+: use state enum (client.open is deprecated)
            if hasattr(self.client, 'state'):
                from websockets.protocol import State
                return self.client.state == State.OPEN
            # Fallback for older websockets versions
            if hasattr(self.client, 'open'):
                return self.client.open
            if hasattr(self.client, 'closed'):
                return not self.client.closed
            return False  # Safe default: assume disconnected if unknown
        except Exception:
            return False
