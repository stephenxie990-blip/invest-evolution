from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from config import PROJECT_ROOT, AgentConfigRegistry, agent_config_registry, config
from config.control_plane import ControlPlaneConfigService
from config.services import EvolutionConfigService, RuntimePathConfigService
from invest.allocator import build_allocation_plan
from invest.leaderboard import write_leaderboard
from invest.models import list_models
from market_data.gateway import MarketDataGateway
from market_data.services import MarketQueryService


@dataclass
class DataDownloadJob:
    status: str = "idle"
    started_at: str = ""
    finished_at: str = ""
    error: str = ""


_DATA_DOWNLOAD_LOCK = threading.Lock()
_DATA_DOWNLOAD_JOB = DataDownloadJob()


def get_investment_models_payload(runtime: Any) -> dict[str, Any]:
    controller = runtime.body.controller
    items = list_models()
    return {
        "count": len(items),
        "items": items,
        "active_model": getattr(controller, "model_name", "momentum"),
        "active_config": getattr(controller, "model_config_path", ""),
        "routing": {
            "enabled": bool(getattr(controller, "model_routing_enabled", False)),
            "mode": str(getattr(controller, "model_routing_mode", "off") or "off"),
            "allowed_models": list(getattr(controller, "model_routing_allowed_models", []) or []),
            "last_decision": dict(getattr(controller, "last_routing_decision", {}) or {}),
        },
    }


def get_leaderboard_payload(runtime: Any) -> dict[str, Any]:
    root_dir = Path(runtime.cfg.training_output_dir).parent
    return write_leaderboard(root_dir)


def get_allocator_payload(runtime: Any, *, regime: str = "oscillation", top_n: int = 3, as_of_date: str | None = None) -> dict[str, Any]:
    root_dir = Path(runtime.cfg.training_output_dir).parent
    leaderboard = write_leaderboard(root_dir)
    leaderboard_path = root_dir / "leaderboard.json"
    plan = build_allocation_plan(
        regime,
        leaderboard_path,
        as_of_date=as_of_date or datetime.now().strftime("%Y%m%d"),
        top_n=max(1, min(4, int(top_n or 3))),
    )
    return {
        "leaderboard_generated_at": leaderboard.get("generated_at"),
        "allocation": plan.to_dict(),
    }


def get_model_routing_preview_payload(
    runtime: Any,
    *,
    cutoff_date: str | None = None,
    stock_count: int | None = None,
    min_history_days: int | None = None,
    allowed_models: list[str] | None = None,
) -> dict[str, Any]:
    controller = runtime.body.controller
    payload = controller.preview_model_routing(
        cutoff_date=cutoff_date,
        stock_count=stock_count,
        min_history_days=min_history_days,
        allowed_models=allowed_models or None,
    )
    return {"status": "ok", "routing": payload}


def list_agent_prompts_payload(*, project_root: Path | None = None) -> dict[str, Any]:
    registry = _resolve_agent_registry(project_root or PROJECT_ROOT)
    items = []
    for cfg in registry.list_configs():
        name = str(cfg.get("name", "") or "").strip()
        if not name:
            continue
        items.append({
            "name": name,
            "role": str(cfg.get("role", name) or name),
            "system_prompt": str(cfg.get("system_prompt", "") or ""),
        })
    return {"status": "ok", "configs": items}


def _resolve_agent_registry(project_root: Path | None = None):
    root = Path(project_root or PROJECT_ROOT)
    target = (root / "agent_settings" / "agents_config.json").resolve()
    current = Path(agent_config_registry.json_path).resolve()
    if current == target:
        return agent_config_registry
    return AgentConfigRegistry(target)


def update_agent_prompt_payload(*, agent_name: str, system_prompt: str, project_root: Path | None = None) -> dict[str, Any]:
    registry = _resolve_agent_registry(project_root)
    current_cfg = dict(registry.get_config(agent_name) or {})
    current_cfg["system_prompt"] = str(system_prompt or "")
    ok = registry.save_config(agent_name, current_cfg)
    if not ok:
        raise RuntimeError("failed to persist agent config")
    if registry is not agent_config_registry:
        agent_config_registry.reload()
    return {
        "status": "ok",
        "updated": [f"agent_prompts.{agent_name}.system_prompt"],
        "restart_required": False,
    }


def get_runtime_paths_payload(runtime: Any | None = None, *, project_root: Path | None = None) -> dict[str, Any]:
    service = RuntimePathConfigService(project_root=project_root or PROJECT_ROOT)
    payload = service.get_payload()
    if runtime is not None:
        payload.update({
            "training_output_dir": str(runtime.cfg.training_output_dir),
            "meeting_log_dir": str(runtime.cfg.meeting_log_dir),
            "config_audit_log_path": str(runtime.cfg.config_audit_log_path),
            "config_snapshot_dir": str(runtime.cfg.config_snapshot_dir),
            "runtime_loaded": True,
        })
    else:
        payload["runtime_loaded"] = False
    return {"status": "ok", "config": payload}


def update_runtime_paths_payload(*, patch: dict[str, Any], runtime: Any | None = None, project_root: Path | None = None, sync_runtime: Any | None = None) -> dict[str, Any]:
    service = RuntimePathConfigService(project_root=project_root or PROJECT_ROOT)
    payload = service.apply_patch(patch)
    if runtime is not None and sync_runtime is not None:
        sync_runtime(runtime, payload["config"])
        payload["config"].update({
            "training_output_dir": str(runtime.cfg.training_output_dir),
            "meeting_log_dir": str(runtime.cfg.meeting_log_dir),
            "config_audit_log_path": str(runtime.cfg.config_audit_log_path),
            "config_snapshot_dir": str(runtime.cfg.config_snapshot_dir),
            "runtime_loaded": True,
        })
    else:
        payload["config"]["runtime_loaded"] = False
    return {"status": "ok", "updated": payload["updated"], "config": payload["config"]}


