import unittest
from unittest.mock import patch

import pandas as pd
from starlette.requests import Request

from api.routes import executive_overview as home
from api.routes import sales_dashboard as sales
from api.routes import product_images
from pipeline import item_master


def request(query=''):
    return Request({'type': 'http', 'query_string': query.encode(), 'headers': []})


class HomeFinancialPeriodTests(unittest.TestCase):
    def setUp(self):
        self.frame = pd.DataFrame({
            'date': pd.to_datetime(['2026-03-31', '2026-04-01', '2026-09-23', '2027-04-01']),
            'net_sale_amount': [900, 100, 200, 800],
            'qty': [9, 1, 2, 8], 'sku': ['A', 'A', 'B', 'A'],
            'site_id': ['S'] * 4, 'store_type': ['PT'] * 4,
        })

    def test_default_fy27_excludes_adjacent_fiscal_years(self):
        with patch.object(sales, '_load_sales_for_timeline', return_value=self.frame):
            frame = home._home_sales(request(), 2027)
        self.assertEqual(frame.net_sale_amount.sum(), 300)

    def test_product_and_date_filters_apply_together(self):
        with patch.object(sales, '_load_sales_for_timeline', return_value=self.frame):
            frame = home._home_sales(request('timeline_mode=fy_year&timeline_values=FY27&sku=B'), 2027)
        self.assertEqual(frame.net_sale_amount.sum(), 200)

    def test_trend_includes_september_and_totals_selected_period(self):
        current = self.frame.iloc[[1, 2]]
        prior = current.assign(date=current.date - pd.DateOffset(years=1), net_sale_amount=[50, 100])
        with patch.object(home, '_home_sales', side_effect=[current, prior]), patch.object(sales, '_load_sply_prior_df', return_value=pd.DataFrame()):
            result = home.get_multi_year_trend(request(), 2027)
        self.assertEqual(result['ytd_2026'], 300)
        self.assertEqual(next(m for m in result['months'] if m['month'] == 9)['sales_2026'], 200)
        self.assertEqual(result['yoy_growth_pct'], 100)

    def test_current_master_url_wins_over_old_disk_image(self):
        with patch.object(product_images, '_get_item_master_imagekit_map', return_value={'A': 'https://example.com/new.jpg'}):
            result = product_images.get_product_image('a')
        self.assertEqual(result.headers['location'], 'https://example.com/new.jpg')

    def test_master_without_image_column_does_not_break_dashboard(self):
        with patch.object(item_master, '_find_item_master_path', return_value='mock'), patch.object(item_master, '_path_cache_key', return_value='missing-image-column'), patch.object(item_master, '_read_item_master', return_value=pd.DataFrame({'SKU': ['A']})), patch.object(product_images, '_get_blob_image_map', return_value={}):
            self.assertEqual(item_master.build_sku_image_map(force_refresh=True), {})

    def test_missing_image_does_not_redirect_to_invented_url(self):
        from fastapi import HTTPException
        with patch.object(product_images, '_get_item_master_imagekit_map', return_value={}), patch.object(product_images, '_get_blob_image_map', return_value={}):
            with self.assertRaises(HTTPException) as caught:
                product_images.get_product_image('MISSING_TEST_SKU_987654')
        self.assertEqual(caught.exception.status_code, 404)

    def test_master_url_overrides_raw_blob_and_refreshes_on_source_change(self):
        master = pd.DataFrame({'SKU CODE': ['A'], 'Image URL': ['https://example.com/current.jpg']})
        with patch.object(item_master, '_find_item_master_path', return_value='mock'), patch.object(item_master, '_path_cache_key', return_value='new-version'), patch.object(item_master, '_read_item_master', return_value=master), patch.object(product_images, '_get_blob_image_map', return_value={'A': 'Raw_Files/ItemImage/A.jpg'}), patch.object(item_master, '_SKU_IMAGE_MAP_CACHE', {'A': 'https://example.com/old.jpg'}), patch.object(item_master, '_SKU_IMAGE_MAP_CACHE_KEY', 'old-version'):
            result = item_master.build_sku_image_map()
        self.assertEqual(result['A'], 'https://example.com/current.jpg')


if __name__ == '__main__':
    unittest.main()
