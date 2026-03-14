"""Shared interface-layer response helpers for web runtime routes."""

from __future__ import annotations

from typing import Any

from flask import Response, jsonify

from app.commander_support.presentation import build_human_display
from app.commander_support.training import build_promotion_lineage_ops_panel


def _latest_training_result(body: dict[str, Any]) -> dict[str, Any]:
    training_lab = dict(body.get("training_lab") or {})
    run = dict(training_lab.get("run") or {})
    latest = dict(run.get("latest_result") or body.get("latest_result") or {})
    if latest:
        return latest
    payload = dict(body.get("payload") or {})
    results = [dict(item) for item in list(payload.get("results") or body.get("results") or []) if isinstance(item, dict)]
    return dict(results[-1]) if results else {}


def _kv(label: str, value: Any) -> dict[str, str]:
    return {"label": str(label), "value": str(value)}


def _training_display_cards(body: dict[str, Any]) -> list[dict[str, Any]]:
    latest = _latest_training_result(body)
    if not latest and not body.get("training_lab"):
        return []

    cards: list[dict[str, Any]] = []
    ops_panel = dict(
        dict(dict(body.get("training_lab") or {}).get("run") or {}).get("ops_panel")
        or latest.get("ops_panel")
        or build_promotion_lineage_ops_panel(latest)
        or {}
    )
    if ops_panel.get("available", False):
        refs = dict(ops_panel.get("refs") or {})
        status = dict(ops_panel.get("status") or {})
        review_window = dict(ops_panel.get("review_window") or {})
        rows = [
            _kv("promotion", status.get("promotion_status") or "unknown"),
            _kv("gate", status.get("gate_status") or "unknown"),
            _kv("lineage", status.get("lineage_status") or "unknown"),
        ]
        if status.get("basis_stage"):
            rows.append(_kv("basis_stage", status.get("basis_stage")))
        if refs.get("active_config_ref"):
            rows.append(_kv("active", refs.get("active_config_ref")))
        if refs.get("candidate_config_ref"):
            rows.append(_kv("candidate", refs.get("candidate_config_ref")))
        if review_window:
            rows.append(
                _kv(
                    "review_window",
                    f"{review_window.get('mode', 'unknown')} / {int(review_window.get('size', 0) or 0)}",
                )
            )
        cards.append(
            {
                "id": "training_ops_panel",
                "title": "Promotion / Lineage",
                "tone": "warning" if list(ops_panel.get("warnings") or []) else "neutral",
                "summary": str(ops_panel.get("summary") or ""),
                "rows": rows,
                "badges": [
                    str(item)
                    for item in [
                        status.get("promotion_status"),
                        status.get("gate_status"),
                        status.get("lineage_status"),
                    ]
                    if str(item or "").strip()
                ],
                "warnings": [str(item) for item in list(ops_panel.get("warnings") or []) if str(item or "").strip()],
            }
        )

    causal_diagnosis = dict(
        latest.get("causal_diagnosis")
        or dict(latest.get("review_decision") or {}).get("causal_diagnosis")
        or {}
    )
    if causal_diagnosis:
        drivers = [dict(item) for item in list(causal_diagnosis.get("drivers") or [])]
        rows = [
            _kv("primary_driver", causal_diagnosis.get("primary_driver") or "unknown"),
            _kv("summary", causal_diagnosis.get("summary") or ""),
        ]
        if drivers:
            top = drivers[0]
            rows.append(_kv("top_evidence", ",".join(str(item) for item in list(top.get("evidence_cycle_ids") or []))))
            rows.append(_kv("top_score", top.get("score") or ""))
        cards.append(
            {
                "id": "causal_diagnosis",
                "title": "Causal Diagnosis",
                "tone": "warning",
                "summary": str(causal_diagnosis.get("summary") or ""),
                "rows": rows,
                "badges": [str(causal_diagnosis.get("primary_driver") or "unknown")],
            }
        )

    similarity_summary = dict(
        latest.get("similarity_summary")
        or dict(latest.get("review_decision") or {}).get("similarity_summary")
        or {}
    )
    similar_results = [
        dict(item)
        for item in list(
            latest.get("similar_results")
            or dict(latest.get("review_decision") or {}).get("similar_results")
            or []
        )
    ]
    if similarity_summary or similar_results:
        rows = []
        matched_cycle_ids = list(similarity_summary.get("matched_cycle_ids") or [])
        if matched_cycle_ids:
            rows.append(_kv("matched_cycles", ",".join(str(item) for item in matched_cycle_ids)))
        if similarity_summary.get("dominant_regime"):
            rows.append(_kv("dominant_regime", similarity_summary.get("dominant_regime")))
        if similar_results:
            top = dict(similar_results[0])
            rows.append(
                _kv(
                    "top_match",
                    f"cycle {top.get('cycle_id')} / {float(top.get('return_pct', 0.0) or 0.0):+.2f}%",
                )
            )
        cards.append(
            {
                "id": "similar_samples",
                "title": "Similar Samples",
                "tone": "neutral",
                "summary": f"命中 {len(matched_cycle_ids or similar_results)} 个历史相似样本",
                "rows": rows,
                "badges": [str(similarity_summary.get("dominant_regime") or "").strip()] if str(similarity_summary.get("dominant_regime") or "").strip() else [],
            }
        )
    realism_metrics = dict(latest.get("realism_metrics") or {})
    if realism_metrics:
        rows = [
            _kv("avg_trade_amount", realism_metrics.get("avg_trade_amount") or ""),
            _kv("avg_turnover_rate", realism_metrics.get("avg_turnover_rate") or ""),
            _kv("avg_holding_days", realism_metrics.get("avg_holding_days") or ""),
        ]
        cards.append(
            {
                "id": "execution_realism",
                "title": "Execution Realism",
                "tone": "neutral",
                "summary": f"{int(realism_metrics.get('trade_record_count', 0) or 0)} 条交易记录",
                "rows": rows,
                "badges": [str(realism_metrics.get("selection_mode") or "").strip()] if str(realism_metrics.get("selection_mode") or "").strip() else [],
            }
        )
    return cards


