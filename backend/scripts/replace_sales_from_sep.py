"""
Replace Sales data from 2026-09-01 onwards using:
  '3rd Party Sales Se'26 to till date.xlsx'
and upload the updated files back to Azure Blob Storage (container: zinc-data):
  1) input/Sales (PT, SSL, Centro, Iconic, EBO)/current/SSI_PT_CENTRO_ICONIC_SALES.csv
  2) input/Sales-Master (PT, SSL, Centro, Iconic, EBO)/current/SSI_PT_CENTRO_ICONIC_EBO_SALES_MASTER.csv
  3) input/Sales-Master (PT, SSL, Centro, Iconic, EBO)/2026/current/SSI_PT_CENTRO_ICONIC_EBO_SALES_MASTER.csv
And update local mirrors + sidecar date bounds.
"""
import io
import json
import os
import shutil
import sys
import time
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

# Set paths
BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = BACKEND_DIR.parents[1]
EXCEL_PATH = ROOT_DIR / "3rd Party Sales Se'26 to till date.xlsx"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

load_dotenv(BACKEND_DIR / ".env")

from azure.storage.blob import BlobServiceClient
from api.blob_store import BlobJsonStore, upload_local_file_with_retry

CANONICAL_COLUMNS = [
    "Site", "Zone", "Site Type", "Site Code", "Invoice No", "Invoice Date",
    "Type", "Channel Name", "Barcode", "Division", "Secion", "Department",
    "Cat3", "Cat6", "Cat5", "RSP", "Qty", "Rate", "CGST Rate", "SGST Rate",
    "IGST Rate", "CGST Amt", "SGST Amt", "IGST Amt", "Tax Charge Amt",
    "Taxable Amt", "Gross Amt", "Discount Amt", "Item Discount", "Bill Discount",
    "Item Net Amt", "Cost Rate", "Cost Amt"
]

