"""Bulk inserts for many fact rows."""

from typing import Any

from sqlalchemy import Connection, Table, column, insert, select, text

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
    for start in range(0, len(rows), CHUNK):
        chunk = rows[start : start + CHUNK]
        conn.execute(stmt, {c: [r[c] for r in chunk] for c in columns})
