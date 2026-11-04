"""List cache/page-response/* namespaces on blob vs critical FE keys."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from azure.storage.blob import BlobServiceClient

conn = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
container = os.environ.get("AZURE_STORAGE_CONTAINER", "zinc-data")
client = BlobServiceClient.from_connection_string(conn).get_container_client(container)
prefix = "cache/page-response/"
ns: dict[str, dict] = {}
for b in client.list_blobs(name_starts_with=prefix):
    parts = b.name[len(prefix) :].split("/")
    if not parts:
        continue
    n = parts[0]
    node = ns.setdefault(n, {"count": 0, "latest": None, "latest_size": 0})
    node["count"] += 1
    lm = b.last_modified
    if node["latest"] is None or lm > node["latest"]:
        node["latest"] = lm
        node["latest_size"] = b.size

for k in sorted(ns):
    print(f"{k}: count={ns[k]['count']} latest={ns[k]['latest']} size={ns[k]['latest_size']}")

need = [
    "sales-dashboard-kpis",
    "sales-dashboard-insights",
    "sales-dashboard-rows",
    "dispatch-lead-time",
    "sku-breakeven",
]
print("---NEED---")
for n in need:
    print(n, "OK" if n in ns else "MISSING")
