import click
import pygit2

from kart.exceptions import (
    InvalidOperation,
    NotFound,
    UNCOMMITTED_CHANGES,
    NO_DATA,
    NO_WORKING_COPY,
)
from kart.key_filters import RepoKeyFilter

# General code for working copies.
# Nothing specific to tabular working copies, nor file-based working copies.


class WorkingCopyDirty(Exception):
    """Exception to abort immediately if working copy is dirty."""

    pass


class WorkingCopyTreeMismatch(ValueError):
    """Error for if the tree id stored in state table doesn't match the one at HEAD."""

    def __init__(self, working_copy_tree_id, expected_tree_id):
        self.working_copy_tree_id = working_copy_tree_id
        self.expected_tree_id = expected_tree_id

    def __str__(self):
        return f"Working Copy is tree {self.working_copy_tree_id}; expecting {self.expected_tree_id}"


class WorkingCopy:
    """
    Interface through which the various working copy types are accessed.
    Currently only the tabular working copy is accessible here.
    """

    DONT_RESET = object()

    def __init__(self, repo):
        self.repo = repo

    def assert_exists(self, error_message=None):
        """
        Make sure that the working copy exists, print the provided error message if it does not, plus some extra context.
        Now that the working copy can potentially have more than one part, its not simple to say if it exists or not.
        If a particular part doesn't exist, we would generally treat that the same as if that part existed but had no diffs.
        However, if the user has spefically asked for a WC operation, and no WC parts at all exist, we want to warn the user.
        That is what this function is for.
        """

        if any(self.parts()):
            return

        error_message = f"{error_message}:\n" if error_message else ""

        if self.repo.head_is_unborn:
            raise NotFound(
                f'{error_message}Empty repository.\n  (use "kart import" to add some data)',
                exit_code=NO_DATA,
            )
        if self.repo.is_bare:
            raise NotFound(
                f'{error_message}Repository is "bare": it has no working copy.',
                exit_code=NO_WORKING_COPY,
            )
        raise NotFound(
            f'{error_message}Repository has no working copy.\n  (use "kart create-workingcopy")',
            exit_code=NO_WORKING_COPY,
        )

    def check_not_dirty(self, help_message=None):
        """Checks that all parts of the working copy have no changes. Otherwise, raises InvalidOperation"""
        for p in self.parts():
            p.check_not_dirty(help_message=help_message)

    def assert_matches_head_tree(self):
        """
        Checks that all parts of the working copy are based on the HEAD tree, according to their kart-state tables.
        Otherwise, raises WorkingCopyTreeMismatch.
        """
        self.assert_matches_tree(self.repo.head_tree)

    def assert_matches_tree(self, tree):
        """
        Checks that all parts of the working copy are based on the given tree, according to their kart-state tables.
        Otherwise, raises WorkingCopyTreeMismatch.
        """
        for p in self.parts():
            p.assert_matches_tree(tree)

    def parts(self):
        """Yields extant working-copy parts. Raises an error if a corrupt or uncontactable part is encountered."""
        for part in self._all_parts_inluding_nones():
            if part is not None:
                yield part

    def _all_parts_inluding_nones(self):
        # TODO - add more parts here. Next part to be added is the file-based working copy for point clouds.
        yield self.tabular

    @property
    def tabular(self):
        """Return the tabular working copy of the Kart repository, or None if it does not exist."""
        if not hasattr(self, "_tabular"):
            self._tabular = self.get_tabular()
        return self._tabular

    def get_tabular(
        self,
        allow_uncreated=False,
        allow_invalid_state=False,
        allow_unconnectable=False,
    ):
        """
        Gets the tabular working copy but with more fine-grained control.
        See TableWorkingCopy.get for more details.
        """

        from kart.tabular.working_copy.base import TableWorkingCopy

        return TableWorkingCopy.get(
            self.repo,
            allow_uncreated=allow_uncreated,
            allow_invalid_state=allow_invalid_state,
            allow_unconnectable=allow_unconnectable,
        )

    def create_parts_if_missing(self, parts_to_create, reset_to=DONT_RESET):
        """
        Creates the named parts if they are missing and can be created. Returns any created parts themselves.
        Supported parts:
        tabular
        """
        # TODO - support more parts here. Next part to be added is the file-based working copy for point clouds.
        created_parts = []

        for part_type in parts_to_create:
            assert part_type == "tabular"
            if self.create_and_initialise_tabular():
                created_parts.append(self.tabular)

        if reset_to != self.DONT_RESET:
            for p in created_parts:
                p.reset(reset_to)

        return created_parts

    def create_and_initialise_tabular(self):
        """
        Create the tabular part of the working copy if it currently doesn't exist
        but is configured so that we know where to put it. Otherwise, this has no effect.
        """
        from kart.tabular.working_copy import TableWorkingCopyStatus

        t = self.get_tabular(allow_uncreated=True)
        if t and not (t.status() & TableWorkingCopyStatus.INITIALISED):
            click.echo(f"Creating {t.WORKING_COPY_TYPE_NAME} working copy at {t} ...")
            t.create_and_initialise()
            self._tabular = t
            return True
        return False

    def delete_tabular(self):
        """
        Deletes the tabular working copy - from disk or from a server - and removes the cached reference to it.
        Leaves the repo config unchanged (ie, running checkout will recreate a working copy in the same place).
        """
        t = self.get_tabular(allow_invalid_state=True)
        if t:
            t.delete()
        del self._tabular

    def reset_to_head(
        self,
        *,
        create_parts_if_missing=(),
        quiet=False,
        repo_key_filter=RepoKeyFilter.MATCH_ALL,
        track_changes_as_dirty=False,
        rewrite_full=False,
    ):
        """Reset all working copy parts to the head commit. See reset() below."""
        self.reset(
            self.repo.head_commit,
            create_parts_if_missing=create_parts_if_missing,
            quiet=quiet,
            repo_key_filter=repo_key_filter,
            track_changes_as_dirty=track_changes_as_dirty,
            rewrite_full=rewrite_full,
        )

    def reset(
        self,
        commit_or_tree,
        *,
        create_parts_if_missing=(),
        quiet=False,
        repo_key_filter=RepoKeyFilter.MATCH_ALL,
        track_changes_as_dirty=False,
        rewrite_full=False,
    ):
        """
        Resets the working copy to the given target-tree (or the tree pointed to by the given target-commit).
        This is called when we want to move content from the Kart repo ODB into the working copy - ie, during
        create-workingcopy, checkout, switch, restore, reset.

        Any existing changes which match the repo_key_filter will be discarded. Existing changes which do not
        math the repo_key_filter will be kept.

        If track_changes_as_dirty=False (the default) the tree ID in the kart_state table gets set to the
        new tree ID and the tracking table is left empty. If it is True, the old tree ID is kept and the
        tracking table is used to record all the changes, so that they can be committed later.

        If rewrite_full is True, then every dataset currently being tracked will be dropped, and all datasets
        present at target_tree_or_commit will be written from scratch using write_full.
        Since write_full honours the current repo spatial filter, this also ensures that the working copy spatial
        filter is up to date.
        """
        created_parts = ()
        if create_parts_if_missing:
            # Even we're only partially resetting the WC, we still need to do a full reset on anything that
            # is newly created since it won't otherwise contain any data yet. The extra parameters (repo_key_filter
            # and track_changes_as_dirty) don't have any effect for a WC part that is newly created.
            created_parts = self.create_parts_if_missing(
                create_parts_if_missing, reset_to=commit_or_tree
            )

        for p in self.parts():
            if p in created_parts:
                # This part was already handled above.
                continue

            if not quiet:
                click.echo(f"Updating {p} ...")
            p.reset(
                commit_or_tree,
                repo_key_filter=repo_key_filter,
                track_changes_as_dirty=track_changes_as_dirty,
                rewrite_full=rewrite_full,
            )

    def set_state_post_commit(
        self,
        commit_or_tree,
        *,
        quiet=False,
        mark_as_clean=None,
        now_outside_spatial_filter=None,
    ):
        """
        Like a reset, this marks the working copy as now being based on the given target-tree (or the tree in the given
        target-commit). Unlike a reset, this doesn't update the dataset contents - this is called post-commit, so the
        overall flow of dataset contents is from working copy into the Kart repo ODB. However, we still need to tidy up
        a few things afterwards:
        - the working copy is now based on the newly created commit, not the previous commit which is now the parent.
        - all of the dataset contents that were committed should no longer be tracked as dirty - it can be marked as clean.
        - newly committed features which are outside the spatial filter should be removed from the working copy, since they
          are no longer dirty and now no different to anything else outside the spatial filter.

        mark_as_clean - a RepoKeyFilter of what was committed and can be marked as clean. Most commonly, this is simply
            RepoKeyFilter.MATCH_ALL
        now_outside_spatial_filter - a RepoKeyFilter of the newly committed features that can simply be dropped since
            they are outside the spatial filter.
        """
        if now_outside_spatial_filter and not quiet:
            # TODO we currently only check if vector features match the filter - no other dataset types are supported.
            total_count = now_outside_spatial_filter.recursive_len()
            click.echo(
                f"Removing {total_count} features from the working copy that no longer match the spatial filter..."
            )

        for p in self.parts():
            p.set_state_post_commit(
                commit_or_tree,
                mark_as_clean=mark_as_clean,
                now_outside_spatial_filter=now_outside_spatial_filter,
            )


