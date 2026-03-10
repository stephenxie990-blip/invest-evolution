import logging
import random
import time
from datetime import datetime, timedelta
from typing import Any, Dict, Sequence

import pandas as pd

from config import normalize_date
from .quality import DataQualityService
from .repository import MarketDataRepository

logger = logging.getLogger(__name__)

_NUMERIC_COLUMNS = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "pct_chg",
    "turnover",
)
_CAPITAL_FLOW_COLUMNS = (
    "close",
    "pct_chg",
    "main_net_inflow",
    "main_net_inflow_ratio",
    "super_large_net_inflow",
    "super_large_net_inflow_ratio",
    "large_net_inflow",
    "large_net_inflow_ratio",
    "medium_net_inflow",
    "medium_net_inflow_ratio",
    "small_net_inflow",
    "small_net_inflow_ratio",
)


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------


def _query_end_date(cutoff_date: str, include_future_days: int) -> str:
    cutoff = normalize_date(cutoff_date)
    if include_future_days <= 0:
        return cutoff
    cutoff_dt = datetime.strptime(cutoff, "%Y%m%d")
    return (cutoff_dt + timedelta(days=include_future_days * 2)).strftime("%Y%m%d")


def _query_start_date(cutoff_date: str, history_days: int) -> str:
    cutoff = normalize_date(cutoff_date)
    cutoff_dt = datetime.strptime(cutoff, "%Y%m%d")
    return (cutoff_dt - timedelta(days=history_days)).strftime("%Y%m%d")


# ---------------------------------------------------------------------------
# Frame normalisation
# ---------------------------------------------------------------------------


def normalize_stock_frame(df: pd.DataFrame, code: str) -> pd.DataFrame:
    """Normalise a single-stock DataFrame into canonical column order."""
    if df.empty:
        return df.copy()

    result = df.copy()
    result["code"] = code
    result["trade_date"] = result["trade_date"].astype(str).map(normalize_date)
    for column in _NUMERIC_COLUMNS:
        if column in result.columns:
            result[column] = pd.to_numeric(result[column], errors="coerce")
        else:
            result[column] = pd.NA

    computed_pct = result["close"].pct_change().fillna(0) * 100
    if result["pct_chg"].isna().all():
        result["pct_chg"] = computed_pct
    else:
        result["pct_chg"] = result["pct_chg"].fillna(computed_pct)

    dt = pd.to_datetime(result["trade_date"], format="%Y%m%d", errors="coerce")
    result["date"] = dt.dt.strftime("%Y-%m-%d").fillna(result["trade_date"])
    ordered = [
        "date",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "pct_chg",
        "turnover",
        "code",
    ]
    extra = [column for column in result.columns if column not in ordered]
    return result.sort_values("trade_date").reset_index(drop=True)[ordered + extra]


# ---------------------------------------------------------------------------
# groupby-based split (KV Cache pattern)
# ---------------------------------------------------------------------------


def _split_by_code(
    df: pd.DataFrame,
    *,
    min_rows: int = 0,
    cutoff: str | None = None,
    min_history_days: int = 0,
) -> Dict[str, pd.DataFrame]:
    """Split a multi-stock DataFrame into per-stock normalised frames.

    Uses ``groupby`` for O(M) single-pass hash partition instead of
    N × O(M) boolean index scans.
    """
    if df.empty:
        return {}

    if df["code"].dtype == "object":
        df = df.copy()
        df["code"] = df["code"].astype("category")

    stock_data: Dict[str, pd.DataFrame] = {}
    for code, group_df in df.groupby("code", observed=True):
        code = str(code)
        stock_df = normalize_stock_frame(group_df, code)
        if stock_df.empty:
            continue
        if min_rows and len(stock_df) < min_rows:
            continue
        if cutoff and min_history_days:
            history_count = int((stock_df["trade_date"] <= cutoff).sum())
            if history_count < max(1, min_history_days):
                continue
        stock_data[code] = stock_df

    return stock_data


