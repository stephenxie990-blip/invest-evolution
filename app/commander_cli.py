"""CLI parser and command dispatch helpers for commander."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from typing import Any


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Unified Commander for Invest Evolution")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_common_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--workspace", help="Workspace path for commander runtime")
        p.add_argument("--strategy-dir", help="Strategy gene directory (md/json/py)")
        p.add_argument("--model", help="LLM model id, e.g. minimax/MiniMax-M2.5-highspeed")
        p.add_argument("--api-key", help="LLM API key")
        p.add_argument("--api-base", help="LLM API base URL")
        p.add_argument("--mock", action="store_true", help="Enable mock data mode")

    p_run = sub.add_parser("run", help="Start 24/7 commander daemon")
    add_common_args(p_run)
    p_run.add_argument("--interactive", action="store_true", help="Enable stdin chat while daemon runs")
    p_run.add_argument("--no-autopilot", action="store_true", help="Disable periodic auto-training")
    p_run.add_argument("--no-heartbeat", action="store_true", help="Disable heartbeat loop")
    p_run.add_argument("--train-interval-sec", type=int, help="Autopilot interval seconds")
    p_run.add_argument("--heartbeat-interval-sec", type=int, help="Heartbeat interval seconds")

    p_status = sub.add_parser("status", help="Print commander status snapshot")
    add_common_args(p_status)
    p_status.add_argument("--detail", choices=["fast", "slow"], default="fast", help="Status detail mode")

    p_train = sub.add_parser("train-once", help="Run training cycles once")
    add_common_args(p_train)
    p_train.add_argument("--rounds", type=int, default=1, help="Number of cycles to run")

    p_ask = sub.add_parser("ask", help="Send one message to fused commander brain")
    add_common_args(p_ask)
    p_ask.add_argument("-m", "--message", required=True, help="User message")

    p_genes = sub.add_parser("strategies", help="List strategy genes")
    add_common_args(p_genes)
    p_genes.add_argument("--reload", action="store_true", help="Reload strategy genes from disk")
    p_genes.add_argument("--only-enabled", action="store_true", help="Show only enabled genes")

    return parser


async def run_async(
    args: argparse.Namespace,
    *,
    config_cls: Any,
    runtime_cls: Any,
) -> int:
    cfg = config_cls.from_args(args)
    runtime = runtime_cls(cfg)

    if args.cmd == "status":
        print(json.dumps(runtime.status(detail=getattr(args, "detail", "fast")), ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "train-once":
        out = await runtime.train_once(rounds=max(1, int(args.rounds)), mock=cfg.mock_mode)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "ask":
        reply = await runtime.ask(args.message, session_key="cli:direct", channel="cli", chat_id="direct")
        print(reply)
        return 0

    if args.cmd == "strategies":
        if args.reload:
            runtime.reload_strategies()
        genes = runtime.strategy_registry.list_genes(only_enabled=bool(args.only_enabled))
        print(
            json.dumps(
                {"count": len(genes), "items": [g.to_dict() for g in genes]},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.cmd == "run":
        try:
            await runtime.serve_forever(interactive=bool(args.interactive))
            return 0
        finally:
            await runtime.stop()

    raise ValueError(f"Unknown command: {args.cmd}")


def run_cli_main(
    *,
    config_cls: Any,
    runtime_cls: Any,
) -> int:
    parser = build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    try:
        return asyncio.run(run_async(args, config_cls=config_cls, runtime_cls=runtime_cls))
    except KeyboardInterrupt:
        return 130
