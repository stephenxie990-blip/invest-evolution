from pathlib import Path
from types import SimpleNamespace

import app.stock_analysis as stock_analysis_module
from app.stock_analysis import StockAnalysisService
from invest.models import resolve_model_config_path
from market_data.repository import MarketDataRepository



def _seed_market_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "market.db"
    repo = MarketDataRepository(db_path)
    repo.initialize_schema()
    repo.upsert_security_master(
        [
            {"code": "sh.600001", "name": "Alpha", "list_date": "20200101", "source": "test"},
            {"code": "sh.600002", "name": "Beta", "list_date": "20200101", "source": "test"},
            {"code": "sh.600003", "name": "Gamma", "list_date": "20200101", "source": "test"},
        ]
    )
    daily_rows = []
    flow_rows = []
    for day in range(1, 91):
        daily_rows.extend(
            [
                {
                    "code": "sh.600001",
                    "trade_date": f"202401{day:02d}",
                    "open": 10 + day * 0.08,
                    "high": 10.5 + day * 0.08,
                    "low": 9.6 + day * 0.08,
                    "close": 10 + day * 0.10,
                    "volume": 1000 + day * 12,
                    "amount": 5000 + day * 120,
                    "pct_chg": 0.8,
                    "turnover": 1.1,
                    "source": "test",
                },
                {
                    "code": "sh.600002",
                    "trade_date": f"202401{day:02d}",
                    "open": 15 + day * 0.02,
                    "high": 15.3 + day * 0.02,
                    "low": 14.8 + day * 0.02,
                    "close": 15 + day * 0.015,
                    "volume": 900 + day * 8,
                    "amount": 4300 + day * 80,
                    "pct_chg": 0.2,
                    "turnover": 0.9,
                    "source": "test",
                },
                {
                    "code": "sh.600003",
                    "trade_date": f"202401{day:02d}",
                    "open": 20 - day * 0.05,
                    "high": 20.2 - day * 0.04,
                    "low": 19.4 - day * 0.05,
                    "close": 20 - day * 0.06,
                    "volume": 1100 + day * 7,
                    "amount": 5200 + day * 70,
                    "pct_chg": -0.4,
                    "turnover": 1.4,
                    "source": "test",
                },
            ]
        )
        flow_rows.append(
            {
                "code": "sh.600001",
                "trade_date": f"202401{day:02d}",
                "close": 10 + day * 0.10,
                "pct_chg": 0.8,
                "main_net_inflow": 1000 + day * 10,
                "main_net_inflow_ratio": 0.02 + day * 0.0001,
                "super_large_net_inflow": 500 + day,
                "super_large_net_inflow_ratio": 0.01,
                "large_net_inflow": 300 + day,
                "large_net_inflow_ratio": 0.008,
                "medium_net_inflow": -100,
                "medium_net_inflow_ratio": -0.002,
                "small_net_inflow": -200,
                "small_net_inflow_ratio": -0.004,
            }
        )
    repo.upsert_daily_bars(daily_rows)
    repo.upsert_capital_flow_daily(flow_rows)
    return db_path



def _build_service(tmp_path: Path, controller=None) -> StockAnalysisService:
    db_path = _seed_market_db(tmp_path)
    provider = (lambda: controller) if controller is not None else None
    return StockAnalysisService(
        db_path=db_path,
        strategy_dir=tmp_path / "stock_strategies",
        project_root=tmp_path,
        enable_llm_react=False,
        controller_provider=provider,
    )



def test_ask_stock_returns_unified_research_payload_with_live_controller(tmp_path: Path):
    controller = SimpleNamespace(
        model_name="momentum",
        model_config_path=str(resolve_model_config_path("momentum")),
        current_params={},
        model_routing_enabled=False,
        model_routing_mode="off",
        model_routing_allowed_models=["momentum"],
        experiment_allowed_models=[],
        allocator_top_n=3,
        output_dir=tmp_path / "runtime" / "outputs" / "training",
    )
    service = _build_service(tmp_path, controller=controller)

    payload = service.ask_stock(question="请分析 Alpha", query="Alpha")

    assert payload["research"]["status"] == "ok"
    assert payload["analysis"]["model_bridge"]["parameter_source"] == "live_controller"
    assert payload["research"]["policy"]["model_name"] == "momentum"
    assert payload["policy_id"] == payload["research"]["policy"]["policy_id"]
    cross = payload["research"]["snapshot"]["cross_section_context"]
    assert cross["rank"] >= 1
    assert isinstance(cross["selected_by_policy"], bool)
    assert payload["dashboard"]["signal"] == payload["research"]["hypothesis"]["stance"]



def test_ask_stock_as_of_date_blocks_future_leak_and_saves_attribution(tmp_path: Path):
    controller = SimpleNamespace(
        model_name="momentum",
        model_config_path=str(resolve_model_config_path("momentum")),
        current_params={},
        model_routing_enabled=False,
        model_routing_mode="off",
        model_routing_allowed_models=["momentum"],
        experiment_allowed_models=[],
        allocator_top_n=3,
        output_dir=tmp_path / "runtime" / "outputs" / "training",
    )
    service = _build_service(tmp_path, controller=controller)

    payload = service.ask_stock(question="请分析 Alpha", query="Alpha", as_of_date="20240130")

    history_dates = [item["trade_date"] for item in payload["analysis"]["tool_results"]["get_daily_history"]["items"]]
    quote_date = payload["analysis"]["tool_results"]["get_realtime_quote"]["quote"]["trade_date"]

    assert payload["as_of_date"] == "20240130"
    assert max(history_dates) <= "20240130"
    assert quote_date == "20240130"
    assert payload["research"]["snapshot"]["as_of_date"] == "20240130"
    assert payload["analysis"]["model_bridge"]["parameter_source"] == "config_default_replay_safe"
    assert payload["research"]["attribution"]["saved"] is True



