# MCP application automation: practices worth transferring

Research date: 2026-10-03. Local baseline: `89c0d86`.
Follow-up to [MCP_LANDSCAPE_STUDY.md](MCP_LANDSCAPE_STUDY.md).
This is a research note; proposed contracts are recommendations, not implemented behavior.

## Conclusion

The most useful recurring design is a closed loop: discover the target and its state, perform a
bounded operation, return affected identities and evidence, then recover according to what actually
ran. A typed/raw hybrid supports this loop, but is not itself evidence of reliability.

For Illustrator, the largest practical opportunities are trustworthy serialization, execution-aware
recovery, explicit verification outcomes, and document identity. Adding more tools is secondary.

## Method and evidence limits

The sample covers 12 projects: vendor-maintained browser/design integrations, community application
bridges, and general UI automation. Vendor maintenance, published releases, examples, source, and
regression tests are useful maturity signals. They do not establish a common task-success rate.
Specialized projects such as the selected Revit and OBS bridges are included for concrete designs,
not presented as independently proven production leaders.

Public HEAD revisions were resolved with `git ls-remote`; README and selected source files were
read at those exact commits. Commit-pinned links below avoid relying on stale search extracts.
GitHub's unauthenticated API was rate-limited; Git and raw source retrieval succeeded.

Evidence labels:

- **Code**: implementation and/or regression-test source inspected.
- **Contract**: documented behavior; backend execution not independently inspected.
- **Proposal**: inference for this repository, requiring validation before adoption.

No external server was installed or run, and no target application was controlled in this study.
Upstream tests were inspected, not executed. Performance claims in READMEs are not our measurements.

## Comparative sample

| Project | Evidence inspected | Distinctive practice | Transfer limit |
|---|---|---|---|
| Figma MCP guide | Contract: authoring skill [F1] | Return every affected node ID; an error explicitly tells the agent whether canvas inspection is needed before retry | A skill is a client workflow contract, not a server enforcement mechanism |
| Blender MCP | Code: socket client, addon, safe-mode filter, threading regression [B1], [B2], [B3], [B4] | Serialize socket exchanges; dispatch application work on the main thread; optional AST validation before script submission | AST filtering covers the MCP path and is explicitly not a sandbox around Blender |
| MCP for Unity | Code and contract: tool groups, batches, readiness, refresh recovery, instance routing [U1], [U2], [U3], [U4], [U5] | Gate actions on editor readiness and freshness; group tools per session; route to an identified editor | Compilation and domain reload are Unity-specific; published batch speedups are not transferable measurements |
| FreeCAD MCP | Code and contract: dispatcher, timeout tests, execution modes, version handshake [FC1], [FC2], [FC3], [FC4], [FC5] | Separate queue timeout from execution timeout; expose jobs and diagnostics independently of GUI work | A started GUI task cannot be safely force-cancelled; headless geometry is a separate execution environment |
| Horizun Revit MCP | Code and contract: verified edits, document gate, security model [R1], [R2], [R3] | Rehearse reversible writes, bind apply to request/document/target state, verify through readback | Revit transactions enable rehearsal and rollback; Illustrator undo is not equivalent |
| DaVinci Resolve MCP | Code: operation envelope, bulk-result regressions, API truth ledger, traces [D1], [D2], [D3], [D4] | Distinguish partial, blocked, failed and unverified outcomes; record API traps against a specific application build | A reported or self-reported check must not be upgraded to host-verified evidence |
| OBS MCP, Tom-R-Main | Code: request client, readback tests, output-schema tests, toolsets, live harness [O1], [O2], [O3], [O4], [O5] | Return applied settings; check advertised request support; provide structured results and opt-in toolsets | Batch success requires checking individual results; reconnection does not establish whether a prior write completed |
| Godot MCP, Coding-Solo | Code: process management and bundled headless operations [G1] | Run the project and capture its actual output/errors rather than judging generated files alone | This integration is not the same as an always-connected live editor plugin |
| Playwright MCP | Contract: snapshots, capabilities, sessions, CLI comparison [P1] | Ground actions in structured page state; support isolated sessions and optional capabilities | Response snapshots can be disabled; persistent profiles can conflict between concurrent clients |
| Chrome DevTools MCP | Contract: configuration and tool reference [C1], [C2] | Offer a slim surface, target snapshot UIDs, and inspect performance evidence through dedicated tools | Browser readiness and navigation have different lifecycle semantics from an Illustrator document |
| Mobile Next MCP | Contract: device discovery, accessibility snapshots, screenshot interactions [M1] | Use accessibility structure when available, with screenshot-based interaction as a fallback | Device/UI identifiers are not persistent document object identities |
| Windows-MCP, CursorTouch | Contract: UI automation and state inspection [W1] | Inspect application/UI state before clicking; offer focused browser-content inspection | Generic UI automation depends on focus, locale and accessible controls; it does not expose Illustrator's document semantics |

