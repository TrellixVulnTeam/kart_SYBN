import functools
import itertools
import json
import logging
import re
import time
from collections import defaultdict, deque

import click
import pygit2

from . import core, gpkg
from .exceptions import (
    NotFound,
    NotYetImplemented,
    InvalidOperation,
    NO_COMMIT,
    PATCH_DOES_NOT_APPLY,
)
from .filter_util import UNFILTERED


L = logging.getLogger("sno.structure")


class RepositoryStructure:
    @staticmethod
    def lookup(repo, key):
        L.debug(f"key={key}")
        if isinstance(key, pygit2.Oid):
            key = key.hex
        try:
            obj = repo.revparse_single(key)
        except KeyError:
            raise NotFound(f"{key} is not a commit or tree", exit_code=NO_COMMIT)

        try:
            return RepositoryStructure(repo, commit=obj.peel(pygit2.Commit))
        except pygit2.InvalidSpecError:
            pass

        try:
            return RepositoryStructure(repo, tree=obj.peel(pygit2.Tree))
        except pygit2.InvalidSpecError:
            pass

        raise NotFound(
            f"{key} is a {obj.type_str}, not a commit or tree", exit_code=NO_COMMIT
        )

    def __init__(self, repo, commit=None, tree=None):
        self.L = logging.getLogger(self.__class__.__qualname__)
        self.repo = repo

        # If _commit is not None, self.tree -> self._commit.tree, so _tree is not set.
        if commit is not None:
            self._commit = commit
        elif tree is not None:
            self._commit = None
            self._tree = tree
        elif self.repo.is_empty:
            self._commit = None
            self._tree = None
        else:
            self._commit = self.repo.head.peel(pygit2.Commit)

    def __getitem__(self, path):
        """ Get a specific dataset by path """
        return self.get_at(path, self.tree)

    def __eq__(self, other):
        return other and (self.repo.path == other.repo.path) and (self.id == other.id)

    def __repr__(self):
        if self._commit is not None:
            return f"RepoStructure<{self.repo.path}@{self._commit.id}>"
        elif self._tree is not None:
            return f"RepoStructure<{self.repo.path}@tree={self._tree.id}>"
        else:
            return f"RepoStructure<{self.repo.path} <empty>>"

    def decode_path(self, path):
        """
        Given a path in the sno repository - eg "table_A/.sno-table/49/3e/Bg==" -
        returns a tuple in either of the following forms:
        1. (table, "feature", primary_key)
        2. (table, "meta", metadata_file_path)
        """
        table, table_path = path.split("/.sno-table/", 1)
        return (table,) + self.get(table).decode_path(table_path)

    def get(self, path):
        try:
            return self.get_at(path, self.tree)
        except KeyError:
            return None

    def get_at(self, path, tree):
        """ Get a specific dataset by path using a specified Tree """
        try:
            o = tree[path]
        except KeyError:
            raise

        if isinstance(o, pygit2.Tree):
            ds = DatasetStructure.instantiate(o, path)
            return ds

        raise KeyError(f"No valid dataset found at '{path}'")

    def __iter__(self):
        """ Iterate over available datasets in this repository """
        return self.iter_at(self.tree)

    def iter_at(self, tree):
        """ Iterate over available datasets in this repository using a specified Tree """
        to_examine = deque([("", tree)])

        while to_examine:
            path, tree = to_examine.popleft()

            for o in tree:
                # ignore everything other than directories
                if isinstance(o, pygit2.Tree):

                    if path:
                        te_path = "/".join([path, o.name])
                    else:
                        te_path = o.name

                    try:
                        ds = DatasetStructure.instantiate(o, te_path)
                        yield ds
                    except IntegrityError:
                        self.L.warn(
                            "Error loading dataset from %s, ignoring tree",
                            te_path,
                            exc_info=True,
                        )
                    except ValueError:
                        # examine inside this directory
                        to_examine.append((te_path, o))

    @property
    def id(self):
        obj = self._commit or self._tree
        return obj.id if obj is not None else None

    @property
    def short_id(self):
        obj = self._commit or self._tree
        return obj.short_id if obj is not None else None

    @property
    def head_commit(self):
        return self._commit

    @property
    def tree(self):
        if self._commit is not None:
            return self._commit.peel(pygit2.Tree)
        return self._tree

    @property
    def working_copy(self):
        from .working_copy import WorkingCopy

        if getattr(self, "_working_copy", None) is None:
            self._working_copy = WorkingCopy.open(self.repo)

        return self._working_copy

    @working_copy.deleter
    def working_copy(self):
        wc = self.working_copy
        if wc:
            wc.delete()
        del self._working_copy

    def create_tree_from_diff(self, diff, orig_tree=None):
        """
        Given a tree and a diff, returns a new tree created by applying the diff.

        Doesn't create any commits or modify the working copy at all.

        If orig_tree is not None, the diff is applied from that tree.
        Otherwise, uses the tree at the head of the repo.
        """
        if orig_tree is None:
            orig_tree = self.tree

        git_index = pygit2.Index()
        git_index.read_tree(orig_tree)

        for ds in self.iter_at(orig_tree):
            ds.write_index(diff[ds], git_index, self.repo)

        L.info("Writing tree...")
        new_tree_oid = git_index.write_tree(self.repo)
        L.info(f"Tree sha: {new_tree_oid}")
        return new_tree_oid

    def commit(
        self, wcdiff, message, *, author=None, committer=None, allow_empty=False,
    ):
        """
        Update the repository structure and write the updated data to the tree
        as a new commit, setting HEAD to the new commit.
        NOTE: Doesn't update working-copy meta or tracking tables, this is the
        responsibility of the caller.
        """
        tree = self.tree

        git_index = pygit2.Index()
        git_index.read_tree(tree)

        new_tree_oid = self.create_tree_from_diff(wcdiff)
        L.info("Committing...")
        user = self.repo.default_signature
        # this will also update the ref (branch) to point to the current commit
        new_commit = self.repo.create_commit(
            "HEAD",  # reference_name
            author or user,  # author
            committer or user,  # committer
            message,  # message
            new_tree_oid,  # tree
            [self.repo.head.target],  # parents
        )
        L.info(f"Commit: {new_commit}")

        # TODO: update reflog
        return new_commit


