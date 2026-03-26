from __future__ import annotations

import json
from collections import defaultdict
import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List

import yaml

from invest.shared.model_governance import (
    evaluate_routing_quality_gate,
    infer_deployment_stage,
    normalize_freeze_gate_policy,
    normalize_promotion_gate_policy,
    resolve_model_governance_matrix,
)
from invest.shared.model_regime import get_model_regime_profile, regime_compatibility

logger = logging.getLogger(__name__)

DEFAULT_LEADERBOARD_POLICY: Dict[str, Any] = {
    "min_cycles": 3,
    "min_cycles_per_regime": 2,
}

EXCLUDED_CYCLE_DIR_NAMES = {
    "config_snapshots",
    "control_plane_snapshots",
}


def _is_excluded_cycle_path(path: Path) -> bool:
    if path.name.endswith("_config_snapshot.json"):
        return True
    if any(part in EXCLUDED_CYCLE_DIR_NAMES for part in path.parts):
        return True
    parts = path.parts
    for index in range(len(parts) - 1):
        if parts[index] == "state" and parts[index + 1] == "snapshots":
            return True
    return False


def _infer_model_name(payload: Dict[str, Any], path: Path) -> str:
    candidates = [
        str(payload.get("model_name") or ""),
        str(payload.get("config_name") or ""),
        str(payload.get("config_snapshot_path") or ""),
        str(path),
        str(path.parent),
    ]
    params = dict(payload.get("params") or {})
    if "min_defensive_score" in params or "max_volatility" in params:
        return "defensive_low_vol"
    if "min_value_quality_score" in params or "max_pe_ttm" in params or "min_roe" in params:
        return "value_quality"
    if "min_reversion_score" in params or "oversold_rsi" in params or "max_5d_drop" in params:
        return "mean_reversion"
    if any(key in params for key in ("signal_threshold", "ma_short", "ma_long")):
        inferred = "momentum"
    else:
        inferred = "unknown"
    haystack = " ".join(candidates).lower()
    for name in ("defensive_low_vol", "value_quality", "mean_reversion", "momentum"):
        if name in haystack:
            return name
    return inferred


def _normalize_config_name(payload: Dict[str, Any], path: Path, model_name: str) -> str:
    raw = str(payload.get("config_name") or "").strip()
    if raw.endswith('.yaml'):
        return Path(raw).stem
    if 'config_snapshots' in raw:
        return f"{model_name}_runtime"
    if raw and raw != 'unknown':
        return raw
    run_name = path.parent.name.strip()
    if run_name:
        return run_name
    return f"{model_name}_default"


