/** Execute the real CEP hook with boundary doubles, without mounting a browser. */
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const ts = require('../cep-extension/node_modules/typescript');
const source = fs.readFileSync(path.join(__dirname, '../cep-extension/src/hooks/useMCP.ts'), 'utf8');
const compiled = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS}}).outputText;

function fixture(hasHost = true, throws = false) {
    const effects = [], callbacks = [], intervals = [], sockets = [];
    class Socket {
        static OPEN = 1;
        constructor() { this.readyState = 1; this.sent = []; sockets.push(this); }
        send(message) { this.sent.push(JSON.parse(message)); }
        close() { this.readyState = 3; this.onclose({code: 1000}); }
    }
    const window = {
        __adobe_cep__: hasHost,
        CSInterface: class { evalScript(script, callback) {
            if (throws) throw new Error('Host rejected call');
            callbacks.push(callback);
        } },
        setInterval(fn) { intervals.push(fn); return intervals.length; },
        clearInterval() {}, setTimeout() { return 1; }, clearTimeout() {},
    };
    const exports = {};
    const context = {exports, window, WebSocket: Socket, performance: {now: () => 10}, console,
        require(name) {
            if (name === 'react') return {
                useRef: value => ({current: value}), useState: value => [value, () => {}],
                useCallback: fn => fn, useEffect: fn => effects.push(fn),
            };
            if (name === '../session') return {
                readBridgeSession: () => ({session: {port: 8081, subprotocol: 'test'}}),
                describeSessionError: () => 'missing',
            };
            if (name === '../extendscript') return {toExtendScriptLiteral: JSON.stringify};
            throw new Error('Unexpected dependency: ' + name);
        },
    };
    vm.runInNewContext(compiled, context);
    const hook = exports.useMCP();
    effects.forEach(fn => fn());
    sockets[0].onopen();
    return {hook, callbacks, sockets, intervals};
}
function request(socket, id) { socket.onmessage({data: JSON.stringify({id, script: 'mutation()'})}); }
function completion(socket) { return socket.sent.filter(value => value.type === 'complete').at(-1); }

// Reconnecting must not make an in-flight ExtendScript available for replay.
const live = fixture();
const original = live.sockets[0];
request(original, 1);
assert.equal(original.sent.at(-1).busy, true);
original.close();
live.hook.connect();
const reconnected = live.sockets[1]; reconnected.onopen();
assert.equal(reconnected.sent.at(-1).busy, true);
request(reconnected, 2);
assert.equal(live.callbacks.length, 1);
assert.equal(completion(reconnected).execution.state, 'not_started');
live.callbacks[0]('JSON is broken');
live.intervals.at(-1)();
assert.equal(reconnected.sent.at(-1).busy, false);

const malformed = fixture();
request(malformed.sockets[0], 1);
malformed.callbacks[0]('EvalScript error.');
assert.equal(completion(malformed.sockets[0]).execution.state, 'unknown');
assert.equal(completion(malformed.sockets[0]).execution.safe_to_retry, false);

const missing = fixture(false);
request(missing.sockets[0], 1);
assert.equal(completion(missing.sockets[0]).execution.state, 'not_started');
assert(completion(missing.sockets[0]).error);
assert.equal(missing.callbacks.length, 0);

const rejected = fixture(true, true);
request(rejected.sockets[0], 1);
assert.equal(completion(rejected.sockets[0]).execution.state, 'unknown');
assert.equal(rejected.sockets[0].sent.filter(x => x.type === 'heartbeat').at(-1).busy, false);
console.log('PASS CEP reconnect busy guard, invalid result, absent host and synchronous host failure');
