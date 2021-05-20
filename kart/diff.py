import logging
import re
import sys
from pathlib import Path

import click

from .crs_util import CoordinateReferenceString
from .diff_output import (  # noqa - used from globals()
    diff_output_text,
    diff_output_json,
    diff_output_geojson,
    diff_output_quiet,
    diff_output_html,
)
from .diff_structs import RepoDiff, DatasetDiff
from .exceptions import (
    InvalidOperation,
    NotFound,
    NO_WORKING_COPY,
    UNCATEGORIZED_ERROR,
)
from .filter_util import build_feature_filter, UNFILTERED
from .output_util import dump_json_output
from .repo import KartRepoState
from . import diff_estimation


L = logging.getLogger("kart.diff")


def get_dataset_diff(
    base_rs, target_rs, working_copy, dataset_path, ds_filter=UNFILTERED
):
    diff = DatasetDiff()

    if base_rs != target_rs:
        # diff += base_rs<>target_rs
        base_ds = base_rs.datasets.get(dataset_path)
        target_ds = target_rs.datasets.get(dataset_path)

        params = {}
        if not base_ds:
            base_ds, target_ds = target_ds, base_ds
            params["reverse"] = True

        diff_cc = base_ds.diff(target_ds, ds_filter=ds_filter, **params)
        L.debug("commit<>commit diff (%s): %s", dataset_path, repr(diff_cc))
        diff += diff_cc

    if working_copy:
        # diff += target_rs<>working_copy
        target_ds = target_rs.datasets.get(dataset_path)
        diff_wc = working_copy.diff_db_to_tree(target_ds, ds_filter=ds_filter)
        L.debug(
            "commit<>working_copy diff (%s): %s",
            dataset_path,
            repr(diff_wc),
        )
        diff += diff_wc

    diff.prune()
    return diff


def get_repo_diff(base_rs, target_rs, feature_filter=UNFILTERED):
    """Generates a Diff for every dataset in both RepoStructures."""
    base_ds_paths = {ds.path for ds in base_rs.datasets}
    target_ds_paths = {ds.path for ds in target_rs.datasets}
    all_ds_paths = base_ds_paths | target_ds_paths

    if feature_filter is not UNFILTERED:
        all_ds_paths = all_ds_paths.intersection(feature_filter.keys())

    result = RepoDiff()
    for ds_path in sorted(all_ds_paths):
        ds_diff = get_dataset_diff(
            base_rs, target_rs, None, ds_path, feature_filter[ds_path]
        )
        result[ds_path] = ds_diff

    result.prune()
    return result


def get_common_ancestor(repo, rs1, rs2):
    for rs in rs1, rs2:
        if not rs.commit:
            raise click.UsageError(
                f"The .. operator works on commits, not trees - {rs.id} is a tree. (Perhaps try the ... operator)"
            )
    ancestor_id = repo.merge_base(rs1.id, rs2.id)
    if not ancestor_id:
        raise InvalidOperation(
            "The .. operator tries to find the common ancestor, but no common ancestor was found. Perhaps try the ... operator."
        )
    return repo.structure(ancestor_id)


def _parse_diff_commit_spec(repo, commit_spec):
    # Parse <commit> or <commit>...<commit>
    commit_spec = commit_spec or "HEAD"
    commit_parts = re.split(r"(\.{2,3})", commit_spec)

    if len(commit_parts) == 3:
        # Two commits specified - base and target. We diff base<>target.
        base_rs = repo.structure(commit_parts[0] or "HEAD")
        target_rs = repo.structure(commit_parts[2] or "HEAD")
        if commit_parts[1] == "..":
            # A   C    A...C is A<>C
            #  \ /     A..C  is B<>C
            #   B      (git log semantics)
            base_rs = get_common_ancestor(repo, base_rs, target_rs)
        working_copy = None
    else:
        # When one commit is specified, it is base, and we diff base<>working_copy.
        # When no commits are specified, base is HEAD, and we do the same.
        # We diff base<>working_copy by diffing base<>target + target<>working_copy,
        # and target is set to HEAD.
        base_rs = repo.structure(commit_parts[0])
        target_rs = repo.structure("HEAD")
        working_copy = repo.working_copy
        if not working_copy:
            raise NotFound("No working copy", exit_code=NO_WORKING_COPY)
        working_copy.assert_db_tree_match(target_rs.tree)
    return base_rs, target_rs, working_copy


