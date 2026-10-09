"""
Schema of the hand-written app tables (provena.db) and the LinkAssignmentMap
migration: regression tests for B7 (docs/tasks/testing.md, section 6).
"""

import logging
import re

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from progsnap2.spec.enums import CoreTables

from provena.assignments.models import ASSIGNMENT_MAP_TABLE, AssignmentMapping
from provena.db.base import init_app_tables

from tests.support.databases import show_create_table

# The table as the ProgSnap2 spec generated it before D8, with the prefix
# unique key that was added to the course DB by hand.
LEGACY_ASSIGNMENT_MAP_DDL = f"""
CREATE TABLE `{ASSIGNMENT_MAP_TABLE}` (
  `SubjectID` varchar(255) NOT NULL,
  `AssignmentID` varchar(255) NOT NULL,
  `CodeStateSection` varchar(512) NOT NULL,
  `LastValidTimestamp` varchar(255) NOT NULL,
  `CodeStateID` varchar(255) NOT NULL,
  UNIQUE KEY `uq_linkassignmentmap_SubjectID_AssignmentID_CodeStateSection`
    (`SubjectID`, `AssignmentID`, `CodeStateSection`(250))
)
"""


def _ddl(engine: sa.Engine) -> str:
    # The AUTO_INCREMENT counter depends on how many rows were ever inserted,
    # not on the schema.
    return re.sub(r" AUTO_INCREMENT=\d+", "", show_create_table(engine, ASSIGNMENT_MAP_TABLE))


def _rows(engine: sa.Engine) -> list[sa.Row]:
    with engine.connect() as conn:
        return conn.execute(
            sa.select(AssignmentMapping.SubjectID, AssignmentMapping.AssignmentID,
                      AssignmentMapping.CodeStateSection, AssignmentMapping.CodeStateSectionHash)
        ).all()


def _insert_mapping(engine: sa.Engine, section: str, subject: str = "s1", assignment: str = "A1") -> None:
    with engine.begin() as conn:
        conn.execute(sa.insert(AssignmentMapping), {
            "SubjectID": subject,
            "AssignmentID": assignment,
            "CodeStateSection": section,
            "LastValidTimestamp": "2026-01-01T00:00:00",
            "CodeStateID": "cs1",
        })


def test_startup_creates_the_whole_schema(test_db_engine):
    """B7's symptom: a failed CREATE TABLE was swallowed at startup, leaving
    the database half-created."""
    with test_db_engine.connect() as conn:
        tables = {name.lower() for name in sa.inspect(conn).get_table_names()}
    expected = {
        CoreTables.MainTable.lower(), CoreTables.Metadata.lower(), CoreTables.CodeStates.lower(),
        "linkloggingerror", ASSIGNMENT_MAP_TABLE.lower(),
        "auth_users", "auth_oauth_identities", "auth_tokens",
    }
    assert expected <= tables


def test_fresh_assignment_map_is_keyed_on_the_path_hash(scratch_database):
    engine = scratch_database()
    init_app_tables(bind=engine)

    ddl = _ddl(engine)
    assert "`CodeStateSectionHash` binary(32) GENERATED ALWAYS AS" in ddl
    assert "UNIQUE KEY `uq_linkassignmentmap_subject_assignment_section` (`SubjectID`,`AssignmentID`,`CodeStateSectionHash`)" in ddl


def test_legacy_assignment_map_migrates_to_the_fresh_schema(scratch_database):
    fresh = scratch_database()
    init_app_tables(bind=fresh)

    legacy = scratch_database()
    with legacy.begin() as conn:
        conn.execute(sa.text(LEGACY_ASSIGNMENT_MAP_DDL))
        conn.execute(sa.text(
            f"INSERT INTO `{ASSIGNMENT_MAP_TABLE}` VALUES ('old_s', 'old_a', 'proj/old.py', '2020-01-01', 'cs_old')"
        ))

    init_app_tables(bind=legacy)

    assert _ddl(legacy) == _ddl(fresh)
    [row] = _rows(legacy)
    assert (row.SubjectID, row.AssignmentID, row.CodeStateSection) == ("old_s", "old_a", "proj/old.py")
    assert len(row.CodeStateSectionHash) == 32


def test_init_app_tables_is_idempotent(scratch_database, caplog):
    engine = scratch_database()
    with engine.begin() as conn:
        conn.execute(sa.text(LEGACY_ASSIGNMENT_MAP_DDL))
    init_app_tables(bind=engine)
    ddl = _ddl(engine)

    with caplog.at_level(logging.INFO, logger="provena.db.migrations"):
        init_app_tables(bind=engine)

    assert _ddl(engine) == ddl
    assert not caplog.records, "a second run shouldn't migrate anything"


def test_paths_sharing_a_long_prefix_are_distinct_keys(test_db_engine):
    """The legacy key indexed only the first 250 chars of the path, so these
    two would have collided."""
    shared = "d" * 300
    _insert_mapping(test_db_engine, f"{shared}/a.py")
    _insert_mapping(test_db_engine, f"{shared}/b.py")
    assert len(_rows(test_db_engine)) == 2


def test_duplicate_mapping_is_rejected(test_db_engine):
    _insert_mapping(test_db_engine, "proj/a.py")
    with pytest.raises(IntegrityError):
        _insert_mapping(test_db_engine, "proj/a.py")


def test_path_uniqueness_is_case_sensitive(test_db_engine):
    """Pins a side effect of D8: the hash covers the exact bytes, so unlike
    the rest of the system (ai_ci collation), paths differing only in case
    are separate mappings."""
    _insert_mapping(test_db_engine, "proj/A.py")
    _insert_mapping(test_db_engine, "proj/a.py")
    assert len(_rows(test_db_engine)) == 2
