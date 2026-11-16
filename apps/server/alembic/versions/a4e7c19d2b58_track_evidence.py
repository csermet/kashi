"""track_evidence: what a full job read off the audio, for a refresh without download

Revision ID: a4e7c19d2b58
Revises: f3a8c2d61b07
Create Date: 2026-11-15
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = 'a4e7c19d2b58'
down_revision: str | None = 'f3a8c2d61b07'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Additive only: a new table, nothing existing changes. Keyed like jobs and
    # processed_tracks (source_type, source_id). Written only with evidence_mode on.
    op.create_table(
        'track_evidence',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('source_type', sa.String(), nullable=False),
        sa.Column('source_id', sa.String(), nullable=False),
        sa.Column('format', sa.Integer(), nullable=False),
        sa.Column('key', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('meta', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('emissions', sa.LargeBinary(), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint(
            "source_type IN ('youtube','plex','upload')", name='ck_track_evidence_source_type'
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('source_type', 'source_id', name='uq_track_evidence_source'),
    )
    # The arrays arrive compressed (zlib); TOAST would only try again. EXTERNAL keeps them
    # out of line, uncompressed by Postgres.
    op.execute("ALTER TABLE track_evidence ALTER COLUMN emissions SET STORAGE EXTERNAL")


def downgrade() -> None:
    op.drop_table('track_evidence')
