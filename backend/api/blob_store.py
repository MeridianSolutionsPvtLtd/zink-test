"""
Azure Blob Storage store — replaces OneLake / Fabric for transfer & replen data.

Layout (container: AZURE_STORAGE_CONTAINER, default zinc-data):
  {AZURE_BLOB_OUTPUT_PREFIX}{table}.json|.parquet|.csv|.xlsx

Large CSVs (e.g. recom_neww5 ~120MB) are downloaded once to a local disk cache,
then optionally materialised as parquet for faster reloads.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd
from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import BlobServiceClient, ContentSettings

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CACHE_DIR = Path(os.environ.get("BLOB_CACHE_DIR") or (_PROJECT_ROOT / "data" / "blob_cache"))
# In-memory rows: table_name -> (etag, rows)
_MEMORY_CACHE: Dict[str, Tuple[str, List[Dict[str, Any]]]] = {}


def _env(*keys: str, default: str = "") -> str:
    for key in keys:
        value = os.environ.get(key)
        if value:
            return value
    return default


def upload_local_file_with_retry(
    container,
    *,
    blob_name: str,
    path: Path,
    content_type: str = "application/octet-stream",
    attempts: int = 4,
    timeout_s: int = 900,
) -> None:
    """Stream a local file to blob with retries (large SSL uploads often flake once).

    Files >5MB use staged 4MB blocks — full-stream upload was timing out on
    home/office SSL and left hosted App Service without default page caches.
    """
    if not path.exists() or path.stat().st_size <= 0:
        raise FileNotFoundError(f"missing local file: {path}")
    last_exc: Exception | None = None
    size = path.stat().st_size
    blob = container.get_blob_client(blob_name)
    settings = ContentSettings(content_type=content_type)
    chunk = 4 * 1024 * 1024

    def _upload_once() -> None:
        if size <= 5 * 1024 * 1024:
            with path.open("rb") as fh:
                blob.upload_blob(
                    fh,
                    overwrite=True,
                    content_settings=settings,
                    timeout=timeout_s,
                    max_concurrency=1,
                    length=size,
                )
            return
        # Chunked block upload — survives mid-stream SSL resets better.
        from azure.storage.blob import BlobBlock
        import uuid

        blocks: list[BlobBlock] = []
        with path.open("rb") as fh:
            while True:
                data = fh.read(chunk)
                if not data:
                    break
                block_id = uuid.uuid4().hex
                blob.stage_block(block_id, data, length=len(data), timeout=timeout_s)
                blocks.append(BlobBlock(block_id=block_id))
        blob.commit_block_list(blocks, content_settings=settings, timeout=timeout_s)

    for attempt in range(1, max(1, attempts) + 1):
        try:
            _upload_once()
            return
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "Blob upload attempt %s/%s failed for %s (%s MB): %s",
                attempt,
                attempts,
                blob_name,
                round(size / 1e6, 2),
                exc,
            )
            if attempt < attempts:
                time.sleep(min(30, 2 ** attempt))
    raise RuntimeError(f"blob upload failed after {attempts} attempts: {blob_name}: {last_exc}")


def _norm_prefix(prefix: str) -> str:
    p = (prefix or "").strip().replace("\\", "/")
    if p and not p.endswith("/"):
        p += "/"
    return p.lstrip("/")


def _safe_name(blob_name: str) -> str:
    return re.sub(r"[^\w.\-]+", "_", blob_name)


class BlobJsonStore:
    """
    Drop-in replacement for OneLakeJsonStore using Azure Blob containers.
    Same method names used by transfers_recom / replenishments_recom.
    """

    def __init__(self) -> None:
        conn = _env("AZURE_STORAGE_CONNECTION_STRING")
        account = _env("AZURE_STORAGE_ACCOUNT", default="zinklondon")
        container = _env("AZURE_STORAGE_CONTAINER", default="zinc-data")
        self.output_prefix = _norm_prefix(_env("AZURE_BLOB_OUTPUT_PREFIX", default="output/"))
        self.input_prefix = _norm_prefix(_env("AZURE_BLOB_INPUT_PREFIX", default="input/"))
        self.container_name = container

        if not conn:
            raise RuntimeError(
                "Blob config missing. Set AZURE_STORAGE_CONNECTION_STRING in .env "
                "(AccountName should match AZURE_STORAGE_ACCOUNT)."
            )

        # Connect stays short (fail fast). Read allows large parquet pulls (DM / sales ready).
        connect_s = float(os.environ.get("AZURE_BLOB_CONNECT_TIMEOUT", "3") or "3")
        read_s = float(os.environ.get("AZURE_BLOB_READ_TIMEOUT", "120") or "120")
        try:
            self.service = BlobServiceClient.from_connection_string(
                conn,
                connection_timeout=connect_s,
                read_timeout=read_s,
            )
        except TypeError:
            self.service = BlobServiceClient.from_connection_string(conn)
        self.container = self.service.get_container_client(container)
        self.account_name = account
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            exists = self.container.exists(timeout=max(2, int(connect_s)))
        except TypeError:
            exists = self.container.exists()
        if not exists:
            raise RuntimeError(f"Blob container not found or inaccessible: {container}")

    def _blob_name(self, table_name: str, ext: str) -> str:
        return f"{self.output_prefix}{table_name}{ext}"

    def ensure_input_candidates(self, rel_candidates: Sequence[str]) -> Optional[Path]:
        """
        Download the first existing blob under input/<rel> into local blob_cache.

        rel_candidates are paths relative to AZURE_BLOB_INPUT_PREFIX
        (e.g. "Warehouse SOH/current/WAREHOUSE_SOH.csv").
        """
        prefix = self.input_prefix or "input/"
        for rel in rel_candidates:
            rel_norm = str(rel or "").strip().replace("\\", "/").lstrip("/")
            if not rel_norm:
                continue
            blob_name = f"{prefix}{rel_norm}"
            try:
                if not self._blob_exists(blob_name):
                    continue
                path = self._ensure_local_file(blob_name)
                if path is not None and path.exists() and path.stat().st_size > 0:
                    return path
            except Exception as exc:
                logger.warning("ensure_input_candidates skip %s: %s", blob_name, exc)
        return None

    def ensure_output_table(self, table_name: str, exts: Sequence[str] = (".parquet", ".csv", ".xlsx")) -> Optional[Path]:
        """Download first existing output/<table><ext> into local blob_cache."""
        for ext in exts:
            blob_name = self._blob_name(table_name, ext)
            try:
                if not self._blob_exists(blob_name):
                    continue
                path = self._ensure_local_file(blob_name)
                if path is not None and path.exists() and path.stat().st_size > 0:
                    return path
            except Exception as exc:
                logger.warning("ensure_output_table skip %s: %s", blob_name, exc)
        return None

    def _blob_exists(self, blob_name: str) -> bool:
        try:
            return bool(self.container.get_blob_client(blob_name).exists())
        except Exception:
            return False

    def _blob_etag(self, blob_name: str) -> Optional[str]:
        try:
            props = self.container.get_blob_client(blob_name).get_blob_properties()
            return str(getattr(props, "etag", None) or props.size)
        except Exception:
            return None

    def _cache_paths(self, blob_name: str) -> Tuple[Path, Path, Path]:
        base = _CACHE_DIR / _safe_name(blob_name)
        return base, Path(str(base) + ".etag"), Path(str(base) + ".parquet")

    def _ensure_local_file(self, blob_name: str) -> Optional[Path]:
        """Download blob to disk cache if etag changed. Returns local raw file path."""
        try:
            client = self.container.get_blob_client(blob_name)
            if not client.exists():
                return None
            props = client.get_blob_properties()
            etag = str(getattr(props, "etag", None) or props.size)
        except ResourceNotFoundError:
            return None
        except Exception as e:
            msg = str(e).lower()
            if "404" in msg or "not found" in msg or "blobnotfound" in msg:
                return None
            raise

        raw_path, etag_path, _pq_path = self._cache_paths(blob_name)
        if raw_path.exists() and etag_path.exists() and etag_path.read_text(encoding="utf-8").strip() == etag:
            return raw_path

        logger.info("Blob cache miss — downloading %s (~%.1f MB)", blob_name, (props.size or 0) / 1e6)
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = raw_path.with_suffix(raw_path.suffix + f".tmp.{os.getpid()}")
        with open(tmp, "wb") as fh:
            stream = client.download_blob()
            for chunk in stream.chunks():
                fh.write(chunk)
        last_err: Optional[Exception] = None
        for attempt in range(8):
            try:
                os.replace(tmp, raw_path)
                last_err = None
                break
            except PermissionError as e:
                last_err = e
                time.sleep(0.4 * (attempt + 1))
        if last_err is not None:
            # Another process may have finished the same download — use existing file if present
            if raw_path.exists():
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
            else:
                raise last_err
        etag_path.write_text(etag, encoding="utf-8")
        # Invalidate parquet sidecar so it is rebuilt from fresh raw
        pq = Path(str(raw_path) + ".parquet")
        if pq.exists():
            try:
                pq.unlink()
            except OSError:
                pass
        return raw_path

    def _download_bytes(self, blob_name: str) -> Optional[bytes]:
        path = self._ensure_local_file(blob_name)
        if not path:
            return None
        return path.read_bytes()

    def _upload_bytes(self, blob_name: str, data: bytes, content_type: str) -> None:
        self.container.upload_blob(
            name=blob_name,
            data=data,
            overwrite=True,
            content_settings=ContentSettings(content_type=content_type),
        )
        # Invalidate caches for this blob
        for p in self._cache_paths(blob_name):
            if p.exists():
                try:
                    p.unlink()
                except OSError:
                    pass
        for key in list(_MEMORY_CACHE.keys()):
            if key.endswith(":" + blob_name) or key == blob_name:
                _MEMORY_CACHE.pop(key, None)

    def _list_prefix(self, prefix: str) -> List[str]:
        return [b.name for b in self.container.list_blobs(name_starts_with=prefix)]

    def _df_to_rows(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        if df is None or df.empty:
            return []
        return df.where(pd.notnull(df), None).to_dict(orient="records")

    def _read_rows_from_path(self, path: Path, name: str) -> List[Dict[str, Any]]:
        lower = name.lower()
        pq_sidecar = Path(str(path) + ".parquet")

        if lower.endswith(".csv"):
            if pq_sidecar.exists() and pq_sidecar.stat().st_mtime >= path.stat().st_mtime:
                logger.info("Blob: reading parquet cache %s", pq_sidecar.name)
                return self._df_to_rows(pd.read_parquet(pq_sidecar))
            logger.info("Blob: parsing CSV %s", path.name)
            try:
                from pipeline.date_merge import read_csv_bytes
            except ImportError:
                from date_merge import read_csv_bytes  # type: ignore
            df = read_csv_bytes(path.read_bytes(), low_memory=False)
            try:
                df.to_parquet(pq_sidecar, index=False)
                logger.info("Blob: wrote parquet cache %s", pq_sidecar.name)
            except Exception as e:
                logger.warning("Could not write parquet cache: %s", e)
            return self._df_to_rows(df)

        if lower.endswith(".parquet"):
            return self._df_to_rows(pd.read_parquet(path))
        if lower.endswith(".xlsx") or lower.endswith(".xls"):
            return self._df_to_rows(pd.read_excel(path))
        if lower.endswith(".json"):
            parsed = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict) and isinstance(parsed.get("data"), list):
                return parsed["data"]
            return []
        # fallback
        return self._df_to_rows(pd.read_csv(path, low_memory=False))

    def _read_rows_from_bytes(self, content: bytes, name: str) -> List[Dict[str, Any]]:
        lower = name.lower()
        bio = io.BytesIO(content)
        if lower.endswith(".json"):
            parsed = json.loads(content.decode("utf-8"))
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict) and isinstance(parsed.get("data"), list):
                return parsed["data"]
            return []
        if lower.endswith(".parquet"):
            return self._df_to_rows(pd.read_parquet(bio))
        if lower.endswith(".csv"):
            return self._df_to_rows(pd.read_csv(bio, low_memory=False))
        if lower.endswith(".xlsx") or lower.endswith(".xls"):
            return self._df_to_rows(pd.read_excel(bio))
        try:
            return self._read_rows_from_bytes(content, name + ".json")
        except Exception:
            return self._df_to_rows(pd.read_parquet(bio))

    def read_delta_table(self, table_name: str, schema: str = "dbo") -> List[Dict[str, Any]]:
        """
        Read table from blob (disk-cached). Prefers csv/parquet/xlsx/json under output/.
        """
        _ = schema
        # Prefer csv first so we hit user's uploaded files before empty parquet stubs.
        for ext in (".csv", ".parquet", ".xlsx", ".json"):
            blob_name = self._blob_name(table_name, ext)
            etag = self._blob_etag(blob_name) if self._blob_exists(blob_name) else None
            if not etag:
                continue
            mem_key = f"{table_name}:{blob_name}"
            cached = _MEMORY_CACHE.get(mem_key)
            if cached and cached[0] == etag:
                logger.info("Blob: memory cache hit %s (%s rows)", table_name, len(cached[1]))
                return cached[1]

            path = self._ensure_local_file(blob_name)
            if not path:
                continue
            rows = self._read_rows_from_path(path, blob_name)
            _MEMORY_CACHE[mem_key] = (etag, rows)
            logger.info("Blob: loaded %s (%s rows) from %s", table_name, len(rows), blob_name)
            return rows

        folder = f"{self.output_prefix}{table_name}/"
        names = [n for n in self._list_prefix(folder) if n.lower().endswith(".parquet") and "_delta_log" not in n]
        if names:
            all_rows: List[Dict[str, Any]] = []
            for n in names:
                path = self._ensure_local_file(n)
                if path:
                    all_rows.extend(self._read_rows_from_path(path, n))
            return all_rows
        return []

    def replace_delta_table(self, table_name: str, rows: List[Dict[str, Any]], schema: str = "dbo") -> None:
        """Write overwrite as parquet + json + csv under output/."""
        _ = schema
        df = pd.DataFrame(rows or [])
        if df.empty:
            df = pd.DataFrame({"_empty": pd.Series(dtype="string")})

        pq_buf = io.BytesIO()
        df.to_parquet(pq_buf, index=False)
        self._upload_bytes(
            self._blob_name(table_name, ".parquet"),
            pq_buf.getvalue(),
            "application/octet-stream",
        )
        payload = json.dumps(rows or [], ensure_ascii=True, default=str).encode("utf-8")
        self._upload_bytes(self._blob_name(table_name, ".json"), payload, "application/json")
        csv_buf = io.StringIO()
        df.to_csv(csv_buf, index=False)
        self._upload_bytes(
            self._blob_name(table_name, ".csv"),
            csv_buf.getvalue().encode("utf-8"),
            "text/csv",
        )
        logger.info("Blob: wrote %s (%s rows)", table_name, len(rows or []))

    def read_table(self, table_name: str) -> List[Dict[str, Any]]:
        # Prefer json; fall back to csv table if json missing
        for ext in (".json", ".csv"):
            path = self._ensure_local_file(self._blob_name(table_name, ext))
            if path:
                return self._read_rows_from_path(path, path.name)
        return []

    def write_table(self, table_name: str, rows: List[Dict[str, Any]]) -> None:
        payload = json.dumps(rows, ensure_ascii=True, default=str).encode("utf-8")
        self._upload_bytes(self._blob_name(table_name, ".json"), payload, "application/json")

    def replace_table(self, table_name: str, rows: List[Dict[str, Any]]) -> None:
        self.write_table(table_name, rows)

    def list_by_filter(
        self,
        table_name: str,
        donor_site: Optional[str] = None,
        donor_sku: Optional[str] = None,
        recv_site: Optional[str] = None,
        recv_sku: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        rows = self.read_table(table_name)

        def ok(v: Optional[str], row_val: Any) -> bool:
            if not v or v in ("All Sites", "All Stores", "All", "All SKUs"):
                return True
            return str(row_val or "").strip() == str(v).strip()

        filtered = []
        for row in rows:
            if not ok(donor_site, row.get("donor_store")):
                continue
            if not ok(donor_sku, row.get("sku")):
                continue
            if not ok(recv_site, row.get("receiver_store")):
                continue
            if not ok(recv_sku, row.get("sku")):
                continue
            filtered.append(row)
        return filtered

    def list_input_blobs(self, max_results: int = 100) -> List[str]:
        return self._list_prefix(self.input_prefix)[:max_results]

    def list_output_blobs(self, max_results: int = 100) -> List[str]:
        return self._list_prefix(self.output_prefix)[:max_results]

    def ping(self) -> Dict[str, Any]:
        exists = self.container.exists()
        inputs = list(self.container.list_blobs(name_starts_with=self.input_prefix))
        outputs = list(self.container.list_blobs(name_starts_with=self.output_prefix))
        return {
            "ok": bool(exists),
            "account": self.account_name,
            "container": self.container_name,
            "input_prefix": self.input_prefix,
            "output_prefix": self.output_prefix,
            "input_blob_count": len(inputs),
            "output_blob_count": len(outputs),
            "sample_input": [b.name for b in inputs[:10]],
            "sample_output": [b.name for b in outputs[:10]],
            "tables": {
                "replen": _env(
                    "REPLENISHMENTS_RECOM_TABLE",
                    default="prediction/current/SS26_weekly_predictions_formula_next_week_with_refill_recommendation",
                ),
                "transfer_source": _env(
                    "TRANSFER_SYNC_SOURCE_TABLE",
                    default="prediction/current/SS26_weekly_predictions_formula_next_week_with_refill_recommendation",
                ),
                "recommended_transfers": _env(
                    "RECOMMENDED_TRANSFERS_TABLE",
                    default="transfer/final_transfer_sizewise",
                ),
                "potential_donors": _env(
                    "POTENTIAL_DONORS_TABLE",
                    default="transfer/potential_donors",
                ),
                "potential_receivers": _env(
                    "POTENTIAL_RECEIVERS_TABLE",
                    default="transfer/potential_receivers",
                ),
                "zero_soh": _env(
                    "ZERO_SOH_TABLE",
                    default="soh_null_no_sales_transfer_days",
                ),
            },
        }

    @staticmethod
    def now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()


OneLakeJsonStore = BlobJsonStore
