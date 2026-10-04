# Study: how successful app-automation MCP servers are built

Historical snapshot: the `isError`, output-wrapper and server-instructions gaps below were fixed
in `4620b6a` and `89c0d86`. See [MCP_AUTOMATION_PRACTICES.md](MCP_AUTOMATION_PRACTICES.md) for the
expanded, commit-pinned study and the current implementation baseline.

Status: research note, 2026-10-03. Companion to `ADOBE_MCPTOOLKIT_STUDY.md`. Sources are listed at
the end; everything marked "measured" was run against this repository on 2026-10-03, everything
else is what the cited source states.

## Servers studied

| Server | Shape | What it teaches |
|--------|-------|-----------------|
| Figma MCP (`use_figma`, since 2026-03) | One raw Plugin-API JS tool for writes, typed read tools (`get_metadata`, `get_screenshot`, ...), plus **skills** the agent must load before writing | Raw script is viable when a skill teaches the API's traps; results must return every touched id; retries are governed by an explicit flag |
| Blender MCP (ahujasid, ~30k stars) | Addon socket server + a few typed tools + `execute_blender_code` | Raw code carries most real work; viewport screenshots close the loop; unsandboxed exec is the main criticism (issue #207) |
| Unity MCP (CoplayDev, ~15k stars) | 47 tools, `manage_*` consolidated tools with an `action` param, `batch_execute`, toggleable tool groups, Roslyn validation of scripts, multi-editor routing | Consolidation by resource, opt-in tool groups, validate code before running it |
| Playwright MCP (Microsoft) | Accessibility-snapshot refs instead of pixels; optional groups via `--caps`; every action returns a fresh snapshot | Return the new state with the action result; opt-in capabilities; README itself says CLI + skills is cheaper than MCP for throughput work |
| Chrome DevTools MCP (Google) | Full and `--slim` tool sets; trace *summaries* instead of raw traces; also a CLI and skills | Summarize heavy data server-side; ship a slim profile |
| GitHub MCP | 20+ toolsets, a small default set, `--tools` allow-list, read-only mode | Default to a small surface; enabling fewer tools "helps tool choice and reduces context" |
| adb-mcp (Mike Chambers) | MCP server -> Node proxy -> UXP/CEP plugin; typed tools for Photoshop/Premiere, ExtendScript passthrough for Illustrator/After Effects; instructions via `config://get_instructions` | Same proxy topology as ours; instructions delivered as a resource |
| Adobe for creativity (official, remote, since 2026-04) | 50+ cloud tools across 8 apps | Cloud document operations, not live desktop control; no overlap with a local Illustrator session |
| Adobe MCPToolkit (in-app, Illustrator 30.8) | 87 typed tools, raw JS internal-only | See `ADOBE_MCPTOOLKIT_STUDY.md` |
| Cloudflare Code Mode | Two tools, `search()` over the spec and `execute()` of code, ~1k tokens for 2,500+ endpoints | The extreme of "code over tool calls" |

## Patterns, and where we stand

### 1. Typed core plus a raw-script escape hatch is the winning shape

Every successful desktop-app server ends up hybrid: typed tools for frequent, trap-prone operations;
raw code for the long tail. Figma went the other way round (raw-first) and made it work by making a
skill mandatory before the first write: the skill lists the API's traps (await every font load, color
range 0-1, append before setting sizing, one page switch per call) and a progressive reference
(`index.md` first, then grep a `.d.ts`).

**Us:** the hybrid is right. Gap: the ExtendScript traps we discovered live (per-character
`textRanges`, threaded-frame indexing, missing-font placeholders, `getPageItemFromUuid` ignoring its
receiver, no `JSON.parse`) live in a study note and in tool code, not in a reference the agent loads
before writing raw script. The resources `illustrator://reference/extendscript` and
`illustrator://reference/libraries` exist; they should carry these traps, and the tool descriptions
should point at them instead of repeating prose.

### 2. Small default surface, opt-in groups

GitHub (toolsets, small default), Playwright (`--caps`), Unity (tool groups), Chrome DevTools
(`--slim`) all ship a small default and let the user add groups. Anthropic's measurements: tool
search pays off once definitions exceed ~10k tokens; keep the 3-5 most used tools always loaded.

**Us (measured):** 18 tools, 86,910 characters of descriptions and schemas (~22k tokens);
`execute_task` alone is 15,269 characters and `execute_script` 10,202. No grouping. Claude Code
defers MCP tools behind tool search, so there the cost is paid per loaded tool; clients without tool
search (Codex, ChatGPT) pay all of it on every turn.

### 3. Return the new state and every id with the action result

Playwright returns a fresh snapshot after each action. Figma requires every write to return the ids
it created or changed, because nothing persists between calls.

**Us:** typed tools return uuids and the batch contract. `execute_task` results are not uniform on
this; worth checking against the same contract.

### 4. Tell the agent whether a failed call is safe to retry

