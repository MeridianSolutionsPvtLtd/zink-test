import pandas as pd
from api.sales_year_loader import load_sales_for_year

for yr in [2025, 2026]:
    df = load_sales_for_year(yr)
    m = df[df['site_id'].astype(str).str.contains('SS-SHIPRA MALL-120')]
    print(f"Year {yr} SS-SHIPRA: channel={m['channel'].iloc[0] if 'channel' in m.columns else 'NO COL'} | customer_name={m['customer_name'].iloc[0] if 'customer_name' in m.columns else 'NO COL'}")
