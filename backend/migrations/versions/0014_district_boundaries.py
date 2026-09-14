"""Compatibility marker for the district-boundary data revision.

Revision ID: 0014_district_boundaries
Revises: 0013_ranking_snapshots

Some development databases were stamped with this revision by the local
district-boundary import step, but the marker file was not kept in the source
tree.  The imported boundaries are optional data (no application table or
runtime query depends on them), so this migration intentionally contains no
DDL.  Keeping the marker makes both existing databases and clean installs
share one valid Alembic history.
"""

from __future__ import annotations


revision = "0014_district_boundaries"
down_revision = "0013_ranking_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
