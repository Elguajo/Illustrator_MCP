# Changelog

Notable changes to this project. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

History before 3.1.0 was not tracked here; see `git log` for it.

## [Unreleased]

### Added

- **`tests_live/`: a live suite for every `illustrator_*` tool** (101 steps, macOS +
  Illustrator). It simulates only the CEP panel's WebSocket hop: the exact script the server
  builds runs through `cep-extension/jsx/host.jsx` via osascript, so tool models, library
  injection, envelopes and formatting are real. Each step reads state back through an
  independent probe. It works only in documents it creates, suppresses alerts, and refuses to
  start when foreign documents are open.

- **`illustrator_text` replace: styled matches, batches, dry run.** Found while
  translating a 12-artboard deck, where bold lead-ins and bullet markers sit in
  separate style runs:
  - `replace_runs` writes one string per style run of a match, each in that
    run's own font/size/color, instead of skipping the match. A run count that
    does not match is skipped as `run_count_mismatch`.
  - `skipped_occurrences` (and dry-run `matches`) now carry the match's `runs`
    (text, font, size), so the caller can build `replace_runs`.
  - `replacements` applies a list of pairs in one call, in order, each on the
    text the previous pair left; the result has one entry per pair, and
    failures and skips name their `pair`.
  - `dry_run` reports every match and whether it would be replaced, changes
    nothing.
  - Line breaks: `\n` and `\r` are a paragraph break, `\u0003` is a forced line
    break, in `find` as well as in `replace`. Verified live on Illustrator 30.8.1
    in a throwaway document (real library prelude through osascript): a soft
    break written by `replace` reads back as character 3 and gives two lines,
    and `find` with `\u0003` matches it.

- **Typed tools keyed by Illustrator's native `PageItem.uuid`** (first step of
  the hybrid toolset in `docs/ADOBE_MCPTOOLKIT_STUDY.md`):
  - `illustrator_inspect` — progressive views: `structure` (breadth-first
    layer/group tree, `max_depth`, never half-lists a container), `artboard`,
    `selection`, and `details` (fill/stroke incl. gradients and spots, font
    runs with a missing-font flag, anchor counts). Capped views return
    `truncated` and a `resume_hint`; unknown uuids land in `failed_objects`.
  - `illustrator_artboards` — `list`, `create`, `update` (rename, resize around
    a 9-point anchor, move, exact bounds), `delete` (keeps artwork, refuses the
    last artboard), `activate`, `fit` (computed from bounds, selection
    untouched), and named `presets` that keep the target's orientation.
  - `illustrator_document` gains `list` and `switch`.
  - Task Protocol target `{"type": "uuid", "uuids": [...], "document": name}`:
    O(1) resolution instead of the `@mcp:id` scan of `doc.pageItems`; a
    missing, hidden, or locked item fails collect. uuids are numbered per
    document and collide across open documents (two new documents both number
    their first item 473), and `getPageItemFromUuid()` ignores its receiver and
    resolves in the active document. So inspect results name their `document`,
    the target must carry it back, and collect fails with
    `reason: "document_mismatch"` when another document is active;
    `resolvePageItemByUuid` also returns null for a hit owned by any document
    other than the one passed in (verified live on 30.8.1).
  - All new bounds are canvas-global, Y-down, in the same space as artboard
    bounds. Request errors (unknown artboard, last-artboard delete, nothing to
    fit) return `V011` with their own message instead of `E999` "review script
    syntax".

