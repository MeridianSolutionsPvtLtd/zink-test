"""Saved Home and Sales Dashboard responses.

A repeat open returns this file instead of scanning sales again.
refresh=1 on the sales dashboard skips the file and writes a new one.
"""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import logging
import os
import threading
import time
import traceback
from collections import OrderedDict
from pathlib import Path
from typing import Any

_REBUILD = contextvars.ContextVar("page_cache_rebuild", default=False)
# Manual Home/Sales refresh must mirror blob immediately — App Service has multiple
# workers; async upload left siblings on yesterday's JSON for up to 5 minutes.
_SYNC_BLOB = contextvars.ContextVar("page_cache_sync_blob", default=False)

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "api_cache" / "page_cache"
_BLOB_PREFIX = "cache/page-response/"
_DAILY_STAMP_BLOB = f"{_BLOB_PREFIX}_daily_stamp.json"

# After DNS/network failure, skip Azure for a while so Home/local stays snappy.
_BLOB_CIRCUIT_OPEN_UNTIL = 0.0
_BLOB_CIRCUIT_COOLDOWN_S = float(os.environ.get("PAGE_CACHE_BLOB_COOLDOWN_S", "600") or "600")

# Hosted used to keep forever-stale local JSON even after morning blob publish.
# Re-check blob etag at most every N seconds (default 5 min) so daily warm shows up.
_BLOB_REFRESH_S = float(os.environ.get("PAGE_CACHE_BLOB_REFRESH_S", "300") or "300")
_BLOB_CHECK_AT: dict[str, float] = {}

# Bounded process RAM for page JSON — avoids repeated disk/blob on filter revisits.
# Invalidated when blob content changes or daily stamp advances.
_PAGE_RAM_MAX = max(8, int(os.environ.get("PAGE_CACHE_RAM_MAX", "64") or "64"))
_PAGE_RAM: OrderedDict[str, dict[str, Any]] = OrderedDict()
_PAGE_RAM_LOCK = threading.Lock()
_PAGE_RAM_STAMP: str | None = None