def diff_with_writer(
    ctx,
    diff_writer,
    *,
    output_path="-",
    exit_code,
    json_style="pretty",
    commit_spec,
    filters,
    target_crs=None,
):
    """
    Calculates the appropriate diff from the arguments,
    and writes it using the given writer contextmanager.

      ctx: the click context
      diff_writer: One of the `diff_output_*` contextmanager factories.
                   When used as a contextmanager, the diff_writer should yield
                   another callable which accepts (dataset, diff) arguments
                   and writes the output by the time it exits.
      output_path: The output path, or a file-like object, or the string '-' to use stdout.
      exit_code:   If True, the process will exit with code 1 if the diff is non-empty.
      commit_spec: The commit-ref or -refs to diff.
      filters:     Limit the diff to certain datasets or features.
      target_crs:  An osr.SpatialReference object, or None
    """
    try:
        if isinstance(output_path, str) and output_path != "-":
            output_path = Path(output_path).expanduser()

        repo = ctx.obj.get_repo(allowed_states=KartRepoState.ALL_STATES)

        base_rs, target_rs, working_copy = _parse_diff_commit_spec(repo, commit_spec)

        # Parse [<dataset>[:pk]...]
        feature_filter = build_feature_filter(filters)

        base_str = base_rs.id
        target_str = "working-copy" if working_copy else target_rs.id
        L.debug("base=%s target=%s", base_str, target_str)

        base_ds_paths = {ds.path for ds in base_rs.datasets}
        target_ds_paths = {ds.path for ds in target_rs.datasets}
        all_ds_paths = base_ds_paths | target_ds_paths

        if feature_filter is not UNFILTERED:
            all_ds_paths = all_ds_paths.intersection(feature_filter.keys())

        dataset_geometry_transforms = {}
        if target_crs is not None:
            for ds_path in all_ds_paths:
                ds = base_rs.datasets.get(ds_path) or target_rs.datasets.get(ds_path)
                transform = ds.get_geometry_transform(target_crs)
                if transform is not None:
                    dataset_geometry_transforms[ds_path] = transform

        writer_params = {
            "repo": repo,
            "base": base_rs,
            "target": target_rs,
            "output_path": output_path,
            "dataset_count": len(all_ds_paths),
            "json_style": json_style,
            "dataset_geometry_transforms": dataset_geometry_transforms,
        }

        L.debug(
            "base_rs %s == target_rs %s: %s",
            repr(base_rs),
            repr(target_rs),
            base_rs == target_rs,
        )

        num_changes = 0
        feature_change_counts = {}
        with diff_writer(**writer_params) as w:
            for ds_path in all_ds_paths:
                diff = get_dataset_diff(
                    base_rs,
                    target_rs,
                    working_copy,
                    ds_path,
                    feature_filter[ds_path],
                )
                ds = base_rs.datasets.get(ds_path) or target_rs.datasets.get(ds_path)
                num_changes += len(diff)
                if "feature" in diff:
                    feature_change_counts[ds_path] = len(diff["feature"])
                L.debug("overall diff (%s): %s", ds_path, repr(diff))
                w(ds, diff)

        if not working_copy:
            # store this count in case it's needed later
            repo.diff_annotations.store(
                base_rs=base_rs,
                target_rs=target_rs,
                annotation_type="feature-change-counts-exact",
                data=feature_change_counts,
            )

    except click.ClickException as e:
        L.debug("Caught ClickException: %s", e)
        if exit_code and e.exit_code == 1:
            e.exit_code = UNCATEGORIZED_ERROR
        raise
    except Exception as e:
        L.debug("Caught non-ClickException: %s", e)
        if exit_code:
            click.secho(f"Error: {e}", fg="red", file=sys.stderr)
            raise SystemExit(UNCATEGORIZED_ERROR) from e
        else:
            raise
    else:
        if exit_code and num_changes:
            sys.exit(1)


def feature_count_diff(
    ctx,
    output_format,
    commit_spec,
    output_path,
    exit_code,
    json_style,
    accuracy,
):
    if output_format not in ("text", "json"):
        raise click.UsageError("--only-feature-count requires text or json output")

    repo = ctx.obj.repo
    base_rs, target_rs, working_copy = _parse_diff_commit_spec(repo, commit_spec)

    estimator = diff_estimation.FeatureCountEstimator(repo)
    dataset_change_counts = estimator.get_estimate(
        base_rs, target_rs, working_copy=working_copy, accuracy=accuracy
    )

    if output_format == "text":
        if dataset_change_counts:
            for dataset_name, count in sorted(dataset_change_counts.items()):
                click.secho(f"{dataset_name}:", bold=True)
                click.echo(f"\t{count} features changed")
        else:
            click.echo("0 features changed")
    elif output_format == "json":
        dump_json_output(dataset_change_counts, output_path, json_style=json_style)
    if dataset_change_counts and exit_code:
        sys.exit(1)


