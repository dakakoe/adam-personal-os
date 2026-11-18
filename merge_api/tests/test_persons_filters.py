"""The People list's filter binds — positional, and invisible without a database.

list_persons/count_persons build their args list by hand and the SQL refers to
those positions by number; the viewer scope then appends one more bind. Get a
position wrong and the query still parses, still ships, and filters by the
wrong value (or 500s in prod). These pin the numbering to the args order.
"""
from __future__ import annotations

import inspect

from merge_api import queries


def test_source_filter_binds_the_position_it_is_given():
    clause = queries._source_filter(6)
    assert clause.count("$6::text") == 2
    assert "$5" not in clause and "$7" not in clause


def test_list_and_count_bind_source_where_their_args_put_it():
    # list_persons: [q, company_id, circle, limit, offset, source] → $6
    assert "$6::text IS NULL" in queries.LIST_PERSONS_SQL
    assert "LIMIT $4 OFFSET $5" in queries.LIST_PERSONS_SQL
    # count_persons: [q, company_id, circle, source] → $4
    assert "$4::text IS NULL" in queries.COUNT_PERSONS_SQL
    src = inspect.getsource(queries.list_persons)
    assert "args: list = [q, company_id, circle, limit, offset, source]" in src
    src = inspect.getsource(queries.count_persons)
    assert "args: list = [q, company_id, circle, source]" in src


def test_viewer_scope_binds_after_the_filters():
    # The member id is appended, so it must land one past the last filter bind.
    args = ["q", None, None, 50, 0, "telegram"]
    clause = queries.visibility_clause("p", {"member_id": "m"}, args)
    assert "$7::uuid" in clause and len(args) == 7


def test_a_handle_only_telegram_identity_is_still_telegram():
    # Both the filter and the catalogue must fold it, or the dropdown offers
    # "telegram_handle" as its own option and filtering by Telegram misses them.
    assert "telegram_handle" in queries._SOURCE_NORM
    assert queries._SOURCE_NORM in queries.PERSON_SOURCES_SQL
    assert queries._SOURCE_NORM in queries.LIST_PERSONS_SQL
    assert queries._SOURCE_NORM in queries.COUNT_PERSONS_SQL


def test_sources_catalogue_counts_only_live_people():
    assert "p.merged_into IS NULL AND p.deleted_at IS NULL" in queries.PERSON_SOURCES_SQL
    assert "count(DISTINCT i.person_id)" in queries.PERSON_SOURCES_SQL
