import argparse

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


def test_cli_parser_accepts_view_flag():
    from app.commander_support.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["ask", "-m", "你好", "--view", "human"])

    assert isinstance(args, argparse.Namespace)
    assert args.cmd == "ask"
    assert args.view == "human"


def test_cli_parser_status_and_train_accept_view_flag():
    from app.commander_support.cli import build_parser

    parser = build_parser()
    status_args = parser.parse_args(["status", "--view", "human"])
    train_args = parser.parse_args(["train-once", "--view", "json"])

    assert status_args.view == "human"
    assert train_args.view == "json"
