# """
# StockPulse Backend API — serves data from Fabric or Power BI semantic model.
# Run: uvicorn main:app --reload --port 5001
# Re-loaded at: 2026-09-20T17:33:00
# """
# import os
# import sys
# import logging

# # Load .env 
# try:
#     from dotenv import load_dotenv
#     # Look for .env in the same directory as this script (backend/)
#     load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))
# except ImportError:
#     pass  # python-dotenv optional

# from fastapi import FastAPI, Request
# from fastapi.middleware.cors import CORSMiddleware
# from fastapi.responses import JSONResponse

# from api.routes import dashboard, stores, inventory, analytics, sellthrough, seasonal, alerts, replenishment, targets, transfers, data_insights, ai_planning, sales_kpi, retail_performance

# app = FastAPI(title="StockPulse API", version="1.0.0")
# log = logging.getLogger(__name__)


# _EMPTY_INVENTORY_SUMMARY = {
#     "totalStockUnits": 0, "totalStockValue": 0, "totalUnitsSold": 0, "totalRevenue": 0,
#     "totalUnitsPurchased": 0, "totalSkus": 0, "soldSkus": 0, "noSaleSkus": 0,
#     "overallDaysOfSupply": 0, "dailyVelocity": 0, "sellThrough": 0,
#     "needsReplenishCategories": 0, "overstockUnits": 0, "healthDistribution": {}, "categoryCount": 0,
# }

# _PATH_FALLBACKS = {
#     "/api/dashboard/regions": {"regions": [], "zoneList": ["All Regions"]},
#     "/api/dashboard/summary": {"revenue": 0, "units_sold": 0, "units_purchased": 0, "sell_through_pct": 0, "active_stores": 0, "inventory_units": 0, "avg_discount_pct": 0, "at_risk_skus": 0, "sell_through_trend": 0, "inventory_trend": 0, "discount_trend": 0, "at_risk_trend": 0},
#     "/api/dashboard/region-profile": {"sellThrough": 0, "inventoryValue": 0, "discountRate": 0, "at_risk_skus": 0, "sell_through_trend": 0, "inventory_trend": 0, "discount_trend": 0, "at_risk_trend": 0},
#     "/api/alerts": {"alerts": []},
#     "/api/stores": {"stores": []},
#     "/api/stores/regions": {"regions": ["All Regions"]},
#     "/api/inventory": {"summary": _EMPTY_INVENTORY_SUMMARY, "categories": []},
#     "/api/data-insights/overview": {"stores": 0, "cities": 0, "states": 0, "zones": 0, "uniqueSkus": 0, "barcodes": 0, "purchaseRecords": 0, "salesRecords": 0, "purchaseUnits": 0, "salesUnits": 0, "revenue": 0, "avgDiscount": 0},
#     "/api/data-insights/quality-summary": {"returns": {"count": 0, "percentage": 0}, "negativeDiscount": {"count": 0, "percentage": 0}, "extremeDiscount": {"count": 0, "percentage": 0}, "validData": {"count": 0, "percentage": 0}, "missingZones": 0, "missingGrades": 0, "totalRecords": 0},
#     "/api/data-insights/zone-distribution": {"zones": []},
#     "/api/data-insights/season-distribution": {"seasons": []},
#     "/api/data-insights/inventory-movement": {"totalSkus": 0, "soldSkus": 0, "neverSoldSkus": 0, "neverSoldPercentage": 0},
#     "/api/data-insights/idle-time": {"distribution": [], "summary": {"slowMoving": 0, "slowMovingPct": 0, "deadStock": 0, "deadStockPct": 0, "staleStock": 0, "staleStockPct": 0}},
# }


# @app.exception_handler(Exception)
# def handle_db_errors(request: Request, exc: Exception):
#     """Return 200 with empty structure when DB/query fails (AKS empty DB)."""
#     path = request.url.path.split("?")[0]
#     fallback = _PATH_FALLBACKS.get(path)
#     if fallback is None:
#         if path == "/api/stores/regions":
#             fallback = {"regions": ["All Regions"]}
#         elif path.startswith("/api/stores/"):
#             fallback = {"stores": []} if path.count("/") == 3 else {"siteCode": "", "categories": []}
#         elif path.startswith("/api/inventory/"):
#             fallback = {"category": "", "skus": [], "totalSkus": 0}
#     if fallback is None:
#         fallback = {}
#     log.warning("API fallback for %s: %s", path, exc)
#     return JSONResponse(status_code=200, content=fallback)

# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=["*"],
#     allow_credentials=True,
#     allow_methods=["*"],
#     allow_headers=["*"],
# )

# app.include_router(dashboard.router, prefix="/api", tags=["dashboard"])
# app.include_router(stores.router, prefix="/api", tags=["stores"])
# app.include_router(inventory.router, prefix="/api", tags=["inventory"])
# app.include_router(analytics.router, prefix="/api", tags=["analytics"])
# app.include_router(sellthrough.router, prefix="/api", tags=["sellthrough"])
# app.include_router(seasonal.router, prefix="/api", tags=["seasonal"])
# app.include_router(alerts.router, prefix="/api", tags=["alerts"])
# app.include_router(replenishment.router, prefix="/api", tags=["replenishment"])
# app.include_router(targets.router, prefix="/api", tags=["targets"])
# app.include_router(transfers.router, prefix="/api", tags=["transfers"])
# app.include_router(data_insights.router, prefix="/api", tags=["data-insights"])
# app.include_router(ai_planning.router, prefix="/api/ai", tags=["ai-planning"])
# app.include_router(sales_kpi.router, prefix="/api", tags=["sales-kpi"])
# app.include_router(retail_performance.router, prefix="/api", tags=["retail-performance"])



