from __future__ import annotations

import json
from collections import OrderedDict, defaultdict
from copy import deepcopy
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List

from invest_evolution.config import normalize_date
from invest_evolution.investment.runtimes import create_manager_runtime
from invest_evolution.investment.shared.research_feedback_gate import (
    DEFAULT_RESEARCH_FEEDBACK_GATE,
    evaluate_research_feedback_gate,
)

from .analysis import OutcomeAttribution, PolicySnapshot, ResearchHypothesis, ResearchSnapshot, stable_hash


def normalize_manager_config_ref(value: str) -> str:
    from invest_evolution.investment.managers.registry import (
        normalize_manager_config_ref as _normalize_manager_config_ref,
    )

    return _normalize_manager_config_ref(value)


def canonical_manager_config_ref(manager_id: str, manager_config_ref: str) -> str:
    from invest_evolution.investment.managers.registry import (
        canonical_manager_config_ref as _canonical_manager_config_ref,
    )

    return _canonical_manager_config_ref(manager_id, manager_config_ref)


class ResearchCaseStore:
    _ITER_RECORD_CACHE_SIZE = 32

    def __init__(self, root_dir: str | Path):
        self.root_dir = Path(root_dir)
        self.case_dir = self.root_dir / "research_cases"
        self.attribution_dir = self.root_dir / "research_attributions"
        self.calibration_dir = self.root_dir / "research_calibration"
        self.case_dir.mkdir(parents=True, exist_ok=True)
        self.attribution_dir.mkdir(parents=True, exist_ok=True)
        self.calibration_dir.mkdir(parents=True, exist_ok=True)
        self._case_cache_signature: tuple[tuple[str, int, int], ...] = ()
        self._case_cache_items: List[Dict[str, Any]] = []
        self._attribution_cache_signature: tuple[tuple[str, int, int], ...] = ()
        self._attribution_cache_items: List[Dict[str, Any]] = []
        self._attribution_index_signature: tuple[tuple[str, int, int], ...] = ()
        self._attribution_by_hypothesis: Dict[str, Dict[str, Any]] = {}
        self._iter_case_record_cache: OrderedDict[tuple[Any, ...], List[Dict[str, Any]]] = OrderedDict()

    def save_case(
        self,
        *,
        snapshot: ResearchSnapshot,
        policy: PolicySnapshot,
        hypothesis: ResearchHypothesis,
        metadata: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        payload = {
            "research_case_id": f"case_{stable_hash({'snapshot_id': snapshot.snapshot_id, 'policy_id': policy.policy_id, 'hypothesis_id': hypothesis.hypothesis_id})[:16]}",
            "created_at": datetime.now().isoformat(),
            "snapshot": snapshot.to_dict(),
            "policy": policy.to_dict(),
            "hypothesis": hypothesis.to_dict(),
            "metadata": dict(metadata or {}),
        }
        path = self.case_dir / f"{payload['research_case_id']}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._invalidate_case_cache()
        payload["path"] = str(path)
        return payload

    def save_attribution(self, attribution: OutcomeAttribution, *, metadata: Dict[str, Any] | None = None) -> Dict[str, Any]:
        payload = {
            "attribution_id": attribution.attribution_id,
            "created_at": datetime.now().isoformat(),
            "attribution": attribution.to_dict(),
            "metadata": dict(metadata or {}),
        }
        path = self.attribution_dir / f"{attribution.attribution_id}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._invalidate_attribution_cache()
        payload["path"] = str(path)
        return payload

    @staticmethod
    def _scan_json_files(
        directory: Path,
        pattern: str,
    ) -> list[tuple[Path, tuple[str, int, int]]]:
        files: list[tuple[Path, tuple[str, int, int]]] = []
        for path in sorted(directory.glob(pattern), key=lambda item: str(item)):
            try:
                stat = path.stat()
            except OSError:
                continue
            files.append((path, (str(path), int(stat.st_mtime_ns), int(stat.st_size))))
        return files

    def _invalidate_iter_case_record_cache(self) -> None:
        self._iter_case_record_cache.clear()

    def _invalidate_case_cache(self) -> None:
        self._case_cache_signature = ()
        self._case_cache_items = []
        self._invalidate_iter_case_record_cache()

    def _invalidate_attribution_cache(self) -> None:
        self._attribution_cache_signature = ()
        self._attribution_cache_items = []
        self._attribution_index_signature = ()
        self._attribution_by_hypothesis = {}
        self._invalidate_iter_case_record_cache()

    def _refresh_case_cache(
        self,
    ) -> tuple[List[Dict[str, Any]], tuple[tuple[str, int, int], ...]]:
        scanned_files = self._scan_json_files(self.case_dir, "case_*.json")
        signature = tuple(state for _, state in scanned_files)
        if signature != self._case_cache_signature:
            items = [
                json.loads(path.read_text(encoding="utf-8")) | {"path": str(path)}
                for path, _ in scanned_files
            ]

            # IMPORTANT: "history_limit" must reflect a time-ordered window.
            # Case ids are stable hashes and do not sort chronologically.
            def sort_key(item: Dict[str, Any]) -> tuple[str, str, str]:
                snapshot = dict(item.get("snapshot") or {})
                as_of = normalize_date(snapshot.get("as_of_date") or "") if snapshot.get("as_of_date") else ""
                created_at = str(item.get("created_at") or "")
                return (as_of or "", created_at or "", str(item.get("path") or ""))

            items.sort(key=sort_key)
            self._case_cache_items = items
            self._case_cache_signature = signature
            self._invalidate_iter_case_record_cache()
        return self._case_cache_items, self._case_cache_signature

    def _refresh_attribution_cache(
        self,
    ) -> tuple[List[Dict[str, Any]], tuple[tuple[str, int, int], ...]]:
        scanned_files = self._scan_json_files(self.attribution_dir, "attribution_*.json")
        signature = tuple(state for _, state in scanned_files)
        if signature != self._attribution_cache_signature:
            items = [
                json.loads(path.read_text(encoding="utf-8")) | {"path": str(path)}
                for path, _ in scanned_files
            ]

            def sort_key(item: Dict[str, Any]) -> tuple[str, str]:
                created_at = str(item.get("created_at") or "")
                return (created_at or "", str(item.get("path") or ""))

            items.sort(key=sort_key)
            self._attribution_cache_items = items
            self._attribution_cache_signature = signature
            self._attribution_index_signature = ()
            self._attribution_by_hypothesis = {}
            self._invalidate_iter_case_record_cache()
        return self._attribution_cache_items, self._attribution_cache_signature

    def _attribution_index_by_hypothesis(self) -> Dict[str, Dict[str, Any]]:
        attributions, signature = self._refresh_attribution_cache()
        if signature != self._attribution_index_signature:
            self._attribution_by_hypothesis = {
                str(dict(item.get("attribution") or {}).get("hypothesis_id") or ""): item
                for item in attributions
            }
            self._attribution_index_signature = signature
        return self._attribution_by_hypothesis

    def list_cases(self, *, limit: int | None = None) -> List[Dict[str, Any]]:
        items, _ = self._refresh_case_cache()
        if limit is not None:
            return deepcopy(items[-max(1, int(limit)) :])
        return deepcopy(items)

    def list_attributions(self, *, limit: int | None = None) -> List[Dict[str, Any]]:
        items, _ = self._refresh_attribution_cache()
        if limit is not None:
            return deepcopy(items[-max(1, int(limit)) :])
        return deepcopy(items)

    @staticmethod
    def _case_symbol(snapshot: Dict[str, Any]) -> str:
        security = dict(snapshot.get("security") or {})
        return str(security.get("code") or snapshot.get("metadata", {}).get("query_code") or "")

    @staticmethod
    def _case_regime(snapshot: Dict[str, Any]) -> str:
        market_context = dict(snapshot.get("market_context") or {})
        governance_context = dict(market_context.get("governance_context") or {})
        governance_regime = str(governance_context.get("regime") or "").strip()
        market_regime = str(market_context.get("regime") or "").strip()
        if governance_regime and governance_regime.lower() != "unknown":
            return governance_regime
        return market_regime

    @staticmethod
    def _case_manager_id(snapshot: Dict[str, Any]) -> str:
        market_context = dict(snapshot.get("market_context") or {})
        return str(market_context.get("manager_id") or "").strip()

    @staticmethod
    def _case_manager_config_ref(snapshot: Dict[str, Any]) -> str:
        market_context = dict(snapshot.get("market_context") or {})
        return canonical_manager_config_ref(
            str(market_context.get("manager_id") or "").strip(),
            market_context.get("manager_config_ref") or ""
        )

    @staticmethod
    def _canonical_manager_config_ref(value: str) -> str:
        return normalize_manager_config_ref(value)

    @staticmethod
    @lru_cache(maxsize=32)
    def _manager_research_feedback_policy(
        manager_id: str,
        manager_config_ref: str,
    ) -> Dict[str, Any]:
        normalized_manager_id = str(manager_id or "").strip()
        normalized_config_ref = canonical_manager_config_ref(
            normalized_manager_id,
            manager_config_ref,
        )
        if not normalized_manager_id:
            return {}
        try:
            runtime = create_manager_runtime(
                normalized_manager_id,
                runtime_config_ref=normalized_config_ref or None,
            )
        except Exception:
            return {}
        train_config = dict(runtime.config_section("train", {}) or {})
        freeze_gate = dict(train_config.get("freeze_gate") or {})
        feedback_policy = dict(freeze_gate.get("research_feedback") or {})
        if feedback_policy:
            return feedback_policy
        promotion_gate = dict(train_config.get("promotion_gate") or {})
        return dict(promotion_gate.get("research_feedback") or {})

    @classmethod
    def _training_feedback_min_sample_count(
        cls,
        manager_id: str,
        manager_config_ref: str,
    ) -> int:
        policy = cls._manager_research_feedback_policy(
            manager_id,
            manager_config_ref,
        )
        value = policy.get("min_sample_count")
        if value in (None, ""):
            value = DEFAULT_RESEARCH_FEEDBACK_GATE.get("min_sample_count")
        if value is None:
            return 3
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return 3

    @staticmethod
    def _unavailable_requested_regime_feedback(
        *,
        requested_regime: str,
        overall_summary: Dict[str, Any],
    ) -> Dict[str, Any]:
        overall_sample_count = int(overall_summary.get("sample_count") or 0)
        notes = [
            f"请求 regime={requested_regime or 'unknown'} 的研究反馈样本不足，未启用跨 regime fallback。"
        ]
        if overall_sample_count > 0:
            notes.append(f"overall 样本数={overall_sample_count}")
        return {
            "generated_at": str(overall_summary.get("generated_at") or datetime.now().isoformat()),
            "matched_case_count": 0,
            "sample_count": 0,
            "brier_like_direction_score": None,
            "invalidation_timeliness": {},
            "scenario_hit_distribution": {},
            "horizons": {},
            "recommendation": {
                "bias": "maintain",
                "reason_codes": ["requested_regime_unavailable"],
                "summary": f"请求 regime={requested_regime or 'unknown'} 的 research feedback 样本不足，未启用跨 regime fallback。",
            },
            "notes": notes,
        }

    def _compute_case_attribution_records(
        self,
        *,
        cases: List[Dict[str, Any]],
        attribution_by_hypothesis: Dict[str, Dict[str, Any]],
        normalized_policy: str,
        normalized_manager: str,
        normalized_config_ref: str,
        normalized_as_of: str,
        normalized_symbol: str,
        normalized_regime: str,
        normalized_stance: str,
    ) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        for case in cases:
            snapshot = dict(case.get("snapshot") or {})
            policy = dict(case.get("policy") or {})
            hypothesis = dict(case.get("hypothesis") or {})
            case_symbol = self._case_symbol(snapshot)
            case_as_of = str(snapshot.get("as_of_date") or "")
            case_regime = self._case_regime(snapshot)
            case_stance = str(hypothesis.get("stance") or "")
            if normalized_policy and str(policy.get("policy_id") or "") != normalized_policy:
                continue
            if normalized_manager and str(policy.get("manager_id") or "") != normalized_manager:
                continue
            snapshot_manager_id = self._case_manager_id(snapshot)
            if normalized_manager and snapshot_manager_id and snapshot_manager_id != normalized_manager:
                continue
            case_config_ref = canonical_manager_config_ref(
                normalized_manager or str(policy.get("manager_id") or "").strip(),
                policy.get("manager_config_ref") or "",
            )
            if normalized_config_ref and case_config_ref != normalized_config_ref:
                continue
            snapshot_config_ref = self._case_manager_config_ref(snapshot)
            if snapshot_config_ref:
                snapshot_config_ref = canonical_manager_config_ref(
                    snapshot_manager_id or normalized_manager,
                    snapshot_config_ref,
                )
            if normalized_config_ref and snapshot_config_ref and snapshot_config_ref != normalized_config_ref:
                continue
            if normalized_symbol and case_symbol != normalized_symbol:
                continue
            if normalized_as_of and case_as_of and case_as_of > normalized_as_of:
                continue
            if normalized_regime and case_regime != normalized_regime:
                continue
            if normalized_stance and case_stance != normalized_stance:
                continue
            hypothesis_id = str(hypothesis.get("hypothesis_id") or "")
            attribution_item = attribution_by_hypothesis.get(hypothesis_id)
            records.append(
                {
                    "case": case,
                    "snapshot": snapshot,
                    "policy": policy,
                    "hypothesis": hypothesis,
                    "symbol": case_symbol,
                    "attribution_item": attribution_item,
                    "attribution": dict((attribution_item or {}).get("attribution") or {}),
                }
            )
        return records

    def _iter_case_attribution_records(
        self,
        *,
        policy_id: str = "",
        manager_id: str = "",
        manager_config_ref: str = "",
        as_of_date: str = "",
        symbol: str = "",
        regime: str = "",
        stance: str = "",
    ) -> Iterable[Dict[str, Any]]:
        normalized_policy = str(policy_id or "").strip()
        normalized_manager = str(manager_id or "").strip()
        normalized_config_ref = canonical_manager_config_ref(
            normalized_manager,
            manager_config_ref,
        )
        normalized_symbol = str(symbol or "").strip()
        normalized_as_of = normalize_date(as_of_date) if str(as_of_date or "").strip() else ""
        normalized_regime = str(regime or "").strip()
        normalized_stance = str(stance or "").strip()
        cases, case_signature = self._refresh_case_cache()
        _, attribution_signature = self._refresh_attribution_cache()
        cache_key = (
            case_signature,
            attribution_signature,
            normalized_policy,
            normalized_manager,
            normalized_config_ref,
            normalized_as_of,
            normalized_symbol,
            normalized_regime,
            normalized_stance,
        )
        cached_records = self._iter_case_record_cache.get(cache_key)
        if cached_records is None:
            cached_records = self._compute_case_attribution_records(
                cases=cases,
                attribution_by_hypothesis=self._attribution_index_by_hypothesis(),
                normalized_policy=normalized_policy,
                normalized_manager=normalized_manager,
                normalized_config_ref=normalized_config_ref,
                normalized_as_of=normalized_as_of,
                normalized_symbol=normalized_symbol,
                normalized_regime=normalized_regime,
                normalized_stance=normalized_stance,
            )
            self._iter_case_record_cache[cache_key] = cached_records
            self._iter_case_record_cache.move_to_end(cache_key)
            while len(self._iter_case_record_cache) > self._ITER_RECORD_CACHE_SIZE:
                self._iter_case_record_cache.popitem(last=False)
        else:
            self._iter_case_record_cache.move_to_end(cache_key)
        for item in cached_records:
            yield deepcopy(item)

    def _training_feedback_observation_key(
        self,
        item: Dict[str, Any],
    ) -> tuple[Any, ...]:
        snapshot = dict(item.get("snapshot") or {})
        hypothesis = dict(item.get("hypothesis") or {})
        symbol = self._case_symbol(snapshot)
        as_of_date = normalize_date(snapshot.get("as_of_date") or "")
        regime = self._case_regime(snapshot) or "unknown"
        stance = str(hypothesis.get("stance") or "").strip()
        snapshot_manager_id = self._case_manager_id(snapshot) or "unknown"
        snapshot_config_ref = self._case_manager_config_ref(snapshot) or "unknown"
        if symbol and as_of_date:
            return (
                "observation",
                snapshot_manager_id,
                snapshot_config_ref,
                regime,
                symbol,
                as_of_date,
                stance,
            )
        case = dict(item.get("case") or {})
        return (
            "case",
            str(case.get("research_case_id") or ""),
            str(hypothesis.get("hypothesis_id") or ""),
        )

    @staticmethod
    def _training_feedback_record_rank(item: Dict[str, Any]) -> tuple[Any, ...]:
        case = dict(item.get("case") or {})
        hypothesis = dict(item.get("hypothesis") or {})
        attribution = dict(item.get("attribution") or {})
        return (
            1 if attribution else 0,
            len(dict(attribution.get("horizon_results") or {})),
            len(dict((hypothesis.get("scenario_distribution") or {}).get("horizons") or {})),
            str(case.get("created_at") or ""),
            str(case.get("research_case_id") or ""),
        )

    def _dedupe_training_feedback_records(
        self,
        records: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        unique_records: Dict[tuple[Any, ...], Dict[str, Any]] = {}
        key_order: List[tuple[Any, ...]] = []
        for item in records:
            key = self._training_feedback_observation_key(item)
            existing = unique_records.get(key)
            if existing is None:
                unique_records[key] = item
                key_order.append(key)
                continue
            if self._training_feedback_record_rank(item) >= self._training_feedback_record_rank(existing):
                unique_records[key] = item
        return [unique_records[key] for key in key_order]

    @staticmethod
    def _training_feedback_record_sort_key(
        item: Dict[str, Any],
    ) -> tuple[str, str, str]:
        snapshot = dict(item.get("snapshot") or {})
        case = dict(item.get("case") or {})
        return (
            normalize_date(snapshot.get("as_of_date") or ""),
            str(case.get("created_at") or ""),
            str(case.get("research_case_id") or ""),
        )

    def find_cases(
        self,
        *,
        policy_id: str = "",
        symbol: str = "",
        as_of_date: str = "",
        horizon: str = "",
        limit: int | None = None,
    ) -> List[Dict[str, Any]]:
        normalized_horizon = str(horizon or "").strip()
        matches: List[Dict[str, Any]] = []
        for item in self._iter_case_attribution_records(policy_id=policy_id, symbol=symbol, as_of_date=as_of_date):
            record = dict(item["case"])
            if item.get("attribution_item"):
                record["attribution"] = dict(item.get("attribution") or {})
                record["attribution_path"] = str(dict(item.get("attribution_item") or {}).get("path") or "")
            if normalized_horizon:
                horizon_result = dict((record.get("attribution") or {}).get("horizon_results", {}).get(normalized_horizon) or {})
                if not horizon_result:
                    continue
            matches.append(record)
        if limit is not None:
            return matches[-int(limit):]
        return matches

    def _summarize_records(self, records: List[Dict[str, Any]], *, subject: Dict[str, Any] | None = None) -> Dict[str, Any]:
        horizon_stats: Dict[str, Dict[str, Any]] = {}
        briers: List[float] = []
        first_invalidation_horizons: List[int] = []
        scenario_buckets: Dict[str, int] = defaultdict(int)
        attributed_records = [item for item in records if item.get("attribution")]
        for item in attributed_records:
            attribution = dict(item.get("attribution") or {})
            case = dict(item.get("case") or {})
            hypothesis = dict(case.get("hypothesis") or {})
            calibration = dict(attribution.get("calibration_metrics") or {})
            if calibration.get("positive_return_brier") is not None:
                briers.append(float(calibration.get("positive_return_brier") or 0.0))
            scenario = dict(hypothesis.get("scenario_distribution") or {})
            first_invalidated = None
            for horizon_key, result in dict(attribution.get("horizon_results") or {}).items():
                bucket = horizon_stats.setdefault(
                    horizon_key,
                    {
                        "count": 0,
                        "label_counts": defaultdict(int),
                        "interval_hit_count": 0,
                        "interval_sample_count": 0,
                    },
                )
                bucket["count"] += 1
                label = str(dict(result or {}).get("label") or "timeout")
                bucket["label_counts"][label] += 1
                if label == "invalidated" and first_invalidated is None:
                    try:
                        first_invalidated = int(str(horizon_key).replace("T+", ""))
                    except Exception:
                        first_invalidated = None
                actual_return = dict(result or {}).get("return_pct")
                interval = dict((dict(scenario.get("horizons") or {}).get(horizon_key) or {}).get("interval") or {})
                if actual_return is not None and interval:
                    p25 = interval.get("p25")
                    p75 = interval.get("p75")
                    if p25 is not None and p75 is not None:
                        bucket["interval_sample_count"] += 1
                        if float(p25) <= float(actual_return) <= float(p75):
                            bucket["interval_hit_count"] += 1
            if first_invalidated is not None:
                first_invalidation_horizons.append(first_invalidated)
            actual_t20 = dict((dict(attribution.get("horizon_results") or {}).get("T+20") or {})).get("return_pct")
            interval_t20 = dict((dict(scenario.get("horizons") or {}).get("T+20") or {}).get("interval") or {})
            if actual_t20 is not None and interval_t20:
                p25 = interval_t20.get("p25")
                p75 = interval_t20.get("p75")
                if p25 is not None and float(actual_t20) <= float(p25):
                    scenario_buckets["bear"] += 1
                elif p75 is not None and float(actual_t20) >= float(p75):
                    scenario_buckets["bull"] += 1
                else:
                    scenario_buckets["base"] += 1
        serialized_horizons: Dict[str, Dict[str, Any]] = {}
        for horizon_key, bucket in horizon_stats.items():
            count = int(bucket.get("count") or 0)
            labels = dict(bucket.get("label_counts") or {})
            interval_sample_count = int(bucket.get("interval_sample_count") or 0)
            serialized_horizons[horizon_key] = {
                "count": count,
                "label_counts": labels,
                "hit_rate": round(labels.get("hit", 0) / count, 4) if count else None,
                "invalidation_rate": round(labels.get("invalidated", 0) / count, 4) if count else None,
                "timeout_rate": round(labels.get("timeout", 0) / count, 4) if count else None,
                "interval_hit_rate": round(int(bucket.get("interval_hit_count") or 0) / interval_sample_count, 4)
                if interval_sample_count
                else None,
            }
        return {
            "generated_at": datetime.now().isoformat(),
            "subject": dict(subject or {}),
            "matched_case_count": len(records),
            "sample_count": len(attributed_records),
            "brier_like_direction_score": round(sum(briers) / len(briers), 6) if briers else None,
            "invalidation_timeliness": {
                "sample_count": len(first_invalidation_horizons),
                "avg_first_invalidation_horizon": round(sum(first_invalidation_horizons) / len(first_invalidation_horizons), 4)
                if first_invalidation_horizons
                else None,
            },
            "scenario_hit_distribution": dict(sorted(scenario_buckets.items())),
            "horizons": serialized_horizons,
        }

    @staticmethod
    def _generic_feedback_recommendation(
        *,
        hit_rate: Any,
        invalidation_rate: Any,
        interval_hit_rate: Any,
        brier: Any,
    ) -> tuple[str, list[str]]:
        reason_codes: list[str] = []
        bias = "maintain"
        if hit_rate is not None and float(hit_rate) < 0.45:
            bias = "tighten_risk"
            reason_codes.append("t20_hit_rate_low")
        if invalidation_rate is not None and float(invalidation_rate) >= 0.35:
            bias = "tighten_risk"
            reason_codes.append("t20_invalidation_high")
        if bias == "maintain" and brier is not None and float(brier) > 0.28:
            bias = "recalibrate_probability"
            reason_codes.append("direction_brier_high")
        if bias == "maintain" and interval_hit_rate is not None and float(interval_hit_rate) < 0.40:
            bias = "recalibrate_probability"
            reason_codes.append("interval_hit_rate_low")
        return bias, reason_codes

    @staticmethod
    def _feedback_recommendation(summary: Dict[str, Any]) -> Dict[str, Any]:
        subject = dict(summary.get("subject") or {})
        manager_id = str(subject.get("manager_id") or "").strip()
        manager_config_ref = str(subject.get("manager_config_ref") or "").strip()
        t5 = dict(summary.get("horizons", {}).get("T+5") or {})
        t20 = dict(summary.get("horizons", {}).get("T+20") or {})
        t5_hit_rate = t5.get("hit_rate")
        hit_rate = t20.get("hit_rate")
        invalidation_rate = t20.get("invalidation_rate")
        interval_hit_rate = t20.get("interval_hit_rate")
        brier = summary.get("brier_like_direction_score")
        reason_codes: list[str] = []
        bias = "maintain"
        min_sample_count = ResearchCaseStore._training_feedback_min_sample_count(
            manager_id,
            manager_config_ref,
        )
        feedback_policy = ResearchCaseStore._manager_research_feedback_policy(
            manager_id,
            manager_config_ref,
        )
        if int(summary.get("sample_count") or 0) < min_sample_count:
            bias = "insufficient_samples"
            reason_codes.append("insufficient_samples")
        elif feedback_policy:
            gate_result = evaluate_research_feedback_gate(
                {
                    **dict(summary or {}),
                    "recommendation": {"bias": "maintain"},
                },
                policy=feedback_policy,
                defaults={},
            )
            if not bool(gate_result.get("active")):
                bias, reason_codes = ResearchCaseStore._generic_feedback_recommendation(
                    hit_rate=hit_rate,
                    invalidation_rate=invalidation_rate,
                    interval_hit_rate=interval_hit_rate,
                    brier=brier,
                )
            else:
                failed_checks = list(gate_result.get("failed_checks") or [])
                horizon_reason_codes: list[str] = []
                interval_failed = False
                direction_brier_failed = False
                for check in failed_checks:
                    name = str(check.get("name") or "").strip()
                    horizon = str(check.get("horizon") or "").strip().lower().replace("+", "")
                    metric = str(check.get("metric") or "").strip()
                    if metric == "hit_rate" and horizon:
                        horizon_reason_codes.append(f"{horizon}_hit_rate_low")
                    elif metric == "invalidation_rate" and horizon:
                        horizon_reason_codes.append(f"{horizon}_invalidation_high")
                    elif metric == "interval_hit_rate":
                        interval_failed = True
                    elif name == "max_brier_like_direction_score":
                        direction_brier_failed = True
                if horizon_reason_codes:
                    bias = "tighten_risk"
                    reason_codes.extend(horizon_reason_codes)
                elif direction_brier_failed or interval_failed:
                    bias = "recalibrate_probability"
                    if direction_brier_failed:
                        reason_codes.append("direction_brier_high")
                    if interval_failed:
                        reason_codes.append("interval_hit_rate_low")
        else:
            bias, reason_codes = ResearchCaseStore._generic_feedback_recommendation(
                hit_rate=hit_rate,
                invalidation_rate=invalidation_rate,
                interval_hit_rate=interval_hit_rate,
                brier=brier,
            )
        notes = [
            f"样本数={int(summary.get('sample_count') or 0)}",
            f"T+5 hit_rate={t5_hit_rate}",
            f"T+20 hit_rate={hit_rate}",
            f"T+20 invalidation_rate={invalidation_rate}",
            f"T+20 interval_hit_rate={interval_hit_rate}",
            f"direction_brier={brier}",
        ]
        return {
            **dict(summary or {}),
            "recommendation": {
                "bias": bias,
                "reason_codes": reason_codes,
                "summary": f"基于 ask 侧归因样本给训练侧的建议：{bias}",
            },
            "notes": notes,
        }

    def build_calibration_report(self, *, policy_id: str = "") -> Dict[str, Any]:
        normalized_policy = str(policy_id or "").strip()
        records = list(self._iter_case_attribution_records(policy_id=normalized_policy))
        return self._summarize_records(records, subject={"policy_id": normalized_policy})

    def build_training_feedback(
        self,
        *,
        manager_id: str,
        manager_config_ref: str = "",
        as_of_date: str = "",
        regime: str = "",
        limit: int | None = 200,
        max_history_limit: int | None = None,
    ) -> Dict[str, Any]:
        normalized_manager = str(manager_id or "").strip()
        normalized_config_ref = canonical_manager_config_ref(
            normalized_manager,
            manager_config_ref,
        )
        normalized_as_of = normalize_date(as_of_date) if str(as_of_date or "").strip() else ""
        normalized_regime = str(regime or "").strip()
        records = list(
            self._iter_case_attribution_records(
                manager_id=normalized_manager,
                manager_config_ref=normalized_config_ref,
                as_of_date=normalized_as_of,
            )
        )
        records = self._dedupe_training_feedback_records(records)
        records = sorted(records, key=self._training_feedback_record_sort_key)

        # Window selection strategy:
        # 1) Keep overall feedback based on the recent global window (`limit`).
        # 2) For requested regime, use its own recent regime window first instead of
        #    slicing overall then re-grouping, so rare regimes are not diluted by
        #    dominant recent regimes.
        # 3) If requested regime is still under-sampled, expand only the requested
        #    regime window up to max_history_limit.
        #
        # NOTE: This changes evidence sampling only; gate thresholds remain unchanged.
        min_sample_count = self._training_feedback_min_sample_count(
            normalized_manager,
            normalized_config_ref,
        )

        base_limit = None
        if limit is not None:
            try:
                base_limit = max(1, int(limit))
            except (TypeError, ValueError):
                base_limit = 200
        hard_cap = None
        if max_history_limit not in (None, ""):
            try:
                hard_cap = max(1, int(max_history_limit))
            except (TypeError, ValueError):
                hard_cap = None
        if hard_cap is not None and base_limit is not None:
            base_limit = min(base_limit, hard_cap)
        if hard_cap is not None:
            hard_cap = min(hard_cap, len(records))

        def _tail_window(
            source: List[Dict[str, Any]],
            requested_limit: int | None,
        ) -> tuple[List[Dict[str, Any]], int]:
            if not source:
                return [], 0
            if requested_limit is None:
                return list(source), len(source)
            bounded_limit = max(1, int(requested_limit))
            bounded_limit = min(bounded_limit, len(source))
            return list(source[-bounded_limit:]), bounded_limit

        def _attributed_sample_count(source: List[Dict[str, Any]]) -> int:
            return sum(1 for item in source if item.get("attribution"))

        def _regime_attributed_counts(
            source: List[Dict[str, Any]],
        ) -> dict[str, int]:
            counts: dict[str, int] = defaultdict(int)
            for item in source:
                if not item.get("attribution"):
                    continue
                regime_key = self._case_regime(dict(item.get("snapshot") or {})) or "unknown"
                counts[regime_key] += 1
            return dict(counts)

        overall_window_records, overall_effective_limit = _tail_window(records, base_limit)
        overall_summary = self._feedback_recommendation(
            self._summarize_records(
                overall_window_records,
                subject={
                    "manager_id": normalized_manager,
                    "manager_config_ref": normalized_config_ref,
                    "as_of_date": normalized_as_of,
                },
            )
        )

        regime_groups: dict[str, list[Dict[str, Any]]] = defaultdict(list)
        for item in overall_window_records:
            regime_key = self._case_regime(dict(item.get("snapshot") or {})) or "unknown"
            regime_groups[regime_key].append(item)
        regime_breakdown = {
            regime_key: self._feedback_recommendation(
                self._summarize_records(
                    group_records,
                    subject={
                        "manager_id": normalized_manager,
                        "manager_config_ref": normalized_config_ref,
                        "as_of_date": normalized_as_of,
                        "regime": regime_key,
                    },
                )
            )
            for regime_key, group_records in sorted(regime_groups.items())
        }

        requested_regime_feedback: Dict[str, Any] = {}
        requested_regime_effective_limit = 0
        requested_regime_expanded = False
        if normalized_regime:
            regime_records = [
                item
                for item in records
                if (self._case_regime(dict(item.get("snapshot") or {})) or "unknown")
                == normalized_regime
            ]
            regime_window_records, requested_regime_effective_limit = _tail_window(
                regime_records,
                base_limit,
            )
            current_sample_count = _attributed_sample_count(regime_window_records)
            regime_cap = len(regime_records)
            if hard_cap is not None:
                regime_cap = min(regime_cap, hard_cap)
            if current_sample_count < min_sample_count and requested_regime_effective_limit < regime_cap:
                requested_regime_expanded = True
                while current_sample_count < min_sample_count and requested_regime_effective_limit < regime_cap:
                    next_limit = min(
                        regime_cap,
                        max(
                            requested_regime_effective_limit * 2,
                            requested_regime_effective_limit + 1,
                        ),
                    )
                    regime_window_records, requested_regime_effective_limit = _tail_window(
                        regime_records,
                        next_limit,
                    )
                    current_sample_count = _attributed_sample_count(regime_window_records)
            if regime_window_records:
                requested_regime_feedback = self._feedback_recommendation(
                    self._summarize_records(
                        regime_window_records,
                        subject={
                            "manager_id": normalized_manager,
                            "manager_config_ref": normalized_config_ref,
                            "as_of_date": normalized_as_of,
                            "regime": normalized_regime,
                        },
                    )
                )
                regime_breakdown[normalized_regime] = dict(requested_regime_feedback)

        coverage_regime_counts = _regime_attributed_counts(records)
        target_regimes = sorted(
            {
                *coverage_regime_counts.keys(),
                *regime_breakdown.keys(),
                *( [normalized_regime] if normalized_regime else [] ),
            }
        )
        if normalized_regime:
            target_regimes = [
                normalized_regime,
                *[item for item in target_regimes if item != normalized_regime],
            ]
        regime_targets: dict[str, dict[str, Any]] = {}
        for regime_key in target_regimes:
            sample_count = int(coverage_regime_counts.get(regime_key) or 0)
            gap_count = max(0, min_sample_count - sample_count)
            regime_targets[regime_key] = {
                "sample_count": sample_count,
                "gap_count": gap_count,
                "ready": gap_count == 0,
            }
        requested_regime_sample_count = (
            int(regime_targets.get(normalized_regime, {}).get("sample_count") or 0)
            if normalized_regime
            else 0
        )
        current_cycle_records = [
            item
            for item in records
            if normalized_as_of
            and normalize_date(str(dict(item.get("snapshot") or {}).get("as_of_date") or ""))
            == normalized_as_of
            and item.get("attribution")
        ]
        current_cycle_regime_counts = _regime_attributed_counts(current_cycle_records)
        current_cycle_requested_regime_gain = (
            int(current_cycle_regime_counts.get(normalized_regime) or 0)
            if normalized_regime
            else 0
        )
        requested_regime_gap_count = (
            max(0, min_sample_count - requested_regime_sample_count)
            if normalized_regime
            else 0
        )
        coverage_plan = {
            "schema_version": "research.feedback_coverage_plan.v1",
            "requested_regime": normalized_regime,
            "target_regimes": target_regimes,
            "min_sample_count": int(min_sample_count),
            "coverage_ready": bool(
                target_regimes
                and all(bool(item.get("ready", False)) for item in regime_targets.values())
            ),
            "requested_regime_ready": (
                bool(regime_targets.get(normalized_regime, {}).get("ready", False))
                if normalized_regime
                else False
            ),
            "requested_regime_gap_count": int(requested_regime_gap_count),
            "next_target_regimes": [
                regime_key
                for regime_key, summary in sorted(
                    regime_targets.items(),
                    key=lambda pair: (
                        -int(pair[1].get("gap_count") or 0),
                        -int(pair[1].get("sample_count") or 0),
                        pair[0],
                    ),
                )
                if int(summary.get("gap_count") or 0) > 0
            ],
            "regime_targets": regime_targets,
            "current_cycle_contribution": {
                "sample_count": len(current_cycle_records),
                "regime_counts": current_cycle_regime_counts,
                "requested_regime_sample_count": current_cycle_requested_regime_gain,
            },
        }

        effective_feedback = dict(overall_summary)
        effective_scope = "overall"
        scope_actionable = True
        unavailable_reason = ""
        if normalized_regime:
            requested_sample_count = int(requested_regime_feedback.get("sample_count") or 0)
            if requested_sample_count >= min_sample_count:
                effective_feedback = dict(requested_regime_feedback)
                effective_scope = "regime"
            elif requested_sample_count > 0:
                effective_feedback = dict(requested_regime_feedback)
                effective_scope = "regime_insufficient_samples"
                scope_actionable = False
                unavailable_reason = "requested_regime_insufficient_samples"
            else:
                effective_feedback = self._unavailable_requested_regime_feedback(
                    requested_regime=normalized_regime,
                    overall_summary=overall_summary,
                )
                effective_scope = "requested_regime_unavailable"
                scope_actionable = False
                unavailable_reason = "requested_regime_unavailable"
        feedback = {
            "schema_version": "research.training_feedback.v1",
            **effective_feedback,
            "subject": {
                "manager_id": normalized_manager,
                "manager_config_ref": normalized_config_ref,
                "as_of_date": normalized_as_of,
                "regime": normalized_regime,
            },
            "scope": {
                "requested_regime": normalized_regime,
                "effective_scope": effective_scope,
                "overall_sample_count": int(overall_summary.get("sample_count") or 0),
                "regime_sample_count": int(requested_regime_feedback.get("sample_count") or 0),
                "covered_regimes": sorted(regime_breakdown.keys()),
                "actionable": scope_actionable,
                "unavailable_reason": unavailable_reason,
                "window": {
                    "base_history_limit": int(base_limit or 0),
                    "overall_effective_history_limit": int(overall_effective_limit or 0),
                    "requested_regime_effective_history_limit": int(
                        requested_regime_effective_limit or 0
                    ),
                    "max_history_limit": int(hard_cap or 0),
                    "requested_regime_expanded": bool(requested_regime_expanded),
                },
            },
            "overall_feedback": overall_summary,
            "requested_regime_feedback": requested_regime_feedback,
            "regime_breakdown": regime_breakdown,
            "coverage_plan": coverage_plan,
        }
        return feedback

    def write_calibration_report(self, *, policy_id: str = "") -> Dict[str, Any]:
        report = self.build_calibration_report(policy_id=policy_id)
        file_name = f"policy_{policy_id}.json" if policy_id else "policy_all.json"
        path = self.calibration_dir / file_name
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return {**report, "path": str(path)}

    def iter_similar_attributions(
        self,
        *,
        manager_id: str,
        regime: str,
        stance: str,
    ) -> Iterable[Dict[str, Any]]:
        for item in self._iter_case_attribution_records(manager_id=manager_id, regime=regime, stance=stance):
            if not item.get("attribution"):
                continue
            yield {
                "case": item["case"],
                "attribution": item["attribution"],
            }
