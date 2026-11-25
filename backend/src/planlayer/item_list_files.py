"""Item lists as an Excel workbook (ADR 0017): an overview sheet with one row per list, linked to
the list's own sheet, then one sheet per list with its items, category subtotals and total.
No database access here; the lists come as the user sees them.

Amounts are written as numbers, not formulas, so the file reads the same in any program. Text
typed by users is always written as text: a name starting with ``=`` stays a name."""

import io
import re
from decimal import Decimal

import openpyxl
from openpyxl.styles import Alignment, Border, Font, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.hyperlink import Hyperlink
from openpyxl.worksheet.worksheet import Worksheet

from planlayer.item_lists import ItemList, Visibility

MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
VISIBLE_TO: dict[Visibility, str] = {
    "private": "Private",
    "cost_center": "Cost center",
    "public": "Public",
}
OVERVIEW = "Overview"
AMOUNT = "#,##0.00"
DATE = "yyyy-mm-dd"
BOLD = Font(bold=True)
TOP = Border(top=Side(style="thin"))
# Excel's rules for a sheet name: at most 31 characters, none of these, unique ignoring case.
MAX_NAME = 31
NOT_IN_NAME = re.compile(r"[\[\]:*?/\\]")


def _sheet_names(lists: list[ItemList]) -> list[str]:
    """One name per list from its codes, cut to Excel's length and numbered when two collide."""
    taken = {OVERVIEW.lower()}
    names = []
    for lst in lists:
        base = NOT_IN_NAME.sub("-", f"{lst.cost_center_code} {lst.account_code}").strip("'")
        base = base[:MAX_NAME] or "List"
        name, n = base, 1
        while name.lower() in taken:
            n += 1
            suffix = f" ({n})"
            name = base[: MAX_NAME - len(suffix)] + suffix
        taken.add(name.lower())
        names.append(name)
    return names


def _put(ws: Worksheet, row: int, col: int, value: object, fmt: str | None = None) -> None:
    cell = ws.cell(row, col)
    cell.value = value  # type: ignore[assignment]
    if isinstance(value, str):
        cell.data_type = "s"  # never a formula
    if fmt:
        cell.number_format = fmt


def _widths(ws: Worksheet, widths: tuple[int, ...]) -> None:
    for i, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = width


def _overview(ws: Worksheet, lists: list[ItemList], names: list[str]) -> None:
    header = (
        "Sheet", "Cost center", "Cost center name", "Account", "Account name", "Owner",
        "Visible to", "Items", "Total", "Changed", "Changed by",
    )  # fmt: skip
    for col, title in enumerate(header, start=1):
        _put(ws, 1, col, title)
        ws.cell(1, col).font = BOLD
    for row, (lst, name) in enumerate(zip(lists, names, strict=True), start=2):
        _put(ws, row, 1, name)
        link = ws.cell(row, 1)
        quoted = name.replace("'", "''")
        link.hyperlink = Hyperlink(ref=link.coordinate, location=f"'{quoted}'!A1")
        link.style = "Hyperlink"
        _put(ws, row, 2, lst.cost_center_code, "@")
        _put(ws, row, 3, lst.cost_center_name)
        _put(ws, row, 4, lst.account_code, "@")
        _put(ws, row, 5, lst.account_name)
        _put(ws, row, 6, lst.owner_name)
        _put(ws, row, 7, VISIBLE_TO[lst.visibility])
        _put(ws, row, 8, lst.items)
        _put(ws, row, 9, lst.total, AMOUNT)
        _put(ws, row, 10, lst.changed_at.date(), DATE)
        _put(ws, row, 11, lst.changed_by)
    total = len(lists) + 2
    _put(ws, total, 1, "Total")
    _put(ws, total, 8, sum(lst.items for lst in lists))
    _put(ws, total, 9, sum((lst.total for lst in lists), Decimal(0)), AMOUNT)
    for col in range(1, len(header) + 1):
        ws.cell(total, col).font = BOLD
        ws.cell(total, col).border = TOP
    ws.freeze_panes = "A2"
    _widths(ws, (16, 12, 28, 10, 28, 22, 12, 8, 14, 12, 22))


def _list_sheet(ws: Worksheet, lst: ItemList) -> None:
    _put(ws, 1, 1, f"{lst.cost_center_code} {lst.cost_center_name} · "
                   f"{lst.account_code} {lst.account_name}")  # fmt: skip
    ws.cell(1, 1).font = Font(bold=True, size=13)
    _put(ws, 2, 1, f"Owner {lst.owner_name} · Visible to {VISIBLE_TO[lst.visibility]} · "
                   f"Changed {lst.changed_at:%Y-%m-%d} by {lst.changed_by}")  # fmt: skip
    ws.cell(2, 1).font = Font(italic=True, color="666666")
    header = ("Item", "Supplier", "Quantity", "Unit price", "Amount", "Comment")
    for col, title in enumerate(header, start=1):
        _put(ws, 4, col, title)
        ws.cell(4, col).font = BOLD
        ws.cell(4, col).border = Border(bottom=Side(style="thin"))
    row = 5
    in_category = False
    for i, entry in enumerate(lst.entries):
        if entry.kind == "heading":
            # The subtotal of the items below, up to the next category.
            subtotal = Decimal(0)
            for after in lst.entries[i + 1 :]:
                if after.kind == "heading":
                    break
                subtotal += after.amount or 0
            _put(ws, row, 1, entry.name)
            _put(ws, row, 5, subtotal, AMOUNT)
            ws.cell(row, 1).font = BOLD
            ws.cell(row, 5).font = BOLD
            in_category = True
        else:
            _put(ws, row, 1, entry.name)
            if in_category:
                ws.cell(row, 1).alignment = Alignment(indent=1)
            _put(ws, row, 2, entry.supplier)
            _put(ws, row, 3, entry.quantity, "General")
            _put(ws, row, 4, entry.unit_price, AMOUNT)
            _put(ws, row, 5, entry.amount, AMOUNT)
            _put(ws, row, 6, entry.comment)
        row += 1
    _put(ws, row, 1, "Total")
    _put(ws, row, 5, lst.total, AMOUNT)
    for col in range(1, len(header) + 1):
        ws.cell(row, col).font = BOLD
        ws.cell(row, col).border = TOP
    ws.freeze_panes = "A5"
    _widths(ws, (36, 22, 10, 14, 14, 40))


def workbook(lists: list[ItemList]) -> bytes:
    """The overview first, then the lists in the order given."""
    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = OVERVIEW
    names = _sheet_names(lists)
    _overview(ws, lists, names)
    for lst, name in zip(lists, names, strict=True):
        _list_sheet(wb.create_sheet(name), lst)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