# @app.get("/api/health")
# def health():
#     return {"status": "ok"}


# @app.get("/api/data-source")
# def data_source():
#     """Returns the configured data source for verification."""
#     from api.config import DATABASE_BACKEND
#     backend = DATABASE_BACKEND.lower()
#     return {
#         "data_source": "fabric_sql" if backend == "fabric_sql" else ("powerbi" if backend == "powerbi" else "unknown"),
#         "database_backend": backend,
#         "fabric_workspace": os.environ.get("FABRIC_WORKSPACE_NAME") or os.environ.get("FABRIC_SQL_ENDPOINT", ""),
#         "model_id": os.environ.get("MODEL_REVENUE_ID") or os.environ.get("PBI_DATASET_ID", ""),
#     }


# @app.get("/api/debug/sellthrough-tables")
# def debug_sellthrough_tables():
#     """Probe Fact_Sales_Agg and Fact_Stock_Received to discover real column names."""
#     import traceback
#     from api.db_powerbi import execute_dax
#     result = {}
#     for tbl in ["Fact_Sales_Agg", "Fact_Stock_Received"]:
#         try:
#             rows = execute_dax(f"EVALUATE ROW(\"cnt\", COUNTROWS('{tbl}'))")
#             cnt = rows[0].get("[cnt]") if rows else "?"
#             result[tbl] = {"row_count": cnt, "error": None}
#             # Try to get first row to inspect columns
#             try:
#                 sample = execute_dax(f"EVALUATE TOPN(1, '{tbl}')")
#                 result[tbl]["sample_columns"] = list(sample[0].keys()) if sample else []
#             except Exception as e2:
#                 result[tbl]["sample_columns"] = f"Error: {e2}"
#         except Exception as e:
#             result[tbl] = {"row_count": None, "error": str(e)}
#     return result


# @app.get("/api/debug/discount-by-dept")
# def debug_discount_by_dept():
#     """Probe Fact_Sales_Detail columns and test [1Discount %] measure."""
#     from api.db_powerbi import execute_dax
#     results = {}
#     # Step 1: probe Fact_Sales_Detail columns
#     try:
#         sample = execute_dax("EVALUATE TOPN(1, 'Fact_Sales_Detail')")
#         results["fact_sales_detail_columns"] = list(sample[0].keys()) if sample else []
#     except Exception as e:
#         results["fact_sales_detail_columns"] = f"ERROR: {e}"
#     # Step 2: try the full SUMMARIZECOLUMNS with [1Discount %]
#     dax = """
# EVALUATE
# SUMMARIZECOLUMNS(
#     'Fact_Sales_Detail'[Department],
#     "discount_pct", CALCULATE([1Discount %])
# )
# """
#     try:
#         rows = execute_dax(dax)
#         results["discount_by_dept"] = rows
#     except Exception as e:
#         results["discount_by_dept_error"] = str(e)
#     return results

#     return results


# @app.get("/api/debug/stock-by-site")
# def debug_stock_by_site():
#     """Test Dual Query logic"""
#     from api.db_powerbi import execute_dax
#     q1 = "EVALUATE SUMMARIZECOLUMNS('Fact_Stock_Received'[Site], \"qty_received\", [Qty_Received])"
#     q2 = "EVALUATE SUMMARIZECOLUMNS('Fact_Sales_Detail'[Site], \"qty_sold\", [Qty_Sold])"
    
#     try:
#         r1 = execute_dax(q1)
#         r2 = execute_dax(q2)
        
#         # Join in python
#         sites = {}
#         for r in r1:
#             site = r.get("Fact_Stock_Received[Site]")
#             if site:
#                 sites[site] = {"qr": r.get("[qty_received]", 0), "qs": 0}
                
#         for r in r2:
#             site = r.get("Fact_Sales_Detail[Site]")
#             if site and site in sites:
#                 sites[site]["qs"] = r.get("[qty_sold]", 0)
                
#         results = []
#         for s, v in sites.items():
#             st = v["qr"] - v["qs"]
#             if 0 <= st < 10:
#                 results.append({"site": s, "stock": st, "qr": v["qr"], "qs": v["qs"]})
                
#         results.sort(key=lambda x: x["stock"])
#         return results[:5]
#     except Exception as e:
#         return {"error": str(e)}


