from __future__ import annotations

from typing import Any, cast

import pandas as pd

PIT_SAFE_MARKET_CAP_SOURCES = frozenset(
    {
        "pit_daily_basic",
        "point_in_time_market_cap",
    }
)


def _source_series(frame: pd.DataFrame, source_column: str) -> pd.Series:
    if source_column not in frame.columns:
        return pd.Series("", index=frame.index, dtype="object")
    return frame[source_column].astype(str).str.strip().str.lower()


def validate_market_cap_point_in_time(
    frame: pd.DataFrame,
    *,
    source_column: str = "source",
    market_cap_column: str = "market_cap",
    availability_column: str = "market_cap_available",
) -> pd.DataFrame:
    """Mask market_cap unless it originates from a point-in-time safe source."""
    result = frame.copy()
    if market_cap_column not in result.columns:
        result[market_cap_column] = pd.NA
    market_cap = cast(
        pd.Series,
        pd.to_numeric(result[market_cap_column], errors="coerce"),
    )
    safe_source = _source_series(result, source_column).isin(PIT_SAFE_MARKET_CAP_SOURCES)
    available = safe_source & market_cap.notna()
    result[market_cap_column] = market_cap.where(available)
    result[availability_column] = available.astype(bool)
    return result


def summarize_feature_availability(frame: pd.DataFrame) -> dict[str, dict[str, Any]]:
    if frame.empty:
        return {
            "market_cap": {
                "available": False,
                "coverage": 0.0,
                "reason": "missing_pit_safe_source",
            }
        }
    if "market_cap_available" in frame.columns:
        availability = cast(pd.Series, frame["market_cap_available"].astype(bool))
    else:
        availability = cast(
            pd.Series,
            validate_market_cap_point_in_time(frame)["market_cap_available"].astype(bool),
        )
    available = bool(availability.any())
    coverage = float(availability.sum()) / float(len(availability)) if len(availability) else 0.0
    return {
        "market_cap": {
            "available": available,
            "coverage": coverage,
            "reason": "" if available else "missing_pit_safe_source",
        }
    }
