"""add_cookie_rotation_and_refresh_tables

Revision ID: 16259d859285
Revises: 67b82b9f3c4a
Create Date: 2026-03-21 23:18:59.960163

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '16259d859285'
down_revision: Union[str, Sequence[str], None] = '67b82b9f3c4a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create cookie_configs table
    op.create_table(
        'cookie_configs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('cookie_hash', sa.String(), nullable=False),
        sa.Column('cookie_content', sa.Text(), nullable=False),
        sa.Column('source', sa.String(), nullable=False, server_default='manual'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('cookie_hash', name='uq_cookie_configs_hash'),
    )

    # Create cookie_rotation_logs table
    op.create_table(
        'cookie_rotation_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('timestamp', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('from_cookie_id', sa.String(), nullable=True),
        sa.Column('to_cookie_id', sa.String(), nullable=False),
        sa.Column('reason', sa.String(), nullable=False),
        sa.Column('context_json', sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )

    # Create cookie_refresh_logs table
    op.create_table(
        'cookie_refresh_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('timestamp', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('cookie_id', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('old_expired_at', sa.Integer(), nullable=True),
        sa.Column('new_expired_at', sa.Integer(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('cookie_refresh_logs')
    op.drop_table('cookie_rotation_logs')
    op.drop_table('cookie_configs')
