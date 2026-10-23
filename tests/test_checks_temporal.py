import pandas as pd

from loci import synthetic as syn
from loci.checks.core import Layer
from loci.checks.temporal import check_temporal_coverage

LAYER = Layer("stack", syn.time_stack())


def test_monthly_series_passes() -> None:
    out = check_temporal_coverage(LAYER, syn.time_stack()["time"].values)

    assert out.status == "pass"
    assert out.message == (
        f"{syn.TS_STEPS} time steps from {syn.TS_START} to {syn.TS_END}, evenly spaced "
        "(median step 31 days)."
    )


def test_gaps_warn_with_the_largest() -> None:
    out = check_temporal_coverage(LAYER, syn.gappy_time_stack()["time"].values)

    assert out.status == "warn"
    assert out.message == (
        "2 gaps longer than 1.5x the median step of 31 days "
        "(largest: 2024-07-15 to 2024-09-15, 62 days)."
    )
    gaps = out.details["gaps"]
    assert isinstance(gaps, list)
    assert [g["days"] for g in gaps if isinstance(g, dict)] == sorted(
        syn.GAPPY_GAP_DAYS, reverse=True
    )


def test_short_steps_and_repeats_warn() -> None:
    times = list(pd.date_range("2024-01-01", periods=6, freq="10D"))
    times += [pd.Timestamp("2024-01-03"), pd.Timestamp("2024-01-21")]

    out = check_temporal_coverage(LAYER, times)

    assert out.status == "warn"
    # Unique steps: 2, 8, 10, 10, 10, 10 days; only the 2-day step is under half the median.
    assert "1 step is under half the median step." in out.message
    assert "1 timestamp is repeated." in out.message


def test_single_date_and_no_dates() -> None:
    single = check_temporal_coverage(LAYER, [pd.Timestamp("2024-06-30")])
    none = check_temporal_coverage(LAYER, [])

    assert single.status == "pass"
    assert none.status == "warn"


def test_vector_time_columns_work_too() -> None:
    surveyed = syn.polygons()["surveyed"]  # the 1st of each month, Jan to Apr

    out = check_temporal_coverage(Layer("parcels", syn.polygons()), surveyed)

    assert out.status == "pass"
    assert out.details["steps"] == 4
