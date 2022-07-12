from glob import glob
import json
import re
import shutil
import subprocess
import pytest

from kart.exceptions import (
    INVALID_FILE_FORMAT,
    WORKING_COPY_OR_IMPORT_CONFLICT,
)
from kart.repo import KartRepo
from kart.point_cloud.metadata_util import extract_pc_tile_metadata

DUMMY_REPO = "git@example.com/example.git"

# using a fixture instead of a skipif decorator means we get one aggregated skip
# message rather than one per test
@pytest.fixture(scope="session")
def requires_pdal():
    has_pdal = False
    try:
        import pdal

        assert pdal.Pipeline
        has_pdal = True
    except ModuleNotFoundError:
        pass

    pytest.helpers.feature_assert_or_skip(
        "pdal package installed", "KART_EXPECT_PDAL", has_pdal, ci_require=True
    )


@pytest.fixture(scope="session")
def requires_git_lfs():
    r = subprocess.run(["git", "lfs", "--version"])
    has_git_lfs = r.returncode == 0

    pytest.helpers.feature_assert_or_skip(
        "Git LFS installed", "KART_EXPECT_GIT_LFS", has_git_lfs, ci_require=True
    )


def test_import_single_las(
    tmp_path, chdir, cli_runner, data_archive_readonly, requires_pdal, requires_git_lfs
):
    with data_archive_readonly("point-cloud/las-autzen.tgz") as autzen:
        repo_path = tmp_path / "point-cloud-repo"
        r = cli_runner.invoke(["init", repo_path])
        assert r.exit_code == 0, r.stderr

        repo = KartRepo(repo_path)
        with chdir(repo_path):
            r = cli_runner.invoke(
                ["point-cloud-import", f"{autzen}/autzen.las", "--dataset-path=autzen"]
            )
            assert r.exit_code == 0, r.stderr

            r = cli_runner.invoke(["data", "ls"])
            assert r.exit_code == 0, r.stderr
            assert r.stdout.splitlines() == ["autzen"]

            r = cli_runner.invoke(["meta", "get", "autzen", "format.json", "-ojson"])
            assert r.exit_code == 0, r.stderr
            assert json.loads(r.stdout) == {
                "autzen": {
                    "format.json": {
                        "compression": "laz",
                        "lasVersion": "1.4",
                        "optimization": "copc",
                        "optimizationVersion": "1.0",
                        "pointDataRecordFormat": 6,
                        "pointDataRecordLength": 30,
                    }
                }
            }

            r = cli_runner.invoke(["meta", "get", "autzen", "schema.json", "-ojson"])
            assert r.exit_code == 0, r.stderr
            assert json.loads(r.stdout) == {
                "autzen": {
                    "schema.json": [
                        {"name": "X", "dataType": "float", "size": 64},
                        {"name": "Y", "dataType": "float", "size": 64},
                        {"name": "Z", "dataType": "float", "size": 64},
                        {"name": "Intensity", "dataType": "integer", "size": 16},
                        {"name": "ReturnNumber", "dataType": "integer", "size": 8},
                        {"name": "NumberOfReturns", "dataType": "integer", "size": 8},
                        {"name": "ScanDirectionFlag", "dataType": "integer", "size": 8},
                        {"name": "EdgeOfFlightLine", "dataType": "integer", "size": 8},
                        {"name": "Classification", "dataType": "integer", "size": 8},
                        {"name": "ScanAngleRank", "dataType": "float", "size": 32},
                        {"name": "UserData", "dataType": "integer", "size": 8},
                        {"name": "PointSourceId", "dataType": "integer", "size": 16},
                        {"name": "GpsTime", "dataType": "float", "size": 64},
                        {"name": "ScanChannel", "dataType": "integer", "size": 8},
                        {"name": "ClassFlags", "dataType": "integer", "size": 8},
                    ]
                }
            }

            r = cli_runner.invoke(["show", "HEAD", "autzen:tile:autzen"])
            assert r.exit_code == 0, r.stderr
            assert r.stdout.splitlines()[4:] == [
                "    Importing 1 LAZ tiles as autzen",
                "",
                "+++ autzen:tile:autzen",
                "+                                     name = autzen.copc.laz",
                "+                              crs84Extent = -123.075389,-123.0625145,44.04998981,44.06229306,407.35,536.84",
                "+                                   format = laz-1.4/copc-1.0",
                "+                             nativeExtent = 635616.31,638864.6,848977.79,853362.37,407.35,536.84",
                "+                               pointCount = 106",
                "+                                      oid = sha256:213ef4211ba375e2eec60aa61b6c230d1a3d1498b8fcc39150fd3040ee8f0512",
                "+                                     size = 3607",
            ]

            r = cli_runner.invoke(["remote", "add", "origin", DUMMY_REPO])
            assert r.exit_code == 0, r.stderr
            repo.config[f"lfs.{DUMMY_REPO}/info/lfs.locksverify"] = False

            stdout = subprocess.check_output(
                ["kart", "lfs", "push", "origin", "--all", "--dry-run"], encoding="utf8"
            )
            assert re.match(
                r"push [0-9a-f]{64} => autzen/.point-cloud-dataset.v1/tile/60/autzen",
                stdout.splitlines()[0],
            )

            assert (repo_path / "autzen" / "autzen.copc.laz").is_file()


