"""song_overrides: per-song corrections that survive reprocess (review step C.1)

Revision ID: f3a8c2d61b07
Revises: b36dfc2aae66
Create Date: 2026-11-14
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'f3a8c2d61b07'
down_revision: str | None = 'b36dfc2aae66'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Additive only: a new table, nothing existing changes. Keyed like jobs
    # and processed_tracks (source_type, source_id).
    op.create_table(
        'song_overrides',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('source_type', sa.String(), nullable=False),
        sa.Column('source_id', sa.String(), nullable=False),
        sa.Column('lrclib_id', sa.BigInteger(), nullable=True),
        sa.Column('lyrics_text', sa.String(), nullable=True),
        sa.Column('speed_factor', sa.Float(), nullable=True),
        sa.Column('offset_ms', sa.Integer(), nullable=True),
        sa.Column('plain_only', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('note', sa.String(), nullable=True),
        sa.Column('applied_etag', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint(
            "source_type IN ('youtube','plex','upload')", name='ck_song_overrides_source_type'
        ),
        sa.CheckConstraint(
            'lyrics_text IS NULL OR lrclib_id IS NULL', name='ck_song_overrides_one_lyrics_source'
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('source_type', 'source_id', name='uq_song_overrides_source'),
    )


def downgrade() -> None:
    op.drop_table('song_overrides')
