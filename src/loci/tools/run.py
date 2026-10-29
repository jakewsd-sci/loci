"""The `run` tool: execute a Python script in its own process and folder, and keep what it made.

Each run gets <workspace>/runs/<run_id>/ holding the exact script, its declared inputs and
parameters, captured stdout and stderr, an outputs/ folder and run.json. After a successful run,
every file in outputs/ is registered (spatial files as data_refs), given a provenance record and a
sidecar, and validated. Failed or timed-out runs register nothing.
"""

import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from loci import provenance
from loci.errors import LociError
from loci.io import detect_format, read
from loci.models import (
    ReadOptions,
    RunOutput,
    RunResult,
    RunStatus,
    SourceInput,
    ValidationReport,
)
from loci.script import INPUTS_FILE, OUTPUTS_DIR, PARAMETERS_FILE, RUN_DIR_ENV, WARNINGS_FILE
from loci.tools.validate import check_together, validate
from loci.workspace import ROOT_ENV, Workspace

DEFAULT_TIMEOUT = 600.0
MAX_TIMEOUT = 3600.0
TAIL_CHARS = 3000
MAX_PROBLEMS = 3
# Files that belong to another output (shapefile parts, GDAL side files) are not outputs.
COMPANIONS = (".dbf", ".shx", ".prj", ".cpg", ".qix", ".sbn", ".sbx", ".aux.xml", ".ovr")


