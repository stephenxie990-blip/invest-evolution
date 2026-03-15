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

_DEFAULT_PROMOTION_REGIME_VALIDATION_GATE = {
    "min_distinct_regimes": 2,
    "min_samples_per_regime": 1,
    "min_avg_return_pct": 0.0,
    "min_win_rate": 0.40,
    "min_benchmark_pass_rate": 0.40,
    "max_dominant_regime_share": 0.75,
}

_DEFAULT_PROMOTION_RETURN_OBJECTIVES = {
    "min_avg_return_pct": 0.0,
    "min_median_return_pct": 0.0,
    "min_cumulative_return_pct": 0.0,
    "min_win_rate": 0.50,
    "max_loss_share": 0.50,
    "min_benchmark_pass_rate": 0.50,
}

_DEFAULT_PROMOTION_CANDIDATE_AB_GATE = {
    "required_when_candidate_present": True,
    "require_candidate_outperform_active": True,
    "min_return_lift_pct": 0.0,
    "min_strategy_score_lift": 0.0,
    "min_benchmark_lift": 0.0,
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


def _build_regime_validation_guardrail_view(policy: dict[str, Any] | None) -> dict[str, Any]:
    resolved = dict(policy or {})
    if not resolved:
        return {
            "enabled": False,
            "summary": "未启用 regime 分层验证。",
            "reason_codes": [],
            "thresholds": {},
        }

    summary = (
        "默认启用 regime 分层验证："
        f"至少覆盖 {int(resolved.get('min_distinct_regimes') or 0)} 个市场状态，"
        f"每个状态样本数>={int(resolved.get('min_samples_per_regime') or 0)}，"
        f"分状态平均收益>={_format_threshold(resolved.get('min_avg_return_pct'))}%，"
        f"胜率>={_format_threshold(resolved.get('min_win_rate'))}，"
        f"基准通过率>={_format_threshold(resolved.get('min_benchmark_pass_rate'))}。"
    )
    return {
        "enabled": True,
        "summary": summary,
        "reason_codes": [
            "default_regime_validation_gate_enabled",
            "regime_diversity_guarded",
            "regime_performance_guarded",
        ],
        "thresholds": jsonable(resolved),
    }


def _build_return_objectives_guardrail_view(policy: dict[str, Any] | None) -> dict[str, Any]:
    resolved = dict(policy or {})
    if not resolved:
        return {
            "enabled": False,
            "summary": "未启用收益导向晋升目标。",
            "reason_codes": [],
            "thresholds": {},
        }

    summary = (
        "默认启用收益导向晋升目标："
        f"平均收益>={_format_threshold(resolved.get('min_avg_return_pct'))}%，"
        f"中位收益>={_format_threshold(resolved.get('min_median_return_pct'))}%，"
        f"累计收益>={_format_threshold(resolved.get('min_cumulative_return_pct'))}%，"
        f"胜率>={_format_threshold(resolved.get('min_win_rate'))}，"
        f"亏损占比<={_format_threshold(resolved.get('max_loss_share'))}，"
        f"基准通过率>={_format_threshold(resolved.get('min_benchmark_pass_rate'))}。"
    )
    return {
        "enabled": True,
        "summary": summary,
        "reason_codes": [
            "default_return_objectives_gate_enabled",
            "return_quality_guarded",
        ],
        "thresholds": jsonable(resolved),
    }


def _build_candidate_ab_guardrail_view(policy: dict[str, Any] | None) -> dict[str, Any]:
    resolved = dict(policy or {})
    if not resolved:
        return {
            "enabled": False,
            "summary": "未启用候选策略 A/B 对照门。",
            "reason_codes": [],
            "thresholds": {},
        }

    summary = (
        "默认启用候选策略 A/B 对照门："
        f"候选对 active 的收益 lift>={_format_threshold(resolved.get('min_return_lift_pct'))}%，"
        f"策略分 lift>={_format_threshold(resolved.get('min_strategy_score_lift'))}，"
        f"基准通过 lift>={_format_threshold(resolved.get('min_benchmark_lift'))}。"
    )
    return {
        "enabled": True,
        "summary": summary,
        "reason_codes": [
            "default_candidate_ab_gate_enabled",
            "candidate_ab_return_guarded",
            "candidate_ab_benchmark_guarded",
        ],
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
    regime_validation_view = _build_regime_validation_guardrail_view(
        dict(promotion_gate.get("regime_validation") or {}),
    )
    return_objectives_view = _build_return_objectives_guardrail_view(
        dict(promotion_gate.get("return_objectives") or {}),
    )
    candidate_ab_view = _build_candidate_ab_guardrail_view(
        dict(promotion_gate.get("candidate_ab") or {}),
    )
    promotion_summary = (
        "晋升门已启用，使用默认模板+用户覆盖的 research_feedback 校准约束。"
        if research_feedback_view.get("policy_source", {}).get("user_overrides_present")
        else "晋升门已启用，默认纳入 research_feedback、regime 分层验证、收益目标与候选 A/B 对照约束。"
    )
    return {
        "promotion_gate": {
            "enabled": bool(promotion_gate),
            "summary": promotion_summary,
            "reason_codes": [
                "promotion_gate_enabled",
                "promotion_gate_research_feedback_enabled",
                "promotion_gate_regime_validation_enabled",
                "promotion_gate_return_objectives_enabled",
                "promotion_gate_candidate_ab_enabled",
            ],
            "research_feedback": research_feedback_view,
            "regime_validation": regime_validation_view,
            "return_objectives": return_objectives_view,
            "candidate_ab": candidate_ab_view,
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
                run_payload = dict(payload.get('payload') or {})
                results = [
                    dict(entry)
                    for entry in list(run_payload.get('results') or [])
                    if isinstance(entry, dict)
                ]
                if results:
                    latest = dict(results[-1])
                    item['latest_result'] = jsonable(
                        {
                            'cycle_id': latest.get('cycle_id'),
                            'status': str(latest.get('status') or ''),
                            'return_pct': latest.get('return_pct'),
                            'benchmark_passed': bool(latest.get('benchmark_passed', False)),
                            'promotion_record': dict(latest.get('promotion_record') or {}),
                            'lineage_record': dict(latest.get('lineage_record') or {}),
                        }
                    )
                assessment = dict(payload.get('assessment') or {})
                if assessment:
                    item['assessment'] = jsonable(
                        {
                            'success_count': int(assessment.get('success_count', 0) or 0),
                            'no_data_count': int(assessment.get('no_data_count', 0) or 0),
                            'error_count': int(assessment.get('error_count', 0) or 0),
                            'avg_return_pct': assessment.get('avg_return_pct'),
                            'benchmark_pass_rate': assessment.get('benchmark_pass_rate'),
                            'latest_result': dict(assessment.get('latest_result') or {}),
                        }
                    )
                promotion = dict(payload.get('promotion') or {})
                if promotion:
                    research_feedback = dict(promotion.get('research_feedback') or {})
                    item['promotion'] = jsonable(
                        {
                            'verdict': str(promotion.get('verdict') or ''),
                            'passed': bool(promotion.get('passed', False)),
                            'research_feedback': {
                                'enabled': bool(research_feedback.get('enabled', False)),
                                'passed': bool(research_feedback.get('passed', False)),
                                'summary': str(research_feedback.get('summary') or ''),
                            },
                        }
                    )
                governance_metrics = dict(payload.get('governance_metrics') or {})
                if governance_metrics:
                    item['governance_metrics'] = jsonable(governance_metrics)
                realism_summary = dict(payload.get('realism_summary') or {})
                if realism_summary:
                    item['realism_summary'] = jsonable(realism_summary)
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
        promotion_gate['regime_validation'] = _deep_merge(
            _DEFAULT_PROMOTION_REGIME_VALIDATION_GATE,
            dict(promotion_gate.get('regime_validation') or {}),
        )
        promotion_gate['return_objectives'] = _deep_merge(
            _DEFAULT_PROMOTION_RETURN_OBJECTIVES,
            dict(promotion_gate.get('return_objectives') or {}),
        )
        promotion_gate['candidate_ab'] = _deep_merge(
            _DEFAULT_PROMOTION_CANDIDATE_AB_GATE,
            dict(promotion_gate.get('candidate_ab') or {}),
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
                'protocol': dict(plan.get('protocol') or {}),
                'dataset': dict(plan.get('dataset') or {}),
                'model_scope': dict(plan.get('model_scope') or {}),
                'optimization': dict(plan.get('optimization') or {}),
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
