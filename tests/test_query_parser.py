# Copyright (C) 2025
# Licensed under the GPL-3.0 License.
# Created for TagStudio: https://github.com/CyanVoxel/TagStudio

from tagstudio.core.query_lang.ast import ANDList, Constraint, ConstraintType
from tagstudio.core.query_lang.parser import Parser


def test_non_tag_constraint_does_not_stick() -> None:
    parsed = Parser("date:day abc").parse()

    assert isinstance(parsed, ANDList)
    assert len(parsed.terms) == 2

    first = parsed.terms[0]
    second = parsed.terms[1]

    assert isinstance(first, Constraint)
    assert isinstance(second, Constraint)

    assert first.type == ConstraintType.Date
    assert first.value == "day"

    # Bare literals after a non-tag constraint should revert to default tag mode.
    assert second.type == ConstraintType.Tag
    assert second.value == "abc"


def test_tag_constraint_still_sticks() -> None:
    parsed = Parser("tag:orange square").parse()

    assert isinstance(parsed, ANDList)
    assert len(parsed.terms) == 2

    first = parsed.terms[0]
    second = parsed.terms[1]

    assert isinstance(first, Constraint)
    assert isinstance(second, Constraint)

    assert first.type == ConstraintType.Tag
    assert second.type == ConstraintType.Tag


def test_order_constraint_does_not_stick() -> None:
    parsed = Parser('order:title "portrait"').parse()

    assert isinstance(parsed, ANDList)
    assert len(parsed.terms) == 2

    first = parsed.terms[0]
    second = parsed.terms[1]

    assert isinstance(first, Constraint)
    assert isinstance(second, Constraint)

    assert first.type == ConstraintType.Order
    assert first.value == "title"

    # Bare literals after `order:` should revert to default tag mode.
    assert second.type == ConstraintType.Tag
    assert second.value == "portrait"