def run(
    ws: Workspace,
    *,
    code: str | None = None,
    path: str | None = None,
    inputs: dict[str, str] | None = None,
    parameters: dict[str, Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> RunResult:
    """Run a Python script with the project's environment; register what it saves.

    Args:
        code: The script's source. Give this or path.
        path: A .py file to run; it is copied into the run folder first.
        inputs: name -> data_ref. The script reads them with loci.script.read(name), which gives
            physical values (scale and offset applied, nodata as NaN); read(name, raw=True)
            gives stored values.
        parameters: JSON-serialisable values, read with loci.script.parameters().
        timeout: Seconds before the script is stopped (at most 3600).

    Declare every input and read it through loci.script: files a script opens by itself do not
    appear in provenance. Two or more inputs are first checked against each other (same CRS,
    overlapping extents, aligned grids) and the result is input_checks. The run goes ahead
    whatever they find: reproject, resample or clip explicitly in the script, and say so.

    The script saves outputs with loci.script.save(obj, filename) (or writes into
    loci.script.output_path(filename)). After a successful run, spatial outputs become data_refs
    and are validated; every output gets a provenance record naming the script, its hash, the
    inputs and the parameters.
    """
    source = _source(code, path)
    declared = inputs or {}
    if not 0 < timeout <= MAX_TIMEOUT:
        raise LociError(f"timeout must be between 0 and {MAX_TIMEOUT:g} seconds.")
    records = {name: ws.record(ref) for name, ref in declared.items()}  # unknown refs fail here
    input_checks = check_together(ws, list(declared.values()))
    try:
        parameter_text = json.dumps(parameters or {})
    except TypeError as e:
        raise LociError(f"parameters must be JSON-serialisable: {e}") from e

    run_id, run_dir = _new_run_dir(ws)
    script_path = run_dir / "script.py"
    script_path.write_text(source)
    (run_dir / INPUTS_FILE).write_text(json.dumps(declared))
    (run_dir / PARAMETERS_FILE).write_text(parameter_text)
    (run_dir / OUTPUTS_DIR).mkdir()

    started_at = provenance.now()
    status, exit_code, stdout, stderr, seconds = _execute(ws, run_dir, timeout)
    (run_dir / "stdout.txt").write_text(stdout)
    (run_dir / "stderr.txt").write_text(stderr)

    # A script file is recorded by its absolute path, so lineage finds it from any directory.
    script_file = str(Path(path).expanduser().resolve()) if path is not None else None
    context = provenance.new_record(
        produced_by="run",
        call=provenance.tool_call(
            "run",
            {"path": script_file, "inputs": declared, "parameters": parameters, "timeout": timeout},
        ),
        inputs=[
            SourceInput(name=name, data_ref=ref, uri=records[name].source.uri)
            for name, ref in declared.items()
        ],
        parameters=parameters,
        run_id=run_id,
        script=str(script_path),
        code_sha256=provenance.sha256_text(source),
    )
    files = _output_files(run_dir / OUTPUTS_DIR)
    if status == "ok":
        outputs = [_register(ws, file, context) for file in files]
    else:
        outputs = [
            RunOutput(file=f.name, note="Not registered: the run did not finish successfully.")
            for f in files
        ]

    result = RunResult(
        run_id=run_id,
        status=status,
        exit_code=exit_code,
        seconds=round(seconds, 2),
        run_dir=str(run_dir),
        stdout=_tail(stdout),
        stderr=_tail(stderr),
        outputs=outputs,
        input_checks=input_checks,
        warnings=_input_warnings(input_checks)
        + _warnings(status, exit_code, timeout, outputs)
        + _script_warnings(run_dir),
        notes=_notes(status, outputs),
    )
    (run_dir / "run.json").write_text(
        json.dumps(
            {
                **result.model_dump(mode="json", exclude={"stdout", "stderr"}),
                "code_sha256": context.code_sha256,
                "inputs": declared,
                "parameters": parameters or {},
                "started_at": started_at,
                "finished_at": context.created_at,
            },
            indent=2,
        )
    )
    return result


def _source(code: str | None, path: str | None) -> str:
    if (code is None) == (path is None):
        raise LociError("Pass the script as code= or path=, not both or neither.")
    if code is not None:
        return code
    assert path is not None
    file = Path(path).expanduser()
    if not file.is_file():
        raise LociError(f"No such script: {path!r}.", hint=f"Paths are resolved from {Path.cwd()}.")
    return file.read_text()


def _new_run_dir(ws: Workspace) -> tuple[str, Path]:
    while True:
        run_id = f"run-{secrets.token_hex(4)}"
        run_dir = ws.runs_dir / run_id
        try:
            run_dir.mkdir()
        except FileExistsError:
            continue
        return run_id, run_dir


def _execute(
    ws: Workspace, run_dir: Path, timeout: float
) -> tuple[RunStatus, int | None, str, str, float]:
    env = {
        **os.environ,
        ROOT_ENV: str(ws.root),
        RUN_DIR_ENV: str(run_dir),
        "PYTHONUNBUFFERED": "1",
        "MPLBACKEND": "Agg",
    }
    started = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, "script.py"],
            cwd=run_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        # Output captured before the timeout arrives as bytes, whatever text= says.
        return "timeout", None, _text(e.stdout), _text(e.stderr), time.monotonic() - started
    status: RunStatus = "ok" if proc.returncode == 0 else "error"
    return status, proc.returncode, proc.stdout, proc.stderr, time.monotonic() - started


def _output_files(outputs: Path) -> list[Path]:
    return sorted(
        f
        for f in outputs.iterdir()
        if f.is_file()
        and not f.name.startswith(".")
        and not f.name.endswith(provenance.SIDECAR_SUFFIX)
        and not f.name.lower().endswith(COMPANIONS)
    )


def _register(ws: Workspace, file: Path, context: Any) -> RunOutput:
    """Register one output: as a data_ref if loci can read it, else as a plain file."""
    record = context.model_copy(update={"path": str(file), "sha256": provenance.sha256_file(file)})
    try:
        loaded = read(str(file), detect_format(str(file)), ReadOptions())
    except LociError as e:
        provenance.write(ws, record)
        return RunOutput(file=file.name, note=f"Kept as a file, not a data_ref: {e.message}")
    ref = ws.register(loaded, str(file))
    provenance.write(ws, record.model_copy(update={"data_ref": ref.data_ref}))
    report = validate(ws, [ref.data_ref])
    problems = [c.message for c in report.checks if c.status != "pass"][:MAX_PROBLEMS]
    return RunOutput(
        file=file.name,
        data_ref=ref.data_ref,
        kind=ref.kind,
        validation=report.status,
        problems=problems,
    )


def _warnings(
    status: RunStatus, exit_code: int | None, timeout: float, outputs: list[RunOutput]
) -> list[str]:
    warnings = []
    if status == "error":
        warnings.append(f"The script failed with exit code {exit_code}; see stderr.")
    elif status == "timeout":
        warnings.append(f"The script was stopped after {timeout:g} s.")
    failed = [o.file for o in outputs if o.validation == "fail"]
    if failed:
        warnings.append(f"Output(s) {failed} failed validation; see their problems.")
    return warnings


def _input_warnings(report: ValidationReport | None) -> list[str]:
    """One warning naming what the inputs disagree on, if anything."""
    if report is None or report.status == "pass":
        return []
    found = " ".join(c.message for c in report.checks if c.status != "pass")
    return [f"The inputs were checked together: {report.status}. {found} See input_checks."]


def _script_warnings(run_dir: Path) -> list[str]:
    """Warnings the script recorded through loci.script.warn (e.g. dropped scaling)."""
    path = run_dir / WARNINGS_FILE
    return [line for line in path.read_text().splitlines() if line] if path.exists() else []


def _notes(status: RunStatus, outputs: list[RunOutput]) -> list[str]:
    if status == "ok" and not outputs:
        return [
            "The script saved no outputs. Use loci.script.save(obj, 'name.tif') to keep results."
        ]
    return []


def _text(output: bytes | str | None) -> str:
    if output is None:
        return ""
    return output.decode(errors="replace") if isinstance(output, bytes) else output


def _tail(text: str) -> str:
    return text if len(text) <= TAIL_CHARS else "…" + text[-TAIL_CHARS:]
