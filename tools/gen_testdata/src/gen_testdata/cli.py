"""``gen-testdata``: write Nordwerk into a warehouse database, optionally export files."""

import argparse
import time
from datetime import date
from pathlib import Path

import psycopg

from gen_testdata import master as master_data
from gen_testdata import warehouse
from gen_testdata.actuals import Rows
from gen_testdata.calendar import FIXED_ANCHOR, Calendar, relative_anchor


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="gen-testdata", description=__doc__)
    parser.add_argument(
        "--db",
        required=True,
        help="warehouse database, e.g. postgresql://postgres:postgres@localhost/dwh",
    )
    parser.add_argument("--variant", choices=sorted(master_data.VARIANTS), default="standard")
    parser.add_argument("--date-mode", choices=["fixed", "relative"], default="fixed")
    parser.add_argument("--fiscal-start-month", type=int, default=1, choices=range(1, 13))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--bronze-schema", default="bronze")
    parser.add_argument("--gold-schema", default="gold")
    parser.add_argument("--out", type=Path, help="also export the gold views as CSV and Parquet")
    args = parser.parse_args(argv)

    anchor = FIXED_ANCHOR if args.date_mode == "fixed" else relative_anchor(date.today())
    variant = master_data.VARIANTS[args.variant]
    calendar = Calendar(anchor, args.fiscal_start_month, variant.full_years)
    master = master_data.build(variant, calendar, args.seed)

    started = time.monotonic()
    with psycopg.connect(args.db) as conn:
        counts = warehouse.load(
            conn,
            master,
            Rows(master, args.seed),
            args.date_mode,
            args.bronze_schema,
            args.gold_schema,
        )
        for table, n in counts.items():
            print(f"{args.bronze_schema}.{table}: {n:,} rows")
        if args.out:
            for path in warehouse.export(conn, args.out, args.gold_schema):
                print(f"wrote {path}")
    first, last = calendar.periods[0], calendar.periods[-1]
    print(f"{variant.name}: {first:%Y-%m} to {last:%Y-%m}, {time.monotonic() - started:.1f}s")