# @app.get("/api/debug/at-risk-test")
# def debug_at_risk():
#     """Directly test the at-risk SKU DAX and return raw result or error."""
#     from api.db_powerbi import execute_dax
#     dax_query = """
# EVALUATE
# VAR MaxDate = CALCULATE(MAX('Fact_Sales_Agg'[Date]))
# VAR SkusIn180Days = CALCULATETABLE(
#     VALUES('Fact_Stock_Received'[SKU]),
#     'Fact_Stock_Received'[Date] >= MaxDate - 179,
#     'Fact_Stock_Received'[Date] <= MaxDate
# )
# VAR Universe = COUNTROWS(SkusIn180Days)
# VAR SkusWithRecentSales = CALCULATE(
#     DISTINCTCOUNT('Fact_Sales_Agg'[SKU]),
#     TREATAS(SkusIn180Days, 'Fact_Sales_Agg'[SKU]),
#     'Fact_Sales_Agg'[Date] >= MaxDate - 13,
#     'Fact_Sales_Agg'[Date] <= MaxDate,
#     'Fact_Sales_Agg'[Sales_Qty] > 0
# )
# VAR AtRisk = Universe - IF(ISBLANK(SkusWithRecentSales), 0, SkusWithRecentSales)
# RETURN ROW(
#     "max_date", MaxDate,
#     "universe_180d", Universe,
#     "sold_last_14d", SkusWithRecentSales,
#     "at_risk", AtRisk
# )
# """
#     try:
#         rows = execute_dax(dax_query)
#         return {"success": True, "rows": rows}
#     except Exception as e:
#         return {"success": False, "error": str(e)}



# @app.get("/api/debug/dax-test")
# def debug_dax_test():
#     """Run progressive DAX tests to pinpoint schema/DAX failures. Call this to see the real error."""
#     import traceback
#     from api.schema_config import T_FS, C_SALE_DATE, C_SALE_AMOUNT, C_SALE_QTY
#     result = {"steps": [], "ok": False, "error": None}
#     execute_dax = None
#     try:
#         from api.db_powerbi import execute_dax

#         # Step 1: Simplest possible DAX (no table reference)
#         try:
#             rows = execute_dax('EVALUATE ROW("n", 1)')
#             result["steps"].append({"step": 1, "name": "EVALUATE ROW (no tables)", "ok": True, "rows": len(rows or [])})
#         except Exception as e:
#             result["steps"].append({"step": 1, "name": "EVALUATE ROW (no tables)", "ok": False, "error": str(e)})
#             result["error"] = f"Step 1 failed - API or permissions issue: {e}"
#             return result

#         # Step 2: Table exists (COUNTROWS)
#         try:
#             rows = execute_dax(f'EVALUATE ROW("cnt", COUNTROWS({T_FS}))')
#             result["steps"].append({"step": 2, "name": f"COUNTROWS({T_FS})", "ok": True, "rows": rows})
#         except Exception as e:
#             result["steps"].append({"step": 2, "name": f"COUNTROWS({T_FS})", "ok": False, "error": str(e)})
#             result["error"] = f"Step 2 failed - table '{T_FS}' may not exist: {e}"
#             return result

#         # Step 3: Date column exists
#         try:
#             rows = execute_dax(f'EVALUATE ROW("maxdate", MAX({T_FS}[{C_SALE_DATE}]))')
#             result["steps"].append({"step": 3, "name": f"MAX({T_FS}[{C_SALE_DATE}])", "ok": True, "rows": rows})
#         except Exception as e:
#             result["steps"].append({"step": 3, "name": f"MAX([{C_SALE_DATE}])", "ok": False, "error": str(e)})
#             result["error"] = f"Step 3 failed - column '{C_SALE_DATE}' may not exist: {e}"
#             return result

#         # Step 4: Amount/Qty columns (often the cause of 400 if they're measures, not columns)
#         for col, label in [(C_SALE_AMOUNT, "amount"), (C_SALE_QTY, "qty")]:
#             try:
#                 rows = execute_dax(f'EVALUATE ROW("sum_{label}", SUM({T_FS}[{col}]))')
#                 result["steps"].append({"step": f"4_{label}", "name": f"SUM([{col}])", "ok": True, "rows": rows})
#             except Exception as e:
#                 result["steps"].append({"step": f"4_{label}", "name": f"SUM([{col}])", "ok": False, "error": str(e)})
#                 result["error"] = f"Column '{col}' may not exist or is a measure (use a base column): {e}"

#         result["ok"] = True
#         return result
#     except Exception as e:
#         result["error"] = str(e)
#         result["trace"] = traceback.format_exc()
#         log.exception("dax-test failed")
#         return result


# @app.get("/api/debug/schema")
# def debug_schema():
#     """Return current semantic model table/column mapping (from .env or defaults)."""
#     from api import schema_config
#     return {
#         "tables": {
#             "sales": schema_config.T_FS,
#             "purchases": schema_config.T_FP,
#             "inventory": schema_config.T_FI,
#             "stores": schema_config.T_DS,
#             "products": schema_config.T_DP,
#         },
#         "columns": {
#             "sales": {"date": schema_config.C_SALE_DATE, "amount": schema_config.C_SALE_AMOUNT, "qty": schema_config.C_SALE_QTY, "is_return": schema_config.C_SALE_IS_RETURN, "site": schema_config.C_SALE_SITE, "barcode": schema_config.C_SALE_BARCODE, "discount": schema_config.C_SALE_DISCOUNT},
#             "purchases": {"date": schema_config.C_PURCH_DATE, "qty": schema_config.C_PURCH_QTY, "site": schema_config.C_PURCH_SITE, "barcode": schema_config.C_PURCH_BARCODE},
#             "inventory": {"date": schema_config.C_INV_DATE, "qty": schema_config.C_INV_QTY, "barcode": schema_config.C_INV_BARCODE, "site": schema_config.C_INV_SITE},
#             "stores": {"site": schema_config.C_STORE_SITE, "zone": schema_config.C_STORE_ZONE},
#             "products": {"barcode": schema_config.C_PROD_BARCODE, "department": schema_config.C_PROD_DEPT, "section": schema_config.C_PROD_SECTION},
#         },
#         "env_vars": "Set PBI_TABLE_* and PBI_COL_* in .env to override (see api/schema_config.py)",
#     }


