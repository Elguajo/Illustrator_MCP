"""Reject a wheel with missing or stale runtime data (run after pip wheel)."""

import argparse
from pathlib import Path
import zipfile


def check_wheel(wheel: Path, root: Path) -> int:
    package = root / "illustrator_mcp"
    required = sorted(path for directory in (package / "resources", package / "schemas")
                      for path in directory.rglob("*")
                      if path.is_file() and path.suffix in {".jsx", ".json", ".md"})
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        for path in required:
            name = path.relative_to(root).as_posix()
            if name not in names:
                raise ValueError("Missing runtime resource in wheel: " + name)
            if archive.read(name) != path.read_bytes():
                raise ValueError("Stale runtime resource in wheel: " + name)
    return len(required)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()
    count = check_wheel(args.wheel, Path(__file__).resolve().parent.parent)
    print(f"PASS: {count} runtime resources match in {args.wheel.name}")
