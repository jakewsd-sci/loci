import math
from collections.abc import Iterator

import pandas as pd
import pytest

from loci.models import InvalidGeometry
from loci.summary.common import (
    counted,
    crs_info,
    extent,
    iso,
    sig,
    take_within_budget,
    truncate,
)


def _items(n: int, reason: str = "x") -> list[InvalidGeometry]:
    return [InvalidGeometry(row=i, reason=reason) for i in range(n)]


def test_take_within_budget_caps_count_and_bytes() -> None:
    size = len(_items(1)[0].model_dump_json())

    assert len(take_within_budget(_items(10), max_items=3)) == 3
    assert len(take_within_budget(_items(10), max_items=10, budget_bytes=4 * size)) == 4


def test_take_within_budget_always_keeps_the_first_item() -> None:
    assert len(take_within_budget(_items(3, "y" * 500), max_items=10, budget_bytes=10)) == 1


def test_take_within_budget_builds_items_lazily() -> None:
    built: list[int] = []

    def generate() -> Iterator[InvalidGeometry]:
        for i in range(100):
            built.append(i)
            yield InvalidGeometry(row=i, reason="x")

    take_within_budget(generate(), max_items=2)

    assert len(built) == 3


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (28.866070047722118, 28.8661),
        (123456789, 123457000.0),
        (-0.000123456789, -0.000123457),
        (0, 0.0),
        (49.5, 49.5),
    ],
)
def test_sig_rounds_to_six_significant_figures(value: float, expected: float) -> None:
    assert sig(value) == expected


@pytest.mark.parametrize("value", [None, math.nan, math.inf, -math.inf])
def test_sig_returns_none_for_missing_or_non_finite(value: float | None) -> None:
    assert sig(value) is None


def test_counted_matches_phrase_to_count() -> None:
    assert counted(1, "band is", "bands are") == "1 band is"
    assert counted(3, "band is", "bands are") == "3 bands are"


def test_truncate_marks_cut_text() -> None:
    assert truncate("short") == "short"
    cut = truncate("x" * 100)
    assert len(cut) == 40
    assert cut.endswith("…")


def test_iso_drops_midnight_and_subseconds() -> None:
    assert iso(pd.Timestamp("2024-01-15")) == "2024-01-15"
    assert iso(pd.Timestamp("2024-01-15T10:05:31.024")) == "2024-01-15T10:05:31"


def test_crs_info_for_projected_and_geographic_crs() -> None:
    utm = crs_info("EPSG:32633")
    wgs84 = crs_info("EPSG:4326")

    assert utm is not None and wgs84 is not None
    assert (utm.epsg, utm.is_geographic, utm.units) == (32633, False, "metre")
    assert (wgs84.epsg, wgs84.is_geographic, wgs84.units) == (4326, True, "degree")
    assert crs_info(None) is None


def test_extent_adds_wgs84_bounds() -> None:
    # 1 km square whose top-left is on the UTM 33N central meridian (15 E) near 37 N.
    result = extent((500_000, 4_099_000, 501_000, 4_100_000), "EPSG:32633")

    assert result.bounds == (500_000, 4_099_000, 501_000, 4_100_000)
    assert result.bounds_wgs84 is not None
    west, south, east, north = result.bounds_wgs84
    assert west == pytest.approx(15.0, abs=1e-6)
    assert east == pytest.approx(15.0 + 1000 / 88_800, abs=1e-3)  # ~88.8 km per degree at 37 N
    assert 37.0 < south < north < 37.1


def test_extent_without_crs_has_no_wgs84_bounds() -> None:
    assert extent((0, 0, 1, 1), None).bounds_wgs84 is None