- **`illustrator_text`: edit existing text by native uuid** (second step of the
  hybrid toolset). Actions:
  - `replace` — find/replace that keeps each match's character attributes
    (font, size, tracking, color, ...). Matching is per story, so a match that
    crosses threaded frames is found. A match spanning differently styled
    characters is skipped and listed in `skipped_occurrences` with its index.
  - `replace_font` — from a font in use (a missing font included, by the
    PostScript name `illustrator_inspect` reports) to an installed one; only
    runs in the source font change.
  - `style` — font, size, fill color, tracking over a 0-based character range,
    plus paragraph `space_before` / `space_after` for every paragraph the range
    touches. Colors are checked by read-back; in a CMYK document an RGB color
    is converted by Illustrator and reported as `color_converted`.
  - `outline` — text to glyph paths; returns the group's uuid and child uuids,
    and moves the frame's name and note (`@mcp:id`) onto the group.
  Every result follows the batch contract (`success_count`, `fail_count`,
  `failed_objects[{uuid, reason}]`, `skipped_objects`) and names its
  `document`. Locked or hidden frames (also through their layer or group) are
  never modified; they are listed in `skipped_objects`. Every write is read
  back and a value that did not stick is `verify_failed`, not success.
  `uuids` require `document` (uuids are per document).
- **Text overflow detection** in `illustrator_inspect(view="details")` and in
  preflight: area and path text report `overflow: {overset, overset_chars,
  overset_preview}`; a frame that threads on reports `continues_in`; point text
  has no overflow. Measured from composed lines against the story text, and
  checked live on area text, threaded area text and path text (a trailing
  paragraph return is not overset).
- **Preflight v2** (`illustrator_preflight_check`). Scopes `document`,
  `objects`, `text`, `images`, `links`, `colors`; 21 checks identified as
  `<scope>.<name>`. Findings are aggregated by tag with exact `count`,
  `severity` (error / warning / info) and `affected_objects[{uuid, name, type,
  layer_path, mcp_id?, ...raw facts}]` capped by `max_affected`. New checks:
  missing fonts (with `fonts_used`), overset text, low effective PPI (with
  pixel size, placed size and file), missing and modified links, hairline
  strokes, stray points, partially off-artboard objects, empty artboards,
  raster-effects resolution, Registration color, white overprint, rich black in
  small text, total ink, RGB/CMYK image mismatch, spot-color summary.
  `checks_run` / `checks_skipped` (reason `scope_not_requested`,
  `disabled_by_parameter`, `partial`, ...) say which checks covered every
  object, so an empty category is only trusted when its check ran; known blind
  spots are written to `check_notes`. Hidden objects are not checked (they are
  reported under `objects.hidden`).

  **Compatibility.** Every v1 parameter is still accepted and `ok` /
  `warnings` keep their meaning (`ok` = the check ran; one warning per
  non-info finding). `result.issues` is still returned, derived from the
  findings (`type`, `tag`, `count`, `message`, `samples`; `severity: "info"`
  unchanged), and v1 types that existed (`off_artboard`, `zero_size`,
  `empty_text`, `locked`) keep their names. Behavior differences, documented
  as the break:
  - `result.checks` (the raw v1 sub-reports, e.g. `checks.bounds`) is gone;
    use `findings`, `checks_run`, `checks_skipped`.
  - Without `artboard_index`, v1 judged items against the *active* artboard;
    v2 treats every artboard as valid, so an item sitting on another artboard
    is no longer reported. Pass `artboard_index` for the v1 reference.
  - `off_artboard` now means entirely outside the reference artboard(s). Under
    the default `policy="fully-contained"`, v1 counted partially off items as
    `off_artboard` warnings; v2 reports them as `objects.partially_off_artboard`
    (info, no warning), since bleed and decorative overhang are normal.
    `policy="intersects"` hides them altogether.
  - `scope` ("document" / "artboard") still filters items, now documented as
    an item filter distinct from the new `scopes` categories.
  - An out-of-range `artboard_index` is `V011` with a clear message (was `E999`).
  - Hidden objects (and everything in a hidden layer or group) are not checked;
    they appear under `objects.hidden`.
