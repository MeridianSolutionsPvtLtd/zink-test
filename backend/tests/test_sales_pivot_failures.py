import unittest
from unittest.mock import patch

from fastapi import HTTPException
from api.routes import sales_analyzer as analyzer
from pipeline import year_stores


class SalesPivotFailureTests(unittest.TestCase):
    def test_year_loader_runs_without_blob_configuration(self):
        with patch.dict('os.environ', {}, clear=True):
            self.assertEqual(year_stores.ensure_year_files_local('sales', []), [])

    def test_blob_failure_can_use_local_fallback(self):
        with patch.dict('os.environ', {'AZURE_STORAGE_CONNECTION_STRING': 'test'}), patch('api.blob_store.BlobJsonStore', side_effect=RuntimeError('offline')):
            self.assertEqual(year_stores.ensure_year_files_local('sales', []), [])

    def test_calculation_failure_is_http_error_not_empty_success(self):
        req = analyzer.AnalyzerQueryRequest(rows=['zone'], year=2027)
        with patch('api.sales_year_loader.resolve_sales_master_path', return_value=None), \
             patch('api.financial_calendar.calendar_options', return_value={'version': 'test'}), \
             patch.object(analyzer, '_QUERY_RESULT_CACHE', {}), \
             patch.object(analyzer, '_get_prepared_sales_df', side_effect=RuntimeError('test calculation failed')), \
             patch('api.page_response_cache.load_page_cache', return_value=None), \
             patch('api.page_response_cache.save_page_cache', return_value=None), \
             patch('api.page_response_cache.rebuilding_page_cache', return_value=True):
            with self.assertRaises(HTTPException) as caught:
                analyzer.execute_analyzer_query(req)
        self.assertEqual(caught.exception.status_code, 500)

    def test_sales_per_sqft_measure_calculation(self):
        import pandas as pd
        mock_df = pd.DataFrame({
            'zone': ['North', 'North', 'South'],
            'site_id': ['S1', 'S1', 'S2'],
            'sales': [1000.0, 2000.0, 5000.0],
            'quantity': [10, 20, 50],
            'gross_sales': [1200.0, 2400.0, 6000.0],
            'discount_amount': [200.0, 400.0, 1000.0],
            'store_sqft': [500.0, 500.0, 1000.0],
        })
        req = analyzer.AnalyzerQueryRequest(
            rows=['zone'],
            values=[
                analyzer.ValueConfig(field='sales', aggregation='sum'),
                analyzer.ValueConfig(field='sales_per_sqft', aggregation='calculated'),
                analyzer.ValueConfig(field='store_sqft', aggregation='sum'),
            ],
            year=2027
        )
        with patch('api.sales_year_loader.resolve_sales_master_path', return_value=None), \
             patch('api.financial_calendar.calendar_options', return_value={'version': 'test'}), \
             patch.object(analyzer, '_QUERY_RESULT_CACHE', {}), \
             patch.object(analyzer, '_get_prepared_sales_df', return_value=mock_df), \
             patch('api.page_response_cache.load_page_cache', return_value=None), \
             patch('api.page_response_cache.save_page_cache', return_value=None), \
             patch('api.page_response_cache.rebuilding_page_cache', return_value=True):
            res = analyzer.execute_analyzer_query(req)
        self.assertTrue(res['ok'])
        # North: total sales = 3000, distinct sqft = 500 -> SPSF = 6.0
        # South: total sales = 5000, distinct sqft = 1000 -> SPSF = 5.0
        # Grand Total: total sales = 8000, distinct sqft = 1500 -> SPSF = 5.33
        gt = res['grand_total']['metrics']
        self.assertEqual(gt['sales'], 8000.0)
        self.assertEqual(gt['store_sqft'], 1500.0)
        self.assertAlmostEqual(gt['sales_per_sqft'], 5.33, places=2)

    def test_current_soh_joins_by_store_key_despite_label_variants(self):
        import pandas as pd

        sales = pd.DataFrame({
            'zone': ['East', 'East', 'East'],
            'sales_channel': ['Shoppers Stop (SSL)', 'Shoppers Stop (SSL)', 'Pantaloons (PT)'],
            'site_id': ['166', 'SS-RAIPUR-166', 'P033'],
            'site_name': [
                'SHOPPERS STOP LTD - RAIPUR( 166 )',
                'SHOPPERS STOP LTD - RAIPUR( 166 )',
                'PT-KOLKATA-SOUTH CITY (P033)',
            ],
            'site_id_name': [
                '166 - SHOPPERS STOP LTD - RAIPUR( 166 )',
                'SS-RAIPUR-166 - SHOPPERS STOP LTD - RAIPUR( 166 )',
                'PT-South City (P033) - PT-KOLKATA-SOUTH CITY (P033)',
            ],
            'sales': [100.0, 200.0, 500.0],
            'quantity': [1.0, 2.0, 5.0],
            'gross_sales': [100.0, 200.0, 500.0],
            'discount_amount': [0.0, 0.0, 0.0],
            'date': pd.to_datetime(['2026-05-01', '2026-05-02', '2026-05-03']),
            'financial_year': ['FY27', 'FY27', 'FY27'],
        })
        soh = pd.DataFrame({
            'site_id': ['166', 'P033'],
            'site_name': ['SHOPPERS STOP LTD - RAIPUR( 166 )', 'PT-KOLKATA-SOUTH CITY (P033)'],
            'site_id_name': [
                '166 - SHOPPERS STOP LTD - RAIPUR( 166 )',
                'P033 - PT-KOLKATA-SOUTH CITY (P033)',
            ],
            'sales_channel': ['Shoppers Stop (SSL)', 'Pantaloons (PT)'],
            'zone': ['Central', 'East'],
            'barcode': ['B1', 'B2'],
            'current_soh': [40.0, 60.0],
            'mdq': [10.0, 20.0],
            'department': ['Apparel', 'Apparel'],
        })
        req = analyzer.AnalyzerQueryRequest(
            rows=['zone', 'sales_channel', 'site_id_name'],
            values=[
                analyzer.ValueConfig(field='sales', aggregation='sum'),
                analyzer.ValueConfig(field='current_soh', aggregation='sum'),
            ],
            year=2027,
        )
        with patch('api.sales_year_loader.resolve_sales_master_path', return_value=None), \
             patch('api.financial_calendar.calendar_options', return_value={'version': 'test'}), \
             patch.object(analyzer, '_QUERY_RESULT_CACHE', {}), \
             patch.object(analyzer, '_get_prepared_sales_df', return_value=sales), \
             patch.object(analyzer, '_get_prepared_soh_df', return_value=soh), \
             patch('api.page_response_cache.load_page_cache', return_value=None), \
             patch('api.page_response_cache.save_page_cache', return_value=None), \
             patch('api.page_response_cache.rebuilding_page_cache', return_value=True):
            res = analyzer.execute_analyzer_query(req)

        self.assertTrue(res['ok'])
        self.assertEqual(res['grand_total']['metrics']['current_soh'], 100.0)

        def _walk(nodes):
            for n in nodes:
                yield n
                yield from _walk(n.get('children') or [])

        store_nodes = [
            n for n in _walk(res['rows'])
            if str(n.get('key') or '').count('site_id_name:') == 1
        ]
        soh_by_store = {
            (n.get('key') or ''): (n.get('metrics') or {}).get('current_soh')
            for n in store_nodes
        }
        self.assertTrue(any(v == 40.0 for v in soh_by_store.values()), soh_by_store)
        self.assertTrue(any(v == 60.0 for v in soh_by_store.values()), soh_by_store)
        # Duplicate SSL labels must collapse to one store row with SOH attached once.
        ssl_soh_vals = [v for k, v in soh_by_store.items() if '166' in k or 'RAIPUR' in k.upper()]
        self.assertEqual(len(ssl_soh_vals), 1)
        self.assertEqual(ssl_soh_vals[0], 40.0)

    def test_ssl_store_8528_not_classified_as_ebo(self):
        """Bare site_id 8528 contains '528' but must stay SSL (SHOPERS typo + store_type)."""
        import pandas as pd

        raw = pd.DataFrame({
            'site_id': ['8528', '528', 'FGPL-Vadodra'],
            'site_name': [
                'SHOPERS STOP -KOTA (8528)',
                'Fresco EBO Hub',
                'Fresco Global Pvt Ltd-Inorbit Mall Vadodra',
            ],
            'store_type': ['SSL', 'EBO', 'EBO'],
            'store_type_master': ['SSL', 'EBO', 'EBO'],
            'sales': [100.0, 200.0, 300.0],
            'quantity': [1.0, 2.0, 3.0],
            'date': pd.to_datetime(['2026-05-01', '2026-05-02', '2026-05-03']),
        })
        out = analyzer._prepare_analyzer_dataframe(raw)
        by_id = out.set_index('site_id')['sales_channel'].to_dict()
        self.assertEqual(by_id['8528'], 'Shoppers Stop (SSL)')
        self.assertEqual(by_id['528'], 'EBO')
        self.assertEqual(by_id['FGPL-Vadodra'], 'EBO')

    def test_canonicalize_does_not_mix_ssl_label_with_pt_channel(self):
        """SSL manual code 114 and PT sitecode 114 must not Frankenstein after canonicalize."""
        import pandas as pd

        df = pd.DataFrame({
            'site_id': ['114', '114', 'SS-MALAD-114', 'SS-MALAD-114'],
            'site_name': [
                'PT-JORHAT-ABS MALL (P126)',
                'PT-JORHAT-ABS MALL (P126)',
                'SHOPPERS STOP LTD - MALAD( 114 )',
                'SHOPPERS STOP LTD - MALAD( 114 )',
            ],
            'site_id_name': [
                '114 - PT-JORHAT-ABS MALL (P126)',
                '114 - PT-JORHAT-ABS MALL (P126)',
                'SS-MALAD-114 - SHOPPERS STOP LTD - MALAD( 114 )',
                'SS-MALAD-114 - SHOPPERS STOP LTD - MALAD( 114 )',
            ],
            'sales_channel': [
                'Pantaloons (PT)',
                'Pantaloons (PT)',
                'Shoppers Stop (SSL)',
                'Shoppers Stop (SSL)',
            ],
            'channel': ['PT', 'PT', 'SSL', 'SSL'],
            'zone': ['East', 'East', 'West', 'West'],
        })
        out = analyzer._canonicalize_store_dims(df)
        # SSL rows keep SSL channel + SS label
        ssl = out[out['channel'] == 'SSL']
        self.assertTrue((ssl['sales_channel'] == 'Shoppers Stop (SSL)').all())
        self.assertTrue(ssl['site_id_name'].str.contains('SHOPPERS STOP', case=False).all())
        self.assertFalse(ssl['site_id_name'].str.contains('JORHAT', case=False).any())
        # PT rows keep PT channel + PT label
        pt = out[out['channel'] == 'PT']
        self.assertTrue((pt['sales_channel'] == 'Pantaloons (PT)').all())
        self.assertTrue(pt['site_id_name'].str.contains('JORHAT|P126|PT-', case=False, regex=True).all())
        self.assertFalse(pt['site_id_name'].str.contains('SHOPPERS', case=False).any())

    def test_azure_warming_only_on_true_prepared_miss(self):
        """Empty frame after a successful load must not raise warming 503."""
        import pandas as pd

        empty = pd.DataFrame()
        req = analyzer.AnalyzerQueryRequest(rows=['site_id_name'], year=2027)
        with patch.object(analyzer, '_on_azure', return_value=True), \
             patch.object(analyzer, '_QUERY_RESULT_CACHE', {}), \
             patch.object(analyzer, '_get_prepared_sales_df', return_value=empty), \
             patch.object(analyzer, '_read_prepared_sales_directly_from_blob', return_value=None), \
             patch('api.page_response_cache.load_page_cache', return_value=None), \
             patch('api.page_response_cache.save_page_cache', return_value=None), \
             patch('api.page_response_cache.rebuilding_page_cache', return_value=True):
            with self.assertRaises(HTTPException) as caught:
                analyzer.execute_analyzer_query(req)
        self.assertEqual(caught.exception.status_code, 503)
        self.assertIn('warming', str(caught.exception.detail).lower())

    def test_timeline_empty_after_prepared_load_is_empty_success_not_warming(self):
        """Prepared hit + timeline filter empty → ok empty rows (not 503 warming)."""
        import pandas as pd

        req = analyzer.AnalyzerQueryRequest(
            rows=['site_id_name'],
            filters={'timeline_mode': 'fy_year', 'timeline_values': 'FY99'},
            year=2027,
        )
        with patch.object(analyzer, '_on_azure', return_value=True), \
             patch.object(analyzer, '_QUERY_RESULT_CACHE', {}), \
             patch.object(
                 analyzer,
                 '_load_prepared_sales_for_query',
                 return_value=(pd.DataFrame(), True),  # source_ok=True, frame empty
             ), \
             patch('api.page_response_cache.load_page_cache', return_value=None), \
             patch('api.page_response_cache.save_page_cache', return_value=None), \
             patch('api.page_response_cache.rebuilding_page_cache', return_value=True):
            res = analyzer.execute_analyzer_query(req)
        self.assertTrue(res['ok'])
        self.assertEqual(res['rows'], [])

    def test_store_code_name_hierarchy_returns_rows(self):
        import pandas as pd

        sales = pd.DataFrame({
            'zone': ['East', 'West'],
            'sales_channel': ['Shoppers Stop (SSL)', 'Pantaloons (PT)'],
            'site_id': ['166', 'P033'],
            'site_name': ['RAIPUR', 'KOLKATA'],
            'site_id_name': ['166 - RAIPUR', 'P033 - KOLKATA'],
            'sales': [100.0, 200.0],
            'quantity': [1.0, 2.0],
            'gross_sales': [100.0, 200.0],
            'discount_amount': [0.0, 0.0],
            'date': pd.to_datetime(['2026-05-01', '2026-05-02']),
            'financial_year': ['FY27', 'FY27'],
            '_pivot_sk': ['SSL|166', 'PT|P033'],
        })
        req = analyzer.AnalyzerQueryRequest(
            rows=['zone', 'sales_channel', 'site_id_name'],
            values=[analyzer.ValueConfig(field='sales', aggregation='sum')],
            year=2027,
        )
        with patch('api.sales_year_loader.resolve_sales_master_path', return_value=None), \
             patch('api.financial_calendar.calendar_options', return_value={'version': 'test'}), \
             patch.object(analyzer, '_QUERY_RESULT_CACHE', {}), \
             patch.object(analyzer, '_get_prepared_sales_df', return_value=sales), \
             patch('api.page_response_cache.load_page_cache', return_value=None), \
             patch('api.page_response_cache.save_page_cache', return_value=None), \
             patch('api.page_response_cache.rebuilding_page_cache', return_value=True):
            res = analyzer.execute_analyzer_query(req)
        self.assertTrue(res['ok'])
        self.assertEqual(res['grand_total']['metrics']['sales'], 300.0)
        self.assertTrue(len(res['rows']) >= 1)


if __name__ == '__main__':
    unittest.main()

