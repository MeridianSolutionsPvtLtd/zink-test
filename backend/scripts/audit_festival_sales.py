"""Read-only reconciliation of event filters against Sales-Master categories."""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from api.routes.sales_dashboard import _apply_timeline_filter


def main():
    source = ROOT / "pipeline/input_files/SSI_PT_CENTRO_ICONIC_EBO_SALES_MASTER.csv"
    frame = pd.read_csv(source, usecols=["Invoice Date", "Department", "Secion", "Qty", "Item Net Amt"], low_memory=False)
    frame["date"] = pd.to_datetime(frame["Invoice Date"], format="mixed", errors="coerce")
    month, day = frame.date.dt.month, frame.date.dt.day
    # Independent masks, matching the labelled retail windows in the UI.
    windows = {
        "diwali": month.isin([10, 11]), "eos_ss": month.isin([6, 7]),
        "eos_aw": month.isin([1, 2]), "wedding": month.isin([11, 12, 1, 2]),
        "republic": month.eq(1) & day.ge(15),
        "independence": month.eq(8) & day.between(5, 20),
        "newyear": (month.eq(12) & day.ge(20)) | (month.eq(1) & day.le(5)),
        "holi": month.eq(3),
    }
    reports = []
    for year in (2024, 2025, 2026):
        for event, window in windows.items():
            expected = frame.loc[frame.date.dt.year.eq(year) & window]
            actual = _apply_timeline_filter(frame, timeline_mode="event", timeline_values=f"{event}:{year}")
            pd.testing.assert_frame_equal(actual, expected)
            grouped = actual.groupby(["Department", "Secion"], dropna=False).agg(rows=("date", "size"), source_item_net_amount=("Item Net Amt", "sum"), qty=("Qty", "sum")).reset_index()
            grouped.insert(0, "event", event)
            grouped.insert(0, "year", year)
            reports.append(grouped)
            print(f"{year} {event}: {len(actual):,} rows; {len(grouped)} categories; source amount {actual['Item Net Amt'].sum():,.2f}")
    output = ROOT / "data/api_cache/festival_sales_category_audit.csv"
    pd.concat(reports, ignore_index=True).to_csv(output, index=False)
    print(f"All 24 event/year selections reconcile. Category report: {output}")


if __name__ == "__main__":
    main()
