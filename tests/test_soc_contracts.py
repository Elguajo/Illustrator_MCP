"""
test_soc_contracts.py - Contract integration tests (no Illustrator required).

Ensures consistency across the SOC registry layers:
  1. contracts.py (Python SSOT)
  2. contracts.jsx OP_PARAM_SCHEMAS (compiled from the SSOT)
  3. JSX handler registrations (via source grep — constrained format)
  4. OP_CLASS classification (via source grep)

These tests catch drift without requiring a running Illustrator instance.

Layer 2 used to read a generated op_schemas.json. That file was an orphan —
test_audit_fixes asserts it must not exist — so both checks against it skipped
permanently and the drift they were meant to catch went unguarded. They now run
against contracts.jsx, which is the artefact ExtendScript actually loads.
"""

import json
import re
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent / "illustrator_mcp" / "resources" / "scripts"
CONTRACTS_JSX_PATH = SCRIPTS_DIR / "contracts.jsx"

# Ops that are Python-only (no JSX handler expected)
KNOWN_SERVER_SIDE = {"path_boolean"}

# Ops that are internal helpers (registered but not in contracts.py)
# These should eventually be removed or formalized
KNOWN_INTERNAL = set()  # None currently — both undocumented ops now have schemas

# Ops that are readonly but whose name doesn't match readonly prefixes
KNOWN_READONLY = {"style_snapshot", "layer_list"}


def _get_contract_ops() -> set:
    """Get all op names from contracts.py."""
    from illustrator_mcp.schemas.contracts import OP_SCHEMAS
    return {op.name for op in OP_SCHEMAS}


def _get_jsx_schema_ops() -> set:
    """Get all op names from the OP_PARAM_SCHEMAS block in contracts.jsx."""
    content = CONTRACTS_JSX_PATH.read_text(encoding="utf-8")
    start = content.index("var OP_PARAM_SCHEMAS = {")
    # The block ends at the first line that closes it at column 0.
    end = content.index("\n};", start)
    block = content[start:end]
    # Op keys sit at a fixed indent of one level inside the object literal.
    return set(re.findall(r'^    "([a-z_]+)":\s*\{', block, re.MULTILINE))


def _get_jsx_registered_ops() -> set:
    """Get all registered op handler names from JSX source files."""
    ops = set()
    pattern = re.compile(r'registerOpHandler\(\s*"([^"]+)"')
    for jsx_file in SCRIPTS_DIR.glob("ops_*.jsx"):
        content = jsx_file.read_text(encoding="utf-8")
        for match in pattern.finditer(content):
            ops.add(match.group(1))
    return ops


def _get_op_class_entries() -> set:
    """Get all ops listed in OP_CLASS from ops_core.jsx."""
    core_path = SCRIPTS_DIR / "ops_core.jsx"
    content = core_path.read_text(encoding="utf-8")
    # Match entries like: "element_create": "doc"
    pattern = re.compile(r'"([a-z_]+)":\s*"(doc|session)"')
    return {match.group(1) for match in pattern.finditer(content)}


class TestContractSync:
    """Each contract op should have a corresponding JSX handler (or be server-side)."""

    def test_every_contract_has_jsx_schema(self):
        contract_ops = _get_contract_ops()
        jsx_schema_ops = _get_jsx_schema_ops()
        missing = contract_ops - jsx_schema_ops - KNOWN_SERVER_SIDE
        assert not missing, (
            f"Ops in contracts.py but missing from contracts.jsx: {sorted(missing)}\n"
            "Recompile: python -m illustrator_mcp.tools.compile_contracts"
        )

    def test_every_jsx_schema_has_contract(self):
        contract_ops = _get_contract_ops()
        jsx_schema_ops = _get_jsx_schema_ops()
        extra = jsx_schema_ops - contract_ops
        assert not extra, (
            f"Ops in contracts.jsx but missing from contracts.py: {sorted(extra)}\n"
            "contracts.jsx is generated; edit contracts.py and recompile."
        )

    def test_every_contract_has_jsx_handler(self):
        contract_ops = _get_contract_ops()
        jsx_ops = _get_jsx_registered_ops()
        missing = contract_ops - jsx_ops - KNOWN_SERVER_SIDE
        assert not missing, (
            f"Ops in contracts.py but no JSX handler: {sorted(missing)}"
        )

    def test_every_jsx_handler_has_contract(self):
        contract_ops = _get_contract_ops()
        jsx_ops = _get_jsx_registered_ops()
        extra = jsx_ops - contract_ops - KNOWN_INTERNAL
        assert not extra, (
            f"JSX handlers registered but no contract in contracts.py: {sorted(extra)}"
        )


class TestOpClassification:
    """Verify OP_CLASS covers all mutating operations."""

    def test_known_mutating_ops_classified(self):
        """Every JSX handler that creates/modifies/deletes should be in OP_CLASS."""
        op_class = _get_op_class_entries()
        jsx_ops = _get_jsx_registered_ops()

        # Ops known to be readonly (assert/measure/snapshot/hash)
        readonly_prefixes = ("assert_", "measure_", "snapshot_", "hash_")
        readonly_ops = {op for op in jsx_ops if any(op.startswith(p) for p in readonly_prefixes)}

        # Ops that are not readonly and not classified
        mutating_candidates = jsx_ops - readonly_ops - KNOWN_READONLY
        unclassified = mutating_candidates - op_class
        assert not unclassified, (
            f"Potentially mutating ops not in OP_CLASS: {sorted(unclassified)}\n"
            "Add them to OP_CLASS in ops_core.jsx or rename with readonly prefix"
        )

    def test_no_readonly_ops_classified_as_doc(self):
        """Assert ops should not be classified as 'doc' (they don't mutate)."""
        op_class = _get_op_class_entries()
        readonly_prefixes = ("assert_", "measure_", "snapshot_", "hash_")
        misclassified = {
            op for op in op_class
            if any(op.startswith(p) for p in readonly_prefixes)
        }
        assert not misclassified, (
            f"Readonly ops incorrectly classified in OP_CLASS: {sorted(misclassified)}"
        )
