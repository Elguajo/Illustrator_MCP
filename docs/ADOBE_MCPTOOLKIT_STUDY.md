# Study: Adobe's built-in MCPToolkit (Illustrator 2026)

Status: research note, 2026-10-02. Phase 1 implemented the same day: native uuid
identity, `illustrator_inspect`, `illustrator_artboards`, document `list`/`switch`, and the
`{"type": "uuid"}` task target. Phase 2 implemented: `illustrator_text` (style-preserving replace,
font replace, range and paragraph styling, outlines), text overflow detection, and preflight v2
(patterns 3, 4, 5, 7). See CHANGELOG. The remaining gap rows below are open.

## Source and boundary

Illustrator 30.8.1 ships `Contents/Required/Plug-ins/Extensions/MCPToolkit.aip`, a signed native
(C++) plug-in. There is no source to reuse, and Adobe's licence forbids decompiling it, so **no code
is copied**. What was studied is the tool registry the binary embeds as plain JSON (names, input
schemas, access flags), read with `strings`. This note restates the *design ideas* in our own
words; Adobe's descriptions are not reproduced.

## What it is

- An in-process tool registry: 87 tools in 28 categories, validated with valijson
  (`EnableMCPToolkitSchemaValidation`). The plug-in opens no port and has no stdio transport: it
  serves Adobe's in-app AI Assistant (the "IRIS" agent) and Firefly.
