import argparse
import json
import queue

import pytest

from app.commander_support.presentation import build_human_display


def test_build_human_display_prefers_receipt_text():
    payload = {
        "reply": "raw",
        "message": "raw",
        "human_readable": {
            "title": "系统运行摘要",
            "summary": "系统可用",
            "receipt_text": "结论：系统可用",
            "sections": [{"label": "结论", "text": "系统可用"}],
            "suggested_actions": ["继续观察"],
            "recommended_next_step": "继续观察",
            "risk_level": "low",
        },
    }

    display = build_human_display(payload)

    assert display["available"] is True
    assert display["text"] == "结论：系统可用"
    assert display["title"] == "系统运行摘要"
    assert display["sections"][0]["label"] == "结论"


def test_build_human_display_synthesizes_from_feedback():
    payload = {
        "feedback": {
            "summary": "当前任务仍需人工确认后才能视为审计闭环完成。",
            "reason_texts": ["当前操作仍需要人工确认"],
        },
        "next_action": {
            "label": "补充确认后重试",
            "description": "当前任务需要人工确认，建议补充 confirm=true 后重试。",
        },
        "pending": {"rounds": 2, "mock": False},
    }

    display = build_human_display(payload)

    assert display["available"] is True
    assert display["synthesized"] is True
    assert "结论：" in display["text"]
    assert "风险提示：" in display["text"]
    assert "建议动作：" in display["text"]


def test_build_human_display_surfaces_training_promotion_and_lineage_summary():
    payload = {
        "status": "completed",
        "plan_id": "plan_1",
        "training_lab": {
            "run": {
                "run_id": "run_1",
                "ops_panel": {
                    "available": True,
                    "summary": "候选配置已生成，当前仍待发布门确认。",
                    "refs": {
                        "active_config_ref": "configs/active.yaml",
                        "candidate_config_ref": "configs/candidate.yaml",
                        "candidate_meta_ref": "configs/candidate.json",
                    },
                    "review_window": {
                        "mode": "rolling",
                        "size": 3,
                        "cycle_ids": [5, 6, 7],
                    },
                    "fitness_source_cycles": [5, 6, 7],
                    "ops_flags": {
                        "candidate_pending": True,
                        "awaiting_gate": True,
                        "active_candidate_drift": True,
                    },
                    "warnings": [
                        "候选配置仍待发布门确认",
                        "active 与 candidate 配置已发生漂移",
                    ],
                },
                "latest_result": {
                    "cycle_id": 7,
                    "return_pct": 0.8,
                    "promotion_record": {
                        "status": "candidate_generated",
                        "gate_status": "awaiting_gate",
                        "candidate_config_ref": "configs/candidate.yaml",
                    },
                    "lineage_record": {
                        "lineage_status": "candidate_pending",
                        "active_config_ref": "configs/active.yaml",
                        "candidate_config_ref": "configs/candidate.yaml",
                        "review_basis_window": {"mode": "rolling", "size": 3},
                        "fitness_source_cycles": [5, 6, 7],
                    },
                },
            }
        },
    }

    display = build_human_display(payload)

    assert display["available"] is True
    assert "晋升状态：candidate_generated / awaiting_gate" in display["text"]
    assert "lineage：candidate_pending" in display["text"]
    assert "候选配置：configs/candidate.yaml" in display["text"]
    assert "review 窗口：rolling / 3" in display["text"]
    assert "运营关注：候选配置仍待发布门确认" in display["text"]
    assert any(section["label"] == "运营面板" for section in display["sections"])


def test_cli_parser_accepts_view_flag():
    from app.commander_support.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["ask", "-m", "你好", "--view", "human"])

    assert isinstance(args, argparse.Namespace)
    assert args.cmd == "ask"
    assert args.view == "human"


def test_cli_parser_accepts_stream_events_flag():
    from app.commander_support.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["ask", "-m", "你好", "--stream-events"])

    assert args.stream_events is True


def test_cli_parser_status_and_train_accept_view_flag():
    from app.commander_support.cli import build_parser

    parser = build_parser()
    status_args = parser.parse_args(["status", "--view", "human"])
    train_args = parser.parse_args(["train-once", "--view", "json"])

    assert status_args.view == "human"
    assert train_args.view == "json"


@pytest.mark.asyncio
async def test_cli_run_async_streams_events_before_final_reply(capsys):
    from app.commander_support.cli import run_async

    class FakeConfig:
        mock_mode = False

        @classmethod
        def from_args(cls, args):
            return cls()

    class FakeRuntime:
        def __init__(self, cfg):
            self.cfg = cfg
            self._queue = queue.Queue()

        @staticmethod
        def new_request_id():
            return "req:test-cli-stream"

        def subscribe_event_stream(self, **kwargs):
            return "sub:test", self._queue

        def build_stream_summary_packet(self, subscription_id):
            return {
                "display_text": "本次共播报 1 条事件；主要阶段：模块处理；最后播报：模块处理：正在整理运行上下文。。",
            }

        @staticmethod
        def merge_stream_summary_into_reply_payload(payload, summary_packet):
            body = dict(payload or {})
            human = dict(body.get("human_readable") or {})
            human["receipt_text"] = str(human.get("receipt_text") or "") + "\n流式过程摘要：" + str(summary_packet.get("display_text") or "")
            body["human_readable"] = human
            return body

        def unsubscribe_event_stream(self, subscription_id):
            return None

        async def ask(self, message, session_key="cli:direct", channel="cli", chat_id="direct", request_id=""):
            self._queue.put(
                {
                    "event": "module_log",
                    "display_text": "模块处理：正在整理运行上下文。",
                    "human_reply": "模块日志更新：dispatcher / 解析意图 / 正在整理运行上下文。",
                }
            )
            return json.dumps(
                {
                    "status": "ok",
                    "reply": "raw",
                    "message": "raw",
                    "human_readable": {
                        "summary": "系统可用",
                        "receipt_text": "结论：系统可用",
                    },
                },
                ensure_ascii=False,
            )

    args = argparse.Namespace(cmd="ask", message="你好", view="human", stream_events=True)
    exit_code = await run_async(args, config_cls=FakeConfig, runtime_cls=FakeRuntime)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "模块处理：正在整理运行上下文。" in captured.out
    assert "本次共播报 1 条事件" in captured.out
    assert "流式过程摘要：" in captured.out
    assert "结论：系统可用" in captured.out
