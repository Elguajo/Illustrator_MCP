#!/usr/bin/env python3
"""
Adobe Illustrator MCP Server.

This server provides tools to interact with Adobe Illustrator via the
Model Context Protocol, enabling AI assistants to control Illustrator
using natural language.

Architecture (SIMPLIFIED - Single Process!):
- MCP server runs as main process (stdio transport for Claude Code)
- Integrated WebSocket server (port 8081) for CEP panel connection
- NO separate Node.js proxy server needed!

How it works:
1. Claude Code connects to this server via stdio
2. CEP panel in Illustrator connects via WebSocket (port 8081)
3. MCP tools send scripts through the WebSocket bridge to Illustrator

Lifecycle:
- WebSocket bridge starts via lifespan management (see shared.py)
- Bridge is automatically shut down when server stops

HYBRID TOOLSET:
Typed operations and SOC batches cover routine work; execute_script is the
escape hatch for other ExtendScript operations. All 18 tools are discovered by
the default startup profile. ILLUSTRATOR_MCP_TOOL_PROFILE=core opts into 10 tools;
app.py owns registration filtering, compact descriptions and server instructions.

"""

import logging
from illustrator_mcp.log_config import configure_logging

# Configure logging
configure_logging()
logger = logging.getLogger(__name__)

# Import the shared mcp instance (includes lifespan management)
from illustrator_mcp.shared import mcp

# Import tool registration function
from illustrator_mcp.tools import register_tools

# Register tools explicitly
# The startup profile is applied by the shared application decorator.
register_tools(mcp)


def main():
    """Entry point for the MCP server."""
    logger.info("Starting Adobe Illustrator MCP Server...")
    logger.info("(WebSocket bridge will start via lifespan management)")
    logger.info("")
    
    # Run the MCP server (lifespan handles bridge startup/shutdown)
    mcp.run()


if __name__ == "__main__":
    main()
