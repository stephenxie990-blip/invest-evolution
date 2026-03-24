import argparse
import asyncio

from invest_evolution.application import runtime_service


def test_runtime_service_parser_accepts_common_runtime_flags():
    parser = runtime_service.build_parser()

    args = parser.parse_args(["--mock", "--no-autopilot", "--train-interval-sec", "600"])

    assert args.mock is True
    assert args.no_autopilot is True
    assert args.train_interval_sec == 600


def test_runtime_service_runs_until_stop_event(monkeypatch):
    events = []
    env_calls = []

    class FakeConfig:
        @classmethod
        def from_args(cls, args):
            events.append(("config", getattr(args, "mock", False)))
            return object()

    class FakeRuntime:
        def __init__(self, cfg):
            self.cfg = cfg

        async def start(self):
            events.append(("start", True))

        async def stop(self):
            events.append(("stop", True))

    monkeypatch.setattr(runtime_service, "ensure_environment", lambda **kwargs: env_calls.append(kwargs))

    stop_event = asyncio.Event()
    args = argparse.Namespace(
        workspace=None,
        playbook_dir=None,
        model=None,
        api_key=None,
        api_base=None,
        mock=False,
        no_autopilot=False,
        no_heartbeat=False,
        train_interval_sec=None,
        heartbeat_interval_sec=None,
    )

    async def _run() -> int:
        task = asyncio.create_task(
            runtime_service.run_runtime_service_async(
                args,
                config_cls=FakeConfig,
                runtime_cls=FakeRuntime,
                install_signal_handlers=False,
                stop_event=stop_event,
            )
        )
        await asyncio.sleep(0)
        stop_event.set()
        return await task

    exit_code = asyncio.run(_run())

    assert exit_code == 0
    assert env_calls == [
        {
            "required_modules": ["pandas", "requests", "rank_bm25"],
            "require_project_python": False,
            "validate_requests_stack": True,
            "component": "runtime service",
        }
    ]
    assert events == [("config", False), ("start", True), ("stop", True)]


def test_runtime_service_mock_mode_relaxes_environment_requirements(monkeypatch):
    env_calls = []
    monkeypatch.setattr(runtime_service, "ensure_environment", lambda **kwargs: env_calls.append(kwargs))

    runtime_service._ensure_runtime_service_environment(mock=True)

    assert env_calls == [
        {
            "required_modules": ["pandas"],
            "require_project_python": False,
            "validate_requests_stack": False,
            "component": "runtime service",
        }
    ]
