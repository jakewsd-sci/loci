import json

import pytest
from pydantic import ValidationError

from loci.errors import LociError, UnknownRefError
from loci.models import (
    CRSInfo,
    Extent,
    InvalidGeometries,
    RasterSummary,
    ReadOptions,
    RefSummary,
    VectorSummary,
)

UTM33 = CRSInfo(epsg=32633, name="WGS 84 / UTM zone 33N", is_geographic=False, units="metre")


def _raster_summary() -> RasterSummary:
    return RasterSummary(
        crs=UTM33,
        extent=Extent(bounds=(0.0, 0.0, 10.0, 10.0)),
        resolution=(1.0, 1.0),
        dims={"y": 10, "x": 10},
        bands=[],
    )


def _vector_summary() -> VectorSummary:
    return VectorSummary(
        crs=None,
        extent=None,
        feature_count=0,
        geometry_types={},
        null_geometries=0,
        empty_geometries=0,
        invalid_geometries=InvalidGeometries(count=0),
        attributes=[],
    )


@pytest.mark.parametrize("summary", [_raster_summary(), _vector_summary()])
def test_ref_summary_round_trips_through_json(summary: RasterSummary | VectorSummary) -> None:
    result = RefSummary(data_ref="x-00000000", kind=summary.kind, summary=summary)

    restored = RefSummary.model_validate_json(result.model_dump_json())

    assert restored == result
    assert type(restored.summary) is type(summary)


def test_ref_summary_json_is_plain() -> None:
    result = RefSummary(data_ref="raster-00000000", kind="raster", summary=_raster_summary())

    data = json.loads(result.model_dump_json())

    assert data["summary"]["extent"]["bounds"] == [0.0, 0.0, 10.0, 10.0]
    assert data["warnings"] == []


def test_models_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ReadOptions.model_validate({"layer": "a", "colour": "red"})


def test_error_message_includes_hint() -> None:
    err = UnknownRefError("No data_ref 'raster-1'.", hint="Call load first.")

    assert isinstance(err, LociError)
    assert str(err) == "No data_ref 'raster-1'. Hint: Call load first."
    assert str(LociError("Plain.")) == "Plain."
