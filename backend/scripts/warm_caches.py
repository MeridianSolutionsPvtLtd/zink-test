"""Run the dashboard cache warm-up (and optionally the daily pipeline scheduler) in its
own process, so the API workers never compete with it for the GIL.

The warm-up writes page_cache JSON (disk + blob); API workers serve the default first
view straight from that cache.

Local (second terminal, from the backend root):
    python -m scripts.warm_caches                 # warm once, then exit
    python -m scripts.warm_caches --scheduler     # warm, then keep running the 09:00 pipeline

API side (.env):
    DASHBOARD_WARM_ON_STARTUP=0
    DAILY_PIPELINE_SCHEDULER=0
    FINANCIAL_WEEK_AUTO_SYNC=0

Azure: run this as a WebJob / scheduled task after the daily pipeline.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--scheduler",
        action="store_true",
        help="After warming, keep running the daily pipeline scheduler + financial-week sync.",
    )
    args = parser.parse_args()

    # Set before importing main: load_dotenv() does not override existing env vars,
    # so these win over the API-side .env values.
    os.environ["DASHBOARD_WARM_ON_STARTUP"] = "1"
    os.environ["DAILY_PIPELINE_SCHEDULER"] = "1" if args.scheduler else "0"
    os.environ["FINANCIAL_WEEK_AUTO_SYNC"] = "1" if args.scheduler else "0"
    os.chdir(BACKEND_ROOT)
    sys.path.insert(0, str(BACKEND_ROOT))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    import main as api_main  # registers routes; does not start a server

    t0 = time.perf_counter()
    # Same function the API runs on startup — it spawns the warm + scheduler threads.
    api_main._warm_dashboard_caches_on_startup()
    for t in threading.enumerate():
        if t.name == "dashboard-warm":
            t.join()
    logging.info("Dashboard warm-up finished in %.0fs", time.perf_counter() - t0)

    if not args.scheduler:
        return 0
    logging.info("Scheduler mode — keeping process alive (Ctrl+C to stop)")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
