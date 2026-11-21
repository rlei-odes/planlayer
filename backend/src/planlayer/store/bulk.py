"""Bulk inserts for many fact rows."""

from typing import Any

from sqlalchemy import Connection, Table, column, insert, select, text
from sqlalchemy.types import TypeDecorator

CHUNK = 50_000


def insert_all(conn: Connection, table: Table, rows: list[dict[str, Any]]) -> None:
    """Insert rows with one statement per chunk: the columns go as parallel arrays and are
    unnested. Row-level security checks and statement triggers then run once per chunk, not once
    per row, which makes a full sync of actuals several times faster than a row-wise insert."""
    if not rows:
        return
    columns = list(rows[0])
    dialect = conn.dialect
    types = {c: table.c[c].type.compile(dialect=dialect) for c in columns}
    arrays = ", ".join(f"CAST(:{c} AS {types[c]}[])" for c in columns)
    source = text(f"unnest({arrays}) AS t({', '.join(columns)})")
    stmt = insert(table).from_select(
        columns, select(*(column(c) for c in columns)).select_from(source)
    )
    sealed = {c: t for c in columns if isinstance(t := table.c[c].type, TypeDecorator)}

    def value(c: str, v: Any) -> Any:
        return sealed[c].process_bind_param(v, dialect) if c in sealed else v

    for start in range(0, len(rows), CHUNK):
        chunk = rows[start : start + CHUNK]
        # The arrays pass as text parameters, past the columns' types: a sealed column (ADR 0029)
        # is sealed here.
        conn.execute(stmt, {c: [value(c, r[c]) for r in chunk] for c in columns})
