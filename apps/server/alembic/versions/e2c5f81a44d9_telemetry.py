"""telemetry (Faz 6.7 P1 field diagnostics)

Revision ID: e2c5f81a44d9
Revises: d7e94b1c3a55
Create Date: 2026-07-26
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = 'e2c5f81a44d9'
down_revision: str | None = 'd7e94b1c3a55'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'telemetry',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('session_id', sa.UUID(), nullable=False),
        sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
        sa.Column('kind', sa.String(), nullable=False),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('received_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('reported_by', sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(['reported_by'], ['api_keys.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_telemetry_session_ts', 'telemetry', ['session_id', 'ts'])
    op.create_index('ix_telemetry_kind_ts', 'telemetry', ['kind', 'ts'])
    # The retention sweep's only index — it deletes by OUR clock, not the
    # client's (a skewed device must not pin rows or lose them early).
    op.create_index('ix_telemetry_received', 'telemetry', ['received_at'])


def downgrade() -> None:
    op.drop_index('ix_telemetry_received', table_name='telemetry')
    op.drop_index('ix_telemetry_kind_ts', table_name='telemetry')
    op.drop_index('ix_telemetry_session_ts', table_name='telemetry')
    op.drop_table('telemetry')