def total_feature_size_diff(
    ctx,
    output_format,
    commit_spec,
    output_path,
    exit_code,
    json_style,
    accuracy,
):
    if output_format not in ("text", "json"):
        raise click.UsageError("--only-total-blob-size requires text or json output")

    repo = ctx.obj.repo
    base_rs, target_rs, working_copy = _parse_diff_commit_spec(repo, commit_spec)

    estimator = diff_estimation.TotalFeatureSizeEstimator(repo)

    dataset_total_feature_sizes = estimator.get_estimate(
        base_rs, target_rs, working_copy=working_copy, accuracy=accuracy
    )

    if output_format == "text":
        if dataset_total_feature_sizes:
            for dataset_name, size in sorted(dataset_total_feature_sizes.items()):
                click.secho(f"{dataset_name}:", bold=True)
                click.echo(f"\t{size} bytes")
        else:
            click.echo("0 features changed")
    elif output_format == "json":
        dump_json_output(
            dataset_total_feature_sizes, output_path, json_style=json_style
        )
    if dataset_total_feature_sizes and exit_code:
        sys.exit(1)


@click.command()
@click.pass_context
@click.option(
    "--output-format",
    "-o",
    type=click.Choice(["text", "json", "geojson", "quiet", "feature-count", "html"]),
    default="text",
    help=(
        "Output format. 'quiet' disables all output and implies --exit-code.\n"
        "'html' attempts to open a browser unless writing to stdout ( --output=- )"
    ),
)
@click.option(
    "--exit-code",
    is_flag=True,
    help="Make the program exit with codes similar to diff(1). That is, it exits with 1 if there were differences and 0 means no differences.",
)
@click.option(
    "--crs",
    type=CoordinateReferenceString(encoding="utf-8"),
    help="Reproject geometries into the given coordinate reference system. Accepts: 'EPSG:<code>'; proj text; OGC WKT; OGC URN; PROJJSON.)",
)
@click.option(
    "--output",
    "output_path",
    help="Output to a specific file/directory instead of stdout.",
    type=click.Path(writable=True, allow_dash=True),
)
@click.option(
    "--json-style",
    type=click.Choice(["extracompact", "compact", "pretty"]),
    default="pretty",
    help="How to format the output. Only used with -o json or -o geojson",
)
@click.option(
    "--only-feature-count",
    default=None,
    type=click.Choice(diff_estimation.FeatureCountEstimator.ACCURACY_CHOICES),
    help=(
        "Returns only a feature count (the number of features modified in this diff). "
        "If the value is 'exact', the feature count is exact (this may be slow.) "
        "Otherwise, the feature count will be approximated with varying levels of accuracy."
    ),
)
@click.option(
    "--only-total-feature-size",
    default=None,
    type=click.Choice(diff_estimation.TotalFeatureSizeEstimator.ACCURACY_CHOICES),
    help=(
        "Returns only the sum of the blob sizes for the features involved in this diff. "
        "If the value is 'exact', the total feature size is exact (this may be slow.) "
        "Otherwise, the feature size will be approximated with varying levels of accuracy. "
        "This option is much slower than --only-feature-count; use only when really required."
    ),
)
@click.argument("commit_spec", required=False, nargs=1)
@click.argument("filters", nargs=-1)
def diff(
    ctx,
    output_format,
    crs,
    output_path,
    exit_code,
    json_style,
    only_feature_count,
    only_total_feature_size,
    commit_spec,
    filters,
):
    """
    Show changes between two commits, or between a commit and the working copy.

    COMMIT_SPEC -

    - if not supplied, the default is HEAD, to diff between HEAD and the working copy.

    - if a single ref is supplied: commit-A - diffs between commit-A and the working copy.

    - if supplied with the form: commit-A...commit-B - diffs between commit-A and commit-B.

    - if supplied with the form: commit-A..commit-B - diffs between (the common ancestor of
    commit-A and commit-B) and (commit-B).

    To list only particular conflicts, supply one or more FILTERS of the form [DATASET[:PRIMARY_KEY]]
    """
    if only_feature_count:
        return feature_count_diff(
            ctx,
            output_format,
            commit_spec,
            output_path,
            exit_code,
            json_style,
            only_feature_count,
        )
    if only_total_feature_size:
        return total_feature_size_diff(
            ctx,
            output_format,
            commit_spec,
            output_path,
            exit_code,
            json_style,
            only_total_feature_size,
        )

    diff_writer = globals()[f"diff_output_{output_format}"]
    if output_format == "quiet":
        exit_code = True

    return diff_with_writer(
        ctx,
        diff_writer,
        output_path=output_path,
        exit_code=exit_code,
        json_style=json_style,
        commit_spec=commit_spec,
        filters=filters,
        target_crs=crs,
    )
