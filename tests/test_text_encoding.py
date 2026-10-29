"""loci reads and writes text as UTF-8, whatever the system's locale.

Python takes the locale's encoding when none is given, and on Windows that is usually cp1252, so a
script or record with "°C" in it would be written in one encoding and read in another.
"""

import ast
from pathlib import Path

import loci

TEXT_METHODS = {"read_text", "write_text"}
PROCESS_CALLS = {"run", "Popen", "check_output"}


def _unspecified(file: Path) -> list[int]:
    """Lines of calls that read or write text without saying its encoding."""
    found = []
    for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call) or any(k.arg == "encoding" for k in node.keywords):
            continue
        name = (
            node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        )
        keywords = {k.arg: k.value for k in node.keywords}
        if name in TEXT_METHODS:
            found.append(node.lineno)
        elif name == "open" and isinstance(node.func, ast.Name):
            mode = node.args[1] if len(node.args) > 1 else keywords.get("mode")
            if not (isinstance(mode, ast.Constant) and "b" in str(mode.value)):
                found.append(node.lineno)
        elif name == "open" and _text_mode(node.args[0] if node.args else keywords.get("mode")):
            found.append(node.lineno)  # Path.open; rasterio.open takes a path first, not a mode
        elif name in PROCESS_CALLS:
            text = keywords.get("text")
            if isinstance(text, ast.Constant) and text.value is True:
                found.append(node.lineno)
    return found


def _text_mode(mode: ast.expr | None) -> bool:
    """Whether a method's first argument is a text mode such as "a" or "w" (Path.open)."""
    if mode is None:
        return False
    value = mode.value if isinstance(mode, ast.Constant) else None
    return isinstance(value, str) and set(value) <= set("rwax+t") and "b" not in value


def test_text_is_read_and_written_as_utf8() -> None:
    package = Path(loci.__file__).parent
    found = {str(f.relative_to(package)): _unspecified(f) for f in sorted(package.rglob("*.py"))}

    assert {file: lines for file, lines in found.items() if lines} == {}
