"""S-05: the adapter interface (D-10) with a Postgres and a DuckDB-over-Parquet implementation.

A source is a relation (table or view) or a query the admin entered (T-16). Reads stream Arrow
record batches with an explicit schema, so decimals stay decimals (T-12). Writes replace one
partition (e.g. a version) in one transaction, so publishing is idempotent (T-14).
"""

import os
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import Engine, column, create_engine, delete, literal_column, select, table, text
from sqlalchemy.engine import Connection


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
    def schema(self, source: Source) -> pa.Schema: ...
    def read(
        self, source: Source, columns: Sequence[str] | None = None, cast: pa.Schema | None = None
    ) -> Iterator[pa.RecordBatch]:
        """Stream the source; with ``cast``, every batch is cast (safely) to the contract schema."""
        ...


class TargetAdapter(Protocol):
    def replace(self, target: Relation, data: pa.Table, partition: Mapping[str, object]) -> int: ...


# --- Postgres (SQLAlchemy Core + psycopg) -----------------------------------------------------------

_PG_TYPES = {
    "text": pa.string(), "character varying": pa.string(), "date": pa.date32(),
    "integer": pa.int32(), "bigint": pa.int64(), "smallint": pa.int16(), "boolean": pa.bool_(),
    "double precision": pa.float64(), "timestamp with time zone": pa.timestamp("us", tz="UTC"),
}  # fmt: skip


class PostgresAdapter:
    def __init__(self, url: str, batch_rows: int = 50_000) -> None:
        self.engine: Engine = create_engine(url)
        self.batch_rows = batch_rows

    def test_connection(self) -> None:
        with self.engine.connect() as conn:
            conn.execute(text("SELECT 1"))

    def _from(self, source: Source):  # type: ignore[no-untyped-def]
        if isinstance(source, Relation):
            return table(source.name, schema=source.schema)
        return text(source.sql).columns().subquery("src")

    def schema(self, source: Source) -> pa.Schema:
        # Ask Postgres for the result types of the source, without reading rows.
        if isinstance(source, Relation):
            target = f"{source.schema}.{source.name}" if source.schema else source.name
            probe = f"SELECT * FROM {target}"
        else:
            probe = source.sql
        with self.engine.connect() as conn:
            conn.execute(text(f"CREATE TEMP VIEW _probe AS {probe}"))
            rows = conn.execute(text("""
                SELECT a.attname, format_type(a.atttypid, a.atttypmod), t.typname
                FROM pg_attribute a JOIN pg_type t ON t.oid = a.atttypid
                WHERE a.attrelid = '_probe'::regclass AND a.attnum > 0 ORDER BY a.attnum""")).all()
            conn.rollback()
        return pa.schema([pa.field(name, _arrow_type(full)) for name, full, _ in rows])

    def read(
        self, source: Source, columns: Sequence[str] | None = None, cast: pa.Schema | None = None
    ) -> Iterator[pa.RecordBatch]:
        full = self.schema(source)
        names = list(columns) if columns else full.names
        schema = pa.schema([full.field(n) for n in names])
        stmt = select(*[column(n) for n in names]).select_from(self._from(source))
        with self.engine.connect() as conn:
            result = conn.execution_options(stream_results=True, yield_per=self.batch_rows).execute(stmt)
            for chunk in result.partitions():
                cols = list(zip(*chunk, strict=True)) if chunk else [[] for _ in names]
                batch = pa.RecordBatch.from_arrays(
                    [pa.array(c, type=f.type) for c, f in zip(cols, schema, strict=True)], schema=schema
                )
                yield batch.cast(cast) if cast is not None else batch

    def replace(self, target: Relation, data: pa.Table, partition: Mapping[str, object]) -> int:
        """Delete the partition's rows and COPY the new ones, in one transaction."""
        tbl = table(target.name, *[column(n) for n in data.schema.names], schema=target.schema)
        where = [literal_column(k) == v for k, v in partition.items()]
        with self.engine.begin() as conn:
            conn.execute(delete(tbl).where(*where))
            return _copy(conn, target, data)


def _arrow_type(pg: str) -> pa.DataType:
    if pg.startswith("numeric"):
        if "(" not in pg:
            return pa.decimal128(38, 10)
        precision, scale = pg[pg.index("(") + 1 : -1].split(",")
        return pa.decimal128(int(precision), int(scale))
    return _PG_TYPES[pg]


def _copy(conn: Connection, target: Relation, data: pa.Table) -> int:
    raw = conn.connection.driver_connection
    name = f'"{target.schema}"."{target.name}"' if target.schema else f'"{target.name}"'
    cols = ", ".join(f'"{n}"' for n in data.schema.names)
    with raw.cursor() as cur, cur.copy(f"COPY {name} ({cols}) FROM STDIN") as copy:
        for batch in data.to_batches():
            for row in zip(*(c.to_pylist() for c in batch.columns), strict=True):
                copy.write_row(row)
    return data.num_rows


# --- DuckDB over Parquet files --------------------------------------------------------------------


class ParquetAdapter:
    """A directory of Parquet files; a relation is a file (``<name>.parquet``) or a partitioned
    directory (``<name>/<key>=<value>.parquet``). Queries are DuckDB SQL over those names."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _connect(self) -> duckdb.DuckDBPyConnection:
        db = duckdb.connect()
        for path in sorted(self.root.glob("*.parquet")):
            db.execute(f"CREATE VIEW {path.stem} AS SELECT * FROM read_parquet('{path}')")
        for path in sorted(p for p in self.root.iterdir() if p.is_dir()):
            db.execute(f"CREATE VIEW {path.name} AS SELECT * FROM read_parquet('{path}/*.parquet')")
        return db

    def test_connection(self) -> None:
        if not self.root.is_dir():
            raise FileNotFoundError(self.root)

    def _sql(self, source: Source) -> str:
        return f"SELECT * FROM {source.name}" if isinstance(source, Relation) else source.sql

    def schema(self, source: Source) -> pa.Schema:
        return self._connect().sql(f"SELECT * FROM ({self._sql(source)}) LIMIT 0").to_arrow_table().schema

    def read(
        self, source: Source, columns: Sequence[str] | None = None, cast: pa.Schema | None = None
    ) -> Iterator[pa.RecordBatch]:
        cols = ", ".join(f'"{c}"' for c in columns) if columns else "*"
        reader = self._connect().sql(f"SELECT {cols} FROM ({self._sql(source)})").to_arrow_reader(50_000)
        for batch in reader:
            yield batch.cast(cast) if cast is not None else batch

    def replace(self, target: Relation, data: pa.Table, partition: Mapping[str, object]) -> int:
        """One file per partition, written to a temp name and renamed: readers never see half."""
        key = "_".join(f"{k}={v}" for k, v in partition.items())
        directory = self.root / target.name
        directory.mkdir(parents=True, exist_ok=True)
        tmp = directory / f".{key}.parquet.tmp"
        pq.write_table(data, tmp)
        os.replace(tmp, directory / f"{key}.parquet")
        return data.num_rows
