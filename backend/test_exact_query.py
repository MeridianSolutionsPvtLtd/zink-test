import json
from dotenv import load_dotenv
load_dotenv()

from api.routes.sales_analyzer import _get_prepared_sales_df, _prepare_analyzer_dataframe

df = _get_prepared_sales_df(2027)
print("Total sales rows:", len(df))

# Check rows where site_id or site_name starts with SS
ss_rows = df[df['site_id'].astype(str).str.contains('^SS[- ]|SHOPPER', case=False) | df['site_name'].astype(str).str.contains('^SS[- ]|SHOPPER', case=False)]
print("Total SS rows:", len(ss_rows))
print("SS sales_channel distribution in df:")
print(ss_rows['sales_channel'].value_counts())

# Specifically check the 5 stores from user's file:
test_stores = ['SS-MALAD-114', 'SS-KOLKATA 2-115', 'SS-SHIPRA MALL-120', 'SS-BENNARGHATTA-116', 'SS-KOLHAPUR-253']
print("\nSpecific test stores:")
for st in test_stores:
    m = df[df['site_id'].astype(str).str.contains(st, case=False)]
    if not m.empty:
        r = m.iloc[0]
        print(f"{st:22} -> channel: {r.get('channel')} | sales_channel: {r.get('sales_channel')} | customer: {r.get('customer_name')}")
    else:
        print(f"{st:22} -> NOT FOUND")
