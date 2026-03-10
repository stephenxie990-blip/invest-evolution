from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None


def jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [jsonable(v) for v in value]
    if isinstance(value, tuple):
        return [jsonable(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if np is not None and isinstance(value, np.generic):
        return value.item()
    return value


class TrainingLabArtifactStore:
    def __init__(self, *, training_plan_dir: Path, training_run_dir: Path, training_eval_dir: Path):
        self.training_plan_dir = Path(training_plan_dir)
        self.training_run_dir = Path(training_run_dir)
        self.training_eval_dir = Path(training_eval_dir)

    def ensure_storage(self) -> None:
        self.training_plan_dir.mkdir(parents=True, exist_ok=True)
        self.training_run_dir.mkdir(parents=True, exist_ok=True)
        self.training_eval_dir.mkdir(parents=True, exist_ok=True)

    def counts(self) -> dict[str, int]:
        return {
            "plan_count": len(list(self.training_plan_dir.glob("*.json"))) if self.training_plan_dir.exists() else 0,
            "run_count": len(list(self.training_run_dir.glob("*.json"))) if self.training_run_dir.exists() else 0,
            "evaluation_count": len(list(self.training_eval_dir.glob("*.json"))) if self.training_eval_dir.exists() else 0,
        }

    def new_plan_id(self) -> str:
        return f"plan_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"

    def new_run_id(self) -> str:
        return f"run_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"

    def plan_path(self, plan_id: str) -> Path:
        return self.training_plan_dir / f"{plan_id}.json"

    def run_path(self, run_id: str) -> Path:
        return self.training_run_dir / f"{run_id}.json"

    def evaluation_path(self, run_id: str) -> Path:
        return self.training_eval_dir / f"{run_id}.json"

    def write_json_artifact(self, path: Path, payload: dict[str, Any]) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(jsonable(payload), ensure_ascii=False, indent=2), encoding='utf-8')
        return path

    def read_json_artifact(self, path: Path, *, label: str) -> dict[str, Any]:
        if not path.exists():
            raise FileNotFoundError(f"{label} not found: {path.stem}")
        return json.loads(path.read_text(encoding='utf-8'))

    def list_json_artifacts(self, directory: Path, *, limit: int = 20) -> dict[str, Any]:
        if not directory.exists():
            return {"count": 0, "items": []}
        rows = []
        for path in sorted(directory.glob('*.json'), reverse=True)[: max(1, int(limit))]:
            try:
                rows.append(json.loads(path.read_text(encoding='utf-8')))
            except Exception:
                continue
        return {"count": len(rows), "items": rows}

    def build_training_plan_payload(
        self,
        *,
        rounds: int,
        mock: bool,
        source: str,
        goal: str = '',
        notes: str = '',
        tags: list[str] | None = None,
        detail_mode: str = 'fast',
        protocol: dict[str, Any] | None = None,
        dataset: dict[str, Any] | None = None,
        model_scope: dict[str, Any] | None = None,
        optimization: dict[str, Any] | None = None,
        llm: dict[str, Any] | None = None,
        plan_id: str | None = None,
        auto_generated: bool = False,
    ) -> dict[str, Any]:
        resolved_plan_id = plan_id or self.new_plan_id()
        return {
            'plan_id': resolved_plan_id,
            'created_at': datetime.now().isoformat(),
            'status': 'planned',
            'source': source,
            'auto_generated': bool(auto_generated),
            'spec': {
                'rounds': int(rounds),
                'mock': bool(mock),
                'detail_mode': str(detail_mode or 'fast'),
            },
            'protocol': jsonable(dict(protocol or {})),
            'dataset': jsonable(dict(dataset or {})),
            'model_scope': jsonable(dict(model_scope or {})),
            'optimization': jsonable(dict(optimization or {})),
            'llm': jsonable(dict(llm or {})),
            'objective': {
                'goal': str(goal or ''),
                'notes': str(notes or ''),
                'tags': [str(tag) for tag in (tags or []) if str(tag).strip()],
            },
            'artifacts': {
                'plan_path': str(self.plan_path(resolved_plan_id)),
            },
        }

    def record_training_lab_artifacts(
        self,
        *,
        plan: dict[str, Any],
        payload: dict[str, Any],
        status: str,
        eval_payload: dict[str, Any],
        run_id: str | None = None,
        error: str = '',
    ) -> dict[str, Any]:
        resolved_run_id = run_id or self.new_run_id()
        run_path = self.run_path(resolved_run_id)
        evaluation_path = self.evaluation_path(resolved_run_id)
        run_payload = {
            'run_id': resolved_run_id,
            'plan_id': plan['plan_id'],
            'created_at': datetime.now().isoformat(),
            'status': status,
            'error': str(error or ''),
            'plan': {
                'plan_id': plan['plan_id'],
                'source': plan.get('source'),
                'auto_generated': plan.get('auto_generated', False),
                'spec': dict(plan.get('spec') or {}),
                'objective': dict(plan.get('objective') or {}),
                'llm': dict(plan.get('llm') or {}),
            },
            'payload': jsonable(payload),
        }
        plan_update = dict(plan)
        plan_update['status'] = 'completed' if status in {'ok', 'completed', 'completed_with_skips', 'insufficient_data'} else status
        plan_update['last_run_id'] = resolved_run_id
        plan_update['last_run_at'] = datetime.now().isoformat()
        plan_update.setdefault('artifacts', {})['latest_run_path'] = str(run_path)
        plan_update.setdefault('artifacts', {})['latest_evaluation_path'] = str(evaluation_path)
        self.write_json_artifact(run_path, run_payload)
        self.write_json_artifact(evaluation_path, eval_payload)
        self.write_json_artifact(self.plan_path(plan['plan_id']), plan_update)
        return {'plan': plan_update, 'run': run_payload, 'evaluation': eval_payload}
