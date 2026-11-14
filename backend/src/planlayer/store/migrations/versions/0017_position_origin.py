"""Positions from HR and positions added in Planlayer (ADR 0020): every position has a number,
HR's personnel number or one Planlayer gives (N00001, ...); a position taken from HR keeps its
number, name, entry date and flags, and only controllers remove it.

Revision ID: 0017
Revises: 0016
"""

from collections.abc import Sequence

from alembic import op

from planlayer.store.migrate import migration_schema

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    s = migration_schema()
    # Alembic's column operations aren't schema-translated.
    op.execute(
        f"ALTER TABLE {s}.workforce_position ADD COLUMN origin text NOT NULL DEFAULT 'manual'"
        " CONSTRAINT ck_workforce_position_origin CHECK (origin IN ('hr', 'manual'))"
    )
    # Positions whose number is in HR's list came from it; the others get Planlayer's numbers,
    # in the order they were added, per round. The backfill sees every row (row-level security
    # is forced, and a migration has no user) and fires no trigger: it changes no plan and
    # moves no status.
    for table in ("workforce_position", "hr_staff"):
        op.execute(f"ALTER TABLE {s}.{table} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {s}.workforce_position DISABLE TRIGGER USER")
    op.execute(f"""
        UPDATE {s}.workforce_position p SET origin = 'hr'
        WHERE p.code IN (SELECT code FROM {s}.hr_staff) AND NOT p.is_plan
    """)
    op.execute(f"""
        UPDATE {s}.workforce_position p SET code = n.code
        FROM (
            SELECT position_id,
                   'N' || lpad(row_number() OVER (PARTITION BY version_id
                                                  ORDER BY position_id)::text, 5, '0') AS code
            FROM {s}.workforce_position WHERE origin = 'manual'
        ) n
        WHERE n.position_id = p.position_id
    """)
    op.execute(f"ALTER TABLE {s}.workforce_position ENABLE TRIGGER USER")
    for table in ("workforce_position", "hr_staff"):
        op.execute(f"ALTER TABLE {s}.{table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"ALTER TABLE {s}.workforce_position ALTER COLUMN code SET NOT NULL, "
        "ADD CONSTRAINT ck_workforce_position_number "
        "CHECK (origin = 'hr' OR code ~ '^N[0-9]{5,}$'), "
        "ADD CONSTRAINT ck_workforce_position_hr_staff CHECK (origin = 'manual' OR NOT is_plan)"
    )
    op.execute(f"""
        CREATE FUNCTION {s}.guard_workforce_position() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.origin <> 'hr' THEN
                RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
            END IF;
            IF TG_OP = 'DELETE' THEN
                IF NOT {s}.writes_as_controller() THEN
                    RAISE EXCEPTION 'plan values are locked: only controllers remove a position '
                        'taken from HR' USING ERRCODE = 'PL001';
                END IF;
                RETURN OLD;
            END IF;
            IF (NEW.origin, NEW.code, NEW.person_name, NEW.entry_date, NEW.is_plan,
                NEW.is_replacement) IS DISTINCT FROM
               (OLD.origin, OLD.code, OLD.person_name, OLD.entry_date, OLD.is_plan,
                OLD.is_replacement) THEN
                RAISE EXCEPTION 'plan values are locked: a position taken from HR keeps its '
                    'number, name and entry date' USING ERRCODE = 'PL001';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute(f"""
        CREATE TRIGGER guard BEFORE UPDATE OR DELETE ON {s}.workforce_position
        FOR EACH ROW EXECUTE FUNCTION {s}.guard_workforce_position()
    """)


def downgrade() -> None:
    s = migration_schema()
    op.execute(f"DROP TRIGGER guard ON {s}.workforce_position")
    op.execute(f"DROP FUNCTION {s}.guard_workforce_position()")
    op.execute(
        f"ALTER TABLE {s}.workforce_position DROP CONSTRAINT ck_workforce_position_hr_staff, "
        "DROP CONSTRAINT ck_workforce_position_number, ALTER COLUMN code DROP NOT NULL, "
        "DROP COLUMN origin"
    )
