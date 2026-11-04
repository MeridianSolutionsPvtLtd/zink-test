"""Master fold must catch up from daily current when Raw_Files are already cleared."""
import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

from pipeline import post_merge_refresh as pmr


class MasterFoldDailyCurrentTests(unittest.TestCase):
    def test_dedupe_keeps_one_invoice_barcode_row(self):
        df = pd.DataFrame(
            {
                "Invoice No": ["A", "A", "B"],
                "Barcode": ["1", "1", "2"],
                "Qty": [1, 1, 3],  # same qty on dup so identity keys collapse
            }
        )
        out = pmr._dedupe_delta_frame(df)
        self.assertEqual(len(out), 2)

    def test_collect_falls_back_to_daily_current_when_raw_empty(self):
        daily_cfg = {
            "folder": "Sales (PT, SSL, Centro, Iconic, EBO)",
            "canonical_name": "SSI_PT_CENTRO_ICONIC_SALES.csv",
            "date_candidates": ["ENTRY_DATE"],
            "header_row": 0,
            "column_hints": ["ENTRY_DATE", "Barcode", "Qty"],
        }
        master_cfg = {
            "folder": "Sales-Master (PT, SSL, Centro, Iconic, EBO)",
            "canonical_name": "SSI_PT_CENTRO_ICONIC_EBO_SALES_MASTER.csv",
            "date_candidates": ["ENTRY_DATE"],
        }
        daily_frame = pd.DataFrame(
            {
                "ENTRY_DATE": ["2026-10-09", "2026-10-10", "2026-10-11"],
                "Barcode": ["111", "222", "333"],
                "Qty": [1, 2, 3],
                "Invoice No": ["X", "Y", "Z"],
            }
        )

        cc = MagicMock()
        cc.list_blobs.return_value = []  # no Raw_Files
        blob = MagicMock()
        blob.name = "input/Sales (PT, SSL, Centro, Iconic, EBO)/current/SSI_PT_CENTRO_ICONIC_SALES.csv"

        with patch.object(pmr, "CATEGORY_MERGE_CONFIG", create=True), \
             patch("pipeline.merge_category_inputs.CATEGORY_MERGE_CONFIG", {
                 "sales-pt-ssl-centro-iconic": daily_cfg,
             }), \
             patch("pipeline.merge_category_inputs._category_prefixes", return_value={
                 "incoming": "in/", "current": "cur/", "history": "h/", "base": "b/",
             }), \
             patch("pipeline.merge_category_inputs._list_files", return_value=[blob]), \
             patch("pipeline.merge_category_inputs._download", return_value=b"csv"), \
             patch("pipeline.date_merge.read_excel_smart", return_value=(daily_frame, {})), \
             patch("pipeline.date_merge.find_date_column", return_value="ENTRY_DATE"), \
             patch("pipeline.date_merge.to_day_series", side_effect=lambda s: pd.to_datetime(s, errors="coerce")):
            frames, files, dates, source = pmr._collect_newer_delta_frames(
                cc,
                "input/",
                "sales-pt-ssl-centro-iconic",
                master_cfg,
                cut=pd.Timestamp("2026-10-09"),
                year=2026,
            )

        self.assertEqual(source, "daily_current")
        self.assertTrue(frames)
        self.assertEqual(len(frames[0]), 2)  # 10th + 11th only
        self.assertTrue(any("daily:" in f for f in files))


if __name__ == "__main__":
    unittest.main()