- **`illustrator_effects`**: live drop shadow and Gaussian blur on objects
  addressed by uuid, plus removal. Everything below was verified live on
  30.8.1, rendered to pixels where it mattered.
  - Applied with `PageItem.applyEffect(LiveEffectXML)` from fixed templates.
    The shadow color is set exactly (rendered `#2a9d8f` read back as
    `42,157,143`), with blend modes normal/multiply/screen and a darkness mode.
  - It works on paths, compound paths (the effect goes on the compound),
    groups and text.
  - Effects cannot be read from script: there is no DOM property, FXG export
    silently writes `.ai`, and the `.ai` private data is compressed. The tool
    says so and reports visible bounds before and after as its evidence. An
    apply that does not enlarge the bounds of an unpadded object is reported as
    failed, and one on an object already padded by an earlier effect as
    `verified: false`.
  - Removal re-applies the document's `[Default]` graphic style, then restores
    and reads back fill, stroke, opacity, blend, isolation and knockout. That
    style re-aligns a centered stroke to the inside, which cannot be scripted
    back, so stroked paths are skipped unless `allow_stroke_realign=true`.
  - Replaying "Reduce to Basic Appearance" through a generated action
    (`app.loadAction` + `app.doScript`) hung Illustrator at 100% CPU, so it is
    deliberately not used.
  - Locked and hidden objects, including those inside a locked or hidden group
    or layer, are reported in `skipped_objects`. An optional `document` stops
    the call when another document is active.
- **`illustrator_swatches`**: `list` (paged, with groups and kinds:
  process/spot/global/gradient/pattern), exact `get`, `create`
  (process/spot/global), `create_group`, `delete`, `delete_group`,
  `libraries` and `library`.
  - Names are never guessed: a miss returns only existing names within two
    edits or containing the query.
  - Illustrator accepts duplicate swatch and group names, so the tool refuses
    them itself.
  - `SwatchGroup.remove()` deletes its swatches, so `delete_group` moves them
    out first unless `keep_swatches=false`.
  - Deleting `[None]`/`[Registration]` is a silent no-op in Illustrator; those
    are skipped and every delete is read back.
  - `.ai` libraries are read by opening them with alerts suppressed (a library
    with stale links would otherwise block on a modal dialog), then closing them
    and restoring the active document.
  - `.ase` libraries, which `app.open` refuses, are parsed directly. The parser
    was checked against an independent implementation on all 40 shipped files
    (1866 swatches). `.acb`/`.acbl` color books are reported as not readable.

### Fixed

- **Found by a live run of every tool against Illustrator 30.8.1** (`tests_live/`):
  - `illustrator_text` stripped leading and trailing whitespace from `find`,
    `replace`, `replace_runs` and batch pairs (`ToolInputBase` strips strings), so
    `"big " -> "enormous "` silently lost its space and `replace_runs=["Our mission ",
    "is"]` glued words together. The text models keep whitespace now.
  - `illustrator_path_import_svg` accepted `name` and ignored it; `drawPathPoints` never
    applied `spec.name`. It does now. The docstring example used a `fill` parameter that
    did not exist (and was silently ignored); `fill={r, g, b}` is implemented.
  - `illustrator_export_document` timed out (R005) on SVG with live text although the file
    was written: `ExportOptionsSVG.fontSubsetting` defaults to ALLGLYPHS, so one Myriad Pro
    text frame exported 949 KB in 1-15 s. SVG now sets `GLYPHSUSED` (3 KB in 50 ms; the
    ten-frame test document went from 1.2 MB / 3-60 s to 10 KB / 0.4 s) and gets a 120 s
    timeout as headroom (PDF keeps 60 s).
  - SVG export and PDF `saveAs` re-point the active document at the exported file (name,
    path, `saved=True`; a plain Save would then write SVG/PDF). That was silent; the result
    now carries `document.before/after/renamed` and a warning.
  - `illustrator_place_file(trace=True, linked=False)` always failed with "Trace target not
    found": `embed()` replaces the PlacedItem with a RasterItem that has an empty note, and the
    stale reference accepted the marker without error. The marker now goes to the raster.
    An unknown `trace_preset` was silently ignored (`loadFromPreset` returns false instead of
    throwing); it is reported as a warning now.
  - Every `{"type": "selection"}` task did nothing ("Resolved 0 targets", `group_ungroup`,
    `style_set_fill`, ...): the pipeline cleared `app.selection` right after its collect stage, but
    the ops stage resolves the selection again. It also wiped the user's selection on any
    `illustrator_query_items` call. The selection is kept when the task targets it and on dry runs.
  - `group_ungroup` could not ungroup anything: it moved children relative to the parent
    Layer with `PLACEAFTER`, which Layers reject, so every group failed ("Failed to ungroup").
    Children now move relative to the group, front-most first, keeping the stacking order.