def test_ask_stock_scenario_engine_switches_to_empirical_after_prior_case(tmp_path: Path):
    controller = SimpleNamespace(
        model_name="momentum",
        model_config_path=str(resolve_model_config_path("momentum")),
        current_params={},
        model_routing_enabled=False,
        model_routing_mode="off",
        model_routing_allowed_models=["momentum"],
        experiment_allowed_models=[],
        allocator_top_n=3,
        output_dir=tmp_path / "runtime" / "outputs" / "training",
    )
    service = _build_service(tmp_path, controller=controller)

    first = service.ask_stock(question="请分析 Alpha", query="Alpha", as_of_date="20240130")
    second = service.ask_stock(question="请分析 Alpha", query="Alpha", as_of_date="20240130")

    assert first["research"]["status"] == "ok"
    assert second["research"]["status"] == "ok"
    assert second["research"]["scenario"]["engine"] == "case_similarity_v1"
    assert second["research"]["scenario"]["sample_count"] >= 1


def test_ask_stock_canonical_dashboard_path_does_not_require_legacy_dashboard_builder(tmp_path: Path):
    controller = SimpleNamespace(
        model_name="momentum",
        model_config_path=str(resolve_model_config_path("momentum")),
        current_params={},
        model_routing_enabled=False,
        model_routing_mode="off",
        model_routing_allowed_models=["momentum"],
        experiment_allowed_models=[],
        allocator_top_n=3,
        output_dir=tmp_path / "runtime" / "outputs" / "training",
    )
    service = _build_service(tmp_path, controller=controller)
    service._build_dashboard = lambda **kwargs: (_ for _ in ()).throw(AssertionError("legacy dashboard should not be used when research bridge is available"))

    payload = service.ask_stock(question="请分析 Alpha", query="Alpha")

    assert payload["research"]["status"] == "ok"
    assert payload["dashboard"]["signal"] == payload["research"]["hypothesis"]["stance"]


def test_ask_stock_fallback_path_keeps_legacy_dashboard_contract(tmp_path: Path):
    service = _build_service(tmp_path)
    service._build_research_bridge = lambda **kwargs: {
        "status": "unavailable",
        "error": "bridge offline",
        "details": {"stage": "test"},
    }
    service._build_dashboard = lambda **kwargs: {
        "signal": "legacy-fallback",
        "score": 42.0,
        "entry_price": None,
        "stop_loss": None,
        "reason": "fallback",
        "matched_signals": [],
        "core_rules": [],
        "entry_conditions": [],
    }

    payload = service.ask_stock(question="请分析 Alpha", query="Alpha")

    assert payload["research"]["status"] == "unavailable"
    assert payload["research"]["fallback"] == "legacy_yaml_dashboard"
    assert payload["analysis"]["model_bridge"]["fallback"] == "legacy_yaml_dashboard"
    assert payload["dashboard"]["signal"] == "legacy-fallback"


def test_ask_stock_fallback_path_uses_canonical_dashboard_renderer(tmp_path: Path, monkeypatch):
    service = _build_service(tmp_path)
    service._build_research_bridge = lambda **kwargs: {
        "status": "unavailable",
        "error": "bridge offline",
        "details": {"stage": "test"},
    }
    service._build_dashboard = lambda **kwargs: {
        "signal": "legacy-fallback",
        "score": 42.0,
        "entry_price": 9.9,
        "stop_loss": 9.3,
        "reason": "legacy fallback reason",
        "matched_signals": ["B", "A"],
        "core_rules": ["rule-1"],
        "entry_conditions": ["cond-1"],
    }
    captured = {}

    def _fake_projection(**kwargs):
        captured.update(kwargs)
        return {
            "signal": "canonical-fallback",
            "score": kwargs["hypothesis"].score,
            "entry_price": kwargs["hypothesis"].entry_rule.get("price"),
            "stop_loss": kwargs["hypothesis"].invalidation_rule.get("price"),
            "reason": kwargs.get("legacy_reason", ""),
            "matched_signals": list(kwargs["matched_signals"]),
            "core_rules": list(kwargs["core_rules"]),
            "entry_conditions": list(kwargs["entry_conditions"]),
        }

    monkeypatch.setattr(stock_analysis_module, "build_dashboard_projection", _fake_projection)

    payload = service.ask_stock(question="请分析 Alpha", query="Alpha")

    assert payload["dashboard"]["signal"] == "canonical-fallback"
    assert payload["dashboard"]["reason"] == "legacy fallback reason"
    assert captured["hypothesis"].stance == "legacy-fallback"
    assert captured["legacy_reason"] == "legacy fallback reason"
    assert captured["matched_signals"] == ["B", "A"]
