"""Quality audit facade for the market data domain."""

from __future__ import annotations

from typing import Any, Protocol

from market_data.quality import DataQualityService


class QualityServiceLike(Protocol):
    def audit(
        self,
        *,
        use_snapshot: bool = True,
        force_refresh: bool = False,
        max_age_seconds: int = 300,
    ) -> dict[str, Any]:
        ...

    def persist_audit(self, *, force_refresh: bool = True) -> dict[str, Any]:
        ...


class QualityAuditService:
    """Explicit service facade for quality audits and persistence."""

    def __init__(self, quality_service: QualityServiceLike | None = None, **quality_kwargs: Any):
        self.quality_service = quality_service or DataQualityService(**quality_kwargs)

    def audit(
        self,
        *,
        use_snapshot: bool = True,
        force_refresh: bool = False,
        max_age_seconds: int = 300,
    ) -> dict[str, Any]:
        return self.quality_service.audit(
            use_snapshot=use_snapshot,
            force_refresh=force_refresh,
            max_age_seconds=max_age_seconds,
        )

    def persist_audit(self, *, force_refresh: bool = True) -> dict[str, Any]:
        return self.quality_service.persist_audit(force_refresh=force_refresh)
