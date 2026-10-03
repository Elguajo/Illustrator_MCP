"""Regression coverage for conservative recovery, session guards and discovery."""

import asyncio
import json
import os
import subprocess
from unittest.mock import AsyncMock, patch

import pytest

from illustrator_mcp.bridge.request_registry import RequestRegistry
from illustrator_mcp.result_contract import add_result_evidence
from illustrator_mcp.tool_catalog import CORE_TOOLS, TOOL_SUMMARIES
from tests.test_doc_model import _value, _run, SCENE


def test_session_stays_stable_across_reads_and_document_switches():
    assert _value('''(function () {
        var first = dmDocumentRef(doc).session_id;
        var other = build({name: doc.name, layers: []}).doc;
        app.documents.push(other);
        var second = dmDocumentRef(other).session_id;
        return first === dmDocumentRef(doc).session_id && first !== second;
    })()''', SCENE)


def test_same_filename_reopened_document_rejects_previous_session():
    result = _run('''(function () {
        var old = dmDocumentRef(doc).session_id;
        var reopened = build({name: doc.name, layers: []}).doc;
        app.documents = [reopened];
        mcpCheckDocumentSession(reopened, old);
        throw new Error("Guard did not reject stale identity");
    })()''', SCENE)
    assert not result["ok"] and "session mismatch" in result["message"]


def test_closed_document_references_are_released():
    assert _value('''(function () {
        dmDocumentRef(doc);
        var other = build({name: doc.name, layers: []}).doc;
        app.documents = [other]; dmDocumentRef(other);
        return $.global.__mcpDocumentSessions.length;
    })()''', SCENE) == 1


def test_two_documents_in_the_same_clock_tick_get_distinct_sessions():
    assert _value('''(function () {
        Date = function () { this.valueOf = function () { return 42; }; };
        var other = build({name: doc.name, layers: []}).doc;
        app.documents.push(other);
        return mcpDocumentSession(doc) !== mcpDocumentSession(other);
    })()''', SCENE)


@pytest.mark.asyncio
async def test_transport_timeout_leaves_request_unknown_and_late_result_is_observable():
    from illustrator_mcp.websocket_bridge import WebSocketBridge
    from illustrator_mcp.proxy_client import build_envelope_dict
    bridge = WebSocketBridge()
    bridge.loop = asyncio.get_running_loop()
    bridge.is_connected = lambda: True
    bridge.server.send = AsyncMock()
    result = await bridge.execute_script_async("mutation()", timeout=0.01)
    envelope = build_envelope_dict(result)
    assert not envelope["ok"] and envelope["error"]["safe_to_retry"] is False
    phase = envelope["diagnostics"]["execution"]
    assert phase["state"] == "unknown"
    request_id = phase["request_id"]
    assert bridge.registry.pending_count == 0
    bridge.registry.complete_request(request_id, {"result": "done"})
    assert bridge.registry.execution_status(request_id)["state"] == "completed"
    assert bridge.server.send.await_count == 1


@pytest.mark.asyncio
async def test_transport_busy_rejection_is_distinct_from_a_started_script_error():
    from illustrator_mcp.websocket_bridge import WebSocketBridge
    bridge = WebSocketBridge()
    bridge.loop = asyncio.get_running_loop()
    bridge.is_connected = lambda: True

    async def rejected(message):
        request_id = json.loads(message)["id"]
        bridge.registry.complete_request(request_id, {"error": "BUSY", "execution": {
            "state": "not_started", "safe_to_retry": True}})

    bridge.server.send = rejected
    result = await bridge.execute_script_async("mutation()")
    assert result["execution"]["state"] == "not_started"
    assert result["execution"]["safe_to_retry"] is True


