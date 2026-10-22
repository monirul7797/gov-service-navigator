"""States that exist only on a database that has never published a release.

This file sorts first on purpose: the tests share one database, and once any test has published a
release (or rolled one back) ``publications`` is no longer empty. CI starts every run from an empty
database, so these checks run there; a local database that was used before skips them (reset it
with ``docker compose down -v``).
"""
import pytest
from psycopg.errors import RaiseException
from test_releases_schema import (
    _actor,
    active_release_id,
    audit_rows,
    make_validated_release,
    publications_after,
    publish,
    rollback,
)


def test_rollback_has_nothing_to_undo_before_and_after_the_first_publication(connect):
    worker, web = connect("worker"), connect("web")
    if worker.execute("SELECT count(*) FROM publications").fetchone()[0]:
        pytest.skip("this database already has publications; the check needs a fresh database")
    assert active_release_id(worker) is None
    actor = _actor()

    # Nothing has been published at all.
    with pytest.raises(RaiseException, match="nothing to roll back"):
        rollback(worker, actor)

    # The very first publication has no earlier release to go back to either.
    built = make_validated_release(connect)
    publish(worker, built.release_id, actor)
    assert publications_after(worker, 0) == [(built.release_id, None, "publish", actor)]
    assert audit_rows(web, actor) == [("release.publish", f"release:{built.release_id}", {"previous": None})]
    with pytest.raises(RaiseException, match="nothing to roll back"):
        rollback(worker, actor)

    # The refused rollbacks changed nothing.
    assert active_release_id(worker) == built.release_id
    assert publications_after(worker, 0) == [(built.release_id, None, "publish", actor)]
    assert len(audit_rows(web, actor)) == 1
