"""Strategy gene models and registry for commander runtime."""

from __future__ import annotations

import ast
import json
import logging
import textwrap
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class StrategyGene:
    """A strategy asset loaded from md/json/py."""

    gene_id: str
    name: str
    kind: str
    path: str
    enabled: bool = True
    priority: int = 50
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class StrategyGeneRegistry:
    """Loads editable strategy genes from local files."""

    SUPPORTED_SUFFIXES = {".md", ".json", ".py"}

    def __init__(self, strategy_dir: Path):
        self.strategy_dir = strategy_dir
        self.genes: list[StrategyGene] = []

    def ensure_default_templates(self) -> None:
        self.strategy_dir.mkdir(parents=True, exist_ok=True)

        md_file = self.strategy_dir / "momentum_trend.md"
        if not md_file.exists():
            md_file.write_text(
                textwrap.dedent(
                    """\
                    ---
                    id: momentum_trend
                    name: Momentum Trend Gene
                    enabled: true
                    priority: 80
                    description: Focus on strong trend continuation with volume confirmation.
                    ---

                    # Momentum Trend Gene

                    Entry:
                    - MA5 > MA20 > MA60
                    - RSI in [45, 78]
                    - volume_ratio >= 1.5

                    Exit:
                    - hard_stop: 5%
                    - take_profit: 15%
                    - trailing_drawdown: 8%
                    """
                ),
                encoding="utf-8",
            )

        json_file = self.strategy_dir / "mean_reversion.json"
        if not json_file.exists():
            json_file.write_text(
                json.dumps(
                    {
                        "id": "mean_reversion",
                        "name": "Mean Reversion Gene",
                        "enabled": True,
                        "priority": 60,
                        "description": "Catch oversold rebounds with strict risk limits.",
                        "rules": {
                            "entry": {
                                "rsi_max": 30,
                                "drop_20d_min": 0.12,
                                "volume_ratio_min": 1.2,
                            },
                            "risk": {
                                "stop_loss_pct": 0.06,
                                "take_profit_pct": 0.10,
                                "max_hold_days": 12,
                            },
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

        py_file = self.strategy_dir / "risk_guard.py"
        if not py_file.exists():
            py_file.write_text(
                textwrap.dedent(
                    '''\
                    """Risk guard gene.

                    This file is intentionally simple and editable.
                    Commander only parses metadata by default.
                    """

                    GENE_META = {
                        "id": "risk_guard",
                        "name": "Risk Guard Gene",
                        "enabled": True,
                        "priority": 95,
                        "description": "Portfolio level drawdown and exposure guardrails.",
                    }

                    def suggest_risk_overrides(context: dict) -> dict:
                        """Optional helper function if you want Python-based custom logic."""
                        drawdown = float(context.get("drawdown", 0.0))
                        if drawdown > 0.10:
                            return {"position_size": 0.10, "max_positions": 2}
                        return {"position_size": 0.20, "max_positions": 5}
                    '''
                ),
                encoding="utf-8",
            )

    def reload(self, create_dir: bool = True) -> list[StrategyGene]:
        if create_dir:
            self.strategy_dir.mkdir(parents=True, exist_ok=True)
        elif not self.strategy_dir.exists():
            self.genes = []
            return []
        genes: list[StrategyGene] = []

        for path in sorted(self.strategy_dir.glob("*")):
            if not path.is_file() or path.suffix.lower() not in self.SUPPORTED_SUFFIXES:
                continue
            try:
                gene = self._load_gene(path)
                if gene:
                    genes.append(gene)
            except Exception as exc:
                logger.warning("Load strategy gene failed %s: %s", path, exc)

        genes.sort(key=lambda gene: (-gene.priority, gene.gene_id))
        self.genes = genes
        return genes

    def list_genes(self, only_enabled: bool = False) -> list[StrategyGene]:
        if not only_enabled:
            return list(self.genes)
        return [gene for gene in self.genes if gene.enabled]

    def to_summary(self) -> str:
        if not self.genes:
            return "No strategy genes loaded."
        lines = []
        for gene in self.genes:
            status = "ON" if gene.enabled else "OFF"
            lines.append(
                f"- [{status}] {gene.gene_id} ({gene.kind}, P{gene.priority}): {gene.description}"
            )
        return "\n".join(lines)

    def _load_gene(self, path: Path) -> StrategyGene | None:
        loader = {
            ".md": self._load_md_gene,
            ".json": self._load_json_gene,
            ".py": self._load_py_gene,
        }.get(path.suffix.lower())
        return loader(path) if loader else None

    def _load_md_gene(self, path: Path) -> StrategyGene:
        text = path.read_text(encoding="utf-8")
        front, body = self._split_front_matter(text)
        gene_id = str(front.get("id") or path.stem)
        name = str(front.get("name") or gene_id)
        enabled = self._to_bool(front.get("enabled", True))
        priority = self._to_int(front.get("priority", 50), 50)
        description = str(front.get("description") or self._first_nonempty_line(body) or "")
        return StrategyGene(
            gene_id=gene_id,
            name=name,
            kind="md",
            path=str(path),
            enabled=enabled,
            priority=priority,
            description=description,
            metadata={"front_matter": front},
        )

    def _load_json_gene(self, path: Path) -> StrategyGene:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("JSON strategy gene must be an object")

        warnings = self._validate_json_gene(data, path)
        for warning in warnings:
            logger.warning("Strategy gene %s: %s", path.name, warning)

        gene_id = str(data.get("id") or path.stem)
        name = str(data.get("name") or gene_id)
        enabled = self._to_bool(data.get("enabled", True))
        priority = max(0, min(100, self._to_int(data.get("priority", 50), 50)))
        description = str(data.get("description") or "")
        metadata = dict(data)
        return StrategyGene(
            gene_id=gene_id,
            name=name,
            kind="json",
            path=str(path),
            enabled=enabled,
            priority=priority,
            description=description,
            metadata=metadata,
        )

    @staticmethod
    def _validate_json_gene(data: dict, path: Path) -> list[str]:
        warnings: list[str] = []
        if "id" not in data:
            warnings.append("missing required field 'id', will use filename as id")
        elif not isinstance(data["id"], str):
            warnings.append(f"field 'id' should be string, got {type(data['id']).__name__}")
        if "name" not in data:
            warnings.append("missing required field 'name', will use id as name")
        elif not isinstance(data["name"], str):
            warnings.append(f"field 'name' should be string, got {type(data['name']).__name__}")
        if "enabled" in data and not isinstance(data["enabled"], (bool, int, float, str)):
            warnings.append(f"field 'enabled' has unexpected type {type(data['enabled']).__name__}")
        if "priority" in data:
            try:
                priority = int(data["priority"])
                if priority < 0 or priority > 100:
                    warnings.append(
                        f"field 'priority' value {priority} out of range [0, 100], will be clamped"
                    )
            except (TypeError, ValueError):
                warnings.append(f"field 'priority' is not a valid integer: {data['priority']!r}")
        if "description" in data and not isinstance(data["description"], str):
            warnings.append(
                f"field 'description' should be string, got {type(data['description']).__name__}"
            )
        if "rules" in data and not isinstance(data["rules"], dict):
            warnings.append(f"field 'rules' should be an object, got {type(data['rules']).__name__}")
        return warnings

    def _load_py_gene(self, path: Path) -> StrategyGene:
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        module_doc = ast.get_docstring(tree) or ""
        meta: dict[str, Any] = {}
        functions: list[str] = []
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in {
                        "GENE_META",
                        "STRATEGY_META",
                        "META",
                    }:
                        try:
                            literal = ast.literal_eval(node.value)
                            if isinstance(literal, dict):
                                meta = literal
                        except Exception as exc:
                            logger.warning(
                                "Failed to parse python strategy gene metadata from %s: %s",
                                path,
                                exc,
                            )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.append(node.name)

        gene_id = str(meta.get("id") or path.stem)
        name = str(meta.get("name") or gene_id)
        enabled = self._to_bool(meta.get("enabled", True))
        priority = self._to_int(meta.get("priority", 50), 50)
        description = str(meta.get("description") or self._first_nonempty_line(module_doc) or "")
        if not description:
            description = "Python strategy gene"
        return StrategyGene(
            gene_id=gene_id,
            name=name,
            kind="py",
            path=str(path),
            enabled=enabled,
            priority=priority,
            description=description,
            metadata={"meta": meta, "functions": functions},
        )

    @staticmethod
    def _first_nonempty_line(text: str) -> str:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped:
                return stripped
        return ""

    @staticmethod
    def _to_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        return str(value).strip().lower() not in {"0", "false", "off", "no", ""}

    @staticmethod
    def _to_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except Exception:
            return default

    @staticmethod
    def _split_front_matter(text: str) -> tuple[dict[str, str], str]:
        lines = text.splitlines()
        if not lines or lines[0].strip() != "---":
            return {}, text
        end = None
        for index in range(1, len(lines)):
            if lines[index].strip() == "---":
                end = index
                break
        if end is None:
            return {}, text
        front_lines = lines[1:end]
        body = "\n".join(lines[end + 1 :])
        front: dict[str, str] = {}
        for line in front_lines:
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            front[key.strip()] = value.strip()
        return front, body
