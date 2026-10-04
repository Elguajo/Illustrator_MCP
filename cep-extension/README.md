
# Illustrator MCP CEP Panel

This React/Vite panel runs inside Adobe Illustrator. It reads the authenticated
local bridge session and executes ExtendScript requests sent by the Python MCP
server. It is not a standalone web application.

## Development setup

From the repository root:

```bash
cd cep-extension
npm ci
npm run build
cd ..
./install-cep.sh
```

On Windows, run `install-cep.bat` as Administrator after building. The
development installers create a symlink into Adobe's CEP extensions directory
and enable `PlayerDebugMode`; restart Illustrator afterwards and open **Window >
Extensions > MCP Control**.

The panel connects only after an MCP client starts the Python server. Configure
Claude Desktop, Claude Code, or Codex as described in the repository
[Quick Start](../README.md#quick-start), then wait for the panel to show
**Connected**.

## Build and package

`npm run build` creates `dist/`. Do not distribute the source directory: an
unsigned CEP extension only loads in debug mode. To stage or sign a distributable
package, run these commands from the repository root:

```bash
scripts/package-cep.sh
scripts/package-cep.sh --sign
```

The second command needs Adobe `ZXPSignCmd`, a certificate password, and either
an existing certificate or permission to create the repository's self-signed
development certificate. See [Distributing the Panel](../README.md#distributing-the-panel)
for details.