def load_and_transform_excel(excel_path: Path, daily_reference: pd.DataFrame) -> pd.DataFrame:
    print(f"\n[1/5] Loading Excel: {excel_path.name} ...", flush=True)
    df_raw = pd.read_excel(excel_path, skiprows=3)
    print(f"      Raw rows: {len(df_raw)}", flush=True)
    
    # Drop total/summary row where ENTRY_DATE is NaN
    df = df_raw.dropna(subset=["ENTRY_DATE"]).copy()
    print(f"      Valid rows (non-null date): {len(df)}", flush=True)
    
    # Parse dates
    df["ENTRY_DATE"] = pd.to_datetime(df["ENTRY_DATE"], errors="coerce")
    df = df[df["ENTRY_DATE"].notna()].copy()
    
    # Filter >= 2026-09-01
    df = df[df["ENTRY_DATE"] >= "2026-09-01"].copy()
    print(f"      Rows >= 2026-09-01: {len(df)} (date range: {df['ENTRY_DATE'].min().date()} to {df['ENTRY_DATE'].max().date()})", flush=True)
    
    # Build reference lookup tables for metadata
    print("      Building barcode/site lookups from reference data...", flush=True)
    ref_clean = daily_reference.copy()
    site_map = ref_clean.dropna(subset=["Site", "Site Code"]).drop_duplicates("Site").set_index("Site")["Site Code"].to_dict()
    
    # Barcode lookups
    b_ref = ref_clean.dropna(subset=["Barcode"])
    b_div = b_ref.dropna(subset=["Division"]).drop_duplicates("Barcode").set_index("Barcode")["Division"].to_dict()
    b_cost = b_ref.dropna(subset=["Cost Rate"]).drop_duplicates("Barcode").set_index("Barcode")["Cost Rate"].to_dict()
    
    # Cat3 lookups fallback
    cat3_ref = ref_clean.dropna(subset=["Cat3"])
    cat3_div = cat3_ref.dropna(subset=["Division"]).drop_duplicates("Cat3").set_index("Cat3")["Division"].to_dict()
    cat3_cost = cat3_ref.dropna(subset=["Cost Rate"]).drop_duplicates("Cat3").set_index("Cat3")["Cost Rate"].to_dict()
    
    # Transform into 33 canonical columns
    out = pd.DataFrame(index=df.index)
    
    # Site & Store info
    out["Site"] = df["TRANSACTION_SITE"].astype(str).str.strip()
    out["Zone"] = df["ZONE"].astype(str).str.strip()
    out["Site Type"] = df["SITE TYPE"].astype(str).str.strip()
    
    # Site Code: prefer mapped site code from existing database, fallback to raw STORE CODE
    mapped_site_code = out["Site"].map(site_map)
    out["Site Code"] = mapped_site_code.fillna(df["STORE CODE"]).astype(str)
    
    # Invoice info
    out["Invoice No"] = df["ENTRY_NO"].astype(str).str.strip()
    out["Invoice Date"] = df["ENTRY_DATE"].dt.strftime("%Y-%m-%d")
    out["Type"] = "Store"
    out["Channel Name"] = df["CUSTOMER_NAME"].astype(str).str.strip()
    
    # Item & Classification
    out["Barcode"] = pd.to_numeric(df["BARCODE"], errors="coerce")
    out["Cat3"] = df["CATEGORY3"].astype(str).str.strip()
    out["Cat6"] = df["CATEGORY6"].astype(str).str.strip()
    out["Cat5"] = df["CATEGORY5"].astype(str).str.strip()
    out["Secion"] = df["Section"].astype(str).str.strip()
    out["Department"] = df["Department"].astype(str).str.strip()
    
    # Division
    div_mapped = out["Barcode"].map(b_div).fillna(out["Cat3"].map(cat3_div)).fillna("WOMENS APPS")
    out["Division"] = div_mapped
    
    # Quantities & Prices
    qty = pd.to_numeric(df["QUANTITY"], errors="coerce").fillna(1.0)
    rsp = pd.to_numeric(df["RSP"], errors="coerce").fillna(0.0)
    out["RSP"] = rsp
    out["Qty"] = qty
    out["Rate"] = rsp
    
    # Taxes
    out["CGST Rate"] = pd.to_numeric(df["CGST_RATE"], errors="coerce").fillna(0.0)
    out["SGST Rate"] = pd.to_numeric(df["SGST_RATE"], errors="coerce").fillna(0.0)
    out["IGST Rate"] = pd.to_numeric(df["IGST_RATE"], errors="coerce").fillna(0.0)
    out["CGST Amt"] = pd.to_numeric(df["CGST_AMOUNT"], errors="coerce").fillna(0.0)
    out["SGST Amt"] = pd.to_numeric(df["SGST_AMOUNT"], errors="coerce").fillna(0.0)
    out["IGST Amt"] = pd.to_numeric(df["IGST_AMOUNT"], errors="coerce").fillna(0.0)
    out["Tax Charge Amt"] = pd.to_numeric(df["TAX_AMOUNT"], errors="coerce").fillna(0.0)
    out["Taxable Amt"] = pd.to_numeric(df["TAXABLE_AMOUNT"], errors="coerce").fillna(0.0)
    
    # Amounts & Discounts
    item_disc = pd.to_numeric(df["ITEM DISCOUNT_AMOUNT"], errors="coerce").fillna(0.0)
    bill_disc = pd.to_numeric(df["BILL DISCOUNT AMT"], errors="coerce").fillna(0.0)
    tot_disc = item_disc + bill_disc
    net_amt = pd.to_numeric(df["NET_AMOUNT"], errors="coerce").fillna(0.0)
    
    out["Gross Amt"] = rsp * qty
    out["Discount Amt"] = tot_disc
    out["Item Discount"] = item_disc
    out["Bill Discount"] = bill_disc
    out["Item Net Amt"] = net_amt
    
    # Cost
    cost_rate = out["Barcode"].map(b_cost).fillna(out["Cat3"].map(cat3_cost))
    out["Cost Rate"] = pd.to_numeric(cost_rate, errors="coerce")
    out["Cost Amt"] = (out["Cost Rate"] * qty).round(2)
    
    # Ensure exact column ordering
    out = out[CANONICAL_COLUMNS].copy()
    print(f"      Final transformed shape: {out.shape}", flush=True)
    return out

