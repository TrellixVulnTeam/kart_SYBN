import json
import pytest

from kart.exceptions import INVALID_OPERATION
from kart.tabular.feature_output import feature_as_json
from kart.merge_util import MergedIndex
from kart.repo import KartRepoState, KartRepo


H = pytest.helpers.helpers()


def get_conflict_ids(cli_runner):
    r = cli_runner.invoke(["conflicts", "-s", "--flat", "-o", "json"])
    assert r.exit_code == 0, r.stderr
    return json.loads(r.stdout)["kart.conflicts/v1"]


def delete_remaining_conflicts(cli_runner):
    # TODO - do this with a single resolve when this is supported.
    conflict_ids = get_conflict_ids(cli_runner)
    while conflict_ids:
        r = cli_runner.invoke(["resolve", conflict_ids[0], "--with=delete"])
        assert r.exit_code == 0, r.stderr
        conflict_ids = get_conflict_ids(cli_runner)


def get_json_feature(rs, layer, pk):
    try:
        feature = rs.datasets()[layer].get_feature(pk)
        return feature_as_json(feature, pk)
    except KeyError:
        return None


def test_resolve_with_version(data_archive, cli_runner):
    with data_archive("conflicts/polygons.tgz") as repo_path:
        repo = KartRepo(repo_path)

        r = cli_runner.invoke(["merge", "theirs_branch", "-o", "json"])
        assert r.exit_code == 0, r.stderr
        assert json.loads(r.stdout)["kart.merge/v1"]["conflicts"]
        assert repo.state == KartRepoState.MERGING

        # Can't just complete the merge until we resolve the conflicts.
        r = cli_runner.invoke(["merge", "--continue"])
        assert r.exit_code == INVALID_OPERATION

        conflict_ids = get_conflict_ids(cli_runner)
        resolutions = iter(["ancestor", "ours", "theirs", "delete"])

        # Keep track of which order we resolve the conflicts - each conflict
        # resolved will have a primary key, and we resolve conflicts in
        # primary key order, but the primary keys are not contiguous.
        pk_order = []
        # Each conflict also has an internal "conflict" key - just its index
        # in the original list of conflicts - these are contiguous, but
        # we don't necessarily resolve the conflicts in this order.
        ck_order = []

        while conflict_ids:
            num_conflicts = len(conflict_ids)
            conflict_id = conflict_ids[0]
            pk = conflict_id.split(":", 2)[2]
            pk_order += [pk]

            r = cli_runner.invoke(
                ["resolve", conflict_id, f"--with={next(resolutions)}"]
            )
            assert r.exit_code == 0, r.stderr
            conflict_ids = get_conflict_ids(cli_runner)
            assert len(conflict_ids) == num_conflicts - 1

            resolved_keys = MergedIndex.read_from_repo(repo).resolves.keys()
            ck_order += [k for k in resolved_keys if k not in ck_order]

        assert len(conflict_ids) == 0

        merged_index = MergedIndex.read_from_repo(repo)
        assert len(merged_index.entries) == 237
        assert len(merged_index.conflicts) == 4
        assert len(merged_index.resolves) == 4

        ck0, ck1, ck2, ck3 = ck_order
        # Conflict ck0 is resolved to ancestor, but the ancestor is None.
        assert merged_index.resolves[ck0] == []
        assert merged_index.conflicts[ck0].ancestor is None
        assert merged_index.resolves[ck1] == [merged_index.conflicts[ck1].ours]
        assert merged_index.resolves[ck2] == [merged_index.conflicts[ck2].theirs]
        assert merged_index.resolves[ck3] == []

        r = cli_runner.invoke(["merge", "--continue", "-m", "merge commit"])
        assert r.exit_code == 0, r.stderr
        assert repo.head_commit.message == "merge commit"
        assert repo.state != KartRepoState.MERGING

        merged = repo.structure("HEAD")
        ours = repo.structure("ours_branch")
        theirs = repo.structure("theirs_branch")
        l = H.POLYGONS.LAYER

        pk0, pk1, pk2, pk3 = pk_order
        # Feature at pk0 was resolved to ancestor, which was None.
        assert get_json_feature(merged, l, pk0) is None
        assert get_json_feature(merged, l, pk1) == get_json_feature(ours, l, pk1)
        assert get_json_feature(merged, l, pk2) == get_json_feature(theirs, l, pk2)
        assert get_json_feature(merged, l, pk3) is None


