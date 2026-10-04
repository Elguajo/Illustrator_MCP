/**
 * ExtendScript Host for Illustrator MCP
 *
 * This script runs in the Illustrator ExtendScript context and provides
 * the bridge between the CEP panel JavaScript and Illustrator's DOM.
 */

/** MCP-owned JSON codec. ES3; independent of Illustrator's partial JSON. */
function mcpJsonStringify(value) {
    var stack = [];
    function quote(s) {
        return '"' + s.replace(/[\\"\x00-\x1f\x7f-\uffff]/g, function (c) {
            if (c === '"') return '\\"';
            if (c === "\\") return "\\\\";
            var h = c.charCodeAt(0).toString(16);
            return "\\u" + "0000".substring(h.length) + h;
        }) + '"';
    }
    function encode(v) {
        var t = typeof v, i, k, part, parts = [], arr;
        if (v === null) return "null";
        if (t === "string") return quote(v);
        if (t === "number") return isFinite(v) ? String(v) : "null";
        if (t === "boolean") return String(v);
        if (t !== "object") return undefined;
        for (i = 0; i < stack.length; i++) {
            if (stack[i] === v) throw new Error("Cyclic JSON value");
        }
        stack.push(v);
        arr = v instanceof Array;
        if (arr) {
            for (i = 0; i < v.length; i++) {
                part = encode(v[i]);
                parts.push(part === undefined ? "null" : part);
            }
        } else {
            for (k in v) {
                if (!Object.prototype.hasOwnProperty.call(v, k)) continue;
                part = encode(v[k]);
                if (part !== undefined) parts.push(quote(String(k)) + ":" + part);
            }
        }
        stack.pop();
        return (arr ? "[" : "{") + parts.join(",") + (arr ? "]" : "}");
    }
    return encode(value);
}

// Parse without eval: only JSON grammar is accepted, including in passthrough.
function mcpJsonParse(source) {
    var s = String(source), at = 0;
    function fail() { throw new Error("Invalid JSON at " + at); }
    function space() { while (/[ \t\r\n]/.test(s.charAt(at)) && at < s.length) at++; }
    function string() {
        var out = "", c, esc, hex;
        at++;
        while (at < s.length) {
            c = s.charAt(at++);
            if (c === '"') return out;
            if (c.charCodeAt(0) < 32) fail();
            if (c !== "\\") { out += c; continue; }
            esc = s.charAt(at++);
            if (esc === "u") {
                hex = s.substr(at, 4);
                if (!/^[0-9a-fA-F]{4}$/.test(hex)) fail();
                out += String.fromCharCode(parseInt(hex, 16)); at += 4;
            } else if (esc === '"' || esc === "\\" || esc === "/") out += esc;
            else if (esc === "b") out += "\b";
            else if (esc === "f") out += "\f";
            else if (esc === "n") out += "\n";
            else if (esc === "r") out += "\r";
            else if (esc === "t") out += "\t";
            else fail();
        }
        fail();
    }
    function value() {
        space();
        var c = s.charAt(at), out, key, match, close;
        if (c === '"') return string();
        if (c === "[" || c === "{") {
            out = c === "[" ? [] : {}; close = c === "[" ? "]" : "}"; at++; space();
            if (s.charAt(at) === close) { at++; return out; }
            while (true) {
                if (c === "{") {
                    if (s.charAt(at) !== '"') fail();
                    key = string(); space();
                    if (s.charAt(at++) !== ":") fail();
                    // Never alter the prototype of a parsed object.
                    if (key === "__proto__") fail();
                    out[key] = value();
                } else out.push(value());
                space();
                if (s.charAt(at) === close) { at++; return out; }
                if (s.charAt(at++) !== ",") fail();
                space();
            }
        }
        match = /^(true|false|null|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)/.exec(s.substring(at));
        if (!match) fail();
        at += match[0].length;
        if (match[0] === "true") return true;
        if (match[0] === "false") return false;
        if (match[0] === "null") return null;
        return Number(match[0]);
    }
    var result = value(); space();
    if (at !== s.length) fail();
    return result;
}

// Use the same codec for host envelopes and injected/user scripts. Keep the
// JSON object identity: libraries may have retained a reference to it.
if (typeof JSON === "undefined") JSON = {};
JSON.stringify = mcpJsonStringify;
JSON.parse = mcpJsonParse;