# ---------------------------------------------------------------------------
# Lean merge with vectorised key filtering
#
# Previous version used a Python list comprehension over 900万 rows:
#   mask = [(c, d) in existing_keys for c, d in zip(right_code, right_date)]
#   → Pure Python loop, ~25 seconds for 900万 rows
#
# New version uses Pandas string concat + Series.isin():
#   → Executed entirely in C/Cython, ~0.5-1 second for 900万 rows
#
# LLM analogy — FlashAttention vs naive attention:
#   Naive:  Python-level loop over attention scores → slow
#   Flash:  Fused CUDA kernel, same math in hardware → 10-50× faster
# ---------------------------------------------------------------------------


def _lean_merge_with_keyset(
    combined: pd.DataFrame,
    right_df: pd.DataFrame,
    merge_key: list[str],
    existing_keyset: set[str],
    suffix: str,
) -> pd.DataFrame:
    """Filter right_df to matching keys then merge, all vectorised.

    Parameters
    ----------
    combined : pd.DataFrame
        The left side (daily bar data), already has string code/trade_date.
    right_df : pd.DataFrame
        The right side (factors/status/capital_flow), potentially 900万+ rows.
    merge_key : list[str]
        Column names for the composite key, e.g. ["code", "trade_date"].
    existing_keyset : set[str]
        Pre-built set of "code\x00trade_date" strings from combined.
        Built once, shared across all merge calls.
    suffix : str
        Suffix for duplicate columns, e.g. "_factor".

    Returns
    -------
    pd.DataFrame
        combined with right_df columns joined via left merge.
    """
    if right_df.empty:
        return combined

    t0 = time.perf_counter()

    # ── Step A: Ensure string types for consistent key building ──
    right_df = right_df.copy()
    for col in merge_key:
        if col in right_df.columns:
            right_df[col] = right_df[col].astype(str)

    rows_before = len(right_df)

    # ── Step B: Vectorised key filtering ──
    # Build composite key using a separator that cannot appear in
    # stock codes or dates. This turns the 2-column key match into
    # a single-column isin() check, which Pandas executes in C via
    # a hash table internally — no Python loop at all.
    #
    # Performance: 900万 rows → ~0.5s (vs ~25s for Python list comprehension)
    _SEP = "\x00"
    right_composite = right_df[merge_key[0]].str.cat(
        right_df[merge_key[1]], sep=_SEP
    )
    mask = right_composite.isin(existing_keyset)
    filtered = right_df.loc[mask]
    t1 = time.perf_counter()

    rows_after = len(filtered)
    reduction_pct = (1 - rows_after / max(rows_before, 1)) * 100

    if filtered.empty:
        logger.debug(
            "[lean_merge] %s: %d→0 rows, skip", suffix, rows_before
        )
        return combined

    # ── Step C: Deduplicate on merge key ──
    filtered = filtered.drop_duplicates(subset=merge_key, keep="last")
    t2 = time.perf_counter()

    # ── Step D: Merge (on much smaller filtered data) ──
    result = combined.merge(
        filtered,
        on=merge_key,
        how="left",
        suffixes=("", suffix),
    )
    t3 = time.perf_counter()

    logger.debug(
        "[lean_merge] %s: %d→%d rows (%.0f%% cut), "
        "filter=%.2fs dedup=%.2fs merge=%.2fs total=%.2fs",
        suffix,
        rows_before,
        rows_after,
        reduction_pct,
        t1 - t0,
        t2 - t1,
        t3 - t2,
        t3 - t0,
    )
    return result


# ---------------------------------------------------------------------------
# Build the composite key-set used by _lean_merge_with_keyset
# ---------------------------------------------------------------------------


