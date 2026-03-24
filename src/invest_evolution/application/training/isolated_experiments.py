from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from invest_evolution.config import config, normalize_date


DEFAULT_REVIEW_WINDOW = {"mode": "rolling", "size": 5}
DEFAULT_STEP_DAYS = 30
DEFAULT_WARMUP_WINDOWS = 3


@dataclass(frozen=True)
class IsolatedExperimentPreset:
    name: str
    manager_id: str
    target_regime: str
    label: str


ISOLATED_EXPERIMENT_PRESETS: dict[str, IsolatedExperimentPreset] = {
    "defensive_low_vol@bear": IsolatedExperimentPreset(
        name="defensive_low_vol@bear",
        manager_id="defensive_low_vol",
        target_regime="bear",
        label="isolated_defensive_low_vol_bear",
    ),
    "mean_reversion@oscillation": IsolatedExperimentPreset(
        name="mean_reversion@oscillation",
        manager_id="mean_reversion",
        target_regime="oscillation",
        label="isolated_mean_reversion_oscillation",
    ),
}


def resolve_isolated_experiment_preset(value: str) -> IsolatedExperimentPreset:
    key = str(value or "").strip()
    if key not in ISOLATED_EXPERIMENT_PRESETS:
        available = ", ".join(sorted(ISOLATED_EXPERIMENT_PRESETS))
        raise ValueError(f"unknown isolated experiment preset: {key!r}; expected one of: {available}")
    return ISOLATED_EXPERIMENT_PRESETS[key]


def resolve_ready_anchor_date(
    controller: Any,
    *,
    step_days: int = DEFAULT_STEP_DAYS,
    warmup_windows: int = DEFAULT_WARMUP_WINDOWS,
    min_date: str | None = None,
    max_date: str | None = None,
) -> str | None:
    normalized_min_date = normalize_date(
        str(min_date or getattr(controller, "experiment_min_date", None) or "20180101")
    )
    normalized_max_date = normalize_date(str(max_date)) if str(max_date or "").strip() else ""
    target_stock_count = max(1, int(getattr(config, "max_stocks", 50) or 50))
    min_history_days = max(
        30,
        int(
            getattr(controller, "experiment_min_history_days", None)
            or getattr(config, "min_history_days", 200)
            or 200
        ),
    )
    probe_days = max(1, int(step_days or DEFAULT_STEP_DAYS))
    data_manager = getattr(controller, "data_manager", None)
    readiness = getattr(data_manager, "check_training_readiness", None)
    if not callable(readiness):
        return None

    try:
        initial = readiness(
            normalized_min_date,
            stock_count=target_stock_count,
            min_history_days=min_history_days,
        )
    except Exception:
        return None
    initial_payload = dict(initial) if isinstance(initial, dict) else {}

    normalized_warmup_windows = max(0, int(warmup_windows or 0))
    if bool(initial_payload.get("ready")) and normalized_warmup_windows == 0:
        return normalized_min_date

    if not normalized_max_date:
        normalized_max_date = normalize_date(
            str(dict(initial_payload.get("date_range") or {}).get("max") or "")
        )
    if not normalized_max_date:
        return None

    start_dt = datetime.strptime(normalized_min_date, "%Y%m%d") + timedelta(
        days=probe_days * normalized_warmup_windows
    )
    min_dt = datetime.strptime(normalized_min_date, "%Y%m%d")
    if start_dt < min_dt:
        start_dt = min_dt
    cursor = start_dt
    end_dt = datetime.strptime(normalized_max_date, "%Y%m%d")
    while cursor <= end_dt:
        candidate = cursor.strftime("%Y%m%d")
        try:
            diagnostics = readiness(
                candidate,
                stock_count=target_stock_count,
                min_history_days=min_history_days,
            )
        except Exception:
            cursor += timedelta(days=probe_days)
            continue
        diagnostics_payload = dict(diagnostics) if isinstance(diagnostics, dict) else {}
        if bool(diagnostics_payload.get("ready")):
            return candidate
        cursor += timedelta(days=probe_days)
    return None


