"""
Commander playbook JSON validation tests.

Covers:
  - valid JSON playbook loading
  - missing field degradation
  - type validation warnings
  - priority range clamping
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from invest_evolution.application.commander.bootstrap import PlaybookRegistry


class TestPlaybookValidation:
    """Commander playbook JSON validation."""

    def _make_registry(self, tmpdir: str) -> PlaybookRegistry:
        return PlaybookRegistry(Path(tmpdir))

    def _write_playbook(self, tmpdir: str, filename: str, content: dict | list | str) -> Path:
        path = Path(tmpdir) / filename
        if isinstance(content, (dict, list)):
            path.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
        else:
            path.write_text(content, encoding="utf-8")
        return path

    def test_valid_json_playbook_loads(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_playbook(
                tmpdir,
                "test.json",
                {
                    "id": "test_playbook",
                    "name": "Test Playbook",
                    "enabled": True,
                    "priority": 70,
                    "description": "A test commander playbook.",
                    "rules": {
                        "entry": {"rsi_max": 30},
                        "risk": {"stop_loss_pct": 0.05},
                    },
                },
            )
            registry = self._make_registry(tmpdir)
            playbooks = registry.reload()
            assert len(playbooks) == 1
            playbook = playbooks[0]
            assert playbook.playbook_id == "test_playbook"
            assert playbook.name == "Test Playbook"
            assert playbook.enabled is True
            assert playbook.priority == 70
            assert playbook.description == "A test commander playbook."
            assert playbook.kind == "json"

    def test_missing_id_uses_filename(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_playbook(
                tmpdir,
                "fallback_playbook.json",
                {
                    "name": "Fallback Playbook",
                    "priority": 50,
                },
            )
            registry = self._make_registry(tmpdir)
            playbooks = registry.reload()
            assert len(playbooks) == 1
            assert playbooks[0].playbook_id == "fallback_playbook"

    def test_missing_name_uses_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_playbook(
                tmpdir,
                "no_name.json",
                {
                    "id": "no_name_playbook",
                    "priority": 50,
                },
            )
            registry = self._make_registry(tmpdir)
            playbooks = registry.reload()
            assert len(playbooks) == 1
            assert playbooks[0].name == "no_name_playbook"

    def test_non_object_json_skipped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_playbook(tmpdir, "array.json", [1, 2, 3])
            registry = self._make_registry(tmpdir)
            playbooks = registry.reload()
            assert len(playbooks) == 0

    def test_rules_not_dict_still_loads(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_playbook(
                tmpdir,
                "bad_rules.json",
                {
                    "id": "bad_rules",
                    "name": "Bad Rules Playbook",
                    "rules": "should be a dict",
                },
            )
            registry = self._make_registry(tmpdir)
            playbooks = registry.reload()
            assert len(playbooks) == 1
            assert playbooks[0].playbook_id == "bad_rules"

    def test_priority_clamped_high(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_playbook(
                tmpdir,
                "high_p.json",
                {
                    "id": "high_priority",
                    "name": "High Priority",
                    "priority": 200,
                },
            )
            registry = self._make_registry(tmpdir)
            playbooks = registry.reload()
            assert len(playbooks) == 1
            assert playbooks[0].priority == 100

    def test_priority_clamped_low(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_playbook(
                tmpdir,
                "low_p.json",
                {
                    "id": "low_priority",
                    "name": "Low Priority",
                    "priority": -10,
                },
            )
            registry = self._make_registry(tmpdir)
            playbooks = registry.reload()
            assert len(playbooks) == 1
            assert playbooks[0].priority == 0

    def test_validate_clean_data(self):
        warnings = PlaybookRegistry._validate_json_playbook(
            {
                "id": "ok",
                "name": "OK Playbook",
                "enabled": True,
                "priority": 50,
                "description": "Test",
                "rules": {"entry": {}},
            },
            Path("test.json"),
        )
        assert warnings == []

    def test_validate_bad_types(self):
        warnings = PlaybookRegistry._validate_json_playbook(
            {"id": 123, "name": True, "priority": "high", "rules": [1, 2]},
            Path("test.json"),
        )
        assert len(warnings) >= 3

    def test_validate_missing_fields(self):
        warnings = PlaybookRegistry._validate_json_playbook(
            {"description": "no id/name"},
            Path("test.json"),
        )
        assert any("id" in warning for warning in warnings)
        assert any("name" in warning for warning in warnings)
