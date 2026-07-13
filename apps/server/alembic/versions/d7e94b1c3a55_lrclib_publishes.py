"""lrclib_publishes ledger (Faz 5 P6 contribute-back)

Revision ID: d7e94b1c3a55
Revises: c41f7a2d9e02
Create Date: 2026-07-13
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'd7e94b1c3a55'
down_revision: str | None = 'c41f7a2d9e02'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'lrclib_publishes',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('source_type', sa.String(), nullable=False),
        sa.Column('source_id', sa.String(), nullable=False),
        sa.Column('etag', sa.String(), nullable=False),
        sa.Column('status', sa.String(), server_default=sa.text("'queued'"), nullable=False),
        sa.Column('error', sa.String(), nullable=True),
        sa.Column('requested_by', sa.UUID(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued','published','dry_run','failed')",
            name='ck_lrclib_publish_status',
        ),
        sa.ForeignKeyConstraint(['requested_by'], ['api_keys.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('source_type', 'source_id', 'etag', name='uq_lrclib_publish_doc'),
    )


def downgrade() -> None:
    op.drop_table('lrclib_publishes')
