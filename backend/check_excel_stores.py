import pandas as pd
df = pd.read_excel(r'c:\Users\ChandanSoni\Downloads\merged_backend_test\Sales_Pivot_zone_sales_channel_site_id_name_2026-10-07.xlsx')
print("Sample Store (Code - Name) from exported file:")
for v in df['Store (Code - Name)'].dropna().head(20):
    print("  *", v)
