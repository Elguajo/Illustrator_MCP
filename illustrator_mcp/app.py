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
from illustrator_mcp.config import config
from illustrator_mcp.result_contract import add_result_evidence
from illustrator_mcp.tool_catalog import CORE_TOOLS, compact_description

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
        name = kwargs.get("name")
        if config.tool_profile == "core" and name and name not in CORE_TOOLS:
            return lambda fn: fn
        if name and "description" not in kwargs:
            description = compact_description(name)
            if description:
                kwargs["description"] = description
        return super().tool(*args, structured_output=structured_output, **kwargs)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        result = await super().call_tool(name, arguments)
        if isinstance(result, dict):
            return result
        for block in result:
            if isinstance(block, TextContent):
                try:
                    envelope = json.loads(block.text)
                except ValueError:
                    break
                if isinstance(envelope, dict) and "ok" in envelope and "diagnostics" in envelope:
                    block.text = json.dumps(add_result_evidence(envelope))
                break
        return mark_tool_error(result)


SERVER_INSTRUCTIONS = """\
Live control of the running Adobe Illustrator through its CEP panel.

Workflow
1. Look first: illustrator_inspect (view structure, artboard, selection, details) returns native
   uuids. illustrator_get_document dumps everything; use it only when you need every item.
   illustrator_query_items resolves Task Protocol selectors.
2. Act with the most specific tool; raw script is the last resort:
   - existing text (replace, fonts, styling, outline): illustrator_text
   - artboards: illustrator_artboards; documents (create, open, save, list, switch): illustrator_document
   - drop shadow, Gaussian blur: illustrator_effects; swatch names and palettes: illustrator_swatches
   - create shapes and text, paint, transform, align, group, layers, z-order: illustrator_execute_task
   - unite, subtract, intersect, xor: illustrator_path_boolean; SVG path data: illustrator_path_import_svg
   - place or trace a file: illustrator_place_file; tracing reference layer: illustrator_set_reference
   - print and export problems: illustrator_preflight_check
   - anything else: illustrator_execute_script, after reading resource illustrator://reference/extendscript
3. Check: illustrator_export_document(return_image=true) or the annotated preview;
   illustrator_ground_object maps a preview label [N] to its item. illustrator_history undoes and keeps
   checkpoints; save one before a risky step.

Identity
- A uuid from illustrator_inspect is valid only in that document while it stays open. Pass
  document=<result.document.name> with it; inspect again after open, reopen or switch.
- @mcp:id (in item.note) survives save and reopen. illustrator_path_boolean and checkpoints use it.

Coordinates
- Typed tools: canvas-global points, Y-down, [left, top, right, bottom], same space as artboard bounds.
- illustrator_execute_task element ops: x, y from the active artboard's top-left, Y-down.
- Illustrator DOM (execute_script, path_import_svg bounds, ground_object *_ai bounds): Y-up, y_dom = -y.

Results
- Every tool returns {ok, warnings, error, diagnostics, result}; ok=false comes with isError and
  error.code plus suggestions. illustrator_text, illustrator_effects and illustrator_swatches report
  success_count, fail_count, failed_objects and skipped_objects: a partial success is ok=true, so
  read fail_count.
- Locked and hidden objects are skipped, never modified.
"""

# Create MCP server with lifespan management
SERVER_INSTRUCTIONS += """
Recovery and evidence
- Pass document_session_id=<result.document.session_id> with typed edits and uuid targets.
  It rejects a reopened or different document, even if its filename and uuids match.
- diagnostics.execution reports not_started/running/completed/unknown. Only not_started is
  safe to replay without reading the canvas. Timeout/disconnect after submission means unknown;
  poll illustrator_inspect(view='execution',request_id=...) and re-inspect the document before retrying.
- diagnostics.changes separates full/partial/unverified execution from verification.
  completed and full never imply a visual or DOM read-back check. Rollback counts do not prove restoration.
- Read illustrator://reference/tools for full tool documentation and examples.
"""
if config.tool_profile == "core":
    SERVER_INSTRUCTIONS = """Live control of Adobe Illustrator via its CEP panel. Core startup profile.
Inspect first; then use the specific available typed tool, execute_task for SOC operations,
or execute_script after reading illustrator://reference/extendscript for unsupported operations.
Use export_document(return_image=true) for visual checks; ground_object for preview identity;
history for checkpoints. Typed bounds are canvas Y-down points; SOC x/y are artboard-relative;
raw Illustrator DOM coordinates are Y-up.
Pass document name and document_session_id from inspect when using native uuids.
@mcp:id survives reopen; native uuid does not. Read diagnostics.changes and per-object failures.
Only execution state not_started is safe to replay without inspecting. Timeout may leave edits
running: poll inspect(view='execution',request_id=...) and inspect the canvas before recovery.
Full tool documentation: illustrator://reference/tools. Set ILLUSTRATOR_MCP_TOOL_PROFILE=all
and restart for the complete toolset. A profile limits discovery, not permissions.
"""
mcp = IllustratorFastMCP(
    "illustrator_mcp",
    instructions=SERVER_INSTRUCTIONS,
    lifespan=server_lifespan
)
