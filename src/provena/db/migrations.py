"""
In-place upgrades for app tables that already exist in older shapes. Models
always describe the *current* schema, which is what create_all builds for a
new database; the functions here only bring existing databases up to match.

Each migration inspects the live schema and applies only what's missing, so
all of them run on every startup (see provena.db.base.init_app_tables) and
re-running is a no-op. A migration can be deleted once every deployment has
run it. If these accumulate, switch to Alembic (versioned migrations tracked
in a DB table) instead of growing this file.
"""

import logging
logger = logging.getLogger(__name__)

from sqlalchemy import Connection, inspect, text

from provena.assignments.models import (
    ASSIGNMENT_MAP_TABLE,
    ASSIGNMENT_MAP_UNIQUE_KEY,
    CODESTATE_SECTION_HASH_EXPRESSION,
)


def run_migrations(conn: Connection) -> None:
    _migrate_assignment_map_to_hash_key(conn)


# The spec-generated LinkAssignmentMap's unique key, on a 250-char prefix of
# CodeStateSection.
_LEGACY_ASSIGNMENT_MAP_UNIQUE_KEY = "uq_linkassignmentmap_SubjectID_AssignmentID_CodeStateSection"


def _migrate_assignment_map_to_hash_key(conn: Connection) -> None:
    """
    2026-10: LinkAssignmentMap moved from the ProgSnap2 spec to a hand-written
    model (provena.assignments.models). The spec-generated table had no id or
    CodeStateSectionHash column, and a unique key on a prefix of
    CodeStateSection (a key on the full path exceeds InnoDB's limit). Adds
    both columns and swaps that key for one on the hash, in one ALTER.
    """
    inspector = inspect(conn)
    columns = {column["name"] for column in inspector.get_columns(ASSIGNMENT_MAP_TABLE)}
    index_names = {index["name"] for index in inspector.get_indexes(ASSIGNMENT_MAP_TABLE)}

    changes = []
    if "CodeStateSectionHash" not in columns:
        changes.append(
            f"ADD COLUMN `CodeStateSectionHash` BINARY(32) GENERATED ALWAYS AS ({CODESTATE_SECTION_HASH_EXPRESSION}) "
            "STORED NOT NULL AFTER `CodeStateSection`"
        )
    if "id" not in columns:
        changes.append("ADD COLUMN `id` INTEGER NOT NULL AUTO_INCREMENT PRIMARY KEY FIRST")
    if _LEGACY_ASSIGNMENT_MAP_UNIQUE_KEY in index_names:
        changes.append(f"DROP INDEX `{_LEGACY_ASSIGNMENT_MAP_UNIQUE_KEY}`")
    if ASSIGNMENT_MAP_UNIQUE_KEY not in index_names:
        changes.append(f"ADD UNIQUE KEY `{ASSIGNMENT_MAP_UNIQUE_KEY}` (`SubjectID`, `AssignmentID`, `CodeStateSectionHash`)")

    if changes:
        logger.info(f"Migrating {ASSIGNMENT_MAP_TABLE}: {'; '.join(changes)}")
        conn.execute(text(f"ALTER TABLE `{ASSIGNMENT_MAP_TABLE}` {', '.join(changes)}"))
