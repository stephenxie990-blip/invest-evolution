import json
from pathlib import Path
from types import SimpleNamespace

from invest_evolution.application.commander import ops as ops_module


def _runtime_with_training_output(root: Path) -> SimpleNamespace:
    return SimpleNamespace(cfg=SimpleNamespace(training_output_dir=str(root / "training")))


def test_get_leaderboard_payload_reads_existing_snapshot_without_write(monkeypatch, tmp_path):
    root_dir = tmp_path / "runtime"
    root_dir.mkdir(parents=True, exist_ok=True)
    leaderboard_path = root_dir / "leaderboard.json"
    leaderboard_path.write_text(
        json.dumps({"generated_at": "2026-03-20T12:30:00", "entries": [{"manager_id": "momentum"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        ops_module,
        "write_leaderboard",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not write leaderboard")),
        raising=False,
    )

    payload = ops_module.get_leaderboard_payload(_runtime_with_training_output(root_dir))

    assert payload["entries"][0]["manager_id"] == "momentum"


def test_get_allocator_payload_reads_existing_snapshot_without_write(monkeypatch, tmp_path):
    root_dir = tmp_path / "runtime"
    root_dir.mkdir(parents=True, exist_ok=True)
    leaderboard_path = root_dir / "leaderboard.json"
    leaderboard_path.write_text(
        json.dumps(
            {
                "generated_at": "2026-03-20T12:30:00",
                "entries": [
                    {
                        "manager_id": "momentum",
                        "manager_config_ref": "momentum_v1",
                        "eligible_for_governance": True,
                        "score": 0.91,
                        "avg_return_pct": 0.5,
                        "avg_sharpe_ratio": 1.2,
                        "avg_max_drawdown": 3.0,
                        "benchmark_pass_rate": 1.0,
                        "avg_strategy_score": 0.7,
                        "rank": 1,
                    }
                ],
                "regime_leaderboards": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        ops_module,
        "write_leaderboard",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not write leaderboard")),
        raising=False,
    )

    payload = ops_module.get_allocator_payload(
        _runtime_with_training_output(root_dir),
        regime="bull",
        top_n=1,
        as_of_date="20260321",
    )

    assert payload["leaderboard_generated_at"] == "2026-03-20T12:30:00"
    assert "momentum" in payload["allocation"]["active_manager_ids"]


def test_get_leaderboard_payload_does_not_create_missing_snapshot(tmp_path):
    root_dir = tmp_path / "runtime"
    root_dir.mkdir(parents=True, exist_ok=True)
    payload = ops_module.get_leaderboard_payload(_runtime_with_training_output(root_dir))

    assert payload["entries"] == []
    assert not (root_dir / "leaderboard.json").exists()
