"""Label / custom-field filter connection points (label-property.md §3.2 / §2.7).

Reusable SQL clause builders consumed by the list/kanban projection layer
(MES-33 remainder) when wiring ``?label=…`` / ``?cf_<key>=…`` filters and
view ``filters`` trees. Kept dependency-free from the view/issue query code:
each builder returns an ``EXISTS``-shaped ``ColumnElement`` correlating on
``issues.id`` so callers just ``stmt.where(clause)``.

Index contract (§2.7): enum filters hit the composite GIN
``idx_icfv_value_json`` (``field_def_id = … AND value_json @> …``); number /
date / member filters hit the ``(field_def_id, value_*)`` partial indexes —
``field_def_id`` always leads so scans narrow to one field's rows.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Select, and_, exists, select
from sqlalchemy.sql import ColumnElement

from mesh.db.models.issue import Issue
from mesh.db.models.label import IssueCustomFieldValue, IssueLabel


def issues_with_labels(label_ids: Sequence[uuid.UUID], *, match_all: bool = False) -> ColumnElement:
    """Clause: the issue carries ANY (or ALL, with ``match_all``) of the labels."""
    if not label_ids:
        raise ValueError("label_ids must not be empty")
    label_filter = IssueLabel.label_id.in_(list(label_ids))
    subq: Select = (
        select(IssueLabel.issue_id)
        .where(IssueLabel.issue_id == Issue.id, label_filter)
        .correlate(Issue)
    )
    if not match_all:
        return exists(subq)
    # ALL: count DISTINCT matched labels and require the full set size.
    from sqlalchemy import func

    count_subq = (
        select(func.count(func.distinct(IssueLabel.label_id)))
        .where(IssueLabel.issue_id == Issue.id, label_filter)
        .correlate(Issue)
        .scalar_subquery()
    )
    return count_subq >= len(set(label_ids))


def issues_with_enum_value(
    field_def_id: uuid.UUID, option_ids: Sequence[str]
) -> ColumnElement:
    """Clause: the issue's value for ``field_def_id`` contains ANY option id.

    ``value_json @> '"<option>"'::jsonb`` containment — single_select rows
    store a JSON string, multi_select rows a JSON array; scalar ``@>`` works
    for the single case, array-element containment for the multi case. GIN
    path: ``idx_icfv_value_json`` (§2.8 plan shape).
    """
    from sqlalchemy import or_
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.sql import type_coerce

    if not option_ids:
        raise ValueError("option_ids must not be empty")
    conditions = [
        IssueCustomFieldValue.value_json.op("@>")(type_coerce(option_id, JSONB))
        for option_id in option_ids
    ]
    subq = (
        select(IssueCustomFieldValue.issue_id)
        .where(
            IssueCustomFieldValue.issue_id == Issue.id,
            IssueCustomFieldValue.field_def_id == field_def_id,
            or_(*conditions),
        )
        .correlate(Issue)
    )
    return exists(subq)


def issues_with_number_range(
    field_def_id: uuid.UUID,
    *,
    ge: float | int | None = None,
    le: float | int | None = None,
) -> ColumnElement:
    """Clause: numeric value within [ge, le] — hits ``idx_icfv_number``."""
    conditions = [IssueCustomFieldValue.field_def_id == field_def_id]
    if ge is not None:
        conditions.append(IssueCustomFieldValue.value_number >= ge)
    if le is not None:
        conditions.append(IssueCustomFieldValue.value_number <= le)
    subq = (
        select(IssueCustomFieldValue.issue_id)
        .where(IssueCustomFieldValue.issue_id == Issue.id, and_(*conditions))
        .correlate(Issue)
    )
    return exists(subq)


def issues_with_date_range(
    field_def_id: uuid.UUID,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> ColumnElement:
    """Clause: date/datetime value within [start, end] — ``idx_icfv_date``."""
    conditions = [IssueCustomFieldValue.field_def_id == field_def_id]
    if start is not None:
        conditions.append(IssueCustomFieldValue.value_date >= start)
    if end is not None:
        conditions.append(IssueCustomFieldValue.value_date <= end)
    subq = (
        select(IssueCustomFieldValue.issue_id)
        .where(IssueCustomFieldValue.issue_id == Issue.id, and_(*conditions))
        .correlate(Issue)
    )
    return exists(subq)


def issues_with_member_value(
    field_def_id: uuid.UUID, member_ids: Sequence[uuid.UUID]
) -> ColumnElement:
    """Clause: member-typed value is one of ``member_ids`` — ``idx_icfv_member``."""
    if not member_ids:
        raise ValueError("member_ids must not be empty")
    subq = (
        select(IssueCustomFieldValue.issue_id)
        .where(
            IssueCustomFieldValue.issue_id == Issue.id,
            IssueCustomFieldValue.field_def_id == field_def_id,
            IssueCustomFieldValue.value_member_id.in_(list(member_ids)),
        )
        .correlate(Issue)
    )
    return exists(subq)


def issues_with_boolean_value(field_def_id: uuid.UUID, value: bool) -> ColumnElement:
    """Clause: boolean-typed value equals ``value``."""
    subq = (
        select(IssueCustomFieldValue.issue_id)
        .where(
            IssueCustomFieldValue.issue_id == Issue.id,
            IssueCustomFieldValue.field_def_id == field_def_id,
            IssueCustomFieldValue.value_boolean.is_(value),
        )
        .correlate(Issue)
    )
    return exists(subq)
