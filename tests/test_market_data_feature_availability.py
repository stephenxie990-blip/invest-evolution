from __future__ import annotations

import pandas as pd

from invest_evolution.market_data.feature_availability import (
    PIT_SAFE_MARKET_CAP_SOURCES,
    summarize_feature_availability,
    validate_market_cap_point_in_time,
)


def test_validate_market_cap_point_in_time_masks_unsafe_sources():
    frame = pd.DataFrame(
        [
            {"code": "sh.600010", "market_cap": 1234.5, "source": "tushare"},
            {"code": "sh.600011", "market_cap": 2345.6, "source": "akshare"},
        ]
    )

    validated = validate_market_cap_point_in_time(frame)

    assert bool(validated["market_cap_available"].any()) is False
    assert validated["market_cap"].isna().all()

    summary = summarize_feature_availability(validated)
    assert summary["market_cap"]["available"] is False
    assert summary["market_cap"]["reason"] == "missing_pit_safe_source"


def test_validate_market_cap_point_in_time_keeps_safe_sources():
    safe_source = next(iter(PIT_SAFE_MARKET_CAP_SOURCES))
    frame = pd.DataFrame(
        [
            {"code": "sh.600010", "market_cap": 1234.5, "source": safe_source},
        ]
    )

    validated = validate_market_cap_point_in_time(frame)

    assert bool(validated.loc[0, "market_cap_available"]) is True
    assert float(validated.loc[0, "market_cap"]) == 1234.5

