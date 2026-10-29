"""Frozen semester maxLength boundaries at repository and PostgreSQL layers."""
from __future__ import annotations

import json
from uuid import uuid4

import pytest
import sqlalchemy as sa

from classroom_web.domain.policies import (SEMESTER_ACADEMIC_YEAR_MAX_LENGTH,
                                           SEMESTER_NAME_MAX_LENGTH)
from classroom_web.infrastructure.schema import tables

from conftest import ROOT


LIMITS = (("name", 100, "ck_semesters_name_length"),
          ("academic_year", 30, "ck_semesters_academic_year_length"))
TEXT_UNITS = ("学", "😀", "e\u0301")


def boundary_text(unit, length):
    """Count Unicode code points, including distinct combining code points."""
    return (unit * length)[:length]


def test_semester_limits_match_frozen_create_and_patch_contract():
    contract = json.loads((ROOT / "docs/web-alpha/architecture/contracts/openapi.json")
                          .read_text(encoding="utf-8"))
    schemas = contract["components"]["schemas"]
    for name in ("SemesterCreate", "SemesterPatch"):
        properties = schemas[name]["properties"]
        assert properties["name"]["maxLength"] == SEMESTER_NAME_MAX_LENGTH == 100
        year = next(item for item in properties["academic_year"]["anyOf"]
                    if item["type"] == "string")
        assert year["maxLength"] == SEMESTER_ACADEMIC_YEAR_MAX_LENGTH == 30


@pytest.mark.parametrize("operation", ("create", "update"))
@pytest.mark.parametrize("field,limit,constraint", LIMITS)
@pytest.mark.parametrize("unit", TEXT_UNITS, ids=("chinese", "astral", "combining"))
def test_repository_semester_boundary_and_overflow(pg, owners, operation, field, limit,
                                                   constraint, unit):
    accepted = boundary_text(unit, limit)
    rejected = accepted + "界"
    assert len(accepted) == limit and len(rejected) == limit + 1
    with pg.db.transaction(owners.a) as tx:
        if operation == "create":
            with pytest.raises(ValueError, match="invalid text value"):
                tx.catalog.create_semester(**({"name": "Synthetic semester"} | {field: rejected}))
            result = tx.catalog.create_semester(**({"name": "Synthetic semester"} | {field: accepted}))
        else:
            original = tx.catalog.create_semester("Synthetic semester")
            with pytest.raises(ValueError, match="invalid text value"):
                tx.catalog.update_semester(original.id, original.revision, **{field: rejected})
            unchanged = tx.catalog.get_semester(original.id)
            assert unchanged == original
            result = tx.catalog.update_semester(original.id, original.revision, **{field: accepted})
    with pg.db.transaction(owners.a) as tx:
        persisted = tx.catalog.get_semester(result.id)
        assert getattr(persisted, field) == accepted
        assert persisted.revision == (1 if operation == "create" else 2)
        count = tx.connection.scalar(sa.select(sa.func.count()).select_from(tables["semesters"]))
        assert count == 1


@pytest.mark.parametrize("operation", ("insert", "update"))
@pytest.mark.parametrize("field,limit,constraint", LIMITS)
@pytest.mark.parametrize("unit", TEXT_UNITS, ids=("chinese", "astral", "combining"))
def test_raw_postgres_semester_boundary_and_overflow(pg, owners, operation, field, limit,
                                                    constraint, unit):
    table = tables["semesters"]
    resource_id = uuid4()
    accepted = boundary_text(unit, limit)
    rejected = accepted + "界"
    if operation == "update":
        with pg.db.transaction(owners.a) as tx:
            resource_id = tx.catalog.create_semester("Synthetic semester").id

    def statement(value):
        if operation == "insert":
            return table.insert().values(**({"id": resource_id, "owner_id": owners.a.owner_id,
                                            "name": "Synthetic semester"} | {field: value}))
        return table.update().where(table.c.id == resource_id,
                                    table.c.owner_id == owners.a.owner_id).values(
                                        **{field: value}, revision=2,
                                        updated_at=sa.func.transaction_timestamp())

    with pytest.raises(sa.exc.IntegrityError) as caught:
        with pg.db.transaction(owners.a) as tx:
            tx.connection.execute(statement(rejected))
    assert caught.value.orig.sqlstate == "23514"
    assert caught.value.orig.diag.constraint_name == constraint
    with pg.db.transaction(owners.a) as tx:
        if operation == "update":
            assert tx.catalog.get_semester(resource_id).revision == 1
        tx.connection.execute(statement(accepted))
        actual_length = tx.connection.scalar(sa.select(sa.func.char_length(table.c[field]))
                                             .where(table.c.id == resource_id))
        assert actual_length == len(accepted) == limit
    with pg.db.transaction(owners.a) as tx:
        persisted = tx.catalog.get_semester(resource_id)
        assert getattr(persisted, field) == accepted
        assert persisted.revision == (1 if operation == "insert" else 2)


def test_semester_academic_year_remains_nullable(pg, owners):
    with pg.db.transaction(owners.a) as tx:
        semester = tx.catalog.create_semester("Synthetic semester", academic_year=None)
        assert semester.academic_year is None
        semester = tx.catalog.update_semester(semester.id, semester.revision, academic_year="年" * 30)
        semester = tx.catalog.update_semester(semester.id, semester.revision, academic_year=None)
    with pg.db.transaction(owners.a) as tx:
        assert tx.catalog.get_semester(semester.id).academic_year is None
