from pathlib import Path

from invest.evolution import YamlConfigMutator


def test_yaml_config_mutator_writes_generation_snapshot(tmp_path: Path):
    config_path = tmp_path / "momentum_v1.yaml"
    config_path.write_text(
        """
name: momentum_v1
kind: momentum
params:
  top_n: 5
  max_positions: 4
  cash_reserve: 0.2
  stop_loss_pct: 0.05
  take_profit_pct: 0.15
risk:
  stop_loss_pct: 0.05
  take_profit_pct: 0.15
execution:
  initial_capital: 100000
benchmark:
  risk_free_rate: 0.03
""".strip(),
        encoding="utf-8",
    )
    mutator = YamlConfigMutator(generations_dir=tmp_path / "generations")

    result = mutator.mutate(
        config_path,
        param_adjustments={"stop_loss_pct": 0.04, "position_size": 0.18},
        narrative_adjustments={"style": "concise"},
        generation_label="g001",
        parent_meta={"cycle_id": 1},
    )

    out_path = Path(result["config_path"])
    meta_path = Path(result["meta_path"])
    assert out_path.exists()
    assert meta_path.exists()
    assert result["config"]["params"]["stop_loss_pct"] == 0.04
    assert result["config"]["params"]["position_size"] == 0.18
    assert result["config"]["context"]["style"] == "concise"