- **Fill and stroke edits on compound paths were silent no-ops.**
  `style_set_fill`, `style_set_stroke`, `style_remove_fill`,
  `style_remove_stroke`, `style_set_gradient`, `style_clone`, and
  `element_modify` wrote paint to the `CompoundPathItem` itself, which has no
  paint of its own: Illustrator accepted the write, the op reported
  "1 modified", and the artwork kept its old color (confirmed live on 30.8.1 by
  reading the fill back). Paint now goes to every child path through
  `paintTargetsOf`; `style_snapshot` and `style_clone` read it from the first
  child. Same defect class as the earlier `path_boolean` fill loss.

- **Typed-tool results broke the CEP panel whenever the text held a quote, a
  backslash or a paragraph break.** Measured live on Illustrator 30.8.1: the
  native `JSON` has `stringify` but no `parse`, and its `stringify` escapes only
  `"` and `\n` (tab, `\r` — Illustrator's paragraph separator — other control
  characters, U+2028/U+2029 and the backslash itself come out raw, so `a\b` was
  silently corrupted). host.jsx's "already an envelope" passthrough needs
  `JSON.parse`, so it never fires: every returned string is stringified a
  second time, and the panel's strict `JSON.parse` rejected the result.
  `illustrator_inspect` on a two-paragraph text frame failed this way (confirmed
  live). Results of `dm_script` tools now leave ExtendScript as `dm1:` + JSON
  built by our own serializer with `%`, `\` and `"` percent-encoded, which is
  immune to how host.jsx serializes strings; Python decodes it (`run_dm`,
  `unwrap_dm_response`). Pinned by `tests/test_dm_json_escaping.py`, which runs
  the real host.jsx under an Illustrator-like `JSON`. The rest of the tools
  still go through host.jsx's serialization and have the same exposure for
  results containing quotes or backslashes (see Known issues in the study).
- **Missing fonts were never reported for most text.** `app.textFonts
  .getByName()` succeeds for a missing font because Illustrator registers a
  placeholder; the run's font then carries an embedded-subset family
  (`XPUYQY+Name`) that differs from the installed record. `dmFontStatus` uses
  that signal. `TextFrame.textRanges` is also per *character*, not per run, so
  phase 1's `font_runs` looked at the first 200 characters only; runs are now
  rebuilt over the whole frame.
- **Font runs of the second frame of a threaded story failed** with "The
  specified text range is invalid": in a continuation frame `textRanges` is
  indexed by story position. Frames are now read through their story.

- Worked around an Illustrator quirk: `getPageItemFromUuid()` returns a
  *different* object typed `GroupItem` for a `CompoundPathItem`, and throws on
  an unknown uuid. `resolvePageItemByUuid` (`mcp_id.jsx`) recovers the real
  compound path from the wrapper's parent and turns a miss into `null`.

- `illustrator_preflight_check` is fixed after being completely non-functional:
  it always returned `ok: true, result: {}` regardless of document state,
  confirmed live against a document with a real off-artboard item and a real
  empty text frame — neither was reported. host.jsx's `executeScript()` wraps a
  bare script return as `{ok: true, data: <value>}`; the Python side looked for
  a `result` key inside that envelope, which never exists there. Fixed by
  reusing `unwrap_jsx_result`, the shared helper `execute.py` already used
  correctly for the same envelope shape.

  Fixing that alone would have newly exposed a second, previously-dormant
  defect: `ok` was computed from issue severity, and `make_envelope`'s contract
  drops `result` whenever `ok` is `False` — with no `error` supplied to
  compensate, a real finding would have produced `{ok:false, error:null,
  result:null}`, discarding the very data the tool exists to report. That path
  was never reachable in production because the unwrap bug always fed it `{}`.
  `ok` now reflects whether the check ran, matching the tool's own documented
  contract; findings surface through `warnings` and the full `result` payload.

