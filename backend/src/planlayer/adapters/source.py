"""Source adapters (ADR 0004): read a relation or query as Arrow batches, cast to a contract.

The contract schema comes from the dataset definition, never from the source's own types: a
safe cast turns values that don't fit (extra decimals, wrong types) into errors.
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Protocol

import pyarrow as pa
from sqlalchemy import Engine, FromClause, column, create_engine, select, table, text


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

    def read(self, source: Source, contract: pa.Schema) -> Iterator[pa.RecordBatch]:
        """The contract's columns from ``source``, cast to the contract's types."""
        ...


class PostgresSource:
    """Reads through SQLAlchemy Core with a server-side cursor, in batches."""

    def __init__(self, url: str, batch_rows: int = 50_000) -> None:
        self.engine: Engine = create_engine(url, pool_pre_ping=True)
        self.batch_rows = batch_rows

    def test_connection(self) -> None:
        with self.engine.connect() as conn:
            conn.execute(text("SELECT 1"))

    def read(self, source: Source, contract: pa.Schema) -> Iterator[pa.RecordBatch]:
        names: Sequence[str] = contract.names
        origin: FromClause = (
            table(source.name, schema=source.schema)
            if isinstance(source, Relation)
            else text(source.sql).columns().subquery("src")
        )
        # Columns built at runtime: SQLAlchemy's generics can't type the row shape.
        stmt = select(*[column(n) for n in names]).select_from(origin)  # type: ignore[var-annotated]
        with self.engine.connect() as conn:
            result = conn.execution_options(stream_results=True, yield_per=self.batch_rows).execute(
                stmt
            )
            for chunk in result.partitions():
                columns = list(zip(*chunk, strict=True))
                arrays = [
                    pa.array(values, from_pandas=False).cast(field.type, safe=True)
                    if values
                    else pa.array([], field.type)
                    for values, field in zip(columns, contract, strict=True)
                ]
                yield pa.RecordBatch.from_arrays(arrays, schema=contract)

    def dispose(self) -> None:
        self.engine.dispose()