def contract_payload_root(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, dict):
        if isinstance(payload.get("protocol"), dict) or isinstance(payload.get("task_bus"), dict):
            return payload
        snapshot = payload.get("snapshot")
        if isinstance(snapshot, dict) and (
            isinstance(snapshot.get("protocol"), dict)
            or isinstance(snapshot.get("task_bus"), dict)
        ):
            return snapshot
    return None


def jsonify_contract_payload(payload: Any, *, status_code: int = 200):
    response = jsonify(payload)
    response.status_code = int(status_code)
    root = contract_payload_root(payload)
    if not root:
        return response

    protocol = dict(root.get("protocol") or {})
    task_bus = dict(root.get("task_bus") or {})
    coverage = dict(root.get("coverage") or {})
    artifact_taxonomy = dict(root.get("artifact_taxonomy") or {})

    if protocol.get("schema_version"):
        response.headers["X-Bounded-Workflow-Schema"] = str(protocol.get("schema_version"))
    if protocol.get("task_bus_schema_version"):
        response.headers["X-Task-Bus-Schema"] = str(protocol.get("task_bus_schema_version"))
    elif task_bus.get("schema_version"):
        response.headers["X-Task-Bus-Schema"] = str(task_bus.get("schema_version"))
    if coverage.get("schema_version"):
        response.headers["X-Coverage-Schema"] = str(coverage.get("schema_version"))
    if artifact_taxonomy.get("schema_version"):
        response.headers["X-Artifact-Taxonomy-Schema"] = str(
            artifact_taxonomy.get("schema_version")
        )
    if protocol.get("domain"):
        response.headers["X-Commander-Domain"] = str(protocol.get("domain"))
    if protocol.get("operation"):
        response.headers["X-Commander-Operation"] = str(protocol.get("operation"))
    return response


def attach_display_payload(payload: Any) -> dict[str, Any]:
    body: dict[str, Any] = (
        dict(payload or {}) if isinstance(payload, dict) else {"reply": str(payload)}
    )
    display = build_human_display(body)
    body.setdefault(
        "human_reply",
        str(display.get("text") or body.get("reply") or body.get("message") or ""),
    )
    body.setdefault(
        "display",
        {
            "available": bool(display.get("available")),
            "title": str(display.get("title") or ""),
            "summary": str(display.get("summary") or ""),
            "text": str(display.get("text") or ""),
            "sections": list(display.get("sections") or []),
            "cards": _training_display_cards(body),
            "suggested_actions": list(display.get("suggested_actions") or []),
            "recommended_next_step": str(display.get("recommended_next_step") or ""),
            "risk_level": str(display.get("risk_level") or ""),
            "synthesized": bool(display.get("synthesized")),
        },
    )
    return body


def respond_with_display(payload: Any, *, status_code: int = 200, view: str = "json"):
    enriched = attach_display_payload(payload)
    if view == "human":
        return Response(
            str(enriched.get("human_reply") or ""),
            status=int(status_code),
            mimetype="text/plain; charset=utf-8",
        )
    return jsonify_contract_payload(enriched, status_code=status_code)