/**
 * Wrap a user script with an iteration safety guard.
 *
 * Injects:
 *   __mcp_ops        – running operation counter
 *   __MCP_MAX_OPS    – hard op limit (default 500 000, overridable by Python)
 *   __mcp_start      – timestamp at script start
 *   __MCP_MAX_MS     – hard wall-clock limit in ms (default 25 000, overridable)
 *   __mcp_check()    – call inside loops; throws when either limit exceeded
 *   __mcp_snapshot(collection) – snapshot a live Illustrator collection to Array
 *   __mcp_forEachSnapshot(collection, fn) – safe iteration with built-in check
 *
 * COVERAGE NOTE:
 *   This is an opt-in guard. Scripts that never call __mcp_check() (e.g. bare
 *   while(true){}) are NOT protected. The only robust fix for non-cooperative
 *   loops is chunked execution via app.scheduleTask() (future P2 work).
 *   The helpers here solve the most common real-world crash class: iterating
 *   a live collection while adding/removing items.
 *
 * @param {string} scriptStr - The raw user script
 * @returns {string} - Script with safety preamble prepended
 */
function wrapWithSafetyGuard(scriptStr) {
    // Do not double-wrap (idempotent)
    if (scriptStr.indexOf('__mcp_ops') >= 0) return scriptStr;

    var preamble =
        '// MCP safety guard \u2013 injected by host.jsx\n' +
        'var __mcp_ops = 0;\n' +
        'var __mcp_start = +new Date();\n' +
        'if (typeof __MCP_MAX_OPS === "undefined") var __MCP_MAX_OPS = 500000;\n' +
        'if (typeof __MCP_MAX_MS  === "undefined") var __MCP_MAX_MS  = 25000;\n' +
        'function __mcp_check() {\n' +
        '  if (++__mcp_ops > __MCP_MAX_OPS)\n' +
        '    throw new Error("MCP_SAFETY: Exceeded " + __MCP_MAX_OPS + " ops. Possible infinite loop.");\n' +
        '  if (__mcp_ops % 1000 === 0 && (+new Date() - __mcp_start) > __MCP_MAX_MS)\n' +
        '    throw new Error("MCP_SAFETY: Exceeded " + __MCP_MAX_MS + "ms wall-clock limit.");\n' +
        '}\n' +
        'function __mcp_snapshot(col) {\n' +
        '  var out = [];\n' +
        '  for (var _i = 0; _i < col.length; _i++) out.push(col[_i]);\n' +
        '  return out;\n' +
        '}\n' +
        'function __mcp_forEachSnapshot(col, fn) {\n' +
        '  var snap = __mcp_snapshot(col);\n' +
        '  for (var _i = 0; _i < snap.length; _i++) { __mcp_check(); fn(snap[_i], _i); }\n' +
        '}\n\n';

    return preamble + scriptStr;
}

/**
 * Execute a JavaScript script string in Illustrator context
 * @param {string} scriptStr - The JavaScript code to execute
 * @returns {string} - JSON string of the result
 */
function executeScript(scriptStr) {
    try {
        // Wrap with safety guard to prevent infinite-loop crashes
        var safeScript = wrapWithSafetyGuard(scriptStr);

        // Execute the script
        var result = eval(safeScript);

        // Contract-validated passthrough: if the script already returned
        // a JSON string matching the internal envelope contract, pass it
        // through directly. This eliminates double-serialization for
        // wrap_script() results and SOC batch reports.
        if (typeof result === 'string') {
            try {
                var parsed = JSON.parse(result);
                if (typeof parsed === 'object' && parsed !== null) {
                    var isEnvelope =
                        (parsed.ok === true && 'data' in parsed) ||
                        (parsed.ok === false && 'error' in parsed);
                    if (isEnvelope) return result;
                }
            } catch (pe) { /* not JSON, fall through */ }
        }

        // Bare return value — wrap in standard envelope
        if (result === undefined) result = null;

        // Handle Illustrator objects by converting to plain objects
        if (typeof result === 'object' && result !== null) {
            result = convertToPlainObject(result);
        }

        return JSON.stringify({ ok: true, data: result });

    } catch (e) {
        // Matches required internal fields; name is extra (useful signal)
        return JSON.stringify({
            ok: false,
            error: { message: e.message, line: e.line || null, name: e.name || null }
        });
    }
}

