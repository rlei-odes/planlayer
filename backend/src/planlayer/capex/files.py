"""A round's investments as an Excel workbook (ADR 0028): the list as the user sees it, then the
cash-out schedules. No database access here; the view comes as the user may read it.

Large projects come with their lines below them, indented; the totals count projects and single
investments, not the lines again. Amounts are numbers, not formulas, so the file reads the same
in any program; text typed by users is always text, so a name starting with ``=`` stays a name.
"""

import io
from datetime import date
from decimal import Decimal

import openpyxl
from openpyxl.styles import Alignment, Border, Font, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from planlayer.capex.items import CapexView, Item

MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
AMOUNT = "#,##0.00"
MONTH = "mmm yyyy"
DATE = "yyyy-mm-dd"
BOLD = Font(bold=True)
TOP = Border(top=Side(style="thin"))
STATUS = {"open": "Open", "confirmed": "Confirmed", "declined": "Declined"}


def _put(ws: Worksheet, row: int, col: int, value: object, fmt: str | None = None) -> None:
    cell = ws.cell(row, col)
    cell.value = value  # type: ignore[assignment]
    if isinstance(value, str):
        cell.data_type = "s"  # never a formula
    if fmt:
        cell.number_format = fmt


def _header(ws: Worksheet, titles: tuple[str, ...]) -> None:
    for col, title in enumerate(titles, start=1):
        _put(ws, 1, col, title)
        ws.cell(1, col).font = BOLD
    ws.freeze_panes = "A2"


def _widths(ws: Worksheet, widths: tuple[int, ...]) -> None:
    for i, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = width


def _kind(item: Item) -> str:
    if item.kind == "project":
        return "Large project"
    return "Project line" if item.project_id is not None else "Investment"


def _counted(items: list[Item]) -> list[Item]:
    """What a total counts: projects (the sum of their lines) and single investments."""
    return [i for i in items if i.project_id is None]


def _total(ws: Worksheet, row: int, columns: int, sums: dict[int, Decimal]) -> None:
    _put(ws, row, 1, "Total")
    for col, value in sums.items():
        _put(ws, row, col, value, AMOUNT)
    for col in range(1, columns + 1):
        ws.cell(row, col).font = BOLD
        ws.cell(row, col).border = TOP


def _investments(ws: Worksheet, view: CapexView) -> None:
    types = {t.code: t.name for t in view.types}
    header = (
        "Name", "Kind", "Cost center", "Cost center name", "Type", "Priority", "Start",
        "Amount", "Cash out planned", "Status", "Confirmed or declined by", "On",
        "Changed by", "Changed",
    )  # fmt: skip
    _header(ws, header)
    row = 2
    for item in view.items:
        _put(ws, row, 1, item.name)
        if item.project_id is not None:
            ws.cell(row, 1).alignment = Alignment(indent=2)
        if item.kind == "project":
            ws.cell(row, 1).font = BOLD
        _put(ws, row, 2, _kind(item))
        _put(ws, row, 3, item.cost_center_code, "@")
        _put(ws, row, 4, item.cost_center_name)
        _put(ws, row, 5, types.get(item.type_code or "", item.type_code or ""))
        _put(ws, row, 6, item.priority)
        _put(ws, row, 7, item.start_month, MONTH)
        _put(ws, row, 8, item.amount, AMOUNT)
        _put(ws, row, 9, item.scheduled, AMOUNT)
        _put(ws, row, 10, STATUS[item.status])
        _put(ws, row, 11, item.status_by if item.status != "open" else None)
        on = item.status_at.date() if item.status_at and item.status != "open" else None
        _put(ws, row, 12, on, DATE)
        _put(ws, row, 13, item.changed_by)
        _put(ws, row, 14, item.changed_at.date() if item.changed_at else None, DATE)
        row += 1
    counted = _counted(view.items)
    _total(
        ws,
        row,
        len(header),
        {
            8: sum((i.amount for i in counted if i.amount is not None), Decimal(0)),
            9: sum((i.scheduled for i in counted), Decimal(0)),
        },
    )
    _widths(ws, (36, 14, 12, 26, 22, 9, 11, 14, 16, 11, 24, 12, 24, 12))


def _cash_out(ws: Worksheet, view: CapexView) -> None:
    """One row per investment with a schedule, one column per period and the next year."""
    periods: list[date] = [*view.periods, view.next_year]
    quarters = view.round.grain == "quarter"
    header = (
        "Name",
        "Cost center",
        *(f"Q{i + 1}" if quarters else f"{p:%b %Y}" for i, p in enumerate(view.periods)),
        f"FY {view.round.fiscal_year + 1}",
        "Total",
        "Amount",
    )
    _header(ws, header)
    row = 2
    scheduled = [i for i in view.items if i.kind == "line" and i.cash_out]
    totals = [Decimal(0)] * len(periods)
    for item in scheduled:
        _put(ws, row, 1, item.name)
        _put(ws, row, 2, item.cost_center_code, "@")
        for col, period in enumerate(periods, start=3):
            value = item.cash_out.get(period)
            _put(ws, row, col, value, AMOUNT)
            if value is not None:
                totals[col - 3] += value
        _put(ws, row, len(periods) + 3, item.scheduled, AMOUNT)
        _put(ws, row, len(periods) + 4, item.amount, AMOUNT)
        row += 1
    sums = {col: total for col, total in enumerate(totals, start=3)}
    sums[len(periods) + 3] = sum(totals, Decimal(0))
    _total(ws, row, len(header), sums)
    _widths(ws, (36, 12, *([12] * len(periods)), 14, 14))


def workbook(view: CapexView) -> bytes:
    book = openpyxl.Workbook()
    first = book.active
    assert first is not None
    first.title = "Investments"
    _investments(first, view)
    _cash_out(book.create_sheet("Cash out"), view)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()
