from invest.models import MomentumModel
from invest.models.defaults import COMMON_EXECUTION_DEFAULTS, COMMON_PARAM_DEFAULTS, COMMON_RISK_DEFAULTS


def test_model_default_resolution_prefers_runtime_then_config_then_common_defaults():
    model = MomentumModel(runtime_overrides={"stop_loss_pct": 0.07, "top_n": 7})

    assert model.param("top_n") == 7
    assert model.risk_param("stop_loss_pct") == 0.07
    assert model.risk_param("take_profit_pct") == 0.15
    assert model.param("max_hold_days") == COMMON_PARAM_DEFAULTS["max_hold_days"]
    assert model.execution_param("commission_rate") == COMMON_EXECUTION_DEFAULTS["commission_rate"]
    assert model.risk_param("trailing_pct") == 0.10
    assert COMMON_RISK_DEFAULTS["stop_loss_pct"] == 0.05
