import io
import unittest
import zipfile
from unittest.mock import patch

import openpyxl
import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from api.routes import sales_dashboard as sales


class SalesExcelExportTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(sales.router)
        self.client = TestClient(app)
        self.frame = pd.DataFrame({
            "date": pd.to_datetime(["2026-04-01", "2026-04-02"]),
            "site_id": ["A", "B"], "sku": ["=SUM(1,2)", "OTHER"],
            "qty": [3, 7], "net_sale_amount": [150, 350], "mrp": [100, 100],
        })

    def test_real_xlsx_with_and_without_images_respects_filters(self):
        thumbnail = io.BytesIO()
        Image.new("RGB", (10, 10), "blue").save(thumbnail, "PNG")
        for images in ("true", "false"):
            with self.subTest(images=images), patch.object(sales, "_load_sales_for_timeline", return_value=self.frame), patch.object(sales, "_get_sku_thumbnail_bytes", return_value=thumbnail.getvalue()):
                response = self.client.get("/api/sales-dashboard/export-excel", params={
                    "year": 2027, "timeline_mode": "fy_year", "timeline_values": "FY27",
                    "site_id": "A", "columns": "sku,qty,image", "with_images": images,
                })
                self.assertEqual(response.status_code, 200, response.text[:100] if response.status_code != 200 else "")
                self.assertEqual(int(response.headers["content-length"]), len(response.content))
                archive = zipfile.ZipFile(io.BytesIO(response.content))
                self.assertIsNone(archive.testzip())
                self.assertIn("xl/workbook.xml", archive.namelist())
                self.assertEqual(any(n.startswith("xl/media/") for n in archive.namelist()), images == "true")
                book = openpyxl.load_workbook(io.BytesIO(response.content))
                sheet = book.active
                self.assertEqual(sheet.max_row, 2)
                cells = list(sheet[2])
                sku = next(c for c in cells if c.value == "=SUM(1,2)")
                self.assertEqual(sku.data_type, "s")
                self.assertTrue(any(c.value == 3 for c in cells))


if __name__ == "__main__":
    unittest.main()
