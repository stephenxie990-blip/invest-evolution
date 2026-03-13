"""Mutation-oriented runtime helpers for commander entry methods."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable


def reload_strategies_response(
    runtime: Any,
    *,
    ensure_runtime_storage: Callable[[], None],
    begin_task: Callable[..., None],
    set_runtime_state: Callable[[str], None],
    write_commander_identity: Callable[[], None],
    complete_runtime_task: Callable[..., None],
    attach_domain_mutating_workflow: Callable[..., dict[str, Any]],
    reloading_state: str,
    idle_state: str,
    ok_status: str,
) -> dict[str, Any]:
    ensure_runtime_storage()
    begin_task("reload_strategies", "direct")
    set_runtime_state(reloading_state)
    runtime.strategy_registry.ensure_default_templates()
    genes = runtime.strategy_registry.reload()
    write_commander_identity()
    complete_runtime_task(state=idle_state, status=ok_status, gene_count=len(genes))
    return attach_domain_mutating_workflow(
        {
            "status": ok_status,
            "count": len(genes),
            "genes": [gene.to_dict() for gene in genes],
        },
        domain="strategy",
        operation="reload_strategies",
        runtime_tool="invest_reload_strategies",
        phase="strategy_reload",
        phase_stats={"gene_count": len(genes)},
    )


def add_cron_job_response(
    runtime: Any,
    *,
    name: str,
    message: str,
    every_sec: int,
    deliver: bool,
    channel: str,
    to: str,
    persist_state: Callable[[], None],
    attach_domain_mutating_workflow: Callable[..., dict[str, Any]],
    ok_status: str,
) -> dict[str, Any]:
    job = runtime.cron.add_job(
        name=name,
        message=message,
        every_sec=int(every_sec),
        deliver=bool(deliver),
        channel=str(channel),
        to=str(to),
    )
    persist_state()
    return attach_domain_mutating_workflow(
        {"status": ok_status, "job": job.to_dict()},
        domain="scheduler",
        operation="add_cron_job",
        runtime_tool="invest_cron_add",
        phase="cron_add",
        phase_stats={"job_id": getattr(job, "id", ""), "every_sec": int(every_sec)},
    )


def list_cron_jobs_response(
    runtime: Any,
    *,
    attach_domain_readonly_workflow: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    rows = [job.to_dict() for job in runtime.cron.list_jobs()]
    return attach_domain_readonly_workflow(
        {"count": len(rows), "items": rows},
        domain="scheduler",
        operation="list_cron_jobs",
        runtime_tool="invest_cron_list",
        phase="cron_list",
        phase_stats={"count": len(rows)},
    )


def remove_cron_job_response(
    runtime: Any,
    *,
    job_id: str,
    persist_state: Callable[[], None],
    attach_domain_mutating_workflow: Callable[..., dict[str, Any]],
    ok_status: str,
    not_found_status: str,
) -> dict[str, Any]:
    removed = runtime.cron.remove_job(str(job_id))
    persist_state()
    return attach_domain_mutating_workflow(
        {"status": ok_status if removed else not_found_status, "job_id": str(job_id)},
        domain="scheduler",
        operation="remove_cron_job",
        runtime_tool="invest_cron_remove",
        phase="cron_remove",
        phase_stats={"job_id": str(job_id), "removed": bool(removed)},
    )


def reload_plugins_response(
    runtime: Any,
    *,
    ensure_runtime_storage: Callable[[], None],
    load_plugins: Callable[..., dict[str, Any]],
    attach_domain_mutating_workflow: Callable[..., dict[str, Any]],
    ok_status: str,
) -> dict[str, Any]:
    ensure_runtime_storage()
    payload = load_plugins(persist=True)
    return attach_domain_mutating_workflow(
        {"status": ok_status, **payload},
        domain="plugin",
        operation="reload_plugins",
        runtime_tool="invest_plugins_reload",
        phase="plugin_reload",
        phase_stats={"plugin_count": int(payload.get("count", 0) or 0)},
    )


async def serve_forever_loop(
    *,
    start_runtime: Callable[[], Awaitable[None]],
    ask_runtime: Callable[..., Awaitable[str]],
    interactive: bool,
    input_func: Callable[[str], str] = input,
    print_func: Callable[..., None] = print,
    sleep_func: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    await start_runtime()

    if interactive:
        print_func("Commander interactive mode. Type 'exit' to quit.")
        while True:
            line = await asyncio.to_thread(input_func, "commander> ")
            cmd = line.strip()
            if not cmd:
                continue
            if cmd.lower() in {"exit", "quit", "/exit", ":q"}:
                break
            reply = await ask_runtime(cmd, session_key="cli:commander", channel="cli", chat_id="commander")
            print_func(reply)
        return

    while True:
        await sleep_func(1)
