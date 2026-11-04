"""Quick KPI health audit: local page_cache + Azure page-response blobs."""
from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from azure.storage.blob import BlobServiceClient

ROOT = Path(__file__).resolve().parents[1]
PC = ROOT / "data" / "api_cache" / "page_cache"


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def inspect_payload(ns: str, name: str, d: dict) -> list[str]:
    issues: list[str] = []
    if not isinstance(d, dict):
        return [f"{ns}/{name}: not a dict"]

    # store-managers
    if ns == "store-managers":
        s = d.get("summary") or {}
        net = _num(s.get("total_net_sales"))
        soh = _num(s.get("total_soh"))
        disp = _num(s.get("total_dispatch_qty"))
        strp = _num(s.get("overall_sell_through_pct"))
        mgrs = len(d.get("managers") or [])
        if mgrs == 0:
            issues.append("no managers")
        if net and net > 0 and (soh or 0) <= 0 and (disp or 0) <= 0:
            issues.append(f"SOH/Dispatch ZERO with sales={net:.0f}")
        if net and net > 0 and (strp or 0) <= 0 and (disp or 0) <= 0:
            issues.append("STR 0%")
        print(
            f"  summary net={net} soh={soh} disp={disp} str={strp}% mgrs={mgrs}"
        )

    # sales-dashboard / home
    if ns in {"sales-dashboard", "home-summary", "home-breakdowns", "home-trend", "home-target"}:
        # various shapes
        for key in ("kpis", "summary", "totals", "cards"):
            block = d.get(key)
            if isinstance(block, dict):
                zeros = [
                    k
                    for k, v in block.items()
                    if isinstance(v, (int, float))
                    and v == 0
                    and any(
                        x in k.lower()
                        for x in ("sales", "soh", "qty", "target", "achiev", "dispatch")
                    )
                ]
                if zeros:
                    issues.append(f"{key} zero fields: {zeros[:8]}")
                sample = {
                    k: block[k]
                    for k in list(block)[:8]
                    if not isinstance(block[k], (dict, list))
                }
                print(f"  {key}: {sample}".encode("ascii", "replace").decode("ascii"))

    # sales-analyzer
    if ns == "sales-analyzer":
        m = ((d.get("grand_total") or {}).get("metrics") or {})
        sales = _num(m.get("sales"))
        soh = _num(m.get("current_soh"))
        disp = _num(m.get("dispatch_qty"))
        print(f"  analyzer sales={sales} soh={soh} disp={disp}")
        if sales and sales > 0 and (soh is None or soh <= 0) and (disp or 0) <= 0:
            issues.append("analyzer SOH/dispatch empty with sales")

    # product-performance
    if ns == "product-performance":
        s = d.get("summary") or d.get("kpis") or {}
        if isinstance(s, dict):
            print(f"  product keys sample: {list(s)[:12]}")
            if _num(s.get("total_net_sales") or s.get("net_sales") or s.get("sales")) in (0, None):
                # not always error
                pass

    # stock-overview
    if ns == "stock-overview":
        s = d.get("summary") or {}
        print(
            f"  stock summary soh={s.get('total_soh') or s.get('current_soh')} "
            f"stores={s.get('store_count')} rows={len(d.get('rows') or [])}"
        )
        soh = _num(s.get("total_soh") or s.get("current_soh") or s.get("total_units"))
        if soh is not None and soh <= 0:
            issues.append("stock SOH 0")

    # fy / target
    if ns in {"fy-comparison", "target-vs-achieved"}:
        s = d.get("summary") or d.get("kpis") or {}
        print(f"  {ns} summary keys: {list(s)[:10] if isinstance(s, dict) else type(s)}")

    if d.get("ok") is False or d.get("error"):
        issues.append(f"error={d.get('error') or d.get('detail')}")
    return issues


def main() -> None:
    print("=== LOCAL page_cache ===")
    local_issues = []
    if PC.exists():
        for ns_dir in sorted(PC.iterdir()):
            if not ns_dir.is_dir():
                continue
            files = sorted(ns_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            if not files:
                continue
            f = files[0]
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception as exc:
                print(f"{ns_dir.name}/{f.name}: READ FAIL {exc}")
                continue
            print(f"\n[{ns_dir.name}] newest={f.name} mb={round(f.stat().st_size/1e6,3)}")
            iss = inspect_payload(ns_dir.name, f.name, d)
            for i in iss:
                local_issues.append(f"LOCAL {ns_dir.name}: {i}")
                print("  !!", i)

    print("\n=== AZURE page-response (newest per namespace) ===")
    cc = BlobServiceClient.from_connection_string(
        os.environ["AZURE_STORAGE_CONNECTION_STRING"]
    ).get_container_client(os.environ.get("AZURE_STORAGE_CONTAINER", "zinc-data"))
    blobs = list(cc.list_blobs(name_starts_with="cache/page-response/"))
    by_ns: dict[str, list] = {}
    for b in blobs:
        parts = b.name.split("/")
        if len(parts) < 3:
            continue
        ns = parts[2]
        by_ns.setdefault(ns, []).append(b)

    azure_issues = []
    for ns, items in sorted(by_ns.items()):
        newest = sorted(items, key=lambda x: x.last_modified or 0, reverse=True)[0]
        raw = cc.download_blob(newest.name).readall()
        try:
            d = json.loads(raw)
        except Exception as exc:
            print(f"\n[{ns}] BLOB READ FAIL {exc}")
            continue
        print(
            f"\n[{ns}] blob={Path(newest.name).name} "
            f"mb={round((newest.size or 0)/1e6,3)} mtime={newest.last_modified}"
        )
        iss = inspect_payload(ns, newest.name, d)
        for i in iss:
            azure_issues.append(f"AZURE {ns}: {i}")
            print("  !!", i)

    print("\n=== SOURCE CACHES (api_cache) ===")
    critical = [
        "daily_inventory_movement.parquet",
        "dispatch_lead_time.parquet",
        "sku_breakeven.parquet",
        "replenishments_recom_snapshot_v1.parquet",
        "festival_comparison.json",
        "financial_year_summary.parquet",
        "soh_analyzer_prepared_v5_daily_only.parquet",
    ]
    for name in critical:
        local = ROOT / "data" / "api_cache" / name
        blob_name = f"cache/api_cache/{name}"
        b = cc.get_blob_client(blob_name)
        loc = f"local={round(local.stat().st_size/1e6,2)}MB" if local.exists() else "local=MISS"
        if b.exists():
            p = b.get_blob_properties()
            az = f"azure={round((p.size or 0)/1e6,2)}MB @{p.last_modified.date()}"
        else:
            az = "azure=MISS"
        flag = ""
        if not local.exists() or not b.exists():
            flag = " !!"
        elif name == "daily_inventory_movement.parquet" and b.exists():
            # stale if older than local by much
            pass
        print(f"  {name}: {loc} | {az}{flag}")

    print("\n=== ISSUE SUMMARY ===")
    all_iss = local_issues + azure_issues
    if not all_iss:
        print("No KPI zero/stale flags in newest caches.")
    else:
        for i in all_iss:
            print("-", i)


if __name__ == "__main__":
    main()