# @app.get("/api/debug/db-status")
# def debug_db_status():
#     """Diagnostic endpoint: test Fabric/DB connection and surface any error."""
#     import traceback
#     from api.config import DATABASE_BACKEND
#     backend = DATABASE_BACKEND.lower()
#     result = {"backend": backend, "ok": False, "error": None, "detail": None}
#     try:
#         if backend == "powerbi":
#             from api.db_powerbi import execute_dax
#             rows = execute_dax("EVALUATE ROW(\"n\", 1)")
#             result["ok"] = bool(rows and len(rows) > 0)
#             result["detail"] = "Power BI Execute Queries API succeeded" if result["ok"] else "No rows returned"
#         else:
#             from api.db import get_conn, query_one
#             conn = get_conn()
#             r = query_one(conn, "SELECT 1 AS n") if backend == "fabric_sql" else query_one(conn, "SELECT 1 AS n")
#             result["ok"] = r is not None
#             result["detail"] = "Connection and basic query succeeded" if r else "Query returned no rows"
#             if hasattr(conn, "close"):
#                 conn.close()
#     except Exception as e:
#         result["error"] = str(e)
#         result["detail"] = traceback.format_exc()
#         log.exception("db-status diagnostic failed")
#     return result
 
 
"""
StockPulse Backend API — serves data from Fabric or Power BI semantic model.
Run: uvicorn main:app --reload --port 5001
"""
 
import os
import logging
from pathlib import Path

print("BACKEND RELOADED AT: " + str(__import__('datetime').datetime.now()))
# Load .env
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
 
from api.routes import (
    dashboard,
    stores,
    inventory,
    analytics,
    sellthrough,
    seasonal,
    alerts,
    replenishment,
    targets,
    transfers,
    data_insights,
    ai_planning,
    sales_kpi,
    retail_performance,
    combined,
    ai_seasonal,
    replenishments_recom,
    transfers_recom,
    download_logs,
    pipeline,
    upload,
    sales_dashboard,
    stock_overview,
    target_vs_achieved,
    store_soh_weekly,
    site_growth_playbook,
    wh_soh_dept_insights,
    wh_sku_demand,
    focus_day_events,
    product_performance,
    executive_overview,
    product_images,
    manager_performance,
    sales_analyzer,
    dispatch_lead_time,
    sku_breakeven,
)

app = FastAPI(title="StockPulse API", version="1.0.0")


