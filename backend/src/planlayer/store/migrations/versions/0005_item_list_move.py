"""Item lists move and change hands (ADR 0036): the owner moves a list to another line of a cost
center they plan; a controller takes over a list whose owner is disabled, for themself or for
someone who plans its cost center. The guard trigger refuses everything else; controllers read
and update the lists of disabled owners so they can take them over.

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

from alembic import op

from planlayer.store.migrate import migration_schema

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ME = "(NULLIF(current_setting('app.user_id', true), ''))::integer"


def _plans(s: str, user: str = ME, cc: str = "cost_center_id") -> str:
    return (
        f"{cc} = ANY (ARRAY(SELECT cost_center_id FROM {s}.cost_center_access "
        f"WHERE user_id = {user} AND can_write))"
    )


def _reads(s: str) -> str:
    return (
        f"cost_center_id = ANY (ARRAY(SELECT cost_center_id FROM {s}.cost_center_access "
        f"WHERE user_id = {ME}))"
    )


def _controller(s: str) -> str:
    return f"EXISTS (SELECT 1 FROM {s}.app_user WHERE user_id = {ME} AND is_controller)"


def _owner_gone(s: str) -> str:
    return f"EXISTS (SELECT 1 FROM {s}.app_user g WHERE g.user_id = owner_id AND g.disabled)"


def _policies(s: str, takeover: bool) -> tuple[str, str]:
    writes = f"owner_id = {ME} OR (visibility IN ('cost_center', 'public') AND {_plans(s)})"
    reads = (
        f"owner_id = {ME} OR (visibility = 'cost_center' AND {_plans(s)}) "
        f"OR (visibility = 'public' AND {_reads(s)})"
    )
    if not takeover:
        return (
            f"CREATE POLICY read ON {s}.item_list FOR SELECT USING ({reads})",
            f"CREATE POLICY change ON {s}.item_list FOR UPDATE USING ({writes}) "
            f"WITH CHECK ({writes})",
        )
    gone = f"({_controller(s)} AND {_owner_gone(s)})"
    return (
        f"CREATE POLICY read ON {s}.item_list FOR SELECT USING ({reads} OR {gone})",
        f"CREATE POLICY change ON {s}.item_list FOR UPDATE USING ({writes} OR {gone}) "
        f"WITH CHECK ({writes} OR {_controller(s)})",
    )


def _guard(s: str, takeover: bool) -> str:
    me = "nullif(current_setting('app.user_id', true), '')::int"
    if not takeover:
        body = f"""
            IF (NEW.visibility IS DISTINCT FROM OLD.visibility
                OR NEW.owner_id IS DISTINCT FROM OLD.owner_id)
               AND OLD.owner_id IS DISTINCT FROM {me} THEN
                RAISE EXCEPTION 'only the list''s owner changes its visibility'
                    USING ERRCODE = 'PL002';
            END IF;"""
    else:
        body = f"""
            IF NEW.cost_center_id IS DISTINCT FROM OLD.cost_center_id
               OR NEW.account_id IS DISTINCT FROM OLD.account_id THEN
                IF OLD.owner_id IS DISTINCT FROM {me} THEN
                    RAISE EXCEPTION 'only the list''s owner moves it' USING ERRCODE = 'PL002';
                END IF;
                IF NOT EXISTS (SELECT 1 FROM {s}.cost_center_access a
                               WHERE a.user_id = {me} AND a.cost_center_id = NEW.cost_center_id
                                 AND a.can_write) THEN
                    RAISE EXCEPTION 'a list moves only to a cost center you plan'
                        USING ERRCODE = 'PL002';
                END IF;
            END IF;
            IF NEW.owner_id IS DISTINCT FROM OLD.owner_id THEN
                IF NOT (EXISTS (SELECT 1 FROM {s}.app_user u
                                WHERE u.user_id = {me} AND u.is_controller)
                        AND EXISTS (SELECT 1 FROM {s}.app_user u
                                    WHERE u.user_id = OLD.owner_id AND u.disabled)
                        AND (NEW.owner_id = {me}
                             OR EXISTS (SELECT 1 FROM {s}.cost_center_access a
                                        WHERE a.user_id = NEW.owner_id
                                          AND a.cost_center_id = NEW.cost_center_id
                                          AND a.can_write))) THEN
                    RAISE EXCEPTION 'a controller takes over a list only from a disabled owner, '
                        'for themself or someone who plans its cost center'
                        USING ERRCODE = 'PL002';
                END IF;
            ELSIF NEW.visibility IS DISTINCT FROM OLD.visibility
                  AND OLD.owner_id IS DISTINCT FROM {me} THEN
                RAISE EXCEPTION 'only the list''s owner changes its visibility'
                    USING ERRCODE = 'PL002';
            END IF;"""
    return (
        f"CREATE OR REPLACE FUNCTION {s}.guard_item_list() RETURNS trigger LANGUAGE plpgsql "
        f"AS $$ BEGIN {body}\n            RETURN NEW;\n        END $$"
    )


def _apply(takeover: bool) -> None:
    s = migration_schema()
    op.execute(_guard(s, takeover))
    op.execute(f"DROP POLICY read ON {s}.item_list")
    op.execute(f"DROP POLICY change ON {s}.item_list")
    for policy in _policies(s, takeover):
        op.execute(policy)


def upgrade() -> None:
    _apply(takeover=True)


def downgrade() -> None:
    _apply(takeover=False)