Figma's `use_figma` failure carries `safeToRetryWithoutCanvasRead`: `true` means fix and retry,
`false` means read the canvas first because something may already have changed.

**Us:** ExtendScript is not transactional, and a script that throws halfway leaves partial state.
We have checkpoints and undo, but the failure does not tell the agent which case it is in. A
`safe_to_retry` field (true for validation and connection failures before anything ran, false after
the script started mutating) is cheap and directly prevents duplicated objects on retry.

### 5. Signal execution errors with `isError`

The MCP spec (2025-11-25) separates protocol errors from tool execution errors and says execution
errors belong in a result with `isError: true` so the model can self-correct.

**Us (measured):** an in-process call to `illustrator_inspect` with Illustrator disconnected returned
`{"ok": false, "error": {"code": "C001", ...}}` with `isError: false`. `structuredContent` was
`{"result": "<the same JSON as a string>"}`: the SDK's automatic wrapper for a `str` return, which
adds a second copy of the payload and no structure. Clients and any eval that counts failures see
every failure as a success.

### 6. Server instructions and examples beat longer descriptions

Figma and adb-mcp deliver usage rules as instructions or a resource the agent reads first. Anthropic
measured tool-use examples raising accuracy on complex parameters from 72% to 90%, and recommends 1-5
compact examples per complex tool.

**Us:** `FastMCP("illustrator_mcp", lifespan=...)` sends no `instructions`. Tool routing (typed tool vs
`execute_task` SOC op vs `execute_script`) is therefore left to each description. `execute_task` has
the most nested schema and would benefit most from examples replacing prose.

### 7. Close the loop visually

Figma asks for one screenshot after the build and one after a fix; Blender exposes the viewport.

**Us:** ahead here: annotated previews, object grounding, assertions, checkpoints.

### 8. Validate code before running it

Unity compiles C# with Roslyn before executing. Blender's unrestricted `exec` is its most cited flaw.

**Us:** the bridge is authenticated and `execute_script` is annotated `destructiveHint`/`openWorldHint`.
A syntax pre-check of generated ExtendScript would turn a class of `E999` round trips into immediate,
specific errors; worth measuring how often `E999` occurs in request logs first.

## Not adopting

- Cloud asset and generative integrations (Poly Haven, Firefly, Sketchfab): out of scope for live
  document control.
- Code Mode's sandboxed runtime: our "code" already runs inside Illustrator; the transferable part is
  a compact, greppable API reference, covered by pattern 1.
- One tool per operation: every large server consolidates by resource with an `action` parameter,
  which is what our typed tools already do.

## Recommended order

Merges this study with the open items from the hybrid-toolset review.

| # | Change | Why first | Risk |
|---|--------|-----------|------|
| 1 | `isError: true` on `ok: false`; drop the string-wrapped `structuredContent` | Smallest change, fixes failure signalling for every tool and makes evals possible | Clients that branch on `isError` will start seeing failures they ignored before |
| 2 | Own serializer in `host.jsx` | Fixes quote/backslash corruption on all legacy tools | Needs a panel rebuild and reload |
| 3 | Server `instructions` with tool routing; traps moved into the reference resources | Addresses tool overlap without adding tools | None functional |
| 4 | `safe_to_retry` on failures | Prevents duplicate objects on retry | Must be correct per failure point, needs live tests |
| 5 | Tool groups (core + optional), examples instead of prose in `execute_task` | Cuts the ~22k-token footprint for clients without tool search | Changing defaults hides tools from existing users; keep `all` available |
| 6 | Live tests for uuid document switch and reopen | Proves the guard that unit tests only mock | Live Illustrator time |
| 7 | A/B eval (raw vs typed) with Anthropic's metrics: success, tool calls, tokens, errors | Turns "quality improved" into a number | Live Illustrator time |

## Sources

- Anthropic, Writing effective tools for AI agents: https://www.anthropic.com/engineering/writing-tools-for-agents
- Anthropic, Code execution with MCP: https://www.anthropic.com/engineering/code-execution-with-mcp
- Anthropic, Advanced tool use: https://www.anthropic.com/engineering/advanced-tool-use
- MCP specification 2025-11-25, Tools: https://modelcontextprotocol.io/specification/2025-11-25/server/tools
- Figma `figma-use` skill: https://github.com/figma/mcp-server-guide/blob/main/skills/figma-use/SKILL.md
- Blender MCP: https://github.com/ahujasid/blender-mcp and issue #207
- Unity MCP: https://github.com/CoplayDev/unity-mcp
- Playwright MCP: https://github.com/microsoft/playwright-mcp
- Chrome DevTools MCP: https://github.com/ChromeDevTools/chrome-devtools-mcp
- GitHub MCP server: https://github.com/github/github-mcp-server
- adb-mcp: https://github.com/mikechambers/adb-mcp
- Adobe for creativity MCP: https://mcpservers.org/servers/adobe-creativity-mcp
- Cloudflare Code Mode: https://blog.cloudflare.com/code-mode-mcp/
