from __future__ import annotations

import json
from collections import defaultdict
import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List

logger = logging.getLogger(__name__)

DEFAULT_LEADERBOARD_POLICY: Dict[str, int] = {
    "min_cycles": 3,
    "min_cycles_per_regime": 2,
}


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
        if "config_snapshots" in path.parts:
            continue
        if path.name.endswith("_config_snapshot.json"):
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


def build_leaderboard(records: List[Dict[str, Any]], policy: Dict[str, Any] | None = None) -> Dict[str, Any]:
    resolved_policy = dict(DEFAULT_LEADERBOARD_POLICY)
    resolved_policy.update(dict(policy or {}))
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
        composite_score = (
            _safe_avg(returns) * 0.30
            + _safe_avg(sharpes) * 10.0
            + _safe_avg(excess_returns) * 0.15
            + _safe_avg(strategy_scores) * 15.0
            + (benchmark_passes / len(items) if items else 0.0) * 18.0
            - _safe_avg(drawdowns) * 0.45
        )
        scoring_summaries = [_extract_scoring_change_summary(item) for item in items]
        entry = {
            "key": key,
            "model_name": str(items[0].get("model_name", "unknown")),
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
            "eligible_for_routing": eligible_for_routing,
            "ineligible_reason": ineligible_reason,
            "sample_gate": sample_gate,
            "scoring_mutation_count": sum(item.get("scoring_mutation_count", 0) for item in scoring_summaries),
            "scoring_changed_keys": sorted({key for item in scoring_summaries for key in item.get("scoring_changed_keys", [])}),
        }
        entries.append(entry)
        if eligible_for_routing:
            regime_groups[dominant_regime].append(entry)

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
        ranked = sorted(items, key=lambda item: (item["score"], item["avg_return_pct"]), reverse=True)
        regime_leaderboards[regime] = [
            {
                "rank": idx,
                "model_name": item["model_name"],
                "config_name": item["config_name"],
                "score": item["score"],
                "avg_return_pct": item["avg_return_pct"],
                "avg_sharpe_ratio": item["avg_sharpe_ratio"],
                "benchmark_pass_rate": item["benchmark_pass_rate"],
                "eligible_for_routing": True,
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
        "policy": dict(resolved_policy),
        "entries": entries,
        "best_model": best_model,
        "regime_leaderboards": regime_leaderboards,
    }


def write_leaderboard(root_dir: str | Path, output_path: str | Path | None = None) -> Dict[str, Any]:
    root_path = Path(root_dir)
    records = collect_cycle_records(root_path)
    leaderboard = build_leaderboard(records)
    target = Path(output_path) if output_path is not None else root_path / "leaderboard.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(leaderboard, ensure_ascii=False, indent=2), encoding="utf-8")
    return leaderboard
