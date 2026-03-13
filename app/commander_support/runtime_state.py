"""Runtime state and lock helpers for commander."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


def copy_runtime_task(task: dict[str, Any] | None) -> dict[str, Any] | None:
    if task is None:
        return None
    return deepcopy(task)


def build_started_task(task_type: str, source: str, **metadata: Any) -> dict[str, Any]:
    return {
        "type": task_type,
        "source": source,
        "started_at": datetime.now().isoformat(),
        **metadata,
    }


def build_finished_task(
    current_task: dict[str, Any],
    *,
    status: str,
    copy_task: Callable[[dict[str, Any] | None], dict[str, Any] | None],
    **metadata: Any,
) -> dict[str, Any]:
    return {
        **(copy_task(current_task) or {}),
        "finished_at": datetime.now().isoformat(),
        "status": status,
        **metadata,
    }


def apply_restored_body_state(body: Any, body_payload: dict[str, Any]) -> None:
    body.total_cycles = int(body_payload.get("total_cycles") or 0)
    body.success_cycles = int(body_payload.get("success_cycles") or 0)
    body.no_data_cycles = int(body_payload.get("no_data_cycles") or 0)
    body.failed_cycles = int(body_payload.get("failed_cycles") or 0)
    body.last_result = dict(body_payload.get("last_result") or {}) or None
    body.last_error = str(body_payload.get("last_error") or "")
    body.last_run_at = str(body_payload.get("last_run_at") or "")
    body.training_state = str(body_payload.get("training_state") or body.training_state)
    body.current_task = dict(body_payload.get("current_task") or {}) or None
    body.last_completed_task = dict(body_payload.get("last_completed_task") or {}) or None


def read_runtime_lock_payload(lock_file: Path, *, logger: Any) -> dict[str, Any]:
    try:
        raw = lock_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        logger.warning("Failed to read runtime lock payload %s: %s", lock_file, exc)
        return {}

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("Invalid runtime lock payload %s: %s", lock_file, exc)
        return {}

    if not isinstance(data, dict):
        logger.warning("Runtime lock payload must be a JSON object: %s", lock_file)
        return {}
    return data


def is_pid_alive(pid: int, *, os_module: Any) -> bool:
    if pid <= 0:
        return False
    try:
        os_module.kill(pid, 0)
        return True
    except OSError:
        return False


def acquire_runtime_lock(
    *,
    lock_file: Path,
    instance_id: str,
    workspace: str,
    read_lock_payload: Callable[[], dict[str, Any]],
    pid_alive: Callable[[int], bool],
    os_module: Any,
    socket_module: Any,
) -> None:
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": os_module.getpid(),
        "host": socket_module.gethostname(),
        "instance_id": instance_id,
        "started_at": datetime.now().isoformat(),
        "workspace": workspace,
    }

    while True:
        try:
            fd = os_module.open(lock_file, os_module.O_WRONLY | os_module.O_CREAT | os_module.O_EXCL, 0o644)
        except FileExistsError:
            existing = read_lock_payload()
            existing_pid = int(existing.get("pid") or 0)
            if existing_pid and pid_alive(existing_pid):
                raise RuntimeError(
                    f"Commander runtime already active (pid={existing_pid}, host={existing.get('host', '')})"
                )
            if existing and existing_pid:
                try:
                    lock_file.unlink()
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    raise RuntimeError(f"Failed to clear stale runtime lock: {exc}") from exc
                continue
            raise RuntimeError(f"Commander runtime lock exists but is unreadable: {lock_file}")

        try:
            with os_module.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, indent=2))
        except Exception:
            lock_file.unlink(missing_ok=True)
            raise
        return


def release_runtime_lock(
    *,
    lock_file: Path,
    instance_id: str,
    read_lock_payload: Callable[[], dict[str, Any]],
    os_module: Any,
    logger: Any,
) -> None:
    existing = read_lock_payload()
    existing_pid = int(existing.get("pid") or 0)
    existing_instance = str(existing.get("instance_id") or "")
    if not existing or existing_pid == os_module.getpid() or existing_instance == instance_id:
        lock_file.unlink(missing_ok=True)
        return
    logger.warning(
        "Runtime lock ownership changed before release; keeping lock file intact: %s",
        lock_file,
    )
