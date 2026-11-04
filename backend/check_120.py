import pandas as pd
df = pd.read_excel(r'c:\Users\ChandanSoni\Downloads\merged_backend_test\Sales_Pivot_zone_sales_channel_site_id_name_2026-10-07.xlsx')
matches = df[df['Store (Code - Name)'].astype(str).str.contains('120', na=False)]
print(matches[['Zone', 'Channel / Format', 'Store (Code - Name)', 'Sales (Net GSV)', 'Current SOH']])
