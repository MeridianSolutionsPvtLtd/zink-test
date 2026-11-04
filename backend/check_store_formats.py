import pandas as pd
from pipeline.customer_master import load_customer_master
from api.sales_year_loader import load_sales_for_year

cm = load_customer_master()
sales = load_sales_for_year(2027)

# Check unique site_id in sales
print("Unique site_id count in sales:", sales['site_id'].nunique())
print("Sample sales site_id:")
print(sales['site_id'].drop_duplicates().head(10).tolist())

print("\nSample sales site_name:")
print(sales['site_name'].drop_duplicates().head(10).tolist())

print("\nSample sales site_name_master (from Customer Master):")
print(sales['site_name_master'].drop_duplicates().head(10).tolist())
