# Tests for Illustrator MCP

This directory contains unit tests for the Illustrator MCP server.

## Running Tests

```bash
# Install project and test dependencies
python -m pip install -e ".[dev]"

# Run all tests
python -m pytest tests/ -v

# Run specific test file
python -m pytest tests/test_documents.py -v

# Run with coverage
python -m pytest tests/ --cov=illustrator_mcp --cov-report=html
```

## Test Structure

- `test_documents.py` - Document operation tool tests
- `test_execute.py` - Raw ExtendScript execution tests
- `test_doc_model.py` - Document inspection and artboard tests
- `test_automation_contract.py` - Result, recovery, and instruction contracts
- `test_wheel_contract.py` - Built-wheel resource checks
- `conftest.py` - Shared test fixtures