def test_resolve_with_file(data_archive, cli_runner):
    with data_archive("conflicts/polygons.tgz") as repo_path:
        repo = KartRepo(repo_path)

        r = cli_runner.invoke(["diff", "ancestor_branch..ours_branch", "-o", "geojson"])
        assert r.exit_code == 0, r.stderr
        ours_geojson = json.loads(r.stdout)["features"][0]
        assert ours_geojson["id"] == "nz_waca_adjustments:feature:98001:I"

        r = cli_runner.invoke(
            ["diff", "ancestor_branch..theirs_branch", "-o", "geojson"]
        )
        assert r.exit_code == 0, r.stderr
        theirs_geojson = json.loads(r.stdout)["features"][0]
        assert theirs_geojson["id"] == "nz_waca_adjustments:feature:98001:I"

        r = cli_runner.invoke(["merge", "theirs_branch", "-o", "json"])
        assert r.exit_code == 0, r.stderr
        assert json.loads(r.stdout)["kart.merge/v1"]["conflicts"]

        r = cli_runner.invoke(["conflicts", "-s", "-o", "json"])
        assert r.exit_code == 0, r.stderr

        conflicts = json.loads(r.stdout)["kart.conflicts/v1"]
        add_add_conflict_pk = conflicts[H.POLYGONS.LAYER]["feature"][0]
        assert add_add_conflict_pk == 98001

        # These IDs are irrelevant, but we change them to at least be unique.
        ours_geojson["id"] = "ours-feature"
        theirs_geojson["id"] = "theirs-feature"
        # Changing this ID means the two features no long conflict.
        theirs_geojson["properties"]["id"] = 98002

        resolution = {
            "features": [ours_geojson, theirs_geojson],
            "type": "FeatureCollection",
        }
        (repo.workdir_path / "resolution.geojson").write_text(json.dumps(resolution))
        r = cli_runner.invoke(
            [
                "resolve",
                f"{H.POLYGONS.LAYER}:feature:98001",
                "--with-file=resolution.geojson",
            ]
        )
        assert r.exit_code == 0, r.stderr

        merged_index = MergedIndex.read_from_repo(repo)
        assert len(merged_index.entries) == 237
        assert len(merged_index.conflicts) == 4
        assert len(merged_index.resolves) == 1

        ck = next(iter(merged_index.resolves.keys()))
        assert len(merged_index.resolves[ck]) == 2  # Resolved with 2 features

        delete_remaining_conflicts(cli_runner)

        r = cli_runner.invoke(["merge", "--continue", "-m", "merge commit"])
        assert r.exit_code == 0, r.stderr
        assert repo.head_commit.message == "merge commit"
        assert repo.state != KartRepoState.MERGING

        merged = repo.structure("HEAD")
        ours = repo.structure("ours_branch")
        theirs = repo.structure("theirs_branch")
        l = H.POLYGONS.LAYER

        # Both features are present in the merged repo, ours at 98001 and theirs at 98002.
        assert get_json_feature(merged, l, 98001) == get_json_feature(ours, l, 98001)
        # Theirs feature is slightly different - it has a new primary key.
        assert get_json_feature(merged, l, 98002) != get_json_feature(theirs, l, 98001)

        modified_theirs_json = get_json_feature(theirs, l, 98001)
        modified_theirs_json["id"] = 98002
        assert get_json_feature(merged, l, 98002) == modified_theirs_json


def test_resolve_with_workingcopy(data_working_copy, cli_runner):
    with data_working_copy("conflicts/polygons.tgz") as (repo_path, wc_path):
        repo = KartRepo(repo_path)

        r = cli_runner.invoke(["merge", "theirs_branch", "-o", "json"])
        assert r.exit_code == 0, r.stderr
        assert json.loads(r.stdout)["kart.merge/v1"]["conflicts"]

        r = cli_runner.invoke(["conflicts", "-o", "json"])
        assert r.exit_code == 0, r.stderr

        conflicts = json.loads(r.stdout)["kart.conflicts/v1"]
        add_add_conflict = conflicts[H.POLYGONS.LAYER]["feature"]["98001"]
        ours = add_add_conflict["ours"]
        theirs = add_add_conflict["theirs"]
        assert "ancestor" not in add_add_conflict

        assert ours["survey_reference"] == "insert_ours"
        assert theirs["survey_reference"] == "insert_theirs"

        dataset = repo.datasets()[H.POLYGONS.LAYER]
        assert (
            feature_as_json(
                repo.working_copy.tabular.get_feature(dataset, 98001), 98001
            )
            == ours
        )
        with repo.working_copy.tabular.session() as sess:
            sess.execute(
                f"UPDATE nz_waca_adjustments SET survey_reference='merged' WHERE id=98001;"
            )

        merged = ours.copy()
        merged["survey_reference"] = "merged"
        assert (
            feature_as_json(
                repo.working_copy.tabular.get_feature(dataset, 98001), 98001
            )
            == merged
        )

        r = cli_runner.invoke(
            ["resolve", "nz_waca_adjustments:feature:98001", "--with=workingcopy"]
        )
        assert r.exit_code == 0, r.stderr
        assert r.stdout.splitlines() == ["Resolved 1 conflict. 3 conflicts to go."]
        delete_remaining_conflicts(cli_runner)

        r = cli_runner.invoke(["merge", "--continue", "-m", "Merge with theirs_branch"])
        assert r.exit_code == 0, r.stderr

        r = cli_runner.invoke(
            ["show", "HEAD", "-o", "json", "--", "nz_waca_adjustments:feature:98001"]
        )
        assert r.exit_code == 0, r.stderr

        diff = json.loads(r.stdout)["kart.diff/v1+hexwkb"]
        delta = diff["nz_waca_adjustments"]["feature"][0]
        assert delta["-"] == ours
        assert delta["+"] == merged


