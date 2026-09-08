"""
Session handshake secret for the CEP bridge.

The bridge listens on a loopback WebSocket that executes arbitrary
ExtendScript, and ExtendScript can read and write the filesystem. Loopback is
not a trust boundary: any local process can reach 127.0.0.1, and a WebSocket
handshake from a web page is not subject to the same-origin policy, so an
open browser tab can connect to a localhost port without any CORS check.

So the panel proves it is a local process that can read the user's home
directory: the server writes a random secret to a 0600 file at startup, and
the panel echoes it back in the handshake. A remote page can open the socket
but cannot read the file, so it cannot produce the secret.

The file also carries the port, which lets the panel follow a non-default
WS_PORT instead of hardcoding 8081.
"""

import json
import logging
import os
import secrets
import stat
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Subprotocol carrying the secret. The browser WebSocket API cannot set
# request headers, but it can offer subprotocols, which arrive as
# Sec-WebSocket-Protocol.
TOKEN_PROTOCOL_PREFIX = "mcp.token."

SESSION_DIR_NAME = ".illustrator-mcp"
SESSION_FILE_NAME = "session.json"

_DIR_MODE = 0o700
_FILE_MODE = 0o600


def session_dir() -> Path:
    """Directory holding the session file (``~/.illustrator-mcp``)."""
    return Path.home() / SESSION_DIR_NAME


def session_file() -> Path:
    """Full path of the session handshake file."""
    return session_dir() / SESSION_FILE_NAME


def generate_token() -> str:
    """Return a fresh handshake secret.

    Hex keeps the value inside the token charset RFC 6455 allows for a
    subprotocol name, so it can travel in Sec-WebSocket-Protocol unescaped.
    """
    return secrets.token_hex(32)


def expected_subprotocol(token: str) -> str:
    """The subprotocol a client must offer to authenticate."""
    return f"{TOKEN_PROTOCOL_PREFIX}{token}"


def write_session_file(port: int, token: str) -> Optional[Path]:
    """Publish ``port`` and ``token`` for the panel to read.

    Written via a private temp file and an atomic replace so a panel reading
    concurrently never sees a half-written file, and the secret is never
    briefly world-readable. Returns the path, or None if it could not be
    written — the caller decides whether that is fatal.
    """
    path = session_file()
    payload = {
        "version": 1,
        "port": port,
        "token": token,
        "pid": os.getpid(),
    }

    try:
        directory = path.parent
        directory.mkdir(mode=_DIR_MODE, parents=True, exist_ok=True)
        # mkdir keeps the existing mode if the directory was already there.
        try:
            os.chmod(directory, _DIR_MODE)
        except OSError:
            pass

        fd, tmp_name = tempfile.mkstemp(dir=str(directory), prefix=".session-")
        try:
            os.fchmod(fd, _FILE_MODE)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
        except BaseException:
            os.unlink(tmp_name)
            raise
        os.replace(tmp_name, path)
    except OSError as exc:
        logger.error(f"Could not write session file {path}: {exc}")
        return None

    logger.info(f"Session handshake file written: {path}")
    return path


def remove_session_file(token: Optional[str] = None) -> None:
    """Delete the session file, optionally only when it belongs to ``token``.

    More than one MCP process can exist briefly during a restart.  In that
    case, an old process must not remove the session file most recently
    published by a newer process.  Callers that own a bridge therefore pass
    its token; the argument remains optional for diagnostics and legacy use.
    """
    path = session_file()
    try:
        if token is not None:
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            published_token = payload.get("token")
            if not isinstance(published_token, str) or not secrets.compare_digest(
                published_token, token
            ):
                logger.info("Session handshake file belongs to another bridge; leaving it intact")
                return
        path.unlink()
        logger.info(f"Session handshake file removed: {path}")
    except FileNotFoundError:
        pass
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(f"Could not remove session file {path}: {exc}")


def read_session_file() -> Optional[dict]:
    """Read the session file. Returns None when absent or unreadable.

    Provided for tests and diagnostics; the panel reads the file itself.
    """
    path = session_file()
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None


def file_mode_is_private(path: Path) -> bool:
    """True when ``path`` is not readable or writable by group or others."""
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return False
    return not (mode & 0o077)
