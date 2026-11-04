"""
Add first_transfer_date to weekly file.

first_transfer_date = per (site_id + barcode) ka MIN(Transfer_Date), COMBINED_TRANSFER.xlsx se.
(EBO already COMBINED_TRANSFER mein hai — ebo_data / retail_input sheets use nahi karte.)

5w / panel columns weekly file mein already hain (2.weekly_aggregate.py se), isliye unhe
dobara compute nahi karte — sirf first_transfer_date add karte hain.

Input : output_files/weekly_aggregated_sales_new.csv
        output_files/COMBINED_TRANSFER.xlsx  (raw warehouse copy OK — normalized on load)
Output: output_files/weekly_aggregated_sales_new_5w_avg_sold_with_first_transfer.csv
"""

from pathlib import Path

import pandas as pd

from pipeline_inputs import load_combined_transfer


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output_files"

INPUT_CSV = OUTPUT_DIR / "weekly_aggregated_sales_new.csv"
OUTPUT_CSV = OUTPUT_DIR / "weekly_aggregated_sales_new_5w_avg_sold_with_first_transfer.csv"


def _norm_site(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().str.upper().str.replace(r"\.0$", "", regex=True)


def _norm_barcode(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().str.upper().str.replace(r"\.0$", "", regex=True)


def main() -> None:
    if not INPUT_CSV.exists():
        raise FileNotFoundError(f"Input file not found: {INPUT_CSV}")

    print("[1/3] reading weekly CSV ...", flush=True)
    df = pd.read_csv(INPUT_CSV, low_memory=False)
    df.columns = df.columns.str.strip()
    if "site_id" not in df.columns or "barcode" not in df.columns:
        raise ValueError("weekly CSV must contain 'site_id' and 'barcode'.")

    df["_k_site"] = _norm_site(df["site_id"])
    df["_k_bc"] = _norm_barcode(df["barcode"])

    print("[2/3] first_transfer_date from COMBINED_TRANSFER ...", flush=True)
    # Copy-mode COMBINED_TRANSFER may still have warehouse columns (SITE SHORT NAME, etc.)
    transfer = load_combined_transfer()
    for col in ("site_id", "Barcode", "Transfer_Date"):
        if col not in transfer.columns:
            raise ValueError(
                f"COMBINED_TRANSFER missing '{col}' after normalize. "
                f"Found: {list(transfer.columns)[:20]}"
            )

    first_transfer = pd.DataFrame(
        {
            "_k_site": _norm_site(transfer["site_id"]),
            "_k_bc": _norm_barcode(transfer["Barcode"]),
            "_dt": pd.to_datetime(transfer["Transfer_Date"], errors="coerce"),
        }
    )
    first_transfer = (
        first_transfer.dropna(subset=["_dt"])
        .groupby(["_k_site", "_k_bc"], as_index=False)["_dt"]
        .min()
        .rename(columns={"_dt": "first_transfer_date"})
    )

    df = df.drop(columns=["first_transfer_date"], errors="ignore")
    df = df.merge(first_transfer, on=["_k_site", "_k_bc"], how="left")
    df = df.drop(columns=["_k_site", "_k_bc"])

    print("[3/3] writing CSV + XLSX ...", flush=True)
    try:
        from pipeline_inputs import save_output_pair
    except ImportError:
        from pipeline.pipeline_inputs import save_output_pair  # type: ignore
    save_output_pair(df, OUTPUT_CSV)

    matched = df["first_transfer_date"].notna().sum()
    print(f"Done. Rows: {len(df):,}", flush=True)
    print(f"  first_transfer_date filled: {matched:,}", flush=True)
    print(f"  Saved: {OUTPUT_CSV}", flush=True)


if __name__ == "__main__":
    main()