def get_evolution_config_payload(*, project_root: Path | None = None, live_config: Any = None) -> dict[str, Any]:
    service = EvolutionConfigService(project_root=project_root or PROJECT_ROOT, live_config=live_config or config)
    return {"status": "ok", "config": dict(service.get_masked_payload())}


def update_evolution_config_payload(*, patch: dict[str, Any], project_root: Path | None = None, live_config: Any = None, source: str = "commander") -> dict[str, Any]:
    forbidden_keys = {"llm_fast_model", "llm_deep_model", "llm_api_base", "llm_api_key"}
    touched = sorted(key for key in forbidden_keys if key in patch)
    if touched:
        raise ValueError("LLM 配置已迁移到 /api/control_plane；/api/evolution_config 仅保留训练参数")
    service = EvolutionConfigService(project_root=project_root or PROJECT_ROOT, live_config=live_config or config)
    payload = service.apply_patch(patch, source=source)
    return {"status": "ok", "updated": payload["updated"], "config": dict(payload["config"]), "restart_required": False}


def get_control_plane_payload(*, project_root: Path | None = None) -> dict[str, Any]:
    service = ControlPlaneConfigService(project_root=project_root or PROJECT_ROOT)
    return {
        "status": "ok",
        "config": service.get_masked_payload(),
        "restart_required": False,
        "config_path": str(service.config_path),
        "local_override_path": str(service.local_override_path),
        "audit_log_path": str(service.audit_log_path),
        "snapshot_dir": str(service.snapshot_dir),
    }


def update_control_plane_payload(*, patch: dict[str, Any], project_root: Path | None = None, source: str = "commander") -> dict[str, Any]:
    service = ControlPlaneConfigService(project_root=project_root or PROJECT_ROOT)
    payload = service.apply_patch(patch, source=source)
    return {"status": "ok", "updated": payload["updated"], "config": payload["config"], "restart_required": True}


def get_data_status_payload(*, refresh: bool = False) -> dict[str, Any]:
    return MarketQueryService().get_status_summary(refresh=refresh)


def get_capital_flow_payload(*, codes: list[str] | None = None, start_date: str | None = None, end_date: str | None = None, limit: int = 200) -> dict[str, Any]:
    frame = MarketQueryService().get_capital_flow(codes=codes, start_date=start_date, end_date=end_date)
    if not frame.empty:
        frame = frame.head(max(1, min(int(limit), 5000)))
    return {"count": int(len(frame)), "items": frame.to_dict(orient="records")}


def get_dragon_tiger_payload(*, codes: list[str] | None = None, start_date: str | None = None, end_date: str | None = None, limit: int = 200) -> dict[str, Any]:
    frame = MarketQueryService().get_dragon_tiger_events(codes=codes, start_date=start_date, end_date=end_date)
    if not frame.empty:
        frame = frame.head(max(1, min(int(limit), 5000)))
    return {"count": int(len(frame)), "items": frame.to_dict(orient="records")}


def get_intraday_60m_payload(*, codes: list[str] | None = None, start_date: str | None = None, end_date: str | None = None, limit: int = 500) -> dict[str, Any]:
    frame = MarketQueryService().get_intraday_60m_bars(codes=codes, start_date=start_date, end_date=end_date)
    if not frame.empty:
        frame = frame.head(max(1, min(int(limit), 10000)))
    return {"count": int(len(frame)), "items": frame.to_dict(orient="records")}


def get_data_download_status_payload() -> dict[str, Any]:
    return {
        "status": _DATA_DOWNLOAD_JOB.status,
        "started_at": _DATA_DOWNLOAD_JOB.started_at,
        "finished_at": _DATA_DOWNLOAD_JOB.finished_at,
        "error": _DATA_DOWNLOAD_JOB.error,
    }


def trigger_data_download() -> dict[str, Any]:
    def _do_download() -> None:
        global _DATA_DOWNLOAD_JOB
        try:
            _DATA_DOWNLOAD_JOB.status = "running"
            _DATA_DOWNLOAD_JOB.started_at = datetime.now().isoformat()
            _DATA_DOWNLOAD_JOB.finished_at = ""
            _DATA_DOWNLOAD_JOB.error = ""
            MarketDataGateway().sync_background_full_refresh()
            _DATA_DOWNLOAD_JOB.status = "ok"
            _DATA_DOWNLOAD_JOB.finished_at = datetime.now().isoformat()
        except Exception as exc:  # pragma: no cover
            _DATA_DOWNLOAD_JOB.status = "error"
            _DATA_DOWNLOAD_JOB.error = str(exc)
            _DATA_DOWNLOAD_JOB.finished_at = datetime.now().isoformat()
        finally:
            pass

    with _DATA_DOWNLOAD_LOCK:
        if _DATA_DOWNLOAD_JOB.status == "running":
            return {"status": "running", "message": "后台同步已在运行"}
        thread = threading.Thread(target=_do_download, daemon=True)
        thread.start()
    return {"status": "started", "message": "后台同步任务已启动", "job": get_data_download_status_payload()}


def read_json_file(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
