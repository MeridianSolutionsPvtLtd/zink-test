import pandas as pd

df = pd.read_excel(r'c:\Users\ChandanSoni\Downloads\merged_backend_test\Sales_Pivot_zone_sales_channel_site_id_name_2026-10-07.xlsx')

print("All rows in exported file where Store (Code - Name) starts with SS but Channel is NOT Shoppers Stop:")
bad_ss = df[df['Store (Code - Name)'].astype(str).str.contains(r'^SS[- ]|SHOPPER', regex=True, case=False) & (df['Channel / Format'] != 'Shoppers Stop (SSL)')]
print(f"Total bad SS rows: {len(bad_ss)}")
for _, r in bad_ss.iterrows():
    print(f"  Channel: {r['Channel / Format']:20} | Store: {r['Store (Code - Name)']}")

print("\nAll rows where Store (Code - Name) starts with PT but Channel is NOT Pantaloons:")
bad_pt = df[df['Store (Code - Name)'].astype(str).str.contains(r'^PT[- ]|PANTALOON', regex=True, case=False) & (df['Channel / Format'] != 'Pantaloons (PT)')]
print(f"Total bad PT rows: {len(bad_pt)}")
for _, r in bad_pt.iterrows():
    print(f"  Channel: {r['Channel / Format']:20} | Store: {r['Store (Code - Name)']}")

print("\nAll rows where Store (Code - Name) starts with RRL or T8 but Channel is NOT Centro:")
bad_centro = df[df['Store (Code - Name)'].astype(str).str.contains(r'^RRL[- ]|^TC3|^T8', regex=True, case=False) & (df['Channel / Format'] != 'Centro')]
print(f"Total bad Centro rows: {len(bad_centro)}")
for _, r in bad_centro.iterrows():
    print(f"  Channel: {r['Channel / Format']:20} | Store: {r['Store (Code - Name)']}")