@pytest.mark.asyncio
async def test_busy_panel_after_timeout_never_receives_a_queued_mutation():
    from illustrator_mcp.websocket_bridge import WebSocketBridge
    bridge = WebSocketBridge()
    bridge.loop = asyncio.get_running_loop()
    bridge.is_connected = lambda: True
    bridge.server.send = AsyncMock()
    first = await bridge.execute_script_async("slow_mutation()", timeout=0.01)
    bridge._panel_busy = True
    second = await bridge.execute_script_async("must_not_run()")
    assert second["execution"] == {"state": "not_started", "safe_to_retry": True}
    assert bridge.server.send.await_count == 1
    assert bridge.registry.execution_status(first["execution"]["request_id"])["state"] == "unknown"
    streamed = [x async for x in bridge.execute_script_streaming("must_not_run()")]
    assert streamed[0]["execution"]["state"] == "not_started"
    assert bridge.server.send.await_count == 1


@pytest.mark.asyncio
async def test_pending_request_blocks_a_second_send_before_busy_heartbeat():
    from illustrator_mcp.websocket_bridge import WebSocketBridge
    bridge = WebSocketBridge()
    bridge.loop = asyncio.get_running_loop()
    bridge.is_connected = lambda: True
    sent = asyncio.Event()

    async def send(message):
        sent.set()

    bridge.server.send = AsyncMock(side_effect=send)
    first = asyncio.create_task(bridge.execute_script_async("mutation()"))
    await sent.wait()
    second = await bridge.execute_script_async("must_not_run()")
    assert second["execution"]["state"] == "not_started"
    assert bridge.server.send.await_count == 1
    request_id = bridge.registry.execution_status()["request_id"]
    bridge.registry.complete_request(request_id, {"result": "done"})
    assert (await first)["execution"]["state"] == "completed"


@pytest.mark.asyncio
async def test_transport_never_trusts_retry_flag_on_a_completed_execution():
    from illustrator_mcp.websocket_bridge import WebSocketBridge
    bridge = WebSocketBridge()
    bridge.loop = asyncio.get_running_loop()
    bridge.is_connected = lambda: True

    async def completed(message):
        bridge.registry.complete_request(json.loads(message)["id"], {"result": "done", "execution": {
            "state": "completed", "safe_to_retry": True}})

    bridge.server.send = completed
    result = await bridge.execute_script_async("mutation()")
    assert result["execution"]["safe_to_retry"] is False


@pytest.mark.asyncio
async def test_late_completion_resolves_timeout_uncertainty_without_replay():
    registry = RequestRegistry()
    request_id, future = registry.create_request(asyncio.get_running_loop(), "mutation()")
    registry.record_execution(request_id, "unknown")
    future.cancel()
    registry.fail_request(request_id, TimeoutError())
    assert registry.execution_status(request_id)["safe_to_retry"] is False
    assert registry.complete_request(request_id, {"result": "done"}) is False
    assert registry.execution_status(request_id) == {
        "state": "completed", "safe_to_retry": False, "request_id": request_id,
    }


def test_recovery_history_is_bounded_and_does_not_keep_script_or_result():
    registry = RequestRegistry()
    for request_id in range(100):
        registry.record_execution(request_id, "unknown")
    assert registry.execution_status(0) is None
    assert registry.execution_status(99) == {"state": "unknown", "safe_to_retry": False, "request_id": 99}
    assert len(registry._execution) == 64


def test_late_completion_does_not_replace_the_latest_submitted_request():
    registry = RequestRegistry()
    registry.record_execution(1, "unknown")
    registry.record_execution(2, "unknown")
    registry.complete_request(1, {"result": "done"})
    assert registry.execution_status()["request_id"] == 2


@pytest.mark.parametrize("state,safe", [("not_started", True), ("unknown", False), ("completed", False)])
def test_retry_flag_follows_execution_evidence(state, safe):
    env = {"ok": False, "error": {"message": "failed"}, "result": None,
           "diagnostics": {"execution": {"state": state, "safe_to_retry": safe}}}
    assert add_result_evidence(env)["error"]["safe_to_retry"] is safe


def test_partial_typed_result_never_claims_read_back():
    result = {"success_count": 1, "fail_count": 1,
              "changed": [{"uuid": "1"}], "failed_objects": [{"uuid": "2"}]}
    env = add_result_evidence({"ok": True, "result": result, "diagnostics": {}})
    assert env["ok"] is True and env["result"] is result
    summary = env["diagnostics"]["changes"]
    assert summary["status"] == "partial"
    assert summary["verification"] == "not_performed"
    assert summary["affected"] == [{"uuid": "1"}]


