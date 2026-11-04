import unittest
from unittest.mock import patch

import pandas as pd
from api.routes import sales_dashboard as sales


class SalesSkuStockTests(unittest.TestCase):
    def test_combined_soh_sums_barcodes_within_site_and_sku(self):
        soh = pd.DataFrame({"site_id": ["A", "A", "A", "B"], "sku": ["X", "X", "Y", "X"], "barcode": ["1", "2", "3", "1"], "qty": [2, 3, 99, 40]})
        with patch.object(sales, "_SOH_MAP_CACHE", None), patch.object(sales, "_SOH_MAP_TIME", 0), patch("api.routes.stock_overview._load_soh_frame", return_value=(soh, "version", "combined")) as loader:
            result = sales._get_soh_map()
        loader.assert_called_once_with(prefer="combined")
        self.assertEqual(result["site_sku"][("A", "X")], 5)
        self.assertEqual(result["site_sku"][("B", "X")], 40)
        self.assertEqual(result["site_barcode"][("A", "1")], 2)


if __name__ == "__main__":
    unittest.main()
