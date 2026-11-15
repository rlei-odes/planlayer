"""Pay rights per round, controlled by HR (ADR 0023): who reads a cost center's pay in a round,
proposed from the source's owners; HR releases the round before anyone but HR reads pay; the
source owner HR last decided on, so a new one shows as a proposal; and a log of every change of
these rights and of the HR role, read by HR.

Revision ID: 0020
Revises: 0019
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from planlayer.store.migrate import migration_schema
from planlayer.store.schema import APP_SCHEMA

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S = APP_SCHEMA
COST_CENTER = 2
MODULE = "personnel_costs"
USER = "nullif(current_setting('app.user_id', true), '')::int"
ACTOR = "nullif(current_setting('app.actor', true), '')"


def _module() -> list[sa.Column | sa.Constraint]:  # type: ignore[type-arg]
    return [
        sa.Column("module", sa.Text, nullable=False, server_default=MODULE),
        sa.ForeignKeyConstraint(
            ["version_id", "module"],
            [f"{S}.round_module.version_id", f"{S}.round_module.module"],
        ),
        sa.CheckConstraint(f"module = '{MODULE}'", name="module"),
    ]


def _member(name: str, nullable: bool = False) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, sa.Integer, sa.ForeignKey(f"{S}.member.member_id"), nullable=nullable)


def _user(name: str, nullable: bool = False) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, sa.Integer, sa.ForeignKey(f"{S}.app_user.user_id"), nullable=nullable)


def _drop_pay_policies(s: str) -> None:
    for table in ("hr_pay", "position_pay"):
        for policy in ("read", "add", "change", "remove"):
            op.execute(f"DROP POLICY {policy} ON {s}.{table}")
    op.execute(f"DROP POLICY read ON {s}.position_pay_log")
    op.execute(f"DROP POLICY read ON {s}.fact_cost_center")


def _pay_policies(s: str, *, per_round: bool) -> None:
    """The policies of migration 0019 over ``pay_access``; per round once it has a version."""
    if per_round:
        pays = f"SELECT version_id, cost_center_id FROM {s}.pay_access WHERE user_id = {USER}"
        positions = (
            f"position_id IN (SELECT p.position_id FROM {s}.workforce_position p "
            f"WHERE (p.version_id, p.cost_center_id) IN ({pays}))"
        )
        log = f"(version_id, cost_center_id) IN ({pays})"
        facts = f"(version_id, dim_cost_center) IN ({pays})"
        # HR's staff list salaries belong to no round: HR and the system user only.
        hr = (
            f"EXISTS (SELECT 1 FROM {s}.app_user u WHERE u.user_id = {USER} "
            "AND (u.is_hr OR u.is_system) AND NOT u.disabled)"
        )
    else:
        cc = f"ARRAY(SELECT cost_center_id FROM {s}.pay_access WHERE user_id = {USER})"
        positions = (
            f"position_id IN (SELECT p.position_id FROM {s}.workforce_position p "
            f"WHERE p.cost_center_id = ANY ({cc}))"
        )
        log = f"cost_center_id = ANY ({cc})"
        facts = f"dim_cost_center = ANY ({cc})"
        hr = f"cost_center_id = ANY ({cc})"
    for table, scope in (("hr_pay", hr), ("position_pay", positions)):
        op.execute(f"CREATE POLICY read ON {s}.{table} FOR SELECT USING ({scope})")
        op.execute(f"CREATE POLICY add ON {s}.{table} FOR INSERT WITH CHECK ({scope})")
        op.execute(
            f"CREATE POLICY change ON {s}.{table} FOR UPDATE USING ({scope}) WITH CHECK ({scope})"
        )
        op.execute(f"CREATE POLICY remove ON {s}.{table} FOR DELETE USING ({scope})")
    op.execute(f"CREATE POLICY read ON {s}.position_pay_log FOR SELECT USING ({log})")
    read = (
        f"dim_cost_center = ANY (ARRAY(SELECT cost_center_id FROM {s}.cost_center_access "
        f"WHERE user_id = {USER}))"
    )
    op.execute(f"""
        CREATE POLICY read ON {s}.fact_cost_center FOR SELECT USING ({read} AND (
            (version_id, dim_cost_center, dim_account) NOT IN
                (SELECT version_id, cost_center_id, account_id FROM {s}.pay_small_group)
            OR {facts}))
    """)


def upgrade() -> None:
    s = migration_schema()
    op.create_table(
        "pay_reader",
        sa.Column("version_id", sa.SmallInteger, nullable=False),
        *_module(),
        _member("cost_center_id"),
        _user("user_id"),
        sa.Column("added_by", sa.Text),
        sa.Column(
            "added_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("version_id", "cost_center_id", "user_id"),
        schema=S,
    )
    # The source owner HR last decided on per cost center (NULL: none); another one now is a
    # proposal for HR. A cost center without a row has never been decided on.
    op.create_table(
        "pay_owner_seen",
        sa.Column("version_id", sa.SmallInteger, nullable=False),
        *_module(),
        _member("cost_center_id"),
        _user("owner_id", nullable=True),
        sa.PrimaryKeyConstraint("version_id", "cost_center_id"),
        schema=S,
    )
    op.create_table(
        "pay_release",
        sa.Column("version_id", sa.SmallInteger, primary_key=True),
        *_module(),
        sa.Column("released_by", sa.Text, nullable=False),
        sa.Column(
            "released_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        schema=S,
    )
    op.create_table(
        "pay_access_log",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("actor", sa.Text),
        # reader_added / reader_removed / released / release_withdrawn / hr_granted / hr_revoked
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("version_id", sa.SmallInteger),
        sa.Column("cost_center_id", sa.Integer),
        sa.Column("user_id", sa.Integer),
        schema=S,
    )

    # Rounds with personnel costs keep their owners as proposed readers, not released yet;
    # seeded before the guard and the log exist.
    op.execute(f"""
        INSERT INTO {s}.pay_owner_seen (version_id, cost_center_id, owner_id)
        SELECT rm.version_id, o.cost_center_id, o.matched_user_id
        FROM {s}.round_module rm
        JOIN {s}.round_member m
            ON m.version_id = rm.version_id AND m.module = 'cost_center' AND m.enabled
        JOIN {s}.cost_center_owner o ON o.cost_center_id = m.member_id
        WHERE rm.module = '{MODULE}'
    """)
    op.execute(f"""
        INSERT INTO {s}.pay_reader (version_id, cost_center_id, user_id, added_by)
        SELECT version_id, cost_center_id, owner_id, 'system'
        FROM {s}.pay_owner_seen WHERE owner_id IS NOT NULL
    """)

    # Only HR changes pay rights; the system user seeds them when personnel costs come on and
    # carries them into a new round.
    op.execute(f"""
        CREATE FUNCTION {s}.guard_pay_rights() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NOT ({s}.writes_as_hr() OR EXISTS (
                    SELECT 1 FROM {s}.app_user u WHERE u.user_id = {USER} AND u.is_system)) THEN
                RAISE EXCEPTION 'plan values are locked: only HR changes who reads pay'
                    USING ERRCODE = 'PL001';
            END IF;
            RETURN NULL;
        END $$
    """)
    for table in ("pay_reader", "pay_owner_seen", "pay_release"):
        op.execute(f"""
            CREATE TRIGGER guard AFTER INSERT OR UPDATE OR DELETE ON {s}.{table}
            FOR EACH ROW EXECUTE FUNCTION {s}.guard_pay_rights()
        """)
    # The log, written by triggers so no change escapes it.
    op.execute(f"""
        CREATE FUNCTION {s}.log_pay_reader() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            k {s}.pay_reader;
        BEGIN
            IF TG_OP = 'DELETE' THEN k := OLD; ELSE k := NEW; END IF;
            INSERT INTO {s}.pay_access_log (actor, action, version_id, cost_center_id, user_id)
            VALUES ({ACTOR}, CASE WHEN TG_OP = 'DELETE' THEN 'reader_removed'
                                  ELSE 'reader_added' END,
                    k.version_id, k.cost_center_id, k.user_id);
            RETURN NULL;
        END $$
    """)
    op.execute(f"""
        CREATE TRIGGER log AFTER INSERT OR DELETE ON {s}.pay_reader
        FOR EACH ROW EXECUTE FUNCTION {s}.log_pay_reader()
    """)
    op.execute(f"""
        CREATE FUNCTION {s}.log_pay_release() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            INSERT INTO {s}.pay_access_log (actor, action, version_id)
            VALUES ({ACTOR}, CASE WHEN TG_OP = 'DELETE' THEN 'release_withdrawn'
                                  ELSE 'released' END,
                    CASE WHEN TG_OP = 'DELETE' THEN OLD.version_id ELSE NEW.version_id END);
            RETURN NULL;
        END $$
    """)
    op.execute(f"""
        CREATE TRIGGER log AFTER INSERT OR DELETE ON {s}.pay_release
        FOR EACH ROW EXECUTE FUNCTION {s}.log_pay_release()
    """)
    op.execute(f"""
        CREATE FUNCTION {s}.log_hr_role() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.is_hr IS DISTINCT FROM coalesce(OLD.is_hr, false) THEN
                INSERT INTO {s}.pay_access_log (actor, action, user_id)
                VALUES ({ACTOR}, CASE WHEN NEW.is_hr THEN 'hr_granted' ELSE 'hr_revoked' END,
                        NEW.user_id);
            END IF;
            RETURN NULL;
        END $$
    """)
    op.execute(f"""
        CREATE TRIGGER log_hr AFTER INSERT OR UPDATE OF is_hr ON {s}.app_user
        FOR EACH ROW EXECUTE FUNCTION {s}.log_hr_role()
    """)

    # Who reads pay, now per round: HR and the system user everywhere, the listed readers of a
    # released round.
    _drop_pay_policies(s)
    op.execute(f"DROP VIEW {s}.pay_access")
    op.execute(f"""
        CREATE VIEW {s}.pay_access AS
        SELECT u.user_id, r.version_id, m.member_id AS cost_center_id
        FROM {s}.app_user u CROSS JOIN {s}.round_module r CROSS JOIN {s}.member m
        WHERE (u.is_hr OR u.is_system) AND NOT u.disabled AND r.module = '{MODULE}'
          AND m.dim_id = {COST_CENTER} AND NOT m.is_group
        UNION
        SELECT p.user_id, p.version_id, p.cost_center_id
        FROM {s}.pay_reader p
        JOIN {s}.pay_release x ON x.version_id = p.version_id
        JOIN {s}.app_user u ON u.user_id = p.user_id
        WHERE NOT u.disabled
    """)
    _pay_policies(s, per_round=True)


def downgrade() -> None:
    s = migration_schema()
    _drop_pay_policies(s)
    op.execute(f"DROP VIEW {s}.pay_access")
    op.execute(f"""
        CREATE VIEW {s}.pay_access AS
        SELECT u.user_id, m.member_id AS cost_center_id
        FROM {s}.app_user u CROSS JOIN {s}.member m
        WHERE (u.is_hr OR u.is_system) AND NOT u.disabled
          AND m.dim_id = {COST_CENTER} AND NOT m.is_group
        UNION
        SELECT o.matched_user_id, o.cost_center_id
        FROM {s}.cost_center_owner o JOIN {s}.app_user u ON u.user_id = o.matched_user_id
        WHERE NOT u.disabled
    """)
    _pay_policies(s, per_round=False)
    op.execute(f"DROP TRIGGER log_hr ON {s}.app_user")
    for table in ("pay_access_log", "pay_release", "pay_owner_seen", "pay_reader"):
        op.drop_table(table, schema=S)
    for fn in ("guard_pay_rights", "log_pay_reader", "log_pay_release", "log_hr_role"):
        op.execute(f"DROP FUNCTION {s}.{fn}()")
