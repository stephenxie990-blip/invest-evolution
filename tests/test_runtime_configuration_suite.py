from pathlib import Path
import yaml
import pytest

import invest_evolution.investment.evolution.mutation as mutators_module
import invest_evolution.investment.runtimes.base as runtimes_base_module
from invest_evolution.config import PROJECT_ROOT as REPO_PROJECT_ROOT
from invest_evolution.investment.evolution import RuntimeConfigMutator
from invest_evolution.investment.runtimes import (
    MomentumRuntime,
    DefensiveLowVolRuntime,
    MeanReversionRuntime,
    ValueQualityRuntime,
)
from invest_evolution.investment.runtimes.catalog import COMMON_PARAM_DEFAULTS
from invest_evolution.investment.runtimes.ops import RuntimeConfigValidationError, validate_runtime_config


def _seed_runtime_config(tmp_path: Path, relpath: str) -> Path:
    source_path = REPO_PROJECT_ROOT / relpath
    target_path = tmp_path / relpath
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
    return target_path


def _deep_merge_dict(base: dict, patch: dict) -> dict:
    merged = dict(base)
    for key, value in patch.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _deep_merge_dict(current, value)
            continue
        merged[key] = value
    return merged


def _write_temp_config(tmp_path: Path, relpath: str, patch: dict) -> str:
    source_path = REPO_PROJECT_ROOT / relpath
    target_path = tmp_path / relpath
    target_path.parent.mkdir(parents=True, exist_ok=True)
    data = yaml.safe_load(source_path.read_text(encoding='utf-8'))
    data = _deep_merge_dict(data, patch)
    target_path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding='utf-8')
    return str(target_path)


# --- Mutation Tests ---