@pytest.mark.slow
def test_import_several_laz(
    tmp_path, chdir, cli_runner, data_archive_readonly, requires_pdal, requires_git_lfs
):
    # Using postgres here because it has the best type preservation
    with data_archive_readonly("point-cloud/laz-auckland.tgz") as auckland:
        repo_path = tmp_path / "point-cloud-repo"
        r = cli_runner.invoke(["init", repo_path])
        assert r.exit_code == 0

        repo = KartRepo(repo_path)
        with chdir(repo_path):
            r = cli_runner.invoke(
                [
                    "point-cloud-import",
                    *glob(f"{auckland}/auckland_*.laz"),
                    "--dataset-path=auckland",
                ]
            )
            assert r.exit_code == 0, r.stderr

            r = cli_runner.invoke(["data", "ls"])
            assert r.exit_code == 0, r.stderr
            assert r.stdout.splitlines() == ["auckland"]

            r = cli_runner.invoke(["meta", "get", "auckland", "schema.json", "-ojson"])
            assert r.exit_code == 0, r.stderr
            assert json.loads(r.stdout) == {
                "auckland": {
                    "schema.json": [
                        {"name": "X", "dataType": "float", "size": 64},
                        {"name": "Y", "dataType": "float", "size": 64},
                        {"name": "Z", "dataType": "float", "size": 64},
                        {"name": "Intensity", "dataType": "integer", "size": 16},
                        {"name": "ReturnNumber", "dataType": "integer", "size": 8},
                        {"name": "NumberOfReturns", "dataType": "integer", "size": 8},
                        {"name": "ScanDirectionFlag", "dataType": "integer", "size": 8},
                        {"name": "EdgeOfFlightLine", "dataType": "integer", "size": 8},
                        {"name": "Classification", "dataType": "integer", "size": 8},
                        {"name": "ScanAngleRank", "dataType": "float", "size": 32},
                        {"name": "UserData", "dataType": "integer", "size": 8},
                        {"name": "PointSourceId", "dataType": "integer", "size": 16},
                        {"name": "GpsTime", "dataType": "float", "size": 64},
                        {"name": "ScanChannel", "dataType": "integer", "size": 8},
                        {"name": "ClassFlags", "dataType": "integer", "size": 8},
                        {"name": "Red", "dataType": "integer", "size": 16},
                        {"name": "Green", "dataType": "integer", "size": 16},
                        {"name": "Blue", "dataType": "integer", "size": 16},
                    ]
                }
            }

            r = cli_runner.invoke(["remote", "add", "origin", DUMMY_REPO])
            assert r.exit_code == 0, r.stderr
            repo.config[f"lfs.{DUMMY_REPO}/info/lfs.locksverify"] = False

            stdout = subprocess.check_output(
                ["kart", "lfs", "push", "origin", "--all", "--dry-run"], encoding="utf8"
            )
            lines = stdout.splitlines()
            for i in range(16):
                assert re.match(
                    r"push [0-9a-f]{64} => auckland/.point-cloud-dataset.v1/tile/[0-9a-f]{2}/auckland_\d_\d",
                    lines[i],
                )

            for x in range(4):
                for y in range(4):
                    assert (
                        repo_path / "auckland" / f"auckland_{x}_{y}.copc.laz"
                    ).is_file()


def test_import_single_laz_no_convert(
    tmp_path, chdir, cli_runner, data_archive_readonly, requires_pdal, requires_git_lfs
):
    with data_archive_readonly("point-cloud/laz-auckland.tgz") as auckland:
        repo_path = tmp_path / "point-cloud-repo"
        r = cli_runner.invoke(["init", repo_path])
        assert r.exit_code == 0

        with chdir(repo_path):
            r = cli_runner.invoke(
                [
                    "point-cloud-import",
                    *glob(f"{auckland}/auckland_0_0.laz"),
                    "--message=test_import_single_laz_no_convert",
                    "--dataset-path=auckland",
                    "--preserve-format",
                ]
            )
            assert r.exit_code == 0, r.stderr

            r = cli_runner.invoke(["meta", "get", "auckland", "format.json", "-ojson"])
            assert r.exit_code == 0, r.stderr
            assert json.loads(r.stdout) == {
                "auckland": {
                    "format.json": {
                        "compression": "laz",
                        "lasVersion": "1.2",
                        "pointDataRecordFormat": 3,
                        "pointDataRecordLength": 34,
                    }
                }
            }

            r = cli_runner.invoke(["show", "HEAD", "auckland:tile:auckland_0_0"])
            assert r.exit_code == 0, r.stderr
            assert r.stdout.splitlines()[4:] == [
                "    test_import_single_laz_no_convert",
                "",
                "+++ auckland:tile:auckland_0_0",
                "+                                     name = auckland_0_0.laz",
                "+                              crs84Extent = 174.7382443,174.7496594,-36.85123712,-36.84206322,-1.66,99.83",
                "+                                   format = laz-1.2",
                "+                             nativeExtent = 1754987.85,1755987.77,5920219.76,5921219.64,-1.66,99.83",
                "+                               pointCount = 4231",
                "+                                      oid = sha256:6b980ce4d7f4978afd3b01e39670e2071a792fba441aca45be69be81cb48b08c",
                "+                                     size = 51489",
            ]


def test_import_single_las_no_convert(
    tmp_path, chdir, cli_runner, data_archive_readonly, requires_pdal, requires_git_lfs
):
    with data_archive_readonly("point-cloud/las-autzen.tgz") as autzen:
        repo_path = tmp_path / "point-cloud-repo"
        r = cli_runner.invoke(["init", repo_path])
        assert r.exit_code == 0

        with chdir(repo_path):
            r = cli_runner.invoke(
                [
                    "point-cloud-import",
                    *glob(f"{autzen}/autzen.las"),
                    "--dataset-path=autzen",
                    "--preserve-format",
                ]
            )
            assert r.exit_code == INVALID_FILE_FORMAT
            assert "LAS datasets are not supported" in r.stderr


def test_import_mismatched_las(
    tmp_path, chdir, cli_runner, data_archive_readonly, requires_pdal, requires_git_lfs
):
    # Using postgres here because it has the best type preservation
    with data_archive_readonly("point-cloud/laz-auckland.tgz") as auckland:
        with data_archive_readonly("point-cloud/las-autzen.tgz") as autzen:
            repo_path = tmp_path / "point-cloud-repo"
            r = cli_runner.invoke(["init", repo_path])
            assert r.exit_code == 0, r.stderr
            with chdir(repo_path):
                r = cli_runner.invoke(
                    [
                        "point-cloud-import",
                        *glob(f"{auckland}/auckland_*.laz"),
                        f"{autzen}/autzen.las",
                        "--dataset-path=mixed",
                    ]
                )
                assert r.exit_code == WORKING_COPY_OR_IMPORT_CONFLICT
                assert "Non-homogenous" in r.stderr
                # This is disallowed even though we are converting to COPC, since these tiles would have different
                # schemas even once converted to COPC.
                assert "schema" in r.stderr


