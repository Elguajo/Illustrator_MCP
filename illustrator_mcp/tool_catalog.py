"""Compact startup discovery; detailed function documentation stays in a resource."""

CORE_TOOLS = frozenset({
    "illustrator_inspect", "illustrator_document", "illustrator_execute_task",
    "illustrator_execute_script", "illustrator_artboards", "illustrator_text",
    "illustrator_export_document", "illustrator_history", "illustrator_ground_object",
    "illustrator_query_items",
})

TOOL_SUMMARIES = {
    "inspect": "Read structure/artboard/selection/details; returns uuid, mcp_id and document.session_id. view='execution' reads bridge health/status without executing JSX. Example: params={view:'details',uuids:['412']}.",
    "document": "Create/open/save/close/list/switch documents. Close may discard edits. Example: params={action:'list'}; params={action:'switch',name:'poster.ai'}.",
    "execute_task": "Create/paint/transform/align/group/layer/z-order via SOC ops. Use typed tools for text/artboards/effects. Native uuid targets require document and should carry document_session_id. Example: params={payload:{task:'batch',params:{ops:[{task:'element_create',params:{type:'rect',x:20,y:20,width:100,height:60}}]}}}. Params x/y use the active artboard top-left, Y-down. Inspect changes and per-op failures; partial edits may survive errors. Read illustrator://reference/tools for ops/options/custom compute/apply and more examples.",
    "execute_script": "Raw ES3 ExtendScript escape hatch. Read illustrator://reference/extendscript first. DOM uses Y-up; return changed identities and read-back values. Do not replay after timeout. Example: params={script:'app.version'}.",
    "export_document": "Export document/artboard to file or preview image. Example: params={format:'png',return_image:true}. Can overwrite files.",
    "history": "Undo/redo and checkpoints before risky edits. Read illustrator://reference/tools for checkpoint actions.",
    "place_file": "Place a file into the document, optionally trace/rasterize. Read illustrator://reference/tools for tracing controls.",
    "set_reference": "Set up a locked reference layer from an image for tracing. May replace an existing reference.",
    "get_document": "Paginated full document/app information. Prefer inspect for a small progressive read. Example: params={scope:'app'}.",
    "query_items": "Resolve Task Protocol target selectors without mutation. Example: params={targets:{type:'selection'}}.",
    "preflight_check": "Read-only print/export checks: fonts, paths, images and document issues.",
    "path_boolean": "Unite/subtract/intersect/xor paths by persistent @mcp:id. Originals may be deleted. Read illustrator://reference/tools for tolerance and styling.",
    "path_import_svg": "Import SVG d-string paths. Imported path geometry uses SVG screen coordinates; placement bounds use Illustrator Y-up. Read illustrator://reference/tools.",
    "ground_object": "Map annotated preview label [N] to an item identity/bounds. assign_id optionally writes @mcp:id to item.note; do this before persistent targeting.",
    "artboards": "List/presets/create/update/delete/activate/fit artboards. Bounds are canvas Y-down points. Example: params={action:'create',preset:'A4',name:'Print'}.",
    "effects": "Apply live drop shadow/Gaussian blur or remove effects by uuid. Pass document_session_id from inspect. Removal can affect stroke alignment: read illustrator://reference/tools. Example: params={action:'apply',effect:'gaussian_blur',uuids:['412'],radius:3}.",
    "swatches": "List/create/update/delete swatches/groups and inspect/import installed palette libraries. Batch failures/skips are reported per swatch.",
    "text": "Replace text/fonts, style ranges or outline existing text. uuids require document name; pass document_session_id too. Locked/hidden objects are skipped. Example: params={action:'replace',uuids:['412'],document:'poster.ai',find:'2025',replace:'2026'}; dry_run=true previews matches.",
}


def compact_description(name: str) -> str | None:
    return TOOL_SUMMARIES.get(name.removeprefix("illustrator_"))