@app.on_event("startup")
def _warm_dashboard_caches_on_startup() -> None:
    import os
    import threading

    def _env_flag(name: str, default: bool) -> bool:
        raw = os.environ.get(name)
        if raw is None:
            return default
        return raw.strip().lower() not in ("0", "false", "no", "off")

    # Azure App Service: avoid OOM from loading multi-GB frames on every deploy.
    on_azure = bool(os.environ.get("WEBSITE_SITE_NAME") or os.environ.get("WEBSITE_INSTANCE_ID"))
    warm_enabled = _env_flag("DASHBOARD_WARM_ON_STARTUP", True)
    heavy_warm = _env_flag("DASHBOARD_HEAVY_WARM", not on_azure)
    parquet_only = _env_flag("DASHBOARD_WARM_PARQUET_ONLY", on_azure)

    def _sequential_warm() -> None:
        if not warm_enabled:
            logger.info(
                "Dashboard warm-up disabled (DASHBOARD_WARM_ON_STARTUP=0) — "
                "run `python -m scripts.warm_caches` in a separate process"
            )
            # Filtered (Azure SQL) Sales views still join SOH from in-process maps.
            # Load just those once so the first filter apply in this worker is not +30-40 s.
            if _env_flag("API_LIGHT_WARM", True):
                from pipeline.customer_master import load_customer_master
                from pipeline.item_master import build_sku_image_map

                for name, fn in (
                    ("soh-map", sales_dashboard._get_soh_map),
                    ("dm-soh-frame", sales_dashboard._load_dm_soh_frame),
                    ("customer-master", load_customer_master),
                    # Home SKU cards; disk-cached after the first build per Item Master file.
                    ("sku-image-map", build_sku_image_map),
                ):
                    try:
                        t0 = time.perf_counter()
                        fn()
                        logger.info("API light warm %s done in %.1fs", name, time.perf_counter() - t0)
                    except Exception as exc:
                        logger.warning("API light warm %s failed: %s", name, exc)
            return

        logger.info(
            "Dashboard warm-up starting (azure=%s parquet_only=%s heavy=%s)",
            on_azure,
            parquet_only,
            heavy_warm,
        )

        # Hosted: pull daily_inventory_movement from blob before any SOH surface warms
        if on_azure:
            try:
                from api.current_soh import ensure_daily_movement_from_blob

                path = ensure_daily_movement_from_blob(force=False)
                logger.info(
                    "Startup DM blob pull: exists=%s size=%.1fMB",
                    path.exists(),
                    (path.stat().st_size / 1e6) if path.exists() else 0.0,
                )
            except Exception as exc:
                logger.warning("Startup DM blob pull failed: %s", exc)

        from api.sales_year_loader import get_default_dashboard_year

        dash_year = get_default_dashboard_year()

        # Critical path first (Home / Sales / Store SOH) so UI can hit warm caches
        # before inventory pricing compete for CPU+blob bandwidth.
        # Analyzer prepared frames are critical too — otherwise Pivot stays on
        # "cache is warming" while deferred steps run for several minutes.
        critical_steps: list[tuple[str, object]] = [
            (
                "sales-dashboard",
                lambda: sales_dashboard.warm_sales_dashboard_cache(parquet_only=parquet_only),
            ),
            (
                # FE Sales Overview hits /kpis|/insights|/rows — not monolithic /sales-dashboard.
                "sales-overview-progressive",
                lambda y=dash_year: sales_dashboard.warm_sales_overview_progressive_caches(
                    year=y,
                    force_refresh=False,
                ),
            ),
            (
                "sales-analyzer-frames",
                lambda y=dash_year: sales_analyzer.warm_analyzer_prepared_frames(y),
            ),
            (
                "sales-analyzer-default-queries",
                lambda: sales_analyzer.warm_default_analyzer_queries(),
            ),
            (
                "home-page",
                lambda y=dash_year: executive_overview.warm_home_page_caches(y),
            ),
            (
                "stock-overview",
                lambda: stock_overview.warm_stock_overview_cache(parquet_only=parquet_only),
            ),
            (
                "focus-day",
                lambda: focus_day_events.warm_festival_comparison_cache(),
            ),
        ]

        deferred_steps: list[tuple[str, object]] = []

        def _warm_inventory_pricing() -> None:
            """Item Master prices + lead-time/breakeven — defer on Azure so Home/Sales win."""
            from api.routes.product_performance import _load_item_master_meta
            from api.routes import dispatch_lead_time, sku_breakeven

            meta = _load_item_master_meta()
            logger.info(
                "Inventory pricing warm: item_master_sku_meta rows=%s",
                0 if meta is None else len(meta),
            )
            dlt = dispatch_lead_time._compute_lead_time_dataset()
            be = sku_breakeven._compute_breakeven_dataset()
            dlt_pub = dispatch_lead_time.publish_dispatch_lead_time_to_blob()
            be_pub = sku_breakeven.publish_sku_breakeven_to_blob()
            # Default Inventory Analytics tabs (FE page_size) → page_cache JSON for cold open.
            try:
                dlt_page = dispatch_lead_time.get_dispatch_lead_time_report(
                    search="",
                    alert_filter="all",
                    section="",
                    season="",
                    sort_by="inward_date",
                    sort_dir="desc",
                    page=1,
                    page_size=20,
                    refresh=0,
                )
            except Exception as dlt_exc:
                dlt_page = {"ok": False, "error": str(dlt_exc)}
            try:
                be_page = sku_breakeven.get_sku_breakeven_report(
                    search="",
                    risk_filter="all",
                    section="",
                    season="",
                    sort_by="dispatch_gap_vs_be",
                    sort_dir="desc",
                    page=1,
                    page_size=50,
                    refresh=0,
                )
            except Exception as be_exc:
                be_page = {"ok": False, "error": str(be_exc)}
            logger.info(
                "Inventory pricing warm: lead_time_rows=%s breakeven_rows=%s "
                "lead_time_blob=%s breakeven_blob=%s "
                "lead_time_page=%s breakeven_page=%s",
                0 if dlt is None else len(dlt),
                0 if be is None else len(be),
                dlt_pub.get("ok"),
                be_pub.get("ok"),
                bool(isinstance(dlt_page, dict) and dlt_page.get("ok", True)),
                bool(isinstance(be_page, dict) and be_page.get("ok", True)),
            )

        def _warm_replenishments() -> None:
            """Serve Replenishment page from disk cache (rebuild only if refill file changed)."""
            from api.routes.replenishments_recom import warm_replenishments_recom_cache

            warm = warm_replenishments_recom_cache(force_refresh=False)
            logger.info(
                "Replenishments page cache warm: ok=%s rows=%s elapsed=%ss",
                warm.get("ok"),
                warm.get("rows"),
                warm.get("elapsed_s"),
            )

        def _warm_transfers() -> None:
            """Serve Transfers tab from local blob_cache / api_cache parquet (no Azure wait)."""
            from api.routes.transfers_recom import warm_transfers_recom_cache

            warm = warm_transfers_recom_cache(force_refresh=False)
            logger.info(
                "Transfers page cache warm: ok=%s elapsed=%ss tables=%s",
                warm.get("ok"),
                warm.get("elapsed_s"),
                warm.get("tables"),
            )

        def _warm_store_managers() -> None:
            from api.routes.manager_performance import warm_managers_overview_cache

            warm = warm_managers_overview_cache(year=dash_year)
            logger.info("Store managers page_cache warm: %s", warm)

        def _warm_product_performance() -> None:
            from api.routes.product_performance import warm_product_performance_overview

            warm = warm_product_performance_overview(year=dash_year)
            logger.info("Product performance page_cache warm: %s", warm)

        def _warm_target_frames() -> None:
            target_vs_achieved.warm_target_vs_achieved_cache()

        def _warm_wh_demand() -> None:
            from api.routes import wh_sku_demand

            wh_sku_demand._compute_full_analysis()

        def _warm_dispatch_playbook() -> None:
            from api.routes import site_growth_playbook

            site_growth_playbook.warm_dispatch_playbook_cache("month")

        # Lower pages (Managers / Inventory / Product) must hit page_cache after deploy.
        # Run after critical so Home/Sales stay first, but ALWAYS on Azure (not heavy-only).
        deferred_steps.extend(
            [
                # Demo-critical: Replenishment + Transfer must be warm before Managers
                ("replenishments-recom", _warm_replenishments),
                ("transfers-recom", _warm_transfers),
                ("store-managers", _warm_store_managers),
                ("inventory-pricing", _warm_inventory_pricing),
                ("product-performance", _warm_product_performance),
                ("target-vs-achieved", _warm_target_frames),
                ("wh-sku-demand", _warm_wh_demand),
                ("dispatch-playbook", _warm_dispatch_playbook),
            ]
        )

        for name, fn in critical_steps:
            try:
                logger.info("Dashboard warm-up: %s …", name)
                fn()
            except Exception as exc:
                logger.warning("Dashboard warm-up %s failed: %s", name, exc)

        for name, fn in deferred_steps:
            try:
                logger.info("Dashboard warm-up (deferred): %s …", name)
                fn()
            except Exception as exc:
                logger.warning("Dashboard warm-up %s failed: %s", name, exc)

        if not heavy_warm:
            logger.info("Dashboard heavy warm-up skipped (DASHBOARD_HEAVY_WARM=0 / Azure default)")
            return

        # target / wh-demand / dispatch-playbook already in deferred (Azure too)
        heavy_steps: list[tuple[str, object]] = []

        def _warm_item_master() -> None:
            from pipeline.item_master import build_item_master_filters

            build_item_master_filters(force_refresh=False)

        def _warm_store_soh() -> None:
            from api.routes import store_soh_weekly

            store_soh_weekly.warm_store_soh_weekly_cache()

        heavy_steps.extend(
            [
                ("item-master-filters", _warm_item_master),
                ("store-soh-weekly", _warm_store_soh),
            ]
        )

        for name, fn in heavy_steps:
            try:
                logger.info("Dashboard warm-up (heavy): %s …", name)
                fn()
            except Exception as exc:
                logger.warning("Dashboard warm-up %s failed: %s", name, exc)

    def _run_daily_scheduler() -> None:
        try:
            from pipeline.daily_scheduler import start_daily_scheduler
            from pipeline.financial_week_sync import start_financial_week_sync

            start_financial_week_sync()
            start_daily_scheduler()
        except Exception as exc:
            logger.warning("Daily pipeline scheduler failed to start: %s", exc)

    threading.Thread(target=_sequential_warm, daemon=True, name="dashboard-warm").start()
    threading.Thread(
        target=_run_daily_scheduler,
        daemon=True,
        name="start-daily-scheduler",
    ).start()

