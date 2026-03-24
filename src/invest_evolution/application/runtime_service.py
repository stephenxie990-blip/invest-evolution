"""Dedicated runtime service entrypoint for split web/runtime deployments."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from typing import Any

from invest_evolution.common.environment import ensure_environment

logger = logging.getLogger(__name__)


def _ensure_runtime_service_environment(*, mock: bool) -> None:
    required_modules = ["pandas"]
    if not mock:
        required_modules.extend(["requests", "rank_bm25"])
    ensure_environment(
        required_modules=required_modules,
        require_project_python=False,
        validate_requests_stack=not mock,
        component="runtime service",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Invest Evolution Commander runtime service")
    parser.add_argument("--workspace", help="Workspace path for commander runtime")
    parser.add_argument("--playbook-dir", help="Commander playbook directory (md/json/py)")
    parser.add_argument("--model", help="LLM model id")
    parser.add_argument("--api-key", help="LLM API key")
    parser.add_argument("--api-base", help="LLM API base URL")
    parser.add_argument("--mock", action="store_true", help="Enable mock data mode")
    parser.add_argument("--no-autopilot", action="store_true", help="Disable periodic auto-training")
    parser.add_argument("--no-heartbeat", action="store_true", help="Disable heartbeat loop")
    parser.add_argument("--train-interval-sec", type=int, help="Autopilot interval seconds")
    parser.add_argument("--heartbeat-interval-sec", type=int, help="Heartbeat interval seconds")
    return parser


def _install_stop_signal_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()

    def _request_stop(signame: str) -> None:
        logger.info("Runtime service received %s, starting graceful shutdown", signame)
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _request_stop, sig.name)
        except NotImplementedError:
            signal.signal(sig, lambda *_args, name=sig.name: _request_stop(name))


async def run_runtime_service_async(
    args: argparse.Namespace,
    *,
    config_cls: Any = None,
    runtime_cls: Any = None,
    install_signal_handlers: bool = True,
    stop_event: asyncio.Event | None = None,
) -> int:
    _ensure_runtime_service_environment(mock=bool(getattr(args, "mock", False)))
    if config_cls is None or runtime_cls is None:
        from invest_evolution.application.commander_main import CommanderConfig, CommanderRuntime

        config_cls = config_cls or CommanderConfig
        runtime_cls = runtime_cls or CommanderRuntime
    cfg = config_cls.from_args(args)
    runtime = runtime_cls(cfg)
    local_stop_event = stop_event or asyncio.Event()
    if install_signal_handlers and stop_event is None:
        _install_stop_signal_handlers(local_stop_event)

    started = False
    try:
        await runtime.start()
        started = True
        await local_stop_event.wait()
        return 0
    finally:
        if started:
            await runtime.stop()


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    try:
        return asyncio.run(run_runtime_service_async(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
