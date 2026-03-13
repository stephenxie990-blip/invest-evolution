from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    return value


_DEFAULT_PROMOTION_RESEARCH_FEEDBACK_GATE = {
    "min_sample_count": 5,
    "blocked_biases": ["tighten_risk", "recalibrate_probability"],
    "max_brier_like_direction_score": 0.25,
    "horizons": {
        "T+20": {
            "min_hit_rate": 0.45,
            "max_invalidation_rate": 0.30,
            "min_interval_hit_rate": 0.40,
        }
    },
}


def _deep_merge(base: dict[str, Any], patch: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = dict(base or {})
    for key, value in dict(patch or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(dict(merged.get(key) or {}), value)
        else:
            merged[key] = value
    return merged


def _format_threshold(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}"


def _build_research_feedback_guardrail_view(
    policy: dict[str, Any] | None,
    *,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved = dict(policy or {})
    override_payload = dict(overrides or {})
    if not resolved:
        return {
            "enabled": False,
            "summary": "未启用 research_feedback 校准门。",
            "reason_codes": [],
            "policy_source": {
                "mode": "disabled",
                "defaults_applied": False,
                "user_overrides_present": bool(override_payload),
                "user_override_keys": sorted(str(key) for key in override_payload.keys()),
            },
            "thresholds": {},
        }

    blocked_biases = [str(item) for item in (resolved.get("blocked_biases") or []) if str(item).strip()]
    t20 = dict((resolved.get("horizons") or {}).get("T+20") or {})
    clauses: list[str] = []
    if resolved.get("min_sample_count") is not None:
        clauses.append(f"样本数>={int(resolved.get('min_sample_count') or 0)}")
    if blocked_biases:
        clauses.append(f"阻断偏置={','.join(blocked_biases)}")
    if resolved.get("max_brier_like_direction_score") is not None:
        clauses.append(
            f"方向分数<={_format_threshold(resolved.get('max_brier_like_direction_score'))}"
        )
    if t20.get("min_hit_rate") is not None:
        clauses.append(f"T+20命中率>={_format_threshold(t20.get('min_hit_rate'))}")
    if t20.get("max_invalidation_rate") is not None:
        clauses.append(
            f"T+20失效率<={_format_threshold(t20.get('max_invalidation_rate'))}"
        )
    if t20.get("min_interval_hit_rate") is not None:
        clauses.append(
            f"T+20区间命中率>={_format_threshold(t20.get('min_interval_hit_rate'))}"
        )

    mode = "default_plus_override" if override_payload else "default_injected"
    summary_prefix = "已启用（默认模板+用户覆盖）" if override_payload else "默认启用"
    summary = f"{summary_prefix} research_feedback 校准门："
    if clauses:
        summary += "；".join(clauses) + "。"
    else:
        summary += "已启用基础校准约束。"

    reason_codes = ["default_research_feedback_gate_enabled"]
    if override_payload:
        reason_codes.append("research_feedback_user_override_merged")
    if blocked_biases:
        reason_codes.append("research_feedback_bias_blocked")
    if resolved.get("max_brier_like_direction_score") is not None:
        reason_codes.append("research_feedback_direction_calibration_guarded")
    if t20:
        reason_codes.append("research_feedback_t20_horizon_guarded")

    return {
        "enabled": True,
        "summary": summary,
        "reason_codes": reason_codes,
        "policy_source": {
            "mode": mode,
            "defaults_applied": True,
            "user_overrides_present": bool(override_payload),
            "user_override_keys": sorted(str(key) for key in override_payload.keys()),
        },
        "thresholds": jsonable(resolved),
    }


def _build_guardrails(
    optimization: dict[str, Any] | None,
    *,
    raw_optimization: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_optimization = dict(optimization or {})
    raw_payload = dict(raw_optimization or {})
    promotion_gate = dict(resolved_optimization.get("promotion_gate") or {})
    raw_promotion_gate = dict(raw_payload.get("promotion_gate") or {})
    research_feedback_view = _build_research_feedback_guardrail_view(
        dict(promotion_gate.get("research_feedback") or {}),
        overrides=dict(raw_promotion_gate.get("research_feedback") or {}),
    )
    promotion_summary = (
        "晋升门已启用，使用默认模板+用户覆盖的 research_feedback 校准约束。"
        if research_feedback_view.get("policy_source", {}).get("user_overrides_present")
        else "晋升门已启用，默认纳入 research_feedback 校准约束。"
    )
    return {
        "promotion_gate": {
            "enabled": bool(promotion_gate),
            "summary": promotion_summary,
            "reason_codes": ["promotion_gate_enabled", "promotion_gate_research_feedback_enabled"],
            "research_feedback": research_feedback_view,
        }
    }


class TrainingLabArtifactStore:
    def __init__(
        self,
        *,
        training_plan_dir: Path,
        training_run_dir: Path,
        training_eval_dir: Path,
    ):
        self.training_plan_dir = training_plan_dir
        self.training_run_dir = training_run_dir
        self.training_eval_dir = training_eval_dir

    def ensure_storage(self) -> None:
        self.training_plan_dir.mkdir(parents=True, exist_ok=True)
        self.training_run_dir.mkdir(parents=True, exist_ok=True)
        self.training_eval_dir.mkdir(parents=True, exist_ok=True)

    def counts(self) -> dict[str, int]:
        self.ensure_storage()
        return {
            'plan_count': len(list(self.training_plan_dir.glob('*.json'))),
            'run_count': len(list(self.training_run_dir.glob('*.json'))),
            'evaluation_count': len(list(self.training_eval_dir.glob('*.json'))),
        }

    def new_plan_id(self) -> str:
        self.ensure_storage()
        return f"plan_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"

    def new_run_id(self) -> str:
        self.ensure_storage()
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

    def read_json_artifact(self, path: Path, *, label: str = 'artifact') -> dict[str, Any]:
        if not path.exists():
            raise FileNotFoundError(f'{label} not found: {path}')
        return json.loads(path.read_text(encoding='utf-8'))

    def list_json_artifacts(self, directory: Path, *, limit: int = 20) -> dict[str, Any]:
        directory.mkdir(parents=True, exist_ok=True)
        paths = sorted(directory.glob('*.json'), reverse=True)[: max(1, int(limit or 20))]
        items: list[dict[str, Any]] = []
        for path in paths:
            item: dict[str, Any] = {'path': str(path), 'name': path.name}
            try:
                payload = json.loads(path.read_text(encoding='utf-8'))
            except Exception:
                payload = {}
            if isinstance(payload, dict):
                for key in ('plan_id', 'run_id', 'status', 'created_at', 'last_run_id', 'last_run_at'):
                    if key in payload:
                        item[key] = payload.get(key)
                artifacts = dict(payload.get('artifacts') or {})
                if artifacts:
                    item['artifacts'] = jsonable(artifacts)
                spec = dict(payload.get('spec') or {})
                if spec:
                    item['spec'] = jsonable(spec)
            items.append(item)
        return {
            'count': len(paths),
            'items': items,
        }

    @staticmethod
    def _normalize_optimization_payload(optimization: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(optimization or {})
        promotion_gate = dict(payload.get('promotion_gate') or {})
        promotion_gate['research_feedback'] = _deep_merge(
            _DEFAULT_PROMOTION_RESEARCH_FEEDBACK_GATE,
            dict(promotion_gate.get('research_feedback') or {}),
        )
        payload['promotion_gate'] = promotion_gate
        return payload

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
        normalized_optimization = self._normalize_optimization_payload(optimization)
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
            'optimization': jsonable(normalized_optimization),
            'guardrails': jsonable(_build_guardrails(normalized_optimization, raw_optimization=optimization)),
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
                'guardrails': dict(plan.get('guardrails') or {}),
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
