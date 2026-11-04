"""
Detect which row starts the real column headers in vendor Excel exports.

Scans only the first N rows (no full workbook load). Used by upload merge,
date_merge, and combine_* readers when header row count is uncertain.
"""
from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd


def _norm(s: object) -> str:
    return str(s).strip().lower()


def _cell_str(c: object) -> str:
    if c is None:
        return ""
    return str(c).strip()


def score_header_row_cells(
    cells: Sequence[object],
    column_hints: Optional[Sequence[str]] = None,
    next_row: Optional[Sequence[object]] = None,
) -> int:
    """Higher score = more likely a header row (not title/data)."""
    labels = [_cell_str(c) for c in cells]
    non_empty = [x for x in labels if x]
    if len(non_empty) < 2:
        return -1000

    score = 0
    hints = [_norm(h) for h in (column_hints or []) if str(h).strip()]

    for x in non_empty:
        xl = _norm(x)
        if xl.startswith("unnamed"):
            score -= 8
            continue
        if re.fullmatch(r"[\d.,]+", x.replace(" ", "")):
            score -= 10
            continue
        if re.match(r"^\d{4}-\d{2}-\d{2}", x):
            score -= 12
            continue
        if len(x) > 45:
            score -= 6
        else:
            score += 2

    norm_set = {_norm(x) for x in non_empty}
    for h in hints:
        if h in norm_set:
            score += 150
        elif any(h in nl or nl in h for nl in norm_set if nl):
            score += 45

    if any("date" in _norm(x) or "docdt" in _norm(x) for x in non_empty):
        score += 55
    if any("barcode" in _norm(x) for x in non_empty):
        score += 35
    if any(x in norm_set for x in ("site_id", "site id", "store code", "store_code", "outsite", "insite", "sstore", "dstore")):
        score += 40
    if any("qty" in _norm(x) or "quantity" in _norm(x) or "stfqty" in _norm(x) for x in non_empty):
        score += 35
    if any("docno" in _norm(x) or "stfrate" in _norm(x) for x in non_empty):
        score += 35
    # Sales table exports (new 33-column schema & legacy sales)
    sales_markers = (
        "invoice date",
        "site code",
        "channel name",
        "cat3",
        "cat5",
        "cat6",
        "rsp",
        "tax charge amt",
        "item net amt",
        "discount amt",
        "cost rate",
        "cost amt",
        "cgst amt",
    )
    sales_hits = sum(1 for m in sales_markers if m in norm_set)
    if sales_hits:
        score += 45 + sales_hits * 35

    # Transfer / invoice tracking exports (PT, SSL, Centro, Iconic)
    transfer_markers = (
        "invoice_date",
        "document date",
        "site short name",
        "invoice_quantity",
        "dc_date",
        "site name",
    )
    transfer_hits = sum(1 for m in transfer_markers if m in norm_set)
    if transfer_hits:
        score += 45 + transfer_hits * 35
    if any("invoice" in _norm(x) and "date" in _norm(x) for x in non_empty):
        score += 30

    if next_row is not None:
        nr = list(next_row or ())
        if nr:
            data_like = 0
            for c in nr[: min(len(nr), len(labels) + 5)]:
                if c is None:
                    continue
                if isinstance(c, (int, float)) and not isinstance(c, bool):
                    data_like += 1
                elif hasattr(c, "year"):
                    data_like += 2
            score += min(data_like * 3, 25)

    return score


def detect_header_row_from_rows(
    rows: List[Tuple[int, Sequence[object]]],
    column_hints: Optional[Sequence[str]] = None,
    preferred_header: Optional[int] = None,
) -> int:
    """rows: list of (row_index, cells)."""
    if not rows:
        return 0
    best_idx = rows[0][0]
    best_score = -10**9
    for i, (row_idx, cells) in enumerate(rows):
        nxt = rows[i + 1][1] if i + 1 < len(rows) else None
        sc = score_header_row_cells(cells, column_hints=column_hints, next_row=nxt)
        # Soft hint only — bonus when that row already looks like a header
        if preferred_header is not None and row_idx == int(preferred_header) and sc > 0:
            sc += 80
        if sc > best_score:
            best_score = sc
            best_idx = row_idx
    return int(best_idx)


def _scan_sheet_rows(ws, max_scan_rows: int) -> List[Tuple[int, Sequence[object]]]:
    out: List[Tuple[int, Sequence[object]]] = []
    for i, row in enumerate(ws.iter_rows(values_only=True, max_row=max_scan_rows)):
        out.append((i, list(row or ())))
    return out