def test_runtime_config_mutator_can_mutate_scoring(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(mutators_module, "PROJECT_ROOT", tmp_path)
    runtime_config_ref = tmp_path / "mean_reversion_v1.yaml"
    runtime_config_ref.write_text(
        """
name: mean_reversion_v1
kind: mean_reversion
params:
  top_n: 5
  max_positions: 4
  cash_reserve: 0.3
risk:
  stop_loss_pct: 0.04
execution:
  initial_capital: 100000
benchmark:
  risk_free_rate: 0.03
scoring:
  weights:
    oversold_rsi: 0.35
  bands:
    lower_bb_threshold: 0.35
  penalties:
    overheat_rsi: 0.15
""".strip(),
        encoding="utf-8",
    )
    mutator = RuntimeConfigMutator(generations_dir=tmp_path / "generations")
    result = mutator.mutate(
        runtime_config_ref,
        scoring_adjustments={"weights": {"oversold_rsi": 0.42}},
        generation_label="g002",
    )
    assert result["config"]["scoring"]["weights"]["oversold_rsi"] == 0.42
    assert result["meta"]["scoring_adjustments"]["weights"]["oversold_rsi"] == 0.42


def test_runtime_config_mutation_space_clamps_scoring_values(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(mutators_module, "PROJECT_ROOT", tmp_path)
    runtime_config_ref = tmp_path / "value_quality_v1.yaml"
    runtime_config_ref.write_text(
        """
name: value_quality_v1
kind: value_quality
params:
  top_n: 5
  max_positions: 4
  cash_reserve: 0.3
risk: {}
execution: {}
benchmark: {}
scoring:
  weights:
    roe: 0.3
  bands:
    rsi_low: 40.0
mutation_space:
  scoring:
    weights:
      roe:
        min: 0.1
        max: 0.4
    bands:
      rsi_low:
        min: 30.0
        max: 50.0
""".strip(),
        encoding="utf-8",
    )
    mutator = RuntimeConfigMutator(generations_dir=tmp_path / "generations")
    result = mutator.mutate(
        runtime_config_ref,
        scoring_adjustments={"weights": {"roe": 0.9}, "bands": {"rsi_low": 10.0}},
        generation_label="g003",
    )
    assert result["config"]["scoring"]["weights"]["roe"] == 0.4
    assert result["config"]["scoring"]["bands"]["rsi_low"] == 30.0


def test_runtime_config_mutator_writes_generation_snapshot(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(mutators_module, "PROJECT_ROOT", tmp_path)
    runtime_config_ref = tmp_path / "momentum_v1.yaml"
    runtime_config_ref.write_text(
        """
name: momentum_v1
kind: momentum
params:
  top_n: 5
  max_positions: 4
  cash_reserve: 0.2
risk:
  stop_loss_pct: 0.05
execution:
  initial_capital: 100000
benchmark:
  risk_free_rate: 0.03
""".strip(),
        encoding="utf-8",
    )
    mutator = RuntimeConfigMutator(generations_dir=tmp_path / "generations")
    result = mutator.mutate(
        runtime_config_ref,
        param_adjustments={"stop_loss_pct": 0.04},
        generation_label="g001",
    )
    assert Path(result["runtime_config_ref"]).exists()
    assert Path(result["meta_path"]).exists()
    assert result["meta"]["output_runtime_config_ref"] == result["runtime_config_ref"]
    assert result["config"]["params"]["stop_loss_pct"] == 0.04


def test_runtime_config_mutator_defaults_to_runtime_outputs(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(mutators_module, "OUTPUT_DIR", tmp_path / "runtime" / "outputs")

    mutator = RuntimeConfigMutator()

    assert mutator.generations_dir == tmp_path / "runtime" / "outputs" / "runtime_generations"
    assert mutator.generations_dir != REPO_PROJECT_ROOT / "data" / "evolution" / "generations"


# --- Validation & Defaults Tests ---

def test_validate_runtime_config_rejects_missing_scoring_for_required_runtime():
    cfg = {
        "name": "mean_reversion_v1",
        "kind": "mean_reversion",
        "params": {"top_n": 5, "max_positions": 4, "cash_reserve": 0.3},
        "risk": {},
        "execution": {},
        "benchmark": {},
    }
    with pytest.raises(RuntimeConfigValidationError):
        validate_runtime_config(cfg)


def test_validate_runtime_config_accepts_complete_scoring():
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
    assert validate_runtime_config(cfg)["kind"] == "value_quality"


def test_runtime_default_resolution_prefers_runtime_then_config_then_common_defaults():
    runtime = MomentumRuntime(runtime_overrides={"stop_loss_pct": 0.07, "top_n": 7})
    assert runtime.param("top_n") == 7
    assert runtime.risk_param("stop_loss_pct") == 0.07
    assert runtime.param("max_hold_days") == COMMON_PARAM_DEFAULTS["max_hold_days"]


# --- Runtime Scoring Response Tests ---

def test_mean_reversion_score_responds_to_scoring_config(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(runtimes_base_module, "PROJECT_ROOT", tmp_path)
    _seed_runtime_config(tmp_path, "src/invest_evolution/investment/runtimes/configs/mean_reversion_v1.yaml")
    item = {'rsi': 20.0, 'bb_pos': 0.10, 'change_5d': -8.0, 'ma_trend': '空头'}
    base = MeanReversionRuntime()
    boosted_cfg = _write_temp_config(
        tmp_path,
        'src/invest_evolution/investment/runtimes/configs/mean_reversion_v1.yaml',
        {'scoring': {'weights': {'oversold_rsi': 0.80}}}
    )
    boosted = MeanReversionRuntime(runtime_config_ref=boosted_cfg)
    assert boosted._reversion_score(item) > base._reversion_score(item)


def test_defensive_score_responds_to_penalty_config(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(runtimes_base_module, "PROJECT_ROOT", tmp_path)
    _seed_runtime_config(tmp_path, "src/invest_evolution/investment/runtimes/configs/defensive_low_vol_v1.yaml")
    item = {'volatility': 0.02, 'rsi': 80.0, 'ma_trend': '空头'}
    base = DefensiveLowVolRuntime()
    harsher_cfg = _write_temp_config(
        tmp_path,
        'src/invest_evolution/investment/runtimes/configs/defensive_low_vol_v1.yaml',
        {'scoring': {'penalties': {'bad_rsi': 0.50}}}
    )
    harsher = DefensiveLowVolRuntime(runtime_config_ref=harsher_cfg)
    assert harsher._defensive_score(item) < base._defensive_score(item)


def test_value_quality_score_requires_valuation_and_quality_fundamentals(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(runtimes_base_module, "PROJECT_ROOT", tmp_path)
    _seed_runtime_config(tmp_path, "src/invest_evolution/investment/runtimes/configs/value_quality_v1.yaml")
    runtime = ValueQualityRuntime()
    item = {'rsi': 52.0, 'change_20d': 4.0, 'volatility': 0.02}

    missing_valuation = runtime._value_score(
        item,
        {'pe_ttm': 0.0, 'pb': 0.0, 'roe': 12.0, 'market_cap': 200.0},
    )
    complete = runtime._value_score(
        item,
        {'pe_ttm': 18.0, 'pb': 1.8, 'roe': 12.0, 'market_cap': 200.0},
    )

    assert missing_valuation == 0.0
    assert complete > missing_valuation


def test_runtime_resolves_short_config_name_from_runtime_configs(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(runtimes_base_module, "PROJECT_ROOT", tmp_path)
    seeded = _seed_runtime_config(tmp_path, "src/invest_evolution/investment/runtimes/configs/defensive_low_vol_v1.yaml")

    runtime = DefensiveLowVolRuntime(runtime_config_ref="defensive_low_vol_v1")

    assert runtime.config.path == seeded
