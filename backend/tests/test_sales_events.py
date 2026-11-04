import unittest
from unittest.mock import patch

import pandas as pd

from api.routes.sales_dashboard import _apply_filters, _apply_timeline_filter, _fy_years_for_timeline, _load_sales_for_timeline


class SalesEventTests(unittest.TestCase):
    def test_event_year_loads_only_required_fiscal_years(self):
        for year in (2024, 2025, 2026):
            self.assertEqual(_fy_years_for_timeline(year_val=2027, timeline_mode="event", timeline_values=f"diwali:{year}"), [year + 1])
            self.assertEqual(_fy_years_for_timeline(year_val=2027, timeline_mode="event", timeline_values=f"republic:{year}"), [year])
            self.assertEqual(_fy_years_for_timeline(year_val=2027, timeline_mode="event", timeline_values=f"wedding:{year}"), [year, year + 1])

    def test_historical_events_and_category_filters(self):
        frame = pd.DataFrame({
            "date": pd.to_datetime(["2024-10-10", "2025-10-10", "2025-10-11", "2026-08-10"]),
            "department": ["Dress", "Dress", "Tops", "Dress"], "net_sale_amount": [100, 200, 300, 400],
        })
        selected = _apply_timeline_filter(frame, timeline_mode="event", timeline_values="diwali:2024,diwali:2025")
        self.assertEqual(len(selected), 3)
        self.assertEqual(_apply_filters(selected, departments="Dress").net_sale_amount.sum(), 300)
        self.assertTrue(_apply_timeline_filter(frame, timeline_mode="event", timeline_values="diwali:2026").empty)
        self.assertEqual(_apply_timeline_filter(frame, timeline_mode="event", timeline_values="independence:2026").net_sale_amount.sum(), 400)

    def test_january_event_uses_calendar_year_not_default_fy(self):
        frame = pd.DataFrame({"date": pd.to_datetime(["2025-01-26", "2026-01-26", "2027-01-26"])})
        selected = _apply_timeline_filter(frame, timeline_mode="event", timeline_values="republic:2025")
        self.assertEqual(selected.date.tolist(), [pd.Timestamp("2025-01-26")])

    def test_year_slices_do_not_deduplicate_genuine_sales_lines(self):
        frame = pd.DataFrame({"date": pd.to_datetime(["2025-01-26", "2025-01-26", "2025-10-10"]), "qty": [1, 1, 1]})
        def load(year, **kwargs):
            fy = frame.date.dt.year + (frame.date.dt.month >= 4).astype(int)
            return frame.loc[fy == year]
        with patch("api.sales_year_loader.load_sales_for_year", side_effect=load):
            loaded = _load_sales_for_timeline(year_val=2027, timeline_mode="event", timeline_values="republic:2025,diwali:2025")
        self.assertEqual(len(loaded), 3)


if __name__ == "__main__":
    unittest.main()
