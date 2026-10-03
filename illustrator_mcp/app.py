"""
Application composition for Illustrator MCP.

Owns the FastMCP singleton and server lifespan wiring.
This is the only module that constructs the MCP application instance.
"""

import json
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, Sequence

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, ContentBlock, TextContent

logger = logging.getLogger(__name__)


@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[Dict[str, Any]]:
    """
    Manage MCP server startup and shutdown lifecycle.

    This ensures the WebSocket bridge is properly started before
    any tools are called, and cleanly shut down when the server stops.
    """
    from illustrator_mcp.runtime import get_runtime
    from illustrator_mcp.config import config

    logger.info("=" * 60)
    logger.info("Adobe Illustrator MCP Server - LIFESPAN STARTUP")
    logger.info("=" * 60)
    
    bridge = None
    try:
        # Start the WebSocket bridge via runtime
        logger.info("Starting WebSocket bridge...")
        bridge = get_runtime().get_bridge()
        
        # Verify bridge started successfully
        if bridge.is_running():
            logger.info(f"✓ WebSocket bridge started on port {config.ws_port}")
            logger.info(f"  CEP panel should connect to: ws://localhost:{config.ws_port}")
        else:
            logger.error("✗ WebSocket bridge failed to start!")
            logger.error("  CEP panel will NOT be able to connect.")
        
        # Dynamic tool count
        try:
            tools = await server.list_tools()
            tool_count = len(tools)
            msg = f"{tool_count} tools registered"
        except Exception:
            msg = "tools registered"

        logger.info("")
        logger.info(f"MCP server ready ({msg})")
        logger.info("=" * 60)
        
        # Yield empty context - bridge is accessed via get_runtime().get_bridge()
        yield {}
        
    finally:
        # Clean up on shutdown
        logger.info("=" * 60)
        logger.info("Adobe Illustrator MCP Server - LIFESPAN SHUTDOWN")
        logger.info("=" * 60)
        
        # Use runtime.shutdown() for full cleanup (bridge + proxy)
        try:
            from illustrator_mcp.runtime import get_runtime
            get_runtime().shutdown()
            logger.info("Runtime shutdown complete (bridge + proxy)")
        except Exception as e:
            logger.error(f"Error during runtime shutdown: {e}")
            # Fallback: at least try to stop the bridge directly
            if bridge:
                bridge.stop()
        
        logger.info("Server shutdown complete")


def is_failure_envelope(text: str) -> bool:
    """True when ``text`` is a canonical tool envelope with ``ok: false``."""
    if not text.lstrip().startswith("{"):
        return False
    try:
        data = json.loads(text)
    except ValueError:
        return False
    return isinstance(data, dict) and data.get("ok") is False


def mark_tool_error(content: Sequence[ContentBlock]) -> Sequence[ContentBlock] | CallToolResult:
    """Wrap a failed tool result so the client sees ``isError: true``.

    Every tool reports failure through the ``{ok, error, ...}`` envelope
    (errors.make_envelope / proxy_client.format_envelope), possibly followed
    by preview images. The first text block decides.
    """
    for block in content:
        if isinstance(block, TextContent):
            if is_failure_envelope(block.text):
                return CallToolResult(content=list(content), isError=True)
            break
    return content


class IllustratorFastMCP(FastMCP):
    """FastMCP with MCP-conformant tool error signalling.

    - Tools return the envelope as text only. FastMCP's automatic output schema
      for a ``str`` return wrapped the same JSON in ``{"result": "..."}`` as
      ``structuredContent``: a second copy with no structure.
    - A result whose envelope says ``ok: false`` is sent with ``isError: true``
      (MCP spec, tool execution errors), so clients and evals see failures.
    """

    def tool(self, *args: Any, structured_output: bool | None = False, **kwargs: Any):
        return super().tool(*args, structured_output=structured_output, **kwargs)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        result = await super().call_tool(name, arguments)
        if isinstance(result, dict):
            return result
        return mark_tool_error(result)


# Create MCP server with lifespan management
mcp = IllustratorFastMCP(
    "illustrator_mcp",
    lifespan=server_lifespan
)
