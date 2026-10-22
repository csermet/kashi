"""jobs.deferrals + jobs.deferred_since (LyricFix P3 lrclib deferral)

Revision ID: b36dfc2aae66
Revises: e2c5f81a44d9
Create Date: 2026-10-22
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'b36dfc2aae66'
down_revision: str | None = 'e2c5f81a44d9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A deferral refunds the attempt, so it is counted separately; the first
    # one starts the patience window.
    op.add_column(
        'jobs',
        sa.Column('deferrals', sa.Integer(), server_default=sa.text('0'), nullable=False),
    )
    op.add_column(
        'jobs', sa.Column('deferred_since', sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('jobs', 'deferred_since')
    op.drop_column('jobs', 'deferrals')