@pytest.mark.parametrize("result", [
    {"success_count": "one", "fail_count": None},
    {"stats": {"total": "all", "executed": "some"}},
    {"success_count": 1, "fail_count": 0, "skipped_objects": None},
])
def test_arbitrary_raw_result_fields_cannot_break_envelope_serialization(result):
    env = add_result_evidence({"ok": True, "result": result, "diagnostics": {}})
    assert env["ok"] is True and env["result"] is result
    json.dumps(env)


def test_batch_evidence_preserves_counts_ids_rollback_and_unattempted_operations():
    report = {"batchReport": {"ok": False, "stats": {"total": 4, "executed": 2, "passed": 1, "failed": 1},
                              "createdIds": ["persistent-id"], "rolledBack": 1}}
    env = add_result_evidence({"ok": False, "result": {"report": report}, "diagnostics": {}})
    summary = env["diagnostics"]["changes"]
    assert summary["unattempted"] == 2 and summary["created_ids"] == ["persistent-id"]
    assert summary["rolled_back"] == 1 and summary["verification"] == "not_performed"


@pytest.mark.asyncio
async def test_execution_inspection_never_calls_illustrator():
    from illustrator_mcp.server import mcp
    bridge = AsyncMock()
    bridge.registry = RequestRegistry()
    bridge.registry.record_execution(7, "unknown")
    bridge.is_connected = lambda: True
    bridge.get_panel_health = lambda: {"busy": True, "stale": False, "active_request_id": 7}
    with patch("illustrator_mcp.runtime.get_runtime") as runtime:
        runtime.return_value.get_bridge.return_value = bridge
        result = await mcp.call_tool("illustrator_inspect", {"params": {"view": "execution", "request_id": 7}})
    env = json.loads(result[0].text)
    assert env["result"]["execution"]["state"] == "running"
    bridge.execute_script_async.assert_not_called()


@pytest.mark.parametrize("profile", ["all", "core"])
def test_startup_profiles_and_resource_documentation(profile):
    script = '''import asyncio,json
from illustrator_mcp.server import mcp
from illustrator_mcp.tools.context import tool_reference_resource
async def main():
 tools=await mcp.list_tools()
 reference=await tool_reference_resource()
 print(json.dumps({"names":[t.name for t in tools],"chars":sum(len(t.description or "") for t in tools),
                   "reference":len(reference),"instructions":mcp.instructions}))
asyncio.run(main())'''
    env = {**os.environ, "ILLUSTRATOR_MCP_TOOL_PROFILE": profile}
    import sys
    out = subprocess.run([sys.executable, "-c", script], env=env, check=True, capture_output=True, text=True)
    value = json.loads(out.stdout)
    assert set(value["names"]) == (CORE_TOOLS if profile == "core" else {
        "illustrator_" + key for key in TOOL_SUMMARIES
    })
    assert value["chars"] < value["reference"] / 3
    if profile == "core":
        assert "illustrator_effects" not in value["instructions"]


@pytest.mark.asyncio
async def test_stale_session_is_a_retryable_pre_mutation_error():
    from mcp.types import CallToolResult
    from illustrator_mcp.server import mcp
    raw = json.dumps({"ok": True, "diagnostics": {}, "result": {
        "__dm_request_error": "Document session mismatch", "__dm_not_started": True}})
    with patch("illustrator_mcp.tools.doc_model_tools.execute_jsx_tool", AsyncMock(return_value=raw)):
        response = await mcp.call_tool("illustrator_text", {"params": {
            "action": "replace", "find": "old", "replace": "new", "document_session_id": "old-session",
        }})
    assert isinstance(response, CallToolResult) and response.isError
    env = json.loads(response.content[0].text)
    assert env["error"]["safe_to_retry"] is True
    assert env["diagnostics"]["execution"]["state"] == "not_started"
