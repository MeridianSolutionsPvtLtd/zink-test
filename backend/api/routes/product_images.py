"""
Product Images API Router
Serves SKU item images from Raw_Files/ItemImage in Azure Blob Storage container zinc-data
with local disk caching and fallback to ITEM_MASTER ImageKit URLs.
"""
from __future__ import annotations

import logging
import mimetypes
import os
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse, RedirectResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/product-images", tags=["Product Images"])

BACKEND_DIR = Path(__file__).resolve().parents[2]
CACHE_DIR = BACKEND_DIR / "data" / "item_image_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

_BLOB_IMAGE_MAP: dict[str, str] | None = None
_BLOB_IMAGE_MAP_BUILDING = False
_ITEM_MASTER_IMAGEKIT_MAP: dict[str, str] | None = None
_ITEM_MASTER_IMAGEKIT_AT = 0.0
_ITEM_MASTER_TTL_S = float(os.environ.get("PRODUCT_IMAGE_MAP_TTL_S", "1800") or "1800")
_ITEM_MASTER_BUILDING = False
_ITEM_MASTER_LOCK = threading.Lock()

_BLOB_PREFIXES = (
    "Raw_Files/ItemImage/",
    "Raw_Files/item_image/",
    "Raw_Files/Item_Image/",
)
_BLOB_EXTS = (".jpg", ".jpeg", ".png", ".webp")


def _on_azure() -> bool:
    return bool(os.environ.get("WEBSITE_SITE_NAME") or os.environ.get("WEBSITE_INSTANCE_ID"))


def _get_blob_image_map() -> dict[str, str]:
    """SKU → blob path. Built once; never list the whole container on every image hit."""
    global _BLOB_IMAGE_MAP, _BLOB_IMAGE_MAP_BUILDING
    if _BLOB_IMAGE_MAP is not None:
        return _BLOB_IMAGE_MAP
    if _BLOB_IMAGE_MAP_BUILDING:
        # Concurrent first hits: serve empty map; direct-path lookup still works.
        return {}

    # Hosted: never list the whole ItemImage prefix on a request thread (minutes → 502).
    if _on_azure():
        _BLOB_IMAGE_MAP = {}
        return _BLOB_IMAGE_MAP

    _BLOB_IMAGE_MAP_BUILDING = True
    mapping: dict[str, str] = {}
    try:
        if not os.environ.get("AZURE_STORAGE_CONNECTION_STRING"):
            _BLOB_IMAGE_MAP = mapping
            return mapping
        from api.blob_store import BlobJsonStore

        store = BlobJsonStore()
        if store.container:
            for prefix in _BLOB_PREFIXES:
                for b in store.container.list_blobs(name_starts_with=prefix):
                    filename = b.name.split("/")[-1].strip()
                    if "." in filename:
                        sku = filename.rsplit(".", 1)[0].strip().upper()
                        if sku and sku not in mapping:
                            mapping[sku] = b.name
        logger.info("Product image blob map ready (%s SKUs)", len(mapping))
    except Exception as exc:
        logger.warning("Failed to build Blob item image map: %s", exc)
    finally:
        _BLOB_IMAGE_MAP = mapping
        _BLOB_IMAGE_MAP_BUILDING = False
    return mapping


def _build_item_master_imagekit_map_bg() -> None:
    global _ITEM_MASTER_IMAGEKIT_MAP, _ITEM_MASTER_IMAGEKIT_AT, _ITEM_MASTER_BUILDING
    try:
        from pipeline.item_master import build_sku_image_map

        mapping = {
            sku: url
            for sku, url in build_sku_image_map().items()
            if str(url).lower().startswith(("https://", "http://"))
        }
        _ITEM_MASTER_IMAGEKIT_MAP = mapping
        _ITEM_MASTER_IMAGEKIT_AT = time.time()
        logger.info("Item Master ImageKit map ready (%s SKUs)", len(mapping))
    except Exception as exc:
        logger.warning("Item Master image map failed: %s", exc)
        if _ITEM_MASTER_IMAGEKIT_MAP is None:
            _ITEM_MASTER_IMAGEKIT_MAP = {}
            _ITEM_MASTER_IMAGEKIT_AT = time.time()
    finally:
        _ITEM_MASTER_BUILDING = False


