"""Claude Code hook: validate every data_ref a loci tool makes, automatically (D-038).

.claude/settings.json runs `loci-hook` after the loci MCP tools that make data_refs (load,
stac_search and run). It validates each new data_ref, unless run has already done so, and tells
Claude the result. LOCI_VALIDATION_HOOK sets what happens:

    report  (default) the result is added next to the tool's output, and Claude carries on
    block   a failed validation goes back to Claude as a problem to resolve before going on
    off     nothing runs

A failure is also shown to the user. Input and output follow the PostToolUse hook format in
https://code.claude.com/docs/en/hooks.
"""

import json
import os
import sys
from typing import Any

from loci import provenance
from loci.models import ValidationReport
from loci.tools.validate import validate
from loci.workspace import REF_PATTERN, Workspace

MODE_ENV = "LOCI_VALIDATION_HOOK"
MODES = ("report", "block", "off")
DEFAULT_MODE = "report"
MAX_CHECKS_LISTED = 5


def main() -> None:
    event = json.load(sys.stdin)
    mode = os.environ.get(MODE_ENV, DEFAULT_MODE)
    try:
        output = respond(event, Workspace.from_env(), mode)
    except Exception as e:  # a broken hook must not break the session; say so instead
        output = {"systemMessage": f"loci validation hook failed: {type(e).__name__}: {e}"}
    if output:
        print(json.dumps(output))


def respond(event: dict[str, Any], ws: Workspace, mode: str) -> dict[str, Any] | None:
    """The hook's JSON output for one PostToolUse event, or None to stay silent."""
    mode = mode.strip().lower()
    if mode == "off":
        return None
    if mode not in MODES:
        return {"systemMessage": f"{MODE_ENV}={mode!r} is not one of {list(MODES)}; not validated."}
    refs = new_refs(event.get("tool_response"))
    if not refs:
        return None

    reports = {ref: _report(ws, ref) for ref in refs}
    text = "\n".join(_describe(ref, report) for ref, report in reports.items())
    failed = [ref for ref, report in reports.items() if report.status == "fail"]
    output: dict[str, Any] = {}
    if failed:
        output["systemMessage"] = f"loci: {', '.join(failed)} failed validation."
    if mode == "block" and failed:
        output["decision"] = "block"
        output["reason"] = (
            f"{text}\nResolve this before building on {', '.join(failed)}: fix the input or the "
            "call, or tell the user why the result is still usable."
        )
    else:
        output["hookSpecificOutput"] = {"hookEventName": "PostToolUse", "additionalContext": text}
    return output


def new_refs(response: Any) -> list[str]:
    """The data_refs a tool made: `data_ref` (load, stac_search) and `outputs[].data_ref` (run)."""
    result = _result(response)
    if result is None:
        return []
    outputs = result.get("outputs")
    candidates = [result.get("data_ref")]
    if isinstance(outputs, list):
        candidates += [o.get("data_ref") for o in outputs if isinstance(o, dict)]
    refs = [c for c in candidates if isinstance(c, str) and REF_PATTERN.fullmatch(c)]
    return list(dict.fromkeys(refs))


def _result(response: Any) -> dict[str, Any] | None:
    """The tool's result object, from an MCP response in any of the shapes it may arrive in:
    a result with structured content, a list of content blocks, or JSON text."""
    if isinstance(response, str):
        try:
            return _result(json.loads(response))
        except ValueError:
            return None
    if isinstance(response, list):
        for block in response:
            if isinstance(block, dict) and block.get("type") == "text":
                found = _result(block.get("text"))
                if found is not None:
                    return found
        return None
    if not isinstance(response, dict):
        return None
    for key in ("structuredContent", "structured_content"):
        if isinstance(response.get(key), dict):
            return dict(response[key])
    if "data_ref" in response or "outputs" in response:
        return response
    return _result(response.get("content"))


def _report(ws: Workspace, ref: str) -> ValidationReport:
    """The latest validation of `ref` (run validates its outputs), or a new one."""
    record = provenance.read(ws, ref)
    if record is not None and record.validation is not None:
        return record.validation
    return validate(ws, [ref])


def _describe(ref: str, report: ValidationReport) -> str:
    counts = ", ".join(f"{report.counts.get(s, 0)} {s}" for s in ("pass", "warn", "fail"))
    lines = [f"loci validated {ref} automatically: {report.status} ({counts})."]
    problems = [c for c in report.checks if c.status != "pass"]
    for check in problems[:MAX_CHECKS_LISTED]:
        hint = f" Hint: {check.hint}" if check.hint else ""
        lines.append(f"- {check.status} {check.check}: {check.message}{hint}")
    if len(problems) > MAX_CHECKS_LISTED:
        lines.append(f"- and {len(problems) - MAX_CHECKS_LISTED} more; call validate for all.")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
