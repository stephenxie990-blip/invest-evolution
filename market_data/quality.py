from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from .repository import MarketDataRepository

_QUALITY_AUDIT_SNAPSHOT_KEY = "quality_audit_snapshot"
_QUALITY_AUDIT_UPDATED_AT_KEY = "quality_audit_updated_at"
_QUALITY_AUDIT_MAX_AGE_SECONDS = 300


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
        "last_calendar_sync",
        "calendar_source",
        "last_status_sync",
        "status_source",
        "last_factor_sync",
        "factor_source",
        "last_quality_audit",
        "data_health_status",
        "data_health_summary",
    )

    def __init__(self, repository: MarketDataRepository | None = None, db_path: str | None = None):
        self.repository = repository or MarketDataRepository(db_path)
        self.repository.initialize_schema()

    def _compute_audit_payload(self) -> dict[str, Any]:
        status = self.repository.get_status_summary()
        date_range = self.repository.get_available_date_range()
        meta = self.repository.get_meta(self.META_KEYS)
        index_date_range = self.repository.get_index_available_date_range()
        checks = {
            "has_security_master": status["stock_count"] > 0,
            "has_daily_bars": status["kline_count"] > 0,
            "has_index_bars": status.get("index_kline_count", 0) > 0,
            "has_financial_snapshots": status.get("financial_count", 0) > 0,
            "has_trading_calendar": status.get("calendar_count", 0) > 0,
            "has_security_status": status.get("status_count", 0) > 0,
            "has_factor_snapshots": status.get("factor_count", 0) > 0,
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

    def audit(self, *, use_snapshot: bool = True, force_refresh: bool = False, max_age_seconds: int = _QUALITY_AUDIT_MAX_AGE_SECONDS) -> dict[str, Any]:
        if use_snapshot and not force_refresh:
            meta = self.repository.get_meta([_QUALITY_AUDIT_SNAPSHOT_KEY, _QUALITY_AUDIT_UPDATED_AT_KEY])
            raw_snapshot = meta.get(_QUALITY_AUDIT_SNAPSHOT_KEY, "")
            updated_at = meta.get(_QUALITY_AUDIT_UPDATED_AT_KEY, "")
            if raw_snapshot and updated_at:
                try:
                    age = (datetime.now() - datetime.fromisoformat(updated_at)).total_seconds()
                    if age <= max(0, int(max_age_seconds)):
                        payload = json.loads(raw_snapshot)
                        if isinstance(payload, dict):
                            payload.setdefault("meta", self.repository.get_meta(self.META_KEYS))
                            return payload
                except Exception:
                    pass
        result = self._compute_audit_payload()
        try:
            snapshot = dict(result)
            self.repository.upsert_meta({
                _QUALITY_AUDIT_SNAPSHOT_KEY: json.dumps(snapshot, ensure_ascii=False),
                _QUALITY_AUDIT_UPDATED_AT_KEY: datetime.now().isoformat(timespec="seconds"),
            })
        except Exception:
            pass
        return result

    def persist_audit(self, *, force_refresh: bool = True) -> dict[str, Any]:
        result = self.audit(use_snapshot=not force_refresh, force_refresh=force_refresh)
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
