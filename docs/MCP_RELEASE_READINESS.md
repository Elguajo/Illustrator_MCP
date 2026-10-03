# MCP 3.1.0rc1 / CEP 1.1.0 release evidence

Decision: **SHIP WITH KNOWN RISKS for the tested macOS / Illustrator 30.8.1
configuration**. This is a release candidate, not a certification for every host
version or platform in the extension manifest. No public release was published.

## Release-blocking defects fixed

- The built Python wheel omitted runtime data. Explicit setuptools package data
  now ships all 51 JSX/template/manifest/reference/schema resources. The wheel
  gate compares each resource byte-for-byte with its source and runs in CI.
- On macOS, `evalScript` blocks the CEP JavaScript event loop. A second request
  sent after timeout queued and executed later instead of receiving a busy error.
  The bridge now rejects work while a request is pending or the panel is busy,
  before sending it, for both regular and streaming execution.

## Observed verification (2026-10-03)

| Check | Result | Evidence |
|---|---|---|
| Python regression suite, Python 3.12.10 | 2032 passed, 25 skipped, 26 warnings | `build/releases/3.1.0rc1/python-tests.log` |
| Pure JSX regression suite | 59 passed | `build/releases/3.1.0rc1/jsx-tests.log` |
| CEP hook recovery tests | Passed | `node tests_jsx/test_panel_execution.js` |
| TypeScript strict check (CI arguments) | Passed | Local `tsc --noEmit` |
| Vite production build | Passed | `build/releases/3.1.0rc1/cep-signing.log` |
| Contract compiler checksum | Matches `85bb1b243d2ec4a5` | `python -m illustrator_mcp.tools.compile_contracts --check` |
| ES3 lint | All seven gate regexes passed, 43 files | Python-equivalent scan; native PowerShell unavailable |
| Wheel runtime resource gate | All 51 resources match | `python scripts/check_wheel.py <wheel>` |
| Installed wheel outside source checkout | Passed | `/tmp/illustrator-mcp-wheel-env`, child cwd outside repository |
| Signed ZXP, valid timestamp and installed directory signature | Passed | Adobe ZXPSignCmd 4.1.3, `cep-signing.log` |
| Installation through Adobe UPIA | Successful; installed files match package | `cep-install.log` |
| Real MCP -> WebSocket -> installed CEP -> Illustrator | All ten checks passed | `live-results.json`, `live-server.log` |
| Signed package loading with CSXS.12 PlayerDebugMode=0 | Read-only DOM smoke passed | `no-debug-results.json`, `no-debug-server.log` |
| Diff whitespace | Passed | `git diff --check` |

The full live check verifies stable session identity across independent calls,
typed text mutation with DOM read-back, hostile JSON strings/keys, save/reopen
with a new session, stale typed and UUID target rejection, real timeout with
late callback, socket loss and automatic reconnect. DOM counts were exactly one
mutation for each original timed-out request and zero for rejected requests.
The reconnect-lost result stayed `unknown`; the test read the document rather
than replaying the uncertain request. All fixture documents were closed.

## Artifacts

Local release directory: `build/releases/3.1.0rc1/` (ignored by Git).

| Artifact | SHA-256 |
|---|---|
| `illustrator_mcp-3.1.0rc1-py3-none-any.whl` | `5a2f9bb07eb06101d84f0c1d6dc9a49c3e7e91f32c03a00ffc8b3b48fcf306d7` |
| `com.illustrator.mcp.panel.zxp` | `ca6cc1643907cee3d06b5a587b5332976719bea58ea4c9ac509c5f7d51e7e687` |

The installed panel is `/Library/Application Support/Adobe/CEP/extensions/Illustrator MCP`.
Its loaded CEP process used that directory, not the development checkout.
The former user-level development symlink is preserved as
`build/releases/3.1.0rc1/development-panel-link.backup`; leaving it in the extensions
directory would shadow the packaged installation. CSXS.12 debug preference was
temporarily set to zero for verification and restored to its original value (1).
No dependency versions changed; the npm lockfile only tracks the panel version.

## Reproduce

Open Window -> Extensions -> MCP Control in Illustrator. Install the wheel in
a separate environment (dependencies can be shared with the existing environment):

```bash
python -m venv --system-site-packages /tmp/illustrator-mcp-wheel-env
/tmp/illustrator-mcp-wheel-env/bin/python -m pip install --no-deps --force-reinstall \
  build/releases/3.1.0rc1/illustrator_mcp-3.1.0rc1-py3-none-any.whl
/tmp/illustrator-mcp-wheel-env/bin/python tests_live/live_cep_contract.py \
  --out /tmp/illustrator-cep-release-check
```

The child MCP server runs outside the checkout and reads resources from the
installed wheel. Its private test port is 18081. The harness temporarily publishes
its own handshake, then restores the prior handshake without printing secrets.
`--smoke-only` checks package loading without creating a document.

## Remaining scope limits

- Windows, other Illustrator releases and the remote Python 3.10/3.11 CI matrix
  were not exercised in this local release run. Remote CI remains unobserved.
- The certificate is self-signed, not an OS-trusted publisher identity; CEP
  nevertheless loaded the installed signed package with debug mode disabled.
- The full live suite intentionally covers the changed contracts, not every
  operation exposed by all 18 tools or a long unattended production workload.
- Session guards are optional for compatibility. Unscoped legacy requests lack
  reopen protection. Raw scripts still have full ExtendScript capabilities.
- Timeout does not cancel or roll back work. Restarting/reloading a CEP panel
  during execution is outside the tested same-instance reconnect contract.
- The MCP JSON codec serializes data; replacer/indent and Date/toJSON behavior
  are not implemented. Existing warnings and skipped tests are not represented
  as passing checks.

Restart an already-running MCP client/server to load this candidate's Python
code. A panel install does not reload the Python process.
