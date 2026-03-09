from pathlib import Path

from invest.evolution import YamlConfigMutator


def test_yaml_config_mutator_can_mutate_scoring(tmp_path: Path):
    config_path = tmp_path / "mean_reversion_v1.yaml"
    config_path.write_text(
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
    mutator = YamlConfigMutator(generations_dir=tmp_path / "generations")
    result = mutator.mutate(
        config_path,
        scoring_adjustments={"weights": {"oversold_rsi": 0.42}},
        generation_label="g002",
    )
    assert result["config"]["scoring"]["weights"]["oversold_rsi"] == 0.42
    assert result["meta"]["scoring_adjustments"]["weights"]["oversold_rsi"] == 0.42