- Each tool carries `internalAccess` / `externalAccess`, either a boolean or a feature-flag name
  (`EnableCreativeMCPToolsExternal`, `EnableAgenticScriptExecution`, ...). Adobe plans external
  clients (one tool's text names Cursor), but nothing on this machine exposes it to Claude Code.
- **Raw `ExecuteJavaScript` is internal-only.** Adobe gives external agents typed tools and keeps
  arbitrary script behind a flag. Our server makes raw script the primary path.
- Cloud tools (TextToVector, RecolorArtwork, OpenInFirefly, Turntable) depend on Firefly credits
  and Adobe-hosted upload URLs. We cannot reproduce them and should not try.

## Design patterns worth adopting

1. **Native object identity.** Every tool addresses objects by Illustrator's own UUID. Our
   ScriptingSupport exposes `PageItem.uuid` and `Document.getPageItemFromUuid()` (verified live,
   see below). We instead write `@mcp:id=` into `item.note` (23 files), which mutates the document,
   is opt-in, and can duplicate on copy-paste. The native uuid is now the primary in-session handle;
   it is not a persistent identity (see "Verified live"), so `@mcp:id` stays the cross-session alias.
2. **Progressive inspection instead of one big dump.** A structure browser (layer tree, `maxDepth`,
   breadth-first, summaries at the depth limit), an artboard view (everything overlapping an
   artboard, across layers), then per-object detail tools split by concern: appearance (fill/stroke
   stacks, gradients, opacity, blend, mask context), typography (font runs, missing-font flag,
   overflow), geometry, bounds. Our `get_document` returns one tree with offset paging.
3. **Uniform truncation contract:** `truncated: true` plus a `resume_hint` saying how to continue.
4. **Uniform batch result contract:** `success_count`, `fail_count`, `failed_objects[{uuid, reason}]`,
   `skipped_objects` for inherent limits ("do not retry"), and honest partial-success reporting.
5. **Conservative defaults stated in the schema:** locked/hidden objects are skipped unless
   `force=true` *and* the user named them; deleting an artboard keeps its art; resource extraction
   defaults to dry-run; swatch names must never be guessed.
6. **One coordinate convention everywhere:** canvas-global points, Y-down, with artboard bounds as
   the origin for relative placement. Ours is mixed (e.g. `path_import_svg` returns Y-up bounds).
7. **Preflight as a scoped fact report:** scopes (document, images, links, text, colors, objects),
   findings aggregated by tag with `affected_objects[]`, and `checks_skipped` so an empty category is
   only trusted when the check actually ran. Directly addresses the failure mode of our
   `preflight_check` bug (silent "all clear").
8. **Disambiguation in descriptions:** each tool says which sibling to use instead (align vs move,
   flat color vs gradient vs recolor, new artboard vs new document, export vs save).
9. **Batch meta-tool:** up to 25 tool calls in one round trip, `stopOnFirstError` for dependent
   steps. Our SOC `compound` / `element_create_batch` already cover most of this.

## Capability gap

Covered by us already (as SOC ops inside `execute_task`, 47 handlers): shapes and paths, batch
creation, fill/stroke/opacity/gradient, group/ungroup, clipping mask, z-order, align/distribute,
layers, basic text, boolean ops (Clipper), SVG path import, place and trace, export, undo and
checkpoints. Our own advantages, which Adobe lacks: annotated previews with object grounding, ID
preconditions, assertions (`assert_*`), checkpoints, and an authenticated bridge.

Missing on our side, in priority order:

| Area | Missing capability |
|------|--------------------|
| Artboards | duplicate; artboard background color (list/create/resize/preset/fit/move/delete/activate done) |
| Documents | document properties (color mode, bleed, units); list and switch done |
| Inspection | none open (structure, artboard, selection, appearance, typography, text overflow done) |
| Text | none open (missing-font report, style-preserving replace, font replace, outlines, character ranges, paragraph spacing done). Not covered: character/paragraph *styles*, OpenType features, fitting text to its frame |
| Transforms | move/scale in absolute mode, rotate around combined bounds |
| Effects | live drop shadow and Gaussian blur (apply, read, remove) |
| Swatches | read document and library swatches, create swatches and groups |
| Paths | simplify / smooth cleanup, rasterize |
| Preflight | none open (scoped report with images, links, fonts, overset, overprint, rich black, total ink done). Not covered: font embedding rights, transparency flattening, trapping, output-intent / ICC checks, link status of *placed* files (not exposed to scripts) |

## Not adopting

Firefly and generative tools, Adobe-hosted uploads, the in-app clarification tool (the MCP client
already asks questions), and the prompt relay to Adobe's assistant.

## Verified live (Illustrator 30.8.1)

- `PageItem.uuid` is a short numeric string; `getPageItemFromUuid()` round-trips to the same
  object, the uuid survives moving into a group, and `duplicate()` gets a new one.
- Layers and artboards have no uuid.
- `getPageItemFromUuid()` throws on an unknown uuid, and returns a different `GroupItem`-typed
  object for a `CompoundPathItem` (handled in `resolvePageItemByUuid`).
- **uuid lifetime across save, close and reopen** (probe: path, compound path with two child
  paths, group with a child, text frame, each with an `@mcp:id` note; saved to a temp `.ai`):
  - A uuid is a per-document counter, not an id stored in the file. Fresh items got 473..479;
    `saveAs` and a later read in the same session left them unchanged.
  - Closing the saved document did not touch the file (mtime unchanged). The published claim that
    "a new uuid is written on close" was not observed: renumbering happens when the file is loaded.
  - First reopen: every item was renumbered in document order (473..479 -> 433..439). Second and
    third reopen, and a reopen while another document was open: identical 433..439. The numbering
    is deterministic for an unchanged file.
  - It is positional, not stable identity: after deleting the item numbered 433, adding one item
    and saving, the next reopen shifted every surviving item down by one (text 439 -> 438, group
    437 -> 436, ...). The same uuid can therefore name a different object after an edit plus
    reopen.
  - uuids collide across open documents: two new documents both numbered their first item 473.
  - `doc.getPageItemFromUuid()` ignores `doc` and resolves in `app.activeDocument`: with documents
    A and B open, `a.getPageItemFromUuid("473")` returned B's item while B was active. Our callers
    pass `app.activeDocument`, so they are correct only while the active document does not change
    between inspect and act.
  - `@mcp:id` notes on every item type (compound path and group included) survived every save,
    close and reopen, including the edit-then-reopen case. `@mcp:id` is the identity to use across
    a reopen or a document switch.
- A document created with `app.documents.add(..., 600, 400)` has artboard rect `[0, 400, 600, 0]`:
  the artboard does not start at y = 0, so "visual (x, y) -> [x, -y]" is not universally valid.
  The typed tools sidestep this by reporting artboard and object bounds in one canvas space.
- No ExtendScript access to Illustrator's artboard preset table was found; presets ship as our own
  data (`ARTBOARD_PRESETS`).

## Verified live in phase 2 (Illustrator 30.8.1)

Text:
- `TextFrame.textRanges` is one range per *character*, not per style run; runs must be rebuilt by
  comparing neighbours. In the continuation frame of a threaded story `textRanges` is indexed by
  position in the story (indices below `textRange.start` throw "The specified text range is
  invalid" although `length` is the frame's), so frames are read through `story.textRanges`.
- `story.characters[i]` with `.length = n` addresses an n-character range; assigning `.contents`
  to it gives the new text the attributes of its first character, so a uniformly styled match
  keeps its style on replace. Reading characters one by one is ~15x slower than indexing a cached
  `textRanges` collection.
- Missing fonts: `app.textFonts.getByName()` does not throw for a missing font; Illustrator
  registers a placeholder, and the run's `textFont.family` carries an embedded-subset prefix
  (`XPUYQY+Name`) that differs from the installed record's family. A removed font disappears
  from `app.textFonts` only once no open document uses it. Blind spot: a file saved without PDF
  compatibility reopens with the run bound to the placeholder record itself, and nothing in the
  DOM tells it from an installed font.
- Overflow: composed `lines` cover only the visible text; the story text past the end of the last
  line is hidden. The last frame of a thread reports itself as its own `nextFrame`; `nextFrame`
  and `previousFrame` throw for point and path text. Path text can overflow.
- `TextFrame.createOutline()` returns a `GroupItem` of one `CompoundPathItem` per glyph, drops the
  frame's name and note (restored onto the group by `illustrator_text`) and invalidates its uuid.
- Paragraph spacing is `paragraphAttributes.spaceBefore` / `spaceAfter`; `characterAttributes.size`,
  `tracking` and `fillColor` read back exactly in an RGB document (in CMYK Illustrator converts an
  RGB fill).

Preflight facts:
- Effective PPI of a placed or embedded raster is 72 / the matrix scale (`matrix.mValueA..D`) against
  `boundingBox` pixel size; `PlacedItem.file` throws "There is no file associated with this item"
  for a missing link; `RasterItem.status` is `RasterLinkState.DATAFROMFILE` / `DATAMODIFIED` /
  `NODATA`. A placed (linked) file exposes no link status to scripts.
- The `[Registration]` swatch is a `SpotColor` with `spot.colorType === ColorModel.REGISTRATION`;
  `fillOverprint` reads back on paths.
- A document opened from a file whose font is missing shows nothing to scripts besides the
  placeholder signal above; SVG and PDF imports substitute Arial instead of leaving a missing font.

Transport (affects every tool):
- Illustrator's native `JSON` has `stringify` but **no `parse`**, and its `stringify` escapes only
  `"` and `\n`: tab, `\r`, other control characters, U+2028/U+2029 and the backslash come out raw.
  host.jsx's "already an envelope" passthrough needs `JSON.parse`, so it never fires and every
  result string is stringified twice. Any result containing a quote, a backslash or a control
  character then fails the CEP panel's strict `JSON.parse`. The typed tools avoid this with a
  `dm1:` wire format (see CHANGELOG). **Open:** the other tools (`execute_script`, `execute_task`,
  `get_document`, `query_items`, ...) still depend on host.jsx's serialization; a fix belongs in
  host.jsx (own serializer, drop the dead passthrough) and needs a panel rebuild, so it was not
  made here.
