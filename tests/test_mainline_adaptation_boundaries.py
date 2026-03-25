from pathlib import Path
from types import SimpleNamespace

import app.stock_analysis as stock_analysis_module
from app.stock_analysis_research_services import (
    StockAnalysisResearchServices,
    build_stock_analysis_research_services,
)
from app.stock_analysis_batch_service import (
    BatchAnalysisViewService,
)
from app.stock_analysis_research_resolution_service import (
    ResearchResolutionService,
)
from app.stock_analysis_services import (
    BatchAnalysisViewService as CompatBatchAnalysisViewService,
    ResearchResolutionService as CompatResearchResolutionService,
)
from app.stock_analysis_support_services import (
    StockAnalysisSupportServices,
    build_stock_analysis_support_services,
)


def test_mainline_stock_support_services_bundle_builds_expected_service_type():
    bundle = build_stock_analysis_support_services(
        humanize_macd_cross=lambda value: f"pretty:{value}",
    )

    assert isinstance(bundle, StockAnalysisSupportServices)
    assert isinstance(bundle.batch_analysis_service, BatchAnalysisViewService)


def test_mainline_stock_research_services_bundle_builds_expected_service_type():
    bundle = build_stock_analysis_research_services(
        case_store=SimpleNamespace(name="case_store"),
        scenario_engine=SimpleNamespace(name="scenario_engine"),
        attribution_engine=SimpleNamespace(name="attribution_engine"),
        logger=SimpleNamespace(name="logger"),
    )

    assert isinstance(bundle, StockAnalysisResearchServices)
    assert isinstance(bundle.research_resolution_service, ResearchResolutionService)


def test_mainline_stock_analysis_facade_uses_composition_helpers():
    source = Path("app/stock_analysis.py").read_text(encoding="utf-8")

    assert "from app.stock_analysis_support_services import (" in source
    assert "from app.stock_analysis_research_services import (" in source
    assert "from app.stock_analysis_batch_service import BatchAnalysisViewService" in source
    assert "support_services = build_stock_analysis_support_services(" in source
    assert "research_services = build_stock_analysis_research_services(" in source
    assert "self.batch_analysis_service = support_services.batch_analysis_service" in source
    assert "self.research_resolution_service = (" in source


def test_mainline_stock_analysis_services_module_reexports_extracted_owners():
    assert CompatBatchAnalysisViewService is BatchAnalysisViewService
    assert CompatResearchResolutionService is ResearchResolutionService


def test_mainline_module_surface_imports_cleanly():
    assert hasattr(stock_analysis_module, "StockAnalysisService")
