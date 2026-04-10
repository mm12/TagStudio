# SPDX-FileCopyrightText: (c) TagStudio Contributors
# SPDX-License-Identifier: GPL-3.0-only


import re
from typing import TYPE_CHECKING, override

import structlog
from sqlalchemy import ColumnElement, and_, distinct, false, func, or_, select
from sqlalchemy import true
from sqlalchemy.orm import Session
from sqlalchemy.sql.operators import ilike_op
from datetime import datetime as _dt, timedelta as _timedelta

from tagstudio.core.library.alchemy.constants import TAG_CHILDREN_ID_QUERY
from tagstudio.core.library.alchemy.joins import TagEntry
from tagstudio.core.library.alchemy.models import Entry, Tag, TagAlias, ValueType
from tagstudio.core.library.alchemy.fields import TextField, DatetimeField
from tagstudio.core.media_types import FILETYPE_EQUIVALENTS, MediaCategories
from tagstudio.core.query_lang.ast import (
    AST,
    ANDList,
    BaseVisitor,
    Constraint,
    ConstraintType,
    Not,
    ORList,
    Property,
)

# Only import for type checking/autocompletion, will not be imported at runtime.
if TYPE_CHECKING:
    from tagstudio.core.library.alchemy.library import Library
else:
    Library = None  # don't import library because of circular imports

logger = structlog.get_logger(__name__)

_DATE_DURATION_PATTERN = re.compile(r"^(?P<amount>\d+)\s*(?P<unit>[a-zA-Z]+)$")
_FIELD_SELECTOR_PATTERN = re.compile(r"^(?P<field>.+?)(?:#(?P<index>[1-9]\d*))?$")
_DATE_DURATION_UNITS_TO_DAYS: dict[str, int] = {
    "d": 1,
    "day": 1,
    "days": 1,
    "w": 7,
    "week": 7,
    "weeks": 7,
    "m": 30,
    "month": 30,
    "months": 30,
    "y": 365,
    "year": 365,
    "years": 365,
}
_DATE_DURATION_UNITS_TO_HOURS: dict[str, int] = {
    "h": 1,
    "hour": 1,
    "hours": 1,
}


def get_filetype_equivalency_list(item: str) -> list[str] | set[str]:
    for s in FILETYPE_EQUIVALENTS:
        if item in s:
            return s
    return [item]