def _build_keyset(combined: pd.DataFrame, merge_key: list[str]) -> set[str]:
    """Build a set of composite key strings from combined DataFrame.

    Uses Pandas str.cat() for vectorised string concatenation (C-level),
    then converts to a Python frozenset for O(1) lookup.

    For 500万 rows this takes ~0.8s (vs ~2.0s for Python zip+set).
    """
    _SEP = "\x00"
    composite = combined[merge_key[0]].astype(str).str.cat(
        combined[merge_key[1]].astype(str), sep=_SEP
    )
    return set(composite)


# ---------------------------------------------------------------------------
# Point-in-time enrichment
# ---------------------------------------------------------------------------


def _attach_point_in_time_context(
    repository: MarketDataRepository,
    stock_frames: Dict[str, pd.DataFrame],
    cutoff_date: str,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    include_capital_flow: bool = False,
) -> Dict[str, pd.DataFrame]:
    """Enrich per-stock frames with securities, financials, factors, status, capital flow.

    Optimisation tiers:

    1. Securities & financials: vectorised Series.map() (code-level, no merge)
    2. Factors/status/capital_flow: lean merge with vectorised isin filtering
       - Build composite key-set ONCE from combined (str.cat, ~0.8s)
       - Filter each right-side table via Series.isin (C-level, ~0.5s per table)
       - Merge on the filtered (much smaller) DataFrame (~2-3s per table)
    3. Final split: groupby (single hash pass)
    """
    if not stock_frames:
        return stock_frames

    t_total_start = time.perf_counter()
    codes = list(stock_frames.keys())

    # ══════════════════════════════════════════════════════════════
    # Step 1: Concat all frames
    # ══════════════════════════════════════════════════════════════
    combined = pd.concat(stock_frames.values(), ignore_index=True, copy=False)
    if "code" not in combined.columns or combined.empty:
        return stock_frames

    combined["code"] = combined["code"].astype(str)
    combined["trade_date"] = combined["trade_date"].astype(str)
    t1 = time.perf_counter()
    logger.debug(
        "[enrich] Step 1 concat: %d rows in %.2fs",
        len(combined),
        t1 - t_total_start,
    )

    # ══════════════════════════════════════════════════════════════
    # Step 2: Securities & Financials — vectorised map
    # ══════════════════════════════════════════════════════════════
    securities = {row["code"]: row for row in repository.query_securities(codes)}
    financials = {
        row["code"]: row
        for row in repository.query_latest_financial_snapshots(codes, cutoff_date)
    }

    code_series = combined["code"]
    for key in ("name", "industry", "list_date", "delist_date"):
        lookup = {c: meta.get(key) for c, meta in securities.items()}
        combined[key] = code_series.map(lookup)

    is_st_lookup = {
        c: int(bool(meta.get("is_st", 0))) for c, meta in securities.items()
    }
    combined["is_st_master"] = code_series.map(is_st_lookup).fillna(0).astype(int)

    for fin_key, fin_col in [
        ("report_date", "financial_report_date"),
        ("publish_date", "financial_publish_date"),
        ("roe", "roe"),
        ("net_profit", "net_profit"),
        ("revenue", "revenue"),
        ("total_assets", "total_assets"),
        ("market_cap", "market_cap"),
    ]:
        lookup = {c: fin.get(fin_key) for c, fin in financials.items()}
        combined[fin_col] = code_series.map(lookup)

    t2 = time.perf_counter()
    logger.debug("[enrich] Step 2 securities+financials: %.2fs", t2 - t1)

    # ══════════════════════════════════════════════════════════════
    # Step 3: Build composite key-set ONCE (vectorised)
    #
    # Old: set(zip(code, date)) → Python loop, ~2.0s for 500万 rows
    # New: str.cat() + set()    → C-level concat, ~0.8s for 500万 rows
    # ══════════════════════════════════════════════════════════════
    merge_key = ["code", "trade_date"]
    existing_keyset = _build_keyset(combined, merge_key)
    t_keyset = time.perf_counter()
    logger.debug(
        "[enrich] Key-set built: %d unique pairs in %.2fs",
        len(existing_keyset),
        t_keyset - t2,
    )

    # ══════════════════════════════════════════════════════════════
    # Step 3a: Factors — lean merge
    #
    # Old: Python list comprehension filter → ~20s
    # New: Series.isin(keyset) → ~0.5s filter + ~3s merge
    # ══════════════════════════════════════════════════════════════
    factor_df = repository.query_factor_snapshots(
        codes=codes, start_date=start_date, end_date=end_date
    )
    if not factor_df.empty:
        combined = _lean_merge_with_keyset(
            combined, factor_df, merge_key, existing_keyset, "_factor"
        )
    t3a = time.perf_counter()
    logger.debug("[enrich] Step 3a factors: %.2fs", t3a - t_keyset)

    # ══════════════════════════════════════════════════════════════
    # Step 3b: Status — lean merge (reuses same keyset)
    # ══════════════════════════════════════════════════════════════
    status_df = repository.query_security_status_daily(
        codes=codes, start_date=start_date, end_date=end_date
    )
    if not status_df.empty:
        combined = _lean_merge_with_keyset(
            combined, status_df, merge_key, existing_keyset, "_status"
        )
    t3b = time.perf_counter()
    logger.debug("[enrich] Step 3b status: %.2fs", t3b - t3a)

    # ══════════════════════════════════════════════════════════════
    # Step 3c: Capital Flow — lean merge (if enabled)
    # ══════════════════════════════════════════════════════════════
    if include_capital_flow:
        capital_flow_df = repository.query_capital_flow_daily(
            codes=codes, start_date=start_date, end_date=end_date
        )
        if not capital_flow_df.empty:
            combined = _lean_merge_with_keyset(
                combined, capital_flow_df, merge_key, existing_keyset, "_capital_flow"
            )
    t3c = time.perf_counter()
    logger.debug("[enrich] Step 3c capital_flow: %.2fs", t3c - t3b)

    # ══════════════════════════════════════════════════════════════
    # Step 4: Split back into per-stock dict
    # ══════════════════════════════════════════════════════════════
    combined["code"] = combined["code"].astype("category")
    enriched: Dict[str, pd.DataFrame] = {
        str(code): group.reset_index(drop=True)
        for code, group in combined.groupby("code", observed=True)
    }

    t4 = time.perf_counter()
    elapsed = t4 - t_total_start
    logger.info(
        "[enrich] Done: %d stocks, %d rows in %.2fs "
        "(concat=%.1fs, meta=%.1fs, keyset=%.1fs, "
        "factors=%.1fs, status=%.1fs, flow=%.1fs, split=%.1fs)",
        len(enriched),
        len(combined),
        elapsed,
        t1 - t_total_start,
        t2 - t1,
        t_keyset - t2,
        t3a - t_keyset,
        t3b - t3a,
        t3c - t3b,
        t4 - t3c,
    )
    if elapsed > 15:
        logger.warning(
            "[enrich] Slow enrichment: %.1fs for %d stocks",
            elapsed,
            len(enriched),
        )

    return enriched


