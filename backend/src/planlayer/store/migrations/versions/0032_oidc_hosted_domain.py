"""The login's hosted domain: the Google Workspace domain every ID token must name (hd claim).

Revision ID: 0032
Revises: 0031
"""

from collections.abc import Sequence

from alembic import op

from planlayer.store.migrate import migration_schema

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    s = migration_schema()
    op.execute(
        f"ALTER TABLE {s}.login_setting ADD COLUMN oidc_hosted_domain text NOT NULL DEFAULT ''"
    )


def downgrade() -> None:
    s = migration_schema()
    op.execute(f"ALTER TABLE {s}.login_setting DROP COLUMN oidc_hosted_domain")