def _get_item_master_imagekit_map(*, allow_block: bool = False) -> dict[str, str]:
    """Cached ImageKit URLs from Item Master.

    On Azure request path: never block on a rebuild — kick a background warm and
    return whatever is already cached (often empty → 404/direct-blob).
    """
    global _ITEM_MASTER_IMAGEKIT_MAP, _ITEM_MASTER_IMAGEKIT_AT, _ITEM_MASTER_BUILDING
    now = time.time()
    if (
        _ITEM_MASTER_IMAGEKIT_MAP is not None
        and (now - _ITEM_MASTER_IMAGEKIT_AT) < max(60.0, _ITEM_MASTER_TTL_S)
    ):
        return _ITEM_MASTER_IMAGEKIT_MAP

    if not allow_block or _on_azure():
        with _ITEM_MASTER_LOCK:
            if not _ITEM_MASTER_BUILDING:
                _ITEM_MASTER_BUILDING = True
                threading.Thread(
                    target=_build_item_master_imagekit_map_bg,
                    name="item-master-imagekit",
                    daemon=True,
                ).start()
        return _ITEM_MASTER_IMAGEKIT_MAP or {}

    _build_item_master_imagekit_map_bg()
    return _ITEM_MASTER_IMAGEKIT_MAP or {}


def _try_direct_blob_image(sku: str) -> str | None:
    """Probe known blob paths for one SKU — avoids listing thousands of blobs on cold miss."""
    if not os.environ.get("AZURE_STORAGE_CONNECTION_STRING"):
        return None
    try:
        from api.blob_store import BlobJsonStore

        store = BlobJsonStore()
        # Prefer the primary prefix + jpg first (covers almost all SKUs).
        probes = [
            f"{_BLOB_PREFIXES[0]}{sku}.jpg",
            f"{_BLOB_PREFIXES[0]}{sku}.jpeg",
            f"{_BLOB_PREFIXES[0]}{sku}.png",
            f"{_BLOB_PREFIXES[0]}{sku}.webp",
        ]
        for name in probes:
            try:
                if store.container.get_blob_client(name).exists():
                    return name
            except Exception:
                continue
    except Exception as exc:
        logger.debug("Direct blob image probe failed for %s: %s", sku, exc)
    return None


def _download_blob_to_cache(blob_name: str, sku: str) -> Path | None:
    try:
        from api.blob_store import BlobJsonStore

        store = BlobJsonStore()
        ext = Path(blob_name).suffix.lower() or ".jpg"
        target_path = CACHE_DIR / f"{sku}{ext}"
        data = store.container.get_blob_client(blob_name).download_blob().readall()
        if not data:
            return None
        with open(target_path, "wb") as f:
            f.write(data)
        return target_path
    except Exception as exc:
        logger.warning("Failed to download image blob %s for SKU %s: %s", blob_name, sku, exc)
        return None


@router.get("/sku/{sku}")
@router.get("/{sku}")
def get_product_image(sku: str) -> Response:
    """
    Get item product image for a given SKU.
    Serves from local cache, ImageKit CDN, or Azure Blob Raw_Files/ItemImage.
    """
    clean_sku = sku.strip().upper()
    if not clean_sku:
        raise HTTPException(status_code=400, detail="SKU required")
    if any(c in clean_sku for c in ("/", "\\", ":")) or clean_sku in (".", ".."):
        raise HTTPException(status_code=400, detail="Invalid SKU")

    # 1. Local disk first (fastest; no Azure / Item Master)
    for ext in _BLOB_EXTS:
        cached_file = CACHE_DIR / f"{clean_sku}{ext}"
        if cached_file.exists() and cached_file.stat().st_size > 0:
            mime = mimetypes.guess_type(str(cached_file))[0] or "image/jpeg"
            return FileResponse(
                path=cached_file,
                media_type=mime,
                headers={"Cache-Control": "public, max-age=86400"},
            )

    # 2. ImageKit from Item Master (cached map only — never block rebuild on Azure)
    img_url = _get_item_master_imagekit_map(allow_block=False).get(clean_sku)
    if img_url:
        return RedirectResponse(
            url=img_url,
            status_code=307,
            headers={"Cache-Control": "public, max-age=3600"},
        )

    # 3. Direct blob path for this SKU (no full container list)
    blob_name = _try_direct_blob_image(clean_sku)
    if not blob_name and not _on_azure():
        # 4. Local-only: optional full map
        blob_name = _get_blob_image_map().get(clean_sku)

    if blob_name:
        target = _download_blob_to_cache(blob_name, clean_sku)
        if target is not None and target.exists():
            mime = mimetypes.guess_type(str(target))[0] or "image/jpeg"
            return FileResponse(
                path=target,
                media_type=mime,
                headers={"Cache-Control": "public, max-age=86400"},
            )

    raise HTTPException(status_code=404, detail=f"No product image found for SKU '{sku}'")


@router.get("")
def list_available_product_images() -> dict[str, Any]:
    """List all SKUs that have available product images in Azure Blob Storage / Item Master."""
    blob_map = _get_blob_image_map()
    item_master_map = _get_item_master_imagekit_map(allow_block=not _on_azure())
    all_skus = sorted(set(blob_map.keys()) | set(item_master_map.keys()))
    return {
        "ok": True,
        "total_count": len(all_skus),
        "raw_files_item_image_count": len(blob_map),
        "item_master_url_count": len(item_master_map),
        "sample_skus": all_skus[:30],
    }