# ---------------------------------------------------------------------------
# TrainingDatasetBuilder
# ---------------------------------------------------------------------------


class TrainingDatasetBuilder:
    """Read-side dataset builder for training and backtesting."""

    def __init__(
        self,
        repository: MarketDataRepository | None = None,
        db_path: str | None = None,
    ):
        self.repository = repository or MarketDataRepository(db_path)
        self.repository.initialize_schema()

    @property
    def available(self) -> bool:
        return self.repository.has_daily_bars()

    def get_stocks(
        self,
        cutoff_date: str,
        stock_count: int = 50,
        min_history_days: int = 200,
        include_future_days: int = 0,
        include_capital_flow: bool = False,
    ) -> Dict[str, pd.DataFrame]:
        t_start = time.perf_counter()

        # Phase 1: select candidate codes
        codes = self.repository.select_codes_with_history(
            cutoff_date, min_history_days, stock_count
        )
        if not codes:
            return {}
        t_codes = time.perf_counter()
        logger.info(
            "[get_stocks] Phase 1: selected %d codes in %.2fs",
            len(codes),
            t_codes - t_start,
        )

        # Phase 2: batch load from DB
        end_date = _query_end_date(cutoff_date, include_future_days)
        df = self.repository.query_daily_bars(codes=codes, end_date=end_date)
        t_load = time.perf_counter()
        logger.info(
            "[get_stocks] Phase 2: loaded %d rows from DB in %.2fs",
            len(df),
            t_load - t_codes,
        )

        # Phase 3: split via groupby
        cutoff = normalize_date(cutoff_date)
        stock_data = _split_by_code(
            df,
            cutoff=cutoff,
            min_history_days=min_history_days,
        )
        t_split = time.perf_counter()
        logger.info(
            "[get_stocks] Phase 3: split %d/%d stocks in %.2fs",
            len(stock_data),
            len(codes),
            t_split - t_load,
        )

        # Phase 4: enrich
        result = _attach_point_in_time_context(
            self.repository,
            stock_data,
            cutoff_date,
            end_date=end_date,
            include_capital_flow=include_capital_flow,
        )
        t_done = time.perf_counter()
        elapsed = t_done - t_start
        logger.info(
            "[get_stocks] Done: %d stocks, total %.2fs "
            "(codes=%.1fs, db=%.1fs, split=%.1fs, enrich=%.1fs)",
            len(result),
            elapsed,
            t_codes - t_start,
            t_load - t_codes,
            t_split - t_load,
            t_done - t_split,
        )
        if elapsed > 30:
            logger.warning("[get_stocks] Slow load: %.1fs", elapsed)
        return result

    def get_stock(
        self,
        code: str,
        cutoff_date: str | None = None,
        *,
        include_capital_flow: bool = False,
    ) -> pd.DataFrame | None:
        df = self.repository.get_stock(code, cutoff_date=cutoff_date)
        if df.empty:
            return None
        normalized_cutoff = cutoff_date or normalize_date(df["trade_date"].max())
        result = {code: normalize_stock_frame(df, code)}
        enriched = _attach_point_in_time_context(
            self.repository,
            result,
            normalized_cutoff,
            end_date=normalized_cutoff,
            include_capital_flow=include_capital_flow,
        )
        return enriched.get(code)

    def get_available_date_range(self) -> tuple[str | None, str | None]:
        return self.repository.get_available_date_range()

    def get_stock_count(self) -> int:
        return self.repository.get_stock_count()