class SQLBoolExpressionBuilder(BaseVisitor[ColumnElement[bool]]):
    def __init__(self, lib: Library) -> None:
        super().__init__()
        self.lib = lib

    @override
    def visit_or_list(self, node: ORList) -> ColumnElement[bool]:
        tag_ids, bool_expressions = self.__separate_tags(node.elements, only_single=False)
        if len(tag_ids) > 0:
            bool_expressions.append(self.__entry_has_any_tags(tag_ids))
        return or_(*bool_expressions)

    @override
    def visit_and_list(self, node: ANDList) -> ColumnElement[bool]:
        tag_ids, bool_expressions = self.__separate_tags(node.terms, only_single=True)
        if len(tag_ids) > 0:
            bool_expressions.append(self.__entry_has_all_tags(tag_ids))
        return and_(*bool_expressions)

    @override
    def visit_constraint(self, node: Constraint) -> ColumnElement[bool]:
        """Returns a Boolean Expression that is true, if the Entry satisfies the constraint."""
        if len(node.properties) != 0 and node.type != ConstraintType.Field:
            raise NotImplementedError("Properties are not implemented yet")  # TODO TSQLANG

        if node.type == ConstraintType.Tag:
            return self.__entry_has_any_tags(self.__get_tag_ids(node.value))
        elif node.type == ConstraintType.TagID:
            return self.__entry_has_any_tags([int(node.value)])
        elif node.type == ConstraintType.Path:
            ilike = False
            glob = False
            regex = False

            # Smartcase check
            if node.value == node.value.lower():
                ilike = True
            if node.value.startswith("*") or node.value.endswith("*"):
                glob = True
            if node.value.startswith("/") or node.value.endswith("/"):
                regex = True

            if ilike and glob:
                logger.info("ConstraintType.Path", ilike=True, glob=True)
                return func.lower(Entry.path).op("GLOB")(f"{node.value.lower()}")
            elif ilike:
                logger.info("ConstraintType.Path", ilike=True, glob=False)
                return ilike_op(Entry.path, f"%{node.value}%")
            elif glob:
                logger.info("ConstraintType.Path", ilike=False, glob=True)
                return Entry.path.op("GLOB")(node.value)
            elif regex:
                # search using raw regex. Unlike the final `else`, we do not turn `\s` into `/s`
                # remove starting and ending slashes
                # BUG: sometimes `glob=False ilike=True` gets used when it shouldn't!
                node.value = node.value.strip("/")
                # Validate regex to avoid SQLite UDF exceptions from invalid patterns
                try:
                    re.compile(node.value)
                    logger.info("ConstraintType.Path", ilike=False, glob=False, re=node.value)
                    return Entry.path.regexp_match(node.value)
                except re.error as exc:
                    logger.error(
                        "Invalid regex in path constraint; falling back to substring match",
                        pattern=node.value,
                        error=str(exc),
                    )
                    # Fallback: substring match (case-insensitive) to avoid hard failure
                    return ilike_op(Entry.path, f"%{node.value}%")
            else:
                logger.info(
                    "ConstraintType.Path", ilike=False, glob=False, re=re.escape(node.value)
                )
                return Entry.path.regexp_match(re.escape(node.value))
        elif node.type == ConstraintType.MediaType:
            extensions: set[str] = set[str]()
            for media_cat in MediaCategories.ALL_CATEGORIES:
                if node.value == media_cat.name:
                    extensions = extensions | media_cat.extensions
                    break
            return Entry.suffix.in_(map(lambda x: x.replace(".", ""), extensions))
        elif node.type == ConstraintType.FileType:
            return or_(
                *[Entry.suffix.ilike(ft) for ft in get_filetype_equivalency_list(node.value)]
            )
        elif node.type == ConstraintType.Special:  # noqa: SIM102 unnecessary once there is a second special constraint
            low_v = node.value.lower()
            if low_v == "untagged":
                return ~Entry.id.in_(select(Entry.id).join(TagEntry))
            elif low_v == "empty_fields":
                # Entries with no attached fields (no text or datetime fields)
                return and_(
                    ~Entry.id.in_(select(TextField.entry_id)),
                    ~Entry.id.in_(select(DatetimeField.entry_id)),
                )

        elif node.type == ConstraintType.Date:
            v = node.value.strip()
            low_v = v.lower()

            now = _dt.now()

            # Generic relative durations, e.g. 2d, 2 days, 3w, 1month.
            duration_match = _DATE_DURATION_PATTERN.fullmatch(low_v)
            if duration_match is not None:
                amount = int(duration_match.group("amount"))
                unit = duration_match.group("unit")
                if unit in _DATE_DURATION_UNITS_TO_DAYS:
                    cutoff = now - _timedelta(days=amount * _DATE_DURATION_UNITS_TO_DAYS[unit])
                    return Entry.date_added >= cutoff
                if unit in _DATE_DURATION_UNITS_TO_HOURS:
                    cutoff = now - _timedelta(
                        hours=amount * _DATE_DURATION_UNITS_TO_HOURS[unit]
                    )
                    return Entry.date_added >= cutoff

            if low_v in ("week", "7d", "7days"):
                cutoff = now - _timedelta(days=7)
                return Entry.date_added >= cutoff
            if low_v in ("day", "1d", "today"):
                if low_v == "today":
                    start = _dt(now.year, now.month, now.day)
                    return Entry.date_added >= start
                cutoff = now - _timedelta(days=1)
                return Entry.date_added >= cutoff
            if low_v in ("month", "30d"):
                cutoff = now - _timedelta(days=30)
                return Entry.date_added >= cutoff
            if low_v in ("year", "365d"):
                cutoff = now - _timedelta(days=365)
                return Entry.date_added >= cutoff

            # Adding support for relative keywords and comparison operators
            if v.startswith(">=") or v.startswith("<="):
                op = v[:2]
                date_str = v[2:]
            elif v.startswith(">") or v.startswith("<"):
                op = v[0]
                date_str = v[1:]
            else:
                op = None
                date_str = v

            date_str = date_str.strip()
            try:
                parsed = _dt.fromisoformat(date_str)
            except Exception as exc:
                logger.error("Invalid date format in date constraint", value=node.value)
                raise NotImplementedError("Invalid date format for date constraint") from exc

            if op is None:
                start = _dt(parsed.year, parsed.month, parsed.day)
                end = start + _timedelta(days=1)
                return and_(Entry.date_added >= start, Entry.date_added < end)
            elif op == ">":
                return Entry.date_added > parsed
            elif op == "<":
                return Entry.date_added < parsed
            elif op == ">=":
                return Entry.date_added >= parsed
            elif op == "<=":
                return Entry.date_added <= parsed

        elif node.type == ConstraintType.Order:
            # `order:` is a meta constraint consumed by search ordering logic.
            return true()

        elif node.type == ConstraintType.Field:
            field_name_raw, search_term = self.__split_field_search_spec(node.value)
            field_name, field_occurrence_index = self.__parse_field_selector(field_name_raw)

            explicit_occurrence_prop: int | None = None
            for prop in node.properties:
                if prop.key.lower() in ("index", "occurrence"):
                    try:
                        explicit_occurrence_prop = int(prop.value)
                    except ValueError as exc:
                        raise NotImplementedError("Field index must be a positive integer") from exc
            if explicit_occurrence_prop is not None:
                field_occurrence_index = explicit_occurrence_prop

            if field_occurrence_index is not None and field_occurrence_index < 1:
                raise NotImplementedError("Field index must be >= 1")

            normalized_field_name = self.__normalize_field_lookup(field_name)
            matching_keys = select(ValueType.key).where(
                or_(
                    func.lower(ValueType.key) == normalized_field_name,
                    func.lower(ValueType.name) == field_name.strip().lower(),
                    func.lower(func.replace(ValueType.name, " ", "_")) == normalized_field_name,
                )
            )

            ranked_text = (
                select(
                    TextField.entry_id.label("entry_id"),
                    TextField.value.label("value"),
                    func.row_number()
                    .over(
                        partition_by=(TextField.entry_id, TextField.type_key),
                        order_by=(TextField.position.asc(), TextField.id.asc()),
                    )
                    .label("row_num"),
                )
                .where(TextField.type_key.in_(matching_keys))
                .subquery()
            )

            ranked_datetime = (
                select(
                    DatetimeField.entry_id.label("entry_id"),
                    DatetimeField.value.label("value"),
                    func.row_number()
                    .over(
                        partition_by=(DatetimeField.entry_id, DatetimeField.type_key),
                        order_by=(DatetimeField.position.asc(), DatetimeField.id.asc()),
                    )
                    .label("row_num"),
                )
                .where(DatetimeField.type_key.in_(matching_keys))
                .subquery()
            )

            text_stmt = select(ranked_text.c.entry_id).where(
                self.__string_match_expression(ranked_text.c.value, search_term)
            )
            datetime_stmt = select(ranked_datetime.c.entry_id).where(
                self.__string_match_expression(ranked_datetime.c.value, search_term)
            )

            if field_occurrence_index is not None:
                text_stmt = text_stmt.where(ranked_text.c.row_num == field_occurrence_index)
                datetime_stmt = datetime_stmt.where(
                    ranked_datetime.c.row_num == field_occurrence_index
                )

            return Entry.id.in_(text_stmt.union(datetime_stmt))

        # raise exception if Constraint stays unhandled
        raise NotImplementedError("This type of constraint is not implemented yet")

    @override
    def visit_property(self, node: Property) -> ColumnElement[bool]:
        raise NotImplementedError("This should never be reached!")

    @override
    def visit_not(self, node: Not) -> ColumnElement[bool]:
        return ~self.visit(node.child)

    def __get_tag_ids(self, tag_name: str, include_children: bool = True) -> list[int]:
        """Given a tag name find the ids of all tags that this name could refer to."""
        with Session(self.lib.engine) as session:
            tag_ids = list(
                session.scalars(
                    select(Tag.id)
                    .where(or_(Tag.name.ilike(tag_name), Tag.shorthand.ilike(tag_name)))
                    .union(select(TagAlias.tag_id).where(TagAlias.name.ilike(tag_name)))
                )
            )
            if len(tag_ids) > 1:
                logger.debug(
                    f'Tag Constraint "{tag_name}" is ambiguous, {len(tag_ids)} matching tags found',
                    tag_ids=tag_ids,
                    include_children=include_children,
                )
            if not include_children:
                return tag_ids
            outp: list[int] = []
            for tag_id in tag_ids:
                outp.extend(list(session.scalars(TAG_CHILDREN_ID_QUERY, {"tag_id": tag_id})))
            return outp

    def __separate_tags(
        self, terms: list[AST], only_single: bool = True
    ) -> tuple[list[int], list[ColumnElement[bool]]]:
        tag_ids: set[int] = set()
        bool_expressions: list[ColumnElement[bool]] = []

        for term in terms:
            if isinstance(term, Constraint) and len(term.properties) == 0:
                match term.type:
                    case ConstraintType.TagID:
                        try:
                            tag_ids.add(int(term.value))
                        except ValueError:
                            logger.error(
                                "[SQLBoolExpressionBuilder] Could not cast value to an int Tag ID",
                                value=term.value,
                            )
                        continue
                    case ConstraintType.Tag:
                        ids = self.__get_tag_ids(term.value)
                        if len(ids) == 0:
                            bool_expressions.append(false())
                            continue
                        if not only_single:
                            tag_ids.update(ids)
                            continue
                        elif len(ids) == 1:
                            tag_ids.add(ids[0])
                            continue
                    case ConstraintType.FileType:
                        pass
                    case ConstraintType.MediaType:
                        pass
                    case ConstraintType.Path:
                        pass
                    case ConstraintType.Special:
                        pass
                    case ConstraintType.Date:
                        # Date constraints are handled as regular boolean expressions
                        # and should not trigger NotImplementedError here.
                        pass
                    case ConstraintType.Order:
                        # `order:` affects sorting, not filtering.
                        pass
                    case ConstraintType.Field:
                        # Field search is handled as a regular boolean expression
                        pass
                    case _:
                        raise NotImplementedError(f"Unhandled constraint: '{term.type}'")

            bool_expressions.append(self.visit(term))
        return list(tag_ids), bool_expressions

    def __entry_has_all_tags(self, tag_ids: list[int]) -> ColumnElement[bool]:
        """Returns Binary Expression that is true if the Entry has all provided tag ids."""
        # Relational Division Query
        return Entry.id.in_(
            select(TagEntry.entry_id)
            .where(TagEntry.tag_id.in_(tag_ids))
            .group_by(TagEntry.entry_id)
            .having(func.count(distinct(TagEntry.tag_id)) == len(tag_ids))
        )

    def __entry_has_any_tags(self, tag_ids: list[int]) -> ColumnElement[bool]:
        """Returns Binary Expression that is true if the Entry has any of the provided tag ids."""
        return Entry.id.in_(
            select(TagEntry.entry_id).where(TagEntry.tag_id.in_(tag_ids)).distinct()
        )

    def __split_field_search_spec(self, value: str) -> tuple[str, str]:
        if "=" in value:
            field_name, search_term = value.split("=", 1)
            return field_name.strip(), search_term.strip()

        if ":" in value:
            field_name, search_term = value.split(":", 1)
            return field_name.strip(), search_term.strip()

        # `field:"name"` means "any non-empty value for this field".
        return value.strip(), "*"

    def __parse_field_selector(self, value: str) -> tuple[str, int | None]:
        match = _FIELD_SELECTOR_PATTERN.fullmatch(value.strip())
        if match is None:
            return value.strip(), None

        field_name = (match.group("field") or "").strip()
        index_text = match.group("index")
        return field_name, int(index_text) if index_text is not None else None

    def __normalize_field_lookup(self, value: str) -> str:
        return value.strip().lower().replace(" ", "_")

    def __string_match_expression(self, value_column: ColumnElement, search_term: str) -> ColumnElement[bool]:  # type: ignore
        normalized = search_term.strip()

        if normalized == "*":
            return and_(value_column.is_not(None), func.trim(value_column) != "")

        if "*" in normalized or "?" in normalized:
            if normalized == normalized.lower():
                return func.lower(value_column).op("GLOB")(normalized.lower())
            return value_column.op("GLOB")(normalized)

        return ilike_op(value_column, f"%{normalized}%")
