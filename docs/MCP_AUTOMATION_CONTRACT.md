# Automation execution and recovery contracts

These changes are additive. The canonical envelope remains
`{ok, warnings, error, diagnostics, result}`; partial typed operations retain
their existing `ok` semantics. MCP errors still carry `isError: true`.

## Host JSON

`cep-extension/jsx/host.jsx` owns `mcpJsonStringify` and `mcpJsonParse`, both ES3.
It installs them as `JSON.stringify` / `JSON.parse` for injected scripts too.
String keys/values escape controls, backslashes and Unicode; array holes become
null, non-finite numbers become null, undefined/function object fields are omitted,
cycles are rejected. Parsing accepts JSON grammar without eval and rejects the
prototype setter key `__proto__`. The codec is for data serialization; it does not
implement stringify replacer/indent arguments or automatic Date/toJSON conversion.
Plain returned objects retain their keys; native DOM objects keep the existing
bounded projection. Typed tools retain the `dm1:` encoding for older hosts.

Reload the updated panel and host together. Building the panel does not update a
previously installed/running CEP extension.

## Execution evidence

`diagnostics.execution` contains `state`, `safe_to_retry`, and, for submitted
bridge requests, `request_id`. Errors also contain `error.safe_to_retry`.

| State | Evidence | Retry without canvas inspection |
|---|---|---|
| `not_started` | No connection before dispatch, library injection failed, panel rejected busy/unavailable, or typed session guard rejected before the operation | Safe; fix the rejected input/availability first |
| `running` | Fresh heartbeat identifies the uncertain request as actively executing | Unsafe |
| `completed` | Panel sent the callback result, including a late result after timeout | Unsafe; completion does not prove success or rollback |
| `unknown` | Submitted but no trustworthy completion, timeout, dropped connection, invalid callback, or missing execution evidence | Unsafe |

Timeout does not cancel ExtendScript. The panel keeps its busy guard through a
WebSocket reconnect and publishes busy changes immediately. It reports an error
when CSInterface is unavailable rather than mock success.

The bridge rejects a new script before sending when another request is pending
or the panel reports busy, including after timeout. macOS `evalScript` can block
the panel's JavaScript event loop; a panel-only guard would queue the new script
and execute it later. Reconnect may occur only after that native call returns.

Poll without running JSX:

```json
{"params":{"view":"execution","request_id":17}}
```

Use `illustrator_inspect`. This returns bridge connectivity, panel health and
execution metadata for that request (latest if omitted). History keeps metadata
for 64 non-streaming requests in this server process, without scripts/results.
An evicted/unknown ID returns null, not proof of cancellation. Late completions
resolve uncertainty while retained; after reconnection a result may be lost.
Read the document/canvas before deciding which operation still needs recovery.

## Native UUID scope

Inspection and typed document references now include `document.session_id`.
The token is kept in `$.global` against the open Document, never written into the
artwork. Repeated inspection and switching away/back keep the token. Closing and
reopening creates a new token; closed references are pruned on the next read.

Copy the token into `document_session_id` for inspect, artboard, text, effects,
swatch operations or a native UUID target:

```json
{"type":"uuid","uuids":["412"],"document":"poster.ai","document_session_id":"doc-..."}
```

The field is optional for compatibility. Existing document-name guards remain;
omitting the token gives no protection against a reopen with the same filename.
A stale typed token fails with V011 before the operation. A SOC UUID selector
fails at collection before that operation, but earlier batch operations may have
changed the document. Use `@mcp:id` for identity across reopen.

## Change and verification evidence

`diagnostics.changes` summarizes only existing operation evidence:

- `status`: `full`, `partial`, `unverified`, or `dry_run`.
- Typed batches retain counts, successful affected identities, failed/skipped
  objects. Failed objects may already have changed; this is not a rollback claim.
- SOC batches retain passed/failed/unattempted counts, created IDs, compact
  operation reports, truncation marker and reported rollback count. The failure
  envelope now preserves its TaskReport for recovery.
- `verification` defaults to `not_performed`. Text operations identify their DOM
  read-back; effects distinguish appearance read-back from the visible-bounds
  proxy, including unverified cases. Full completion is never visual approval.
- Opaque raw results stay `unverified`. No identities or successful writes are
  invented. Inspect or preview explicitly when domain evidence is absent.

## Tool discovery

`ILLUSTRATOR_MCP_TOOL_PROFILE=all` remains the default (18 tools).
Opt into `core` before server startup for these 10 tools: inspect, document,
execute_task, execute_script, artboards, text, export_document, history,
ground_object, query_items. Instructions route only available tools. This is
discovery configuration, not a permissions boundary.

Tool descriptions contain concise purpose/caveats/examples. Read
`illustrator://reference/tools` for full function documentation and
`illustrator://reference/extendscript` before raw scripting.

## Validation

Regression tests cover partial native JSON, session continuity/reopen/collision,
timeout/late completion/busy rejection, bounded recovery history, partial-result
evidence, unchanged default registry and startup core profile.

`tests_live/live_cep_contract.py` tests the real MCP stdio -> WebSocket -> installed
CEP -> Illustrator path, creates only owned fixtures and restores the original
active document. On 2026-10-03, all ten checks passed with the installed
`illustrator-mcp 3.1.0rc1` wheel, signed CEP 1.1.0 and Illustrator 30.8.1 on macOS.
This includes independent session reads, hostile JSON, typed read-back, save/reopen,
stale typed/UUID rejection, timeout with late completion, disconnect/reconnect,
and counts proving that rejected requests did not execute or create duplicates.
A separate read-only package smoke passed with CEP debug mode disabled.

The older `tests_live/live_automation_contract.py` uses osascript and substitutes
the WebSocket hop; its earlier Apple Events timeout is not the evidence for this
release. Background fixtures now suppress UI prompts temporarily and restore the
previous interaction level, including cleanup. See `MCP_RELEASE_READINESS.md` for
artifact hashes, commands and the supported validation scope.
