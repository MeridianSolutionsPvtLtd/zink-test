"""Phase B correctness gates: SOH alignment assigns dims; STR never uses Current SOH."""
from __future__ import annotations

import pandas as pd

from api.historical_movement import historical_str
from api.routes.sales_analyzer import _align_soh_product_dims, _compute_measure_values, ValueConfig


def test_align_soh_product_dims_assigns_blank_season_from_sales():
    soh = pd.DataFrame(
        {
            "barcode": ["B1", "B2"],
            "season": ["", "AW25"],
            "current_soh": [10.0, 5.0],
        }
    )
    sales = pd.DataFrame(
        {
            "barcode": ["B1", "B1", "B2"],
            "season": ["SS25", "SS25", "AW25"],
        }
    )
    out = _align_soh_product_dims(soh, sales)
    assert str(out.loc[0, "season"]).strip() == "SS25"
    assert str(out.loc[1, "season"]).strip() == "AW25"


def test_historical_str_formula_opening_plus_receipts():
    assert historical_str(25.0, 10.0, 40.0) == 50.0
    assert historical_str(10.0, 0.0, 0.0) is None
    assert historical_str(10.0, None, 5.0) is None


def test_str_never_falls_back_to_current_soh_denominator():
    vals = [
        ValueConfig(field="sell_through_pct", aggregation="calculated"),
        ValueConfig(field="current_soh", aggregation="sum"),
        ValueConfig(field="quantity", aggregation="sum"),
    ]
    # No opening/receipts, no available — SOH present must NOT invent STR via qty/(qty+soh)
    metrics = _compute_measure_values(
        sales_val=1000.0,
        qty_val=50.0,
        gross_val=1200.0,
        disc_amt=200.0,
        soh_val=50.0,
        mdq_val=None,
        rec_count=10,
        active_values=vals,
        opening_val=None,
        receipts_val=None,
        available_val=None,
    )
    assert metrics["sell_through_pct"] is None
    assert metrics["current_soh"] == 50.0

    # Movement-based STR still works
    metrics2 = _compute_measure_values(
        sales_val=1000.0,
        qty_val=25.0,
        gross_val=1200.0,
        disc_amt=200.0,
        soh_val=999.0,  # must be ignored for STR
        mdq_val=None,
        rec_count=10,
        active_values=vals,
        opening_val=10.0,
        receipts_val=40.0,
        available_val=None,
    )
    assert metrics2["sell_through_pct"] == 50.0
