# Merged Backend — StockPulse API + Zinc Pipeline

Ye folder **sp-new StockPulse FastAPI backend** + **zinc_london refill/transfer pipeline** ka merged version hai.

```
merged_backend/
├── main.py                 # FastAPI app (StockPulse routes + /api/pipeline)
├── api/                    # StockPulse API (Blob-backed transfer/replen)
│   ├── blob_store.py       # Azure Blob (replaces OneLake)
│   └── routes/pipeline.py  # Local zinc pipeline jobs
├── pipeline/               # Zinc batch scripts (copied)
│   ├── runner.py           # Orchestrator (combine / refill / transfer / all)
│   ├── combine_*.py
│   ├── 1..6 scripts
│   ├── soh_null.py, store_soh_new.py
│   ├── transfer/1..4
│   ├── input_files/        # junction → ../../input_files
│   ├── output_files/       # junction → ../../output_files
│   └── old_input_files/    # junction → ../../old_input_files
├── requirements.txt
└── .env                    # (copy from sp-new; Azure / Power BI creds)
```

## Architecture

| Layer | Source | Role |
|-------|--------|------|
| Replen / Transfer UI APIs | Blob | Read/write `zinc-data` container (`output/`) |
| **Local pipeline** | zinc | Excel → refill + transfer artifacts |
| Fabric / OneLake | removed | No longer used at runtime |

## Run

```powershell
cd c:\Users\ChandanSoni\Downloads\zinc_london_project\merged_backend

# Install deps (venv recommended)
pip install -r requirements.txt

# Start API
uvicorn main:app --reload --port 5001
```

Open docs: http://127.0.0.1:5001/docs

## Pipeline APIs (new)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/pipeline/health` | Data root + dirs check |
| GET | `/api/pipeline/stages` | Available stages + scripts |
| GET | `/api/pipeline/status` | Current job status / logs |
| GET | `/api/pipeline/outputs` | Key output files list |
| POST | `/api/pipeline/run?stage=all` | Start job (`combine` / `refill` / `transfer` / `all`) |
| GET | `/api/pipeline/download?name=final_transfer_sizewise.xlsx` | Download output |

### CLI (without FastAPI)

```powershell
cd merged_backend
python -m pipeline.runner --stage refill
python -m pipeline.runner --stage transfer
python -m pipeline.runner --stage all
```

## Blob setup (required)

1. Copy vars from `.env.blob.example` into `.env`
2. Set `AZURE_STORAGE_CONNECTION_STRING` (AccountName=`zinklondon`, container=`zinc-data`)
3. Verify:

```powershell
python scripts/check_blob_connection.py
# or: GET http://127.0.0.1:5001/api/debug/db-status
```

### Blob folder layout

| Blob path | Local (pipeline) |
|-----------|------------------|
| `input/<excel files>` | `pipeline/input_files/` |
| `input/old/...` or `input/old_input_files/...` | `pipeline/old_input_files/` |
| `output/recom_neww5.csv` | UI replen |
| `output/recommended_transfers_new6.csv` | UI transfers |

**Pipeline always syncs from Blob first** (`PIPELINE_SYNC_BLOB_INPUTS=1`):

```powershell
# Sync only
python -m pipeline.runner --sync-only
# Or API
# POST /api/pipeline/sync-inputs

# Run (auto-downloads input/ then runs)
python -m pipeline.runner --stage all
```

Upload the same filenames you used locally (item master, channel SOH/sales, warehouse, etc.) into blob `input/`.

## Notes

1. **Input/output files** zinc project ke shared folders use karte hain (junction) — Blob sync unhin pe likhta hai.
2. **OneLake / Fabric cut** — `api/onelake_store.py` ab Blob store re-export karta hai. `DATABASE_BACKEND=blob`.
3. Frontend: Replenishments + Transfers live; baaki tabs blank.
## Next (frontend)

`sp-new/meridianai-stockpulse-frontend` ko link karna:
- Vite proxy `/api` → `http://127.0.0.1:5001`
- Optional: Pipeline run button page calling `/api/pipeline/run`
