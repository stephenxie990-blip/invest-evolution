import numpy as np
import pandas as pd

from invest.meetings import SelectionMeeting
from invest.models import MomentumModel


def _make_stock_data(n=10, days=160):
    dates = pd.date_range("2023-01-01", periods=days, freq="B")
    stock_data = {}
    rng = np.random.default_rng(7)
    for i in range(n):
        code = f"sh.{600100 + i}"
        close = 8 + np.cumsum(rng.normal(0.03, 0.35, len(dates)))
        close = np.maximum(close, 1)
        stock_data[code] = pd.DataFrame({
            "date": dates.strftime("%Y-%m-%d"),
            "trade_date": dates.strftime("%Y%m%d"),
            "open": close * 0.998,
            "high": close * 1.02,
            "low": close * 0.98,
            "close": close,
            "volume": rng.integers(100000, 10000000, len(dates)).astype(float),
            "pct_chg": pd.Series(close).pct_change().fillna(0) * 100,
        })
    return stock_data


def test_selection_meeting_can_consume_model_output():
    model = MomentumModel(runtime_overrides={"top_n": 5, "max_positions": 3})
    output = model.process(_make_stock_data(), "20230601")
    meeting = SelectionMeeting(llm_caller=None, max_hunters=2)

    result = meeting.run_with_model_output(output)

    assert result["trading_plan"].max_positions == 3
    assert result["meeting_log"]["model_name"] == "momentum"
    assert result["strategy_advice"]["selected_codes"]

def test_selection_meeting_increments_meeting_id_for_model_output_runs():
    model = MomentumModel(runtime_overrides={"top_n": 5, "max_positions": 3})
    output = model.process(_make_stock_data(), "20230601")
    meeting = SelectionMeeting(llm_caller=None, max_hunters=2)

    first = meeting.run_with_model_output(output)
    second = meeting.run_with_model_output(output)

    assert first["meeting_log"]["meeting_id"] == 1
    assert second["meeting_log"]["meeting_id"] == 2