# ---------------------------------------------------------------------------
# CapitalFlowDatasetService
# ---------------------------------------------------------------------------


class CapitalFlowDatasetService:
    """Read-only daily capital-flow service for optional factor enhancement."""

    def __init__(
        self,
        repository: MarketDataRepository | None = None,
        db_path: str | None = None,
    ):
        self.repository = repository or MarketDataRepository(db_path)
        self.repository.initialize_schema()

    def get_capital_flow(
        self,
        codes: Sequence[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        frame = self.repository.query_capital_flow_daily(
            codes=codes, start_date=start_date, end_date=end_date
        )
        if frame.empty:
            return frame
        for column in _CAPITAL_FLOW_COLUMNS:
            if column in frame.columns:
                frame[column] = pd.to_numeric(frame[column], errors="coerce")
        return frame

    def get_capital_flow_by_code(
        self,
        code: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        return self.get_capital_flow(
            codes=[code], start_date=start_date, end_date=end_date
        )

    def attach_to_daily_frames(
        self, stock_frames: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.DataFrame]:
        if not stock_frames:
            return stock_frames
        codes = list(stock_frames.keys())
        all_dates = [
            frame["trade_date"].astype(str)
            for frame in stock_frames.values()
            if not frame.empty
        ]
        if not all_dates:
            return stock_frames
        start_date = min(series.min() for series in all_dates)
        end_date = max(series.max() for series in all_dates)
        return _attach_point_in_time_context(
            self.repository,
            stock_frames,
            cutoff_date=end_date,
            start_date=start_date,
            end_date=end_date,
            include_capital_flow=True,
        )


# ---------------------------------------------------------------------------
# EventDatasetService
# ---------------------------------------------------------------------------


class EventDatasetService:
    """Read-only event dataset service for sparse event tables such as 龙虎榜."""

    def __init__(
        self,
        repository: MarketDataRepository | None = None,
        db_path: str | None = None,
    ):
        self.repository = repository or MarketDataRepository(db_path)
        self.repository.initialize_schema()

    def get_dragon_tiger_events(
        self,
        codes: Sequence[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        frame = self.repository.query_dragon_tiger_list(
            codes=codes, start_date=start_date, end_date=end_date
        )
        if frame.empty:
            return frame
        numeric_columns = [
            "close",
            "pct_chg",
            "net_buy",
            "buy_amount",
            "sell_amount",
            "turnover_amount",
            "market_turnover_amount",
            "net_buy_ratio",
            "turnover_ratio",
            "turnover_rate",
            "float_market_cap",
            "next_day_return",
            "next_2day_return",
            "next_5day_return",
            "next_10day_return",
        ]
        for column in numeric_columns:
            if column in frame.columns:
                frame[column] = pd.to_numeric(frame[column], errors="coerce")
        return frame

    def get_event_summary(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        frame = self.get_dragon_tiger_events(
            start_date=start_date, end_date=end_date
        )
        return {
            "row_count": int(len(frame)),
            "stock_count": (
                int(frame["code"].nunique()) if not frame.empty else 0
            ),
            "latest_date": (
                str(frame["trade_date"].max()) if not frame.empty else ""
            ),
        }


# ---------------------------------------------------------------------------
# IntradayDatasetBuilder
# ---------------------------------------------------------------------------


class IntradayDatasetBuilder:
    """Read-only intraday dataset builder for 60-minute bars."""

    def __init__(
        self,
        repository: MarketDataRepository | None = None,
        db_path: str | None = None,
    ):
        self.repository = repository or MarketDataRepository(db_path)
        self.repository.initialize_schema()

    def get_bars(
        self,
        codes: Sequence[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        frame = self.repository.query_intraday_bars_60m(
            codes=codes, start_date=start_date, end_date=end_date
        )
        if frame.empty:
            return frame
        for column in ("open", "high", "low", "close", "volume", "amount"):
            if column in frame.columns:
                frame[column] = pd.to_numeric(frame[column], errors="coerce")
        return frame

    def get_stock_bars(
        self,
        code: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        return self.get_bars(
            codes=[code], start_date=start_date, end_date=end_date
        )

    def get_bar_summary(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        frame = self.get_bars(start_date=start_date, end_date=end_date)
        return {
            "row_count": int(len(frame)),
            "stock_count": (
                int(frame["code"].nunique()) if not frame.empty else 0
            ),
            "latest_date": (
                str(frame["trade_date"].max()) if not frame.empty else ""
            ),
        }


# ---------------------------------------------------------------------------
# WebDatasetService
# ---------------------------------------------------------------------------


class WebDatasetService:
    """Read-only status/query service for web endpoints."""

    def __init__(
        self,
        repository: MarketDataRepository | None = None,
        db_path: str | None = None,
    ):
        self.repository = repository or MarketDataRepository(db_path)
        self.repository.initialize_schema()
        self.capital_flow = CapitalFlowDatasetService(repository=self.repository)
        self.events = EventDatasetService(repository=self.repository)
        self.intraday = IntradayDatasetBuilder(repository=self.repository)

    def get_status_summary(self, *, refresh: bool = False) -> dict[str, Any]:
        summary = self.repository.get_status_summary(use_snapshot=not refresh)
        quality = DataQualityService(repository=self.repository).audit(
            use_snapshot=not refresh, force_refresh=refresh
        )
        summary["quality"] = {
            "healthy": quality["healthy"],
            "health_status": quality["health_status"],
            "issues": quality["issues"],
            "date_range": quality["date_range"],
            "meta": quality["meta"],
            "detail_mode": "slow" if refresh else "fast",
        }
        summary["detail_mode"] = "slow" if refresh else "fast"
        return summary

    def get_capital_flow(
        self,
        codes: Sequence[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        return self.capital_flow.get_capital_flow(
            codes=codes, start_date=start_date, end_date=end_date
        )

    def get_dragon_tiger_events(
        self,
        codes: Sequence[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        return self.events.get_dragon_tiger_events(
            codes=codes, start_date=start_date, end_date=end_date
        )

    def get_intraday_60m_bars(
        self,
        codes: Sequence[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        return self.intraday.get_bars(
            codes=codes, start_date=start_date, end_date=end_date
        )


# ---------------------------------------------------------------------------
# T0DatasetBuilder
# ---------------------------------------------------------------------------


class T0DatasetBuilder:
    """T0-aware dataset builder backed by the canonical repository."""

    def __init__(
        self,
        repository: MarketDataRepository | None = None,
        db_path: str | None = None,
    ):
        self.repository = repository or MarketDataRepository(db_path)
        self.repository.initialize_schema()

    def get_pool_at_date(self, cutoff_date: str) -> list[str]:
        return self.repository.get_security_pool_at_date(cutoff_date)

    def get_survived_stocks(
        self, cutoff_date: str, stocks: Sequence[str]
    ) -> dict[str, bool]:
        return self.repository.get_survival_flags(cutoff_date, stocks)

    def load_data_at_t0(
        self,
        cutoff_date: str,
        max_stocks: int = 500,
        history_days: int = 800,
        future_days: int = 90,
        include_capital_flow: bool = False,
    ) -> dict[str, Any]:
        t_start = time.perf_counter()

        pool = self.get_pool_at_date(cutoff_date)
        if max_stocks and len(pool) > max_stocks:
            pool = random.sample(pool, max_stocks)

        if not pool:
            return {
                "cutoff_date": normalize_date(cutoff_date),
                "stocks": {},
                "survived": {},
            }

        # Phase 1: batch load
        start_date = _query_start_date(cutoff_date, history_days)
        end_date = _query_end_date(cutoff_date, future_days)
        df = self.repository.query_daily_bars(
            codes=pool, start_date=start_date, end_date=end_date
        )
        t_load = time.perf_counter()
        logger.info(
            "[load_data_at_t0] Loaded %d rows in %.2fs",
            len(df),
            t_load - t_start,
        )

        # Phase 2: split
        stock_data = _split_by_code(df, min_rows=100)
        t_split = time.perf_counter()
        logger.info(
            "[load_data_at_t0] Split %d/%d stocks in %.2fs",
            len(stock_data),
            len(pool),
            t_split - t_load,
        )

        # Phase 3: enrich
        stock_data = _attach_point_in_time_context(
            self.repository,
            stock_data,
            cutoff_date,
            start_date=start_date,
            end_date=end_date,
            include_capital_flow=include_capital_flow,
        )

        survived = self.get_survived_stocks(
            cutoff_date, list(stock_data.keys())
        )
        elapsed = time.perf_counter() - t_start
        logger.info(
            "[load_data_at_t0] Done: %d stocks in %.2fs",
            len(stock_data),
            elapsed,
        )
        return {
            "cutoff_date": normalize_date(cutoff_date),
            "stocks": stock_data,
            "survived": survived,
        }