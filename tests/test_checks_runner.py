import pytest

from loci import synthetic as syn
from loci.checks.core import Layer
from loci.checks.runner import CheckOptions, run_checks
from loci.errors import LociError


def _ids(layers: list[Layer], options: CheckOptions | None = None) -> list[tuple[str, str]]:
    return [(r.check, r.status) for r in run_checks(layers, options or CheckOptions())]


def test_raster_gets_raster_checks_and_named_variables() -> None:
    assert _ids([Layer("stack", syn.time_stack())]) == [
        ("crs_defined", "pass"),
        ("nodata_defined", "pass"),
        ("valid_fraction", "pass"),
        ("value_range", "pass"),  # band "ndvi" is recognised by name
        ("temporal_coverage", "pass"),
    ]


def test_vector_gets_vector_checks() -> None:
    options = CheckOptions(variables={"score": "percent"}, time_column="surveyed")

    assert _ids([Layer("parcels", syn.polygons())], options) == [
        ("crs_defined", "pass"),
        ("geometry_valid", "pass"),
        ("value_range", "pass"),
        ("temporal_coverage", "pass"),
    ]


def test_several_inputs_are_also_checked_together() -> None:
    layers = [
        Layer("a", syn.time_stack()),
        Layer("b", syn.cloudy_time_stack()),
        Layer("c", syn.polygons()),
    ]

    cross = [
        c
        for c in _ids(layers)
        if c[0] in {"crs_consistent", "extents_overlap", "resolution_consistent"}
    ]

    assert cross == [
        ("crs_consistent", "pass"),
        ("extents_overlap", "pass"),
        ("resolution_consistent", "pass"),
    ]


def test_area_conservation_runs_against_the_reference() -> None:
    options = CheckOptions(
        area_reference=Layer("catchments", syn.catchments()), area_by="catchment"
    )

    results = run_checks([Layer("landuse", syn.landuse_by_catchment())], options)

    assert results[-1].check == "area_conserved"
    assert results[-1].data_refs == ["landuse", "catchments"]
    assert results[-1].status == "pass"


@pytest.mark.parametrize(
    ("options", "message"),
    [
        (CheckOptions(variables={"ndvi": "greenness"}), "Unknown variable"),
        (CheckOptions(variables={"nvdi": "ndvi"}), "match no band or column"),
        (CheckOptions(min_valid_fraction=1.5), "between 0 and 1"),
        (CheckOptions(time_column="date"), "No vector input"),
        (CheckOptions(area_by="catchment"), "needs area_reference"),
    ],
)
def test_settings_that_cannot_apply_are_errors(options: CheckOptions, message: str) -> None:
    with pytest.raises(LociError, match=message):
        run_checks([Layer("stack", syn.time_stack())], options)


def test_area_reference_must_suit_the_inputs() -> None:
    raster = Layer("stack", syn.time_stack())
    vector = Layer("landuse", syn.polygons())

    with pytest.raises(LociError, match="must be a vector"):
        run_checks([vector], CheckOptions(area_reference=raster))
    with pytest.raises(LociError, match="at least one vector input"):
        run_checks([raster], CheckOptions(area_reference=vector))
    with pytest.raises(LociError, match="missing from"):
        run_checks([vector], CheckOptions(area_reference=vector, area_by="catchment"))