# Per-path locks so concurrent save + blob-pull do not race on Windows
# (WinError 5 Access denied on os.replace of the same .json).
_PATH_LOCKS: dict[str, threading.Lock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _path_lock(path: Path) -> threading.Lock:
    try:
        key = str(path.resolve())
    except Exception:
        key = str(path)
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _PATH_LOCKS[key] = lock
        return lock


def _atomic_write_text(path: Path, text: str) -> None:
    """Write JSON/text atomically; retry replace on Windows file locks."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(
        path.suffix + f".tmp.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}"
    )
    tmp.write_text(text, encoding="utf-8")
    last_exc: Exception | None = None
    with _path_lock(path):
        for attempt in range(10):
            try:
                os.replace(tmp, path)
                return
            except PermissionError as exc:
                last_exc = exc
            except OSError as exc:
                winerr = getattr(exc, "winerror", None)
                if winerr == 5 or exc.errno in (11, 13):
                    last_exc = exc
                else:
                    try:
                        tmp.unlink(missing_ok=True)
                    except Exception:
                        pass
                    raise
            time.sleep(0.03 * (attempt + 1))
        # Last resort: direct write (still under path lock).
        try:
            path.write_text(text, encoding="utf-8")
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            return
        except Exception as exc:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            raise last_exc or exc


def _local_daily_stamp_token() -> str:
    path = _CACHE_DIR / "_daily_stamp.json"
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()[:240]
    except Exception:
        return ""


_STAMP_CHECK_AT = 0.0


def _sync_daily_stamp_from_blob() -> bool:
    """Pull morning warm stamp from Azure when local is missing/behind.

    Hosted App Service kept yesterday's Home JSON because RAM only looked at
    the *local* stamp file — which was never refreshed from blob after warm.
    Returns True when the local stamp text changed (caller should drop RAM).
    """
    global _STAMP_CHECK_AT
    if not _blob_sync_enabled():
        return False
    now = time.time()
    # Cheap throttle — stamp HEAD at most every PAGE_CACHE_BLOB_REFRESH_S.
    if (now - _STAMP_CHECK_AT) < max(30.0, _BLOB_REFRESH_S):
        return False
    _STAMP_CHECK_AT = now
    path = _CACHE_DIR / "_daily_stamp.json"
    before = _local_daily_stamp_token()
    try:
        from api.routes.sales_dashboard import _blob_store

        store = _blob_store()
        if store is None:
            return False
        remote_etag = store._blob_etag(_DAILY_STAMP_BLOB)
        if not remote_etag:
            return False
        etag_path = Path(str(path) + ".etag")
        local_etag = ""
        if etag_path.exists():
            try:
                local_etag = etag_path.read_text(encoding="utf-8").strip()
            except Exception:
                local_etag = ""
        if local_etag and local_etag == remote_etag and path.exists():
            return False
        remote = store._ensure_local_file(_DAILY_STAMP_BLOB)
        if remote is None or not remote.exists() or remote.stat().st_size <= 0:
            return False
        text = remote.read_text(encoding="utf-8")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
        etag_src = Path(str(remote) + ".etag")
        if etag_src.exists():
            etag_path.write_text(etag_src.read_text(encoding="utf-8"), encoding="utf-8")
        after = text.strip()[:240]
        changed = after != before
        if changed:
            logger.info("Daily page-cache stamp refreshed from blob")
        return changed
    except Exception as exc:
        _trip_blob_circuit(exc)
        return False


def _page_ram_get(cache_id: str) -> dict | None:
    global _PAGE_RAM_STAMP
    # Hosted: sync morning stamp before reading RAM so Home picks up new warm.
    if _sync_daily_stamp_from_blob():
        _invalidate_in_memory_page_caches()
    stamp = _local_daily_stamp_token()
    with _PAGE_RAM_LOCK:
        if stamp and _PAGE_RAM_STAMP is not None and stamp != _PAGE_RAM_STAMP:
            _PAGE_RAM.clear()
            _PAGE_RAM_STAMP = stamp
            return None
        if _PAGE_RAM_STAMP is None and stamp:
            _PAGE_RAM_STAMP = stamp
        hit = _PAGE_RAM.get(cache_id)
        if hit is None:
            return None
        _PAGE_RAM.move_to_end(cache_id)
        return hit


def _page_ram_put(cache_id: str, payload: dict) -> None:
    global _PAGE_RAM_STAMP
    if not isinstance(payload, dict):
        return
    stamp = _local_daily_stamp_token()
    with _PAGE_RAM_LOCK:
        if stamp:
            _PAGE_RAM_STAMP = stamp
        _PAGE_RAM[cache_id] = payload
        _PAGE_RAM.move_to_end(cache_id)
        while len(_PAGE_RAM) > _PAGE_RAM_MAX:
            _PAGE_RAM.popitem(last=False)


def _page_ram_invalidate(cache_id: str | None = None) -> None:
    with _PAGE_RAM_LOCK:
        if cache_id is None:
            _PAGE_RAM.clear()
        else:
            _PAGE_RAM.pop(cache_id, None)


def _blob_sync_enabled() -> bool:
    """Local offline / explicit opt-out / open circuit → never wait on Azure."""
    flag = (os.environ.get("PAGE_CACHE_BLOB") or "1").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return False
    if (os.environ.get("STOCKPULSE_OFFLINE") or "").strip().lower() in ("1", "true", "yes"):
        return False
    return time.time() >= _BLOB_CIRCUIT_OPEN_UNTIL


def _trip_blob_circuit(exc: BaseException | None = None) -> None:
    global _BLOB_CIRCUIT_OPEN_UNTIL
    _BLOB_CIRCUIT_OPEN_UNTIL = time.time() + max(30.0, _BLOB_CIRCUIT_COOLDOWN_S)
    if exc is not None:
        logger.warning(
            "Page-cache blob circuit open for %.0fs after: %s",
            _BLOB_CIRCUIT_COOLDOWN_S,
            exc,
        )
    try:
        from api.routes.sales_dashboard import mark_blob_store_unavailable

        mark_blob_store_unavailable()
    except Exception:
        pass


def _digest(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


def _path(namespace: str, key: str) -> Path:
    return _CACHE_DIR / namespace / f"{_digest(key)}.json"


def _blob_name(namespace: str, key: str) -> str:
    return f"{_BLOB_PREFIX}{namespace}/{_digest(key)}.json"


def _json_default(value: Any) -> Any:
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    raise TypeError(f"Not JSON serializable: {type(value)!r}")


def rebuilding_page_cache() -> bool:
    return bool(_REBUILD.get())


def syncing_page_cache_blob() -> bool:
    return bool(_SYNC_BLOB.get()) or rebuilding_page_cache()


@contextlib.contextmanager
def force_page_cache_blob_sync():
    """Sync Azure page-cache upload for this request (Home refresh=1)."""
    token = _SYNC_BLOB.set(True)
    try:
        yield
    finally:
        _SYNC_BLOB.reset(token)


def _read_local_page_cache(path: Path) -> dict | None:
    if not path.exists() or path.stat().st_size <= 0:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except Exception as exc:
        logger.warning("Page cache read failed for %s: %s", path, exc)
        return None


def _page_etag_path(path: Path) -> Path:
    return Path(str(path) + ".etag")


def _should_recheck_blob(cache_key: str) -> bool:
    """Throttle blob HEAD/etag checks so Home stays snappy."""
    now = time.time()
    last = _BLOB_CHECK_AT.get(cache_key, 0.0)
    if (now - last) < max(30.0, _BLOB_REFRESH_S):
        return False
    _BLOB_CHECK_AT[cache_key] = now
    return True


def _invalidate_in_memory_page_caches() -> None:
    """Drop RAM replies so a freshly pulled blob is what the next request returns."""
    _page_ram_invalidate(None)
    try:
        from api.routes.executive_overview import _EXEC_CACHE, _HOME_SALES_CACHE

        _EXEC_CACHE.clear()
        _HOME_SALES_CACHE.clear()
    except Exception:
        pass
    try:
        from api.routes.sales_dashboard import _DASH_RESPONSE_CACHE, _FY_COMP_CACHE

        _DASH_RESPONSE_CACHE.clear()
        _FY_COMP_CACHE.clear()
    except Exception:
        pass


def _pull_blob_into_page_path(
    namespace: str,
    key: str,
    path: Path,
    *,
    local_payload: dict | None = None,
    timing_out: dict[str, float] | None = None,
) -> dict | None:
    """Etag-aware blob pull into the page_cache JSON path. Returns payload if usable.

    When a valid local payload already exists, only a cheap properties/etag check
    runs — full download is skipped unless the remote etag changed.
    """
    from api.routes.sales_dashboard import _blob_store

    store = _blob_store()
    if store is None:
        return None
    blob = _blob_name(namespace, key)
    etag_path = _page_etag_path(path)
    local_etag = ""
    if etag_path.exists():
        try:
            local_etag = etag_path.read_text(encoding="utf-8").strip()
        except Exception:
            local_etag = ""

    # Fast path: local JSON valid + remote etag unchanged → no download.
    if local_payload is not None and local_etag:
        t_meta = time.perf_counter()
        try:
            remote_etag = store._blob_etag(blob)
        except Exception:
            remote_etag = None
        if timing_out is not None:
            timing_out["blob_meta_ms"] = round(
                float(timing_out.get("blob_meta_ms") or 0.0)
                + (time.perf_counter() - t_meta) * 1000.0,
                1,
            )
        if remote_etag and remote_etag == local_etag:
            return local_payload

    # Prefer a fresher local write (e.g. Home refresh=1) over an older blob mirror
    # that has not finished uploading / has not been replaced yet.
    if local_payload is not None and path.exists():
        try:
            props = store.container.get_blob_client(blob).get_blob_properties()
            remote_mod = getattr(props, "last_modified", None)
            if remote_mod is not None and path.stat().st_mtime > (remote_mod.timestamp() + 1.0):
                logger.info(
                    "Keeping fresher local page_cache %s (local ahead of blob)",
                    namespace,
                )
                return local_payload
        except Exception:
            pass

    remote = store._ensure_local_file(blob)
    if remote is None or not remote.exists() or remote.stat().st_size <= 0:
        return local_payload
    text = remote.read_text(encoding="utf-8")
    payload = json.loads(text)
    if not isinstance(payload, dict):
        return local_payload
    if _page_cache_stale(namespace, payload):
        logger.warning("Ignoring stale blob page_cache %s (empty SOH/dispatch)", namespace)
        return local_payload
    path.parent.mkdir(parents=True, exist_ok=True)
    # Only rewrite page_cache file when content actually changed.
    local_same = False
    if path.exists() and path.stat().st_size > 0:
        try:
            local_same = path.read_text(encoding="utf-8") == text
        except Exception:
            local_same = False
    if not local_same:
        _atomic_write_text(path, text)
        etag_src = Path(str(remote) + ".etag")
        if etag_src.exists():
            try:
                _page_etag_path(path).write_text(etag_src.read_text(encoding="utf-8"), encoding="utf-8")
            except Exception:
                pass
        _invalidate_in_memory_page_caches()
        logger.info("Page cache refreshed from blob: %s/%s", namespace, path.name)
    return payload


def publish_daily_page_cache_stamp(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Write a blob marker after morning warm so hosted can prove caches are today's."""
    from datetime import datetime, timezone

    stamp = {
        "ok": True,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "source": "warm_default_page_caches",
    }
    if extra:
        stamp.update(extra)
    path = _CACHE_DIR / "_daily_stamp.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(stamp, default=_json_default), encoding="utf-8")
    except Exception as exc:
        return {"ok": False, "error": f"local write: {exc}"}
    if not _blob_sync_enabled():
        return {"ok": True, "published": False, "local": True, "stamp": stamp}
    try:
        from api.blob_store import upload_local_file_with_retry
        from api.routes.sales_dashboard import _blob_store

        store = _blob_store()
        if store is None:
            return {"ok": True, "published": False, "stamp": stamp}
        upload_local_file_with_retry(
            store.container,
            blob_name=_DAILY_STAMP_BLOB,
            path=path,
            content_type="application/json",
            attempts=3,
            timeout_s=60,
        )
        return {"ok": True, "published": True, "blob": _DAILY_STAMP_BLOB, "stamp": stamp}
    except Exception as exc:
        _trip_blob_circuit(exc)
        return {"ok": False, "error": str(exc), "stamp": stamp}


def _analyzer_metrics_empty(payload: dict | None) -> bool:
    """Stale Analyzer warm: sales present but SOH/dispatch blank when those measures were requested."""
    if not isinstance(payload, dict):
        return False
    metrics = ((payload.get("grand_total") or {}).get("metrics") or {})
    try:
        sales_n = float(metrics.get("sales") or 0)
    except (TypeError, ValueError):
        sales_n = 0.0
    if sales_n <= 0:
        return False
    # Sales-only pivots omit SOH keys — that is not a stale warm.
    measure_fields = {
        str((m or {}).get("field") or "").strip()
        for m in (payload.get("measures") or [])
        if isinstance(m, dict)
    }
    wants_soh = bool(measure_fields & {"current_soh", "dispatch_qty", "sell_through_pct"})
    if not wants_soh and "current_soh" not in metrics and "dispatch_qty" not in metrics:
        return False
    soh = metrics.get("current_soh")
    disp = metrics.get("dispatch_qty")
    # Stale warm = measure keys missing / blank strings (hosted empty SOH after cold start).
    # Numeric 0 is a legitimate business result (store sold through) and must remain cacheable.
    if wants_soh and soh is None:
        return True
    if wants_soh and soh == "" and disp in (None, ""):
        return True
    return False


def _managers_soh_empty(payload: dict | None) -> bool:
    """Stale Managers warm: net sales present but network SOH/dispatch all zero."""
    if not isinstance(payload, dict):
        return False
    summary = payload.get("summary") or {}
    try:
        net = float(summary.get("total_net_sales") or 0)
        soh = float(summary.get("total_soh") or 0)
        disp = float(summary.get("total_dispatch_qty") or 0)
    except (TypeError, ValueError):
        return False
    return net > 0 and soh <= 0 and disp <= 0


def _product_performance_soh_empty(payload: dict | None) -> bool:
    """Stale Product Performance warm: sales rows present but WH + On-Floor all zero."""
    if not isinstance(payload, dict):
        return False
    rows = payload.get("rows") or []
    if len(rows) < 100:
        return False
    sample = rows[:40]
    try:
        has_sales = any(float(r.get("total_net_sales") or r.get("offline_net_amt") or 0) > 0 for r in sample)
        has_soh = any(
            int(r.get("ho_soh_qty") or 0) > 0 or int(r.get("floor_soh_qty") or 0) > 0 for r in sample
        )
    except (TypeError, ValueError):
        return False
    return has_sales and not has_soh


def _page_cache_stale(namespace: str, payload: dict | None) -> bool:
    if namespace == "sales-analyzer":
        return _analyzer_metrics_empty(payload)
    if namespace == "store-managers":
        if _managers_soh_empty(payload):
            return True
        # Pre-v15 caches omit zone_summary — Zone Analysis must not invent KPIs on UI.
        if not isinstance((payload or {}).get("zone_summary"), list):
            return True
        return False
    if namespace == "product-performance":
        return _product_performance_soh_empty(payload)
    if namespace == "dispatch-lead-time":
        from api.routes.dispatch_lead_time import dispatch_page_cache_stale

        return dispatch_page_cache_stale(payload)
    return False


def load_page_cache(
    namespace: str,
    key: str,
    *,
    timing_out: dict[str, float] | None = None,
) -> dict | None:
    """Load page cache: RAM → local disk, with throttled etag recheck against blob.

    Previously Azure kept forever-stale local JSON after the first hit, so Home
    stayed on yesterday's numbers until App Service recycled. Now we etag-check
    blob every PAGE_CACHE_BLOB_REFRESH_S seconds (default 300) — but only via
    cheap properties when local JSON already exists (no full download).

    If timing_out is provided, fills memory / disk / blob / blob_meta ms fields.
    """
    path = _path(namespace, key)
    cache_id = f"{namespace}:{_digest(key)}"

    t_mem = time.perf_counter()
    ram_hit = _page_ram_get(cache_id)
    mem_ms = round((time.perf_counter() - t_mem) * 1000.0, 1)
    if timing_out is not None:
        timing_out["page_memory_lookup_ms"] = mem_ms

    # Valid RAM hit: skip disk. Blob recheck still allowed on TTL so morning warm lands.
    local_payload = ram_hit
    disk_ms = 0.0
    if local_payload is None:
        t_disk = time.perf_counter()
        local_payload = _read_local_page_cache(path)
        disk_ms = round((time.perf_counter() - t_disk) * 1000.0, 1)
    if local_payload is not None and _page_cache_stale(namespace, local_payload):
        logger.warning("Ignoring stale local page_cache %s (empty SOH/dispatch)", namespace)
        local_payload = None
        _page_ram_invalidate(cache_id)

    blob_ms = 0.0
    if not _blob_sync_enabled():
        if local_payload is not None:
            _page_ram_put(cache_id, local_payload)
        if timing_out is not None:
            timing_out["local_disk_lookup_ms"] = disk_ms
            timing_out["blob_lookup_ms"] = blob_ms
            timing_out.setdefault("blob_meta_ms", 0.0)
        return local_payload

    need_blob = local_payload is None or _should_recheck_blob(cache_id)
    if not need_blob:
        if local_payload is not None:
            _page_ram_put(cache_id, local_payload)
        if timing_out is not None:
            timing_out["local_disk_lookup_ms"] = disk_ms
            timing_out["blob_lookup_ms"] = blob_ms
            timing_out.setdefault("blob_meta_ms", 0.0)
        return local_payload

    t_blob = time.perf_counter()
    try:
        pulled = _pull_blob_into_page_path(
            namespace,
            key,
            path,
            local_payload=local_payload,
            timing_out=timing_out,
        )
        blob_ms = round((time.perf_counter() - t_blob) * 1000.0, 1)
        result = pulled if pulled is not None else local_payload
        if result is not None:
            _page_ram_put(cache_id, result)
        if timing_out is not None:
            timing_out["local_disk_lookup_ms"] = disk_ms
            timing_out["blob_lookup_ms"] = blob_ms
            timing_out.setdefault("blob_meta_ms", 0.0)
        return result
    except Exception as exc:
        blob_ms = round((time.perf_counter() - t_blob) * 1000.0, 1)
        if local_payload is not None:
            _page_ram_put(cache_id, local_payload)
        if timing_out is not None:
            timing_out["local_disk_lookup_ms"] = disk_ms
            timing_out["blob_lookup_ms"] = blob_ms
            timing_out.setdefault("blob_meta_ms", 0.0)
        _trip_blob_circuit(exc)
        return local_payload


def _upload_page_cache_blob(namespace: str, key: str, path: Path) -> None:
    """Best-effort Azure mirror — never runs on the request thread."""
    if not _blob_sync_enabled():
        return
    try:
        from api.blob_store import upload_local_file_with_retry
        from api.routes.sales_dashboard import _blob_store

        store = _blob_store()
        if store is None:
            return
        upload_local_file_with_retry(
            store.container,
            blob_name=_blob_name(namespace, key),
            path=path,
            content_type="application/json",
            attempts=2,
            timeout_s=30,
        )
    except Exception as exc:
        _trip_blob_circuit(exc)


def save_page_cache(namespace: str, key: str, payload: dict) -> None:
    if not isinstance(payload, dict) or payload.get("ok") is False:
        return
    # Never persist warms that have sales but blank SOH/dispatch — hosted
    # used to overwrite a good blob cache with an empty-SOH payload after cold start.
    if _page_cache_stale(namespace, payload):
        logger.warning("Skip saving %s page_cache with empty SOH/dispatch", namespace)
        return
    path = _path(namespace, key)
    cache_id = f"{namespace}:{_digest(key)}"
    try:
        text = json.dumps(payload, default=_json_default)
        _atomic_write_text(path, text)
        _page_ram_put(cache_id, payload)
    except Exception as exc:
        logger.warning("Page cache write failed for %s: %s", namespace, exc)
        # Still keep RAM so this process serves fast even if disk rename failed.
        try:
            _page_ram_put(cache_id, payload)
        except Exception:
            pass
        return
    # Local disk is enough for the live response. Blob mirror must not block
    # Analyzer/Home (Azure upload was hanging Manager pivots for 30–120s → FE error).
    if not _blob_sync_enabled():
        return
    # Morning warm / manual refresh must finish the blob upload before returning
    # — otherwise other App Service workers keep serving the prior JSON.
    if syncing_page_cache_blob():
        _upload_page_cache_blob(namespace, key, path)
        return
    threading.Thread(
        target=_upload_page_cache_blob,
        args=(namespace, key, path),
        name=f"page-cache-blob-{namespace}",
        daemon=True,
    ).start()


def _frontend_request(query: str):
    """Same query string the Home / Sales pages send on first open."""
    from starlette.requests import Request

    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/",
            "raw_path": b"/",
            "query_string": query.encode("utf-8"),
            "headers": [],
            "client": ("127.0.0.1", 0),
            "server": ("127.0.0.1", 80),
        }
    )