def test_working_copy_edit(cli_runner, data_archive, monkeypatch, requires_pdal):
    monkeypatch.setenv("X_KART_POINT_CLOUDS", "1")

    with data_archive("point-cloud/auckland.tgz") as repo_path:
        r = cli_runner.invoke(["diff"])
        assert r.exit_code == 0, r.stderr
        assert r.stdout.splitlines() == []

        tiles_path = repo_path / "auckland"
        assert tiles_path.is_dir()

        shutil.copy(
            tiles_path / "auckland_0_0.copc.laz", tiles_path / "auckland_1_1.copc.laz"
        )
        # TODO - add rename detection.
        (tiles_path / "auckland_3_3.copc.laz").rename(
            tiles_path / "auckland_4_4.copc.laz"
        )

        r = cli_runner.invoke(["status"])
        assert r.exit_code == 0, r.stderr
        assert r.stdout.splitlines() == [
            "On branch main",
            "",
            "Changes in working copy:",
            '  (use "kart commit" to commit)',
            '  (use "kart restore" to discard changes)',
            "",
            "  auckland:",
            "    tile:",
            "      1 inserts",
            "      1 updates",
            "      1 deletes",
        ]

        EXPECTED_TILE_DIFF = [
            "--- auckland:tile:auckland_1_1",
            "+++ auckland:tile:auckland_1_1",
            "-                              crs84Extent = 174.7492629,174.7606572,-36.84205419,-36.83288872,-1.48,35.15",
            "+                              crs84Extent = 174.7382443,174.7496594,-36.85123712,-36.84206322,-1.66,99.83",
            "-                             nativeExtent = 1755989.03,1756987.13,5921220.62,5922219.49,-1.48,35.15",
            "+                             nativeExtent = 1754987.85,1755987.77,5920219.76,5921219.64,-1.66,99.83",
            "-                               pointCount = 1558",
            "+                               pointCount = 4231",
            "-                                      oid = sha256:c0304cd6dc1687eb3419ee9af9f5494245ee31b6560fc2059946e0784b48da54",
            "+                                      oid = sha256:21564a23ebe762997d5b446d99249df2cf4560096c116ef87766981c64b981fc",
            "-                                     size = 24491",
            "+                                     size = 69553",
            "--- auckland:tile:auckland_3_3",
            "-                                     name = auckland_3_3.copc.laz",
            "-                              crs84Extent = 174.7726418,174.7819673,-36.82369125,-36.82346553,-1.28,9.8",
            "-                                   format = laz-1.4/copc-1.0",
            "-                             nativeExtent = 1758093.46,1758925.34,5923219.8,5923229.38,-1.28,9.8",
            "-                               pointCount = 29",
            "-                                      oid = sha256:64895828ea03ce9cafaef4f387338aab8d498c8eccaef1503b8b3bd97e57c5a3",
            "-                                     size = 2319",
            "+++ auckland:tile:auckland_4_4",
            "+                                     name = auckland_4_4.copc.laz",
            "+                              crs84Extent = 174.7726418,174.7819673,-36.82369125,-36.82346553,-1.28,9.8",
            "+                                   format = laz-1.4/copc-1.0",
            "+                             nativeExtent = 1758093.46,1758925.34,5923219.8,5923229.38,-1.28,9.8",
            "+                               pointCount = 29",
            "+                                      oid = sha256:64895828ea03ce9cafaef4f387338aab8d498c8eccaef1503b8b3bd97e57c5a3",
            "+                                     size = 2319",
        ]

        r = cli_runner.invoke(["diff"])
        assert r.exit_code == 0, r.stderr
        assert r.stdout.splitlines() == EXPECTED_TILE_DIFF

        r = cli_runner.invoke(["commit", "-m", "Edit point cloud tiles"])
        assert r.exit_code == 0, r.stderr

        r = cli_runner.invoke(["show"])
        assert r.exit_code == 0, r.stderr
        assert (
            r.stdout.splitlines()[4:]
            == ["    Edit point cloud tiles", ""] + EXPECTED_TILE_DIFF
        )

        r = cli_runner.invoke(["show", "-ojson"])
        assert r.exit_code == 0, r.stderr

        r = cli_runner.invoke(["show", "-ojson-lines"])
        assert r.exit_code == 0, r.stderr

        r = cli_runner.invoke(["diff"])
        assert r.exit_code == 0, r.stderr
        assert r.stdout.splitlines() == []


def test_working_copy_restore_reset(
    cli_runner, data_archive, monkeypatch, requires_pdal
):
    monkeypatch.setenv("X_KART_POINT_CLOUDS", "1")

    def file_count(path):
        return len(list(path.iterdir()))

    with data_archive("point-cloud/auckland.tgz") as repo_path:
        repo = KartRepo(repo_path)

        r = cli_runner.invoke(["status"])
        assert r.exit_code == 0, r.stderr
        assert r.stdout.splitlines()[-1] == "Nothing to commit, working copy clean"

        tiles_path = repo_path / "auckland"
        assert tiles_path.is_dir()
        assert file_count(tiles_path) == 16

        for tile in tiles_path.glob("auckland_0_*.copc.laz"):
            tile.unlink()

        r = cli_runner.invoke(["status"])
        assert r.exit_code == 0, r.stderr
        assert "4 deletes" in r.stdout.splitlines()[-1]

        r = cli_runner.invoke(["commit", "-m", "4 deletes"])
        assert r.exit_code == 0, r.stderr
        assert file_count(tiles_path) == 12
        edit_commit = repo.head_commit.hex

        r = cli_runner.invoke(["restore", "-s", "HEAD^", "auckland:tile:auckland_0_0"])
        assert r.exit_code == 0, r.stderr

        r = cli_runner.invoke(["status"])
        assert r.exit_code == 0, r.stderr
        assert "1 insert" in r.stdout.splitlines()[-1]
        assert file_count(tiles_path) == 13

        r = cli_runner.invoke(["reset", "HEAD^", "--discard-changes"])
        assert r.exit_code == 0, r.stderr

        r = cli_runner.invoke(["status"])
        assert r.exit_code == 0, r.stderr
        assert r.stdout.splitlines()[-1] == "Nothing to commit, working copy clean"
        assert file_count(tiles_path) == 16

        r = cli_runner.invoke(
            [
                "restore",
                "-s",
                edit_commit,
                "auckland:tile:auckland_0_1",
                "auckland:tile:auckland_0_2",
            ]
        )
        assert r.exit_code == 0, r.stderr

        r = cli_runner.invoke(["status"])
        assert r.exit_code == 0, r.stderr
        assert "2 deletes" in r.stdout.splitlines()[-1]
        assert file_count(tiles_path) == 14

        r = cli_runner.invoke(["reset", edit_commit, "--discard-changes"])
        assert r.exit_code == 0, r.stderr

        r = cli_runner.invoke(["status"])
        assert r.exit_code == 0, r.stderr
        assert r.stdout.splitlines()[-1] == "Nothing to commit, working copy clean"
        assert file_count(tiles_path) == 12