## 1. Choose the control surface according to the task

There are three distinct integration families in the sample:

1. **Live application API**: Blender, Unity, FreeCAD, Revit and Resolve. Good for explicit object
   identity, domain operations, and readback.
2. **Process/project execution**: the selected Godot bridge. Good for compiling/running an artifact
   and collecting runtime evidence. Editing a file does not automatically edit an already-open session.
3. **Structured UI inspection**: Playwright, Chrome, Mobile Next and Windows-MCP. Useful when the
   application API does not cover the workflow; accessibility and page structure reduce dependence
   on screenshots alone. [G1], [P1], [C2], [M1], [W1]

**Proposal:** keep Illustrator's CEP/ExtendScript domain integration as the primary surface. Treat
generic UI automation as a separately validated option for genuinely unsupported workflows, such
as a modal dialog. Do not silently substitute clicks for a failed typed operation: identify the
reason for failure and the selected document first.

## 2. Make execution state part of recovery

FreeCAD's implementation distinguishes a queued task that never started from a task that exceeded
its budget after starting. Its tests check that the former is cancelled without running later and
that a running timeout blocks follow-up GUI work until dispatch recovers. Async computation has a
job ID; diagnostics do not require access to the blocked GUI thread. [FC1], [FC2], [FC3]

Unity's readiness resource adds blocking reasons, observation age and retry advice. Its refresh
recovery test waits for readiness after a disconnect instead of treating the disconnect itself as
proof that the editor is usable. [U3], [U4]

**Proposal:** recovery needs at least these distinctions, without introducing a new job framework
for every short action:

| Known execution state | Recovery rule |
|---|---|
| Rejected before submission | Correct the request and resubmit |
| Queued and cancellation confirmed before start | Resubmit when ready |
| Running or completion unknown | Do not repeat the write; query execution/host status first |
| Completed with partial changes | Reinspect affected objects and repair only the missing work |
| Rollback positively verified | Re-evaluate the target state before a new attempt |

For raw scripts, use a conservative retry signal. A transport code alone cannot prove that nothing
ran. A `request_id` correlates messages; it is not an idempotency guarantee. Any future deduplication
must also bind the payload and document session, define retention, and survive the failure modes
it claims to handle.

## 3. Return evidence of the applied result

Figma's authoring contract requires affected IDs and relevant structural evidence. OBS's tests
expect a setting/transform mutation to return the settings/transform read back from the application,
with a bounded screenshot only when requested. Failed screenshot generation is reported without
pretending that an otherwise successful write failed. [F1], [O1]

Resolve's envelope separates operation status from verification and changes. Its regression tests
specifically distinguish all-failed batches from mixed outcomes. A missing measurement is not the
same as a measured zero. [D1], [D2]

**Proposal:** extend the existing Illustrator envelope additively where justified:

- Keep `ok`, `error`, `warnings`, `diagnostics`, and `result` compatible.
- Distinguish full success from partial success, including skipped and failed objects.
- Return created, modified and removed identities with document context when available.
- State whether a check was performed, what it covered, and whether readback matched the request.
- Keep failure of optional visual evidence separate from the state of the mutation.

