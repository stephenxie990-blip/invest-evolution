from pathlib import Path

from invest.evolution import YamlConfigMutator


def test_yaml_mutation_space_clamps_scoring_values(tmp_path: Path):
    config_path = tmp_path / "value_quality_v1.yaml"
    config_path.write_text(
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
    mutator = YamlConfigMutator(generations_dir=tmp_path / "generations")
    result = mutator.mutate(
        config_path,
        scoring_adjustments={"weights": {"roe": 0.9}, "bands": {"rsi_low": 10.0}},
        generation_label="g003",
    )
    assert result["config"]["scoring"]["weights"]["roe"] == 0.4
    assert result["config"]["scoring"]["bands"]["rsi_low"] == 30.0
    assert result["applied_adjustments"]["scoring"]["weights"]["roe"] == 0.4
