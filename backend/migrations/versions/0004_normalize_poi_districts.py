"""Normalize legacy seed district labels.

Revision ID: 0004_normalize_poi_districts
Revises: 0003_enrich_poi_model
"""

from alembic import op


revision = "0004_normalize_poi_districts"
down_revision = "0003_enrich_poi_model"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE pois SET district = CASE district
            WHEN 'Phú Nhuận' THEN 'Quận Phú Nhuận'
            WHEN 'Bình Thạnh' THEN 'Quận Bình Thạnh'
            WHEN 'Tân Bình' THEN 'Quận Tân Bình'
            WHEN 'Bình Tân' THEN 'Quận Bình Tân'
            WHEN 'Gò Vấp' THEN 'Quận Gò Vấp'
            WHEN 'Tân Phú' THEN 'Quận Tân Phú'
            ELSE district
        END
        WHERE district IN ('Phú Nhuận', 'Bình Thạnh', 'Tân Bình', 'Bình Tân', 'Gò Vấp', 'Tân Phú')
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE pois SET district = CASE district
            WHEN 'Quận Phú Nhuận' THEN 'Phú Nhuận'
            WHEN 'Quận Bình Thạnh' THEN 'Bình Thạnh'
            WHEN 'Quận Tân Bình' THEN 'Tân Bình'
            WHEN 'Quận Bình Tân' THEN 'Bình Tân'
            WHEN 'Quận Gò Vấp' THEN 'Gò Vấp'
            WHEN 'Quận Tân Phú' THEN 'Tân Phú'
            ELSE district
        END
        WHERE source = 'seed'
        """
    )
