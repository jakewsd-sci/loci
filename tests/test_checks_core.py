import numpy as np
import pytest

from loci import synthetic as syn
from loci.checks.core import Layer, build_report, layer_bounds, layer_crs, result, worst
from loci.models import CheckResult, CheckStatus


def _check(status: CheckStatus) -> CheckResult:
    return CheckResult(check="x", status=status, data_refs=["a"], message="m")


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([], "pass"),
        (["pass", "pass"], "pass"),
        (["pass", "warn"], "warn"),
        (["warn", "fail"], "fail"),
    ],
)
def test_worst_status(statuses: list[CheckStatus], expected: CheckStatus) -> None:
    assert worst(statuses) == expected


def test_report_counts_statuses_and_takes_the_worst() -> None:
    report = build_report(["a"], [_check("pass"), _check("warn"), _check("pass")])

    assert report.status == "warn"
    assert report.counts == {"pass": 2, "warn": 1, "fail": 0}


def test_result_keeps_hints_only_for_problems() -> None:
    layer = Layer("raster-00000001", syn.time_stack())

    passed = result("c", "pass", [layer], "fine", hint="unused")
    warned = result("c", "warn", [layer], "look", hint="do this")

    assert passed.hint is None
    assert warned.hint == "do this"
    assert warned.data_refs == ["raster-00000001"]


def test_result_details_become_plain_json() -> None:
    layer = Layer("a", syn.time_stack())

    out = result("c", "pass", [layer], "m", details={"n": np.int64(3), "box": (1.0, np.float32(2))})

    assert out.details == {"n": 3, "box": [1.0, 2.0]}


def test_layer_crs_and_bounds_for_rasters_and_vectors() -> None:
    raster = Layer("r", syn.time_stack())
    vector = Layer("v", syn.polygons())
    x0, y0 = syn.ORIGIN

    assert layer_crs(raster) == layer_crs(vector)
    assert layer_bounds(raster) == (x0, y0 - 100.0, x0 + 100.0, y0)
    assert layer_bounds(vector) == (x0, y0 - 200.0, x0 + 200.0, y0)
