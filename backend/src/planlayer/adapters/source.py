"""Source adapters (ADR 0004): read a relation or query as Arrow batches, cast to a contract.

The contract schema comes from the dataset definition, never from the source's own types: a
safe cast turns values that don't fit (extra decimals, wrong types) into errors.
"""

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

import pyarrow as pa
from sqlalchemy import (
    Connection,
    Engine,
    FromClause,
    column,
    create_engine,
    func,
    select,
    table,
    text,
)


@dataclass(frozen=True)
class Relation:
    name: str
    schema: str | None = None


@dataclass(frozen=True)
class Query:
    sql: str


Source = Relation | Query


class SourceAdapter(Protocol):
    def test_connection(self) -> None: ...

    def read(
        self, source: Source, contract: pa.Schema, columns: Mapping[str, str] | None = None
    ) -> Iterator[pa.RecordBatch]:
        """The contract's fields from ``source``, cast to the contract's types. ``columns``
        names the source column of each field; unnamed fields read a column of their name."""
        ...

    def preview(self, source: Source, limit: int) -> tuple[list[str], list[list[str | None]]]:
        """The source's column names and its first rows as text, without a contract."""
        ...


class PostgresSource:
    """Reads through SQLAlchemy Core with a server-side cursor, in batches."""

    def __init__(self, url: str, batch_rows: int = 50_000, timeout_s: int = 600) -> None:
        self.engine: Engine = create_engine(url, pool_pre_ping=True)
        self.batch_rows = batch_rows
        self.timeout_s = timeout_s

    def _read_only(self, conn: Connection, timeout_s: int) -> None:
        """Admin-entered queries run read-only and time out (ADR 0012)."""
        conn.execute(text("SET TRANSACTION READ ONLY"))
        conn.execute(select(func.set_config("statement_timeout", f"{timeout_s}s", True)))

    @staticmethod
    def _origin(source: Source) -> FromClause:
        if isinstance(source, Relation):
            return table(source.name, schema=source.schema)
        return text(source.sql).columns().subquery("src")

    def test_connection(self) -> None:
        with self.engine.connect() as conn:
            conn.execute(text("SELECT 1"))

    def read(
        self, source: Source, contract: pa.Schema, columns: Mapping[str, str] | None = None
    ) -> Iterator[pa.RecordBatch]:
        names: Sequence[str] = contract.names
        mapped = columns or {}
        # Columns built at runtime: SQLAlchemy's generics can't type the row shape.
        stmt = select(  # type: ignore[var-annotated]
            *[column(mapped.get(n, n)).label(n) for n in names]
        ).select_from(self._origin(source))
        with self.engine.begin() as conn:
            self._read_only(conn, self.timeout_s)
            result = conn.execution_options(stream_results=True, yield_per=self.batch_rows).execute(
                stmt
            )
            for chunk in result.partitions():
                by_column = list(zip(*chunk, strict=True))
                arrays = [
                    pa.array(values, from_pandas=False).cast(field.type, safe=True)
                    if values
                    else pa.array([], field.type)
                    for values, field in zip(by_column, contract, strict=True)
                ]
                yield pa.RecordBatch.from_arrays(arrays, schema=contract)

    def dispose(self) -> None:
        self.engine.dispose()

    def preview(self, source: Source, limit: int) -> tuple[list[str], list[list[str | None]]]:
        stmt = select(text("*")).select_from(self._origin(source)).limit(limit)
        with self.engine.begin() as conn:
            self._read_only(conn, 30)
            result = conn.execute(stmt)
            names = list(result.keys())
            rows = [[None if v is None else str(v) for v in row] for row in result]
        return names, rows
