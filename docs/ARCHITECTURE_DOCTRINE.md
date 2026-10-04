# Architecture Doctrine v1.1

> Enforceable rules for the Illustrator MCP JSX runtime. Every PR touching `resources/scripts/` must comply.

## 1. ES3-Only JSX

All `.jsx` files under `resources/scripts/` must use **ES3 syntax only**. ExtendScript is ES3-based.

### Forbidden tokens (enforced by `scripts/es3_lint.ps1`):

| Token | Replacement |
|-------|-------------|
| `const` | `var` |
| `let` | `var` |
| `=>` (arrow function) | `function(x) { ... }` |
| `new Map()` | Plain object `{}` |
| `new Set()` | Plain object `{}` with boolean values |
| Template literals `` `${x}` `` | `"" + x` |
| `for...of` | `for (var i = 0; i < arr.length; i++)` |
| Destructuring `{a, b} = obj` | `var a = obj.a; var b = obj.b;` |

### Allowed polyfills (in `polyfills.jsx`):

`Array.prototype.indexOf`, `.forEach`, `.map`, `.filter`, `.every`, `.some`, `.reduce` — guarded by `if (!Array.prototype.X)`.

### JSDoc exception

`=>` in JSDoc `@param` descriptions (e.g., `(params, targets, ctx) => result`) is **allowed** — it's documentation, not code.

---

## 2. Heap Transaction Scoping

All ID index mutations must be **transaction-scoped** and reversible within a batch.

### Invariants

1. `heapBeginTxn(batchId)` before any op execution
2. `heapCommitTxn()` only on full batch success
3. `heapRollbackTxn()` on batch failure — discards all index changes from that batch
4. Created items that survive a failed batch must not persist in the index

### Prohibited patterns

- Direct mutation of `$.global.mcpIdIndex` outside heap APIs
- Setting `item.note` with `@mcp:id=` without calling `heapRegister`

---

## 3. ID Resolution Complexity

| Operation | Complexity | Notes |
|-----------|-----------|-------|
| `heapResolve(uuid)` | O(1) amortized | Index lookup + identity verify |
| `heapRebuildIndex(doc)` | O(N) | Full scan; at most **once per batch** |
| `heapRegister(uuid, ref)` | O(1) | Insert into index + txn record |
| `heapTombstone(uuid)` | O(1) | Mark for deletion |

**Per-op O(N) scans are prohibited.** If `heapResolve` fails after a rebuild, throw `ERR_ID_NOT_FOUND` — do not scan again.

---

## 4. Hybrid Identity

| Store | Role | When Set |
|-------|------|----------|
| `item.note` containing `@mcp:id=UUID` | **Canonical** — always authoritative, survives close/reopen | On item creation |
| `item.name = "mcp:" + UUID` | **Optional accelerator** — for faster `getByName` resync | On MCP-created items only |
| Native `PageItem.uuid` (AI 24+) | **Session handle** — O(1), no document mutation. Observed on AI 30.8.1: a per-document counter, not stored in the file; renumbered in document order on every load (deterministic for an unchanged file, shifted by edits), collides across open documents, and `getPageItemFromUuid()` resolves only in the active document | Always present; never written |

### Rules

- Never trust `item.name` alone — always verify via `extractMcpId(item.note)`
- `item.name` accelerator is opt-in and disabled by default
- Non-MCP items (user-created) must never have their name overwritten
- Resolve native uuids only through `resolvePageItemByUuid` (`mcp_id.jsx`): raw
  `getPageItemFromUuid()` throws on a miss and returns a `GroupItem` wrapper for
  compound paths; pass `app.activeDocument`, because the lookup ignores
  its receiver document
- Never persist a native uuid across a close/reopen or a document switch; use
  `@mcp:id` for that
- Paint (fill/stroke) goes through `paintTargetsOf` (`ops_core.jsx`): a
  `CompoundPathItem` accepts paint writes silently without applying them

---

## 5. Contract Compilation

Schemas are **compiled artifacts**, not hand-written.

| Source | Output | Mechanism |
|--------|--------|-----------|
| `schemas/contracts.py` (Pydantic) | `contracts.jsx` (ES3) | `tools/compile_contracts.py` |

### Invariants

- `contracts.jsx` includes a SHA-256 checksum header
- Runtime init compares checksums; mismatch → `ERR_VERSION_MISMATCH`
- Editing `contracts.jsx` by hand is prohibited — always re-compile

---

## 6. Typed-Tool Results

Tools built on `dm_script` / `run_dm` (`doc_model_tools.py`) return their result through
`__dmWire`: `dm1:` + JSON from our own serializer with `%`, `\` and `"` percent-encoded.
Keep this wire format for compatibility with older installed panels/hosts. The repository's
`host.jsx` now installs an ES3 codec (`mcpJsonStringify` / `mcpJsonParse`) for both host
envelopes and injected scripts, independent of Illustrator's partial native `JSON`.
Python still decodes with `decode_dm_wire` / `unwrap_dm_response`.
`tests/test_dm_json_escaping.py` pins this against the real host.jsx, including partial native JSON.

Inspection results carry `document.session_id`, an opaque identity held in `$.global` for the
open Document. Pass it as optional `document_session_id` to typed operations or UUID target
selectors. A mismatch refuses the operation before that operation's writes. In a SOC batch,
earlier operations may already have changed the document; this guard does not roll them back.

---

## 7. Go/No-Go PR Checklist

Before merging any PR touching `resources/scripts/`:

- [ ] `scripts/es3_lint.ps1` passes (zero forbidden tokens in executable code)
- [ ] `node tests_jsx/run_tests.js` passes
- [ ] `python -m pytest tests/ -q` passes
- [ ] `contracts.jsx` matches compiled output (if schemas changed)
- [ ] No direct `$.global.mcpIdIndex` mutation outside `heap.jsx`
- [ ] No `app.selection` in `ops_*.jsx` files