Do not infer semantic success solely from `isError: false`, an API boolean, or a screenshot.
An operation may complete successfully while the user's requested property remains wrong.

## 4. Bind work to an identified document and a current observation

Unity has explicit instance routing. Revit's document gate verifies the target and its current
identity; typed apply is tied to the rehearsed request and, where implemented, the resolved target
state. `VerifiedModelEdit` verifies properties before and after commit and distinguishes confirmed
rollback from uncertain state. [U5], [R1], [R2], [R3]

**Proposal:** a filename is useful context but not a document-session identity. Test and document
what happens after switching, closing/reopening, Save As, manual edits, and colliding UUIDs. Native
Illustrator UUIDs should remain explicitly session-scoped; persistent `@mcp:id` has different
semantics and its own duplication risks.

If stale-target defects are demonstrated, add a document-session identifier or bounded target
precondition. Avoid hashing the complete document for every operation without evidence that it is
necessary. The relevant lesson from Revit is binding a write to its observation, not reproducing
its transaction framework in ExtendScript.

## 5. Batch related work, while preserving failure boundaries

Unity's batch interface exposes `fail_fast`, parallel-read hints and per-command results. OBS's
client assigns IDs to individual batch entries and restores response ordering. Neither interface
by itself establishes transactional rollback of a batch. [U2], [O3]

**Proposal:** use `execute_task` batches for related operations that can be validated and repaired
together. Make completed, failed and unattempted steps distinguishable. Serialize document
mutations; do not assume that parallel MCP calls make the application DOM concurrent. Split at
document changes, dependencies on returned IDs, or recovery boundaries, not at every individual
shape. Do not automatically replay a partly completed creation batch.

## 6. Reduce the surface without removing discoverability

Unity's groups are session-aware; its documented core is about 30 tools, not a universal three-to-five
tool target. OBS supports explicit toolsets and optional dynamic enabling. Playwright offers extra
capabilities; Chrome has a slim profile. These are different solutions, not one mandatory default.
[U1], [O4], [P1], [C1]

OBS's output-schema tests also provide an important counterexample to dropping all structured
output: genuine typed `structuredContent` can detect bad responses while accommodating older
versions. The redundant string wrapper previously emitted by our server is a different problem.
[O2]

**Proposal:** first remove repeated prose and replace ambiguous parameter explanations with small
examples. Move long reference material out of tool descriptions, while preserving a discoverable
path for clients that do not reliably read MCP resources. Only then measure a compact profile.
Initially make profiles opt-in and retain the existing full registry. Dynamic tool-list changes
require client compatibility checks; they are not needed for a static startup profile.

Measure serialized definitions and actual client token use separately. A characters-divided-by-four
estimate is not a tokenizer measurement, and client loading/caching behavior is not universal.

## 7. Maintain a versioned ledger of application API behavior

Resolve's `api_truth.py` records observed API behavior, the verified application build, recommended
alternatives and associated mitigations. FreeCAD has tests for missing/old/mismatched addon version
information. Blender now has an optional AST allowlist with a stated enforcement boundary.
[D3], [FC5], [B3]

**Proposal:** retain a compact ExtendScript traps resource with an Illustrator build, a reproducer,
and a typed-tool alternative for each important trap. Add focused live regressions for behaviors
that mocks cannot establish. A future capability handshake should describe the loaded CEP host,
not just the Python package installed on disk.

Syntax checks, authentication, MCP annotations, AST policy and process isolation solve different
problems. None should be described as a complete sandbox merely because scripts run inside the
application. Restricting raw execution would be a separate compatibility/security decision.

## 8. Evaluate complete tasks and recovery, not registration counts

Godot exposes actual project output. Resolve traces correlate calls, duration and verification.
OBS separates fake-server and live testing. These give useful building blocks for evidence, but
do not supply a shared published benchmark proving which server architecture wins. [G1], [D4], [O5]

**Proposal:** define baseline tasks before optimizing the Illustrator tool surface:

