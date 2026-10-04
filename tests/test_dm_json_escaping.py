"""Typed-tool results must survive the production serialization chain.

Illustrator 30.8.1 exposes partial native JSON (no parse; incomplete escaping).
The production host now replaces it with its ES3 codec. Typed dm1 wire results
remain compatible with older installed hosts, while ordinary raw script data
and envelope passthrough must survive the repaired serialization chain.

This test runs the generated script and the real host.jsx ``executeScript``
under a ``JSON`` that behaves like Illustrator's, parses the panel's result
strictly, and decodes it like the Python side does.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from illustrator_mcp.tools.doc_model_tools import (
    decode_dm_wire,
    dm_script,
    unwrap_dm_response,
)

ROOT = Path(__file__).resolve().parents[1]
HOST = (ROOT / "cep-extension" / "jsx" / "host.jsx").read_text(encoding="utf-8")

# Illustrator's JSON: stringify escapes only '"' and "\n"; there is no parse.
_ILLUSTRATOR_JSON = r"""
JSON = {
  stringify: function (v) {
    var t = typeof v;
    if (v === null) { return "null"; }
    if (t === "string") { return '"' + v.replace(/"/g, '\\"').replace(/\n/g, '\\n') + '"'; }
    if (t === "number" || t === "boolean") { return String(v); }
    var parts = [], k, i;
    if (v instanceof Array) {
      for (i = 0; i < v.length; i++) { parts.push(JSON.stringify(v[i])); }
      return "[" + parts.join(",") + "]";
    }
    for (k in v) {
      if (!v.hasOwnProperty(k) || v[k] === undefined) { continue; }
      parts.push('"' + k + '":' + JSON.stringify(v[k]));
    }
    return "{" + parts.join(",") + "}";
  }
};
"""

# Everything that broke: quote, backslash, CR (paragraph break), tab, a control
# character, both JS line separators, a percent sign, non-BMP and accents.
TEXT = 'one\rtwo\tthree\u0003four five six\nseven "q" \\ \\n %41 %22 é ж \U0001F600 end'


def _panel_result(call: str, payload: dict) -> str:
    """What the CEP panel hands to Python for one dm_script run: the string
    produced by host.jsx executeScript, parsed with a strict JSON.parse."""
    script = dm_script(call, payload, needs_doc=False)
    harness = f"""
var app = {{ name: "Illustrator", version: "30.8.1" }};
{_ILLUSTRATOR_JSON}
{HOST}
// host.jsx installs its own codec even when partial native JSON exists.
var hostOut = mcp_handle_request({json.dumps({"script": script}, ensure_ascii=True)});
process.stdout.write(hostOut);
"""
    out = subprocess.run(["node", "-e", harness], check=True, capture_output=True, text=True).stdout
    panel = json.loads(out)  # strict, like useMCP.ts
    assert panel["ok"] is True, panel
    return panel["data"]


def test_host_repairs_partial_native_json_for_bare_script_results():
    """Regression: quoted text survives with Illustrator's partial native JSON."""
    harness = f"""
var app = {{ name: "Illustrator", version: "30.8.1" }};
{_ILLUSTRATOR_JSON}
{HOST}
process.stdout.write(mcp_handle_request({json.dumps({"script": '(function () { return JSON.stringify({ t: "say \\"hi\\"" }); })()'})}));
"""
    out = subprocess.run(["node", "-e", harness], check=True, capture_output=True, text=True).stdout
    assert json.loads(json.loads(out)["data"]) == {"t": 'say "hi"'}


def test_result_with_hostile_text_round_trips_through_host_and_panel():
    wire = _panel_result("({ contents: P.text, nested: [P.text, { k: P.text }] })", {"text": TEXT})
    assert wire.startswith("dm1:")
    value = decode_dm_wire(wire)
    assert value == {"contents": TEXT, "nested": [TEXT, {"k": TEXT}]}


def test_wire_string_contains_nothing_a_serializer_could_mangle():
    wire = _panel_result("({ contents: P.text })", {"text": TEXT})
    assert not any(c in wire for c in '"\\\n\r\t')
    assert all(0x20 <= ord(c) < 0x7F for c in wire)


def test_keys_numbers_and_empty_values_survive():
    call = ("({ 'k\"ey': 1.5, n: null, u: undefined, z: [], o: {}, t: true, big: 1e21, nan: 0 / 0,"
            " f: function () {} })")
    assert decode_dm_wire(_panel_result(call, {})) == {
        'k"ey': 1.5, "n": None, "z": [], "o": {}, "t": True, "big": 1e21, "nan": None,
    }


def test_request_error_message_round_trips():
    wire = _panel_result(
        '(function () { var e = new Error("bad \\"name\\"\\rx"); e.dmUserError = true; throw e; })()', {})
    assert decode_dm_wire(wire) == {"__dm_request_error": 'bad "name"\rx', "__dm_not_started": False}


def test_unwrap_dm_response_finds_the_wire_string_in_a_bridge_response():
    wire = _panel_result("({ contents: P.text })", {"text": TEXT})
    response = {"result": {"ok": True, "data": wire}}
    assert unwrap_dm_response(response) == {"contents": TEXT}
    assert unwrap_dm_response({"result": {"ok": True, "data": {"plain": 1}}}) is None
    assert decode_dm_wire({"already": "decoded"}) == {"already": "decoded"}


def _host_script(script: str) -> dict:
    harness = _ILLUSTRATOR_JSON + HOST + "\nprocess.stdout.write(mcp_handle_request(" + json.dumps({"script": script}) + "));"
    out = subprocess.run(["node", "-e", harness], check=True, capture_output=True, text=True).stdout
    return json.loads(out)


def test_plain_returned_data_keeps_unknown_keys_and_hostile_text():
    data = {TEXT: TEXT, "nested": {"hasOwnProperty": 7, "length": 3, "ids": ["uuid-1"]}}
    assert _host_script("(" + json.dumps(data, ensure_ascii=True) + ")") == {"ok": True, "data": data}


def test_host_codec_handles_array_holes_nonfinite_numbers_and_undefined_fields():
    value = _host_script('JSON.stringify({ok:true,data:{a:[,undefined,function(){},NaN,Infinity], u:undefined, f:function(){}}})')
    assert value == {"ok": True, "data": {"a": [None, None, None, None, None]}}


def test_cyclic_json_is_a_script_failure_with_a_parseable_error():
    value = _host_script('(function(){var x={};x.x=x;return JSON.stringify(x);})()')
    assert value["ok"] is False
    assert "Cyclic" in value["error"]["message"]


@pytest.mark.parametrize("source", [
    '{"x":1,}', '[1,]', '01', '+1', 'undefined', '{x:1}', '"raw\nnewline"',
    '{"__proto__":{}}', '(function(){global.attack=true;return 1;})()',
])
def test_host_parser_rejects_non_json_without_evaluating_it(source):
    script = '(function(){global.attack=false;try {JSON.parse(' + json.dumps(source) + ');return false;}catch(e){return !global.attack;}})()'
    assert _host_script(script) == {"ok": True, "data": True}
