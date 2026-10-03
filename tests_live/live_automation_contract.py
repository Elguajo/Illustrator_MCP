"""Real Illustrator host/DOM checks. Creates and cleans only its own documents.

The existing live_harness replaces the WebSocket hop with osascript; transport
recovery is covered separately in tests/test_automation_contract.py.
Run: .venv/bin/python tests_live/live_automation_contract.py
"""

import asyncio
import json

from live_harness import T, run_osa, SP


async def main():
    saved_path = str(SP / "automation-contract.ai")
    # Remember the original document; cleanup must never enumerate/close user docs.
    setup = '''(function () {
        $.global.__automationOriginal = app.documents.length ? app.activeDocument : null;
        $.global.__automationOwned = [];
        var doc = app.documents.add(DocumentColorSpace.RGB, 300, 200);
        $.global.__automationOwned.push(doc);
        var tf = doc.textFrames.add(); tf.contents = "old contract text";
        tf.position = [20, -40];
        return {name: doc.name, uuid: String(tf.uuid), version: app.version};
    })()'''
    checks = []
    try:
        created = json.loads(run_osa({"script": setup}, 30))
        assert created["ok"], created
        print("Illustrator " + created["data"]["version"], flush=True)
        first, _ = await T("illustrator_inspect", view="structure")
        assert first["ok"], first
        document = first["result"]["document"]
        session = document["session_id"]
        second, _ = await T("illustrator_inspect", view="selection")
        assert second["result"]["document"]["session_id"] == session
        checks.append("session stable across calls")
        changed, _ = await T("illustrator_text", action="replace", find="old", replace="new",
                             document_session_id=session)
        assert changed["ok"] and changed["result"]["replaced_count"] == 1, changed
        assert changed["diagnostics"]["changes"]["verification"]["method"] == "dom_read_back"
        checks.append("typed mutation and explicit DOM verification")
        # Check hostile JSON strings/keys and envelope passthrough in the real host.
        text = '"quote" \\ \r\t\b\f\u0003\u2028\u2029 %41 ж 😀'
        codec = json.loads(run_osa({"script": 'JSON.stringify({ok:true,data:{' + json.dumps(text) + ':' + json.dumps(text) + '}})'}, 30))
        assert codec == {"ok": True, "data": {text: text}}, codec
        checks.append("host JSON: controls, backslashes, keys and Unicode")
        saved = json.loads(run_osa({"script": '''(function () {
            var d = $.global.__automationOwned[0];
            d.saveAs(new File(''' + json.dumps(saved_path) + '''));
            d.close(SaveOptions.DONOTSAVECHANGES);
            var reopened = app.open(new File(''' + json.dumps(saved_path) + '''));
            $.global.__automationOwned = [reopened];
            return {name: reopened.name, uuid: String(reopened.textFrames[0].uuid)};
        })()'''}, 30))
        assert saved["ok"], saved
        reopened, _ = await T("illustrator_inspect", view="structure")
        assert reopened["ok"] and reopened["result"]["document"]["session_id"] != session, reopened
        stale, _ = await T("illustrator_text", action="replace", find="new", replace="WRONG",
                           document_session_id=session)
        assert stale["ok"] is False and stale["error"]["safe_to_retry"] is True, stale
        after = json.loads(run_osa({"script": "String($.global.__automationOwned[0].textFrames[0].contents)"}, 30))
        assert after["data"] == "new contract text", after
        checks.append("reopen rejects stale session before mutation")
        # Exercise UUID guard through actual SOC collection, including legacy name scope.
        bad, _ = await T("illustrator_execute_task", payload={
            "task": "element_move", "targets": {"type": "uuid", "document": saved["data"]["name"],
                "uuids": [saved["data"]["uuid"]], "document_session_id": session},
            "params": {"dx": 10, "dy": 10}}, return_preview=False)
        assert not bad["ok"] and "session mismatch" in json.dumps(bad).lower(), bad
        checks.append("SOC UUID target rejects stale session")
        for check in checks:
            print("PASS " + check, flush=True)
        (SP / "automation-contract-results.json").write_text(json.dumps(checks, indent=2), encoding="utf-8")
    finally:
        cleanup = '''(function () {
            var owned = $.global.__automationOwned || [];
            for (var i = 0; i < owned.length; i++) {
                try { owned[i].close(SaveOptions.DONOTSAVECHANGES); } catch (e) {}
            }
            try { if ($.global.__automationOriginal) $.global.__automationOriginal.activate(); } catch (e) {}
            delete $.global.__automationOwned; delete $.global.__automationOriginal;
            return true;
        })()'''
        result = json.loads(run_osa({"script": cleanup}, 30))
        assert result["ok"], result


if __name__ == "__main__":
    asyncio.run(main())
