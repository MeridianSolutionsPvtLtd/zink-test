import pandas as pd
from api.sales_year_loader import load_sales_for_year

for yr in [2025, 2026, 2027]:
    df = load_sales_for_year(yr)
    print(f"\n================ YEAR {yr} (Total Rows: {len(df):,}) ================")
    # Unpack categoricals
    for c in df.columns:
        if isinstance(df[c].dtype, pd.CategoricalDtype):
            df[c] = df[c].astype(str)

    site_str = df['site_id'].fillna('').astype(str).str.upper()
    name_str = df['site_name'].fillna('').astype(str).str.upper()
    cust_str = df.get('customer_name', pd.Series('', index=df.index)).fillna('').astype(str).str.upper()
    comb = site_str + " " + name_str + " " + cust_str

    is_ssl = comb.str.contains(r"SHOPPER|HOPPER|^SS[- ]|\bSSL\b", regex=True, na=False)
    is_pt = comb.str.contains(r"PANTALOON|^PT[- ]|^PW[- ]|\bPT\b|^P\d|^W\d|ADITYA BIRLA", regex=True, na=False) & ~is_ssl
    is_centro = comb.str.contains(r"CENTRO|^RRL[- ]|^TC3|^T8|RELIANCE", regex=True, na=False) & ~is_ssl & ~is_pt
    is_iconic = comb.str.contains(r"ICONIC", regex=True, na=False) & ~is_ssl & ~is_pt
    is_ebo = comb.str.contains(r"EBO|FRESCO|^FGPL|528|964|972", regex=True, na=False) & ~is_ssl & ~is_pt

    unmapped = ~(is_ssl | is_pt | is_centro | is_iconic | is_ebo)
    print(f"SSL rows:    {is_ssl.sum():,}")
    print(f"PT rows:     {is_pt.sum():,}")
    print(f"Centro rows: {is_centro.sum():,}")
    print(f"Iconic rows: {is_iconic.sum():,}")
    print(f"EBO rows:    {is_ebo.sum():,}")
    print(f"Unmapped:    {unmapped.sum():,}")
    if unmapped.any():
        print("Sample unmapped stores:")
        print(df.loc[unmapped, ['site_id', 'site_name', 'customer_name']].drop_duplicates().head(10))
