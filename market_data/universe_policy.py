from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Sequence

from config import normalize_date

DEFAULT_MAX_STALENESS_DAYS = 20


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _staleness_days(cutoff_date: str, last_trade_date: str) -> int | None:
    if not cutoff_date or not last_trade_date:
        return None
    normalized_cutoff = normalize_date(cutoff_date)
    normalized_last = normalize_date(last_trade_date)
    try:
        cutoff = datetime.strptime(normalized_cutoff, "%Y%m%d")
        latest = datetime.strptime(normalized_last, "%Y%m%d")
    except ValueError:
        if normalized_last <= normalized_cutoff:
            return 0
        return None
    return (cutoff - latest).days


def _candidate_sort_key(candidate: Mapping[str, Any]) -> tuple[int, int, str]:
    return (
        -_to_int(candidate.get("history_days")),
        -_to_int(candidate.get("last_trade_date")),
        str(candidate.get("code") or ""),
    )


def select_universe_codes(
    *,
    candidates: Sequence[Mapping[str, Any]],
    cutoff_date: str,
    stock_count: int,
    min_history_days: int,
    max_staleness_days: int = DEFAULT_MAX_STALENESS_DAYS,
) -> list[str]:
    """Select a deterministic universe with explicit freshness constraints."""
    normalized_cutoff = normalize_date(cutoff_date)
    target_count = max(1, int(stock_count))
    minimum_history = max(1, int(min_history_days))
    max_staleness = max(0, int(max_staleness_days))
    eligible: list[dict[str, Any]] = []
    for raw in candidates:
        code = str(raw.get("code") or "").strip()
        if not code:
            continue
        history_days = _to_int(raw.get("history_days"))
        if history_days < minimum_history:
            continue
        last_trade_date = str(raw.get("last_trade_date") or "").strip()
        if not last_trade_date:
            continue
        normalized_last = normalize_date(last_trade_date)
        stale_days = _staleness_days(normalized_cutoff, normalized_last)
        if stale_days is None or stale_days < 0 or stale_days > max_staleness:
            continue
        eligible.append(
            {
                "code": code,
                "history_days": history_days,
                "last_trade_date": normalized_last,
            }
        )
    ranked = sorted(eligible, key=_candidate_sort_key)
    return [str(item["code"]) for item in ranked[:target_count]]
