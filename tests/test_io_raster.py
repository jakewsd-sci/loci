import subprocess
import sys
from pathlib import Path

import dask.array
import numpy as np
import pytest

from loci import synthetic as syn
from loci.errors import LoadError
from loci.io import Format, read
from loci.io.raster import open_raster, overview_factors
from loci.models import ReadOptions

GEOTIFF = Format("raster", "geotiff")


def test_bands_are_named_from_descriptions_and_kept_raw(multiband_tif: Path) -> None:
    result = read(str(multiband_tif), GEOTIFF, ReadOptions())
    ds = result.data

    assert list(ds.data_vars) == ["red", "nir"]
    assert ds["red"].dtype == "uint16"
    assert ds["red"].rio.nodata == syn.MB_NODATA
    assert result.crs_source == "file"
    assert result.warnings == []


def test_bands_without_descriptions_keep_numbered_names(ramp_tif: Path) -> None:
    ds = read(str(ramp_tif), GEOTIFF, ReadOptions()).data

    assert list(ds.data_vars) == ["band_1"]
    assert ds["band_1"].rio.nodata == syn.RAMP_NODATA


def test_rasters_open_lazily(ramp_tif: Path) -> None:
    ds = read(str(ramp_tif), GEOTIFF, ReadOptions()).data

    assert isinstance(ds["band_1"].data, dask.array.Array)


def test_missing_crs_is_reported(bare_tif: Path) -> None:
    result = read(str(bare_tif), GEOTIFF, ReadOptions())

    assert result.data.rio.crs is None
    assert result.crs_source is None
    assert any("declares no CRS" in w for w in result.warnings)


def test_crs_argument_fills_a_missing_crs(bare_tif: Path) -> None:
    result = read(str(bare_tif), GEOTIFF, ReadOptions(crs="EPSG:32633"))

    assert result.data.rio.crs.to_epsg() == 32633
    assert result.crs_source == "argument"
    assert result.warnings == []


def test_crs_argument_matching_the_file_is_accepted(ramp_tif: Path) -> None:
    result = read(str(ramp_tif), GEOTIFF, ReadOptions(crs="EPSG:32633"))

    assert result.crs_source == "file"


def test_crs_argument_never_overrides_a_declared_crs(ramp_tif: Path) -> None:
    with pytest.raises(LoadError, match="never reprojects"):
        read(str(ramp_tif), GEOTIFF, ReadOptions(crs="EPSG:4326"))


def test_unrecognised_crs_is_an_error(bare_tif: Path) -> None:
    with pytest.raises(LoadError, match="Unrecognised CRS"):
        read(str(bare_tif), GEOTIFF, ReadOptions(crs="EPSG:not-a-code"))


def test_missing_file_is_a_load_error(tmp_path: Path) -> None:
    with pytest.raises(LoadError, match="Cannot open"):
        read(str(tmp_path / "missing.tif"), GEOTIFF, ReadOptions())


def test_vector_options_are_rejected_for_rasters(ramp_tif: Path) -> None:
    with pytest.raises(LoadError, match="layer="):
        read(str(ramp_tif), GEOTIFF, ReadOptions(layer="a"))
    with pytest.raises(LoadError, match="x_col="):
        read(str(ramp_tif), GEOTIFF, ReadOptions(x_col="lon", y_col="lat"))


def test_overviews_open_at_reduced_resolution(overview_tif: Path) -> None:
    assert overview_factors(str(overview_tif)) == list(syn.OV_FACTORS)

    ds = open_raster(str(overview_tif), "geotiff", ReadOptions(), overview_level=1).data

    assert ds.sizes["x"] == syn.OV_SIZE // syn.OV_FACTORS[1]
    assert float(ds["band_1"].mean()) == pytest.approx(syn.OV_MEAN)


def test_band_names_are_kept_at_overview_levels(tmp_path: Path) -> None:
    path = tmp_path / "described_overviews.tif"
    syn._write_geotiff(
        path,
        np.ones((2, 64, 64), dtype="uint16"),
        crs=syn.UTM33N,
        res=10.0,
        nodata=0,
        descriptions=["red", "nir"],
        block=32,
        overviews=(2,),
    )

    ds = open_raster(str(path), "geotiff", ReadOptions(), overview_level=0).data

    assert list(ds.data_vars) == ["red", "nir"]


def test_process_exits_cleanly_with_rasters_open(ramp_tif: Path) -> None:
    """Cached file handles must not print errors at interpreter exit (stderr is captured by run)."""
    code = (
        "from loci.io import Format, read; from loci.models import ReadOptions; "
        f"ds = read({str(ramp_tif)!r}, Format('raster', 'geotiff'), ReadOptions()).data; "
        "ds.load()"
    )
    proc = subprocess.run(
        [sys.executable, "-W", "ignore::PendingDeprecationWarning", "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )

    assert proc.stderr == ""
