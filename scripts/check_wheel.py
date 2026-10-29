"""Check a built wheel before release: it holds the web app, and a tag names its version.

python scripts/check_wheel.py dist [v0.4.0]
"""

import sys
import zipfile
from pathlib import Path


def main(folder: str, ref: str = "") -> None:
    (wheel,) = Path(folder).glob("loci-*.whl")
    names = zipfile.ZipFile(wheel).namelist()
    if "loci/web/static/index.html" not in names:
        sys.exit(f"{wheel.name} has no web app: run npm run build in app/ before uv build.")
    version = wheel.name.split("-")[1]
    if ref.startswith("v") and ref != f"v{version}":
        sys.exit(f"The tag {ref} does not match the wheel's version {version} (pyproject.toml).")
    print(f"{wheel.name}: web app included, {len(names)} files.")


if __name__ == "__main__":
    main(*sys.argv[1:])
