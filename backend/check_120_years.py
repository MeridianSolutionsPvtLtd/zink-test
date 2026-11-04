import pandas as pd
from api.sales_year_loader import load_sales_for_year

for yr in [2025, 2026, 2027]:
    df = load_sales_for_year(yr)
    print(f"\n=== Year {yr} (cols count: {len(df.columns)}) ===")
    cols = [c for c in ['site_id', 'site_name', 'channel', 'store_type', 'zone'] if c in df.columns]
    m = df[df['site_id'].astype(str).str.contains('120') | df['site_name'].astype(str).str.contains('120')]
    print(f"Matching 120 count: {len(m)}")
    if not m.empty:
        print(m[cols].drop_duplicates())
