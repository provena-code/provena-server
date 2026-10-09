from sqlalchemy import BINARY, Computed, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from provena.db.base import Base

# Kept from when this was a ProgSnap2 link table generated from the spec, so
# existing databases carry over (MySQL table names are case-sensitive on
# Linux).
ASSIGNMENT_MAP_TABLE = "LinkAssignmentMap"
ASSIGNMENT_MAP_UNIQUE_KEY = "uq_linkassignmentmap_subject_assignment_section"
CODESTATE_SECTION_HASH_EXPRESSION = "UNHEX(SHA2(`CodeStateSection`, 256))"


class AssignmentMapping(Base):
    """
    Which CodeStateSections (files) each subject submitted for each
    assignment, derived from Submit events. A file may map to multiple
    assignments. Kept up to date by provena.api.read.logic.mapping.
    """

    __tablename__ = ASSIGNMENT_MAP_TABLE
    __table_args__ = (
        # Unique on a hash of the path rather than the path itself: a key on
        # the full (SubjectID, AssignmentID, CodeStateSection) would be
        # (255 + 255 + 512) chars x 4 bytes (utf8mb4), over InnoDB's
        # 3072-byte index limit. mapping.py's upsert relies on this key.
        UniqueConstraint("SubjectID", "AssignmentID", "CodeStateSectionHash", name=ASSIGNMENT_MAP_UNIQUE_KEY),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    SubjectID: Mapped[str] = mapped_column(String(255))
    AssignmentID: Mapped[str] = mapped_column(String(255))
    CodeStateSection: Mapped[str] = mapped_column(String(512))
    """CodeStateSection that maps to the given AssignmentID."""

    CodeStateSectionHash: Mapped[bytes] = mapped_column(BINARY(32), Computed(CODESTATE_SECTION_HASH_EXPRESSION, persisted=True))
    """SHA-256 of CodeStateSection, computed by MySQL. Hashes the exact
    bytes, so unlike comparisons on CodeStateSection itself (which follow
    the column's case- and accent-insensitive collation), paths differing
    only in case are distinct keys."""

    LastValidTimestamp: Mapped[str] = mapped_column(String(255))
    """The last timestamp for which this mapping is valid: edits to a file
    after its submission shouldn't count toward that assignment. This is a
    ServerTimestamp, so it can't be used directly to associate logs with
    submissions."""

    CodeStateID: Mapped[str] = mapped_column(String(255))
    """CodeStateID of the last submission of this file, used to identify a
    likely code submission."""
