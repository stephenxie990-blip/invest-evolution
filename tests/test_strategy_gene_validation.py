"""
策略基因 JSON Schema 校验测试

覆盖：
  - 合法 JSON 基因加载
  - 缺失字段检测与降级
  - 类型错误检测
  - 优先级范围校验
"""

import sys
import json
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from commander import StrategyGeneRegistry


class TestStrategyGeneValidation:
    """策略基因 JSON 校验"""

    def _make_registry(self, tmpdir: str) -> StrategyGeneRegistry:
        return StrategyGeneRegistry(Path(tmpdir))

    def _write_gene(self, tmpdir: str, filename: str, content: dict | list | str) -> Path:
        p = Path(tmpdir) / filename
        if isinstance(content, (dict, list)):
            p.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
        else:
            p.write_text(content, encoding="utf-8")
        return p

    # --- 合法 JSON ---

    def test_valid_json_gene_loads(self):
        """完整合法 JSON 基因加载成功"""
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_gene(tmpdir, "test.json", {
                "id": "test_gene",
                "name": "Test Gene",
                "enabled": True,
                "priority": 70,
                "description": "A test strategy gene.",
                "rules": {
                    "entry": {"rsi_max": 30},
                    "risk": {"stop_loss_pct": 0.05},
                },
            })
            reg = self._make_registry(tmpdir)
            genes = reg.reload()
            assert len(genes) == 1
            g = genes[0]
            assert g.gene_id == "test_gene"
            assert g.name == "Test Gene"
            assert g.enabled is True
            assert g.priority == 70
            assert g.description == "A test strategy gene."
            assert g.kind == "json"

    # --- 缺失字段 ---

    def test_missing_id_uses_filename(self):
        """缺少 id → 使用文件名作为 id"""
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_gene(tmpdir, "fallback_gene.json", {
                "name": "Fallback Gene",
                "priority": 50,
            })
            reg = self._make_registry(tmpdir)
            genes = reg.reload()
            assert len(genes) == 1
            assert genes[0].gene_id == "fallback_gene"

    def test_missing_name_uses_id(self):
        """缺少 name → 使用 id 作为 name"""
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_gene(tmpdir, "no_name.json", {
                "id": "no_name_gene",
                "priority": 50,
            })
            reg = self._make_registry(tmpdir)
            genes = reg.reload()
            assert len(genes) == 1
            assert genes[0].name == "no_name_gene"

    # --- 类型错误 ---

    def test_non_object_json_skipped(self):
        """JSON 数组 → 加载失败，被 reload 的 try/except 捕获跳过"""
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_gene(tmpdir, "array.json", [1, 2, 3])
            reg = self._make_registry(tmpdir)
            genes = reg.reload()
            assert len(genes) == 0  # 被跳过

    def test_rules_not_dict_still_loads(self):
        """rules 非 dict → 发出 warning 但仍加载"""
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_gene(tmpdir, "bad_rules.json", {
                "id": "bad_rules",
                "name": "Bad Rules Gene",
                "rules": "should be a dict",
            })
            reg = self._make_registry(tmpdir)
            genes = reg.reload()
            assert len(genes) == 1
            assert genes[0].gene_id == "bad_rules"

    # --- 优先级范围 ---

    def test_priority_clamped_high(self):
        """priority > 100 → 被 clamp 到 100"""
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_gene(tmpdir, "high_p.json", {
                "id": "high_priority",
                "name": "High Priority",
                "priority": 200,
            })
            reg = self._make_registry(tmpdir)
            genes = reg.reload()
            assert len(genes) == 1
            assert genes[0].priority == 100

    def test_priority_clamped_low(self):
        """priority < 0 → 被 clamp 到 0"""
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_gene(tmpdir, "low_p.json", {
                "id": "low_priority",
                "name": "Low Priority",
                "priority": -10,
            })
            reg = self._make_registry(tmpdir)
            genes = reg.reload()
            assert len(genes) == 1
            assert genes[0].priority == 0

    # --- _validate_json_gene 直接测试 ---

    def test_validate_clean_data(self):
        """完整数据 → 无 warning"""
        warnings = StrategyGeneRegistry._validate_json_gene(
            {"id": "ok", "name": "OK Gene", "enabled": True, "priority": 50,
             "description": "Test", "rules": {"entry": {}}},
            Path("test.json"),
        )
        assert warnings == []

    def test_validate_bad_types(self):
        """多种类型错误 → 多条 warning"""
        warnings = StrategyGeneRegistry._validate_json_gene(
            {"id": 123, "name": True, "priority": "high", "rules": [1, 2]},
            Path("test.json"),
        )
        assert len(warnings) >= 3  # id type, name type, priority invalid, rules type

    def test_validate_missing_fields(self):
        """缺失必要字段 → warning"""
        warnings = StrategyGeneRegistry._validate_json_gene(
            {"description": "no id/name"},
            Path("test.json"),
        )
        assert any("id" in w for w in warnings)
        assert any("name" in w for w in warnings)