def discover_isolated_regime_dates(
    controller: Any,
    *,
    manager_id: str,
    target_regime: str,
    step_days: int = DEFAULT_STEP_DAYS,
    warmup_windows: int = DEFAULT_WARMUP_WINDOWS,
    min_date: str | None = None,
    max_date: str | None = None,
    max_dates: int = 10,
    min_history_days: int | None = None,
) -> dict[str, Any]:
    normalized_manager_id = str(manager_id or "").strip()
    if not normalized_manager_id:
        raise ValueError("manager_id is required")
    normalized_target_regime = str(target_regime or "").strip().lower()
    if not normalized_target_regime:
        raise ValueError("target_regime is required")

    target_stock_count = max(1, int(getattr(config, "max_stocks", 50) or 50))
    resolved_min_history_days = max(
        30,
        int(
            min_history_days
            or getattr(controller, "experiment_min_history_days", None)
            or getattr(config, "min_history_days", 200)
            or 200
        ),
    )
    normalized_min_date = normalize_date(
        str(min_date or getattr(controller, "experiment_min_date", None) or "20180101")
    )
    normalized_max_date = normalize_date(str(max_date)) if str(max_date or "").strip() else ""

    data_manager = getattr(controller, "data_manager", None)
    readiness = getattr(data_manager, "check_training_readiness", None)
    readiness_payload: dict[str, Any] = {}
    if callable(readiness):
        try:
            raw_readiness = readiness(
                normalized_min_date,
                stock_count=target_stock_count,
                min_history_days=resolved_min_history_days,
            )
        except Exception as exc:
            readiness_payload = {"ready": False, "error": str(exc)}
        else:
            readiness_payload = dict(raw_readiness) if isinstance(raw_readiness, dict) else {}

    if not normalized_max_date:
        normalized_max_date = normalize_date(
            str(dict(readiness_payload.get("date_range") or {}).get("max") or "")
        )
    anchor_date = resolve_ready_anchor_date(
        controller,
        step_days=step_days,
        warmup_windows=warmup_windows,
        min_date=normalized_min_date,
        max_date=normalized_max_date or None,
    )
    resolved_anchor_date = normalize_date(anchor_date or normalized_min_date)
    if not normalized_max_date:
        normalized_max_date = resolved_anchor_date

    cursor = datetime.strptime(resolved_anchor_date, "%Y%m%d")
    end_dt = datetime.strptime(normalized_max_date, "%Y%m%d")
    matched_dates: list[str] = []
    probes: list[dict[str, Any]] = []
    allowed_manager_ids = [normalized_manager_id]
    while cursor <= end_dt and len(matched_dates) < max(1, int(max_dates or 1)):
        cutoff_date = cursor.strftime("%Y%m%d")
        probe: dict[str, Any] = {"cutoff_date": cutoff_date}
        try:
            preview = controller.preview_governance(
                cutoff_date=cutoff_date,
                stock_count=target_stock_count,
                min_history_days=resolved_min_history_days,
                allowed_manager_ids=allowed_manager_ids,
            )
            preview_payload = dict(preview) if isinstance(preview, dict) else {}
            regime = str(preview_payload.get("regime") or "unknown").strip().lower()
            confidence = float(
                preview_payload.get("regime_confidence")
                or preview_payload.get("confidence")
                or 0.0
            )
            probe.update(
                {
                    "regime": regime,
                    "regime_confidence": confidence,
                    "matched": regime == normalized_target_regime,
                }
            )
            if regime == normalized_target_regime:
                matched_dates.append(cutoff_date)
        except Exception as exc:
            probe.update({"regime": "error", "regime_confidence": 0.0, "matched": False, "error": str(exc)})
        probes.append(probe)
        cursor += timedelta(days=max(1, int(step_days or DEFAULT_STEP_DAYS)))

    return {
        "schema_version": "training.isolated_regime_date_discovery.v1",
        "manager_id": normalized_manager_id,
        "allowed_manager_ids": allowed_manager_ids,
        "target_regime": normalized_target_regime,
        "step_days": max(1, int(step_days or DEFAULT_STEP_DAYS)),
        "warmup_windows": max(0, int(warmup_windows or 0)),
        "min_date": normalized_min_date,
        "max_date": normalized_max_date,
        "anchor_date": resolved_anchor_date,
        "target_stock_count": target_stock_count,
        "min_history_days": resolved_min_history_days,
        "matched_dates": matched_dates,
        "matched_count": len(matched_dates),
        "probes": probes,
        "readiness": readiness_payload,
    }


def build_isolated_experiment_spec(
    *,
    manager_id: str,
    cutoff_dates: list[str],
    llm_dry_run: bool = False,
    shadow_mode: bool = True,
) -> dict[str, Any]:
    normalized_manager_id = str(manager_id or "").strip()
    if not normalized_manager_id:
        raise ValueError("manager_id is required")
    normalized_cutoff_dates = [
        normalize_date(str(item))
        for item in list(cutoff_dates or [])
        if str(item or "").strip()
    ]
    if not normalized_cutoff_dates:
        raise ValueError("at least one cutoff date is required")

    spec: dict[str, Any] = {
        "protocol": {
            "shadow_mode": bool(shadow_mode),
            "review_window": dict(DEFAULT_REVIEW_WINDOW),
            "cutoff_policy": {
                "mode": "sequence",
                "dates": normalized_cutoff_dates,
            },
        },
        "manager_scope": {
            "allowed_manager_ids": [normalized_manager_id],
        },
    }
    if llm_dry_run:
        spec["llm"] = {"dry_run": True}
    return spec
