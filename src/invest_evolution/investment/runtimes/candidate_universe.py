from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, cast

import pandas as pd

from invest_evolution.config import normalize_date


@dataclass(frozen=True)
class CandidateUniverseEntry:
    code: str
    last_trade_date: str
    history_days: int
    staleness_days: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _cutoff_datetime(cutoff_date: str) -> datetime:
    return datetime.strptime(normalize_date(cutoff_date), "%Y%m%d")


def _last_trade_date(df: pd.DataFrame, cutoff_date: str) -> str:
    if "trade_date" not in df.columns or df.empty:
        return ""
    raw_trade_dates = cast(pd.Series, df["trade_date"]).astype(str)
    trade_dates = pd.Series(
        [normalize_date(value) for value in raw_trade_dates.tolist()],
        index=raw_trade_dates.index,
    )
    eligible = cast(pd.Series, trade_dates[trade_dates <= normalize_date(cutoff_date)])
    return str(eligible.iloc[-1]) if not eligible.empty else ""


def _history_days(df: pd.DataFrame, cutoff_date: str) -> int:
    if "trade_date" not in df.columns or df.empty:
        return 0
    raw_trade_dates = cast(pd.Series, df["trade_date"]).astype(str)
    trade_dates = pd.Series(
        [normalize_date(value) for value in raw_trade_dates.tolist()],
        index=raw_trade_dates.index,
    )
    return int((trade_dates <= normalize_date(cutoff_date)).sum())


def _staleness_days(last_trade_date: str, cutoff_date: str) -> int:
    normalized_last = normalize_date(last_trade_date)
    normalized_cutoff = normalize_date(cutoff_date)
    try:
        return max(
            0,
            (_cutoff_datetime(normalized_cutoff) - _cutoff_datetime(normalized_last)).days,
        )
    except ValueError:
        if normalized_last <= normalized_cutoff:
            return 0
        return 10**9


def iter_candidate_universe_entries(
    stock_data: Dict[str, pd.DataFrame],
    *,
    cutoff_date: str,
    min_history_days: int = 20,
    max_staleness_days: int = 5,
) -> list[CandidateUniverseEntry]:
    entries: list[CandidateUniverseEntry] = []
    for code, df in dict(stock_data or {}).items():
        if df is None or df.empty:
            continue
        last_trade_date = _last_trade_date(df, cutoff_date)
        if not last_trade_date:
            continue
        history_days = _history_days(df, cutoff_date)
        if history_days < max(1, int(min_history_days)):
            continue
        staleness_days = _staleness_days(last_trade_date, cutoff_date)
        if staleness_days > max(0, int(max_staleness_days)):
            continue
        entries.append(
            CandidateUniverseEntry(
                code=str(code),
                last_trade_date=last_trade_date,
                history_days=history_days,
                staleness_days=staleness_days,
            )
        )
    return sorted(
        entries,
        key=lambda item: (
            item.staleness_days,
            -item.history_days,
            item.code,
        ),
    )


def build_candidate_universe(
    stock_data: Dict[str, pd.DataFrame],
    *,
    cutoff_date: str,
    candidate_pool_size: int,
    min_history_days: int = 20,
    max_staleness_days: int = 5,
) -> list[str]:
    entries = iter_candidate_universe_entries(
        stock_data,
        cutoff_date=cutoff_date,
        min_history_days=min_history_days,
        max_staleness_days=max_staleness_days,
    )
    return [
        item.code
        for item in entries[: max(1, int(candidate_pool_size))]
    ]