def test_working_copy_meta_edit(
    cli_runner, data_archive, data_archive_readonly, monkeypatch, requires_pdal
):
    monkeypatch.setenv("X_KART_POINT_CLOUDS", "1")

    with data_archive_readonly("point-cloud/laz-autzen.tgz") as autzen:
        with data_archive("point-cloud/auckland.tgz") as repo_path:
            r = cli_runner.invoke(["diff"])
            assert r.exit_code == 0, r.stderr
            assert r.stdout.splitlines() == []

            tiles_path = repo_path / "auckland"
            assert tiles_path.is_dir()

            shutil.copy(autzen / "autzen.laz", tiles_path / "autzen.laz")

            r = cli_runner.invoke(["status"])
            assert r.exit_code == 0, r.stderr
            # TODO: kart status doesn't report on these type of conflicts.
            assert r.stdout.splitlines() == [
                "On branch main",
                "",
                "Changes in working copy:",
                '  (use "kart commit" to commit)',
                '  (use "kart restore" to discard changes)',
                "",
                "  auckland:",
                "    meta:",
                "      3 updates",
                "    tile:",
                "      1 inserts",
            ]

            # The diff has conflicts as the user is adding a dile with a new CRS without deleting the tiles with the old CRS:
            # Same for the format and the schema.
            r = cli_runner.invoke(["diff"])
            assert r.exit_code == 0, r.stderr
            assert r.stdout.splitlines() == [
                "--- auckland:meta:crs.wkt",
                "+++ auckland:meta:crs.wkt",
                '- COMPD_CS["NZGD2000 / New Zealand Transverse Mercator 2000 + VERT_CS",',
                '-     PROJCS["NZGD2000 / New Zealand Transverse Mercator 2000",',
                '-         GEOGCS["NZGD2000",',
                '-             DATUM["New_Zealand_Geodetic_Datum_2000",',
                '-                 SPHEROID["GRS 1980", 6378137, 298.257222101,',
                '-                     AUTHORITY["EPSG", "7019"]],',
                '-                 AUTHORITY["EPSG", "6167"]],',
                '-             PRIMEM["Greenwich", 0,',
                '-                 AUTHORITY["EPSG", "8901"]],',
                '-             UNIT["degree", 0.0174532925199433,',
                '-                 AUTHORITY["EPSG", "9122"]],',
                '-             AUTHORITY["EPSG", "4167"]],',
                '-         PROJECTION["Transverse_Mercator"],',
                '-         PARAMETER["latitude_of_origin", 0],',
                '-         PARAMETER["central_meridian", 173],',
                '-         PARAMETER["scale_factor", 0.9996],',
                '-         PARAMETER["false_easting", 1600000],',
                '-         PARAMETER["false_northing", 10000000],',
                '-         UNIT["metre", 1,',
                '-             AUTHORITY["EPSG", "9001"]],',
                '-         AXIS["Northing", NORTH],',
                '-         AXIS["Easting", EAST],',
                '-         AUTHORITY["EPSG", "2193"]],',
                '-     VERT_CS["NZVD2009 height",',
                '-         VERT_DATUM["New Zealand Vertical Datum 2009", 2005,',
                '-             AUTHORITY["EPSG", "1039"]],',
                '-         UNIT["metre", 1,',
                '-             AUTHORITY["EPSG", "9001"]],',
                '-         AXIS["Gravity-related height", UP],',
                '-         AUTHORITY["EPSG", "4440"]]]',
                "- ",
                "+ <<<<<<< ",
                '+ COMPD_CS["NZGD2000 / New Zealand Transverse Mercator 2000 + VERT_CS",',
                '+     PROJCS["NZGD2000 / New Zealand Transverse Mercator 2000",',
                '+         GEOGCS["NZGD2000",',
                '+             DATUM["New_Zealand_Geodetic_Datum_2000",',
                '+                 SPHEROID["GRS 1980", 6378137, 298.257222101,',
                '+                     AUTHORITY["EPSG", "7019"]],',
                '+                 AUTHORITY["EPSG", "6167"]],',
                '+             PRIMEM["Greenwich", 0,',
                '+                 AUTHORITY["EPSG", "8901"]],',
                '+             UNIT["degree", 0.0174532925199433,',
                '+                 AUTHORITY["EPSG", "9122"]],',
                '+             AUTHORITY["EPSG", "4167"]],',
                '+         PROJECTION["Transverse_Mercator"],',
                '+         PARAMETER["latitude_of_origin", 0],',
                '+         PARAMETER["central_meridian", 173],',
                '+         PARAMETER["scale_factor", 0.9996],',
                '+         PARAMETER["false_easting", 1600000],',
                '+         PARAMETER["false_northing", 10000000],',
                '+         UNIT["metre", 1,',
                '+             AUTHORITY["EPSG", "9001"]],',
                '+         AXIS["Northing", NORTH],',
                '+         AXIS["Easting", EAST],',
                '+         AUTHORITY["EPSG", "2193"]],',
                '+     VERT_CS["NZVD2009 height",',
                '+         VERT_DATUM["New Zealand Vertical Datum 2009", 2005,',
                '+             AUTHORITY["EPSG", "1039"]],',
                '+         UNIT["metre", 1,',
                '+             AUTHORITY["EPSG", "9001"]],',
                '+         AXIS["Gravity-related height", UP],',
                '+         AUTHORITY["EPSG", "4440"]]]',
                "+ ",
                "+ ======== ",
                '+ PROJCS["NAD83(HARN) / Oregon GIC Lambert (ft)",',
                '+     GEOGCS["NAD83(HARN)",',
                '+         DATUM["NAD83_High_Accuracy_Reference_Network",',
                '+             SPHEROID["GRS 1980", 6378137, 298.257222101,',
                '+                 AUTHORITY["EPSG", "7019"]],',
                '+             AUTHORITY["EPSG", "6152"]],',
                '+         PRIMEM["Greenwich", 0,',
                '+             AUTHORITY["EPSG", "8901"]],',
                '+         UNIT["degree", 0.0174532925199433,',
                '+             AUTHORITY["EPSG", "9122"]],',
                '+         AUTHORITY["EPSG", "4152"]],',
                '+     PROJECTION["Lambert_Conformal_Conic_2SP"],',
                '+     PARAMETER["latitude_of_origin", 41.75],',
                '+     PARAMETER["central_meridian", -120.5],',
                '+     PARAMETER["standard_parallel_1", 43],',
                '+     PARAMETER["standard_parallel_2", 45.5],',
                '+     PARAMETER["false_easting", 1312335.958],',
                '+     PARAMETER["false_northing", 0],',
                '+     UNIT["foot", 0.3048,',
                '+         AUTHORITY["EPSG", "9002"]],',
                '+     AXIS["Easting", EAST],',
                '+     AXIS["Northing", NORTH],',
                '+     AUTHORITY["EPSG", "2994"]]',
                "+ ",
                "+ >>>>>>> ",
                "--- auckland:meta:format.json",
                "+++ auckland:meta:format.json",
                "- {",
                '-   "compression": "laz",',
                '-   "lasVersion": "1.4",',
                '-   "optimization": "copc",',
                '-   "optimizationVersion": "1.0",',
                '-   "pointDataRecordFormat": 7,',
                '-   "pointDataRecordLength": 36',
                "- }",
                "+ <<<<<<< ",
                "+ {",
                '+   "compression": "laz",',
                '+   "lasVersion": "1.4",',
                '+   "optimization": "copc",',
                '+   "optimizationVersion": "1.0",',
                '+   "pointDataRecordFormat": 7,',
                '+   "pointDataRecordLength": 36',
                "+ }",
                "+ ======== ",
                "+ {",
                '+   "compression": "laz",',
                '+   "lasVersion": "1.2",',
                '+   "optimization": null,',
                '+   "optimizationVersion": null,',
                '+   "pointDataRecordFormat": 1,',
                '+   "pointDataRecordLength": 28',
                "+ }",
                "+ >>>>>>> ",
                "--- auckland:meta:schema.json",
                "+++ auckland:meta:schema.json",
                "- [",
                "-   {",
                '-     "name": "X",',
                '-     "dataType": "float",',
                '-     "size": 64',
                "-   },",
                "-   {",
                '-     "name": "Y",',
                '-     "dataType": "float",',
                '-     "size": 64',
                "-   },",
                "-   {",
                '-     "name": "Z",',
                '-     "dataType": "float",',
                '-     "size": 64',
                "-   },",
                "-   {",
                '-     "name": "Intensity",',
                '-     "dataType": "integer",',
                '-     "size": 16',
                "-   },",
                "-   {",
                '-     "name": "ReturnNumber",',
                '-     "dataType": "integer",',
                '-     "size": 8',
                "-   },",
                "-   {",
                '-     "name": "NumberOfReturns",',
                '-     "dataType": "integer",',
                '-     "size": 8',
                "-   },",
                "-   {",
                '-     "name": "ScanDirectionFlag",',
                '-     "dataType": "integer",',
                '-     "size": 8',
                "-   },",
                "-   {",
                '-     "name": "EdgeOfFlightLine",',
                '-     "dataType": "integer",',
                '-     "size": 8',
                "-   },",
                "-   {",
                '-     "name": "Classification",',
                '-     "dataType": "integer",',
                '-     "size": 8',
                "-   },",
                "-   {",
                '-     "name": "ScanAngleRank",',
                '-     "dataType": "float",',
                '-     "size": 32',
                "-   },",
                "-   {",
                '-     "name": "UserData",',
                '-     "dataType": "integer",',
                '-     "size": 8',
                "-   },",
                "-   {",
                '-     "name": "PointSourceId",',
                '-     "dataType": "integer",',
                '-     "size": 16',
                "-   },",
                "-   {",
                '-     "name": "GpsTime",',
                '-     "dataType": "float",',
                '-     "size": 64',
                "-   },",
                "-   {",
                '-     "name": "ScanChannel",',
                '-     "dataType": "integer",',
                '-     "size": 8',
                "-   },",
                "-   {",
                '-     "name": "ClassFlags",',
                '-     "dataType": "integer",',
                '-     "size": 8',
                "-   },",
                "-   {",
                '-     "name": "Red",',
                '-     "dataType": "integer",',
                '-     "size": 16',
                "-   },",
                "-   {",
                '-     "name": "Green",',
                '-     "dataType": "integer",',
                '-     "size": 16',
                "-   },",
                "-   {",
                '-     "name": "Blue",',
                '-     "dataType": "integer",',
                '-     "size": 16',
                "-   }",
                "- ]",
                "+ <<<<<<< ",
                "+ [",
                "+   {",
                '+     "name": "X",',
                '+     "dataType": "float",',
                '+     "size": 64',
                "+   },",
                "+   {",
                '+     "name": "Y",',
                '+     "dataType": "float",',
                '+     "size": 64',
                "+   },",
                "+   {",
                '+     "name": "Z",',
                '+     "dataType": "float",',
                '+     "size": 64',
                "+   },",
                "+   {",
                '+     "name": "Intensity",',
                '+     "dataType": "integer",',
                '+     "size": 16',
                "+   },",
                "+   {",
                '+     "name": "ReturnNumber",',
                '+     "dataType": "integer",',
                '+     "size": 8',
                "+   },",
                "+   {",
                '+     "name": "NumberOfReturns",',
                '+     "dataType": "integer",',
                '+     "size": 8',
                "+   },",
                "+   {",
                '+     "name": "ScanDirectionFlag",',
                '+     "dataType": "integer",',
                '+     "size": 8',
                "+   },",
                "+   {",
                '+     "name": "EdgeOfFlightLine",',
                '+     "dataType": "integer",',
                '+     "size": 8',
                "+   },",
                "+   {",
                '+     "name": "Classification",',
                '+     "dataType": "integer",',
                '+     "size": 8',
                "+   },",
                "+   {",
                '+     "name": "ScanAngleRank",',
                '+     "dataType": "float",',
                '+     "size": 32',
                "+   },",
                "+   {",
                '+     "name": "UserData",',
                '+     "dataType": "integer",',
                '+     "size": 8',
                "+   },",
                "+   {",
                '+     "name": "PointSourceId",',
                '+     "dataType": "integer",',
                '+     "size": 16',
                "+   },",
                "+   {",
                '+     "name": "GpsTime",',
                '+     "dataType": "float",',
                '+     "size": 64',
                "+   },",
                "+   {",
                '+     "name": "ScanChannel",',
                '+     "dataType": "integer",',
                '+     "size": 8',
                "+   },",
                "+   {",
                '+     "name": "ClassFlags",',
                '+     "dataType": "integer",',
                '+     "size": 8',
                "+   },",
                "+   {",
                '+     "name": "Red",',
                '+     "dataType": "integer",',
                '+     "size": 16',
                "+   },",
                "+   {",
                '+     "name": "Green",',
                '+     "dataType": "integer",',
                '+     "size": 16',
                "+   },",
                "+   {",
                '+     "name": "Blue",',
                '+     "dataType": "integer",',
                '+     "size": 16',
                "+   }",
                "+ ]",
                "+ ======== ",
                "+ [",
                "+   {",
                '+     "name": "X",',
                '+     "dataType": "float",',
                '+     "size": 64',
                "+   },",
                "+   {",
                '+     "name": "Y",',
                '+     "dataType": "float",',
                '+     "size": 64',
                "+   },",
                "+   {",
                '+     "name": "Z",',
                '+     "dataType": "float",',
                '+     "size": 64',
                "+   },",
                "+   {",
                '+     "name": "Intensity",',
                '+     "dataType": "integer",',
                '+     "size": 16',
                "+   },",
                "+   {",
                '+     "name": "ReturnNumber",',
                '+     "dataType": "integer",',
                '+     "size": 8',
                "+   },",
                "+   {",
                '+     "name": "NumberOfReturns",',
                '+     "dataType": "integer",',
                '+     "size": 8',
                "+   },",
                "+   {",
                '+     "name": "ScanDirectionFlag",',
                '+     "dataType": "integer",',
                '+     "size": 8',
                "+   },",
                "+   {",
                '+     "name": "EdgeOfFlightLine",',
                '+     "dataType": "integer",',
                '+     "size": 8',
                "+   },",
                "+   {",
                '+     "name": "Classification",',
                '+     "dataType": "integer",',
                '+     "size": 8',
                "+   },",
                "+   {",
                '+     "name": "ScanAngleRank",',
                '+     "dataType": "float",',
                '+     "size": 32',
                "+   },",
                "+   {",
                '+     "name": "UserData",',
                '+     "dataType": "integer",',
                '+     "size": 8',
                "+   },",
                "+   {",
                '+     "name": "PointSourceId",',
                '+     "dataType": "integer",',
                '+     "size": 16',
                "+   },",
                "+   {",
                '+     "name": "GpsTime",',
                '+     "dataType": "float",',
                '+     "size": 64',
                "+   }",
                "+ ]",
                "+ >>>>>>> ",
                "+++ auckland:tile:autzen",
                "+                                     name = autzen.laz",
                "+                              crs84Extent = -123.075389,-123.0625145,44.04998981,44.06229306,407.35,536.84",
                "+                                   format = laz-1.2",
                "+                             nativeExtent = 635616.31,638864.6,848977.79,853362.37,407.35,536.84",
                "+                               pointCount = 106",
                "+                                      oid = sha256:751ec764325610dae8f37d7f4273e3b404e5acb64421676fd72e7e31468c6720",
                "+                                     size = 2359",
            ]

            r = cli_runner.invoke(["commit", "-m", "conflicts"])
            assert r.exit_code == WORKING_COPY_OR_IMPORT_CONFLICT
            assert (
                "Committing more than one 'format.json' for 'auckland' is not supported"
                in r.stderr
            )

            # If all the old tiles are deleted, there will no longer be a conflict.
            for tile in tiles_path.glob("auckland_*.copc.laz"):
                tile.unlink()

            r = cli_runner.invoke(["status"])
            assert r.exit_code == 0, r.stderr
            assert r.stdout.splitlines() == [
                "On branch main",
                "",
                "Changes in working copy:",
                '  (use "kart commit" to commit)',
                '  (use "kart restore" to discard changes)',
                "",
                "  auckland:",
                "    meta:",
                "      3 updates",
                "    tile:",
                "      1 inserts",
                "      16 deletes",
            ]

            EXPECTED_META_DIFF = [
                "--- auckland:meta:crs.wkt",
                "+++ auckland:meta:crs.wkt",
                '- COMPD_CS["NZGD2000 / New Zealand Transverse Mercator 2000 + VERT_CS",',
                '-     PROJCS["NZGD2000 / New Zealand Transverse Mercator 2000",',
                '-         GEOGCS["NZGD2000",',
                '-             DATUM["New_Zealand_Geodetic_Datum_2000",',
                '-                 SPHEROID["GRS 1980", 6378137, 298.257222101,',
                '-                     AUTHORITY["EPSG", "7019"]],',
                '-                 AUTHORITY["EPSG", "6167"]],',
                '-             PRIMEM["Greenwich", 0,',
                '-                 AUTHORITY["EPSG", "8901"]],',
                '-             UNIT["degree", 0.0174532925199433,',
                '-                 AUTHORITY["EPSG", "9122"]],',
                '-             AUTHORITY["EPSG", "4167"]],',
                '-         PROJECTION["Transverse_Mercator"],',
                '-         PARAMETER["latitude_of_origin", 0],',
                '-         PARAMETER["central_meridian", 173],',
                '-         PARAMETER["scale_factor", 0.9996],',
                '-         PARAMETER["false_easting", 1600000],',
                '-         PARAMETER["false_northing", 10000000],',
                '-         UNIT["metre", 1,',
                '-             AUTHORITY["EPSG", "9001"]],',
                '-         AXIS["Northing", NORTH],',
                '-         AXIS["Easting", EAST],',
                '-         AUTHORITY["EPSG", "2193"]],',
                '-     VERT_CS["NZVD2009 height",',
                '-         VERT_DATUM["New Zealand Vertical Datum 2009", 2005,',
                '-             AUTHORITY["EPSG", "1039"]],',
                '-         UNIT["metre", 1,',
                '-             AUTHORITY["EPSG", "9001"]],',
                '-         AXIS["Gravity-related height", UP],',
                '-         AUTHORITY["EPSG", "4440"]]]',
                "- ",
                '+ PROJCS["NAD83(HARN) / Oregon GIC Lambert (ft)",',
                '+     GEOGCS["NAD83(HARN)",',
                '+         DATUM["NAD83_High_Accuracy_Reference_Network",',
                '+             SPHEROID["GRS 1980", 6378137, 298.257222101,',
                '+                 AUTHORITY["EPSG", "7019"]],',
                '+             AUTHORITY["EPSG", "6152"]],',
                '+         PRIMEM["Greenwich", 0,',
                '+             AUTHORITY["EPSG", "8901"]],',
                '+         UNIT["degree", 0.0174532925199433,',
                '+             AUTHORITY["EPSG", "9122"]],',
                '+         AUTHORITY["EPSG", "4152"]],',
                '+     PROJECTION["Lambert_Conformal_Conic_2SP"],',
                '+     PARAMETER["latitude_of_origin", 41.75],',
                '+     PARAMETER["central_meridian", -120.5],',
                '+     PARAMETER["standard_parallel_1", 43],',
                '+     PARAMETER["standard_parallel_2", 45.5],',
                '+     PARAMETER["false_easting", 1312335.958],',
                '+     PARAMETER["false_northing", 0],',
                '+     UNIT["foot", 0.3048,',
                '+         AUTHORITY["EPSG", "9002"]],',
                '+     AXIS["Easting", EAST],',
                '+     AXIS["Northing", NORTH],',
                '+     AUTHORITY["EPSG", "2994"]]',
                "+ ",
                "--- auckland:meta:format.json",
                "+++ auckland:meta:format.json",
                "- {",
                '-   "compression": "laz",',
                '-   "lasVersion": "1.4",',
                '-   "optimization": "copc",',
                '-   "optimizationVersion": "1.0",',
                '-   "pointDataRecordFormat": 7,',
                '-   "pointDataRecordLength": 36',
                "- }",
                "+ {",
                '+   "compression": "laz",',
                '+   "lasVersion": "1.2",',
                '+   "optimization": null,',
                '+   "optimizationVersion": null,',
                '+   "pointDataRecordFormat": 1,',
                '+   "pointDataRecordLength": 28',
                "+ }",
                "--- auckland:meta:schema.json",
                "+++ auckland:meta:schema.json",
                "  [",
                "    {",
                '      "name": "X",',
                '      "dataType": "float",',
                '      "size": 64',
                "    },",
                "    {",
                '      "name": "Y",',
                '      "dataType": "float",',
                '      "size": 64',
                "    },",
                "    {",
                '      "name": "Z",',
                '      "dataType": "float",',
                '      "size": 64',
                "    },",
                "    {",
                '      "name": "Intensity",',
                '      "dataType": "integer",',
                '      "size": 16',
                "    },",
                "    {",
                '      "name": "ReturnNumber",',
                '      "dataType": "integer",',
                '      "size": 8',
                "    },",
                "    {",
                '      "name": "NumberOfReturns",',
                '      "dataType": "integer",',
                '      "size": 8',
                "    },",
                "    {",
                '      "name": "ScanDirectionFlag",',
                '      "dataType": "integer",',
                '      "size": 8',
                "    },",
                "    {",
                '      "name": "EdgeOfFlightLine",',
                '      "dataType": "integer",',
                '      "size": 8',
                "    },",
                "    {",
                '      "name": "Classification",',
                '      "dataType": "integer",',
                '      "size": 8',
                "    },",
                "    {",
                '      "name": "ScanAngleRank",',
                '      "dataType": "float",',
                '      "size": 32',
                "    },",
                "    {",
                '      "name": "UserData",',
                '      "dataType": "integer",',
                '      "size": 8',
                "    },",
                "    {",
                '      "name": "PointSourceId",',
                '      "dataType": "integer",',
                '      "size": 16',
                "    },",
                "    {",
                '      "name": "GpsTime",',
                '      "dataType": "float",',
                '      "size": 64',
                "    },",
                "-   {",
                '-     "name": "ScanChannel",',
                '-     "dataType": "integer",',
                '-     "size": 8',
                "-   },",
                "-   {",
                '-     "name": "ClassFlags",',
                '-     "dataType": "integer",',
                '-     "size": 8',
                "-   },",
                "-   {",
                '-     "name": "Red",',
                '-     "dataType": "integer",',
                '-     "size": 16',
                "-   },",
                "-   {",
                '-     "name": "Green",',
                '-     "dataType": "integer",',
                '-     "size": 16',
                "-   },",
                "-   {",
                '-     "name": "Blue",',
                '-     "dataType": "integer",',
                '-     "size": 16',
                "-   },",
                "  ]",
            ]

            r = cli_runner.invoke(["diff"])
            assert r.exit_code == 0, r.stderr
            assert r.stdout.splitlines()[:169] == EXPECTED_META_DIFF

            r = cli_runner.invoke(["commit", "-m", "Edit meta items"])
            assert r.exit_code == 0, r.stderr

            r = cli_runner.invoke(["show"])
            assert (
                r.stdout.splitlines()[4:175]
                == ["    Edit meta items", ""] + EXPECTED_META_DIFF
            )


