"""Live harness: every illustrator_* tool against a real Illustrator, no CEP panel needed.

The panel is replaced by osascript: a FakeBridge runs the exact script the server
built (library injection, envelopes, formatting all real) through the repo's
cep-extension/jsx/host.jsx `mcp_handle_request`, and replies the way
useMCP.ts does. Only the WebSocket hop is simulated.

Run:  .venv/bin/python tests_live/live_suite.py     (macOS, Illustrator running, no foreign documents open)
Env:  HARNESS_REPO=<checkout to test>, HARNESS_OUT=<output dir for exported files and results>

Safety (shared Illustrator): works only in documents it creates itself,
DONTDISPLAYALERTS around every call, closes its documents with DONOTSAVECHANGES,
no actions/loadAction, per-call timeout.
"""
import asyncio, json, os, subprocess, sys, time, traceback, pathlib

REPO = os.environ.get("HARNESS_REPO") or str(pathlib.Path(__file__).resolve().parent.parent)
sys.path.insert(0, REPO)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tempfile
SP = pathlib.Path(os.environ.get("HARNESS_OUT") or tempfile.mkdtemp(prefix="illustrator-mcp-live-"))
WORK = SP / "live"
WORK.mkdir(parents=True, exist_ok=True)
HOST = pathlib.Path(REPO, "cep-extension/jsx/host.jsx").read_text(encoding="utf-8")

import logging
logging.disable(logging.CRITICAL)
from illustrator_mcp import server  # noqa: F401  (registers tools)
from illustrator_mcp.shared import mcp
import illustrator_mcp.proxy_client as pc

_call_no = 0


def run_osa(script_literal_request: dict, timeout: float) -> str:
    global _call_no
    _call_no += 1
    lit = json.dumps(script_literal_request, ensure_ascii=True)
    src = (HOST + "\n(function(){ var __ui = app.userInteractionLevel;"
           " app.userInteractionLevel = UserInteractionLevel.DONTDISPLAYALERTS;"
           " try { return mcp_handle_request(" + lit + "); }"
           " finally { app.userInteractionLevel = __ui; } })()\n")
    f = WORK / f"call_{_call_no % 5}.jsx"
    f.write_text(src, encoding="utf-8")
    r = subprocess.run(
        ["osascript", "-e", f'tell application id "com.adobe.illustrator" to do javascript (POSIX file "{f}")'],
        capture_output=True, text=True, timeout=timeout)
    if r.returncode:
        raise RuntimeError("osascript: " + r.stderr.strip()[:500])
    return r.stdout.rstrip("\n")


class FakeBridge:
    port = 8081
    loop = None

    def is_connected(self):
        return True

    def get_panel_health(self):
        return {"busy": False, "heartbeat_seen": True, "stale": False}

    async def execute_script_async(self, script, timeout=30.0, command=None, trace_id=None):
        t0 = time.time()
        req = {"id": 1, "script": script}
        if command:
            req["command"] = command.to_dict() if hasattr(command, "to_dict") else command
        loop = asyncio.get_running_loop()
        try:
            text = await asyncio.wait_for(loop.run_in_executor(None, run_osa, req, timeout), timeout + 5)
        except Exception as e:  # timeout / osascript failure
            return {"error": f"R001 harness transport failure: {e}"}
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = {"result": text}
        res = parsed.get("result") if isinstance(parsed, dict) and parsed.get("result") is not None else parsed
        err = parsed.get("error") if isinstance(parsed, dict) else None
        out = {"id": 1, "type": "complete", "command": getattr(command, "command_type", "script"),
               "result": res, "duration": int((time.time() - t0) * 1000)}
        if err:
            out["error"] = err
        return out


_bridge = FakeBridge()
pc._get_bridge = lambda: _bridge


async def T(_tool, **params):
    """Call a tool through FastMCP (validation + formatting real). Returns (envelope, extras)."""
    _bridge.loop = asyncio.get_running_loop()
    res = await mcp.call_tool(_tool, {"params": params})
    content = res[0] if isinstance(res, tuple) else res
    texts = [c.text for c in content if getattr(c, "type", "") == "text"]
    images = [c for c in content if getattr(c, "type", "") == "image"]
    raw = texts[0] if texts else ""
    try:
        env = json.loads(raw)
    except ValueError:
        env = {"ok": None, "raw": raw}
    if isinstance(env, dict) and env.get("type") == "text" and isinstance(env.get("text"), str):
        try:  # tools that also return an image wrap their envelope once more
            env = json.loads(env["text"])
        except ValueError:
            pass
    if isinstance(env, dict) and isinstance(env.get("result"), str):
        try:
            inner = json.loads(env["result"])
            env["result"] = inner
        except ValueError:
            pass
    return env, images


RESULTS = []


def record(tool, step, ok, detail=""):
    RESULTS.append((tool, step, bool(ok), str(detail)[:300]))
    print(("PASS " if ok else "FAIL ") + f"{tool:28s} {step:44s} {str(detail)[:160] if not ok else ''}", flush=True)


async def step(tool, name, coro_fn):
    try:
        r = await coro_fn()
        if r is True or r is None:
            record(tool, name, True)
        elif r is False:
            record(tool, name, False, "assertion false")
        else:
            record(tool, name, False, r)
    except Exception as e:
        record(tool, name, False, f"{type(e).__name__}: {e}")
        if os.environ.get("HARNESS_TRACE"):
            traceback.print_exc()


def ok(env):
    return env.get("ok") is True


def res_of(env):
    r = env.get("result")
    if isinstance(r, dict) and "data" in r and "ok" in r:
        return r["data"]
    return r
