from typing import Any

from data_repository import MarketDataRepository


class DataQualityService:
    """Structured health checks for the canonical market-data repository."""

    def __init__(self, repository: MarketDataRepository | None = None, db_path: str | None = None):
        self.repository = repository or MarketDataRepository(db_path)
        self.repository.initialize_schema()
        self.repository.migrate_legacy_tables()

    def audit(self) -> dict[str, Any]:
        status = self.repository.get_status_summary()
        date_range = self.repository.get_available_date_range()
        return {
            "status": status,
            "date_range": {"min": date_range[0], "max": date_range[1]},
            "has_data": bool(status["kline_count"]),
            "legacy_tables": status["legacy_tables"],
        }
