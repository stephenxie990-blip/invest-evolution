from pathlib import Path

from invest_evolution.investment.research import ResearchAttributionEngine
from invest_evolution.market_data.repository import MarketDataRepository


def _build_repo_with_bars(tmp_path: Path, code: str, bars: list[dict[str, float | str]]) -> MarketDataRepository:
    db_path = tmp_path / "market.db"
    repo = MarketDataRepository(db_path)
    repo.initialize_schema()
    repo.upsert_security_master(
        [
            {
                "code": code,
                "name": "FooBank",
                "list_date": "20200101",
                "source": "test",
            }
        ]
    )
    repo.upsert_daily_bars(bars)
    return repo


def _case_record(
    *,
    code: str,
    as_of_date: str,
    invalidation_price: float,
    de_risk_price: float,
) -> dict:
    return {
        "snapshot": {
            "as_of_date": as_of_date,
            "security": {"code": code, "name": "FooBank"},
        },
        "hypothesis": {
            "hypothesis_id": "hypothesis_1",
            "entry_rule": {"kind": "observe_only", "price": None},
            "invalidation_rule": {"kind": "stop_loss", "price": invalidation_price},
            "de_risk_rule": {"kind": "take_profit", "price": de_risk_price},
            "supporting_factors": ["trend_up"],
            "contradicting_factors": [],
            "scenario_distribution": {
                "horizons": {
                    "T+20": {
                        "positive_return_probability": 0.8,
                        "interval": {"p25": 1.0, "p50": 3.0, "p75": 6.0},
                    }
                }
            },
            "evaluation_protocol": {"clock": ["T+5", "T+10", "T+20", "T+60"]},
        },
    }



def test_research_attribution_engine_scores_multi_horizon_case(tmp_path: Path):
    repo = _build_repo_with_bars(
        tmp_path,
        "sh.600001",
        [
            {
                "code": "sh.600001",
                "trade_date": f"202401{day:02d}",
                "open": 10 + day * 0.1,
                "high": 10.4 + day * 0.1,
                "low": 9.8 + day * 0.1,
                "close": 10 + day * 0.12,
                "volume": 1000 + day * 10,
                "amount": 5000 + day * 100,
                "pct_chg": 0.5,
                "turnover": 1.2,
                "source": "test",
            }
            for day in range(1, 81)
        ]
    )

    engine = ResearchAttributionEngine(repo)
    case_record = _case_record(
        code="sh.600001",
        as_of_date="20240120",
        invalidation_price=1.0,
        de_risk_price=999.0,
    )

    attribution = engine.evaluate_case(case_record)

    assert attribution.thesis_result == "hit"
    assert attribution.horizon_results["T+5"]["label"] == "hit"
    assert attribution.horizon_results["T+20"]["return_pct"] > 0
    assert attribution.horizon_results["T+60"]["label"] == "hit"
    assert attribution.calibration_metrics["positive_return_brier"] >= 0


def test_research_attribution_engine_treats_de_risk_before_later_invalidation_as_hit(tmp_path: Path):
    repo = _build_repo_with_bars(
        tmp_path,
        "sh.600002",
        [
            {
                "code": "sh.600002",
                "trade_date": "20240101",
                "open": 10.0,
                "high": 10.1,
                "low": 9.9,
                "close": 10.0,
                "volume": 1000,
                "amount": 10000,
                "pct_chg": 0.1,
                "turnover": 1.0,
                "source": "test",
            },
            {
                "code": "sh.600002",
                "trade_date": "20240102",
                "open": 10.1,
                "high": 11.3,
                "low": 10.0,
                "close": 11.1,
                "volume": 1100,
                "amount": 10100,
                "pct_chg": 1.0,
                "turnover": 1.0,
                "source": "test",
            },
            {
                "code": "sh.600002",
                "trade_date": "20240103",
                "open": 11.0,
                "high": 11.1,
                "low": 8.8,
                "close": 9.1,
                "volume": 1200,
                "amount": 10200,
                "pct_chg": -1.0,
                "turnover": 1.0,
                "source": "test",
            },
            {
                "code": "sh.600002",
                "trade_date": "20240104",
                "open": 9.1,
                "high": 9.4,
                "low": 8.9,
                "close": 9.0,
                "volume": 1300,
                "amount": 10300,
                "pct_chg": -0.2,
                "turnover": 1.0,
                "source": "test",
            },
            {
                "code": "sh.600002",
                "trade_date": "20240105",
                "open": 9.0,
                "high": 9.2,
                "low": 8.7,
                "close": 8.9,
                "volume": 1400,
                "amount": 10400,
                "pct_chg": -0.1,
                "turnover": 1.0,
                "source": "test",
            },
        ],
    )
    engine = ResearchAttributionEngine(repo)

    attribution = engine.evaluate_case(
        _case_record(
            code="sh.600002",
            as_of_date="20240101",
            invalidation_price=9.0,
            de_risk_price=11.0,
        )
    )

    assert attribution.thesis_result == "hit"
    assert attribution.horizon_results["T+5"]["label"] == "hit"
    assert attribution.horizon_results["T+5"]["de_risk_triggered"] is True
    assert attribution.horizon_results["T+5"]["invalidation_triggered"] is True


def test_research_attribution_engine_keeps_invalidation_before_later_de_risk_as_invalidated(tmp_path: Path):
    repo = _build_repo_with_bars(
        tmp_path,
        "sh.600003",
        [
            {
                "code": "sh.600003",
                "trade_date": "20240101",
                "open": 10.0,
                "high": 10.1,
                "low": 9.9,
                "close": 10.0,
                "volume": 1000,
                "amount": 10000,
                "pct_chg": 0.1,
                "turnover": 1.0,
                "source": "test",
            },
            {
                "code": "sh.600003",
                "trade_date": "20240102",
                "open": 9.9,
                "high": 10.2,
                "low": 8.8,
                "close": 9.0,
                "volume": 1100,
                "amount": 10100,
                "pct_chg": -1.0,
                "turnover": 1.0,
                "source": "test",
            },
            {
                "code": "sh.600003",
                "trade_date": "20240103",
                "open": 9.1,
                "high": 11.3,
                "low": 9.0,
                "close": 11.0,
                "volume": 1200,
                "amount": 10200,
                "pct_chg": 1.8,
                "turnover": 1.0,
                "source": "test",
            },
            {
                "code": "sh.600003",
                "trade_date": "20240104",
                "open": 10.9,
                "high": 11.0,
                "low": 10.7,
                "close": 10.8,
                "volume": 1300,
                "amount": 10300,
                "pct_chg": -0.1,
                "turnover": 1.0,
                "source": "test",
            },
            {
                "code": "sh.600003",
                "trade_date": "20240105",
                "open": 10.8,
                "high": 10.9,
                "low": 10.6,
                "close": 10.7,
                "volume": 1400,
                "amount": 10400,
                "pct_chg": -0.1,
                "turnover": 1.0,
                "source": "test",
            },
        ],
    )
    engine = ResearchAttributionEngine(repo)

    attribution = engine.evaluate_case(
        _case_record(
            code="sh.600003",
            as_of_date="20240101",
            invalidation_price=9.0,
            de_risk_price=11.0,
        )
    )

    assert attribution.thesis_result == "invalidated"
    assert attribution.horizon_results["T+5"]["label"] == "invalidated"
    assert attribution.horizon_results["T+5"]["de_risk_triggered"] is True
    assert attribution.horizon_results["T+5"]["invalidation_triggered"] is True
