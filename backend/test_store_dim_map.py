import pandas as pd
from api.sales_year_loader import load_sales_for_year
from api.historical_movement import site_key_series

sales = load_sales_for_year(2027)

# Build authoritative store dimension map from Sales + Customer Master
store_dims = (
    sales[['site_id', 'site_name', 'channel', 'sales_channel', 'zone']]
    .dropna(subset=['site_id'])
    .copy()
)
store_dims['_sk'] = site_key_series(store_dims['site_id'])
store_dims['site_id_name'] = store_dims['site_id'].astype(str).str.strip() + " - " + store_dims['site_name'].astype(str).str.strip()

# Deduplicate by _sk (prefer non-numeric site_id like SS-SHIPRA MALL-120 over 120)
store_dims = store_dims.sort_values(by='site_id', key=lambda s: s.str.len(), ascending=False)
store_map = store_dims.drop_duplicates(subset=['_sk']).set_index('_sk')

print(f"Total unique mapped stores: {len(store_map)}")
print("Sample mappings from _sk:")
for sk in ['120', '110', '115', 'P001', 'P038', 'P147', 'TC3M', '8005', '528']:
    if sk in store_map.index:
        r = store_map.loc[sk]
        print(f"  {sk:6} -> {r['site_id_name']} | ch={r['sales_channel']} | zone={r['zone']}")
    else:
        print(f"  {sk:6} -> NOT FOUND")
