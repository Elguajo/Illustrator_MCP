"""Opt-in real MCP stdio -> WebSocket -> installed CEP -> Illustrator checks.

Open MCP Control in Illustrator first. Run with the Python/environment to test:
  .venv/bin/python tests_live/live_cep_contract.py --out /tmp/cep-contract

Creates only its own documents, uses a private test port, and restores the previous
handshake file. The --server entry wraps the normal MCP lifespan only to let this
test close its own socket; no test control endpoint is added to production.
"""

import argparse
import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
import json
import os
from pathlib import Path
import secrets
import sys
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO = Path(__file__).resolve().parent.parent


def serve(control: Path):
    from illustrator_mcp.server import mcp
    from illustrator_mcp.app import server_lifespan
    from illustrator_mcp.runtime import get_runtime

    @asynccontextmanager
    async def lifespan(server):
        async with server_lifespan(server) as context:
            async def watch():
                while True:
                    if control.exists():
                        control.unlink()
                        bridge = get_runtime().get_bridge()
                        async def close():
                            client = bridge.server.client
                            if client:
                                await client.close(1001, "release recovery check")
                        future = asyncio.run_coroutine_threadsafe(close(), bridge.loop)
                        await asyncio.wrap_future(future)
                    await asyncio.sleep(0.2)
            task = asyncio.create_task(watch())
            try:
                yield context
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    # FastMCP captures settings.lifespan at construction; replace the test
    # server's actual low-level lifespan, not the already-captured setting.
    mcp._mcp_server.lifespan = lambda server: lifespan(mcp)
    mcp.run()


