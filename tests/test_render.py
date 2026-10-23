from pathlib import Path

import numpy as np
import pytest
from matplotlib.image import imread

from loci import synthetic as syn
from loci.render import render_raster, render_vector, stretch_limits


def test_stretch_uses_the_2nd_and_98th_percentiles() -> None:
    values = np.arange(101, dtype="float64")

    assert stretch_limits([values]) == (2.0, 98.0)
    assert stretch_limits([np.full(4, np.nan)]) is None
    assert stretch_limits([np.full(4, 7.0)]) == (7.0, 7.0)


def test_single_band_is_drawn_in_a_colormap_with_nodata_transparent(tmp_path: Path) -> None:
    values = np.tile(np.linspace(0, 1, 20), (10, 1))
    values[:3] = np.nan

    rendered = render_raster([values], tmp_path / "one.png")
    image = imread(tmp_path / "one.png")

    assert (rendered.width, rendered.height) == (20, 10)
    assert image.shape == (10, 20, 4)
    assert (image[:3, :, 3] == 0).all()
    assert (image[3:, :, 3] == 1).all()
    assert not np.allclose(image[5, 0, :3], image[5, -1, :3])  # low and high differ in colour


def test_three_bands_share_one_stretch(tmp_path: Path) -> None:
    red, green, blue = np.full((4, 4), 0.1), np.full((4, 4), 0.2), np.full((4, 4), 0.3)
    red[0, 0] = np.nan

    rendered = render_raster([red, green, blue], tmp_path / "rgb.png")
    image = imread(tmp_path / "rgb.png")

    assert rendered.stretch is not None
    assert image[0, 0, 3] == 0
    assert image[1, 1, 0] < image[1, 1, 1] < image[1, 1, 2]  # blue brightest, as in the data


def test_render_raster_takes_one_or_three_bands(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="one band or three"):
        render_raster([np.ones((2, 2))] * 2, tmp_path / "x.png")


def test_vectors_are_drawn_to_scale(tmp_path: Path) -> None:
    square_grid = render_vector(syn.polygons(), tmp_path / "grid.png", width=200)
    by_column = render_vector(syn.polygons(), tmp_path / "by.png", width=200, column="landuse")

    assert (square_grid.width, square_grid.height) == (200, 200)
    assert imread(tmp_path / "grid.png").shape[:2] == (200, 200)
    assert (tmp_path / "by.png").stat().st_size > 0
    assert by_column.height == 200


def test_geographic_layers_correct_for_latitude(tmp_path: Path) -> None:
    # The 200 m grid in degrees spans more degrees of longitude than latitude near 37 N.
    rendered = render_vector(syn.polygons().to_crs("EPSG:4326"), tmp_path / "geo.png", width=200)

    assert rendered.height == pytest.approx(200, abs=3)


def test_single_points_and_empty_layers_still_draw(tmp_path: Path) -> None:
    points = syn.polygons().head(1).set_geometry(syn.polygons().head(1).centroid)

    assert render_vector(points, tmp_path / "pt.png", width=100).height == 100
    assert render_vector(points.iloc[:0], tmp_path / "none.png", width=100).height == 100
