"""The files of HR's salary upload (ADR 0025): the template HR downloads and the file HR sends
back, as CSV, Excel or Parquet. No database access here; ``upload`` matches the rows.

A file holds one row per person under fixed column names. ``number`` and ``monthly_salary``
are required; ``cost_center`` is checked against the position when given; the other columns
are there for HR to read and are ignored."""

import csv
import io
import logging
import re
import zipfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

import openpyxl
import pyarrow as pa
import pyarrow.parquet as pq
from openpyxl.styles import Font
from openpyxl.utils.exceptions import InvalidFileException

from planlayer.sheet import EditError

log = logging.getLogger(__name__)

Format = Literal["csv", "xlsx", "parquet"]
FORMATS: tuple[Format, ...] = ("csv", "xlsx", "parquet")
FORMAT_NAME: dict[Format, str] = {"csv": "CSV", "xlsx": "Excel", "parquet": "Parquet"}
# What reading a damaged or mislabelled file raises.
BROKEN = (
    csv.Error,
    UnicodeDecodeError,
    ValueError,
    KeyError,
    OSError,
    zipfile.BadZipFile,
    InvalidFileException,
    pa.ArrowException,
)
MEDIA: dict[Format, str] = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "parquet": "application/vnd.apache.parquet",
}
COLUMNS = ("number", "name", "title", "cost_center", "monthly_salary")
REQUIRED = ("number", "monthly_salary")
MAX_BYTES = 20 * 1024 * 1024
MAX_ROWS = 100_000


class FileError(EditError):
    """The file can't be read as a salary upload; nothing is matched."""


@dataclass(frozen=True)
class TemplateRow:
    number: str
    name: str | None
    title: str
    cost_center: str


@dataclass(frozen=True)
class FileRow:
    line: int  # as HR sees it: the header is line 1
    number: str | None
    cost_center: str | None
    salary: Decimal | None  # None: blank
    error: str | None  # the salary can't be read


@dataclass(frozen=True)
class SalaryFile:
    rows: list[FileRow]
    ignored: list[str]  # columns other than the known ones


def format_of(filename: str) -> Format:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    match ext:
        case "csv" | "txt":
            return "csv"
        case "xlsx" | "xlsm":
            return "xlsx"
        case "parquet":
            return "parquet"
    raise FileError(f"{filename}: upload a CSV, Excel (.xlsx) or Parquet file")


# --- Template -------------------------------------------------------------------------------------


def template(rows: list[TemplateRow], fmt: Format) -> bytes:
    """The template: one row per person, the salary blank."""
    match fmt:
        case "csv":
            out = io.StringIO()
            writer = csv.writer(out, lineterminator="\r\n")
            writer.writerow(COLUMNS)
            for r in rows:
                writer.writerow([r.number, r.name or "", r.title, r.cost_center, ""])
            # With a byte order mark, Excel opens the file as UTF-8.
            return out.getvalue().encode("utf-8-sig")
        case "xlsx":
            return _xlsx(rows)
        case "parquet":
            table = pa.table(
                {
                    "number": pa.array([r.number for r in rows], pa.string()),
                    "name": pa.array([r.name for r in rows], pa.string()),
                    "title": pa.array([r.title for r in rows], pa.string()),
                    "cost_center": pa.array([r.cost_center for r in rows], pa.string()),
                    "monthly_salary": pa.array([None] * len(rows), pa.decimal128(18, 2)),
                }
            )
            buf = io.BytesIO()
            pq.write_table(table, buf)
            return buf.getvalue()


