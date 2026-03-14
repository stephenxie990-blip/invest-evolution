from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)


def artifact_read_roots(runtime: Any) -> list[Path]:
    if runtime is None or not hasattr(runtime, "cfg"):
        return []
    cfg = runtime.cfg
    roots = [
        Path(cfg.training_output_dir),
        Path(cfg.meeting_log_dir),
        Path(cfg.config_snapshot_dir),
        Path(cfg.config_audit_log_path).parent,
        Path(cfg.training_plan_dir),
        Path(cfg.training_run_dir),
        Path(cfg.training_eval_dir),
    ]
    deduped: list[Path] = []
    for root in roots:
        resolved = root.resolve()
        if resolved not in deduped:
            deduped.append(resolved)
    return deduped


def resolve_runtime_artifact_path(runtime: Any, path_str: str) -> Path | None:
    if runtime is None or not hasattr(runtime, "cfg"):
        return None
    raw = str(path_str or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path(runtime.cfg.runtime_state_dir) / path
    try:
        resolved = path.resolve()
    except OSError:
        return None
    for root in artifact_read_roots(runtime):
        try:
            resolved.relative_to(root)
            if resolved.exists() and resolved.is_file():
                return resolved
            return None
        except ValueError:
            continue
    logger.warning("Rejected artifact read outside runtime roots: %s", resolved)
    return None


def safe_read_json(runtime: Any, path_str: str) -> Any:
    path = resolve_runtime_artifact_path(runtime, path_str)
    if path is None:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to read JSON artifact %s: %s", path, exc)
        return None


def safe_read_text(runtime: Any, path_str: str, *, limit: int = 12000) -> str:
    path = resolve_runtime_artifact_path(runtime, path_str)
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8")[:limit]
    except Exception as exc:
        logger.warning("Failed to read text artifact %s: %s", path, exc)
        return ""


def safe_read_jsonl(runtime: Any, path_str: str, *, limit: int = 400) -> list[dict[str, Any]]:
    path = resolve_runtime_artifact_path(runtime, path_str)
    if path is None:
        return []
    rows: list[dict[str, Any]] = []
    invalid_lines = 0
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                invalid_lines += 1
                continue
    except Exception as exc:
        logger.warning("Failed to read JSONL artifact %s: %s", path, exc)
        return []
    if invalid_lines:
        logger.warning("Skipped %d invalid JSONL row(s) while reading %s", invalid_lines, path)
    return rows[-max(1, int(limit)):]
