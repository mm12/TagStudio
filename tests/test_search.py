# Copyright (C) 2025
# Licensed under the GPL-3.0 License.
# Created for TagStudio: https://github.com/CyanVoxel/TagStudio


import pytest
import structlog

from tagstudio.core.library.alchemy.enums import BrowsingState
from tagstudio.core.library.alchemy.library import Library
from tagstudio.core.query_lang.util import ParsingError

logger = structlog.get_logger()


def verify_count(lib: Library, query: str, count: int):
    results = lib.search_library(BrowsingState.from_search_query(query), page_size=500)
    logger.info("results", entry_ids=results.ids, count=results.total_count)
    assert results.total_count == count
    assert len(results.ids) == count


@pytest.mark.parametrize(
    ["query", "count"],
    [
        ("", 32),
        ("path:*", 32),
        ("path:*inherit*", 24),
        ("path:*comp*", 5),
        ("special:untagged", 3),
        ("filetype:png", 25),
        ("filetype:jpg", 6),
        ("filetype:'jpg'", 6),
        ("tag_id:1011", 5),
        ("tag_id:1038", 11),
        ("doesnt exist", 0),
        ("archived", 0),
        ("favorite", 0),
        ("tag:favorite", 0),
        ("circle", 11),
        ("tag:square", 11),
        ("green", 5),
        ("orange", 5),
        ("tag:orange", 5),
    ],
)
def test_single_constraint(search_library: Library, query: str, count: int):
    verify_count(search_library, query, count)


@pytest.mark.parametrize(
    ["query", "count"],
    [
        ("circle aND square", 5),
        ("circle square", 5),
        ("green AND square", 2),
        ("green square", 2),
        ("orange AnD square", 2),
        ("orange square", 2),
        ("orange and filetype:png", 5),
        ("square and filetype:jpg", 2),
        ("orange filetype:png", 5),
        ("green path:*inherit*", 4),
    ],
)
def test_and(search_library: Library, query: str, count: int):
    verify_count(search_library, query, count)


@pytest.mark.parametrize(
    ["query", "count"],
    [
        ("square or circle", 17),
        ("orange or green", 10),
        ("orange Or circle", 14),
        ("orange oR square", 14),
        ("square OR green", 14),
        ("circle or green", 14),
        ("green or circle", 14),
        ("filetype:jpg or tag:orange", 11),
        ("red or filetype:png", 28),
        ("filetype:jpg or path:*comp*", 11),
    ],
)
def test_or(search_library: Library, query: str, count: int):
    verify_count(search_library, query, count)


@pytest.mark.parametrize(
    ["query", "count"],
    [
        ("not unexistant", 32),
        ("not path:*", 0),
        ("not not path:*", 32),
        ("not special:untagged", 29),
        ("not filetype:png", 7),
        ("not filetype:jpg", 26),
        ("not tag_id:1011", 27),
        ("not tag_id:1038", 21),
        ("not green", 27),
        ("tag:favorite", 0),
        ("not circle", 21),
        ("not tag:square", 21),
        ("circle and not square", 6),
        ("not circle and square", 6),
        ("special:untagged or not filetype:jpg", 26),
        ("not square or green", 23),
    ],
)
def test_not(search_library: Library, query: str, count: int):
    verify_count(search_library, query, count)


@pytest.mark.parametrize(
    ["query", "count"],
    [
        ("(tag_id:1041)", 11),
        ("(((tag_id:1041)))", 11),
        ("not (not tag_id:1041)", 11),
        ("((circle) and (not square))", 6),
        ("(not ((square) OR (green)))", 18),
        ("filetype:png and (tag:square or green)", 12),
    ],
)
def test_parentheses(search_library: Library, query: str, count: int):
    verify_count(search_library, query, count)


@pytest.mark.parametrize(
    ["query", "count"],
    [
        ("ellipse", 17),
        ("yellow", 15),
        ("color", 25),
        ("shape", 24),
        ("yellow not green", 10),
    ],
)
def test_parent_tags(search_library: Library, query: str, count: int):
    verify_count(search_library, query, count)


@pytest.mark.parametrize(
    "invalid_query", ["asd AND", "asd AND AND", "tag:(", "(asd", "asd[]", "asd]", ":", "tag: :"]
)
def test_syntax(search_library: Library, invalid_query: str):
    with pytest.raises(ParsingError) as e_info:  # noqa: F841  # pyright: ignore[reportUnusedVariable]
        search_library.search_library(BrowsingState.from_search_query(invalid_query), page_size=500)


def test_date_constraint_with_following_term_does_not_error(search_library: Library):
    results = search_library.search_library(
        BrowsingState.from_search_query("date:day abc"), page_size=500
    )
    assert isinstance(results.total_count, int)


def test_date_constraint_accepts_generic_relative_duration(search_library: Library):
    results = search_library.search_library(BrowsingState.from_search_query("date:2d"), page_size=500)
    assert isinstance(results.total_count, int)


def test_field_search_with_selector_and_wildcard(library: Library):
    entries = list(library.all_entries(with_joins=True))
    assert len(entries) >= 2
    first_id = entries[0].id
    second_id = entries[1].id

    assert library.add_value_type("note", name="Note")
    assert library.add_field_to_entry(first_id, field_id="note", value="25 pages")
    assert library.add_field_to_entry(first_id, field_id="note", value="misc")
    assert library.add_field_to_entry(second_id, field_id="note", value="misc")

    # Plain contains search.
    verify_count(library, 'field:"note=25 pages"', 1)

    # Wildcard search over field value.
    verify_count(library, 'field:"note=25*"', 1)

    # Any non-empty value for this field.
    verify_count(library, 'field:"note=*"', 2)

    # Restrict to the second occurrence of the field inside each entry.
    verify_count(library, 'field:"note#2=misc"', 1)


def test_order_constraint_with_field_selector(library: Library):
    entries = list(library.all_entries(with_joins=True))
    assert len(entries) >= 2
    first_id = entries[0].id
    second_id = entries[1].id

    assert library.add_value_type("artist", name="Artist")
    assert library.add_field_to_entry(first_id, field_id="artist", value="twitter@alice")
    assert library.add_field_to_entry(first_id, field_id="artist", value="twitter@!111")
    assert library.add_field_to_entry(second_id, field_id="artist", value="twitter@bob")

    results = library.search_library(BrowsingState.from_search_query("order:artist#2"), page_size=500)
    assert results.total_count >= 2
    assert results.ids[0] == first_id


def test_order_constraint_with_field_value_match(library: Library):
    entries = list(library.all_entries(with_joins=True))
    assert len(entries) >= 2
    first_id = entries[0].id
    second_id = entries[1].id

    assert library.add_value_type("artist", name="Artist")
    assert library.add_field_to_entry(first_id, field_id="artist", value="twitter@alice")
    assert library.add_field_to_entry(first_id, field_id="artist", value="twitter@!111")
    assert library.add_field_to_entry(second_id, field_id="artist", value="twitter@bob")

    # Only values containing '!' participate in order field sorting.
    # Entries with matching values are sorted first.
    state = BrowsingState.from_search_query('order:"artist=*!*"').with_sorting_direction(True)
    results = library.search_library(state, page_size=500)

    assert results.total_count >= 2
    assert results.ids[0] == first_id