print("[RELOAD TRIGGER] Dashboard All should be live.", flush=True)
print("[RELOAD TRIGGER 3] Forcing reload for 0_sales_soh_new fix.", flush=True)
print("[RELOAD TRIGGER 2] Final cleanup applied.", flush=True)
log = logging.getLogger(__name__)

print("\n" + "#"*40)
print("### BACKEND IS LIVE AND RELOADING! ###")
print("#"*40 + "\n", flush=True)
logging.critical("CRITICAL: BACKEND STARTUP IN PROGRESS")
 
# ---------- FRONTEND BUILD PATH ----------
BASE_DIR = Path(__file__).resolve().parent
DIST_DIR = BASE_DIR / "public"
 
# Mount Vite assets
if DIST_DIR.exists():
    assets_path = DIST_DIR / "assets"
    if assets_path.exists():
        app.mount("/assets", StaticFiles(directory=assets_path), name="assets")
 
# ---------- FALLBACK STRUCTURES ----------
_EMPTY_INVENTORY_SUMMARY = {
    "totalStockUnits": 0,
    "totalStockValue": 0,
    "totalUnitsSold": 0,
    "totalRevenue": 0,
    "totalUnitsPurchased": 0,
    "totalSkus": 0,
    "soldSkus": 0,
    "noSaleSkus": 0,
    "overallDaysOfSupply": 0,
    "dailyVelocity": 0,
    "sellThrough": 0,
    "needsReplenishCategories": 0,
    "overstockUnits": 0,
    "healthDistribution": {},
    "categoryCount": 0,
}
 
 
_PATH_FALLBACKS = {
    "/api/alerts": {"alerts": []},
    "/api/stores": {"stores": []},
    "/api/stores/regions": {"regions": ["All Regions"]},
    "/api/inventory": {"summary": _EMPTY_INVENTORY_SUMMARY, "categories": []},
    "/api/data-insights/overview": {"stores": 0, "cities": 0, "states": 0, "zones": 0, "uniqueSkus": 0, "barcodes": 0, "purchaseRecords": 0, "salesRecords": 0, "purchaseUnits": 0, "salesUnits": 0, "revenue": 0, "avgDiscount": 0},
    "/api/data-insights/quality-summary": {"returns": {"count": 0, "percentage": 0}, "negativeDiscount": {"count": 0, "percentage": 0}, "extremeDiscount": {"count": 0, "percentage": 0}, "validData": {"count": 0, "percentage": 0}, "missingZones": 0, "missingGrades": 0, "totalRecords": 0},
    "/api/data-insights/zone-distribution": {"zones": []},
    "/api/data-insights/season-distribution": {"seasons": []},
    "/api/data-insights/inventory-movement": {"totalSkus": 0, "soldSkus": 0, "neverSoldSkus": 0, "neverSoldPercentage": 0},
    "/api/data-insights/idle-time": {"distribution": [], "summary": {"slowMoving": 0, "slowMovingPct": 0, "deadStock": 0, "deadStockPct": 0, "staleStock": 0, "staleStockPct": 0}},
}
 
