#!/usr/bin/env bash
#
# Package the CEP panel for distribution.
#
# Installing via install-cep.sh symlinks the source folder and switches
# Illustrator into PlayerDebugMode. That is fine on this machine and useless
# everywhere else: without debug mode Illustrator refuses to load an unsigned
# extension, so anyone you hand the folder to sees no panel at all. A signed
# .zxp is the only way to distribute it.
#
# Usage:
#   scripts/package-cep.sh                    # stage only, then report
#   scripts/package-cep.sh --sign             # stage and sign (needs a cert)
#
# Signing needs Adobe's ZXPSignCmd, which is not bundled with anything and has
# to be downloaded once from
# https://github.com/Adobe-CEP/CEP-Resources/tree/master/ZXPSignCMD
# Put it on PATH, or point ZXPSIGNCMD at it.
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXT_DIR="$ROOT/cep-extension"
BUILD_DIR="$ROOT/build/cep"
STAGE_DIR="$BUILD_DIR/staging"
CERT_PATH="${CERT_PATH:-$BUILD_DIR/self-signed.p12}"
ZXP_OUT="$BUILD_DIR/com.illustrator.mcp.panel.zxp"
SIGNCMD="${ZXPSIGNCMD:-$(command -v ZXPSignCmd || true)}"

SIGN=0
[ "${1:-}" = "--sign" ] && SIGN=1

say() { printf '%s\n' "$*"; }
fail() { printf 'error: %s\n' "$*" >&2; exit 1; }

# ── 1. Build the panel ───────────────────────────────────────────────
say "Building panel..."
( cd "$EXT_DIR" && npm run build >/dev/null )
[ -f "$EXT_DIR/dist/index.html" ] || fail "build produced no dist/index.html"

# ── 2. Stage exactly what ships ──────────────────────────────────────
# Everything else — node_modules, src, vite config, lockfiles — stays out.
# A .debug file must never ship: it enables remote debugging on a machine that
# never asked for it.
say "Staging..."
rm -rf "$STAGE_DIR"
mkdir -p "$STAGE_DIR"
cp -R "$EXT_DIR/CSXS" "$STAGE_DIR/"
cp -R "$EXT_DIR/dist" "$STAGE_DIR/"
cp -R "$EXT_DIR/jsx" "$STAGE_DIR/"
[ -f "$EXT_DIR/index.html" ] && cp "$EXT_DIR/index.html" "$STAGE_DIR/"

find "$STAGE_DIR" -name ".debug" -delete
find "$STAGE_DIR" -name ".DS_Store" -delete

# The manifest points at ./dist/index.html and ./jsx/host.jsx; verify rather
# than trusting, because a bad path fails silently as an empty panel.
MANIFEST="$STAGE_DIR/CSXS/manifest.xml"
[ -f "$MANIFEST" ] || fail "no CSXS/manifest.xml in the staged package"
MAIN_PATH="$(sed -n 's:.*<MainPath>\./\(.*\)</MainPath>.*:\1:p' "$MANIFEST" | head -1)"
SCRIPT_PATH="$(sed -n 's:.*<ScriptPath>\./\(.*\)</ScriptPath>.*:\1:p' "$MANIFEST" | head -1)"
[ -n "$MAIN_PATH" ] && [ -f "$STAGE_DIR/$MAIN_PATH" ] \
    || fail "manifest MainPath '$MAIN_PATH' is missing from the package"
[ -n "$SCRIPT_PATH" ] && [ -f "$STAGE_DIR/$SCRIPT_PATH" ] \
    || fail "manifest ScriptPath '$SCRIPT_PATH' is missing from the package"

VERSION="$(sed -n 's:.*ExtensionBundleVersion="\([^"]*\)".*:\1:p' "$MANIFEST" | head -1)"
say "Staged version $VERSION at $STAGE_DIR"
say "  MainPath   -> $MAIN_PATH (present)"
say "  ScriptPath -> $SCRIPT_PATH (present)"

if [ "$SIGN" -eq 0 ]; then
    say ""
    say "Stage-only run. Re-run with --sign to produce a .zxp."
    exit 0
fi

# ── 3. Sign ──────────────────────────────────────────────────────────
if [ -z "$SIGNCMD" ]; then
    fail "ZXPSignCmd not found.
  Download it once from
    https://github.com/Adobe-CEP/CEP-Resources/tree/master/ZXPSignCMD
  then put it on PATH or set ZXPSIGNCMD=/path/to/ZXPSignCmd
  The staged package is ready at $STAGE_DIR"
fi

CERT_PASSWORD="${CERT_PASSWORD:-}"
[ -n "$CERT_PASSWORD" ] || fail "set CERT_PASSWORD for the signing certificate"

if [ ! -f "$CERT_PATH" ]; then
    say "No certificate at $CERT_PATH — creating a self-signed one."
    say "  A self-signed cert is enough for Illustrator to load the extension;"
    say "  it is not a trusted publisher identity."
    "$SIGNCMD" -selfSignedCert US CA "Illustrator MCP" "Illustrator MCP" \
        "$CERT_PASSWORD" "$CERT_PATH"
fi

say "Signing..."
rm -f "$ZXP_OUT"
"$SIGNCMD" -sign "$STAGE_DIR" "$ZXP_OUT" "$CERT_PATH" "$CERT_PASSWORD" -tsa http://timestamp.digicert.com

say "Verifying..."
"$SIGNCMD" -verify "$ZXP_OUT" -certInfo

say ""
say "Signed package: $ZXP_OUT"
say "Install it with Adobe's UPIA / Anastasiy's Extension Manager / ZXPInstaller."
