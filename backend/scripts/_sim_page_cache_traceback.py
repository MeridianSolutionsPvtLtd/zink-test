"""Force sales_analyzer_zone_store warm failure; persist into auto_runs/latest.json."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

os.environ["PAGE_CACHE_SIMULATE_FAIL"] = "1"
os.environ.setdefault("DASHBOARD_WARM_ON_STARTUP", "0")
os.environ.setdefault("PAGE_CACHE_BLOB", "0")

from dotenv import load_dotenv

load_dotenv(_ROOT / ".env")


def _ok(*_a, **_k):
    return {"ok": True}


def main() -> None:
    # Keep warm fast: stub non-analyzer / publish paths; keep analyzer zone_store live
    # so PAGE_CACHE_SIMULATE_FAIL probe inside page_response_cache runs.
    import api.page_response_cache as prc
    from api.routes import sales_analyzer as sa
    from api.routes import sales_dashboard as sd
    from pipeline.auto_daily_run import LOG_DIR, _write_run_artifacts

    sa.warm_analyzer_prepared_frames = lambda *a, **k: None  # type: ignore
    # Fast stubs for heavy non-analyzer jobs
    for name in (
        "get_sales_dashboard",
        "get_financial_year_comparison",
        "get_sales_pivot",
        "warm_sales_overview_progressive_caches",
        "warm_sales_overview_cache",
    ):
        if hasattr(sd, name):
            setattr(sd, name, _ok)

    # Stub other route warms commonly imported inside warm_default_page_caches
    patches = []
    try:
        from api.routes import executive as ex

        for name in (
            "get_executive_summary",
            "get_multi_year_trend",
            "get_executive_breakdowns",
            "get_target_vs_achievement",
        ):
            if hasattr(ex, name):
                patches.append(patch.object(ex, name, _ok))
    except Exception:
        pass

    for p in patches:
        p.start()

    real_exec = sa.execute_analyzer_query

    def _exec(req):
        rows = list(getattr(req, "rows", None) or [])
        # All analyzer warms except zone_store return quickly.
        if rows != ["zone", "site_id_name"]:
            return {
                "ok": True,
                "rows": [],
                "grand_total": {"metrics": {}},
                "total_records": 0,
            }
        return real_exec(req)

    sa.execute_analyzer_query = _exec  # type: ignore

    t0 = time.perf_counter()
    try:
        page_cache = prc.warm_default_page_caches()
    finally:
        for p in patches:
            p.stop()
        os.environ.pop("PAGE_CACHE_SIMULATE_FAIL", None)

    payload = {
        "ok": False,
        "trigger": "sim_page_cache_traceback",
        "seconds": round(time.perf_counter() - t0, 1),
        "page_cache": page_cache,
    }
    path = _write_run_artifacts(payload)
    latest = LOG_DIR / "latest.json"
    print("artifact", path)
    print("latest", latest)
    data = json.loads(latest.read_text(encoding="utf-8"))
    step = ((data.get("page_cache") or {}).get("steps") or {}).get(
        "sales_analyzer_zone_store"
    ) or {}
    print("step_ok", step.get("ok"))
    print("error", step.get("error"))
    tb = step.get("traceback") or ""
    print("has_traceback_field", bool(tb))
    print("has_traceback_body", "Traceback (most recent call last)" in tb)
    print("has_simulated_msg", "SIMULATED_ZONE_STORE_FAIL_FOR_TRACEBACK_TEST" in tb + str(step.get("error")))
    print("traceback_head:")
    print("\n".join(tb.splitlines()[:12]))
    if not (
        step.get("ok") is False
        and tb
        and "Traceback (most recent call last)" in tb
        and "SIMULATED_ZONE_STORE_FAIL_FOR_TRACEBACK_TEST" in (tb + str(step.get("error")))
    ):
        raise SystemExit("TRACEBACK_CHECK_FAILED")
    print("TRACEBACK_IN_LATEST_JSON: YES")


if __name__ == "__main__":
    main()
