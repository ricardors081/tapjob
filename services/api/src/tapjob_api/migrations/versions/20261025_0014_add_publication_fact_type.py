"""Add publication to profile_fact_type check constraint.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UPGRADE_FACT_TYPES = (
    "'experience', 'education', 'skill', 'certification', 'language', "
    "'project', 'link', 'preference', 'work_authorization', 'publication'"
)

_DOWNGRADE_FACT_TYPES = (
    "'experience', 'education', 'skill', 'certification', 'language', "
    "'project', 'link', 'preference', 'work_authorization'"
)


def upgrade() -> None:
    op.drop_constraint("profile_fact_type", "profile_facts", type_="check")
    op.create_check_constraint(
        "profile_fact_type",
        "profile_facts",
        f"fact_type IN ({_UPGRADE_FACT_TYPES})",
    )


def downgrade() -> None:
    conn = op.get_bind()
    count: int = conn.execute(
        sa.text("SELECT count(*) FROM profile_facts WHERE fact_type = 'publication'")
    ).scalar_one()
    if count > 0:
        raise RuntimeError(
            f"Cannot downgrade migration 0014: found {count} profile_facts with "
            "fact_type='publication'"
        )

    op.drop_constraint("profile_fact_type", "profile_facts", type_="check")
    op.create_check_constraint(
        "profile_fact_type",
        "profile_facts",
        f"fact_type IN ({_DOWNGRADE_FACT_TYPES})",
    )
