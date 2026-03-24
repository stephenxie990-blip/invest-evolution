"""Training lab artifacts and evaluation services."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any

from invest_evolution.application.training.observability import (
    build_governance_metrics,
    build_realism_summary,
    evaluate_research_feedback_gate,
)
from invest_evolution.application.training.research import TrainingFeedbackService
from invest_evolution.common.utils import list_json_artifact_paths, safe_read_json_dict
from invest_evolution.investment.shared.policy import normalize_promotion_gate_policy


def jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    return value


_CORE_EXPLAINABILITY_ARTIFACT_KEYS = (
    "cycle_result_path",
    "selection_artifact_json_path",
    "selection_artifact_markdown_path",
    "manager_review_artifact_json_path",
    "manager_review_artifact_markdown_path",
    "allocation_review_artifact_json_path",
    "allocation_review_artifact_markdown_path",
    "optimization_events_path",
    "validation_report_path",
    "trade_history_path",
)


def collect_core_explainability_artifacts(result: dict[str, Any] | None) -> dict[str, str]:
    artifacts = dict(dict(result or {}).get("artifacts") or {})
    return {
        key: value
        for key in _CORE_EXPLAINABILITY_ARTIFACT_KEYS
        if (value := str(artifacts.get(key) or "").strip())
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


def _build_manager_regime_validation_guardrail_view(policy: dict[str, Any] | None) -> dict[str, Any]:
    resolved = dict(policy or {})
    if not resolved or not bool(resolved.get("enabled", False)):
        return {
            "enabled": False,
            "summary": "未启用 manager x regime 二维验证。",
            "reason_codes": [],
            "thresholds": {},
        }

    summary = (
        "已启用 manager x regime 二维验证："
        f"至少覆盖 {int(resolved.get('min_manager_count') or 0)} 个 manager，"
        f"每个 manager 样本数>={int(resolved.get('min_samples_per_manager') or 0)}，"
        f"每个 manager 至少覆盖 {int(resolved.get('min_distinct_regimes') or 0)} 个 regime。"
    )
    return {
        "enabled": True,
        "summary": summary,
        "reason_codes": [
            "manager_regime_validation_enabled",
            "manager_slice_quality_guarded",
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
    manager_regime_validation_view = _build_manager_regime_validation_guardrail_view(
        dict(promotion_gate.get("manager_regime_validation") or {}),
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
                "promotion_gate_manager_regime_validation_available",
                "promotion_gate_return_objectives_enabled",
                "promotion_gate_candidate_ab_enabled",
            ],
            "research_feedback": research_feedback_view,
            "regime_validation": regime_validation_view,
            "manager_regime_validation": manager_regime_validation_view,
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
        return safe_read_json_dict(path)

    def list_json_artifacts(self, directory: Path, *, limit: int = 20) -> dict[str, Any]:
        paths = list_json_artifact_paths(directory, limit=limit, default=20)
        items: list[dict[str, Any]] = []
        for path in paths:
            item: dict[str, Any] = {'path': str(path), 'name': path.name}
            try:
                payload = safe_read_json_dict(path)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
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
                            'core_artifacts': collect_core_explainability_artifacts(latest),
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
        payload['promotion_gate'] = normalize_promotion_gate_policy(
            dict(payload.get('promotion_gate') or {})
        )
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
        manager_scope: dict[str, Any] | None = None,
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
            'manager_scope': jsonable(dict(manager_scope or {})),
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
                'manager_scope': dict(plan.get('manager_scope') or {}),
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


def _feedback_sort_key(item: dict[str, Any]) -> tuple[str, int]:
    cutoff = str(item.get("cutoff_date") or "")
    try:
        cycle_id = int(item.get("cycle_id") or 0)
    except (TypeError, ValueError):
        cycle_id = 0
    return cutoff, cycle_id


def _latest_research_feedback(ok_results: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    latest_feedback: dict[str, Any] = {}
    latest_source: dict[str, Any] = {}
    latest_key = ("", 0)
    for item in ok_results:
        feedback = dict(item.get("research_feedback") or {})
        if not feedback:
            continue
        key = _feedback_sort_key(item)
        if key >= latest_key:
            latest_key = key
            latest_feedback = feedback
            latest_source = {
                "cycle_id": item.get("cycle_id"),
                "cutoff_date": item.get("cutoff_date"),
                "manager_id": item.get("manager_id"),
                "manager_config_ref": item.get("manager_config_ref") or item.get("runtime_config_ref"),
            }
    return latest_feedback, latest_source


def _latest_ab_comparison(ok_results: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    latest_comparison: dict[str, Any] = {}
    latest_source: dict[str, Any] = {}
    latest_key = ("", 0)
    for item in ok_results:
        comparison = dict(item.get("ab_comparison") or {})
        if not comparison:
            continue
        key = _feedback_sort_key(item)
        if key >= latest_key:
            latest_key = key
            latest_comparison = comparison
            latest_source = {
                "cycle_id": item.get("cycle_id"),
                "cutoff_date": item.get("cutoff_date"),
                "manager_id": item.get("manager_id"),
                "manager_config_ref": item.get("manager_config_ref") or item.get("runtime_config_ref"),
            }
    return latest_comparison, latest_source


def _research_feedback_brief(feedback: dict[str, Any], *, source: dict[str, Any] | None = None) -> dict[str, Any]:
    return TrainingFeedbackService.research_feedback_summary(feedback, source=source)


def _result_regime(item: dict[str, Any]) -> str:
    governance = dict(item.get("governance_decision") or {})
    audit_tags = dict(item.get("audit_tags") or {})
    self_assessment = dict(item.get("self_assessment") or {})
    return str(
        governance.get("regime")
        or audit_tags.get("governance_regime")
        or self_assessment.get("regime")
        or "unknown"
    ).strip() or "unknown"


def _result_manager_id(item: dict[str, Any]) -> str:
    return str(item.get("manager_id") or "unknown").strip() or "unknown"


def _result_manager_config_ref(item: dict[str, Any]) -> str:
    return str(
        item.get("manager_config_ref")
        or item.get("runtime_config_ref")
        or ""
    ).strip()


def build_return_profile(ok_results: list[dict[str, Any]], *, benchmark_pass_rate: float) -> dict[str, Any]:
    returns = [float(item.get("return_pct") or 0.0) for item in ok_results]
    if not returns:
        return {
            "sample_count": 0,
            "avg_return_pct": None,
            "median_return_pct": None,
            "cumulative_return_pct": None,
            "win_rate": None,
            "benchmark_pass_rate": benchmark_pass_rate,
            "positive_return_count": 0,
            "negative_return_count": 0,
            "flat_return_count": 0,
            "loss_share": None,
            "avg_gain_pct": None,
            "avg_loss_pct": None,
            "gain_loss_ratio": None,
            "max_return_pct": None,
            "min_return_pct": None,
        }

    positives = [value for value in returns if value > 0]
    negatives = [value for value in returns if value < 0]
    flat_count = sum(1 for value in returns if value == 0)
    avg_gain = round(sum(positives) / len(positives), 4) if positives else None
    avg_loss = round(sum(negatives) / len(negatives), 4) if negatives else None
    gain_loss_ratio = None
    if avg_gain is not None and avg_loss is not None and avg_loss != 0:
        gain_loss_ratio = round(avg_gain / abs(avg_loss), 4)
    return {
        "sample_count": len(returns),
        "avg_return_pct": round(sum(returns) / len(returns), 4),
        "median_return_pct": round(median(returns), 4),
        "cumulative_return_pct": round(sum(returns), 4),
        "win_rate": round(len(positives) / len(returns), 4),
        "benchmark_pass_rate": benchmark_pass_rate,
        "positive_return_count": len(positives),
        "negative_return_count": len(negatives),
        "flat_return_count": flat_count,
        "loss_share": round(len(negatives) / len(returns), 4),
        "avg_gain_pct": avg_gain,
        "avg_loss_pct": avg_loss,
        "gain_loss_ratio": gain_loss_ratio,
        "max_return_pct": round(max(returns), 4),
        "min_return_pct": round(min(returns), 4),
    }


def build_regime_validation_summary(ok_results: list[dict[str, Any]]) -> dict[str, Any]:
    if not ok_results:
        return {
            "sample_count": 0,
            "distinct_regime_count": 0,
            "dominant_regime": "",
            "dominant_regime_share": None,
            "regimes": {},
        }

    regime_groups: dict[str, list[dict[str, Any]]] = {}
    for item in ok_results:
        regime = _result_regime(item)
        regime_groups.setdefault(regime, []).append(item)

    total = len(ok_results)
    dominant_regime = max(regime_groups.items(), key=lambda pair: len(pair[1]))[0] if regime_groups else ""
    regimes: dict[str, dict[str, Any]] = {}
    for regime, items in sorted(regime_groups.items()):
        returns = [float(item.get("return_pct") or 0.0) for item in items]
        benchmark_hits = sum(1 for item in items if bool(item.get("benchmark_passed", False)))
        strategy_scores = [
            float((item.get("strategy_scores") or {}).get("overall_score", 0.0) or 0.0)
            for item in items
        ]
        win_count = sum(1 for value in returns if value > 0)
        regimes[regime] = {
            "sample_count": len(items),
            "share": round(len(items) / total, 4),
            "avg_return_pct": round(sum(returns) / len(returns), 4) if returns else None,
            "median_return_pct": round(median(returns), 4) if returns else None,
            "cumulative_return_pct": round(sum(returns), 4) if returns else None,
            "win_rate": round(win_count / len(items), 4) if items else None,
            "benchmark_pass_rate": round(benchmark_hits / len(items), 4) if items else None,
            "avg_strategy_score": round(sum(strategy_scores) / len(strategy_scores), 4) if strategy_scores else None,
            "max_return_pct": round(max(returns), 4) if returns else None,
            "min_return_pct": round(min(returns), 4) if returns else None,
        }
    return {
        "sample_count": total,
        "distinct_regime_count": len(regimes),
        "dominant_regime": dominant_regime,
        "dominant_regime_share": round(len(regime_groups.get(dominant_regime, [])) / total, 4) if total else None,
        "regimes": regimes,
    }


def build_manager_regime_breakdown_summary(ok_results: list[dict[str, Any]]) -> dict[str, Any]:
    if not ok_results:
        return {
            "sample_count": 0,
            "manager_count": 0,
            "managers": {},
        }

    manager_groups: dict[str, list[dict[str, Any]]] = {}
    for item in ok_results:
        manager_groups.setdefault(_result_manager_id(item), []).append(item)

    managers: dict[str, dict[str, Any]] = {}
    for manager_id, items in sorted(manager_groups.items()):
        returns = [float(item.get("return_pct") or 0.0) for item in items]
        benchmark_hits = sum(1 for item in items if bool(item.get("benchmark_passed", False)))
        strategy_scores = [
            float((item.get("strategy_scores") or {}).get("overall_score", 0.0) or 0.0)
            for item in items
        ]
        runtime_config_refs = sorted(
            {
                config_ref
                for config_ref in (
                    _result_manager_config_ref(item)
                    for item in items
                )
                if config_ref
            }
        )
        managers[manager_id] = {
            "manager_id": manager_id,
            "runtime_config_refs": runtime_config_refs,
            "sample_count": len(items),
            "avg_return_pct": round(sum(returns) / len(returns), 4) if returns else None,
            "benchmark_pass_rate": round(benchmark_hits / len(items), 4) if items else None,
            "avg_strategy_score": round(sum(strategy_scores) / len(strategy_scores), 4) if strategy_scores else None,
            "regime_validation": build_regime_validation_summary(items),
        }

    return {
        "sample_count": len(ok_results),
        "manager_count": len(managers),
        "managers": managers,
    }


def _extend_gate_checks(
    checks: list[dict[str, Any]],
    prefix: str,
    gate_checks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in gate_checks:
        normalized_item = {
            "name": f"{prefix}.{item.get('name')}",
            "passed": bool(item.get("passed", False)),
            "actual": item.get("actual"),
            "threshold": item.get("threshold"),
            "meta": {k: v for k, v in item.items() if k not in {"name", "passed", "actual", "threshold"}},
        }
        checks.append(normalized_item)
        normalized.append(normalized_item)
    return normalized


def evaluate_return_objectives(
    return_profile: dict[str, Any],
    *,
    policy: dict[str, Any] | None,
    baseline_avg_return: float | None = None,
) -> dict[str, Any]:
    config = dict(policy or {})
    if not config:
        return {"enabled": False, "passed": True, "checks": [], "failed_checks": [], "profile": return_profile}

    checks: list[dict[str, Any]] = []

    def add(name: str, passed: bool, actual: Any, threshold: Any) -> None:
        checks.append({"name": name, "passed": bool(passed), "actual": actual, "threshold": threshold})

    for key in ("min_avg_return_pct", "min_median_return_pct", "min_cumulative_return_pct", "min_win_rate", "min_benchmark_pass_rate"):
        if config.get(key) is None:
            continue
        metric_key = key.removeprefix("min_")
        actual = return_profile.get(metric_key)
        threshold = float(config.get(key) or 0.0)
        add(key, actual is not None and float(actual) >= threshold, actual, threshold)
    if config.get("max_loss_share") is not None:
        actual = return_profile.get("loss_share")
        threshold = float(config.get("max_loss_share") or 0.0)
        add("max_loss_share", actual is not None and float(actual) <= threshold, actual, threshold)
    if config.get("min_gain_loss_ratio") is not None:
        actual = return_profile.get("gain_loss_ratio")
        threshold = float(config.get("min_gain_loss_ratio") or 0.0)
        add("min_gain_loss_ratio", actual is not None and float(actual) >= threshold, actual, threshold)
    if config.get("min_return_advantage_vs_baseline") is not None and baseline_avg_return is not None:
        actual_avg = return_profile.get("avg_return_pct")
        actual = round(float(actual_avg) - float(baseline_avg_return), 4) if actual_avg is not None else None
        threshold = float(config.get("min_return_advantage_vs_baseline") or 0.0)
        add("min_return_advantage_vs_baseline", actual is not None and actual >= threshold, actual, threshold)

    failed_checks = [item for item in checks if not item.get("passed", False)]
    return {
        "enabled": True,
        "passed": not failed_checks,
        "checks": checks,
        "failed_checks": failed_checks,
        "profile": return_profile,
    }


def evaluate_regime_validation(
    regime_validation: dict[str, Any],
    *,
    policy: dict[str, Any] | None,
) -> dict[str, Any]:
    config = dict(policy or {})
    if not config:
        return {
            "enabled": False,
            "passed": True,
            "checks": [],
            "failed_checks": [],
            "summary": regime_validation,
        }

    checks: list[dict[str, Any]] = []
    distinct_regimes = int(regime_validation.get("distinct_regime_count") or 0)
    regimes = dict(regime_validation.get("regimes") or {})
    min_distinct_regimes = int(config.get("min_distinct_regimes") or 0)
    min_samples_per_regime = int(config.get("min_samples_per_regime") or 0)

    if min_distinct_regimes:
        checks.append(
            {
                "name": "min_distinct_regimes",
                "passed": distinct_regimes >= min_distinct_regimes,
                "actual": distinct_regimes,
                "threshold": min_distinct_regimes,
            }
        )
    if config.get("max_dominant_regime_share") is not None:
        actual = regime_validation.get("dominant_regime_share")
        threshold = float(config.get("max_dominant_regime_share") or 0.0)
        checks.append(
            {
                "name": "max_dominant_regime_share",
                "passed": actual is not None and float(actual) <= threshold,
                "actual": actual,
                "threshold": threshold,
            }
        )

    for regime_name, summary in sorted(regimes.items()):
        sample_count = int(summary.get("sample_count") or 0)
        if min_samples_per_regime:
            checks.append(
                {
                    "name": f"{regime_name}.sample_count",
                    "passed": sample_count >= min_samples_per_regime,
                    "actual": sample_count,
                    "threshold": min_samples_per_regime,
                }
            )
        if sample_count < max(1, min_samples_per_regime):
            continue
        for metric_name, config_key in (
            ("avg_return_pct", "min_avg_return_pct"),
            ("win_rate", "min_win_rate"),
            ("benchmark_pass_rate", "min_benchmark_pass_rate"),
        ):
            if config.get(config_key) is None:
                continue
            actual = summary.get(metric_name)
            threshold = float(config.get(config_key) or 0.0)
            checks.append(
                {
                    "name": f"{regime_name}.{metric_name}",
                    "passed": actual is not None and float(actual) >= threshold,
                    "actual": actual,
                    "threshold": threshold,
                }
            )

    failed_checks = [item for item in checks if not item.get("passed", False)]
    return {
        "enabled": True,
        "passed": not failed_checks,
        "checks": checks,
        "failed_checks": failed_checks,
        "summary": regime_validation,
    }


def evaluate_manager_regime_validation(
    manager_regime_breakdown: dict[str, Any],
    *,
    policy: dict[str, Any] | None,
) -> dict[str, Any]:
    config = dict(policy or {})
    if not config or not bool(config.get("enabled", False)):
        return {
            "enabled": False,
            "passed": True,
            "checks": [],
            "failed_checks": [],
            "summary": manager_regime_breakdown,
        }

    checks: list[dict[str, Any]] = []
    manager_count = int(manager_regime_breakdown.get("manager_count") or 0)
    managers = dict(manager_regime_breakdown.get("managers") or {})
    min_manager_count = int(config.get("min_manager_count") or 0)
    min_samples_per_manager = int(config.get("min_samples_per_manager") or 0)
    per_manager_policy = {
        key: value
        for key, value in config.items()
        if key not in {"enabled", "min_manager_count", "min_samples_per_manager"}
    }

    if min_manager_count:
        checks.append(
            {
                "name": "min_manager_count",
                "passed": manager_count >= min_manager_count,
                "actual": manager_count,
                "threshold": min_manager_count,
            }
        )

    for manager_id, summary in sorted(managers.items()):
        sample_count = int(summary.get("sample_count") or 0)
        if min_samples_per_manager:
            checks.append(
                {
                    "name": f"{manager_id}.sample_count",
                    "passed": sample_count >= min_samples_per_manager,
                    "actual": sample_count,
                    "threshold": min_samples_per_manager,
                }
            )
        if sample_count < max(1, min_samples_per_manager):
            continue
        manager_validation = evaluate_regime_validation(
            dict(summary.get("regime_validation") or {}),
            policy=per_manager_policy,
        )
        for item in list(manager_validation.get("checks") or []):
            checks.append(
                {
                    "name": f"{manager_id}.{item.get('name')}",
                    "passed": bool(item.get("passed", False)),
                    "actual": item.get("actual"),
                    "threshold": item.get("threshold"),
                }
            )

    failed_checks = [item for item in checks if not item.get("passed", False)]
    return {
        "enabled": True,
        "passed": not failed_checks,
        "checks": checks,
        "failed_checks": failed_checks,
        "summary": manager_regime_breakdown,
    }


def evaluate_candidate_ab(
    ab_comparison: dict[str, Any],
    *,
    policy: dict[str, Any] | None,
) -> dict[str, Any]:
    config = dict(policy or {})
    if not config:
        return {
            "enabled": False,
            "passed": True,
            "checks": [],
            "failed_checks": [],
            "summary": ab_comparison,
        }

    if not ab_comparison:
        return {
            "enabled": True,
            "passed": True,
            "checks": [],
            "failed_checks": [],
            "summary": {},
            "skipped": True,
        }

    checks: list[dict[str, Any]] = []
    comparison = dict(ab_comparison.get("comparison") or {})
    candidate_present = bool(comparison.get("candidate_present", True))
    comparable = bool(comparison.get("comparable", False))
    required_when_candidate_present = bool(config.get("required_when_candidate_present", True))
    if candidate_present and required_when_candidate_present:
        checks.append(
            {
                "name": "available",
                "passed": comparable,
                "actual": int(comparable),
                "threshold": 1,
            }
        )
    if comparable:
        for check_name, metric_name in (
            ("min_return_lift_pct", "return_lift_pct"),
            ("min_strategy_score_lift", "strategy_score_lift"),
            ("min_benchmark_lift", "benchmark_lift"),
            ("min_win_rate_lift", "win_rate_lift"),
        ):
            if config.get(check_name) is None:
                continue
            actual = comparison.get(metric_name)
            threshold = float(config.get(check_name) or 0.0)
            checks.append(
                {
                    "name": check_name,
                    "passed": actual is not None and float(actual) >= threshold,
                    "actual": actual,
                    "threshold": threshold,
                }
            )
        if config.get("require_candidate_outperform_active", True):
            actual = bool(comparison.get("candidate_outperformed", False))
            checks.append(
                {
                    "name": "require_candidate_outperform_active",
                    "passed": actual,
                    "actual": int(actual),
                    "threshold": 1,
                }
            )

    failed_checks = [item for item in checks if not item.get("passed", False)]
    return {
        "enabled": True,
        "passed": not failed_checks,
        "checks": checks,
        "failed_checks": failed_checks,
        "summary": ab_comparison,
        "skipped": False,
    }


def build_promotion_summary(*, plan: dict[str, Any], ok_results: list[dict[str, Any]], avg_return_pct: float | None, avg_strategy_score: float | None, benchmark_pass_rate: float, baseline_entries: list[dict[str, Any]]) -> dict[str, Any]:
    gate = dict((plan.get("optimization") or {}).get("promotion_gate") or {})
    manager_scope = dict(plan.get("manager_scope") or {})
    protocol = dict(plan.get("protocol") or {})
    holdout = protocol.get("holdout") if isinstance(protocol.get("holdout"), dict) else {}
    walk_forward = protocol.get("walk_forward") if isinstance(protocol.get("walk_forward"), dict) else {}
    candidate_manager_id = "unknown"
    candidate_manager_config_ref = ""
    if ok_results:
        first = ok_results[0]
        candidate_manager_id = str(first.get("manager_id") or "unknown")
        candidate_manager_config_ref = str(
            first.get("manager_config_ref") or first.get("runtime_config_ref") or ""
        )
    baseline_manager_ids = [str(x) for x in (manager_scope.get("baseline_manager_ids") or []) if str(x).strip()]
    baseline_summary_entries = [
        {
            "manager_id": str(entry.get("manager_id") or "").strip(),
            "manager_config_ref": str(entry.get("manager_config_ref") or entry.get("runtime_config_ref") or "").strip(),
            "avg_return_pct": entry.get("avg_return_pct"),
            "avg_strategy_score": entry.get("avg_strategy_score"),
            "score": entry.get("score"),
            "rank": entry.get("rank"),
        }
        for entry in baseline_entries
    ]
    baseline_avg_return = round(sum(float(entry.get("avg_return_pct", 0.0) or 0.0) for entry in baseline_entries) / len(baseline_entries), 4) if baseline_entries else None
    baseline_avg_score = round(sum(float(entry.get("avg_strategy_score", 0.0) or 0.0) for entry in baseline_entries) / len(baseline_entries), 4) if baseline_entries else None
    return_profile = build_return_profile(ok_results, benchmark_pass_rate=benchmark_pass_rate)
    regime_validation = build_regime_validation_summary(ok_results)
    manager_regime_breakdown = build_manager_regime_breakdown_summary(ok_results)
    checks: list[dict[str, Any]] = []

    def add_check(name: str, passed: bool, actual: Any, threshold: Any) -> None:
        checks.append({"name": name, "passed": bool(passed), "actual": actual, "threshold": threshold})

    min_samples = int(gate.get("min_samples", 1) or 1)
    add_check("min_samples", len(ok_results) >= min_samples, len(ok_results), min_samples)
    if gate.get("min_avg_return_pct") is not None:
        threshold = float(gate.get("min_avg_return_pct") or 0.0)
        actual = avg_return_pct if avg_return_pct is not None else None
        add_check("min_avg_return_pct", actual is not None and actual >= threshold, actual, threshold)
    if gate.get("min_avg_strategy_score") is not None:
        threshold = float(gate.get("min_avg_strategy_score") or 0.0)
        actual = avg_strategy_score if avg_strategy_score is not None else None
        add_check("min_avg_strategy_score", actual is not None and actual >= threshold, actual, threshold)
    if gate.get("min_benchmark_pass_rate") is not None:
        threshold = float(gate.get("min_benchmark_pass_rate") or 0.0)
        add_check("min_benchmark_pass_rate", benchmark_pass_rate >= threshold, benchmark_pass_rate, threshold)
    if gate.get("min_return_advantage_vs_baseline") is not None and baseline_avg_return is not None and avg_return_pct is not None:
        threshold = float(gate.get("min_return_advantage_vs_baseline") or 0.0)
        actual = round(avg_return_pct - baseline_avg_return, 4)
        add_check("min_return_advantage_vs_baseline", actual >= threshold, actual, threshold)
    if gate.get("min_strategy_score_advantage_vs_baseline") is not None and baseline_avg_score is not None and avg_strategy_score is not None:
        threshold = float(gate.get("min_strategy_score_advantage_vs_baseline") or 0.0)
        actual = round(avg_strategy_score - baseline_avg_score, 4)
        add_check("min_strategy_score_advantage_vs_baseline", actual >= threshold, actual, threshold)

    return_objectives = evaluate_return_objectives(
        return_profile,
        policy=dict(gate.get("return_objectives") or {}),
        baseline_avg_return=baseline_avg_return,
    )
    normalized_return_checks = _extend_gate_checks(
        checks,
        "return_objectives",
        list(return_objectives.get("checks") or []),
    )
    return_objectives = {
        **return_objectives,
        "checks": normalized_return_checks,
        "failed_checks": [item for item in normalized_return_checks if not item.get("passed", False)],
    }

    regime_validation_gate = evaluate_regime_validation(
        regime_validation,
        policy=dict(gate.get("regime_validation") or {}),
    )
    normalized_regime_checks = _extend_gate_checks(
        checks,
        "regime_validation",
        list(regime_validation_gate.get("checks") or []),
    )
    regime_validation_gate = {
        **regime_validation_gate,
        "checks": normalized_regime_checks,
        "failed_checks": [item for item in normalized_regime_checks if not item.get("passed", False)],
    }

    manager_regime_validation_gate = evaluate_manager_regime_validation(
        manager_regime_breakdown,
        policy=dict(gate.get("manager_regime_validation") or {}),
    )
    normalized_manager_regime_checks = _extend_gate_checks(
        checks,
        "manager_regime_validation",
        list(manager_regime_validation_gate.get("checks") or []),
    )
    manager_regime_validation_gate = {
        **manager_regime_validation_gate,
        "checks": normalized_manager_regime_checks,
        "failed_checks": [
            item for item in normalized_manager_regime_checks if not item.get("passed", False)
        ],
    }

    latest_ab_comparison, ab_source = _latest_ab_comparison(ok_results)
    candidate_ab_gate = evaluate_candidate_ab(
        latest_ab_comparison,
        policy=dict(gate.get("candidate_ab") or {}),
    )
    normalized_ab_checks = _extend_gate_checks(
        checks,
        "candidate_ab",
        list(candidate_ab_gate.get("checks") or []),
    )
    candidate_ab_gate = {
        **candidate_ab_gate,
        "source": ab_source,
        "checks": normalized_ab_checks,
        "failed_checks": [item for item in normalized_ab_checks if not item.get("passed", False)],
    }

    latest_feedback, feedback_source = _latest_research_feedback(ok_results)
    research_gate_policy = dict(gate.get("research_feedback") or {})
    research_feedback_summary = _research_feedback_brief(latest_feedback, source=feedback_source)
    promotion_feedback_gate: dict[str, Any] = {
        "enabled": bool(research_gate_policy),
        "passed": True,
        "checks": [],
        "latest_feedback": research_feedback_summary,
    }
    if research_gate_policy:
        if not latest_feedback:
            availability_check = {
                "name": "research_feedback.available",
                "passed": False,
                "actual": 0,
                "threshold": 1,
            }
            checks.append(availability_check)
            promotion_feedback_gate = {
                **promotion_feedback_gate,
                "passed": False,
                "checks": [availability_check],
                "failed_checks": [availability_check],
            }
        else:
            evaluation = evaluate_research_feedback_gate(
                latest_feedback,
                policy=research_gate_policy,
                defaults=research_gate_policy,
            )
            gate_checks: list[dict[str, Any]] = []
            for item in list(evaluation.get("checks") or []):
                gate_check = {
                    "name": f"research_feedback.{item.get('name')}",
                    "passed": bool(item.get("passed", False)),
                    "actual": item.get("actual"),
                    "threshold": item.get("required_gte", item.get("required_lte", item.get("blocked"))),
                    "meta": {k: v for k, v in item.items() if k not in {"name", "passed", "actual", "required_gte", "required_lte", "blocked"}},
                }
                gate_checks.append(gate_check)
                checks.append(gate_check)
            promotion_feedback_gate = {
                **evaluation,
                "enabled": True,
                "latest_feedback": research_feedback_summary,
                "checks": gate_checks,
                "failed_checks": [item for item in gate_checks if not item.get("passed", False)],
                "passed": all(item.get("passed", False) for item in gate_checks) if gate_checks else False,
            }

    passed = all(item.get("passed", False) for item in checks) if checks else False
    verdict = "promoted" if passed else "rejected"
    if not ok_results:
        verdict = "insufficient_data"
    return {
        "candidate": {
            "manager_id": candidate_manager_id,
            "manager_config_ref": candidate_manager_config_ref,
        },
        "baselines": {
            "manager_ids": baseline_manager_ids,
            "entries": baseline_summary_entries,
            "avg_return_pct": baseline_avg_return,
            "avg_strategy_score": baseline_avg_score,
            "sample_count": len(baseline_entries),
        },
        "gate": gate,
        "checks": checks,
        "return_profile": return_profile,
        "return_objectives": return_objectives,
        "regime_validation": regime_validation_gate,
        "manager_regime_validation": manager_regime_validation_gate,
        "candidate_ab": candidate_ab_gate,
        "research_feedback": promotion_feedback_gate,
        "verdict": verdict,
        "passed": passed,
        "protocol": {"holdout": holdout, "walk_forward": walk_forward},
    }


def build_training_evaluation_summary(*, payload: dict[str, Any], plan: dict[str, Any], run_id: str, error: str, promotion: dict[str, Any], run_path: str, evaluation_path: str) -> dict[str, Any]:
    results = list(payload.get("results") or [])
    ok_results = [item for item in results if item.get("status") == "ok"]
    no_data_results = [item for item in results if item.get("status") == "no_data"]
    error_results = [item for item in results if item.get("status") == "error"]
    returns = [float(item.get("return_pct") or 0.0) for item in ok_results]
    strategy_scores = [float((item.get("strategy_scores") or {}).get("overall_score", 0.0) or 0.0) for item in ok_results]
    benchmark_passes = sum(1 for item in ok_results if bool(item.get("benchmark_passed", False)))
    avg_return_pct = round(sum(returns) / len(returns), 4) if returns else None
    avg_strategy_score = round(sum(strategy_scores) / len(strategy_scores), 4) if strategy_scores else None
    benchmark_pass_rate = round(benchmark_passes / len(ok_results), 4) if ok_results else 0.0
    return_profile = build_return_profile(ok_results, benchmark_pass_rate=benchmark_pass_rate)
    regime_validation = build_regime_validation_summary(ok_results)
    manager_regime_breakdown = build_manager_regime_breakdown_summary(ok_results)
    latest_ab_comparison, _ = _latest_ab_comparison(ok_results)
    latest_result = dict(results[-1]) if results else {}
    latest_result_summary = {
        "cycle_id": latest_result.get("cycle_id"),
        "status": str(latest_result.get("status") or ""),
        "return_pct": latest_result.get("return_pct"),
        "benchmark_passed": bool(latest_result.get("benchmark_passed", False)),
        "core_artifacts": collect_core_explainability_artifacts(latest_result),
        "promotion_record": dict(latest_result.get("promotion_record") or {}),
        "lineage_record": dict(latest_result.get("lineage_record") or {}),
    }
    governance_metrics = build_governance_metrics(results)
    realism_summary = build_realism_summary(results)
    return {
        "run_id": run_id,
        "plan_id": plan["plan_id"],
        "created_at": datetime.now().isoformat(),
        "status": str(payload.get("status", "ok")),
        "objective": dict(plan.get("objective") or {}),
        "spec": dict(plan.get("spec") or {}),
        "protocol": dict(plan.get("protocol") or {}),
        "dataset": dict(plan.get("dataset") or {}),
        "manager_scope": dict(plan.get("manager_scope") or {}),
        "optimization": dict(plan.get("optimization") or {}),
        "guardrails": dict(plan.get("guardrails") or {}),
        "llm": dict(plan.get("llm") or {}),
        "assessment": {
            "total_results": len(results),
            "success_count": len(ok_results),
            "no_data_count": len(no_data_results),
            "error_count": len(error_results),
            "avg_return_pct": avg_return_pct,
            "max_return_pct": round(max(returns), 4) if returns else None,
            "min_return_pct": round(min(returns), 4) if returns else None,
            "avg_strategy_score": avg_strategy_score,
            "benchmark_pass_rate": benchmark_pass_rate,
            "return_profile": return_profile,
            "regime_validation": regime_validation,
            "manager_regime_breakdown": manager_regime_breakdown,
            "latest_ab_comparison": latest_ab_comparison,
            "latest_result": latest_result_summary,
        },
        "promotion": promotion,
        "governance_metrics": governance_metrics,
        "realism_summary": realism_summary,
        "error": str(error or ""),
        "artifacts": {"run_path": run_path, "evaluation_path": evaluation_path},
    }


def build_training_memory_summary(*, payload: dict[str, Any], rounds: int, mock: bool, status: str, error: str = "") -> dict[str, Any]:
    results = list(payload.get("results") or [])
    ok_results = [item for item in results if item.get("status") == "ok"]
    skipped_results = [item for item in results if item.get("status") == "no_data"]
    error_results = [item for item in results if item.get("status") == "error"]
    cycle_ids = [item.get("cycle_id") for item in results if item.get("cycle_id") is not None]
    avg_return = round(sum(float(item.get("return_pct") or 0.0) for item in ok_results) / len(ok_results), 2) if ok_results else None
    requested_modes = sorted({str(item.get("requested_data_mode")) for item in results if item.get("requested_data_mode")})
    effective_modes = sorted({str(item.get("effective_data_mode") or item.get("data_mode")) for item in results if (item.get("effective_data_mode") or item.get("data_mode"))})
    llm_modes = sorted({str(item.get("llm_mode")) for item in results if item.get("llm_mode")})
    degraded_count = sum(1 for item in results if bool(item.get("degraded", False)))
    return {
        "status": status,
        "rounds": int(rounds),
        "mock": bool(mock),
        "cycle_ids": cycle_ids,
        "success_count": len(ok_results),
        "skipped_count": len(skipped_results),
        "error_count": len(error_results),
        "avg_return_pct": avg_return,
        "requested_data_modes": requested_modes,
        "effective_data_modes": effective_modes,
        "llm_modes": llm_modes,
        "degraded_count": degraded_count,
        "error": str(error or ""),
    }
