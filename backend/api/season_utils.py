"""
API Season Utilities - Re-export from pipeline.season_utils for API routes
"""
from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND / "pipeline") not in sys.path:
    sys.path.append(str(_BACKEND / "pipeline"))

from season_utils import (
    expand_season_yoy_chain,
    get_previous_season,
    get_season_years_ago,
    parse_season_info,
    shift_season_csv,
)

__all__ = [
    "get_previous_season",
    "get_season_years_ago",
    "expand_season_yoy_chain",
    "shift_season_csv",
    "parse_season_info",
]
