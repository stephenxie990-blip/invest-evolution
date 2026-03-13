"""CLI parser and command dispatch helpers for commander."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import queue
import sys
from typing import Any

from app.commander_support.presentation import build_human_display


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
    p_status.add_argument(
        "--view",
        choices=["auto", "human", "json"],
        default="auto",
        help="CLI output view: auto prints human summary on TTY, json keeps full payload",
    )

    p_train = sub.add_parser("train-once", help="Run training cycles once")
    add_common_args(p_train)
    p_train.add_argument("--rounds", type=int, default=1, help="Number of cycles to run")
    p_train.add_argument(
        "--view",
        choices=["auto", "human", "json"],
        default="auto",
        help="CLI output view: auto prints human summary on TTY, json keeps full payload",
    )

    p_ask = sub.add_parser("ask", help="Send one message to fused commander brain")
    add_common_args(p_ask)
    p_ask.add_argument("-m", "--message", required=True, help="User message")
    p_ask.add_argument(
        "--view",
        choices=["auto", "human", "json"],
        default="auto",
        help="CLI output view: auto prints human receipt on TTY, json keeps full payload",
    )
    p_ask.add_argument(
        "--stream-events",
        action="store_true",
        help="Stream session-bound runtime events before printing the final reply",
    )

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
    def emit_payload(payload: Any) -> None:
        selected_view = str(getattr(args, "view", "auto"))
        if selected_view == "json":
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return
        display = build_human_display(payload if isinstance(payload, dict) else {})
        if selected_view == "human" or (selected_view == "auto" and sys.stdout.isatty() and display.get("available")):
            print(str(display.get("text") or json.dumps(payload, ensure_ascii=False, indent=2)))
            return
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    cfg = config_cls.from_args(args)
    runtime = runtime_cls(cfg)

    if args.cmd == "status":
        emit_payload(runtime.status(detail=getattr(args, "detail", "fast")))
        return 0

    if args.cmd == "train-once":
        out = await runtime.train_once(rounds=max(1, int(args.rounds)), mock=cfg.mock_mode)
        emit_payload(out)
        return 0

    if args.cmd == "ask":
        session_key = "cli:direct"
        chat_id = "direct"
        reply = ""
        summary_packet = None
        if bool(getattr(args, "stream_events", False)):
            request_id = runtime.new_request_id()
            subscription_id, event_queue = runtime.subscribe_event_stream(
                session_key=session_key,
                chat_id=chat_id,
                request_id=request_id,
            )
            try:
                task = asyncio.create_task(
                    runtime.ask(
                        args.message,
                        session_key=session_key,
                        channel="cli",
                        chat_id=chat_id,
                        request_id=request_id,
                    )
                )
                while True:
                    try:
                        packet = await asyncio.to_thread(event_queue.get, True, 0.2)
                    except queue.Empty:
                        if task.done():
                            break
                        continue
                    text = str(
                        packet.get("display_text")
                        or packet.get("human_reply")
                        or packet.get("broadcast_text")
                        or packet.get("label")
                        or packet.get("event")
                        or ""
                    ).strip()
                    if text:
                        print(text, flush=True)
                summary_packet = runtime.build_stream_summary_packet(subscription_id)
                summary_text = str(summary_packet.get("display_text") or "").strip()
                if summary_text:
                    print(summary_text, flush=True)
                reply = await task
            finally:
                runtime.unsubscribe_event_stream(subscription_id)
        else:
            reply = await runtime.ask(args.message, session_key=session_key, channel="cli", chat_id=chat_id)
        if str(getattr(args, "view", "auto")) == "json":
            print(reply)
            return 0
        try:
            payload = json.loads(reply) if isinstance(reply, str) else dict(reply or {})
        except Exception:
            payload = None
        if isinstance(payload, dict) and summary_packet:
            payload = runtime.merge_stream_summary_into_reply_payload(payload, summary_packet)
        display = build_human_display(payload) if isinstance(payload, dict) else {"available": False}
        selected_view = str(getattr(args, "view", "auto"))
        if selected_view == "human" or (selected_view == "auto" and sys.stdout.isatty() and display.get("available")):
            print(str(display.get("text") or reply))
        else:
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
