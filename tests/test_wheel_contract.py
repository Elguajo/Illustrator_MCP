"""The release gate detects both omitted and stale runtime resources."""

import importlib.util
from pathlib import Path
import zipfile

import pytest

_spec = importlib.util.spec_from_file_location("wheel_gate", Path(__file__).resolve().parent.parent / "scripts/check_wheel.py")
_gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_gate)


@pytest.mark.parametrize("contents,error", [(None, "Missing"), (b"old", "Stale"), (b"current", None)])
def test_wheel_runtime_resource_gate(tmp_path, contents, error):
    resource = tmp_path / "illustrator_mcp/resources/scripts/manifest.json"
    resource.parent.mkdir(parents=True)
    resource.write_bytes(b"current")
    wheel = tmp_path / "package.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        if contents is not None:
            archive.writestr("illustrator_mcp/resources/scripts/manifest.json", contents)
    if error:
        with pytest.raises(ValueError, match=error):
            _gate.check_wheel(wheel, tmp_path)
    else:
        assert _gate.check_wheel(wheel, tmp_path) == 1
