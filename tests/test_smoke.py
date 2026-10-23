import loci


def test_package_exposes_version() -> None:
    assert isinstance(loci.__version__, str)
    assert loci.__version__
