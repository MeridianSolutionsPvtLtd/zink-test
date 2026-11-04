"""
Warehouse SOH ledger — avoid double-allocating the same closing register across runs.

When the same WAREHOUSE_SOH file is used for multiple replenishment passes (e.g. PT/SSL
first, Centro later), effective WH = register qty minus prior covered_by_wh_soh
(accumulated in remaining_by_barcode).

Reset automatically when warehouse file fingerprint changes (new upload).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

log = logging.getLogger("pipeline.warehouse_ledger")

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output_files"
LEDGER_DIR = OUTPUT_DIR / "warehouse"
LEDGER_FILE = LEDGER_DIR / "wh_remaining_ledger.json"
BLOB_LEDGER_REL = "warehouse/wh_remaining_ledger.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def warehouse_fingerprint(path: Path) -> str:
    path = Path(path)
    if not path.exists():
        return ""
    st = path.stat()
    h = hashlib.sha256()
    h.update(path.name.encode("utf-8"))
    h.update(str(st.st_size).encode())
    h.update(str(int(st.st_mtime)).encode())
    # small sample for content change without size change
    with path.open("rb") as f:
        h.update(f.read(65536))
    return h.hexdigest()[:32]


def load_ledger() -> Optional[dict[str, Any]]:
    if not LEDGER_FILE.exists():
        return None
    try:
        return json.loads(LEDGER_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("Could not read WH ledger: %s", e)
        return None


def save_ledger(data: dict[str, Any]) -> Path:
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = _utc_now()
    LEDGER_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return LEDGER_FILE


def reset_ledger() -> None:
    if LEDGER_FILE.exists():
        LEDGER_FILE.unlink(missing_ok=True)


def infer_remaining_from_refill_csv(
    csv_path: Path,
    register: Dict[str, float],
) -> Dict[str, float]:
    """Rebuild remaining from last published refill (wh_soh_end_barcode per barcode)."""
    import pandas as pd

    if not csv_path.exists():
        return dict(register)
    df = pd.read_csv(csv_path, low_memory=False)
    df.columns = df.columns.astype(str).str.strip()
    if "barcode" not in df.columns:
        return dict(register)
    bc = df["barcode"].astype(str).str.strip().str.upper().str.replace(r"\.0$", "", regex=True)
    df = df.assign(_bc=bc)
    remaining = dict(register)
    if "wh_soh_end_barcode" in df.columns:
        ends = (
            pd.to_numeric(df["wh_soh_end_barcode"], errors="coerce")
            .groupby(df["_bc"])
            .min()
        )
        for b, v in ends.items():
            if b and pd.notna(v):
                remaining[str(b)] = max(0.0, float(v))
    return remaining


def effective_warehouse_stock(
    register: Dict[str, float],
    warehouse_path: Path,
    *,
    refill_csv_fallback: Path | None = None,
) -> Tuple[Dict[str, float], dict[str, Any]]:
    """
    Return (effective_qty_by_barcode, info dict for logging).
  Honors WH_LEDGER_RESET=1 to ignore ledger.
    """
    info: dict[str, Any] = {
        "warehouse_file": warehouse_path.name,
        "fingerprint": warehouse_fingerprint(warehouse_path),
        "used_ledger": False,
        "reconstructed_from_csv": False,
    }
    if (os.environ.get("WH_LEDGER_RESET") or "").strip().lower() in ("1", "true", "yes"):
        info["note"] = "WH_LEDGER_RESET — full register"
        return dict(register), info

    fp = info["fingerprint"]
    ledger = load_ledger()
    remaining: Dict[str, float] = dict(register)

    if ledger and ledger.get("fingerprint") == fp and ledger.get("remaining_by_barcode"):
        info["used_ledger"] = True
        rem = {str(k).upper(): float(v) for k, v in ledger["remaining_by_barcode"].items()}
        for bc, reg_q in register.items():
            key = str(bc).upper()
            if key in rem:
                remaining[bc] = max(0.0, min(float(reg_q), rem[key]))
            else:
                remaining[bc] = float(reg_q)
        info["ledger_updated_at"] = ledger.get("updated_at")
        info["prior_run_store_types"] = ledger.get("last_store_types")
        return remaining, info

    if refill_csv_fallback and refill_csv_fallback.exists():
        remaining = infer_remaining_from_refill_csv(refill_csv_fallback, register)
        info["reconstructed_from_csv"] = True
        info["note"] = f"Ledger missing/stale; used {refill_csv_fallback.name}"
        return remaining, info

    info["note"] = "No ledger — full register (first run on this WH file)"
    return remaining, info


def update_ledger_after_allocation(
    register: Dict[str, float],
    warehouse_path: Path,
    *,
    barcode_remaining: Dict[str, float],
    store_types: list[str],
    wh_allocated_units: int,
) -> dict[str, Any]:
    """Persist remaining WH after this refill run."""
    fp = warehouse_fingerprint(warehouse_path)
    prev = load_ledger() or {}
    runs = list(prev.get("runs") or [])
    runs.append(
        {
            "at": _utc_now(),
            "store_types": store_types,
            "wh_allocated_units": int(wh_allocated_units),
        }
    )
    runs = runs[-20:]

    # Merge: start from previous remaining if same fingerprint, else register
    if prev.get("fingerprint") == fp and prev.get("remaining_by_barcode"):
        merged = {str(k).upper(): float(v) for k, v in prev["remaining_by_barcode"].items()}
    else:
        merged = {str(k).upper(): float(v) for k, v in register.items()}

    for bc, rem in barcode_remaining.items():
        merged[str(bc).upper()] = max(0.0, float(rem))

    # Barcodes only in register, not touched this run — keep merged value or register
    for bc, reg_q in register.items():
        key = str(bc).upper()
        if key not in barcode_remaining and key not in merged:
            merged[key] = float(reg_q)

    data = {
        "warehouse_file": warehouse_path.name,
        "fingerprint": fp,
        "register_by_barcode": {str(k): float(v) for k, v in register.items()},
        "remaining_by_barcode": merged,
        "last_store_types": store_types,
        "runs": runs,
    }
    save_ledger(data)
    return data


def sync_ledger_from_blob() -> dict[str, Any]:
    """Download ledger from blob if newer / local missing."""
    try:
        from publish_outputs_to_blob import _download_file, _get_container

        cc, _container, out_pfx = _get_container()
        blob = f"{out_pfx}{BLOB_LEDGER_REL}"
        LEDGER_DIR.mkdir(parents=True, exist_ok=True)
        if _download_file(cc, blob, LEDGER_FILE):
            return {"ok": True, "downloaded": BLOB_LEDGER_REL}
        return {"ok": True, "downloaded": []}
    except Exception as e:
        return {"ok": False, "error": str(e)}