class WorkingCopyPart:
    """Abstract base class for a particular part of a working copy - eg the tabular part, or the file-based part."""

    def check_not_dirty(self, help_message=None):
        """Raises an InvalidOperation if this working-copy part is dirty."""
        if not self.is_dirty():
            return

        if not help_message:
            help_message = "Commit these changes (`kart commit`) or discard these changes (`kart restore`) first."
        raise InvalidOperation(
            f"You have uncommitted changes in your working copy.\n{help_message}",
            exit_code=UNCOMMITTED_CHANGES,
        )

    def is_dirty(self):
        """Returns True if this part has uncommitted changes, False if it does not."""
        raise NotImplementedError()

    def assert_matches_head_tree(self):
        """Raises a WorkingCopyTreeMismatch if kart_state refers to a different tree and not the HEAD tree."""
        self.assert_matches_tree(self.repo.head_tree)

    def assert_matches_tree(self, expected_tree):
        """Raises a WorkingCopyTreeMismatch if kart_state refers to a different tree and not the given tree."""
        if expected_tree is None or isinstance(expected_tree, str):
            expected_tree_id = expected_tree
        else:
            expected_tree_id = expected_tree.peel(pygit2.Tree).hex
        actual_tree_id = self.get_tree_id()

        if actual_tree_id != expected_tree_id:
            raise WorkingCopyTreeMismatch(actual_tree_id, expected_tree_id)

    def get_tree_id(self):
        return self.get_kart_state_value("*", "tree")

    def get_spatial_filter_hash(self):
        return self.get_kart_state_value("*", "spatial-filter-hash")

    def get_kart_state_value(self, table_name, key):
        """Looks up a value from the kart-state table."""
        raise NotImplementedError()

    def reset(
        self,
        commit_or_tree,
        *,
        repo_key_filter=RepoKeyFilter.MATCH_ALL,
        track_changes_as_dirty=False,
        rewrite_full=False,
    ):
        raise NotImplementedError()

    def set_state_post_commit(
        self,
        commit_or_tree,
        *,
        mark_as_clean=None,
        now_outside_spatial_filter=None,
    ):
        raise NotImplementedError()
