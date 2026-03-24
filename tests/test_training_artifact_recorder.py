from __future__ import annotations

import json

from invest_evolution.investment.research import TrainingArtifactRecorder


def test_selection_artifact_persists_observability_payload(tmp_path):
    recorder = TrainingArtifactRecorder(base_dir=str(tmp_path))

    recorder.save_selection_artifact(
        {
            "artifact_id": 7,
            "cutoff_date": "20240101",
            "regime": "oscillation",
            "confidence": 0.74,
            "selected": ["AAA", "BBB"],
            "source": "manager_runtime",
            "selected_roster": [
                {"name": "momentum", "cost": 1.0},
                {"name": "value_quality", "cost": 0.8},
            ],
            "observability": {
                "budget": {
                    "selected_hunters": 2,
                    "budget_limit": 2.2,
                    "budget_used": 1.8,
                },
                "timings_ms": {"total": 184.0, "agents": 121.0},
                "llm": {"used": True, "mode": "live", "call_count": 2},
            },
        },
        cycle=7,
    )

    payload = json.loads(
        (tmp_path / "selection" / "selection_0007.json").read_text(encoding="utf-8")
    )

    assert payload["observability"]["budget"]["selected_hunters"] == 2
    assert payload["observability"]["llm"]["mode"] == "live"


def test_review_artifacts_write_manager_and_allocation_outputs(tmp_path):
    recorder = TrainingArtifactRecorder(base_dir=str(tmp_path))

    recorder.save_manager_review_artifact(
        {
            "verdict": "continue",
            "dominant_manager_id": "momentum",
            "summary": {"manager_count": 2},
            "reports": [{"manager_id": "momentum", "verdict": "continue"}],
        },
        cycle=3,
    )
    recorder.save_allocation_review_artifact(
        {
            "verdict": "continue",
            "regime": "bull",
            "active_manager_ids": ["momentum", "value_quality"],
            "manager_budget_weights": {"momentum": 0.6, "value_quality": 0.4},
            "findings": ["weights balanced"],
        },
        cycle=3,
    )

    manager_payload = json.loads(
        (tmp_path / "manager_review" / "manager_review_0003.json").read_text(encoding="utf-8")
    )
    allocation_payload = json.loads(
        (tmp_path / "allocation_review" / "allocation_review_0003.json").read_text(encoding="utf-8")
    )

    assert manager_payload["report"]["summary"]["manager_count"] == 2
    assert allocation_payload["report"]["manager_budget_weights"]["momentum"] == 0.6
    assert "weights balanced" in (
        tmp_path / "allocation_review" / "allocation_review_0003.md"
    ).read_text(encoding="utf-8")
