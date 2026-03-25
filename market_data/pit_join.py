from __future__ import annotations

from typing import cast

import pandas as pd

from config import normalize_date

from .feature_availability import validate_market_cap_point_in_time

_FINANCIAL_COLUMNS = (
    "financial_report_date",
    "financial_publish_date",
    "roe",
    "net_profit",
    "revenue",
    "total_assets",
    "market_cap",
    "market_cap_available",
    "financial_source",
)


def _ensure_financial_columns(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for column in _FINANCIAL_COLUMNS:
        if column not in result.columns:
            result[column] = pd.NA
    return result


def _normalize_snapshot_dates(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["code"] = result["code"].astype(str)
    result["report_date"] = result["report_date"].astype(str).map(normalize_date)
    if "publish_date" in result.columns:
        publish = cast(pd.Series, result["publish_date"].astype(str).str.strip())
    else:
        publish = pd.Series("", index=result.index, dtype="object")
    result["publish_date"] = publish
    result["effective_date"] = publish.where(publish != "", result["report_date"]).map(normalize_date)
    return result


def join_financials_point_in_time(
    bars: pd.DataFrame,
    financial_snapshots: pd.DataFrame,
) -> pd.DataFrame:
    """Attach the latest available financial snapshot for each trade date."""
    if bars.empty:
        return _ensure_financial_columns(bars)
    required = {"code", "trade_date"}
    if not required.issubset(set(bars.columns)):
        return _ensure_financial_columns(bars)
    if financial_snapshots.empty or "code" not in financial_snapshots.columns or "report_date" not in financial_snapshots.columns:
        return _ensure_financial_columns(bars)

    left = cast(pd.DataFrame, bars.copy())
    left["_row_order"] = range(len(left))
    left["_trade_dt"] = pd.to_datetime(
        left["trade_date"].astype(str).map(normalize_date),
        format="%Y%m%d",
        errors="coerce",
    )

    right = cast(pd.DataFrame, _normalize_snapshot_dates(financial_snapshots))
    right = cast(pd.DataFrame, validate_market_cap_point_in_time(right))
    right = cast(
        pd.DataFrame,
        right.rename(
            columns={
                "report_date": "financial_report_date",
                "publish_date": "financial_publish_date",
                "source": "financial_source",
            }
        ),
    )
    for column in _FINANCIAL_COLUMNS:
        if column not in right.columns:
            right[column] = pd.NA
    right["_effective_dt"] = pd.to_datetime(
        right["effective_date"].astype(str),
        format="%Y%m%d",
        errors="coerce",
    )
    right = cast(pd.DataFrame, right[right["_effective_dt"].notna()].copy())
    if right.empty:
        return _ensure_financial_columns(bars)

    right = cast(pd.DataFrame, right.sort_values(by=["code", "_effective_dt", "financial_report_date"]))
    right = cast(pd.DataFrame, right.drop_duplicates(["code", "_effective_dt"], keep="last"))
    right = cast(
        pd.DataFrame,
        right[
            [
                "code",
                "_effective_dt",
                "financial_report_date",
                "financial_publish_date",
                "roe",
                "net_profit",
                "revenue",
                "total_assets",
                "market_cap",
                "market_cap_available",
                "financial_source",
            ]
        ],
    )

    valid = cast(pd.DataFrame, left[left["_trade_dt"].notna()].copy())
    invalid = cast(pd.DataFrame, left[left["_trade_dt"].isna()].copy())
    if valid.empty:
        return _ensure_financial_columns(bars)
    merged_groups: list[pd.DataFrame] = []
    for code, left_group in valid.groupby("code", sort=False):
        current_left = cast(pd.DataFrame, left_group.sort_values(by="_trade_dt").copy())
        current_right = cast(pd.DataFrame, right[right["code"] == str(code)].copy())
        if current_right.empty:
            merged_groups.append(_ensure_financial_columns(current_left))
            continue
        current_right = cast(pd.DataFrame, current_right.sort_values(by="_effective_dt").drop(columns=["code"]))
        merged_group = cast(
            pd.DataFrame,
            pd.merge_asof(
                current_left,
                current_right,
                left_on="_trade_dt",
                right_on="_effective_dt",
                direction="backward",
                allow_exact_matches=True,
            ),
        )
        merged_group = cast(pd.DataFrame, merged_group.drop(columns=["_effective_dt"], errors="ignore"))
        merged_groups.append(_ensure_financial_columns(merged_group))

    merged = cast(pd.DataFrame, pd.concat(merged_groups, ignore_index=True, sort=False))
    if not invalid.empty:
        invalid = _ensure_financial_columns(invalid)
        merged = cast(pd.DataFrame, pd.concat([merged, invalid], ignore_index=True, sort=False))

    merged = cast(
        pd.DataFrame,
        merged.sort_values(by="_row_order").drop(columns=["_row_order", "_trade_dt"], errors="ignore"),
    )
    return _ensure_financial_columns(merged)
