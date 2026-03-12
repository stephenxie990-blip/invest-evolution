from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List

from config import normalize_date

from .contracts import OutcomeAttribution, PolicySnapshot, ResearchHypothesis, ResearchSnapshot, stable_hash


class ResearchCaseStore:
    def __init__(self, root_dir: str | Path):
        self.root_dir = Path(root_dir)
        self.case_dir = self.root_dir / "research_cases"
        self.attribution_dir = self.root_dir / "research_attributions"
        self.calibration_dir = self.root_dir / "research_calibration"
        self.case_dir.mkdir(parents=True, exist_ok=True)
        self.attribution_dir.mkdir(parents=True, exist_ok=True)
        self.calibration_dir.mkdir(parents=True, exist_ok=True)

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
        payload["path"] = str(path)
        return payload

    def list_cases(self, *, limit: int | None = None) -> List[Dict[str, Any]]:
        items = [json.loads(path.read_text(encoding="utf-8")) | {"path": str(path)} for path in sorted(self.case_dir.glob("case_*.json"))]
        if limit is not None:
            return items[-int(limit):]
        return items

    def list_attributions(self, *, limit: int | None = None) -> List[Dict[str, Any]]:
        items = [json.loads(path.read_text(encoding="utf-8")) | {"path": str(path)} for path in sorted(self.attribution_dir.glob("attribution_*.json"))]
        if limit is not None:
            return items[-int(limit):]
        return items

    @staticmethod
    def _case_symbol(snapshot: Dict[str, Any]) -> str:
        security = dict(snapshot.get("security") or {})
        return str(security.get("code") or snapshot.get("metadata", {}).get("query_code") or "")

    def _iter_case_attribution_records(
        self,
        *,
        policy_id: str = "",
        model_name: str = "",
        config_name: str = "",
        as_of_date: str = "",
        symbol: str = "",
        regime: str = "",
        stance: str = "",
    ) -> Iterable[Dict[str, Any]]:
        normalized_policy = str(policy_id or "").strip()
        normalized_model = str(model_name or "").strip()
        normalized_config = str(config_name or "").strip()
        normalized_symbol = str(symbol or "").strip()
        normalized_as_of = normalize_date(as_of_date) if str(as_of_date or "").strip() else ""
        normalized_regime = str(regime or "").strip()
        normalized_stance = str(stance or "").strip()
        attribution_by_hypothesis = {
            str(dict(item.get("attribution") or {}).get("hypothesis_id") or ""): item
            for item in self.list_attributions()
        }
        for case in self.list_cases():
            snapshot = dict(case.get("snapshot") or {})
            policy = dict(case.get("policy") or {})
            hypothesis = dict(case.get("hypothesis") or {})
            case_symbol = self._case_symbol(snapshot)
            case_as_of = str(snapshot.get("as_of_date") or "")
            case_regime = str((snapshot.get("market_context") or {}).get("regime") or "")
            case_stance = str(hypothesis.get("stance") or "")
            if normalized_policy and str(policy.get("policy_id") or "") != normalized_policy:
                continue
            if normalized_model and str(policy.get("model_name") or "") != normalized_model:
                continue
            if normalized_config and str(policy.get("config_name") or "") != normalized_config:
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
            yield {
                "case": case,
                "snapshot": snapshot,
                "policy": policy,
                "hypothesis": hypothesis,
                "symbol": case_symbol,
                "attribution_item": attribution_item,
                "attribution": dict((attribution_item or {}).get("attribution") or {}),
            }

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

    def build_calibration_report(self, *, policy_id: str = "") -> Dict[str, Any]:
        normalized_policy = str(policy_id or "").strip()
        records = list(self._iter_case_attribution_records(policy_id=normalized_policy))
        return self._summarize_records(records, subject={"policy_id": normalized_policy})

    def build_training_feedback(
        self,
        *,
        model_name: str,
        config_name: str = "",
        as_of_date: str = "",
        limit: int | None = 200,
    ) -> Dict[str, Any]:
        normalized_model = str(model_name or "").strip()
        normalized_config = str(config_name or "").strip()
        normalized_as_of = normalize_date(as_of_date) if str(as_of_date or "").strip() else ""
        records = list(
            self._iter_case_attribution_records(
                model_name=normalized_model,
                config_name=normalized_config,
                as_of_date=normalized_as_of,
            )
        )
        if limit is not None:
            records = records[-int(limit):]
        summary = self._summarize_records(
            records,
            subject={
                "model_name": normalized_model,
                "config_name": normalized_config,
                "as_of_date": normalized_as_of,
            },
        )
        t20 = dict(summary.get("horizons", {}).get("T+20") or {})
        hit_rate = t20.get("hit_rate")
        invalidation_rate = t20.get("invalidation_rate")
        interval_hit_rate = t20.get("interval_hit_rate")
        brier = summary.get("brier_like_direction_score")
        reason_codes: list[str] = []
        bias = "maintain"
        if int(summary.get("sample_count") or 0) < 3:
            bias = "insufficient_samples"
            reason_codes.append("insufficient_samples")
        else:
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
        notes = [
            f"样本数={int(summary.get('sample_count') or 0)}",
            f"T+20 hit_rate={hit_rate}",
            f"T+20 invalidation_rate={invalidation_rate}",
            f"T+20 interval_hit_rate={interval_hit_rate}",
            f"direction_brier={brier}",
        ]
        return {
            "schema_version": "research.training_feedback.v1",
            **summary,
            "recommendation": {
                "bias": bias,
                "reason_codes": reason_codes,
                "summary": f"基于 ask 侧归因样本给训练侧的建议：{bias}",
            },
            "notes": notes,
        }

    def write_calibration_report(self, *, policy_id: str = "") -> Dict[str, Any]:
        report = self.build_calibration_report(policy_id=policy_id)
        file_name = f"policy_{policy_id}.json" if policy_id else "policy_all.json"
        path = self.calibration_dir / file_name
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return {**report, "path": str(path)}

    def iter_similar_attributions(
        self,
        *,
        model_name: str,
        regime: str,
        stance: str,
    ) -> Iterable[Dict[str, Any]]:
        for item in self._iter_case_attribution_records(model_name=model_name, regime=regime, stance=stance):
            if not item.get("attribution"):
                continue
            yield {
                "case": item["case"],
                "attribution": item["attribution"],
            }
