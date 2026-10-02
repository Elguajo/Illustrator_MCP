"""
Tool module imports for illustrator_mcp.

SCRIPTING FIRST ARCHITECTURE:
This MCP uses a minimal toolset following the blender-mcp pattern.
Most operations should be done via illustrator_execute_script.

Consolidated tool inventory (18 tools):
- execute_script: Run any ExtendScript code
- execute_task: Structured task protocol operations
- document: Create/open/save/close documents (unified)
- export_document: Multi-format export
- history: Undo/redo/checkpoints
- place_file: Place external files
- set_reference: Reference image overlay
- get_document: Document structure + app info (scope param)
- query_items: Declarative item queries
- preflight_check: scoped preflight report (document/objects/text/images/links/colors)
- path_boolean: Boolean path operations
- path_import_svg: SVG path data import
- ground_object: resolve an annotated-preview label to PageItem metadata and @mcp:id
- inspect: progressive structure/artboard/selection/details views keyed by native uuid
- artboards: list/create/update/delete/activate/fit artboards, named size presets
- effects: live drop shadow / Gaussian blur apply and remove by uuid
- swatches: document swatches and groups, swatch libraries
- text: find/replace, font replacement, range styling, outlines by native uuid
"""

# Authoritative list of expected tool names (single source of truth).
# Registry snapshot test imports this to prevent drift.
EXPECTED_TOOL_NAMES = {
    "illustrator_execute_script",
    "illustrator_execute_task",
    "illustrator_document",
    "illustrator_export_document",
    "illustrator_history",
    "illustrator_place_file",
    "illustrator_set_reference",
    "illustrator_get_document",
    "illustrator_query_items",
    "illustrator_preflight_check",
    "illustrator_path_boolean",
    "illustrator_path_import_svg",
    "illustrator_ground_object",
    "illustrator_inspect",
    "illustrator_artboards",
    "illustrator_effects",
    "illustrator_swatches",
    "illustrator_text",
}


def register_tools(mcp):
    """
    Explicitly register tools with the MCP instance.
    This replaces side-effect imports in server.py.
    """
    # Core tool - the primary way to interact with Illustrator
    from illustrator_mcp.tools import execute

    # Document operations (essential file I/O)
    from illustrator_mcp.tools import documents

    # Context tools (for document structure)
    from illustrator_mcp.tools import context

    # Task Protocol tools (pilot refactor)
    from illustrator_mcp.tools import query

    # SVG import tool (path_import_svg)
    from illustrator_mcp.tools import import_svg

    # Task execution (execute_task) + path boolean (split from execute.py)
    from illustrator_mcp.tools import task_execution

    # Visual label → PageItem identity grounding
    from illustrator_mcp.tools import grounding

    # Typed document-model tools (inspect, artboards) over doc_model.jsx
    from illustrator_mcp.tools import doc_model_tools

    # Typed effects and swatch tools over effects.jsx / swatches.jsx
    from illustrator_mcp.tools import appearance_tools

    # Typed text editing (replace, fonts, styling, outlines) over doc_text.jsx
    from illustrator_mcp.tools import text_tools

    return [execute, documents, context, query, import_svg, task_execution, grounding, doc_model_tools,
            appearance_tools, text_tools]

__all__ = ["register_tools", "EXPECTED_TOOL_NAMES"]