# ---------- LOGGING MIDDLEWARE ----------
# Master dropdown data changes with the morning file drop, not per request.
_BROWSER_CACHEABLE_PATHS = frozenset(
    {
        "/api/sales-dashboard/financial-calendar",
        "/api/sales-dashboard/customer-master-filters",
        "/api/sales-dashboard/item-master-filters",
    }
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    if request.url.path in _BROWSER_CACHEABLE_PATHS and response.status_code == 200:
        response.headers.setdefault("Cache-Control", "private, max-age=3600")
    logger.info(
        "%s %s %d %.0fms",
        request.method,
        request.url.path,
        response.status_code,
        (time.perf_counter() - started) * 1000,
    )
    return response

# ---------- ERROR HANDLER ----------
@app.exception_handler(Exception)
def handle_db_errors(request: Request, exc: Exception):
    path = request.url.path.split("?")[0]
    fallback = _PATH_FALLBACKS.get(path, {})
    
    # Capture error message to return to frontend
    err_msg = str(exc)
    is_capacity_error = "capacity has exceeded its limits" in err_msg.lower()
    is_fabric_cut = (
        "sql/dax connections are disabled" in err_msg.lower()
        or "onelake/fabric cut" in err_msg.lower()
        or "use api.blob_store" in err_msg.lower()
    )

    log.warning("API error for %s: %s", path, exc)

    # Blob mode: never surface Fabric/SQL cutover RuntimeError as a KPI "error" field
    if isinstance(fallback, dict):
        if is_fabric_cut:
            return JSONResponse(
                status_code=200,
                content={
                    **fallback,
                    "ok": False,
                    "detail": "Legacy SQL route disabled. Use /api/executive-overview or /api/sales-dashboard.",
                },
            )
        fallback["error"] = err_msg
        fallback["is_capacity_error"] = is_capacity_error

    return JSONResponse(status_code=200, content=fallback)
 
# ---------- GZIP ----------
# Filter lists / page JSON are 100-250 KB; gzip cuts them ~8x on the wire.
app.add_middleware(GZipMiddleware, minimum_size=1000)

# ---------- CORS ----------
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "*",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
 
# ---------- ROUTERS ----------
app.include_router(dashboard.router, prefix="/api", tags=["dashboard"])
app.include_router(stores.router, prefix="/api", tags=["stores"])
app.include_router(inventory.router, prefix="/api", tags=["inventory"])
app.include_router(analytics.router, prefix="/api", tags=["analytics"])
app.include_router(sellthrough.router, prefix="/api", tags=["sellthrough"])
app.include_router(seasonal.router, tags=["seasonal"])
app.include_router(alerts.router, prefix="/api", tags=["alerts"])
app.include_router(replenishment.router, prefix="/api", tags=["replenishment"])
app.include_router(targets.router, prefix="/api", tags=["targets"])
app.include_router(transfers.router, prefix="/api", tags=["transfers"])
app.include_router(data_insights.router, prefix="/api", tags=["data-insights"])
app.include_router(ai_planning.router, prefix="/api/ai", tags=["ai-planning"])
app.include_router(sales_kpi.router, prefix="/api", tags=["sales-kpi"])
app.include_router(retail_performance.router, prefix="/api", tags=["retail-performance"])
app.include_router(combined.router, tags=["combined"])
app.include_router(ai_seasonal.router, tags=["ai-seasonal"])
app.include_router(replenishments_recom.router, tags=["replenishments-recom"])
app.include_router(transfers_recom.router, tags=["transfers-recom"])
app.include_router(download_logs.router)
app.include_router(sales_dashboard.router)
app.include_router(stock_overview.router)
app.include_router(target_vs_achieved.router)
app.include_router(store_soh_weekly.router)
app.include_router(site_growth_playbook.router)
app.include_router(wh_soh_dept_insights.router)
app.include_router(wh_sku_demand.router)
app.include_router(focus_day_events.router)
app.include_router(product_performance.router)
app.include_router(executive_overview.router, prefix="/api/executive-overview", tags=["executive-overview"])
app.include_router(product_images.router, prefix="/api", tags=["product-images"])
app.include_router(manager_performance.router)
app.include_router(sales_analyzer.router)
app.include_router(dispatch_lead_time.router)
app.include_router(sku_breakeven.router)

# Route aliases for requests without /api prefix
@app.get("/inventory-analytics/sku-breakeven", tags=["Inventory Analytics - Aliases"])
def alias_sku_breakeven(
    search: str = "",
    risk_filter: str = "all",
    section: str = "",
    season: str = "",
    sort_by: str = "dispatch_gap_vs_be",
    sort_dir: str = "desc",
    page: int = 1,
    page_size: int = 50,
):
    return sku_breakeven.get_sku_breakeven_report(
        search=search,
        risk_filter=risk_filter,
        section=section,
        season=season,
        sort_by=sort_by,
        sort_dir=sort_dir,
        page=page,
        page_size=page_size,
    )

@app.get("/inventory-analytics/dispatch-lead-time", tags=["Inventory Analytics - Aliases"])
def alias_dispatch_lead_time(
    search: str = "",
    alert_filter: str = "all",
    section: str = "",
    season: str = "",
    sort_by: str = "inward_date",
    sort_dir: str = "desc",
    page: int = 1,
    page_size: int = 50,
):
    return dispatch_lead_time.get_dispatch_lead_time_report(
        search=search,
        alert_filter=alert_filter,
        section=section,
        season=season,
        sort_by=sort_by,
        sort_dir=sort_dir,
        page=page,
        page_size=page_size,
    )

@app.get("/inventory-analytics/sku-demand-vs-wh-stock", tags=["Inventory Analytics - Aliases"])
def alias_wh_sku_demand(
    search: str = "",
    department: str = "",
    section: str = "",
    status: str = "all",
    limit: int = 500,
    show: str = "all",
):
    return wh_sku_demand.get_sku_demand_vs_wh_stock(
        search=search,
        department=department,
        section=section,
        status=status,
        limit=limit,
        show=show,
    )

@app.get("/focus-day/festival-comparison", tags=["Focus Day - Aliases"])
def alias_festival_comparison(force_refresh: bool = False):
    return focus_day_events.get_festival_comparison(force_refresh=force_refresh)


@app.get("/api/financial-calendar", tags=["Calendar - Aliases"])
@app.get("/api/sales-dashboard/financial-calendar", tags=["Calendar - Aliases"])
@app.get("/financial-calendar", tags=["Calendar - Aliases"])
def alias_financial_calendar():
    from api.financial_calendar import calendar_options
    return {"ok": True, **calendar_options()}


# Zinc local pipeline (combine → refill → transfer)
app.include_router(pipeline.router)
app.include_router(upload.router)
 
# ---------- HEALTH ----------
@app.get("/api/health")
def health():
    import os

    return {
        "status": "ok",
        "backend": "live",
        "azure": bool(os.environ.get("WEBSITE_SITE_NAME")),
        "reload_time": str(__import__("datetime").datetime.now()),
        "v": 2,
    }


@app.get("/health")
def health_root():
    """Azure App Service probe (WEBSITES_HEALTHCHECK_PATH)."""
    return {"status": "ok"}

@app.get("/api/ping")
def ping():
    print("[DIAGNOSTIC] /api/ping was hit!", flush=True)
    return "pong"

@app.get("/api/debug/db-status")
def debug_db_status():
    """Diagnostic endpoint: Blob (default) or legacy Power BI status."""
    import traceback
    from api.config import DATABASE_BACKEND
    backend = DATABASE_BACKEND.lower()
    result = {"backend": backend, "ok": False, "error": None, "detail": None}
    try:
        if backend in ("blob", "azure_blob", "storage"):
            from api.blob_store import BlobJsonStore
            ping = BlobJsonStore().ping()
            result["ok"] = bool(ping.get("ok"))
            result["detail"] = ping
        elif backend == "powerbi":
            from api.db_powerbi import execute_dax
            rows = execute_dax("EVALUATE ROW(\"test\", 1)")
            result["ok"] = bool(rows and len(rows) > 0)
            result["detail"] = "Power BI Execute Queries API succeeded" if result["ok"] else "No rows returned"
        elif backend == "fabric_sql":
            result["error"] = "Fabric SQL disabled — use DATABASE_BACKEND=blob"
            result["detail"] = "OneLake/Fabric cutover complete; use Azure Blob Storage"
        else:
            result["error"] = f"Unknown DATABASE_BACKEND={backend}"
    except Exception as e:
        result["error"] = str(e)
        result["detail"] = traceback.format_exc()
        logger.exception("DB Status check failed")
    try:
        from api.azure_sql import ping_azure_sql

        result["azure_sql"] = ping_azure_sql()
    except Exception as sql_exc:
        result["azure_sql"] = {"ok": False, "error": str(sql_exc)}
    return result
 
# ---------- FRONTEND ----------
@app.get("/")
def serve_react():
    """Serve React frontend"""
    if DIST_DIR.exists():
        return FileResponse(DIST_DIR / "index.html")
    return {"message": "Frontend build not found"}
 
# ---------- SPA ROUTING ----------
@app.get("/{full_path:path}")
def serve_spa(full_path: str):
    """React Router fallback — never swallow /api (return JSON 404 instead of index.html)."""
    if full_path == "api" or full_path.startswith("api/"):
        return JSONResponse(
            status_code=404,
            content={"detail": f"API route not found: /{full_path}"},
        )

    file_path = DIST_DIR / full_path

    if file_path.exists():
        return FileResponse(file_path)

    fallback_index = DIST_DIR / "index.html"
    if fallback_index.exists():
        return FileResponse(fallback_index)

    return {"status": "error", "message": f"Path '{full_path}' not found and frontend build is missing."}
# Reload timestamp: 2026-09-20T17:37:30