class IntegrityError(ValueError):
    pass


class DatasetStructure:
    DEFAULT_IMPORT_VERSION = "1.0"
    META_PATH = "meta"

    def __init__(self, tree, path):
        if self.__class__ is DatasetStructure:
            raise TypeError("Use DatasetStructure.instantiate()")

        self.tree = tree
        self.path = path.strip("/")
        self.name = self.path.replace("/", "__")
        self.L = logging.getLogger(self.__class__.__qualname__)

    def __repr__(self):
        return f"<{self.__class__.__name__}: {self.path}>"

    @classmethod
    @functools.lru_cache(maxsize=1)
    def all_versions(cls):
        """ Get all supported Dataset Structure versions """
        from . import dataset1  # noqa
        from . import dataset2  # noqa

        def _get_subclasses(klass):
            for c in klass.__subclasses__():
                yield c
                yield from _get_subclasses(c)

        return tuple(_get_subclasses(cls))

    @classmethod
    @functools.lru_cache(maxsize=1)
    def version_numbers(cls):
        versions = [klass.VERSION_IMPORT for klass in cls.all_versions()]
        # default first, then newest to oldest
        versions.sort(
            key=lambda v: (
                v == DatasetStructure.DEFAULT_IMPORT_VERSION,
                [int(i) for i in v.split(".")],
            ),
            reverse=True,
        )
        return tuple(versions)

    @classmethod
    def for_version(cls, version=None):
        if version is None:
            version = cls.DEFAULT_IMPORT_VERSION

        if isinstance(version, int) or len(version) == 1:
            version = f"{version}.0"

        for klass in cls.all_versions():
            if klass.VERSION_IMPORT == version:
                return klass

        raise ValueError(f"No DatasetStructure for v{version}")

    @classmethod
    def instantiate(cls, tree, path):
        """ Load a DatasetStructure from a Tree """
        L = logging.getLogger(cls.__qualname__)

        if not isinstance(tree, pygit2.Tree):
            raise TypeError(f"Expected Tree object, got {type(tree)}")

        for version_klass in cls.all_versions():
            try:
                blob = tree[version_klass.VERSION_PATH]
            except KeyError:
                continue
            else:
                L.debug(
                    "Found candidate %sx tree at: %s",
                    version_klass.VERSION_SPECIFIER,
                    path,
                )

            if not isinstance(blob, pygit2.Blob):
                raise IntegrityError(
                    f"{version_klass.__name__}: {path}/{version_klass.VERSION_PATH} isn't a blob ({blob.type_str})"
                )

            if len(blob.data) <= 3:
                version = blob.data.decode("utf8")
            else:
                try:
                    d = json.loads(blob.data)
                    version = d.get("version", None)
                except Exception as e:
                    raise IntegrityError(
                        f"{version_klass.__name__}: Couldn't load version file from: {path}/{version_klass.VERSION_PATH}"
                    ) from e

            if version and version.startswith(version_klass.VERSION_SPECIFIER):
                L.debug("Found %s dataset at: %s", version, path)
                return version_klass(tree, path)
            else:
                continue

        raise ValueError(
            f"{path}: Couldn't find any Dataset Structure version that matched"
        )

    # useful methods

    def full_path(self, rel_path):
        """Given a path relative to this dataset, returns its full path from the repo root."""
        return f"{self.path}/{rel_path}"

    def rel_path(self, full_path):
        """Given a full path to something in this dataset, returns its path relative to the dataset."""
        if not full_path.startswith(f"{self.path}/"):
            raise ValueError(f"{full_path} is not a descendant of {self.path}")
        return full_path[len(self.path) + 1 :]

    @property
    @functools.lru_cache(maxsize=1)
    def version(self):
        return self.get_meta_item("version")["version"]

    @property
    @functools.lru_cache(maxsize=1)
    def meta_tree(self):
        return self.tree / self.META_PATH

    @functools.lru_cache()
    def get_meta_item(self, name):
        meta_tree = self.meta_tree
        try:
            o = meta_tree / name
        except KeyError:
            return None

        if not isinstance(o, pygit2.Blob):
            raise ValueError(f"meta/{name} is a {o.type_str}, expected blob")

        return json.loads(o.data)

    def iter_meta_items(self, exclude=None):
        exclude = set(exclude or [])
        for top_tree, top_path, subtree_names, blob_names in core.walk_tree(
            self.meta_tree
        ):
            for name in blob_names:
                if name in exclude:
                    continue

                meta_path = "/".join([top_path, name]) if top_path else name
                yield (meta_path, self.get_meta_item(meta_path))

            subtree_names[:] = [n for n in subtree_names if n not in exclude]

    @property
    @functools.lru_cache(maxsize=1)
    def has_geometry(self):
        return self.geom_column_name is not None

    @property
    @functools.lru_cache(maxsize=1)
    def geom_column_name(self):
        meta_geom = self.get_meta_item("gpkg_geometry_columns")
        return meta_geom["column_name"] if meta_geom else None

    def cast_primary_key(self, pk_value):
        pk_type = self.primary_key_type

        if pk_value is not None:
            # https://www.sqlite.org/datatype3.html
            # 3.1. Determination Of Column Affinity
            if "INT" in pk_type:
                pk_value = int(pk_value)
            elif re.search("TEXT|CHAR|CLOB", pk_type):
                pk_value = str(pk_value)

        return pk_value

    def get_feature(self, pk_value):
        raise NotImplementedError()

    def feature_tuples(self, col_names, **kwargs):
        """ Feature iterator yielding tuples, ordered by the columns from col_names """

        # not optimised in V0
        for k, f in self.features():
            yield tuple(f[c] for c in col_names)

    def import_meta_items(self, source):
        yield ("version", {"version": self.VERSION_IMPORT})
        for name, value in source.build_meta_info():
            viter = value if isinstance(value, (list, tuple)) else [value]

            for v in viter:
                if v and "table_name" in v:
                    v["table_name"] = self.name

            yield (name, value)

    def import_iter_meta_blobs(self, repo, source):
        for name, value in self.import_meta_items(source):
            yield (
                f"{self.path}/{self.META_PATH}/{name}",
                json.dumps(value).encode("utf8"),
            )

    RTREE_INDEX_EXTENSIONS = ("sno-idxd", "sno-idxi")

    def build_spatial_index(self, path):
        """
        Internal proof-of-concept method for building a spatial index across the repository.

        Uses Rtree (libspatialindex underneath): http://toblerity.org/rtree/index.html
        """
        import rtree

        if not self.has_geometry:
            raise ValueError("No geometry to index")

        def _indexer():
            t0 = time.monotonic()

            c = 0
            for (pk, geom) in self.feature_tuples(
                [self.primary_key, self.geom_column_name]
            ):
                c += 1
                if geom is None:
                    continue

                e = gpkg.geom_envelope(geom)
                yield (pk, e, None)

                if c % 50000 == 0:
                    print(f"  {c} features... @{time.monotonic()-t0:.1f}s")

        p = rtree.index.Property()
        p.dat_extension = self.RTREE_INDEX_EXTENSIONS[0]
        p.idx_extension = self.RTREE_INDEX_EXTENSIONS[1]
        p.leaf_capacity = 1000
        p.fill_factor = 0.9
        p.overwrite = True
        p.dimensionality = 2

        t0 = time.monotonic()
        idx = rtree.index.Index(path, _indexer(), properties=p, interleaved=False)
        t1 = time.monotonic()
        b = idx.bounds
        c = idx.count(b)
        del idx
        t2 = time.monotonic()
        print(f"Indexed {c} features ({b}) in {t1-t0:.1f}s; flushed in {t2-t1:.1f}s")

    def get_spatial_index(self, path):
        """
        Retrieve a spatial index built with build_spatial_index().

        Query with .nearest(coords), .intersection(coords), .count(coords)
        http://toblerity.org/rtree/index.html
        """
        import rtree

        p = rtree.index.Property()
        p.dat_extension = self.RTREE_INDEX_EXTENSIONS[0]
        p.idx_extension = self.RTREE_INDEX_EXTENSIONS[1]

        idx = rtree.index.Index(path, properties=p)
        return idx

    def diff(self, other, pk_filter=UNFILTERED, reverse=False):
        """
        Generates a Diff from self -> other.
        If reverse is true, generates a diff from other -> self.
        """
        # TODO - support multiple primary keys.

        candidates_ins = defaultdict(list)
        candidates_upd = {}
        candidates_del = defaultdict(list)

        params = {}
        if reverse:
            params = {"swap": True}

        if other is None:
            diff_index = self.tree.diff_to_tree(**params)
            self.L.debug(
                "diff (%s -> None / %s): %s changes",
                self.tree.id,
                "R" if reverse else "F",
                len(diff_index),
            )
        else:
            diff_index = self.tree.diff_to_tree(other.tree, **params)
            self.L.debug(
                "diff (%s -> %s / %s): %s changes",
                self.tree.id,
                other.tree.id,
                "R" if reverse else "F",
                len(diff_index),
            )

        if reverse:
            old, new = other, self
        else:
            old, new = self, other

        for d in diff_index.deltas:
            self.L.debug(
                "diff(): %s %s %s", d.status_char(), d.old_file.path, d.new_file.path
            )

            if d.old_file and d.old_file.path.startswith(".sno-table/meta/"):
                continue
            elif d.new_file and d.new_file.path.startswith(".sno-table/meta/"):
                continue

            if d.status == pygit2.GIT_DELTA_DELETED:
                old_pk = old.decode_path_to_1pk(d.old_file.path)
                if str(old_pk) not in pk_filter:
                    continue

                self.L.debug("diff(): D %s (%s)", d.old_file.path, old_pk)

                old_feature = old.get_feature(old_pk, ogr_geoms=False)
                candidates_del[str(old_pk)].append((str(old_pk), old_feature))

            elif d.status == pygit2.GIT_DELTA_MODIFIED:
                old_pk = old.decode_path_to_1pk(d.old_file.path)
                new_pk = new.decode_path_to_1pk(d.new_file.path)
                if str(old_pk) not in pk_filter and str(new_pk) not in pk_filter:
                    continue

                self.L.debug(
                    "diff(): M %s (%s) -> %s (%s)",
                    d.old_file.path,
                    old_pk,
                    d.new_file.path,
                    new_pk,
                )

                old_feature = old.get_feature(old_pk, ogr_geoms=False)
                new_feature = other.get_feature(new_pk, ogr_geoms=False)
                candidates_upd[str(old_pk)] = (old_feature, new_feature)

            elif d.status == pygit2.GIT_DELTA_ADDED:
                new_pk = new.decode_path_to_1pk(d.new_file.path)
                if str(new_pk) not in pk_filter:
                    continue

                self.L.debug("diff(): A %s (%s)", d.new_file.path, new_pk)

                new_feature = new.get_feature(new_pk, ogr_geoms=False)
                candidates_ins[str(new_pk)].append(new_feature)

            else:
                # GIT_DELTA_RENAMED
                # GIT_DELTA_COPIED
                # GIT_DELTA_IGNORED
                # GIT_DELTA_TYPECHANGE
                # GIT_DELTA_UNMODIFIED
                # GIT_DELTA_UNREADABLE
                # GIT_DELTA_UNTRACKED
                raise NotImplementedError(f"Delta status: {d.status_char()}")

        # detect renames
        for h in list(candidates_del.keys()):
            if h in candidates_ins:
                track_pk, my_obj = candidates_del[h].pop(0)
                other_obj = candidates_ins[h].pop(0)

                candidates_upd[track_pk] = (my_obj, other_obj)

                if not candidates_del[h]:
                    del candidates_del[h]
                if not candidates_ins[h]:
                    del candidates_ins[h]

        from .diff import Diff

        return Diff(
            self,
            meta={},
            inserts=list(itertools.chain(*candidates_ins.values())),
            deletes=dict(itertools.chain(*candidates_del.values())),
            updates=candidates_upd,
        )

    def write_index(self, dataset_diff, index, repo):
        """
        Given a diff that only affects this dataset, write it to the given index + repo.
        Blobs will be created in the repo, and referenced in the index, but the index is
        not committed - this is the responsibility of the caller.
        """
        # TODO - support multiple primary keys.
        pk_field = self.primary_key

        conflicts = False
        if dataset_diff["META"]:
            raise NotYetImplemented(
                "Sorry, committing meta changes is not yet supported"
            )

        for _, old_feature in dataset_diff["D"].items():
            pk = old_feature[pk_field]
            feature_path = self.encode_1pk_to_path(pk)
            if feature_path not in index:
                conflicts = True
                click.echo(f"{self.path}: Trying to delete nonexistent feature: {pk}")
                continue
            index.remove(feature_path)

        for new_feature in dataset_diff["I"]:
            pk = new_feature[pk_field]
            feature_path, feature_data = self.encode_feature(new_feature)
            if feature_path in index:
                conflicts = True
                click.echo(
                    f"{self.path}: Trying to create feature that already exists: {pk}"
                )
                continue
            blob_id = repo.create_blob(feature_data)
            entry = pygit2.IndexEntry(feature_path, blob_id, pygit2.GIT_FILEMODE_BLOB)
            index.add(entry)

        geom_column_name = self.geom_column_name
        for _, (old_feature, new_feature) in dataset_diff["U"].items():
            old_pk = old_feature[pk_field]
            old_feature_path = self.encode_1pk_to_path(old_pk)
            if old_feature_path not in index:
                conflicts = True
                click.echo(
                    f"{self.path}: Trying to update nonexistent feature: {old_pk}"
                )
                continue

            actual_existing_feature = self.get_feature(old_pk)
            if geom_column_name:
                # FIXME: actually compare the geometries here.
                # Turns out this is quite hard - geometries are hard to compare sanely.
                # Even if we add hacks to ignore endianness, WKB seems to vary a bit,
                # and ogr_geometry.Equal(other) can return false for seemingly-identical geometries...
                actual_existing_feature.pop(geom_column_name)
                old_feature = old_feature.copy()
                old_feature.pop(geom_column_name)
            if actual_existing_feature != old_feature:
                conflicts = True
                click.echo(
                    f"{self.path}: Trying to update already-changed feature: {old_pk}"
                )
                continue

            index.remove(old_feature_path)
            new_feature_path, new_feature_data = self.encode_feature(new_feature)
            blob_id = repo.create_blob(new_feature_data)
            entry = pygit2.IndexEntry(
                new_feature_path, blob_id, pygit2.GIT_FILEMODE_BLOB
            )
            index.add(entry)

        if conflicts:
            raise InvalidOperation(
                "Patch does not apply", exit_code=PATCH_DOES_NOT_APPLY,
            )