def load_cycle_record(path: str | Path) -> Dict[str, Any]:
    path = Path(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["_path"] = str(path)
    payload["_dir"] = str(path.parent)
    model_name = _infer_model_name(payload, path)
    payload["model_name"] = model_name
    payload["config_name"] = _normalize_config_name(payload, path, model_name)
    payload["regime"] = str((payload.get("self_assessment") or {}).get("regime") or payload.get("regime") or "unknown")
    return payload


def collect_cycle_records(root_dir: str | Path) -> List[Dict[str, Any]]:
    root_path = Path(root_dir)
    if not root_path.exists():
        return []
    records: List[Dict[str, Any]] = []
    for path in sorted(root_path.rglob("cycle_*.json")):
        if not (path.name.startswith("cycle_") and path.name.endswith(".json")):
            continue
        if _is_excluded_cycle_path(path):
            continue
        try:
            records.append(load_cycle_record(path))
        except Exception as exc:
            logger.warning("Skipped invalid cycle record %s: %s", path, exc)
            continue
    return records


def _safe_avg(values: Iterable[float]) -> float:
    data = list(values)
    return float(sum(data) / len(data)) if data else 0.0


def _resolved_train_policy_payload(
    *,
    train_policy: Dict[str, Any] | None = None,
    quality_gate_matrix: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    payload = dict(train_policy or {})
    return {
        "promotion_gate": normalize_promotion_gate_policy(
            dict(payload.get("promotion_gate") or {})
        ),
        "freeze_gate": normalize_freeze_gate_policy(
            dict(payload.get("freeze_gate") or {})
        ),
        "quality_gate_matrix": resolve_model_governance_matrix(
            dict(
                quality_gate_matrix
                or payload.get("quality_gate_matrix")
                or {}
            )
        ),
    }


def _load_train_policy_from_config_ref(config_ref: str) -> Dict[str, Any] | None:
    text = str(config_ref or "").strip()
    if not text:
        return None
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = path.resolve()
    if not path.exists() or path.suffix.lower() not in {".yaml", ".yml"}:
        return None
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        logger.debug("Failed to load train policy from %s: %s", path, exc)
        return None
    if not isinstance(payload, dict):
        return None
    train_policy = payload.get("train") or {}
    return dict(train_policy) if isinstance(train_policy, dict) else {}


def _resolve_runtime_train_policy(
    *,
    records: List[Dict[str, Any]],
    resolved_policy: Dict[str, Any],
    governance_matrix: Dict[str, Any],
) -> Dict[str, Any]:
    override_train = dict(resolved_policy.get("train") or {})
    if override_train:
        return _resolved_train_policy_payload(
            train_policy=override_train,
            quality_gate_matrix=dict(
                resolved_policy.get("quality_gate_matrix")
                or override_train.get("quality_gate_matrix")
                or governance_matrix
                or {}
            ),
        )
    if not records:
        return _resolved_train_policy_payload(
            train_policy={},
            quality_gate_matrix=governance_matrix,
        )

    latest_record = max(
        records,
        key=lambda item: (
            int(item.get("cycle_id", 0) or 0),
            str(item.get("_path", "")),
        ),
    )
    run_context = dict(latest_record.get("run_context") or {})
    resolved_train_policy = dict(run_context.get("resolved_train_policy") or {})
    if resolved_train_policy:
        return _resolved_train_policy_payload(
            train_policy=resolved_train_policy,
            quality_gate_matrix=dict(
                resolved_train_policy.get("quality_gate_matrix")
                or run_context.get("quality_gate_matrix")
                or governance_matrix
                or {}
            ),
        )

    lineage_record = dict(latest_record.get("lineage_record") or {})
    for config_ref in (
        lineage_record.get("active_config_ref"),
        run_context.get("active_config_ref"),
        lineage_record.get("candidate_config_ref"),
        run_context.get("candidate_config_ref"),
        latest_record.get("config_name"),
    ):
        train_policy = _load_train_policy_from_config_ref(str(config_ref or ""))
        if train_policy is not None:
            return _resolved_train_policy_payload(
                train_policy=train_policy,
                quality_gate_matrix=dict(
                    train_policy.get("quality_gate_matrix")
                    or run_context.get("quality_gate_matrix")
                    or governance_matrix
                    or {}
                ),
            )

    return _resolved_train_policy_payload(
        train_policy={},
        quality_gate_matrix=dict(
            run_context.get("quality_gate_matrix")
            or governance_matrix
            or {}
        ),
    )


def _entry_key(record: Dict[str, Any]) -> str:
    return f"{record.get('model_name', 'unknown')}::{record.get('config_name', 'unknown')}"


def _extract_scoring_change_summary(item: Dict[str, Any]) -> Dict[str, Any]:
    events = list(item.get("optimization_events") or [])
    scoring_events = []
    changed_keys = set()
    for event in events:
        applied = dict(event.get("applied_change") or {})
        scoring = dict(applied.get("scoring") or {})
        if not scoring:
            continue
        scoring_events.append(scoring)
        for section_name, section_values in scoring.items():
            if isinstance(section_values, dict):
                for key in section_values.keys():
                    changed_keys.add(f"{section_name}.{key}")
    return {
        "scoring_mutation_count": len(scoring_events),
        "scoring_changed_keys": sorted(changed_keys),
    }


def _eligibility_for_entry(
    *,
    cycle_count: int,
    dominant_regime: str,
    regimes: Dict[str, int],
    policy: Dict[str, Any],
) -> tuple[bool, str, Dict[str, Any]]:
    min_cycles = max(1, int(policy.get("min_cycles", 1) or 1))
    min_cycles_per_regime = max(1, int(policy.get("min_cycles_per_regime", 1) or 1))
    dominant_regime_cycles = int(regimes.get(dominant_regime, 0) or 0)
    if cycle_count < min_cycles:
        return False, "min_cycles", {
            "min_cycles": min_cycles,
            "observed_cycles": cycle_count,
            "min_cycles_per_regime": min_cycles_per_regime,
            "dominant_regime": dominant_regime,
            "dominant_regime_cycles": dominant_regime_cycles,
        }
    if dominant_regime_cycles < min_cycles_per_regime:
        return False, "min_regime_cycles", {
            "min_cycles": min_cycles,
            "observed_cycles": cycle_count,
            "min_cycles_per_regime": min_cycles_per_regime,
            "dominant_regime": dominant_regime,
            "dominant_regime_cycles": dominant_regime_cycles,
        }
    return True, "", {
        "min_cycles": min_cycles,
        "observed_cycles": cycle_count,
        "min_cycles_per_regime": min_cycles_per_regime,
        "dominant_regime": dominant_regime,
        "dominant_regime_cycles": dominant_regime_cycles,
    }


def _build_regime_performance(
    items: List[Dict[str, Any]],
    *,
    model_name: str,
) -> Dict[str, Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for item in items:
        grouped[str(item.get("regime") or "unknown")].append(item)

    performance: Dict[str, Dict[str, Any]] = {}
    for regime_name, regime_items in grouped.items():
        returns = [float(item.get("return_pct", 0.0) or 0.0) for item in regime_items]
        sharpes = [
            float((item.get("self_assessment") or {}).get("sharpe_ratio", 0.0) or 0.0)
            for item in regime_items
        ]
        drawdowns = [
            float((item.get("self_assessment") or {}).get("max_drawdown", 0.0) or 0.0)
            for item in regime_items
        ]
        strategy_scores = [
            float(
                (item.get("self_assessment") or {}).get(
                    "overall_score",
                    (item.get("strategy_scores") or {}).get("overall_score", 0.0),
                )
                or 0.0
            )
            for item in regime_items
        ]
        benchmark_pass_rate = (
            sum(1 for item in regime_items if bool(item.get("benchmark_passed", False))) / len(regime_items)
            if regime_items
            else 0.0
        )
        win_rate = (
            sum(1 for item in regime_items if bool(item.get("is_profit", False))) / len(regime_items)
            if regime_items
            else 0.0
        )
        regime_score = (
            _safe_avg(returns) * 0.35
            + _safe_avg(sharpes) * 9.0
            + _safe_avg(strategy_scores) * 12.0
            + benchmark_pass_rate * 15.0
            - _safe_avg(drawdowns) * 0.40
        ) * max(0.25, regime_compatibility(model_name, regime_name))
        performance[regime_name] = {
            "cycles": len(regime_items),
            "avg_return_pct": round(_safe_avg(returns), 6),
            "avg_sharpe_ratio": round(_safe_avg(sharpes), 6),
            "avg_max_drawdown": round(_safe_avg(drawdowns), 6),
            "avg_strategy_score": round(_safe_avg(strategy_scores), 6),
            "benchmark_pass_rate": round(benchmark_pass_rate, 6),
            "win_rate": round(win_rate, 6),
            "score": round(regime_score, 6),
            "compatibility": regime_compatibility(model_name, regime_name),
        }
    return performance


def build_leaderboard(records: List[Dict[str, Any]], policy: Dict[str, Any] | None = None) -> Dict[str, Any]:
    resolved_policy = dict(DEFAULT_LEADERBOARD_POLICY)
    resolved_policy.update(dict(policy or {}))
    governance_matrix = resolve_model_governance_matrix(
        dict(
            resolved_policy.get("quality_gate_matrix")
            or dict(resolved_policy.get("train") or {}).get("quality_gate_matrix")
            or {}
        )
    )
    policy_payload = dict(resolved_policy)
    policy_payload["train"] = _resolve_runtime_train_policy(
        records=records,
        resolved_policy=resolved_policy,
        governance_matrix=governance_matrix,
    )
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[_entry_key(record)].append(record)

    entries: List[Dict[str, Any]] = []
    regime_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for key, items in grouped.items():
        items = sorted(items, key=lambda item: int(item.get("cycle_id", 0)))
        returns = [float(item.get("return_pct", 0.0) or 0.0) for item in items]
        sharpes = [float((item.get("self_assessment") or {}).get("sharpe_ratio", 0.0) or 0.0) for item in items]
        drawdowns = [float((item.get("self_assessment") or {}).get("max_drawdown", 0.0) or 0.0) for item in items]
        excess_returns = [float((item.get("self_assessment") or {}).get("excess_return", 0.0) or 0.0) for item in items]
        strategy_scores = [float((item.get("self_assessment") or {}).get("overall_score", (item.get("strategy_scores") or {}).get("overall_score", 0.0)) or 0.0) for item in items]
        wins = sum(1 for item in items if bool(item.get("is_profit", False)))
        benchmark_passes = sum(1 for item in items if bool(item.get("benchmark_passed", False)))
        regimes: Dict[str, int] = defaultdict(int)
        for item in items:
            regimes[str(item.get("regime", "unknown"))] += 1
        dominant_regime = max(regimes.items(), key=lambda pair: pair[1])[0] if regimes else "unknown"
        eligible_for_routing, ineligible_reason, sample_gate = _eligibility_for_entry(
            cycle_count=len(items),
            dominant_regime=dominant_regime,
            regimes=regimes,
            policy=resolved_policy,
        )
        latest_item = items[-1]
        latest_run_context = dict(latest_item.get("run_context") or {})
        latest_lineage_record = dict(latest_item.get("lineage_record") or {})
        latest_promotion_record = dict(latest_item.get("promotion_record") or {})
        governance_stage = infer_deployment_stage(
            run_context=latest_run_context,
            optimization_events=list(latest_item.get("optimization_events") or []),
        )
        deployment_stage = str(
            latest_lineage_record.get("deployment_stage")
            or latest_run_context.get("deployment_stage")
            or governance_stage.get("deployment_stage")
            or "active"
        )
        composite_score = (
            _safe_avg(returns) * 0.30
            + _safe_avg(sharpes) * 10.0
            + _safe_avg(excess_returns) * 0.15
            + _safe_avg(strategy_scores) * 15.0
            + (benchmark_passes / len(items) if items else 0.0) * 18.0
            - _safe_avg(drawdowns) * 0.45
        )
        scoring_summaries = [_extract_scoring_change_summary(item) for item in items]
        objective_profile = {
            "benchmark_pass_rate": round(benchmark_passes / len(items), 6) if items else 0.0,
            "avg_sharpe_ratio": round(_safe_avg(sharpes), 6),
            "avg_return_pct": round(_safe_avg(returns), 6),
            "avg_max_drawdown": round(_safe_avg(drawdowns), 6),
        }
        entry = {
            "key": key,
            "model_name": str(items[0].get("model_name", "unknown")),
            "strategy_family": str(
                items[0].get("strategy_family")
                or items[0].get("model_name", "unknown")
            ),
            "config_name": str(items[0].get("config_name", "unknown")),
            "run_dirs": sorted({str(item.get("_dir", "")) for item in items}),
            "cycles": len(items),
            "profit_cycles": wins,
            "profit_rate": wins / len(items) if items else 0.0,
            "avg_return_pct": round(_safe_avg(returns), 6),
            "avg_sharpe_ratio": round(_safe_avg(sharpes), 6),
            "avg_max_drawdown": round(_safe_avg(drawdowns), 6),
            "avg_excess_return": round(_safe_avg(excess_returns), 6),
            "avg_strategy_score": round(_safe_avg(strategy_scores), 6),
            "benchmark_pass_rate": round(benchmark_passes / len(items), 6) if items else 0.0,
            "dominant_regime": dominant_regime,
            "regime_breakdown": dict(sorted(regimes.items())),
            "latest_cycle_id": int(items[-1].get("cycle_id", 0) or 0),
            "latest_cutoff_date": str(items[-1].get("cutoff_date", "")),
            "latest_return_pct": float(items[-1].get("return_pct", 0.0) or 0.0),
            "score": round(composite_score, 6),
            "deployment_stage": deployment_stage,
            "promotion_gate_status": str(latest_promotion_record.get("gate_status") or ""),
            "promotion_status": str(latest_promotion_record.get("status") or ""),
            "sample_gate": sample_gate,
            "quality_gate": {},
            "eligible_for_routing": False,
            "ineligible_reason": ineligible_reason,
            "style_profile": get_model_regime_profile(str(items[0].get("model_name", "unknown"))),
            "regime_performance": _build_regime_performance(
                items,
                model_name=str(items[0].get("model_name", "unknown")),
            ),
            "objective_profile": objective_profile,
            "objective_eligible_after_governance": False,
            "scoring_mutation_count": sum(item.get("scoring_mutation_count", 0) for item in scoring_summaries),
            "scoring_changed_keys": sorted({key for item in scoring_summaries for key in item.get("scoring_changed_keys", [])}),
        }
        quality_gate = evaluate_routing_quality_gate(
            entry,
            policy=dict((governance_matrix.get("routing") or {})),
        )
        entry["quality_gate"] = quality_gate
        entry["objective_eligible_after_governance"] = bool(quality_gate.get("passed", False))
        if eligible_for_routing and quality_gate.get("passed", False):
            entry["eligible_for_routing"] = True
            entry["ineligible_reason"] = ""
        elif not entry["ineligible_reason"] and not quality_gate.get("passed", False):
            failed_checks = list(quality_gate.get("failed_checks") or [])
            entry["ineligible_reason"] = (
                f"quality_gate:{failed_checks[0].get('name')}"
                if failed_checks
                else "quality_gate"
            )
        entries.append(entry)
        if entry["eligible_for_routing"]:
            regime_performance = dict(entry.get("regime_performance") or {})
            for regime_name, performance in regime_performance.items():
                if int(dict(performance or {}).get("cycles", 0) or 0) <= 0:
                    continue
                enriched = dict(entry)
                enriched["_regime_name"] = regime_name
                enriched["_regime_score"] = float(dict(performance or {}).get("score", 0.0) or 0.0)
                regime_groups[regime_name].append(enriched)

    entries.sort(
        key=lambda item: (
            bool(item.get("eligible_for_routing")),
            item["score"],
            item["avg_return_pct"],
            item["avg_sharpe_ratio"],
        ),
        reverse=True,
    )
    eligible_rank = 1
    for idx, entry in enumerate(entries, start=1):
        entry["provisional_rank"] = idx
        if entry.get("eligible_for_routing"):
            entry["rank"] = eligible_rank
            eligible_rank += 1
        else:
            entry["rank"] = 0

    regime_leaderboards: Dict[str, List[Dict[str, Any]]] = {}
    for regime, items in regime_groups.items():
        ranked = sorted(
            items,
            key=lambda item: (
                float(item.get("_regime_score", item["score"]) or 0.0),
                float(dict(item.get("regime_performance") or {}).get(regime, {}).get("compatibility", 0.0) or 0.0),
                item["score"],
                item["avg_return_pct"],
            ),
            reverse=True,
        )
        regime_leaderboards[regime] = [
            {
                "rank": idx,
                "model_name": item["model_name"],
                "config_name": item["config_name"],
                "score": item["score"],
                "regime_score": float(item.get("_regime_score", item["score"]) or 0.0),
                "avg_return_pct": item["avg_return_pct"],
                "avg_sharpe_ratio": item["avg_sharpe_ratio"],
                "benchmark_pass_rate": item["benchmark_pass_rate"],
                "eligible_for_routing": True,
                "cycles": int(dict(item.get("regime_performance") or {}).get(regime, {}).get("cycles", 0) or 0),
                "compatibility": float(
                    dict(item.get("regime_performance") or {}).get(regime, {}).get("compatibility", 0.0) or 0.0
                ),
                "source": "observed_regime",
                "scoring_mutation_count": item["scoring_mutation_count"],
            }
            for idx, item in enumerate(ranked, start=1)
        ]

    best_model = next((entry for entry in entries if entry.get("eligible_for_routing")), None)
    if best_model is None:
        best_model = entries[0] if entries else None

    return {
        "generated_at": __import__("datetime").datetime.now().isoformat(),
        "total_records": len(records),
        "total_models": len(entries),
        "eligible_models": sum(1 for entry in entries if entry.get("eligible_for_routing")),
        "policy": policy_payload,
        "quality_gate_matrix": governance_matrix,
        "entries": entries,
        "best_model": best_model,
        "regime_leaderboards": regime_leaderboards,
    }


def write_leaderboard(
    root_dir: str | Path,
    output_path: str | Path | None = None,
    *,
    policy: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    root_path = Path(root_dir)
    records = collect_cycle_records(root_path)
    leaderboard = build_leaderboard(records, policy=policy)
    target = Path(output_path) if output_path is not None else root_path / "leaderboard.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(leaderboard, ensure_ascii=False, indent=2), encoding="utf-8")
    return leaderboard