def test_resolve_schema_conflict(data_working_copy, cli_runner):
    with data_working_copy("polygons") as (repo_path, wc_path):
        repo = KartRepo(repo_path)
        r = cli_runner.invoke(["checkout", "-b", "ancestor_branch"])
        assert r.exit_code == 0, r.stderr
        r = cli_runner.invoke(["checkout", "-b", "theirs_branch"])
        assert r.exit_code == 0, r.stderr

        with repo.working_copy.tabular.session() as sess:
            sess.execute("ALTER TABLE nz_waca_adjustments ADD COLUMN colour TEXT;")
            sess.execute(H.POLYGONS.INSERT, [H.POLYGONS.RECORD])
            sess.execute(
                "UPDATE nz_waca_adjustments SET colour='pink' WHERE id=9999999;"
            )

        r = cli_runner.invoke(["commit", "-m", "their changes"])
        assert r.exit_code == 0, r.stderr

        r = cli_runner.invoke(["checkout", "ancestor_branch"])
        assert r.exit_code == 0, r.stderr
        r = cli_runner.invoke(["checkout", "-b", "ours_branch"])
        assert r.exit_code == 0, r.stderr

        with repo.working_copy.tabular.session() as sess:
            sess.execute("ALTER TABLE nz_waca_adjustments ADD COLUMN flavour TEXT;")
            sess.execute(H.POLYGONS.INSERT, [H.POLYGONS.RECORD])
            sess.execute(
                "UPDATE nz_waca_adjustments SET flavour='vanilla' WHERE id=9999999;"
            )

        r = cli_runner.invoke(["commit", "-m", "our changes"])
        assert r.exit_code == 0, r.stderr

        r = cli_runner.invoke(["merge", "theirs_branch"])
        assert r.exit_code == 0, r.stderr

        r = cli_runner.invoke(["conflicts", "-s"])
        assert r.exit_code == 0, r.stderr
        assert r.stdout.splitlines() == [
            "nz_waca_adjustments:",
            "    nz_waca_adjustments:feature:",
            "        nz_waca_adjustments:feature:9999999",
            "    nz_waca_adjustments:meta:",
            "        nz_waca_adjustments:meta:schema.json",
            "",
        ]

        r = cli_runner.invoke(
            ["resolve", "nz_waca_adjustments:feature:9999999", "--with=theirs"]
        )
        assert r.exit_code == INVALID_OPERATION
        assert (
            "There are still unresolved meta-item conflicts for dataset nz_waca_adjustments"
            in r.stderr
        )

        r = cli_runner.invoke(
            ["resolve", "nz_waca_adjustments:meta:schema.json", "--with=theirs"]
        )
        assert r.exit_code == 0, r.stderr

        r = cli_runner.invoke(["diff"])
        assert r.exit_code == 0, r.stderr
        # The schema in the WC now differs from HEAD, since we accepted their version.
        assert "nz_waca_adjustments:meta:schema.json" in r.stdout

        FEATURE_RESOLVE = """
        {
          "type": "FeatureCollection",
          "features": [
            {
              "type": "Feature",
              "geometry": {
                "type": "MultiPolygon",
                "coordinates": [[[[0.0,0.0],[0.0,0.001],[0.001,0.001],[0.001,0.0],[0.0,0.0]]]]
              },
              "properties": {
                "id": 9999999,
                "date_adjusted": "2019-07-05T13:04:00",
                "survey_reference": "Null Island",
                "adjusted_nodes": 123,
                "colour": "white"
              },
              "id": "nz_waca_adjustments:feature:9999999"
            }
          ]
        }
        """
        (repo_path / "feature_resolve.json").write_text(FEATURE_RESOLVE)

        # Manually resolve a feature conflict
        r = cli_runner.invoke(
            [
                "resolve",
                "nz_waca_adjustments:feature:9999999",
                "--with-file=feature_resolve.json",
            ]
        )
        assert r.exit_code == 0, r.stderr

        r = cli_runner.invoke(
            ["merge", "--continue", "-m", "Merge with 'theirs_branch'"]
        )
        assert r.exit_code == 0, r.stderr

        # Feature resolve is serialised properly using the resolved dataset schema.
        r = cli_runner.invoke(
            ["show", "HEAD", "--", "nz_waca_adjustments:feature:9999999"]
        )
        assert r.exit_code == 0, r.stderr
        assert r.stdout.splitlines()[4:] == [
            "    Merge with 'theirs_branch'",
            "",
            "--- nz_waca_adjustments:feature:9999999",
            "+++ nz_waca_adjustments:feature:9999999",
            "-                         survey_reference = Null Island™ 🗺",
            "+                         survey_reference = Null Island",
            "-                                  flavour = vanilla",
            "+                                   colour = white",
        ]
