"""
Verify Azure Blob access for merged_backend.

Usage (from merged_backend/):
  1. Put AZURE_STORAGE_CONNECTION_STRING in .env (do not commit)
  2. python scripts/check_blob_connection.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(ROOT, ".env"))
except ImportError:
    pass


def main() -> int:
    conn = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "").strip()
    if not conn:
        print("FAIL: AZURE_STORAGE_CONNECTION_STRING missing in .env")
        print("Add (without committing):")
        print("  AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=https;AccountName=zinklondon;AccountKey=...;EndpointSuffix=core.windows.net")
        print("  AZURE_STORAGE_CONTAINER=zinc-data")
        print("  AZURE_BLOB_INPUT_PREFIX=input/")
        print("  AZURE_BLOB_OUTPUT_PREFIX=output/")
        print("  DATABASE_BACKEND=blob")
        return 1

    # Never print the connection string / key
    try:
        from azure.storage.blob import BlobServiceClient

        account = os.environ.get("AZURE_STORAGE_ACCOUNT", "zinklondon")
        container_name = os.environ.get("AZURE_STORAGE_CONTAINER", "zinc-data")
        input_prefix = os.environ.get("AZURE_BLOB_INPUT_PREFIX", "input/")
        output_prefix = os.environ.get("AZURE_BLOB_OUTPUT_PREFIX", "output/")
        if input_prefix and not input_prefix.endswith("/"):
            input_prefix += "/"
        if output_prefix and not output_prefix.endswith("/"):
            output_prefix += "/"

        svc = BlobServiceClient.from_connection_string(conn)
        cc = svc.get_container_client(container_name)
        exists = cc.exists()
        if not exists:
            print(f"FAIL: container '{container_name}' not found on account '{account}'")
            return 2

        inputs = list(cc.list_blobs(name_starts_with=input_prefix))
        outputs = list(cc.list_blobs(name_starts_with=output_prefix))
        print("OK: Blob connection succeeded")
        print(f"  account:   {account}")
        print(f"  container: {container_name}")
        print(f"  input/:    {len(inputs)} blob(s)")
        print(f"  output/:   {len(outputs)} blob(s)")
        for b in inputs[:8]:
            print(f"    IN  {b.name}")
        for b in outputs[:8]:
            print(f"    OUT {b.name}")
        return 0
    except Exception as e:
        print(f"FAIL: {type(e).__name__}: {e}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
