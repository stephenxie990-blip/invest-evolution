import pytest

from invest.models.validation import ModelConfigValidationError, validate_model_config


def test_validate_model_config_rejects_bad_mutation_space_range():
    cfg = {
        "name": "defensive_low_vol_v1",
        "kind": "defensive_low_vol",
        "params": {"top_n": 5, "max_positions": 4, "cash_reserve": 0.3},
        "risk": {},
        "execution": {},
        "benchmark": {},
        "scoring": {"weights": {"low_volatility": 0.3}, "bands": {"bb_pos_low": 0.3}, "penalties": {"bad_rsi": 0.1}},
        "mutation_space": {"scoring": {"weights": {"low_volatility": {"min": 0.6, "max": 0.2}}}},
    }
    with pytest.raises(ModelConfigValidationError):
        validate_model_config(cfg)