def detect_excel_header_row(
    data: bytes,
    *,
    sheet_name: Any = None,
    column_hints: Optional[Sequence[str]] = None,
    max_scan_rows: int = 25,
    preferred_sheet: Any = None,
    preferred_header: Optional[int] = None,
    auto_sheet: bool = True,
) -> Tuple[int, str]:
    """Pick header row + sheet. When column_hints given, scores every sheet (auto_sheet)."""
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        names = wb.sheetnames
        if not names:
            raise ValueError("Workbook has no sheets")

        hints = [h for h in (column_hints or []) if str(h).strip()]
        scan_all_sheets = bool(auto_sheet and hints)

        def _sheet_best(name: str) -> Tuple[int, int]:
            ws = wb[name]
            rows = _scan_sheet_rows(ws, max_scan_rows)
            if not rows:
                return 0, -10**9
            hdr = detect_header_row_from_rows(
                rows,
                column_hints=column_hints,
                preferred_header=preferred_header,
            )
            cells = next((c for i, c in rows if i == hdr), rows[0][1])
            nxt = None
            for i, (row_idx, row_cells) in enumerate(rows):
                if row_idx == hdr and i + 1 < len(rows):
                    nxt = rows[i + 1][1]
                    break
            sc = score_header_row_cells(cells, column_hints=column_hints, next_row=nxt)
            if preferred_sheet is not None and str(name) == str(preferred_sheet):
                sc += 12
            elif isinstance(sheet_name, str) and str(name) == str(sheet_name):
                sc += 6
            return hdr, sc

        if scan_all_sheets:
            best_hdr, best_score, best_name = 0, -10**9, names[0]
            for name in names:
                hdr, sc = _sheet_best(name)
                if sc > best_score:
                    best_score, best_hdr, best_name = sc, hdr, name
            return int(best_hdr), str(best_name)

        pick = preferred_sheet if preferred_sheet in names else sheet_name
        if pick not in names:
            pick = names[0]
        hdr, _ = _sheet_best(str(pick))
        return int(hdr), str(pick)
    finally:
        wb.close()


def detect_csv_header_row(
    data: bytes,
    *,
    column_hints: Optional[Sequence[str]] = None,
    max_scan_rows: int = 15,
    encoding: str = "utf-8-sig",
    preferred_header: Optional[int] = None,
) -> int:
    """CSV has no sheets; pick which record is the header row.

    Uses csv.reader over the full text so multiline quoted fields stay one row
    (Shoppers Stop SOH exports embed newlines inside header cells).
    """
    import csv

    text = data.decode(encoding, errors="replace")
    rows: List[Tuple[int, Sequence[object]]] = []
    try:
        for i, cells in enumerate(csv.reader(io.StringIO(text))):
            if i >= max_scan_rows:
                break
            if not any(str(c).strip() for c in cells):
                continue
            rows.append((i, cells))
    except Exception:
        rows = []
        for i, line in enumerate(text.splitlines()):
            if i >= max_scan_rows:
                break
            if not line.strip():
                continue
            try:
                cells = next(csv.reader([line]))
            except Exception:
                cells = [line]
            rows.append((i, cells))
    if not rows:
        return int(preferred_header) if preferred_header is not None else 0
    return detect_header_row_from_rows(
        rows, column_hints=column_hints, preferred_header=preferred_header
    )


def detect_excel_layout_path(
    path: Path,
    *,
    sheet_name: Any = 0,
    column_hints: Optional[Sequence[str]] = None,
    max_scan_rows: int = 25,
    preferred_header: Optional[int] = None,
    preferred_sheet: Any = None,
    auto_sheet: bool = True,
) -> Tuple[int, Any]:
    """Return (header_row, sheet_name). sheet_name is None for CSV."""
    path = Path(path)
    lower = path.suffix.lower()
    if lower == ".csv":
        data = path.read_bytes()
        return (
            detect_csv_header_row(
                data,
                column_hints=column_hints,
                preferred_header=preferred_header,
            ),
            None,
        )
    data = path.read_bytes()
    pref_sheet = preferred_sheet
    if pref_sheet is None and isinstance(sheet_name, str):
        pref_sheet = sheet_name
    hdr, sheet = detect_excel_header_row(
        data,
        sheet_name=sheet_name,
        column_hints=column_hints,
        max_scan_rows=max_scan_rows,
        preferred_sheet=pref_sheet,
        preferred_header=preferred_header,
        auto_sheet=auto_sheet,
    )
    return int(hdr), sheet


def detect_header_row_path(
    path: Path,
    *,
    sheet_name: Any = 0,
    column_hints: Optional[Sequence[str]] = None,
    max_scan_rows: int = 25,
    preferred_header: Optional[int] = None,
) -> int:
    path = Path(path)
    hdr, _sheet = detect_excel_layout_path(
        path,
        sheet_name=sheet_name,
        column_hints=column_hints,
        max_scan_rows=max_scan_rows,
        preferred_header=preferred_header,
    )
    return hdr


def merge_column_hints(cfg: dict) -> List[str]:
    hints: List[str] = []
    for key in ("column_hints", "date_candidates"):
        for h in cfg.get(key) or []:
            if h and str(h) not in hints:
                hints.append(str(h))
    return hints


def resolve_category_layout(
    cfg: dict,
    data: bytes,
    filename: str,
) -> Dict[str, Any]:
    """
    Resolve header row, sheet, and column hints for a category upload.

    preferred header_row / sheet_name in cfg are soft hints only.
    """
    column_hints = merge_column_hints(cfg)
    preferred_sheet = cfg.get("sheet_name")
    pref_hdr = cfg.get("header_row")
    preferred_header = int(pref_hdr) if pref_hdr is not None else None

    lower = (filename or "").lower()
    if lower.endswith(".csv"):
        header_row = detect_csv_header_row(data, column_hints=column_hints)
        return {
            "header_row": int(header_row),
            "sheet": None,
            "column_hints": column_hints,
            "preferred_sheet": None,
        }

    header_row, sheet = detect_excel_header_row(
        data,
        column_hints=column_hints,
        preferred_sheet=preferred_sheet,
        preferred_header=preferred_header,
        sheet_name=preferred_sheet,
    )
    return {
        "header_row": int(header_row),
        "sheet": sheet,
        "column_hints": column_hints,
        "preferred_sheet": preferred_sheet or sheet,
    }