def warm_default_page_caches() -> dict[str, Any]:
    """Rebuild default Home, Sales, Analyzer, and Managers replies after the 9 AM daily run."""
    import time

    from api.routes.executive_overview import (
        _EXEC_CACHE,
        _HOME_SALES_CACHE,
        get_executive_breakdowns,
        get_executive_summary,
        get_multi_year_trend,
        get_target_vs_achievement,
    )
    from api.routes.dispatch_lead_time import (
        get_dispatch_lead_time_report,
        publish_dispatch_lead_time_to_blob,
    )
    from api.routes.focus_day_events import warm_festival_comparison_cache
    from api.routes.manager_performance import warm_managers_overview_cache
    from api.routes.product_performance import warm_product_performance_overview
    from api.routes.replenishments_recom import warm_replenishments_recom_cache
    from api.routes.sku_breakeven import get_sku_breakeven_report, publish_sku_breakeven_to_blob
    from api.routes.stock_overview import get_stock_overview
    from api.routes.sales_analyzer import (
        AnalyzerQueryRequest,
        SortConfig,
        ValueConfig,
        execute_analyzer_query,
        _get_prepared_soh_df,
        publish_prepared_analyzer_caches_to_blob,
        warm_analyzer_prepared_frames,
    )
    from api.routes.sales_dashboard import (
        _DASH_RESPONSE_CACHE,
        get_financial_year_comparison,
        get_sales_dashboard,
        get_sales_pivot,
        warm_sales_overview_progressive_caches,
    )
    from api.routes.wh_sku_demand import _compute_full_analysis as _warm_wh_sku_demand_analysis
    from api.routes.target_vs_achieved import get_target_vs_achieved

    try:
        from api import sales_year_loader

        sales_year_loader._YEAR_CACHE.clear()
    except Exception:
        pass
    _HOME_SALES_CACHE.clear()
    _EXEC_CACHE.clear()
    _DASH_RESPONSE_CACHE.clear()

    from api.sales_year_loader import (
        analyzer_build_years,
        current_indian_fy_date_range,
        fy_token,
        get_default_dashboard_year,
    )

    dash_year = int(get_default_dashboard_year())
    fy_label = fy_token(dash_year)
    fy_from, fy_to = current_indian_fy_date_range()
    # Analyzer prepared frames only (Option B): cur-2..cur. Prefer recent first.
    warm_years = sorted(analyzer_build_years(), reverse=True)

    home_query = f"year={dash_year}&timeline_mode=fy_year&timeline_values={fy_label}"
    target_query = home_query + "&group_by=zone"
    steps: dict[str, Any] = {}
    token = _REBUILD.set(True)
    started = time.perf_counter()
    try:
        # Disk/RAM prepared frames first so Analyzer page warm is a hit, not a rebuild.
        # Daily auto_daily_run force-rebuilds + publishes via refresh_analyzer_prepared_caches
        # before this; here we only ensure RAM/disk are warm (no force).
        try:
            for y in warm_years:
                warm_analyzer_prepared_frames(year=int(y), force_refresh=False)
            steps["prepared_frames"] = {
                "ok": True,
                "years": warm_years,
                "seconds": round(time.perf_counter() - started, 1),
            }
        except Exception as prep_exc:
            logger.exception(
                "Prepared analyzer frames warm failed years=%s",
                warm_years,
            )
            steps["prepared_frames"] = {
                "ok": False,
                "error": str(prep_exc),
                "traceback": traceback.format_exc(),
                "years": warm_years,
            }

        def _analyzer_warm_req(
            *,
            rows: list[str],
            filters: dict[str, Any],
            year: int = dash_year,
        ) -> AnalyzerQueryRequest:
            return AnalyzerQueryRequest(
                rows=rows,
                columns=[],
                values=[
                    ValueConfig(field="sales", aggregation="sum"),
                    ValueConfig(field="sales_ly", aggregation="sum"),
                    ValueConfig(field="sales_growth_pct", aggregation="calculated"),
                    ValueConfig(field="dispatch_qty", aggregation="sum"),
                    ValueConfig(field="quantity", aggregation="sum"),
                    ValueConfig(field="current_soh", aggregation="sum"),
                    ValueConfig(field="sell_through_pct", aggregation="calculated"),
                    ValueConfig(field="asp", aggregation="avg"),
                ],
                filters=filters,
                sort=[SortConfig(field="sales", direction="desc")],
                year=year,
            )

        def _run_analyzer_warm(step_name: str, req: AnalyzerQueryRequest):
            """Execute analyzer warm with full exception + request payload logging."""
            try:
                return execute_analyzer_query(req)
            except Exception:
                payload = {
                    "rows": list(req.rows or []),
                    "columns": list(req.columns or []),
                    "values": [
                        v.model_dump() if hasattr(v, "model_dump") else dict(v)
                        for v in (req.values or [])
                    ],
                    "filters": dict(req.filters or {}),
                    "year": req.year,
                }
                logger.exception(
                    "Page cache %s failed payload=%s",
                    step_name,
                    payload,
                )
                raise

        jobs = (
            (
                # FE Sales Overview progressive APIs — must match /kpis|/insights|/rows keys.
                "sales_overview_progressive",
                lambda: warm_sales_overview_progressive_caches(
                    year=dash_year,
                    fy_label=fy_label,
                    force_refresh=True,
                ),
            ),
            (
                "sales_dashboard",
                lambda: get_sales_dashboard(
                    year=dash_year,
                    site_id="all",
                    site_ids="",
                    channel="all",
                    month="all",
                    sku="all",
                    season="all",
                    barcode="all",
                    barcodes="",
                    departments="",
                    sections="",
                    zones="",
                    store_grades="",
                    states="",
                    cities="",
                    search="",
                    timeline_mode="fy_year",
                    timeline_values=fy_label,
                    page=1,
                    page_size=50,
                    refresh=1,
                ),
            ),
            ("home_summary", lambda: get_executive_summary(_frontend_request(home_query), year=dash_year, zone="all", channel="all", site_ids="", season="")),
            ("home_trend", lambda: get_multi_year_trend(_frontend_request(home_query), year=dash_year)),
            ("home_breakdowns", lambda: get_executive_breakdowns(_frontend_request(home_query), year=dash_year, zone="all", channel="all", site_ids="", season="")),
            ("home_target", lambda: get_target_vs_achievement(_frontend_request(target_query), year=dash_year, group_by="zone", zone="all", channel="all", site_ids="")),
            (
                "fy_comparison",
                lambda: get_financial_year_comparison(
                    filters=(
                        f'{{"year":"{dash_year}","channel":"all","site_id":"all","month":"all","sku":"all",'
                        f'"season":"all","search":"","timeline_mode":"fy_year","timeline_values":"{fy_label}"}}'
                    ),
                    site_id="all",
                    site_ids="",
                    source="all",
                    departments="",
                    sections="",
                    seasons="",
                    period_mode="fy",
                ),
            ),
            (
                "sales_pivot",
                lambda: get_sales_pivot(
                    year=dash_year,
                    site_id="all",
                    site_ids="",
                    channel="all",
                    month="all",
                    sku="all",
                    season="all",
                    barcode="all",
                    barcodes="",
                    departments="",
                    sections="",
                    search="",
                    timeline_mode="fy_year",
                    timeline_values=fy_label,
                    primary_dim="zone",
                    secondary_dim="none",
                    tertiary_dim="none",
                    column_split="none",
                    refresh=1,
                ),
            ),
            (
                "sales_analyzer",
                lambda: _run_analyzer_warm(
                    "sales_analyzer",
                    _analyzer_warm_req(
                        rows=["zone", "sales_channel"],
                        filters={"timeline_mode": "fy_year", "timeline_values": fy_label},
                    ),
                ),
            ),
            (
                "sales_analyzer_default_filters",
                # Standalone Analyzer opens with empty filters — different cache key
                lambda: _run_analyzer_warm(
                    "sales_analyzer_default_filters",
                    _analyzer_warm_req(rows=["zone", "sales_channel"], filters={}),
                ),
            ),
            (
                "sales_analyzer_by_store",
                # Store (Code - Name) — common heavy cut; pre-warm so UI does not hang.
                lambda: _run_analyzer_warm(
                    "sales_analyzer_by_store",
                    _analyzer_warm_req(
                        rows=["site_id_name"],
                        filters={"timeline_mode": "fy_year", "timeline_values": fy_label},
                    ),
                ),
            ),
            (
                "sales_analyzer_by_store_empty",
                # Standalone Analyzer often has empty filters when user picks Store hierarchy.
                lambda: _run_analyzer_warm(
                    "sales_analyzer_by_store_empty",
                    _analyzer_warm_req(rows=["site_id_name"], filters={}),
                ),
            ),
            (
                "sales_analyzer_zone_store",
                lambda: _run_analyzer_warm(
                    "sales_analyzer_zone_store",
                    _analyzer_warm_req(
                        rows=["zone", "site_id_name"],
                        filters={"timeline_mode": "fy_year", "timeline_values": fy_label},
                    ),
                ),
            ),
            (
                "sales_analyzer_zone_channel_store",
                # Zone → Channel → Store (Code - Name) — the drill path that previously 503'd.
                lambda: _run_analyzer_warm(
                    "sales_analyzer_zone_channel_store",
                    _analyzer_warm_req(
                        rows=["zone", "sales_channel", "site_id_name"],
                        filters={"timeline_mode": "fy_year", "timeline_values": fy_label},
                    ),
                ),
            ),
            (
                "sales_analyzer_zone_channel_store_empty",
                lambda: _run_analyzer_warm(
                    "sales_analyzer_zone_channel_store_empty",
                    _analyzer_warm_req(
                        rows=["zone", "sales_channel", "site_id_name"],
                        filters={},
                    ),
                ),
            ),
            (
                "target_vs_achieved",
                lambda: get_target_vs_achieved(
                    site_id="all",
                    timeline_mode="fy_year",
                    timeline_values=fy_label,
                    site_ids=None,
                    channel="all",
                    period="month",
                    count=24,
                    date_from=fy_from,
                    date_to=fy_to,
                ),
            ),
            (
                "store_managers",
                # Prefer warm SOH disk hit; daily pipeline already force-refreshes movement.
                lambda: (
                    _get_prepared_soh_df(force_refresh=False),
                    warm_managers_overview_cache(year=dash_year),
                )[1],
            ),
            (
                "product_performance",
                lambda: warm_product_performance_overview(year=dash_year, limit=1500),
            ),
            (
                "stock_overview",
                lambda: get_stock_overview(
                    site_id="all",
                    site_ids="",
                    source="all",
                    search="",
                    as_of="",
                    date_from="",
                    date_to="",
                    departments="",
                    sections="",
                    skus="",
                    barcodes="",
                    sizes="",
                    soh_source="daily",
                    page=1,
                    page_size=500,
                    refresh=1,
                ),
            ),
            (
                "festival_day",
                # Tiny JSON — must land on blob so hosted Festival Day is not a sales rebuild.
                lambda: warm_festival_comparison_cache(force_refresh=True),
            ),
            (
                "replenishments",
                # ~3MB parquet — hosted must not re-parse the 210MB refill CSV on open.
                lambda: warm_replenishments_recom_cache(force_refresh=False),
            ),
            (
                "inventory_lead_time",
                # Default Inventory Analytics tab (FE page_size=20) — blob JSON + parquet.
                lambda: get_dispatch_lead_time_report(
                    search="",
                    alert_filter="all",
                    section="all",
                    season="all",
                    sort_by="inward_date",
                    sort_dir="desc",
                    page=1,
                    page_size=20,
                    refresh=1,
                ),
            ),
            (
                "inventory_breakeven",
                lambda: get_sku_breakeven_report(
                    search="",
                    risk_filter="all",
                    section="all",
                    season="all",
                    sort_by="dispatch_gap_vs_be",
                    sort_dir="desc",
                    page=1,
                    page_size=50,
                    refresh=1,
                ),
            ),
            (
                "inventory_warehouse",
                # Warehouse Demand tab — RAM/dataset warm (FE limit=10000&show=all).
                lambda: (
                    _warm_wh_sku_demand_analysis(),
                    {"ok": True},
                )[1],
            ),
        )
        for name, fn in jobs:
            t0 = time.perf_counter()
            try:
                payload = fn()
                ok = bool(isinstance(payload, dict) and payload.get("ok", True))
                steps[name] = {"ok": ok, "seconds": round(time.perf_counter() - t0, 1)}
                logger.info("Page cache %s ok=%s in %.1fs", name, ok, time.perf_counter() - t0)
            except Exception as exc:
                # Analyzer steps already logger.exception with payload inside _run_analyzer_warm.
                if not str(name).startswith("sales_analyzer"):
                    logger.exception("Page cache %s failed", name)
                steps[name] = {
                    "ok": False,
                    "seconds": round(time.perf_counter() - t0, 1),
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                }
    finally:
        _REBUILD.reset(token)
    # Do not hard-code step count — jobs list grows (was falsely ok=false with 13 successes).
    ok = bool(steps) and all(step.get("ok") for step in steps.values())
    publish: dict[str, Any] = {}
    try:
        from api.current_soh import publish_daily_movement_to_blob
        from api.routes.replenishments_recom import publish_replenishment_snapshot_to_blob
        from api.routes.sales_dashboard import (
            warm_sales_overview_cache,
            warm_sales_overview_progressive_caches,
        )

        publish["daily_movement"] = publish_daily_movement_to_blob()
        publish["prepared_analyzer"] = publish_prepared_analyzer_caches_to_blob()
        publish["replenishment_snapshot"] = publish_replenishment_snapshot_to_blob()
        publish["dispatch_lead_time"] = publish_dispatch_lead_time_to_blob()
        publish["sku_breakeven"] = publish_sku_breakeven_to_blob()
        try:
            publish["sales_overview"] = warm_sales_overview_cache(force_refresh=False)
        except Exception as sales_exc:
            publish["sales_overview"] = {"ok": False, "error": str(sales_exc)}
        # Safety net: ensure progressive FE keys are on blob even if the job step failed.
        try:
            if not (steps.get("sales_overview_progressive") or {}).get("ok"):
                publish["sales_overview_progressive"] = warm_sales_overview_progressive_caches(
                    year=dash_year,
                    fy_label=fy_label,
                    force_refresh=True,
                )
        except Exception as prog_exc:
            publish["sales_overview_progressive"] = {"ok": False, "error": str(prog_exc)}
    except Exception as pub_exc:
        logger.warning("Page-cache blob publish after warm failed: %s", pub_exc)
        publish["error"] = str(pub_exc)

    # Force Home keys again after publish helpers — guarantees blob has today's summary.
    home_token = _REBUILD.set(True)
    try:
        from api.routes.executive_overview import warm_home_page_caches

        warm_home_page_caches(year=dash_year)
        steps["home_explicit"] = {"ok": True}
    except Exception as home_exc:
        logger.warning("Explicit home page-cache warm failed: %s", home_exc)
        steps["home_explicit"] = {"ok": False, "error": str(home_exc)}
    finally:
        _REBUILD.reset(home_token)

    ok = bool(steps) and all(step.get("ok") for step in steps.values())

    stamp_extra: dict[str, Any] = {
        "dash_year": dash_year,
        "fy_label": fy_label,
        "steps_ok": ok,
        "seconds": round(time.perf_counter() - started, 1),
    }
    try:
        hs = _CACHE_DIR / "home-summary"
        if hs.exists():
            files = sorted(hs.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            if files:
                newest = json.loads(files[0].read_text(encoding="utf-8"))
                home_net = newest.get("net_sales") or (newest.get("sales_kpis") or {}).get("net_sales")
                if home_net is not None:
                    stamp_extra["home_net_sales"] = float(home_net)
                    stamp_extra["home_net_sales_cr"] = round(float(home_net) / 1e7, 2)
    except Exception:
        pass
    try:
        publish["daily_stamp"] = publish_daily_page_cache_stamp(stamp_extra)
    except Exception as stamp_exc:
        publish["daily_stamp"] = {"ok": False, "error": str(stamp_exc)}

    return {
        "ok": ok,
        "seconds": round(time.perf_counter() - started, 1),
        "steps": steps,
        "publish": publish,
    }
