from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from .repository import MarketDataRepository


class DataQualityService:
    """Structured health checks for the canonical market-data repository."""

    META_KEYS = (
        "last_security_sync",
        "security_master_source",
        "last_daily_bar_sync",
        "daily_bar_latest_date",
        "daily_bar_source",
        "last_index_bar_sync",
        "index_bar_latest_date",
        "index_bar_source",
        "last_financial_snapshot_sync",
        "financial_snapshot_source",
        "last_quality_audit",
        "data_health_status",
        "data_health_summary",
    )

    def __init__(self, repository: MarketDataRepository | None = None, db_path: str | None = None):
        self.repository = repository or MarketDataRepository(db_path)
        self.repository.initialize_schema()

    def audit(self) -> dict[str, Any]:
        status = self.repository.get_status_summary()
        date_range = self.repository.get_available_date_range()
        meta = self.repository.get_meta(self.META_KEYS)
        index_date_range = self.repository.get_index_available_date_range()
        checks = {
            "has_security_master": status["stock_count"] > 0,
            "has_daily_bars": status["kline_count"] > 0,
            "has_index_bars": status.get("index_kline_count", 0) > 0,
            "has_latest_date": bool(status["latest_date"]),
            "date_range_valid": bool(date_range[0] and date_range[1]),
            "index_date_range_valid": bool(index_date_range[0] and index_date_range[1]) if status.get("index_kline_count", 0) > 0 else True,
        }
        issues = []
        if not checks["has_security_master"]:
            issues.append("security_master is empty")
        if not checks["has_daily_bars"]:
            issues.append("daily_bar is empty")
        if checks["date_range_valid"] and date_range[0] > date_range[1]:
            issues.append("date range is invalid")
        healthy = not issues
        return {
            "status": status,
            "date_range": {"min": date_range[0], "max": date_range[1]},
            "index_date_range": {"min": index_date_range[0], "max": index_date_range[1]},
            "meta": meta,
            "checks": checks,
            "issues": issues,
            "healthy": healthy,
            "health_status": "healthy" if healthy else "degraded",
            "has_data": bool(status["kline_count"]),
        }

    def persist_audit(self) -> dict[str, Any]:
        result = self.audit()
        summary = {
            "healthy": result["healthy"],
            "issues": result["issues"],
            "latest_date": result["status"].get("latest_date", ""),
            "stock_count": result["status"].get("stock_count", 0),
            "kline_count": result["status"].get("kline_count", 0),
        }
        self.repository.upsert_meta(
            {
                "last_quality_audit": datetime.now().isoformat(timespec="seconds"),
                "data_health_status": result["health_status"],
                "data_health_summary": json.dumps(summary, ensure_ascii=False),
            }
        )
        return result