def _xlsx(rows: list[TemplateRow]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Salaries"
    ws.append(list(COLUMNS))
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for r in rows:
        ws.append([r.number, r.name, r.title, r.cost_center, None])
    # Numbers and cost centers as text, so leading zeros stay; the salary as an amount.
    for i in range(2, len(rows) + 2):
        ws.cell(i, 1).number_format = "@"
        ws.cell(i, 4).number_format = "@"
        ws.cell(i, 5).number_format = "#,##0.00"
    for col, width in zip("ABCDE", (14, 28, 28, 14, 16), strict=True):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# --- Reading --------------------------------------------------------------------------------------


def read(data: bytes, filename: str) -> SalaryFile:
    if not data:
        raise FileError(f"{filename} is empty")
    if len(data) > MAX_BYTES:
        raise FileError(f"{filename} is larger than {MAX_BYTES // (1024 * 1024)} MB")
    fmt = format_of(filename)
    try:
        match fmt:
            case "csv":
                header, body = _csv(data)
            case "xlsx":
                header, body = _xlsx_rows(data)
            case "parquet":
                header, body = _parquet(data)
    except FileError:
        raise
    except BROKEN as e:
        # A broken file is HR's to fix. Logged by type only: the file holds pay.
        log.info("salary upload %s unreadable as %s: %s", filename, fmt, type(e).__name__)
        raise FileError(f"{filename} can't be read as {FORMAT_NAME[fmt]}") from None
    names = [_column(h) for h in header]
    missing = [c for c in REQUIRED if c not in names]
    if missing:
        raise FileError(
            f"{filename} has no column {', '.join(missing)}; "
            f"the template's columns are {', '.join(COLUMNS)}"
        )
    doubled = sorted({n for n in names if n and names.count(n) > 1})
    if doubled:
        raise FileError(f"{filename} has the column {', '.join(doubled)} more than once")
    if len(body) > MAX_ROWS:
        raise FileError(f"{filename} has more than {MAX_ROWS} rows")
    index = {n: i for i, n in enumerate(names)}
    rows = []
    for line, values in body:
        if all(_blank(v) for v in values):
            continue

        def get(col: str, values: list[object] = values) -> object:
            i = index.get(col)
            return values[i] if i is not None and i < len(values) else None

        salary, error = _salary(get("monthly_salary"))
        rows.append(FileRow(line, _text(get("number")), _text(get("cost_center")), salary, error))
    ignored = [str(h) for h, n in zip(header, names, strict=True) if n and n not in COLUMNS]
    return SalaryFile(rows, ignored)


def _column(header: object) -> str:
    return re.sub(r"[\s-]+", "_", str(header or "").strip().lower())


def _csv(data: bytes) -> tuple[list[object], list[tuple[int, list[object]]]]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252")  # saved by Excel on Windows
    first = text.split("\n", 1)[0]
    delimiter = max((";", ",", "\t"), key=first.count)
    lines = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    if not lines:
        raise FileError("the file has no header")
    body: list[tuple[int, list[object]]] = [(i + 2, list(r)) for i, r in enumerate(lines[1:])]
    return list(lines[0]), body


def _xlsx_rows(data: bytes) -> tuple[list[object], list[tuple[int, list[object]]]]:
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        ws = wb.worksheets[0]
        it = ws.iter_rows(values_only=True)
        header = next(it, None)
        if header is None:
            raise FileError("the first sheet has no header")
        body: list[tuple[int, list[object]]] = []
        for i, r in enumerate(it):
            body.append((i + 2, list(r)))
            if len(body) > MAX_ROWS:
                break
        return list(header), body
    finally:
        wb.close()


def _parquet(data: bytes) -> tuple[list[object], list[tuple[int, list[object]]]]:
    table = pq.read_table(io.BytesIO(data))
    if table.num_rows > MAX_ROWS:
        raise FileError(f"the file has more than {MAX_ROWS} rows")
    columns = [table.column(i).to_pylist() for i in range(table.num_columns)]
    body: list[tuple[int, list[object]]] = [
        (i + 2, [c[i] for c in columns]) for i in range(table.num_rows)
    ]
    return list(table.column_names), body


def _blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _text(value: object) -> str | None:
    if _blank(value):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)  # a number Excel stored as a number
    return str(value).strip()


def _salary(value: object) -> tuple[Decimal | None, str | None]:
    """A monthly salary: blank, or a non-negative amount with at most two decimals."""
    if _blank(value):
        return None, None
    match value:
        case bool():
            amount = None
        case Decimal():
            amount = value
        case int():
            amount = Decimal(value)
        case float():
            # Excel and Parquet files may hold binary numbers; their shortest decimal form is
            # what HR typed.
            amount = Decimal(repr(value))
        case str():
            amount = _parse(value)
        case _:
            amount = None
    if amount is None or not amount.is_finite():
        return None, f"not an amount: {value}"
    if amount < 0:
        return None, f"a salary isn't negative: {value}"
    if amount != amount.quantize(Decimal("0.01")):
        return None, f"at most two decimal places: {value}"
    return amount.quantize(Decimal("0.01")), None


def _parse(raw: str) -> Decimal | None:
    """Text as an amount, the way the grid reads typed amounts: 4500, 4500.50, 4'500.50,
    4.500,50 or 4,500.50."""
    s = re.sub(r"[\s\u00a0']|CHF|EUR|USD|[€$]", "", raw)
    last_dot, last_comma = s.rfind("."), s.rfind(",")
    if last_dot >= 0 and last_comma >= 0:
        # Both present: the later one is the decimal separator.
        s = s.replace(".", "").replace(",", ".") if last_comma > last_dot else s.replace(",", "")
    elif last_comma >= 0:
        parts = s.split(",")
        s = f"{parts[0]}.{parts[1]}" if len(parts) == 2 and len(parts[1]) <= 2 else "".join(parts)
    elif s.count(".") > 1:
        s = s.replace(".", "")
    if not re.fullmatch(r"-?\d+(\.\d+)?", s):
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None