| Task family | Required evidence |
|---|---|
| Create and arrange shapes | Exact count, geometry/layout, returned identities, visual check |
| Replace styled text | Exact text, preserved intended styling, whitespace and missing-font cases |
| Switch/reopen and mutate | Correct document/object; stale observations do not silently hit a different target |
| Quotes, backslashes and control characters | Exact round-trip values through the real host and MCP transport |
| Fail after the first mutation | Partial state identified; recovery does not create duplicates |
| Timeout after dispatch | Execution uncertainty reported; no blind write replay |
| Locked/hidden/missing targets | Expected skips/failures, with unaffected objects preserved |
| Export | Existing readable output with requested dimensions/format; failure distinguishes missing evidence |

Compare raw and typed routes using the same initial document, model/client configuration and task
assertions. Record task success, repair attempts, wrong-target mutations, duplicate creations,
tool calls, latency and tokens. Repeated runs are required before interpreting small differences.
Separate backend contract tests from agent-level task evaluation and live visual verification.

## Current Illustrator MCP baseline

Observed in this repository at `89c0d86`:

- `app.py` already maps canonical `ok: false` envelopes to MCP `isError: true`, disables the string
  output wrapper, and supplies routing instructions. This aligns with the [MCP tools contract][SPEC].
- `illustrator_mcp/resources/docs/extendscript_reference.md` already contains verified traps.
- The registry has **18 tools**. JSON serialization of the list of `{name, description, inputSchema}`
  with `ensure_ascii=False` produces **89,343 characters**. Instructions are **2,370 characters**;
  no registered tool advertises an output schema. This measures that representation, not wire bytes
  or model tokens.
- The CEP panel already rejects overlapping execution with `BUSY` and sends busy/request heartbeat
  state (`cep-extension/src/hooks/useMCP.ts`). A FIFO queue is therefore not a missing prerequisite
  for serial execution; adding one would change scheduling and cancellation behavior.
- `websocket_bridge.py` times out a waiting request after submission. `request_registry.py` removes
  failed pending requests; this is not proof that Illustrator stopped or that a late result was
  persisted for recovery.
- Errors can arise before submission or after connection loss (`proxy_client.py`). There is no
  `safe_to_retry` contract in the inspected response path.
- Typed tools have batch counts; server instructions correctly warn that partial success may be
  `ok: true`. Verification reporting still deserves an operation-by-operation audit.
- The document-name guard in `doc_text.jsx` does not distinguish reopening the same filename.
  Existing `tests_live/live_suite.py` checks a wrong name, reopen, and switching separately, but
  does not establish rejection of a reused stale UUID after reopen.
- `host.jsx` still depends on global `JSON.stringify`. The document-model route has its own wire
  encoding; that workaround does not establish correct serialization for every legacy/raw result.

These are source observations. No new live Illustrator results are claimed.

## Recommended sequence for this repository

| Priority | Work | Completion evidence |
|---|---|---|
| First | Freeze baseline task assertions and representative traces | Reproducible initial documents and explicit success conditions |
| 1 | Own host serialization without relying on partial application JSON support | Exact live string round trips, plus host/transport regression checks |
| 2 | Test identity and failure boundaries | Switch/reopen/collision/manual-change and partial-mutation scenarios |
| 3 | Add conservative recovery metadata; expose available execution status | Pre-submission failure distinguished from running/unknown completion; duplicate-free recovery |
| 4 | Audit mutation results and readback, adding only missing evidence | Full/partial/unverified outcomes visible; affected IDs and coverage checked |
| 5 | Compact descriptions/examples; opt-in startup profiles | Measured definitions and client behavior, with existing full profile preserved |
| 6 | Run raw-versus-typed task evaluation | Repeated equivalent tasks and reported quality/cost/recovery results |

Do not redo the already implemented `isError` and instructions work. Do not add generic UI control,
a universal job system, transaction emulation, or a broad abstraction layer without a demonstrated
workflow requirement. Candidate follow-ups above are deliberately separable.

## Source snapshots

The following table and reference links are pinned to the public revisions retrieved for this study.

