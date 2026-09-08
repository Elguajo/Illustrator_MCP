"""Live CEP scenario for annotated-preview grounding and safe ID handoff.

Run with Illustrator open and the MCP Control CEP panel connected:

    .venv/bin/python tests/live_test_grounded_handoff.py

The scenario creates an unsaved test document and always closes it without
saving, leaving the previously active document untouched.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from illustrator_mcp.protocol import TaskPayload
from illustrator_mcp.runtime import get_runtime
from illustrator_mcp.tools.execute import ExecuteScriptInput, illustrator_execute_script
from illustrator_mcp.tools.grounding import GroundObjectInput, illustrator_ground_object
from illustrator_mcp.tools.task_execution import ExecuteTaskInput, illustrator_execute_task


CONNECT_TIMEOUT_SECONDS = 90
TARGET_NAME = "live_handoff_target"


def _text_parts(response: Any) -> list[str]:
    if isinstance(response, str):
        return [response]
    return [part.text for part in response if getattr(part, "type", None) == "text"]


def _envelope(response: Any) -> dict[str, Any]:
    for text in _text_parts(response):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and "ok" in parsed:
            return parsed
    raise AssertionError(f"No MCP envelope in response: {_text_parts(response)!r}")


def _annotation_map(response: Any) -> dict[str, Any]:
    for text in _text_parts(response):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and "annotations" in parsed:
            return parsed
    raise AssertionError("Grounding response did not include an annotation map")


async def _script(script: str, description: str) -> dict[str, Any]:
    response = await illustrator_execute_script(
        ExecuteScriptInput(script=script, description=description, return_preview=False)
    )
    envelope = _envelope(response)
    assert envelope["ok"] is True, envelope
    result = envelope.get("result")
    return json.loads(result) if isinstance(result, str) else result


async def _task(mcp_id: str, precondition: dict[str, Any]) -> dict[str, Any]:
    payload = TaskPayload(
        task="live_grounded_handoff",
        targets={"type": "id", "ids": [mcp_id], "precondition": precondition},
        options={"trace": True},
    )
    response = await illustrator_execute_task(
        ExecuteTaskInput(
            payload=payload,
            compute_fn="""
                var actions = [];
                for (var i = 0; i < items.length; i++) {
                    actions.push({item: items[i]});
                }
                return actions;
            """,
            apply_fn="""
                for (var i = 0; i < actions.length; i++) {
                    actions[i].item.opacity = 72;
                    report.stats.itemsModified++;
                }
            """,
            return_preview=False,
        )
    )
    return _envelope(response)


async def _target_state() -> dict[str, Any]:
    return await _script(
        """
        (function () {
            var item = app.activeDocument.pageItems.getByName("live_handoff_target");
            return JSON.stringify({opacity: item.opacity, hidden: item.hidden, locked: item.locked});
        })();
        """,
        "Read live handoff target state",
    )


async def _wait_for_panel() -> None:
    bridge = get_runtime().get_bridge()
    deadline = asyncio.get_running_loop().time() + CONNECT_TIMEOUT_SECONDS
    while not bridge.is_connected():
        if asyncio.get_running_loop().time() >= deadline:
            raise RuntimeError(
                "CEP panel did not connect within 90 seconds. Open Window > Extensions > MCP Control."
            )
        await asyncio.sleep(1)


async def run() -> None:
    await _wait_for_panel()
    fixture_created = False
    try:
        await _script(
            """
            (function () {
                var doc = app.documents.add(DocumentColorSpace.RGB, 400, 300);
                var target = doc.pathItems.rectangle(250, 40, 100, 60);
                target.name = "live_handoff_target";
                var peer = doc.pathItems.ellipse(170, 210, 70, 70);
                peer.name = "live_handoff_peer";
                var label = doc.textFrames.pointText([45, 100]);
                label.contents = "Grounded handoff";
                label.name = "live_handoff_label";
                return JSON.stringify({count: doc.pageItems.length, target: target.name});
            })();
            """,
            "Create isolated live grounded-handoff fixture",
        )
        fixture_created = True

        # The first call gives the exact annotation map used to select the target.
        preview = await illustrator_ground_object(GroundObjectInput(label=1, assign_id=False))
        preview_envelope = _envelope(preview)
        assert preview_envelope["ok"] is True, preview_envelope
        annotations = _annotation_map(preview)["annotations"]
        target_annotation = next(
            entry for entry in annotations if entry.get("name") == TARGET_NAME
        )

        grounded = await illustrator_ground_object(
            GroundObjectInput(label=int(target_annotation["label"]), assign_id=True)
        )
        grounded_envelope = _envelope(grounded)
        assert grounded_envelope["ok"] is True, grounded_envelope
        selected = grounded_envelope["result"]["selected_object"]
        assert selected["name"] == TARGET_NAME
        assert selected["stable_id"]["status"] in {"assigned", "existing"}
        mcp_id = selected["mcp_id"]
        assert mcp_id
        precondition = {
            "type": selected["type"],
            "bounds_screen": selected["bounds"]["screen"],
            "tolerance_pt": 0.5,
        }

        success = await _task(mcp_id, precondition)
        assert success["ok"] is True, success
        resolved = success["result"]["report"]["resolvedTargets"]
        assert len(resolved) == 1 and resolved[0]["mcp_id"] == mcp_id
        assert (await _target_state())["opacity"] == 72

        await _script(
            "app.activeDocument.pageItems.getByName(\"live_handoff_target\").left += 12; JSON.stringify({moved: true});",
            "Move grounded target to invalidate the snapshot",
        )
        stale = await _task(mcp_id, precondition)
        assert stale["ok"] is False, stale
        assert stale["error"]["details"]["reason"] == "precondition_failed", stale
        assert (await _target_state())["opacity"] == 72

        missing = await _task("mcp_live_missing", {"type": selected["type"]})
        assert missing["ok"] is False, missing
        assert missing["error"]["code"] == "R008", missing

        await _script(
            "app.activeDocument.pageItems.getByName(\"live_handoff_target\").hidden = true; JSON.stringify({hidden: true});",
            "Hide grounded target",
        )
        hidden = await _task(mcp_id, {"type": selected["type"]})
        assert hidden["ok"] is False, hidden
        assert hidden["error"]["details"]["reason"] == "hidden_target", hidden

        await _script(
            "app.activeDocument.pageItems.getByName(\"live_handoff_target\").hidden = false; app.activeDocument.pageItems.getByName(\"live_handoff_target\").locked = true; JSON.stringify({locked: true});",
            "Lock grounded target",
        )
        locked = await _task(mcp_id, {"type": selected["type"]})
        assert locked["ok"] is False, locked
        assert locked["error"]["details"]["reason"] == "locked_target", locked
        print("LIVE GROUNDED HANDOFF: PASS")
    finally:
        try:
            if fixture_created:
                # Close the fixture by identity, never app.activeDocument:
                # the user's own (possibly unsaved) document may be active if
                # anything above changed focus, and DONOTSAVECHANGES on it
                # would silently destroy their work.
                await _script(
                    """
                    (function () {
                        var closed = null;
                        for (var i = app.documents.length - 1; i >= 0; i--) {
                            var doc = app.documents[i];
                            var isFixture = false;
                            try {
                                doc.pageItems.getByName("live_handoff_target");
                                isFixture = true;
                            } catch (e) { isFixture = false; }
                            if (isFixture) {
                                closed = doc.name;
                                doc.close(SaveOptions.DONOTSAVECHANGES);
                                break;
                            }
                        }
                        return JSON.stringify({closed: closed});
                    })();
                    """,
                    "Close isolated live fixture without saving",
                )
        finally:
            get_runtime().shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except Exception as exc:
        print(f"LIVE GROUNDED HANDOFF: FAIL: {exc}", file=sys.stderr)
        raise
