import asyncio
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException
from api.routes.upload import save_replenishment_stores_local


class ReplenishmentStartTests(unittest.TestCase):
    def request(self):
        return SimpleNamespace(json=AsyncMock(return_value={"stores": ["pantaloons", "shoppers-stop", "centro", "iconic", "ebo"], "start_pipeline": True}))

    def test_running_job_does_not_report_new_run_or_change_selection(self):
        save = Mock()
        with patch.dict(sys.modules, {"pipeline_inputs": SimpleNamespace(save_replenishment_store_selection=save)}), patch("pipeline.runner.get_job_state", return_value={"status": "running"}):
            with self.assertRaises(HTTPException) as error:
                asyncio.run(save_replenishment_stores_local(self.request()))
        self.assertEqual(error.exception.status_code, 409)
        save.assert_not_called()

    def test_start_rejection_is_not_success(self):
        save = Mock(return_value={"stores": [], "store_types": []})
        with patch.dict(sys.modules, {"pipeline_inputs": SimpleNamespace(save_replenishment_store_selection=save)}), patch("pipeline.runner.get_job_state", return_value={"status": "idle"}), patch("pipeline.runner.start_stage_async", return_value={"ok": False, "message": "Job already running"}):
            with self.assertRaises(HTTPException) as error:
                asyncio.run(save_replenishment_stores_local(self.request()))
        self.assertEqual(error.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
