"""add_audit_indexes

Revision ID: a1b2c3d4e5f6
Revises: 21f8f60c55cf
Create Date: 2026-05-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '21f8f60c55cf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        'idx_player_answers_room_song_round',
        'player_answers',
        ['room_id', 'song_id', 'round_index'],
        if_not_exists=True,
    )
    op.create_index(
        'idx_scores_room_user',
        'scores',
        ['room_id', 'user_id'],
        if_not_exists=True,
    )
    op.create_index(
        'idx_song_tag_history_song',
        'song_tag_history',
        ['song_id'],
        if_not_exists=True,
    )
    op.create_index(
        'idx_song_description_history_song',
        'song_description_history',
        ['song_id'],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index('idx_song_description_history_song', table_name='song_description_history', if_exists=True)
    op.drop_index('idx_song_tag_history_song', table_name='song_tag_history', if_exists=True)
    op.drop_index('idx_scores_room_user', table_name='scores', if_exists=True)
    op.drop_index('idx_player_answers_room_song_round', table_name='player_answers', if_exists=True)
