from pipeline.customer_master import load_customer_master, enrich_sales_with_customer_master
import pandas as pd

cm = load_customer_master(retail_only=False)

test_df = pd.DataFrame([
    {"site_id": "120", "site_name": "SHOPPERS STOP LTD - SHIPRA( 120 )"},
    {"site_id": "SS-SHIPRA MALL-120", "site_name": "SHOPPERS STOP LTD - SHIPRA( 120 )"},
    {"site_id": "115", "site_name": "SHOPPERS STOP LTD - KOLKATA 2( 115 )"},
    {"site_id": "110", "site_name": "SHOPPERS STOP LTD - GURGAON( 110 )"},
    {"site_id": "P038", "site_name": "PT-GHAZIABAD-SHIPRA MALL(P038)"},
    {"site_id": "PT-Shipra Mall (P038)", "site_name": "PT-GHAZIABAD-SHIPRA MALL(P038)"},
    {"site_id": "120", "site_name": ""},
    {"site_id": "115", "site_name": ""},
    {"site_id": "110", "site_name": ""},
    {"site_id": "101", "site_name": ""},
])

enriched = enrich_sales_with_customer_master(test_df, site_col="site_id")
print("Enrichment results:")
for _, r in enriched.iterrows():
    print(f"site_id: {r['site_id']:22} | site_name: {r['site_name'][:35]:35} | channel: {r.get('channel')} | cust: {r.get('customer_name')[:25]}")
