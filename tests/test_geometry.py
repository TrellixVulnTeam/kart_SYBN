import re

import pytest
from osgeo import ogr, osr

from sno.geometry import (
    gpkg_geom_to_hex_wkb,
    gpkg_geom_to_ogr,
    hex_wkb_to_gpkg_geom,
    normalise_gpkg_geom,
    ogr_to_gpkg_geom,
    GPKG_ENVELOPE_NONE,
    GPKG_ENVELOPE_XY,
)

SRID_RE = re.compile(r"^SRID=(-?\d+);(.*)$")


def ewkt_to_ogr(wkt):
    """
    Creates an OGR geometry, optionally with spatial reference,
    from some EWKT.
    """
    m = SRID_RE.match(wkt)
    crs_id = None
    if m:
        crs_id, wkt = m.groups()
        crs_id = int(crs_id)
    g = ogr.CreateGeometryFromWkt(wkt)
    if crs_id and crs_id > 0:
        spatial_ref = osr.SpatialReference()
        spatial_ref.ImportFromEPSG(crs_id)
        g.AssignSpatialReference(spatial_ref)
    return g


@pytest.mark.parametrize(
    "wkt",
    [
        "POINT(1 2)",
        "POINT(1 2 3)",
        "POINT(1 2 3 4)",
        "POINT EMPTY",
        "SRID=-1;POINT(1 2)",
        "SRID=4326;POINT(1 2)",
        "SRID=4326;POINT(1 2 3)",
        "SRID=4326;POINT(1 2 3 4)",
        "SRID=4326;POINT EMPTY",
        "MULTIPOINT EMPTY",
        "MULTIPOINT (1 2)",
        "GEOMETRYCOLLECTION EMPTY",
        "GEOMETRYCOLLECTION (POINT(1 2),MULTIPOINT EMPTY)",
        "TRIANGLE((0 0 0,0 1 0,1 1 0,0 0 0))",
        "TIN (((0 0 0, 0 0 1, 0 1 0, 0 0 0)), ((0 0 0, 0 1 0, 1 1 0, 0 0 0)))",
    ],
)
def test_wkt_gpkg_wkt_roundtrip(wkt):
    orig_ogr_geom = ewkt_to_ogr(wkt)
    gpkg_geom = ogr_to_gpkg_geom(orig_ogr_geom, _add_srs_id=True)
    ogr_geom = gpkg_geom_to_ogr(gpkg_geom, parse_crs=True)
    assert ogr_geom.Equals(
        orig_ogr_geom
    ), f"{ogr_geom.ExportToIsoWkt()} != {orig_ogr_geom.ExportToIsoWkt()}"

    orig_spatial_ref = orig_ogr_geom.GetSpatialReference()
    spatial_ref = ogr_geom.GetSpatialReference()

    def _export_to_proj4(crs):
        return crs.ExportToProj4() if crs else None

    assert _export_to_proj4(spatial_ref) == _export_to_proj4(orig_spatial_ref)


@pytest.mark.parametrize(
    "input_wkb,expected_gpkg_geom,expected_wkb",
    [
        pytest.param(
            # POINT EMPTY (well, POINT(nan nan))
            "00000000017FF80000000000007FF8000000000000",
            b"GP\x00\x11\x00\x00\x00\x00\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\xf8\x7f\x00\x00\x00\x00\x00\x00\xf8\x7f",
            "0101000000000000000000F87F000000000000F87F",
            id="point-empty",
        ),
        pytest.param(
            # GEOMETRYCOLLECTION EMPTY
            "000000000700000000",
            b"GP\x00\x11\x00\x00\x00\x00\x01\x07\x00\x00\x00\x00\x00\x00\x00",
            "010700000000000000",
            id="geometrycollection-empty",
        ),
        pytest.param(
            # TRIANGLE Z ((0 0 0,0 1 0,1 1 0,0 0 0))
            "00000003F9000000010000000400000000000000000000000000000000000000000000000000000000000000003FF000000000000000000000000000003FF00000000000003FF00000000000000000000000000000000000000000000000000000000000000000000000000000",
            b"GP\x00\x05\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xf0?\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xf0?\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01\xf9\x03\x00\x00\x01\x00\x00\x00\x04\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xf0?\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xf0?\x00\x00\x00\x00\x00\x00\xf0?\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00",
            "01F903000001000000040000000000000000000000000000000000000000000000000000000000000000000000000000000000F03F0000000000000000000000000000F03F000000000000F03F0000000000000000000000000000000000000000000000000000000000000000",
            id="triangle",
        ),
        pytest.param(
            # CIRCULARSTRING (1 5,6 2,7 3)
            "0000000008000000033FF0000000000000401400000000000040180000000000004000000000000000401C0000000000004008000000000000",
            b"GP\x00\x03\x00\x00\x00\x00\xbcf\x9a\xd1X\xb1\xef?\x00\x00\x00\x00\x00\x00\x1c@^3\xcdh\xac\xd8\xf7?\x00\x00\x00\x00\x00\x00\x14@\x01\x08\x00\x00\x00\x03\x00\x00\x00\x00\x00\x00\x00\x00\x00\xf0?\x00\x00\x00\x00\x00\x00\x14@\x00\x00\x00\x00\x00\x00\x18@\x00\x00\x00\x00\x00\x00\x00@\x00\x00\x00\x00\x00\x00\x1c@\x00\x00\x00\x00\x00\x00\x08@",
            "010800000003000000000000000000F03F0000000000001440000000000000184000000000000000400000000000001C400000000000000840",
            id="circularstring",
        ),
    ],
)
def test_wkb_gpkg_wkb_roundtrip(input_wkb, expected_gpkg_geom, expected_wkb):
    gpkg_geom = hex_wkb_to_gpkg_geom(input_wkb)
    assert gpkg_geom == expected_gpkg_geom
    assert gpkg_geom_to_hex_wkb(gpkg_geom) == expected_wkb


