"""Login (ADR 0030): local passwords and identities on users, the provider's settings, pending
OpenID Connect logins and the login log.

Revision ID: 0031
Revises: 0030
"""

from collections.abc import Sequence

from alembic import op

from planlayer.store.migrate import migration_schema

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    s = migration_schema()
    op.execute(f"""
        ALTER TABLE {s}.app_user
            ADD COLUMN password_hash text,
            ADD COLUMN identity text UNIQUE
    """)
    op.execute(f"""
        CREATE TABLE {s}.login_setting (
            id smallint PRIMARY KEY DEFAULT 1 CONSTRAINT single_row CHECK (id = 1),
            method text NOT NULL DEFAULT 'none'
                CONSTRAINT login_method CHECK (method IN ('none', 'oidc', 'ldap')),
            oidc_label text NOT NULL DEFAULT '',
            oidc_issuer text NOT NULL DEFAULT '',
            oidc_client_id text NOT NULL DEFAULT '',
            oidc_scopes text NOT NULL DEFAULT 'openid profile email',
            oidc_login_claim text NOT NULL DEFAULT 'preferred_username',
            oidc_email_claim text NOT NULL DEFAULT 'email',
            oidc_name_claim text NOT NULL DEFAULT 'name',
            oidc_groups_claim text NOT NULL DEFAULT 'groups',
            ldap_url text NOT NULL DEFAULT '',
            ldap_bind_dn text NOT NULL DEFAULT '',
            ldap_base_dn text NOT NULL DEFAULT '',
            ldap_login_attribute text NOT NULL DEFAULT 'uid',
            ldap_email_attribute text NOT NULL DEFAULT 'mail',
            ldap_name_attribute text NOT NULL DEFAULT 'cn',
            ca_pem text NOT NULL DEFAULT '',
            allowed_group text NOT NULL DEFAULT '',
            admin_group text NOT NULL DEFAULT '',
            controller_group text NOT NULL DEFAULT '',
            viewer_group text NOT NULL DEFAULT '',
            hr_group text NOT NULL DEFAULT '',
            setup_code_hash text,
            updated_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute(f"INSERT INTO {s}.login_setting DEFAULT VALUES")
    op.execute(f"""
        CREATE TABLE {s}.oidc_login (
            state text PRIMARY KEY,
            nonce text NOT NULL,
            verifier text NOT NULL,
            redirect_uri text NOT NULL,
            test_by integer REFERENCES {s}.app_user (user_id),
            settings jsonb,
            result jsonb,
            created_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute(f"CREATE INDEX ON {s}.oidc_login (test_by)")
    op.execute(f"""
        CREATE TABLE {s}.login_event (
            event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            at timestamptz NOT NULL DEFAULT now(),
            login text NOT NULL,
            method text NOT NULL CONSTRAINT login_event_method
                CHECK (method IN ('demo', 'password', 'oidc', 'setup')),
            outcome text NOT NULL
                CONSTRAINT login_event_outcome
                CHECK (outcome IN ('success', 'failed', 'refused', 'blocked', 'error')),
            address text,
            user_agent text,
            detail text,
            user_id integer REFERENCES {s}.app_user (user_id)
        )
    """)
    op.execute(f"CREATE INDEX ON {s}.login_event (login, at)")
    op.execute(f"CREATE INDEX ON {s}.login_event (address, at)")
    op.execute(f"CREATE INDEX ON {s}.login_event (user_id)")


def downgrade() -> None:
    s = migration_schema()
    op.execute(f"DROP TABLE {s}.login_event")
    op.execute(f"DROP TABLE {s}.oidc_login")
    op.execute(f"DROP TABLE {s}.login_setting")
    op.execute(f"ALTER TABLE {s}.app_user DROP COLUMN identity, DROP COLUMN password_hash")
