from pathlib import Path
from types import SimpleNamespace

import invest_evolution.application.stock_analysis as stock_analysis_module
from invest_evolution.application.stock_analysis import StockAnalysisService
from invest_evolution.application.training.execution import resolve_manager_config_ref
from invest_evolution.market_data.repository import MarketDataRepository



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
        default_manager_id="momentum",
        default_manager_config_ref=str(resolve_manager_config_ref("momentum")),
        current_params={},
        governance_enabled=False,
        governance_mode="off",
        governance_allowed_manager_ids=["momentum"],
        experiment_allowed_manager_ids=[],
        allocator_top_n=3,
        output_dir=tmp_path / "runtime" / "outputs" / "training",
    )
    service = _build_service(tmp_path, controller=controller)

    payload = service.ask_stock(question="请分析 Alpha", query="Alpha")

    assert payload["research"]["status"] == "ok"
    assert payload["analysis"]["research_bridge"]["parameter_source"] == "live_controller"
    assert "governance_decision" in payload["analysis"]["research_bridge"]
    assert "routing_decision" not in payload["analysis"]["research_bridge"]
    assert payload["research"]["policy"]["manager_id"] == "momentum"
    assert "governance_context" in payload["research"]["policy"]
    assert "governance_context" in payload["research"]["snapshot"]["market_context"]
    assert payload["policy_id"] == payload["research"]["policy"]["policy_id"]
    cross = payload["research"]["snapshot"]["cross_section_context"]
    assert cross["rank"] >= 1
    assert isinstance(cross["selected_by_policy"], bool)
    assert payload["dashboard"]["signal"] == payload["research"]["hypothesis"]["stance"]