| Repository | Retrieved HEAD |
|---|---|
| `figma/mcp-server-guide` | [`aaa07946b607`](https://github.com/figma/mcp-server-guide/tree/aaa07946b60797706c131ca50e50ca526a44b073) |
| `ahujasid/mcp-for-blender` | [`60d2a31b4632`](https://github.com/ahujasid/mcp-for-blender/tree/60d2a31b4632a7bc178f3dd636f7e68dfb5c8ae4) |
| `CoplayDev/unity-mcp` | [`91eb0f4ac4c6`](https://github.com/CoplayDev/unity-mcp/tree/91eb0f4ac4c63d9d68c4ce09ee489034205950d0) |
| `neka-nat/freecad-mcp` | [`d6bbe4b38be3`](https://github.com/neka-nat/freecad-mcp/tree/d6bbe4b38be3a622b5981d9d2afa7037ee080534) |
| `HorizunGroup/horizun-revit-mcp` | [`93dababecf3c`](https://github.com/HorizunGroup/horizun-revit-mcp/tree/93dababecf3cc9264d56d317aecf0a81ec1c39c1) |
| `samuelgursky/davinci-resolve-mcp` | [`53f8fc91524c`](https://github.com/samuelgursky/davinci-resolve-mcp/tree/53f8fc91524c71c19b28fceeafe7693eca8121e3) |
| `Tom-R-Main/OBS-MCP` | [`fb6d12234a74`](https://github.com/Tom-R-Main/OBS-MCP/tree/fb6d12234a74de10e53afee7fdc1208e1c01fb8d) |
| `Coding-Solo/godot-mcp` | [`1209744fad78`](https://github.com/Coding-Solo/godot-mcp/tree/1209744fad78f3998f98c7394fd0f6ef50da5281) |
| `microsoft/playwright-mcp` | [`f183dad4a529`](https://github.com/microsoft/playwright-mcp/tree/f183dad4a52965583e3cc1d59b88cdc279e2e57d) |
| `ChromeDevTools/chrome-devtools-mcp` | [`b2f522c8ba0f`](https://github.com/ChromeDevTools/chrome-devtools-mcp/tree/b2f522c8ba0fd2e00a679159b4aa5243de5f1b78) |
| `mobile-next/mobile-mcp` | [`65953016441e`](https://github.com/mobile-next/mobile-mcp/tree/65953016441e1f519d47c98d7bcc742647249c95) |
| `CursorTouch/Windows-MCP` | [`f51d6f14da57`](https://github.com/CursorTouch/Windows-MCP/tree/f51d6f14da57c290dac44f0f465c37c45ca4f394) |

### Primary references

- **F1**: [figma/mcp-server-guide: skills/figma-use/SKILL.md][F1]
- **B1**: [ahujasid/mcp-for-blender: src/blender_mcp/server.py][B1]
- **B2**: [ahujasid/mcp-for-blender: addon.py][B2]
- **B3**: [ahujasid/mcp-for-blender: src/blender_mcp/safe_mode.py][B3]
- **B4**: [ahujasid/mcp-for-blender: tests/test_server_threading.py][B4]
- **U1**: [CoplayDev/unity-mcp: website/docs/guides/tool-groups.md][U1]
- **U2**: [CoplayDev/unity-mcp: website/docs/reference/tools/core/batch_execute.md][U2]
- **U3**: [CoplayDev/unity-mcp: Server/src/services/resources/editor_state.py][U3]
- **U4**: [CoplayDev/unity-mcp: Server/tests/integration/test_refresh_unity_retry_recovery.py][U4]
- **U5**: [CoplayDev/unity-mcp: website/docs/guides/multi-instance.md][U5]
- **FC1**: [neka-nat/freecad-mcp: docs/execution.md][FC1]
- **FC2**: [neka-nat/freecad-mcp: addon/FreeCADMCP/rpc_server/gui_dispatch.py][FC2]
- **FC3**: [neka-nat/freecad-mcp: tests/test_gui_dispatch.py][FC3]
- **FC4**: [neka-nat/freecad-mcp: docs/tools.md][FC4]
- **FC5**: [neka-nat/freecad-mcp: tests/test_version_handshake.py][FC5]
- **R1**: [HorizunGroup/horizun-revit-mcp: src/Horizun.Revit/Commands/VerifiedModelEdit.cs][R1]
- **R2**: [HorizunGroup/horizun-revit-mcp: src/Horizun.Revit/Core/DocumentGate.cs][R2]
- **R3**: [HorizunGroup/horizun-revit-mcp: docs/security-model.md][R3]
- **D1**: [samuelgursky/davinci-resolve-mcp: src/utils/operation_result.py][D1]
- **D2**: [samuelgursky/davinci-resolve-mcp: tests/test_bulk_envelope_summaries.py][D2]
- **D3**: [samuelgursky/davinci-resolve-mcp: src/utils/api_truth.py][D3]
- **D4**: [samuelgursky/davinci-resolve-mcp: src/utils/execution_trace.py][D4]
- **O1**: [Tom-R-Main/OBS-MCP: src/tools/after-change.test.ts][O1]
- **O2**: [Tom-R-Main/OBS-MCP: src/tools/output-schema.test.ts][O2]
- **O3**: [Tom-R-Main/OBS-MCP: src/client.ts][O3]
- **O4**: [Tom-R-Main/OBS-MCP: src/tools/toolsets.ts][O4]
- **O5**: [Tom-R-Main/OBS-MCP: test/live/obs-live.test.ts][O5]
- **G1**: [Coding-Solo/godot-mcp: src/index.ts][G1]
- **P1**: [microsoft/playwright-mcp: README.md][P1]
- **C1**: [ChromeDevTools/chrome-devtools-mcp: docs/configuration.md][C1]
- **C2**: [ChromeDevTools/chrome-devtools-mcp: docs/tool-reference.md][C2]
- **M1**: [mobile-next/mobile-mcp: README.md][M1]
- **W1**: [CursorTouch/Windows-MCP: README.md][W1]
- **SPEC**: [MCP 2025-11-25 tools specification][SPEC].

[F1]: https://github.com/figma/mcp-server-guide/blob/aaa07946b60797706c131ca50e50ca526a44b073/skills/figma-use/SKILL.md
[B1]: https://github.com/ahujasid/mcp-for-blender/blob/60d2a31b4632a7bc178f3dd636f7e68dfb5c8ae4/src/blender_mcp/server.py
[B2]: https://github.com/ahujasid/mcp-for-blender/blob/60d2a31b4632a7bc178f3dd636f7e68dfb5c8ae4/addon.py
[B3]: https://github.com/ahujasid/mcp-for-blender/blob/60d2a31b4632a7bc178f3dd636f7e68dfb5c8ae4/src/blender_mcp/safe_mode.py
[B4]: https://github.com/ahujasid/mcp-for-blender/blob/60d2a31b4632a7bc178f3dd636f7e68dfb5c8ae4/tests/test_server_threading.py
[U1]: https://github.com/CoplayDev/unity-mcp/blob/91eb0f4ac4c63d9d68c4ce09ee489034205950d0/website/docs/guides/tool-groups.md
[U2]: https://github.com/CoplayDev/unity-mcp/blob/91eb0f4ac4c63d9d68c4ce09ee489034205950d0/website/docs/reference/tools/core/batch_execute.md
[U3]: https://github.com/CoplayDev/unity-mcp/blob/91eb0f4ac4c63d9d68c4ce09ee489034205950d0/Server/src/services/resources/editor_state.py
[U4]: https://github.com/CoplayDev/unity-mcp/blob/91eb0f4ac4c63d9d68c4ce09ee489034205950d0/Server/tests/integration/test_refresh_unity_retry_recovery.py
[U5]: https://github.com/CoplayDev/unity-mcp/blob/91eb0f4ac4c63d9d68c4ce09ee489034205950d0/website/docs/guides/multi-instance.md
[FC1]: https://github.com/neka-nat/freecad-mcp/blob/d6bbe4b38be3a622b5981d9d2afa7037ee080534/docs/execution.md
[FC2]: https://github.com/neka-nat/freecad-mcp/blob/d6bbe4b38be3a622b5981d9d2afa7037ee080534/addon/FreeCADMCP/rpc_server/gui_dispatch.py
[FC3]: https://github.com/neka-nat/freecad-mcp/blob/d6bbe4b38be3a622b5981d9d2afa7037ee080534/tests/test_gui_dispatch.py
[FC4]: https://github.com/neka-nat/freecad-mcp/blob/d6bbe4b38be3a622b5981d9d2afa7037ee080534/docs/tools.md
[FC5]: https://github.com/neka-nat/freecad-mcp/blob/d6bbe4b38be3a622b5981d9d2afa7037ee080534/tests/test_version_handshake.py
[R1]: https://github.com/HorizunGroup/horizun-revit-mcp/blob/93dababecf3cc9264d56d317aecf0a81ec1c39c1/src/Horizun.Revit/Commands/VerifiedModelEdit.cs
[R2]: https://github.com/HorizunGroup/horizun-revit-mcp/blob/93dababecf3cc9264d56d317aecf0a81ec1c39c1/src/Horizun.Revit/Core/DocumentGate.cs
[R3]: https://github.com/HorizunGroup/horizun-revit-mcp/blob/93dababecf3cc9264d56d317aecf0a81ec1c39c1/docs/security-model.md
[D1]: https://github.com/samuelgursky/davinci-resolve-mcp/blob/53f8fc91524c71c19b28fceeafe7693eca8121e3/src/utils/operation_result.py
[D2]: https://github.com/samuelgursky/davinci-resolve-mcp/blob/53f8fc91524c71c19b28fceeafe7693eca8121e3/tests/test_bulk_envelope_summaries.py
[D3]: https://github.com/samuelgursky/davinci-resolve-mcp/blob/53f8fc91524c71c19b28fceeafe7693eca8121e3/src/utils/api_truth.py
[D4]: https://github.com/samuelgursky/davinci-resolve-mcp/blob/53f8fc91524c71c19b28fceeafe7693eca8121e3/src/utils/execution_trace.py
[O1]: https://github.com/Tom-R-Main/OBS-MCP/blob/fb6d12234a74de10e53afee7fdc1208e1c01fb8d/src/tools/after-change.test.ts
[O2]: https://github.com/Tom-R-Main/OBS-MCP/blob/fb6d12234a74de10e53afee7fdc1208e1c01fb8d/src/tools/output-schema.test.ts
[O3]: https://github.com/Tom-R-Main/OBS-MCP/blob/fb6d12234a74de10e53afee7fdc1208e1c01fb8d/src/client.ts
[O4]: https://github.com/Tom-R-Main/OBS-MCP/blob/fb6d12234a74de10e53afee7fdc1208e1c01fb8d/src/tools/toolsets.ts
[O5]: https://github.com/Tom-R-Main/OBS-MCP/blob/fb6d12234a74de10e53afee7fdc1208e1c01fb8d/test/live/obs-live.test.ts
[G1]: https://github.com/Coding-Solo/godot-mcp/blob/1209744fad78f3998f98c7394fd0f6ef50da5281/src/index.ts
[P1]: https://github.com/microsoft/playwright-mcp/blob/f183dad4a52965583e3cc1d59b88cdc279e2e57d/README.md
[C1]: https://github.com/ChromeDevTools/chrome-devtools-mcp/blob/b2f522c8ba0fd2e00a679159b4aa5243de5f1b78/docs/configuration.md
[C2]: https://github.com/ChromeDevTools/chrome-devtools-mcp/blob/b2f522c8ba0fd2e00a679159b4aa5243de5f1b78/docs/tool-reference.md
[M1]: https://github.com/mobile-next/mobile-mcp/blob/65953016441e1f519d47c98d7bcc742647249c95/README.md
[W1]: https://github.com/CursorTouch/Windows-MCP/blob/f51d6f14da57c290dac44f0f465c37c45ca4f394/README.md
[SPEC]: https://modelcontextprotocol.io/specification/2025-11-25/server/tools