def test_working_copy_commit_las(
    cli_runner, data_archive, data_archive_readonly, monkeypatch, requires_pdal
):
    monkeypatch.setenv("X_KART_POINT_CLOUDS", "1")

    with data_archive_readonly("point-cloud/las-autzen.tgz") as autzen:
        with data_archive("point-cloud/auckland.tgz") as repo_path:
            tiles_path = repo_path / "auckland"
            assert tiles_path.is_dir()

            shutil.copy(autzen / "autzen.las", tiles_path / "autzen.las")

            r = cli_runner.invoke(["commit", "-m", "Add single LAS file"])
            assert r.exit_code == WORKING_COPY_OR_IMPORT_CONFLICT
            assert (
                "Committing more than one 'format.json' for 'auckland' is not supported"
                in r.stderr
            )

            # If all the old tiles are deleted, there will no longer be a conflict, but we still can't commit LAS files.
            for tile in tiles_path.glob("auckland_*.copc.laz"):
                tile.unlink()

            r = cli_runner.invoke(
                ["commit", "-m", "Replace entire dataset with single LAS file"]
            )
            assert r.exit_code == WORKING_COPY_OR_IMPORT_CONFLICT
            assert "Committing LAS tiles is not supported" in r.stderr


def test_working_copy_commit_and_convert_to_copc(
    cli_runner, data_archive, data_archive_readonly, monkeypatch, requires_pdal
):
    monkeypatch.setenv("X_KART_POINT_CLOUDS", "1")
    with data_archive_readonly("point-cloud/laz-auckland.tgz") as data_dir:
        with data_archive("point-cloud/auckland.tgz") as repo_path:
            tiles_path = repo_path / "auckland"
            assert tiles_path.is_dir()

            shutil.copy(data_dir / "auckland_0_0.laz", tiles_path / "new.laz")

            # The non-COPC LAZ file conflicts with the COPC dataset.
            r = cli_runner.invoke(["diff"])
            assert r.exit_code == 0, r.stderr
            assert r.stdout.splitlines()[:29] == [
                "--- auckland:meta:format.json",
                "+++ auckland:meta:format.json",
                "- {",
                '-   "compression": "laz",',
                '-   "lasVersion": "1.4",',
                '-   "optimization": "copc",',
                '-   "optimizationVersion": "1.0",',
                '-   "pointDataRecordFormat": 7,',
                '-   "pointDataRecordLength": 36',
                "- }",
                "+ <<<<<<< ",
                "+ {",
                '+   "compression": "laz",',
                '+   "lasVersion": "1.4",',
                '+   "optimization": "copc",',
                '+   "optimizationVersion": "1.0",',
                '+   "pointDataRecordFormat": 7,',
                '+   "pointDataRecordLength": 36',
                "+ }",
                "+ ======== ",
                "+ {",
                '+   "compression": "laz",',
                '+   "lasVersion": "1.2",',
                '+   "optimization": null,',
                '+   "optimizationVersion": null,',
                '+   "pointDataRecordFormat": 3,',
                '+   "pointDataRecordLength": 34',
                "+ }",
                "+ >>>>>>> ",
            ]

            r = cli_runner.invoke(["diff", "--convert-to-dataset-format"])
            assert r.exit_code == 0, r.stderr
            assert "auckland:meta:format.json" not in r.stdout
            assert r.stdout.splitlines() == [
                "+++ auckland:tile:new",
                "+                                     name = new.copc.laz",
                "+                               sourceName = new.laz",
                "+                              crs84Extent = 174.7382443,174.7496594,-36.85123712,-36.84206322,-1.66,99.83",
                "+                                   format = laz-1.4/copc-1.0",
                "+                             sourceFormat = laz-1.2",
                "+                             nativeExtent = 1754987.85,1755987.77,5920219.76,5921219.64,-1.66,99.83",
                "+                               pointCount = 4231",
                "+                                sourceOid = sha256:6b980ce4d7f4978afd3b01e39670e2071a792fba441aca45be69be81cb48b08c",
                "+                               sourceSize = 51489",
            ]

            r = cli_runner.invoke(["commit", "-m", "Commit new LAZ tile"])
            assert r.exit_code == WORKING_COPY_OR_IMPORT_CONFLICT

            r = cli_runner.invoke(
                ["commit", "--convert-to-dataset-format", "-m", "Commit new LAZ tile"]
            )
            assert r.exit_code == 0, r.stderr

            r = cli_runner.invoke(["show"])
            assert r.exit_code == 0, r.stderr
            output = r.stdout.splitlines()
            assert output[4:-2] == [
                "    Commit new LAZ tile",
                "",
                "+++ auckland:tile:new",
                "+                                     name = new.copc.laz",
                "+                              crs84Extent = 174.7382443,174.7496594,-36.85123712,-36.84206322,-1.66,99.83",
                "+                                   format = laz-1.4/copc-1.0",
                "+                             nativeExtent = 1754987.85,1755987.77,5920219.76,5921219.64,-1.66,99.83",
                "+                               pointCount = 4231",
            ]
            assert re.match(r"\+\s+oid = sha256:[0-9a-f]{64}", output[-2])
            assert re.match(r"\+\s+size = [0-9]{5}", output[-1])

            r = cli_runner.invoke(["status"])
            assert r.exit_code == 0, r.stderr
            assert r.stdout.splitlines()[-1] == "Nothing to commit, working copy clean"

            assert tiles_path.is_dir()
            assert not (tiles_path / "new.laz").exists()
            assert (tiles_path / "new.copc.laz").is_file()

            converted_tile_metadata = extract_pc_tile_metadata(
                tiles_path / "new.copc.laz"
            )
            assert converted_tile_metadata["tile"]["format"] == "laz-1.4/copc-1.0"
            assert converted_tile_metadata["tile"]["pointCount"] == 4231