def test_ask_stock_as_of_date_blocks_future_leak_and_saves_attribution(tmp_path: Path):
    controller = SimpleNamespace(
        default_manager_id="momentum",
        default_manager_config_ref=str(resolve_manager_config_ref("momentum")),
        current_params={},
        governance_enabled=False,
        governance_mode="off",
        governance_allowed_manager_ids=["momentum"],
        experiment_allowed_manager_ids=[],
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
    assert payload["analysis"]["research_bridge"]["parameter_source"] == "config_default_replay_safe"
    assert payload["research"]["attribution"]["saved"] is True



def test_ask_stock_scenario_engine_switches_to_empirical_after_prior_case(tmp_path: Path):
    controller = SimpleNamespace(
        default_manager_id="momentum",
        default_manager_config_ref=str(resolve_manager_config_ref("momentum")),
        current_params={},
        governance_enabled=False,
        governance_mode="off",
        governance_allowed_manager_ids=["momentum"],
        experiment_allowed_manager_ids=[],
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
        default_manager_id="momentum",
        default_manager_config_ref=str(resolve_manager_config_ref("momentum")),
        current_params={},
        governance_enabled=False,
        governance_mode="off",
        governance_allowed_manager_ids=["momentum"],
        experiment_allowed_manager_ids=[],
        allocator_top_n=3,
        output_dir=tmp_path / "runtime" / "outputs" / "training",
    )
    service = _build_service(tmp_path, controller=controller)

    payload = service.ask_stock(question="请分析 Alpha", query="Alpha")

    assert payload["research"]["status"] == "ok"
    assert payload["dashboard"]["signal"] == payload["research"]["hypothesis"]["stance"]


def test_ask_stock_payload_keeps_research_and_research_bridge_boundaries_stable(tmp_path: Path):
    controller = SimpleNamespace(
        default_manager_id="momentum",
        default_manager_config_ref=str(resolve_manager_config_ref("momentum")),
        current_params={},
        governance_enabled=False,
        governance_mode="off",
        governance_allowed_manager_ids=["momentum"],
        experiment_allowed_manager_ids=[],
        allocator_top_n=3,
        output_dir=tmp_path / "runtime" / "outputs" / "training",
    )
    service = _build_service(tmp_path, controller=controller)

    payload = service.ask_stock(question="请分析 Alpha", query="Alpha")

    assert payload["analysis"]["research_bridge"]["status"] == "ok"
    assert "snapshot" not in payload["analysis"]["research_bridge"]
    assert "policy" not in payload["analysis"]["research_bridge"]
    assert payload["research"]["status"] == "ok"
    assert payload["research"]["snapshot"]["security"]["code"] == "sh.600001"
    assert payload["research"]["policy"]["policy_id"] == payload["policy_id"]
    assert payload["analysis"]["research_bridge"]["policy_id"] == payload["policy_id"]


def test_ask_stock_payload_exposes_canonical_request_and_identifier_sections(tmp_path: Path):
    controller = SimpleNamespace(
        default_manager_id="momentum",
        default_manager_config_ref=str(resolve_manager_config_ref("momentum")),
        current_params={},
        governance_enabled=False,
        governance_mode="off",
        governance_allowed_manager_ids=["momentum"],
        experiment_allowed_manager_ids=[],
        allocator_top_n=3,
        output_dir=tmp_path / "runtime" / "outputs" / "training",
    )
    service = _build_service(tmp_path, controller=controller)

    payload = service.ask_stock(question="请分析 Alpha", query="Alpha", as_of_date="20240130")

    assert payload["request"]["question"] == "请分析 Alpha"
    assert payload["request"]["query"] == "Alpha"
    assert payload["request"]["normalized_query"] == payload["normalized_query"]
    assert payload["request"]["requested_as_of_date"] == payload["requested_as_of_date"]
    assert payload["request"]["as_of_date"] == payload["as_of_date"]
    assert payload["identifiers"]["policy_id"] == payload["policy_id"]
    assert payload["identifiers"]["research_case_id"] == payload["research_case_id"]
    assert payload["identifiers"]["attribution_id"] == payload["attribution_id"]
    assert payload["research"]["identifiers"] == payload["identifiers"]
    assert payload["analysis"]["research_bridge"]["identifiers"] == payload["identifiers"]
    assert payload["resolved_entities"]["security"] == payload["resolved_security"]


def test_ask_stock_payload_canonical_sections_keep_stable_shape(tmp_path: Path):
    controller = SimpleNamespace(
        default_manager_id="momentum",
        default_manager_config_ref=str(resolve_manager_config_ref("momentum")),
        current_params={},
        governance_enabled=False,
        governance_mode="off",
        governance_allowed_manager_ids=["momentum"],
        experiment_allowed_manager_ids=[],
        allocator_top_n=3,
        output_dir=tmp_path / "runtime" / "outputs" / "training",
    )
    service = _build_service(tmp_path, controller=controller)

    payload = service.ask_stock(question="请分析 Alpha", query="Alpha")

    assert sorted(payload["request"].keys()) == [
        "as_of_date",
        "normalized_query",
        "query",
        "question",
        "requested_as_of_date",
    ]
    assert sorted(payload["identifiers"].keys()) == [
        "attribution_id",
        "policy_id",
        "research_case_id",
    ]
    assert sorted(payload["resolved_entities"].keys()) == ["security"]
    assert "identifiers" in payload["research"]
    assert "identifiers" in payload["analysis"]["research_bridge"]


def test_ask_stock_fallback_path_uses_canonical_fallback_contract(tmp_path: Path):
    service = _build_service(tmp_path)
    service._build_research_bridge = lambda **kwargs: {
        "status": "unavailable",
        "error": "bridge offline",
        "details": {"stage": "test"},
    }

    payload = service.ask_stock(question="请分析 Alpha", query="Alpha")

    assert payload["research"]["status"] == "unavailable"
    assert payload["research"]["fallback"] == "canonical_dashboard_fallback"
    assert payload["analysis"]["research_bridge"]["fallback"] == "canonical_dashboard_fallback"
    assert payload["dashboard"]["signal"]


def test_ask_stock_fallback_path_uses_canonical_dashboard_renderer(tmp_path: Path, monkeypatch):
    service = _build_service(tmp_path)
    service._build_research_bridge = lambda **kwargs: {
        "status": "unavailable",
        "error": "bridge offline",
        "details": {"stage": "test"},
    }
    captured = {}

    def _fake_projection(**kwargs):
        captured.update(kwargs)
        return {
            "signal": "canonical-fallback",
            "score": kwargs["hypothesis"].score,
            "entry_price": kwargs["hypothesis"].entry_rule.get("price"),
            "stop_loss": kwargs["hypothesis"].invalidation_rule.get("price"),
            "reason": kwargs.get("supplemental_reason", ""),
            "matched_signals": list(kwargs["matched_signals"]),
            "core_rules": list(kwargs["core_rules"]),
            "entry_conditions": list(kwargs["entry_conditions"]),
        }

    monkeypatch.setattr(stock_analysis_module, "build_dashboard_projection", _fake_projection)

    payload = service.ask_stock(question="请分析 Alpha", query="Alpha")

    assert payload["dashboard"]["signal"] == "canonical-fallback"
    assert "分析摘要" in payload["dashboard"]["reason"] or payload["dashboard"]["reason"]
    assert captured["hypothesis"].stance in {"候选买入", "偏强关注", "持有观察", "偏弱回避", "减仓/回避"}
    assert captured["supplemental_reason"]
    assert isinstance(captured["matched_signals"], list)


def test_stock_analysis_indicator_entrypoints_share_batch_analysis_context(tmp_path: Path, monkeypatch):
    service = _build_service(tmp_path)
    calls = {"count": 0}
    original = StockAnalysisService._build_batch_analysis_context

    def _wrapped(self, frame, code):
        calls["count"] += 1
        return original(self, frame, code)

    monkeypatch.setattr(StockAnalysisService, "_build_batch_analysis_context", _wrapped)

    trend = service.analyze_trend("Alpha")
    snapshot = service.get_indicator_snapshot("Alpha")
    levels = service.analyze_support_resistance("Alpha")

    assert calls["count"] == 3
    assert trend["status"] == "ok"
    assert snapshot["status"] == "ok"
    assert levels["status"] == "ok"
