import pytest

from invest.models.validation import ModelConfigValidationError, validate_model_config


def test_validate_model_config_rejects_missing_scoring_for_required_model():
    cfg = {
        "name": "mean_reversion_v1",
        "kind": "mean_reversion",
        "params": {"top_n": 5, "max_positions": 4, "cash_reserve": 0.3},
        "risk": {},
        "execution": {},
        "benchmark": {},
    }
    with pytest.raises(ModelConfigValidationError):
        validate_model_config(cfg)


def test_validate_model_config_accepts_complete_scoring():
    cfg = {
        "name": "value_quality_v1",
        "kind": "value_quality",
        "params": {"top_n": 5, "max_positions": 4, "cash_reserve": 0.3},
        "risk": {},
        "execution": {},
        "benchmark": {},
        "scoring": {
            "weights": {"pe": 0.2},
            "bands": {"rsi_low": 40.0},
        },
    }
    assert validate_model_config(cfg)["kind"] == "value_quality"
