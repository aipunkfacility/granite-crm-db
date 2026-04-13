"""drop pipeline_runs table

Удаление мёртвой таблицы pipeline_runs, которая осталась в существующих БД
после удаления PipelineRunRow из ORM в Фазе 0 рефакторинга.

Revision ID: a3f1b2c4d5e6
Revises: ecda7d78a38f
Create Date: 2026-04-10 07:37:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3f1b2c4d5e6'
down_revision: Union[str, None] = 'ecda7d78a38f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # IF EXISTS — безопасно для новых БД, где таблицы нет
    op.execute("DROP TABLE IF EXISTS pipeline_runs")


def downgrade() -> None:
    # Данные pipeline_runs не восстанавливаются — таблица была deprecated/пустой.
    # Не создаём таблицу при откате, чтобы downgrade base оставлял БД чистой.
    pass
