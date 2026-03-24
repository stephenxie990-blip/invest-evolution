from __future__ import annotations

from pathlib import Path

import pytest

from invest_evolution.application.commander.workflow import load_training_plan_artifact


def test_load_training_plan_artifact_rejects_invalid_utf8(tmp_path: Path):
    plan_path = tmp_path / "broken.json"
    plan_path.write_bytes(b"\xff")

    with pytest.raises(ValueError, match="invalid training plan json: broken"):
        load_training_plan_artifact(plan_path, plan_id="broken")


def test_load_training_plan_artifact_rejects_non_object_payload(tmp_path: Path):
    plan_path = tmp_path / "list.json"
    plan_path.write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="training plan must decode to an object: list"):
        load_training_plan_artifact(plan_path, plan_id="list")
