import unittest
from unittest.mock import patch
import pandas as pd

from api.routes import manager_performance as mgr


class ManagerPerformanceFYTests(unittest.TestCase):
    def test_overview_respects_financial_year_parameter(self):
        mock_fy27 = pd.DataFrame({
            'manager': ['SANJAY', 'ROHIT'],
            'cluster': ['SANJAY', 'ROHIT'],
            'site_id': ['S101', 'S102'],
            'site_name': ['Store 1', 'Store 2'],
            'city': ['Delhi', 'Mumbai'],
            'zone': ['North', 'West'],
            'state': ['Delhi', 'Maharashtra'],
            'channel': ['PT', 'SSL'],
            'mrp': [1000.0, 2000.0],
            'qty': [10, 20],
            'net_sale_amount': [9000.0, 36000.0],
            'discount_amount': [1000.0, 4000.0],
        })

        mock_fy26 = pd.DataFrame({
            'manager': ['SANJAY'],
            'cluster': ['SANJAY'],
            'site_id': ['S101'],
            'site_name': ['Store 1'],
            'city': ['Delhi'],
            'zone': ['North'],
            'state': ['Delhi'],
            'channel': ['PT'],
            'mrp': [1000.0],
            'qty': [5],
            'net_sale_amount': [4500.0],
            'discount_amount': [500.0],
        })

        with patch.object(mgr, '_OVERVIEW_RESULT_CACHE', {}), patch.object(mgr, '_SALES_CACHE_DF', {}), patch.object(mgr, '_STORE_SUMMARY_CACHE', {}), patch.object(mgr, '_load_sales', side_effect=lambda y: mock_fy27 if y == 2027 else mock_fy26), patch.object(mgr, '_load_target_by_site', return_value={'S101': 10000.0, 'S102': 40000.0}), patch.object(mgr, '_load_soh_by_site', return_value={'S101': 5, 'S102': 10}):
            res27 = mgr.get_managers_overview(year=2027)
            self.assertEqual(res27['period']['financial_year'], 'FY27')
            self.assertEqual(res27['summary']['total_net_sales'], 45000.0)
            self.assertEqual(len(res27['managers']), 2)

            res26 = mgr.get_managers_overview(year=2026)
            self.assertEqual(res26['period']['financial_year'], 'FY26')
            self.assertEqual(res26['summary']['total_net_sales'], 4500.0)
            self.assertEqual(len(res26['managers']), 1)

            res_all = mgr.get_managers_overview(financial_year="all")
            self.assertEqual(res_all['period']['financial_year'], 'All Years')
            self.assertEqual(res_all['period']['year'], 0)

            res_fy26_str = mgr.get_managers_overview(financial_year="FY26")
            self.assertEqual(res_fy26_str['period']['financial_year'], 'FY26')
            self.assertEqual(res_fy26_str['summary']['total_net_sales'], 4500.0)


if __name__ == '__main__':
    unittest.main()
