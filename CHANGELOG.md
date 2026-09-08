# Changelog

Notable changes to this project. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

History before 3.1.0 was not tracked here; see `git log` for it.

## [Unreleased]

### Fixed

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
