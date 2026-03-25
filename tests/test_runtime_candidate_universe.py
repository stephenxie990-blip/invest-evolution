import pandas as pd

from invest_evolution.investment.runtimes.candidate_universe import (
    build_candidate_universe,
    iter_candidate_universe_entries,
)


def _frame(trade_dates: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_date": trade_dates,
            "close": [10.0 + idx for idx, _ in enumerate(trade_dates)],
        }
    )


def test_candidate_universe_is_deterministic_and_filters_stale_entries():
    cutoff_date = "20240110"
    stock_data_a = {
        "sh.600003": _frame(["20240101", "20240102", "20240103"]),
        "sh.600001": _frame(["20240106", "20240108", "20240110"]),
        "sh.600004": _frame(["20240108"]),
        "sh.600002": _frame(["20240105", "20240108", "20240109"]),
    }
    stock_data_b = {
        "sh.600002": stock_data_a["sh.600002"],
        "sh.600004": stock_data_a["sh.600004"],
        "sh.600001": stock_data_a["sh.600001"],
        "sh.600003": stock_data_a["sh.600003"],
    }

    selected_a = build_candidate_universe(
        stock_data_a,
        cutoff_date=cutoff_date,
        candidate_pool_size=3,
        min_history_days=2,
        max_staleness_days=2,
    )
    selected_b = build_candidate_universe(
        stock_data_b,
        cutoff_date=cutoff_date,
        candidate_pool_size=3,
        min_history_days=2,
        max_staleness_days=2,
    )

    assert selected_a == ["sh.600001", "sh.600002"]
    assert selected_b == selected_a


def test_candidate_universe_exposes_history_and_staleness_metadata():
    entries = iter_candidate_universe_entries(
        {
            "sh.600001": _frame(["20240105", "20240108", "20240110"]),
            "sh.600002": _frame(["20240104", "20240105", "20240109"]),
        },
        cutoff_date="20240110",
        min_history_days=2,
        max_staleness_days=5,
    )

    assert [item.code for item in entries] == ["sh.600001", "sh.600002"]
    assert entries[0].staleness_days == 0
    assert entries[0].history_days == 3
    assert entries[1].staleness_days == 1