async def run(out: Path, port: int, smoke_only: bool = False):
    out.mkdir(parents=True, exist_ok=True)
    control = out / "disconnect"
    control.unlink(missing_ok=True)
    handshake = Path.home() / ".illustrator-mcp/session.json"
    previous = handshake.read_bytes() if handshake.exists() else None
    own_pid = None
    fixture_run = secrets.token_hex(8)
    checks, calls = [], []
    outcome = {"status": "failed", "checks": checks, "calls": calls}

    def passed(label):
        checks.append(label)
        print("PASS " + label, flush=True)

    env = {**os.environ, "WS_PORT": str(port), "TIMEOUT": "30"}
    env.pop("PYTHONPATH", None)  # Verify installed package when run from a wheel env.
    params = StdioServerParameters(command=sys.executable,
        args=[str(Path(__file__).resolve()), "--server", str(control)], env=env,
        cwd=str(out))
    try:
        with (out / "server.log").open("w") as log:
            async with stdio_client(params, errlog=log) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=60)) as client:
                    init = await client.initialize()
                    outcome["server"] = init.serverInfo.model_dump()
                    tools = await client.list_tools()
                    assert len(tools.tools) == 18, len(tools.tools)
                    assert init.instructions and "document_session_id" in init.instructions
                    assert all(tool.outputSchema is None for tool in tools.tools)
                    passed("MCP initialize: instructions, 18 tools, no duplicate output schema")
                    reference = await client.read_resource("illustrator://reference/extendscript")
                    assert reference.contents
                    passed("installed ExtendScript reference resource")
                    own_pid = json.loads(handshake.read_text())["pid"]

                    async def call(name, **arguments):
                        response = await client.call_tool(name, {"params": arguments})
                        blocks = [x for x in response.content if x.type == "text"]
                        data = json.loads(blocks[0].text)
                        if data.get("type") == "text":
                            data = json.loads(data["text"])
                        assert response.structuredContent is None
                        assert bool(response.isError) == (data.get("ok") is False), data
                        calls.append({"tool": name, "response": data})
                        return data

                    async def raw(script, timeout=30):
                        # Fixture calls must not wait for aeInteractWithUser when
                        # Illustrator is in the background (including cleanup).
                        silent = '''(function(){var oldUI=app.userInteractionLevel;
                            app.userInteractionLevel=UserInteractionLevel.DONTDISPLAYALERTS;
                            try{return eval(''' + json.dumps(script, ensure_ascii=True) + ''');}
                            finally{app.userInteractionLevel=oldUI;}})()'''
                        return await call("illustrator_execute_script", script=silent,
                            timeout=timeout, auto_assign_ids="off", return_preview=False,
                            description="Release contract fixture")

                    async def status(request_id=None):
                        args = {"view": "execution"}
                        if request_id is not None:
                            args["request_id"] = request_id
                        result = await call("illustrator_inspect", **args)
                        assert result["ok"], result
                        return result["result"]

                    async def wait_status(predicate, request_id=None, seconds=60):
                        deadline = time.monotonic() + seconds
                        while time.monotonic() < deadline:
                            current = await status(request_id)
                            if predicate(current):
                                return current
                            await asyncio.sleep(1)
                        raise AssertionError("CEP state did not converge: " + json.dumps(current))

                    await wait_status(lambda x: x["connected"] and x["panel_health"]["heartbeat_seen"], seconds=120)
                    passed("authenticated CEP connection and real heartbeat")
                    if smoke_only:
                        app = await call("illustrator_get_document", scope="app")
                        assert app["ok"], app
                        outcome["illustrator"] = app["result"]
                        passed("read-only Illustrator DOM through installed CEP host")
                        outcome["status"] = "passed"
                        return
                    setup = '''(function () {
                        if ($.global.__cepContractOwned) throw new Error("Existing fixture: clean it before rerunning");
                        $.global.__cepContractRunId = ''' + json.dumps(fixture_run) + ''';
                        $.global.__cepContractOriginal = app.documents.length ? app.activeDocument : null;
                        $.global.__cepContractOwned = [];
                        var oldUI = app.userInteractionLevel;
                        app.userInteractionLevel = UserInteractionLevel.DONTDISPLAYALERTS;
                        try {
                            var d = app.documents.add(DocumentColorSpace.RGB, 300, 200);
                            $.global.__cepContractOwned.push(d);
                            var t = d.textFrames.add(); t.contents = "old contract text";
                            t.position = [20, 160];
                            return {name:d.name, uuid:String(t.uuid), version:app.version};
                        } finally { app.userInteractionLevel = oldUI; }
                    })()'''
                    cleanup_needed = False
                    try:
                        cleanup_needed = True  # Includes an uncertain setup timeout.
                        made = await raw(setup)
                        assert made["ok"], made
                        outcome["illustrator"] = made["result"]
                        first = await call("illustrator_inspect", view="structure")
                        assert first["ok"], first
                        doc = first["result"]["document"]
                        session = doc["session_id"]
                        again = await call("illustrator_inspect", view="selection")
                        assert again["result"]["document"]["session_id"] == session
                        passed("document session stable across independent CEP invocations")
                        changed = await call("illustrator_text", action="replace", find="old", replace="new", document_session_id=session)
                        assert changed["ok"] and changed["result"]["replaced_count"] == 1, changed
                        assert changed["diagnostics"]["changes"]["verification"]["method"] == "dom_read_back"
                        passed("typed text mutation and DOM read-back")
                        hostile = '"quote" \\ \r\t\b\f\u0003\u2028\u2029 %41 ж 😀'
                        obj = {hostile: hostile}
                        codec = await raw("(" + json.dumps(obj, ensure_ascii=True) + ")")
                        assert codec["ok"] and codec["result"] == obj, codec
                        passed("host JSON controls, Unicode, surrogate pairs and object keys")
                        path = json.dumps(str(out / "contract.ai"))
                        reopened = await raw('''(function () {
                            var d = $.global.__cepContractOwned[0];
                            var oldUI = app.userInteractionLevel;
                            app.userInteractionLevel = UserInteractionLevel.DONTDISPLAYALERTS;
                            try {
                                d.saveAs(new File(''' + path + '''));
                                d.close(SaveOptions.DONOTSAVECHANGES);
                                var r = app.open(new File(''' + path + '''));
                                $.global.__cepContractOwned[0] = r;
                                return {name:r.name, uuid:String(r.textFrames[0].uuid)};
                            } finally { app.userInteractionLevel = oldUI; }
                        })()''')
                        assert reopened["ok"], reopened
                        fresh = await call("illustrator_inspect", view="structure")
                        assert fresh["result"]["document"]["session_id"] != session, fresh
                        stale = await call("illustrator_text", action="replace", find="new", replace="WRONG", document_session_id=session)
                        assert not stale["ok"] and stale["error"]["safe_to_retry"], stale
                        bad = await call("illustrator_execute_task", payload={"task":"element_move",
                            "targets":{"type":"uuid", "document":reopened["result"]["name"],
                                "uuids":[reopened["result"]["uuid"]], "document_session_id":session},
                            "params":{"dx":10,"dy":10}}, return_preview=False)
                        assert not bad["ok"] and "session mismatch" in json.dumps(bad).lower(), bad
                        unchanged = await raw("String($.global.__cepContractOwned[0].textFrames[0].contents)")
                        assert unchanged["result"] == "new contract text", unchanged
                        passed("save/reopen changes session; stale typed and UUID writes rejected")

                        def delayed(tag, ms):
                            return '''(function(){var d=$.global.__cepContractOwned[0];
                                if(app.activeDocument!==d) throw new Error("Fixture is not active");
                                $.sleep(''' + str(ms) + ''');
                                var p=d.pathItems.rectangle(100,20,10,10);p.name=''' + json.dumps(tag) + ''';
                                return {uuid:String(p.uuid)};})()'''

                        async def rejected_probe():
                            refused = await raw('''(function(){var d=$.global.__cepContractOwned[0];
                                var p=d.pathItems.rectangle(100,40,10,10);p.name="must-not-run";return true;})()''', timeout=2)
                            assert not refused["ok"] and refused["error"]["safe_to_retry"], refused
                            assert refused["diagnostics"]["execution"]["state"] == "not_started", refused

                        timeout = await raw(delayed("timeout-once", 8000), timeout=1)
                        assert not timeout["ok"] and timeout["error"]["safe_to_retry"] is False, timeout
                        request = timeout["diagnostics"]["execution"]["request_id"]
                        current = await status(request)
                        assert current["execution"]["state"] == "running", current
                        await rejected_probe()
                        await wait_status(lambda x: x["execution"]["state"] == "completed", request)
                        passed("real timeout: unsafe replay, busy rejection, late completed callback")

                        timeout = await raw(delayed("reconnect-once", 14000), timeout=1)
                        assert not timeout["ok"] and timeout["error"]["safe_to_retry"] is False, timeout
                        request = timeout["diagnostics"]["execution"]["request_id"]
                        control.write_text("close own test socket\n")
                        deadline = time.monotonic() + 10
                        while control.exists() and time.monotonic() < deadline:
                            await asyncio.sleep(0.2)
                        assert not control.exists(), "test server did not close socket"
                        # On macOS evalScript can block the panel event loop, so
                        # reconnect may happen only after it returns. In either
                        # case the second mutation must never reach Illustrator.
                        await rejected_probe()
                        settled = await wait_status(lambda x: x["connected"] and not x["panel_health"]["busy"], request)
                        assert settled["execution"]["state"] == "unknown", settled
                        counts = await raw('''(function(){var d=$.global.__cepContractOwned[0],a=0,b=0,c=0;
                            for(var i=0;i<d.pathItems.length;i++){__mcp_check();var n=d.pathItems[i].name;
                                if(n==="timeout-once")a++;if(n==="reconnect-once")b++;if(n==="must-not-run")c++;}
                            return {timeout:a,reconnect:b,rejected:c};})()''')
                        assert counts["result"] == {"timeout":1,"reconnect":1,"rejected":0}, counts
                        passed("disconnect/reconnect: rejected replay, unknown completion, no duplicate writes")
                    finally:
                        if cleanup_needed:
                            await wait_status(lambda x: x["connected"] and not x["panel_health"]["busy"])
                            cleanup = await raw('''(function(){
                                if($.global.__cepContractRunId!==''' + json.dumps(fixture_run) + ''')return true;
                                var a=$.global.__cepContractOwned||[];
                                for(var i=a.length-1;i>=0;i--){__mcp_check();try{a[i].close(SaveOptions.DONOTSAVECHANGES);}catch(e){}}
                                try{if($.global.__cepContractOriginal)$.global.__cepContractOriginal.activate();}catch(e){}
                                delete $.global.__cepContractOwned;delete $.global.__cepContractOriginal;
                                delete $.global.__cepContractRunId;return true;})()''')
                            assert cleanup["ok"], cleanup
                            passed("own fixtures closed; original active document restored")
                    outcome["status"] = "passed"
    except Exception as exc:
        outcome["error"] = str(exc)
        raise
    finally:
        # Never overwrite a handshake published meanwhile by another server.
        current = json.loads(handshake.read_text()) if handshake.exists() else None
        if previous is not None and (current is None or current.get("pid") == own_pid):
            fd = os.open(handshake, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(previous)
        (out / "results.json").write_text(json.dumps(outcome, ensure_ascii=False, indent=2))
        print("Evidence: " + str(out / "results.json"), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", type=Path)
    parser.add_argument("--out", type=Path, default=Path("/tmp/illustrator-cep-contract"))
    parser.add_argument("--port", type=int, default=18081)
    parser.add_argument("--smoke-only", action="store_true", help="read-only package loading check")
    args = parser.parse_args()
    if args.server:
        serve(args.server)
    else:
        asyncio.run(run(args.out.resolve(), args.port, args.smoke_only))