@pytest.mark.parametrize(
    "input,expected",
    [
        pytest.param(
            # POINT(5 5)
            b"GP\x00\x01\x00\x00\x00\x00\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x14@\x00\x00\x00\x00\x00\x00\x14@",
            b"GP\x00\x01\x00\x00\x00\x00\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x14@\x00\x00\x00\x00\x00\x00\x14@",
            id="point",
        ),
        pytest.param(
            # SRID=-1;POINT(5 5)
            # (srid gets replaced by 0)
            b"GP\x00\x01\xff\xff\xff\xff\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x14@\x00\x00\x00\x00\x00\x00\x14@",
            b"GP\x00\x01\x00\x00\x00\x00\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x14@\x00\x00\x00\x00\x00\x00\x14@",
            id="srid-minus-one",
        ),
        pytest.param(
            # SRID=4326;POINT(5 5)
            # (srid gets replaced by 0)
            b"GP\x00\x01\xe6\x10\x00\x00\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x14@\x00\x00\x00\x00\x00\x00\x14@",
            b"GP\x00\x01\x00\x00\x00\x00\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x14@\x00\x00\x00\x00\x00\x00\x14@",
            id="srid-minus-one",
        ),
        pytest.param(
            # POLYGON((0 0,0 5,5 0,0 0))
            b"GP\x00\x03\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x14@\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x14@\x01\x03\x00\x00\x00\x01\x00\x00\x00\x04\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x14@\x00\x00\x00\x00\x00\x00\x14@\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00",
            b"GP\x00\x03\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x14@\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x14@\x01\x03\x00\x00\x00\x01\x00\x00\x00\x04\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x14@\x00\x00\x00\x00\x00\x00\x14@\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00",
            id="polygon",
        ),
        pytest.param(
            # LINESTRING Z (1 2 3,4 5 6)
            b"GP\x00\x03\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xf0?\x00\x00\x00\x00\x00\x00\x10@\x00\x00\x00\x00\x00\x00\x00@\x00\x00\x00\x00\x00\x00\x14@\x01\xea\x03\x00\x00\x02\x00\x00\x00\x00\x00\x00\x00\x00\x00\xf0?\x00\x00\x00\x00\x00\x00\x00@\x00\x00\x00\x00\x00\x00\x08@\x00\x00\x00\x00\x00\x00\x10@\x00\x00\x00\x00\x00\x00\x14@\x00\x00\x00\x00\x00\x00\x18@",
            b"GP\x00\x05\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xf0?\x00\x00\x00\x00\x00\x00\x10@\x00\x00\x00\x00\x00\x00\x00@\x00\x00\x00\x00\x00\x00\x14@\x00\x00\x00\x00\x00\x00\x08@\x00\x00\x00\x00\x00\x00\x18@\x01\xea\x03\x00\x00\x02\x00\x00\x00\x00\x00\x00\x00\x00\x00\xf0?\x00\x00\x00\x00\x00\x00\x00@\x00\x00\x00\x00\x00\x00\x08@\x00\x00\x00\x00\x00\x00\x10@\x00\x00\x00\x00\x00\x00\x14@\x00\x00\x00\x00\x00\x00\x18@",
            id="linestring-xyz",
        ),
        pytest.param(
            # LINESTRING M (1 2 3,4 5 6)
            b"GP\x00\x03\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xf0?\x00\x00\x00\x00\x00\x00\x10@\x00\x00\x00\x00\x00\x00\x00@\x00\x00\x00\x00\x00\x00\x14@\x01\xd2\x07\x00\x00\x02\x00\x00\x00\x00\x00\x00\x00\x00\x00\xf0?\x00\x00\x00\x00\x00\x00\x00@\x00\x00\x00\x00\x00\x00\x08@\x00\x00\x00\x00\x00\x00\x10@\x00\x00\x00\x00\x00\x00\x14@\x00\x00\x00\x00\x00\x00\x18@",
            b"GP\x00\x03\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xf0?\x00\x00\x00\x00\x00\x00\x10@\x00\x00\x00\x00\x00\x00\x00@\x00\x00\x00\x00\x00\x00\x14@\x01\xd2\x07\x00\x00\x02\x00\x00\x00\x00\x00\x00\x00\x00\x00\xf0?\x00\x00\x00\x00\x00\x00\x00@\x00\x00\x00\x00\x00\x00\x08@\x00\x00\x00\x00\x00\x00\x10@\x00\x00\x00\x00\x00\x00\x14@\x00\x00\x00\x00\x00\x00\x18@",
            id="linestring-xym",
        ),
        pytest.param(
            # LINESTRING ZM (1 2 3 4,4 5 6 7)
            b"GP\x00\x03\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xf0?\x00\x00\x00\x00\x00\x00\x10@\x00\x00\x00\x00\x00\x00\x00@\x00\x00\x00\x00\x00\x00\x14@\x01\xba\x0b\x00\x00\x02\x00\x00\x00\x00\x00\x00\x00\x00\x00\xf0?\x00\x00\x00\x00\x00\x00\x00@\x00\x00\x00\x00\x00\x00\x08@\x00\x00\x00\x00\x00\x00\x10@\x00\x00\x00\x00\x00\x00\x10@\x00\x00\x00\x00\x00\x00\x14@\x00\x00\x00\x00\x00\x00\x18@\x00\x00\x00\x00\x00\x00\x1c@",
            b"GP\x00\x05\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xf0?\x00\x00\x00\x00\x00\x00\x10@\x00\x00\x00\x00\x00\x00\x00@\x00\x00\x00\x00\x00\x00\x14@\x00\x00\x00\x00\x00\x00\x08@\x00\x00\x00\x00\x00\x00\x18@\x01\xba\x0b\x00\x00\x02\x00\x00\x00\x00\x00\x00\x00\x00\x00\xf0?\x00\x00\x00\x00\x00\x00\x00@\x00\x00\x00\x00\x00\x00\x08@\x00\x00\x00\x00\x00\x00\x10@\x00\x00\x00\x00\x00\x00\x10@\x00\x00\x00\x00\x00\x00\x14@\x00\x00\x00\x00\x00\x00\x18@\x00\x00\x00\x00\x00\x00\x1c@",
            id="linestring-xyzm",
        ),
    ],
)
def test_normalise_geometry(input, expected):
    assert normalise_gpkg_geom(input) == expected


