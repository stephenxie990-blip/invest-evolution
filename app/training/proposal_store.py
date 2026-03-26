from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from invest.research.contracts import stable_hash
from invest.shared.model_governance import normalize_config_ref
from app.training.suggestion_tracking import (
    build_suggestion_tracking_summary,
    ensure_proposal_tracking_fields,
)


def _copy_dict(value: Any) -> dict[str, Any]:
    return deepcopy(dict(value or {}))


def _proposal_store_dir(base: Any) -> Path:
    if hasattr(base, "output_dir"):
        root = Path(getattr(base, "output_dir"))
    else:
        root = Path(base)
    store_dir = root / "proposal_store"
    store_dir.mkdir(parents=True, exist_ok=True)
    return store_dir


def persist_cycle_proposal_bundle(
    controller: Any,
    *,
    cycle_id: int,
    execution_snapshot: dict[str, Any] | None = None,
    proposals: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    snapshot = _copy_dict(execution_snapshot or {})
    proposal_items = [
        ensure_proposal_tracking_fields(
            deepcopy(dict(item or {})),
            default_cycle_id=int(cycle_id),
        )
        for item in list(proposals or getattr(controller, "current_cycle_learning_proposals", []) or [])
    ]
    model_name = str(
        snapshot.get("model_name")
        or getattr(controller, "model_name", "")
        or ""
    )
    active_config_ref = normalize_config_ref(
        snapshot.get("active_config_ref")
        or getattr(controller, "model_config_path", "")
        or ""
    )
    active_version_id = str(snapshot.get("active_version_id") or "")
    runtime_fingerprint = str(
        snapshot.get("runtime_fingerprint")
        or snapshot.get("active_runtime_fingerprint")
        or ""
    )
    bundle_signature = {
        "cycle_id": int(cycle_id),
        "model_name": model_name,
        "active_config_ref": active_config_ref,
        "active_version_id": active_version_id,
        "runtime_fingerprint": runtime_fingerprint,
        "proposal_ids": [
            str(dict(item).get("proposal_id") or "")
            for item in proposal_items
        ],
    }
    proposal_bundle_id = f"proposal_bundle_{int(cycle_id):04d}_{stable_hash(bundle_signature)[:8]}"
    payload = {
        "schema_version": "training.proposal_bundle.v1",
        "proposal_bundle_id": proposal_bundle_id,
        "cycle_id": int(cycle_id),
        "created_at": datetime.now().isoformat(),
        "model_name": model_name,
        "active_config_ref": active_config_ref,
        "active_version_id": active_version_id,
        "runtime_fingerprint": runtime_fingerprint,
        "execution_snapshot": snapshot,
        "proposal_count": len(proposal_items),
        "proposal_ids": [
            str(dict(item).get("proposal_id") or "")
            for item in proposal_items
        ],
        "proposals": proposal_items,
        "suggestion_tracking_summary": build_suggestion_tracking_summary(proposal_items),
    }
    path = _proposal_store_dir(controller) / f"cycle_{int(cycle_id):04d}_{proposal_bundle_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    payload["bundle_path"] = str(path)
    setattr(controller, "last_cycle_proposal_bundle", deepcopy(payload))
    return payload


def load_cycle_proposal_bundle(bundle_path: str | Path) -> dict[str, Any]:
    path = Path(bundle_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["proposals"] = [
        ensure_proposal_tracking_fields(dict(item or {}), default_cycle_id=int(payload.get("cycle_id") or 0))
        for item in list(payload.get("proposals") or [])
    ]
    payload["suggestion_tracking_summary"] = build_suggestion_tracking_summary(
        list(payload.get("proposals") or [])
    )
    payload["bundle_path"] = str(path)
    return payload


def list_cycle_proposal_bundles(base: Any, *, limit: int | None = None) -> list[dict[str, Any]]:
    items = [
        load_cycle_proposal_bundle(path)
        for path in sorted(_proposal_store_dir(base).glob("cycle_*.json"))
    ]
    if limit is not None:
        return items[-int(limit):]
    return items


def update_cycle_proposal_bundle(
    controller: Any | None,
    *,
    bundle_path: str | Path,
    proposals: list[dict[str, Any]] | None = None,
    extra_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = load_cycle_proposal_bundle(bundle_path)
    cycle_id = int(payload.get("cycle_id") or 0)
    if proposals is not None:
        payload["proposals"] = [
            ensure_proposal_tracking_fields(dict(item or {}), default_cycle_id=cycle_id)
            for item in list(proposals or [])
        ]
        payload["proposal_count"] = len(list(payload.get("proposals") or []))
        payload["proposal_ids"] = [
            str(dict(item).get("proposal_id") or "")
            for item in list(payload.get("proposals") or [])
        ]
    payload["suggestion_tracking_summary"] = build_suggestion_tracking_summary(
        list(payload.get("proposals") or [])
    )
    for key, value in dict(extra_fields or {}).items():
        payload[key] = deepcopy(value)

    path = Path(bundle_path)
    persisted = dict(payload)
    persisted.pop("bundle_path", None)
    path.write_text(json.dumps(persisted, ensure_ascii=False, indent=2), encoding="utf-8")
    payload["bundle_path"] = str(path)
    if controller is not None:
        setattr(controller, "last_cycle_proposal_bundle", deepcopy(payload))
    return payload
