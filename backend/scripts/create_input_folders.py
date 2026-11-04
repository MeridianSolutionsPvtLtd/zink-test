"""
Create channel/category subfolders under Azure Blob input/
with incoming / current / history staging dirs.

Usage (from meridianai-stockpulse-backend/):
  python scripts/create_input_folders.py
  python scripts/create_input_folders.py --dry-run
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

INPUT_SUBFOLDERS = [
    "Sales (PT, SSL, Centro, Iconic, EBO)",
    "Pantaloons - SOH",
    "Shoppers Stop - SOH",
    "Iconic - SOH",
    "EBO Sales",
    "EBO - SOH",
    "Centro - SOH",
    "Warehouse SOH",
    "Transfer - EBO",
    "Transfer - (PT, SSL, Centro, Iconic)",
    "Item Master",
    "Customer Master",
    "Store Grade List",
    "Top Sellers List",
]

STAGES = ("incoming", "current", "history")


def main() -> int:
    p = argparse.ArgumentParser(description="Create Blob input/ category staging folders")
    p.add_argument("--dry-run", action="store_true", help="Print only, do not upload")
    args = p.parse_args()

    conn = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "").strip()
    if not conn:
        print("ERROR: Set AZURE_STORAGE_CONNECTION_STRING in .env")
        return 1

    container_name = os.environ.get("AZURE_STORAGE_CONTAINER", "zinc-data").strip() or "zinc-data"
    input_prefix = (os.environ.get("AZURE_BLOB_INPUT_PREFIX", "input/") or "input/").strip().replace("\\", "/")
    if input_prefix and not input_prefix.endswith("/"):
        input_prefix += "/"

    from azure.storage.blob import BlobServiceClient, ContentSettings

    svc = BlobServiceClient.from_connection_string(conn)
    cc = svc.get_container_client(container_name)

    created = []
    skipped = []
    for name in INPUT_SUBFOLDERS:
        markers = [f"{input_prefix}{name}/.keep"]
        for stage in STAGES:
            markers.append(f"{input_prefix}{name}/{stage}/.keep")
        for blob_name in markers:
            folder_label = blob_name.rsplit("/.keep", 1)[0] + "/"
            if args.dry_run:
                print(f"[dry-run] would create: {folder_label}")
                created.append(blob_name)
                continue
            client = cc.get_blob_client(blob_name)
            if client.exists():
                print(f"skip (exists): {folder_label}")
                skipped.append(blob_name)
                continue
            client.upload_blob(
                b"",
                overwrite=True,
                content_settings=ContentSettings(content_type="application/octet-stream"),
            )
            print(f"created: {folder_label}")
            created.append(blob_name)

    print()
    print(f"container={container_name}")
    print(f"created={len(created)} skipped={len(skipped)} dry_run={args.dry_run}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