@pytest.mark.parametrize(
    "input,input_has_envelope",
    [
        # These geometries are little-endian with no envelope
        pytest.param(
            # POINT EMPTY (well, 'POINT(nan nan)')
            b"GP\x00\x01\x00\x00\x00\x00\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00",
            False,
            id="point-empty",
        ),
        pytest.param(
            # POINT(5 5)
            b"GP\x00\x01\x00\x00\x00\x00\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x14@\x00\x00\x00\x00\x00\x00\x14@",
            False,
            id="point",
        ),
        pytest.param(
            # POLYGON((0 0,0 5,5 0,0 0))
            b"GP\x00\x03\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x14@\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x14@\x01\x03\x00\x00\x00\x01\x00\x00\x00\x04\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x14@\x00\x00\x00\x00\x00\x00\x14@\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00",
            True,
            id="polygon",
        ),
    ],
)
@pytest.mark.parametrize("little_endian", [False, True])
@pytest.mark.parametrize("little_endian_wkb", [False, True])
@pytest.mark.parametrize("with_envelope", [False, True])
def test_gpkg_wkb_gpkg_roundtrip(
    input, input_has_envelope, little_endian, little_endian_wkb, with_envelope
):
    """
    Tests the following functions work and are consistent with each other:
        * normalise_gpkg_geom
        * gpkg_geom_to_hex_wkb
        * hex_wkb_to_gpkg_geom
    """
    assert normalise_gpkg_geom(input) == input
    hex_wkb = gpkg_geom_to_hex_wkb(input)
    assert hex_wkb.startswith("01"), "gpkg_geom_to_hex_wkb produced big-endian WKB"

    # Produce a GPKG geom in LE/BE variants of both the GPKG headers and the WKB itself.
    gpkg_geom_intermediate = hex_wkb_to_gpkg_geom(
        hex_wkb,
        _little_endian=little_endian,
        _little_endian_wkb=little_endian_wkb,
        _add_envelope_type=GPKG_ENVELOPE_XY if with_envelope else GPKG_ENVELOPE_NONE,
    )
    assert normalise_gpkg_geom(gpkg_geom_intermediate) == input

    if little_endian and little_endian_wkb and with_envelope == input_has_envelope:
        assert gpkg_geom_intermediate == input
        return

    else:
        if with_envelope and not input_has_envelope:
            # If we're adding an envelope, the geometry should have gotten bigger...
            assert len(gpkg_geom_intermediate) > len(input)
        elif input_has_envelope and not with_envelope:
            # If we're removing an envelope, the geometry should have gotten smaller...
            assert len(gpkg_geom_intermediate) < len(input)
        else:
            assert len(gpkg_geom_intermediate) == len(input)

    # Now re-roundtrip to convert it back to the original
    # (little-endian, no envelope)
    hex_wkb_2 = gpkg_geom_to_hex_wkb(gpkg_geom_intermediate)
    gpkg_geom = hex_wkb_to_gpkg_geom(hex_wkb_2)

    assert gpkg_geom == input
