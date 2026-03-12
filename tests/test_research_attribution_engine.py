from pathlib import Path

from invest.research import ResearchAttributionEngine
from market_data.repository import MarketDataRepository



def test_research_attribution_engine_scores_multi_horizon_case(tmp_path: Path):
    db_path = tmp_path / "market.db"
    repo = MarketDataRepository(db_path)
    repo.initialize_schema()
    repo.upsert_security_master([
        {"code": "sh.600001", "name": "FooBank", "list_date": "20200101", "source": "test"}
    ])
    repo.upsert_daily_bars(
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
    case_record = {
        "snapshot": {
            "as_of_date": "20240120",
            "security": {"code": "sh.600001", "name": "FooBank"},
        },
        "hypothesis": {
            "hypothesis_id": "hypothesis_1",
            "entry_rule": {"kind": "observe_only", "price": None},
            "invalidation_rule": {"kind": "stop_loss", "price": 1.0},
            "de_risk_rule": {"kind": "take_profit", "price": 999.0},
            "supporting_factors": ["趋势向上"],
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

    attribution = engine.evaluate_case(case_record)

    assert attribution.thesis_result == "hit"
    assert attribution.horizon_results["T+5"]["label"] == "hit"
    assert attribution.horizon_results["T+20"]["return_pct"] > 0
    assert attribution.horizon_results["T+60"]["label"] == "hit"
    assert attribution.calibration_metrics["positive_return_brier"] >= 0
