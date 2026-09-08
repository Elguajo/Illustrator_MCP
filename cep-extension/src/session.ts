/**
 * Reads the bridge handshake file written by the MCP server.
 *
 * The bridge executes arbitrary ExtendScript, so it will not accept a socket
 * that cannot prove it is a local process: the server writes a random secret
 * to a 0600 file in the user's home directory, and we echo it back as a
 * WebSocket subprotocol. A web page can open a socket to localhost — the
 * same-origin policy does not cover WebSocket handshakes — but it cannot read
 * this file, so it cannot complete the handshake.
 *
 * Node access comes from --enable-nodejs / --mixed-context in
 * CSXS/manifest.xml. Without it there is no way to read the secret and the
 * panel cannot connect at all, which we report rather than retry blindly.
 */

const SESSION_DIR = '.illustrator-mcp';
const SESSION_FILE = 'session.json';
const TOKEN_PROTOCOL_PREFIX = 'mcp.token.';

export interface BridgeSession {
    port: number;
    token: string;
    /** The subprotocol to offer when opening the socket. */
    subprotocol: string;
}

export type SessionError =
    | 'no-node'        // Node integration unavailable in this panel
    | 'not-found'      // server is not running, or has shut down
    | 'unreadable'     // present but malformed or permission-denied
    ;

export interface SessionResult {
    session?: BridgeSession;
    error?: SessionError;
    detail?: string;
}

declare const cep_node: any;

function nodeRequire(): ((id: string) => any) | null {
    // CEP exposes Node differently depending on context mode.
    const w = window as any;
    if (typeof cep_node !== 'undefined' && cep_node?.require) return cep_node.require;
    if (typeof w.cep_node?.require === 'function') return w.cep_node.require;
    if (typeof w.require === 'function') return w.require;
    return null;
}

/**
 * Locate and parse the session file.
 *
 * Never throws: the panel calls this on every reconnect attempt, and a
 * missing file is the normal state while the MCP server is not running.
 */
export function readBridgeSession(): SessionResult {
    const req = nodeRequire();
    if (!req) {
        return {
            error: 'no-node',
            detail: 'Node integration is unavailable; cannot read the handshake file.',
        };
    }

    let fs: any;
    let path: any;
    let os: any;
    try {
        fs = req('fs');
        path = req('path');
        os = req('os');
    } catch (e) {
        return { error: 'no-node', detail: String(e) };
    }

    const file = path.join(os.homedir(), SESSION_DIR, SESSION_FILE);

    let raw: string;
    try {
        raw = fs.readFileSync(file, 'utf8');
    } catch (e: any) {
        if (e && e.code === 'ENOENT') {
            return { error: 'not-found', detail: file };
        }
        return { error: 'unreadable', detail: String(e) };
    }

    let parsed: any;
    try {
        parsed = JSON.parse(raw);
    } catch (e) {
        return { error: 'unreadable', detail: `malformed JSON in ${file}` };
    }

    const port = Number(parsed?.port);
    const token = parsed?.token;
    if (!Number.isFinite(port) || port <= 0 || typeof token !== 'string' || !token) {
        return { error: 'unreadable', detail: `missing port or token in ${file}` };
    }

    return {
        session: {
            port,
            token,
            subprotocol: TOKEN_PROTOCOL_PREFIX + token,
        },
    };
}

/** Human-readable reason shown in the panel log. */
export function describeSessionError(result: SessionResult): string {
    switch (result.error) {
        case 'no-node':
            return `Panel cannot read the handshake file (${result.detail}). ` +
                   'Check --enable-nodejs in CSXS/manifest.xml.';
        case 'not-found':
            return 'MCP server not running (no handshake file). Waiting...';
        case 'unreadable':
            return `Handshake file unusable: ${result.detail}`;
        default:
            return 'Unknown session error';
    }
}