- `illustrator_query_items` silently dropped `error.suggestions` on every
  reported failure. The reachable error branch built `{code, message}` with no
  suggestions key; a second branch that did preserve suggestions could never
  run, because it only executed when the error list was already empty. Fixed
  by reusing `_taskreport_first_error`, the canonical extractor `execute_task`
  already uses for the same `makeError()` shape.


### Security

- **The CEP bridge now authenticates its handshake.** It previously accepted any
  socket that reached `127.0.0.1` while executing arbitrary ExtendScript, which
  can read and write files. A WebSocket handshake is not covered by the
  same-origin policy, so a page in an open browser tab could connect and drive
  Illustrator; the single-client slot was the only barrier and it is empty
  whenever the panel is not connected.

  The server now writes a random per-session secret and the port to
  `~/.illustrator-mcp/session.json` (mode `0600`, atomic replace, removed on
  shutdown). The panel reads it through Node and offers it as the WebSocket
  subprotocol `mcp.token.<secret>`. Handshakes without the secret get HTTP 401;
  handshakes with an `http(s)` `Origin` get HTTP 403 even with a valid secret.

### Added

- `scripts/package-cep.sh` — builds and stages the panel for distribution and,
  with `--sign`, produces a signed `.zxp`. `install-cep.sh` only ever produced a
  debug-mode install that works on the developer's machine and nowhere else.
- CI (`.github/workflows/ci.yml`): the suite on Python 3.10/3.11/3.12 plus a
  panel job (`npm ci`, `tsc --noEmit`, build). The repository had 1600+ tests and
  nothing running them.
- `LICENSE` — the MIT text `pyproject.toml` already declared.

### Fixed

- `path_boolean` no longer loses the subject's fill. `reconstructRegions`
  applied the transferred style to the `CompoundPathItem` container, which has
  no `filled`/`fillColor` of its own: the assignment raises nothing and reads
  back correctly while the document is untouched, so a subtract that cut a hole
  came back unfilled and the tool reported success.
- Annotated previews no longer lose labels. Boxes and labels are drawn in two
  passes, so a later annotation's outline and translucent fill cannot veil the
  labels already drawn — on a 57-item artboard only about 15 stayed legible
  before. Labels whose position falls outside the canvas are clamped back
  inside instead of being drawn off-image. A veiled or missing `[N]` breaks the
  identity handed to `illustrator_ground_object`.

### Changed

- `pyclipper` moved from the optional `[geometry]` extra to core dependencies.
  `path_boolean` is a registered tool and `execute_script` instructs the agent to
  use it for every boolean, so a default install shipped a documented tool that
  raised `ImportError` on first use.
- `websockets` floor raised to `>=14.0` — the bridge uses the asyncio server's
  `process_request(connection, request)` signature.
- The panel reads its port from the handshake file instead of hardcoding 8081,
  so a custom `WS_PORT` needs no rebuild; the footer shows the live endpoint.

### Removed

- `uxp-plugin/` — an unfinished second bridge client at version 0.1.0 with no
  icons, tests, CI job or references anywhere else, which could no longer
  connect to the authenticated bridge.
- `scripts/gen_schemas.py` — running it overwrote the `op_schemas.jsx` forwarder
  with a full second copy of every schema, forking the source of truth owned by
  `compile_contracts`. The README told people to run it.

### Tests

- Contract drift checks now read `contracts.jsx` and actually run. They had been
  gated on an `op_schemas.json` that another test asserted must not exist, so
  they skipped on every run while the suite looked green.
- The annotated-overlay tests no longer require a PNG from the original author's
  machine (`C:\Users\k.jin\...`); the fixture is rendered with Pillow.
- Bridge message routing, `execute_script_async` and the session lifecycle are
  covered — `websocket_bridge` went from 49% to 77%.
- Export tests write to a temp directory instead of a literal `C:/output` path,
  which on POSIX created a `C:` directory in the working tree on every run. CI
  fails if one reappears.
