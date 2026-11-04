import sys
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO)

backend_dir = Path(r"c:\Users\ChandanSoni\Downloads\merged_backend_test\merged_backend\meridianai-stockpulse-backend")
sys.path.insert(0, str(backend_dir))

from pipeline.ginesys_raw import fetch_raw_to_incoming
from pipeline.merge_category_inputs import merge_all_categories, sync_current_to_local

print("=== 1. FETCHING RAW SALES FILES FROM BLOB TO INCOMING ===")
raw_res = fetch_raw_to_incoming(dry_run=False, only_ids=["sales-pt-ssl-centro-iconic"])
print("Raw fetch copied:", raw_res.get("copied"))
print("Touched categories:", raw_res.get("touched_categories"))

print("\n=== 2. MERGING SALES CATEGORY INTO CURRENT AND COMBINED_SALES ===")
merge_res = merge_all_categories(dry_run=False, keep_incoming=False, only_ids=["sales-pt-ssl-centro-iconic"])
print("Merged status:", merge_res.get("ok"))
for m in (merge_res.get("merged") or []):
    print("Category merged:", m.get("category_id"), "New Bounds:", m.get("new_bounds"), "Added rows:", m.get("added_rows"))

print("\n=== 3. SYNCING CURRENT TO LOCAL DISK ===")
sync_res = sync_current_to_local(dry_run=False, only_ids=["sales-pt-ssl-centro-iconic"])
print("Synced local files:", sync_res.get("downloaded"))

print("\n=== SALES MERGE COMPLETED SUCCESSFULLY ===")
