from __future__ import annotations

from typing import Optional, cast

import pandas as pd

from config import normalize_date


def numeric_series(frame: pd.DataFrame, column: str) -> pd.Series:
    return cast(pd.Series, pd.to_numeric(frame[column], errors="coerce")).dropna()


def get_date_col(df: pd.DataFrame) -> Optional[str]:
    if "trade_date" in df.columns:
        return "trade_date"
    if "date" in df.columns:
        return "date"
    return None


def filter_by_cutoff(df: pd.DataFrame, cutoff_norm: str) -> pd.DataFrame:
    date_col = get_date_col(df)
    if date_col is None:
        return pd.DataFrame()
    dates_norm = df[date_col].apply(normalize_date)
    return df.loc[dates_norm <= cutoff_norm].copy()


__all__ = ["filter_by_cutoff", "get_date_col", "numeric_series"]