def replace_and_upload():
    store = BlobJsonStore()
    container = store.container
    
    local_input = BACKEND_DIR / "pipeline" / "input_files"
    daily_file = local_input / "Sales (PT, SSL, Centro, Iconic, EBO)" / "current" / "SSI_PT_CENTRO_ICONIC_SALES.csv"
    master_file = local_input / "Sales-Master (PT, SSL, Centro, Iconic, EBO)" / "current" / "SSI_PT_CENTRO_ICONIC_EBO_SALES_MASTER.csv"
    year2026_file = local_input / "Sales-Master (PT, SSL, Centro, Iconic, EBO)" / "2026" / "current" / "SSI_PT_CENTRO_ICONIC_EBO_SALES_MASTER.csv"
    flat_master = local_input / "SSI_PT_CENTRO_ICONIC_EBO_SALES_MASTER.csv"
    flat_daily = local_input / "SSI_PT_CENTRO_ICONIC_SALES.csv"

    # 1. Load reference daily for lookups and keeping pre-Sept data
    print("[1/5] Reading existing daily sales file...", flush=True)
    daily_df = pd.read_csv(daily_file, low_memory=False)
    daily_df["_dt"] = pd.to_datetime(daily_df["Invoice Date"], errors="coerce")
    
    # Load and transform Excel
    new_sep_df = load_and_transform_excel(EXCEL_PATH, daily_df)
    new_sep_df["_dt"] = pd.to_datetime(new_sep_df["Invoice Date"], errors="coerce")
    
    # 2. Merge for Daily: input/Sales (PT, SSL, Centro, Iconic, EBO)/current/SSI_PT_CENTRO_ICONIC_SALES.csv
    print("\n[2/5] Preparing updated Daily sales...", flush=True)
    daily_pre_sep = daily_df[daily_df["_dt"] < "2026-09-01"].copy()
    print(f"      Daily pre-Sept rows: {len(daily_pre_sep)}", flush=True)
    updated_daily = pd.concat([daily_pre_sep.drop(columns=["_dt"]), new_sep_df.drop(columns=["_dt"])], ignore_index=True)
    print(f"      Updated Daily total rows: {len(updated_daily)}", flush=True)
    
    # Save Daily locally
    tmp_daily = BACKEND_DIR / "data" / "blob_cache" / "_updated_daily_sales.csv"
    tmp_daily.parent.mkdir(parents=True, exist_ok=True)
    updated_daily.to_csv(tmp_daily, index=False)
    shutil.copy2(tmp_daily, daily_file)
    shutil.copy2(tmp_daily, flat_daily)
    print(f"      Saved local copies of Daily sales", flush=True)
    
    # 3. Merge for Master: input/Sales-Master (PT, SSL, Centro, Iconic, EBO)/current/SSI_PT_CENTRO_ICONIC_EBO_SALES_MASTER.csv
    print("\n[3/5] Preparing updated Sales-Master...", flush=True)
    master_df = pd.read_csv(master_file, low_memory=False)
    master_df["_dt"] = pd.to_datetime(master_df["Invoice Date"], errors="coerce")
    master_pre_sep = master_df[master_df["_dt"] < "2026-09-01"].copy()
    print(f"      Master pre-Sept rows: {len(master_pre_sep)}", flush=True)
    updated_master = pd.concat([master_pre_sep.drop(columns=["_dt"]), new_sep_df.drop(columns=["_dt"])], ignore_index=True)
    print(f"      Updated Master total rows: {len(updated_master)}", flush=True)
    
    # Save Master locally
    tmp_master = BACKEND_DIR / "data" / "blob_cache" / "_updated_master_sales.csv"
    updated_master.to_csv(tmp_master, index=False)
    shutil.copy2(tmp_master, master_file)
    shutil.copy2(tmp_master, flat_master)
    print(f"      Saved local copies of Master sales", flush=True)
    
    # 4. Prepare Year 2026 folder slice: input/Sales-Master (PT, SSL, Centro, Iconic, EBO)/2026/current/SSI_PT_CENTRO_ICONIC_EBO_SALES_MASTER.csv
    print("\n[4/5] Preparing updated Year 2026 slice...", flush=True)
    updated_master["_dt"] = pd.to_datetime(updated_master["Invoice Date"], errors="coerce")
    y2026 = updated_master[(updated_master["_dt"] >= "2026-01-01") & (updated_master["_dt"] <= "2026-12-31")].drop(columns=["_dt"]).copy()
    print(f"      Updated Year 2026 total rows: {len(y2026)}", flush=True)
    
    tmp_y2026 = BACKEND_DIR / "data" / "blob_cache" / "_updated_y2026_sales.csv"
    y2026.to_csv(tmp_y2026, index=False)
    year2026_file.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(tmp_y2026, year2026_file)
    print(f"      Saved local copies of Year 2026 sales", flush=True)
    
    # 5. Upload files to Azure Blob Storage
    print("\n[5/5] Uploading updated CSV files and date bounds to Azure Blob...", flush=True)
    
    uploads = [
        (
            "input/Sales (PT, SSL, Centro, Iconic, EBO)/current/SSI_PT_CENTRO_ICONIC_SALES.csv",
            daily_file,
            "input/Sales (PT, SSL, Centro, Iconic, EBO)/current/SSI_PT_CENTRO_ICONIC_SALES.date_bounds.json",
            "2025-09-18",
            "2026-10-15",
            len(updated_daily),
        ),
        (
            "input/Sales-Master (PT, SSL, Centro, Iconic, EBO)/current/SSI_PT_CENTRO_ICONIC_EBO_SALES_MASTER.csv",
            master_file,
            "input/Sales-Master (PT, SSL, Centro, Iconic, EBO)/current/SSI_PT_CENTRO_ICONIC_EBO_SALES_MASTER.date_bounds.json",
            "2024-04-01",
            "2026-10-15",
            len(updated_master),
        ),
        (
            "input/Sales-Master (PT, SSL, Centro, Iconic, EBO)/2026/current/SSI_PT_CENTRO_ICONIC_EBO_SALES_MASTER.csv",
            year2026_file,
            "input/Sales-Master (PT, SSL, Centro, Iconic, EBO)/2026/current/SSI_PT_CENTRO_ICONIC_EBO_SALES_MASTER.date_bounds.json",
            "2026-01-01",
            "2026-10-15",
            len(y2026),
        ),
    ]
    
    for blob_csv, local_path, blob_bounds, min_d, max_d, rows in uploads:
        print(f"\n---> Uploading {blob_csv} ({local_path.stat().st_size / 1e6:.1f} MB)...", flush=True)
        upload_local_file_with_retry(container, blob_name=blob_csv, path=local_path, content_type="text/csv")
        print(f"     Uploaded {blob_csv} OK", flush=True)
        
        # Upload date bounds sidecar
        bounds_payload = {
            "date_column": "Invoice Date",
            "min_str": min_d,
            "max_str": max_d,
            "rows_with_date": rows,
            "total_rows": rows,
            "assumed_covered_through": max_d,
            "merged_file_dates": [f"2026-09-01..{max_d}"],
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
            "catchup_from": "3rd Party Sales Se'26 to till date.xlsx",
        }
        bounds_bytes = json.dumps(bounds_payload, indent=2).encode("utf-8")
        container.upload_blob(blob_bounds, bounds_bytes, overwrite=True, content_type="application/json")
        print(f"     Uploaded {blob_bounds} OK (range: {min_d} to {max_d}, rows: {rows:,})", flush=True)

    # Invalidate stale API parquet caches so dashboards show fresh numbers immediately
    cache_dir = BACKEND_DIR / "data" / "api_cache"
    if cache_dir.exists():
        deleted = 0
        for p in cache_dir.glob("sales_year_*.parquet"):
            try:
                p.unlink()
                deleted += 1
            except Exception:
                pass
        print(f"\nCleared {deleted} stale parquet cache files from {cache_dir.name}", flush=True)

    print("\nSUCCESS! All Sales and Sales-Master blob files have been updated and re-uploaded successfully!", flush=True)

if __name__ == "__main__":
    replace_and_upload()
