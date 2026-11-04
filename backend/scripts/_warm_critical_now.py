"""Quick critical warm: prepared frames + default Analyzer/Home/Sales page caches."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONUNBUFFERED", "1")

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")


def main() -> int:
    t0 = time.perf_counter()
    out: dict = {}

    from api.sales_year_loader import fy_token, get_default_dashboard_year, warm_fy_years_sorted
    from api.routes.sales_analyzer import (
        publish_prepared_analyzer_caches_to_blob,
        warm_analyzer_prepared_frames,
        warm_default_analyzer_queries,
    )
    from api.routes.executive_overview import warm_home_page_caches
    from api.routes.sales_dashboard import warm_sales_overview_cache
    from api.page_response_cache import warm_default_page_caches

    dash = int(get_default_dashboard_year())
    years = sorted(warm_fy_years_sorted(), reverse=True)
    print(f"dash={dash} fy={fy_token(dash)} years={years}", flush=True)

    print("A) prepared frames", flush=True)
    prep = {}
    for y in years:
        t = time.perf_counter()
        try:
            warm_analyzer_prepared_frames(year=int(y), force_refresh=False)
            prep[str(y)] = {"ok": True, "s": round(time.perf_counter() - t, 1)}
        except Exception as exc:
            prep[str(y)] = {"ok": False, "error": str(exc)}
        print(f"  year={y} {prep[str(y)]}", flush=True)
    out["prepared"] = prep

    print("B) publish prepared -> blob", flush=True)
    out["publish_prepared"] = publish_prepared_analyzer_caches_to_blob()
    print(out["publish_prepared"], flush=True)

    print("C) default analyzer queries", flush=True)
    out["analyzer_defaults"] = warm_default_analyzer_queries()
    print(out["analyzer_defaults"], flush=True)

    print("D) home page caches", flush=True)
    warm_home_page_caches(dash)
    out["home"] = {"ok": True}

    print("E) sales overview cache", flush=True)
    out["sales_overview"] = warm_sales_overview_cache(force_refresh=False)
    print(out["sales_overview"], flush=True)

    print("F) full default page caches (may take a few min)", flush=True)
    out["page_caches"] = warm_default_page_caches()
    steps = {k: v.get("ok") for k, v in (out["page_caches"].get("steps") or {}).items()}
    print({"ok": out["page_caches"].get("ok"), "seconds": out["page_caches"].get("seconds"), "steps": steps}, flush=True)

    path = ROOT / "pipeline" / "output_files" / "critical_cache_warm.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"wrote {path} in {round(time.perf_counter() - t0, 1)}s", flush=True)
    return 0 if out.get("analyzer_defaults", {}).get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
