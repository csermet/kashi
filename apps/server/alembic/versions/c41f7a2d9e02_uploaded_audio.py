"""uploaded_audio staging table (Faz 5 P4 BYO-audio)

Revision ID: c41f7a2d9e02
Revises: ba5b9cfda7c7
Create Date: 2026-07-13
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'c41f7a2d9e02'
down_revision: str | None = 'ba5b9cfda7c7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'uploaded_audio',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('content', sa.LargeBinary(), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('mime', sa.String(), nullable=True),
        sa.Column('duration_s', sa.Float(), nullable=False),
        sa.Column('uploaded_by', sa.UUID(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['uploaded_by'], ['api_keys.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_uploaded_audio_expires', 'uploaded_audio', ['expires_at'])


def downgrade() -> None:
    op.drop_index('ix_uploaded_audio_expires', table_name='uploaded_audio')
    op.drop_table('uploaded_audio')
