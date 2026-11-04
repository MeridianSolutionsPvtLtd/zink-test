import unittest
from unittest.mock import patch

import pandas as pd

from pipeline.financial_calendar import merge_calendar, normalize_calendar
from pipeline.ginesys_raw import ginesys_category
from api.routes.sales_dashboard import _apply_timeline_filter, _load_sply_prior_df


class FinancialWeekInputTests(unittest.TestCase):
    def test_file_name_detection(self):
        self.assertEqual(ginesys_category("FY year & FY Week.xlsx"), "financial-week-info")

    def test_duplicate_rows_do_not_duplicate_sales_dates(self):
        data = pd.DataFrame({"Date": ["01-04-2026"] * 2, "F. Year": ["FY-27"] * 2, "FY WK": ["WK-01", "WEEK-1"]})
        self.assertEqual(len(normalize_calendar(data)), 1)

    def test_conflicting_assignments_are_not_silently_used(self):
        data = pd.DataFrame({"Date": ["01-04-2026"] * 2, "F. Year": ["FY-27"] * 2, "FY WK": ["WK-01", "WK-02"]})
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            normalize_calendar(data)

    def test_incremental_date_correction_retains_history(self):
        old = pd.DataFrame({"Date": ["01-04-2026", "02-04-2026"], "F. Year": ["FY-27"] * 2, "FY WK": ["WK-01"] * 2})
        new = pd.DataFrame({"Date": ["02-04-2026"], "F. Year": ["FY-27"], "FY WK": ["WK-02"]})
        merged = merge_calendar(old, new)
        self.assertEqual(merged["FY WK"].tolist(), ["WK-01", "WK-02"])

    def test_exact_mapped_dates_and_last_year_exclude_gaps(self):
        dates = pd.DatetimeIndex(["2026-04-01", "2026-04-03"])
        sales = pd.DataFrame({"date": pd.to_datetime(["2026-04-01", "2026-04-02", "2026-04-03"]), "net_sale_amount": [100, 999, 200]})
        prior = pd.DataFrame({"date": pd.to_datetime(["2025-04-01", "2025-04-02", "2025-04-03"]), "net_sale_amount": [50, 999, 100]})
        with patch("api.financial_calendar.selected_week_dates", return_value=dates), patch("api.sales_year_loader.load_sales_for_year", return_value=prior):
            current = _apply_timeline_filter(sales, timeline_mode="fy_week", timeline_values="FY27-W01")
            last_year = _load_sply_prior_df(current, timeline_mode="fy_week", timeline_values="FY27-W01")
        self.assertEqual(current.net_sale_amount.sum(), 300)
        self.assertEqual(last_year.net_sale_amount.sum(), 150)


if __name__ == "__main__":
    unittest.main()