/**
 * Safe dispatcher for MCP commands (No text concatenation)
 * @param {string} command - The command name
 * @param {object} payload - The arguments object
 * @returns {string} - JSON string of the result
 */
function mcp_dispatch(command, payload) {
    try {
        // Dispatch logic here. For now, we only have simple commands,
        // but this extensibility point allows for mapping string commands
        // to specific functions without 'eval'

        switch (command) {
            case 'ping':
                return ping();
            case 'execute_script':
                // For 'execute_script', we sadly still need eval for arbitrary code,
                // but at least the envelope was safely unpacked
                return executeScript(payload.script);
            default:
                return JSON.stringify({ error: "Unknown command: " + command });
        }
    } catch (e) {
        return JSON.stringify({ error: e.message });
    }
}

/**
 * Handle a full MCP request envelope
 * @param {object} request - The full request object {id, script, command...}
 * @returns {string} - JSON result
 */
function mcp_handle_request(request) {
    // If it's a raw script request (classic mode)
    if (request.script) {
        return executeScript(request.script);
    }
    // Future: handle structured commands safely
    return JSON.stringify({ error: "No script provided" });
}

function ping() {
    return JSON.stringify({
        pong: true,
        app: app.name,
        version: app.version,
        libs: ["host.jsx"]
    });
}

/**
 * Convert Illustrator DOM objects to plain JavaScript objects
 * @param {*} obj - Object to convert
 * @param {number} depth - Current recursion depth
 * @returns {*} - Plain JavaScript object
 */
function convertToPlainObject(obj, depth) {
    if (depth === undefined) depth = 0;
    if (depth > 5) return '[Max depth reached]';

    if (obj === null || obj === undefined) {
        return obj;
    }

    // Handle primitive types
    if (typeof obj !== 'object') {
        return obj;
    }

    // Handle arrays
    if (obj instanceof Array || (obj.typename && typeof obj.length === 'number')) {
        var arr = [];
        var len = Math.min(obj.length, 100); // Limit array size
        for (var i = 0; i < len; i++) {
            try {
                arr.push(convertToPlainObject(obj[i], depth + 1));
            } catch (e) {
                arr.push('[Error: ' + e.message + ']');
            }
        }
        return arr;
    }

    // Handle Illustrator objects - extract common properties
    var result = {};
    // Preserve ordinary data; project native DOM objects below to bound parent links.
    if (!obj.typename) {
        for (var key in obj) {
            if (Object.prototype.hasOwnProperty.call(obj, key) && typeof obj[key] !== 'function') {
                result[key] = convertToPlainObject(obj[key], depth + 1);
            }
        }
        return result;
    }

    // Common properties to extract
    var props = ['name', 'typename', 'width', 'height', 'left', 'top',
        'bounds', 'visible', 'locked', 'selected', 'opacity',
        'fillColor', 'strokeColor', 'strokeWidth', 'contents',
        'length', 'index', 'parent'];

    for (var i = 0; i < props.length; i++) {
        var prop = props[i];
        try {
            if (obj[prop] !== undefined) {
                var val = obj[prop];
                if (typeof val !== 'function') {
                    result[prop] = convertToPlainObject(val, depth + 1);
                }
            }
        } catch (e) {
            // Property not accessible, skip
        }
    }

    // If no properties were extracted, try to get a string representation
    var hasProperties = false;
    for (var key in result) {
        if (Object.prototype.hasOwnProperty.call(result, key)) { hasProperties = true; break; }
    }
    if (!hasProperties) {
        try {
            result = String(obj);
        } catch (e) {
            result = '[Object]';
        }
    }

    return result;
}

/**
 * Helper function to get document info
 * @returns {string} - JSON string with document information
 */
function getDocumentInfo() {
    try {
        if (app.documents.length === 0) {
            return JSON.stringify({ error: 'No documents open' });
        }

        var doc = app.activeDocument;
        return JSON.stringify({
            name: doc.name,
            path: doc.path ? doc.path.fsName : '',
            width: doc.width,
            height: doc.height,
            artboards: doc.artboards.length,
            layers: doc.layers.length
        });
    } catch (e) {
        return JSON.stringify({ error: e.message });
    }
}

/**
 * Test function to verify ExtendScript is working
 * @returns {string} - Test result
 */
function testConnection() {
    return JSON.stringify({
        ok: true,
        data: {
            app: app.name,
            version: app.version,
            documentsOpen: app.documents.length
        },
        operation: "test_connection"
    });
}
