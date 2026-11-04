import re
import pandas as pd

def sanitize_channels(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    out = df.copy()
    site_str = (out["site_id"].astype(str) if "site_id" in out.columns else pd.Series("", index=out.index)).str.upper()
    name_str = (out["site_name"].astype(str) if "site_name" in out.columns else pd.Series("", index=out.index)).str.upper()
    comb = site_str + " " + name_str

    is_ssl = comb.str.contains(r"SHOPPER|HOPPER|^SS[- ]|\bSSL\b", regex=True, na=False)
    is_pt = comb.str.contains(r"PANTALOON|^PT[- ]|^PW[- ]|\bPT\b", regex=True, na=False) & ~is_ssl
    is_centro = comb.str.contains(r"CENTRO|^RRL[- ]|^TC3|^T8", regex=True, na=False) & ~is_ssl & ~is_pt
    is_iconic = comb.str.contains(r"ICONIC", regex=True, na=False) & ~is_ssl & ~is_pt
    is_ebo = comb.str.contains(r"EBO|FRESCO|^FGPL", regex=True, na=False) & ~is_ssl & ~is_pt

    _CHANNEL_MAP = {
        "PT": "Pantaloons (PT)",
        "SSL": "Shoppers Stop (SSL)",
        "CENTRO": "Centro",
        "ICONIC": "Iconic",
        "EBO": "EBO",
    }

    if "channel" in out.columns:
        out.loc[is_ssl, "channel"] = "SSL"
        out.loc[is_pt, "channel"] = "PT"
        out.loc[is_centro, "channel"] = "CENTRO"
        out.loc[is_iconic, "channel"] = "ICONIC"
        out.loc[is_ebo, "channel"] = "EBO"

    if "sales_channel" not in out.columns:
        out["sales_channel"] = out.get("channel", pd.Series("", index=out.index)).astype(str).str.strip().map(
            lambda x: _CHANNEL_MAP.get(x.upper(), x)
        )
    
    out.loc[is_ssl, "sales_channel"] = "Shoppers Stop (SSL)"
    out.loc[is_pt, "sales_channel"] = "Pantaloons (PT)"
    out.loc[is_centro, "sales_channel"] = "Centro"
    out.loc[is_iconic, "sales_channel"] = "Iconic"
    out.loc[is_ebo, "sales_channel"] = "EBO"

    if "customer_name" in out.columns:
        bad_ssl_cust = (
            out["customer_name"].isna()
            | out["customer_name"].astype(str).str.strip().isin({"", "nan", "None", "Jmdee", "WIP Warehouse", "IBT Warehouse"})
            | out["customer_name"].astype(str).str.contains("Aditya Birla|PANTALOON", case=False, na=False)
        )
        out.loc[is_ssl & bad_ssl_cust, "customer_name"] = "Shoppers Stop Ltd"
        out.loc[is_pt & out["customer_name"].astype(str).str.strip().isin({"", "nan", "None"}), "customer_name"] = "M/S Aditya Birla Fashion and Retail Ltd"

    return out

from api.sales_year_loader import load_sales_for_year
for yr in [2025, 2026, 2027]:
    raw_df = load_sales_for_year(yr)
    clean_df = sanitize_channels(raw_df)
    bad_ss = clean_df[clean_df['site_id'].astype(str).str.contains(r'^SS[- ]|SHOPPER', regex=True, case=False) & (clean_df['sales_channel'] != 'Shoppers Stop (SSL)')]
    bad_pt = clean_df[clean_df['site_id'].astype(str).str.contains(r'^PT[- ]|PANTALOON', regex=True, case=False) & (clean_df['sales_channel'] != 'Pantaloons (PT)')]
    print(f"Year {yr} sanitized: bad_ss={len(bad_ss)}, bad_pt={len(bad_pt)}")
    print(f"  sales_channel counts: {clean_df['sales_channel'].value_counts().to_dict()}")
